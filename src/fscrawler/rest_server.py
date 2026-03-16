# Licensed under the Apache License, Version 2.0
"""FSCrawler embedded REST server (FastAPI).

Mirrors the Java FSCrawler REST layer:
  GET  /                       – server status
  POST /_document              – upload document via multipart form
  PUT  /_document/{id}         – upload with explicit document id
  DELETE /_document            – delete by filename (query param)
  DELETE /_document/{id}       – delete by document id
  POST /_crawler/pause         – pause the background crawler
  POST /_crawler/resume        – resume the background crawler
  GET  /_crawler/status        – crawler state
  DELETE /_crawler/checkpoint  – clear checkpoint (requires pause)

Multipart parsing is done with Python stdlib only (no python-multipart).
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from fscrawler import __version__
from fscrawler.client import FsCrawlerClient
from fscrawler.multipart import parse_multipart
from fscrawler.parser import TikaParser
from fscrawler.settings import FsSettings

logger = logging.getLogger("fscrawler.rest_server")


# ---------------------------------------------------------------------------
# Crawler state
# ---------------------------------------------------------------------------


class CrawlerState:
    """Mutable state shared between the background crawler thread and REST endpoints."""

    def __init__(self) -> None:
        self.paused: bool = False
        self.last_checkpoint: str | None = None

    def clear_checkpoint(self) -> None:
        self.last_checkpoint = None


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    settings: FsSettings,
    client: FsCrawlerClient,
    crawler_state: CrawlerState,
    parser: TikaParser | None = None,
) -> FastAPI:
    """Build and return the FastAPI application.

    Parameters
    ----------
    settings:
        Loaded job settings (used for index names, REST config, etc.).
    client:
        Initialised FsCrawlerClient for indexing / deleting documents.
    crawler_state:
        Shared mutable state for pause/resume/status operations.
    parser:
        Optional TikaParser instance.  Created from settings if not provided.
        Inject a mock in tests to avoid real Tika calls.
    """
    app = FastAPI(title="FSCrawler REST API", version=__version__)

    if settings.rest.enable_cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    if parser is None:
        parser = TikaParser(settings)

    # ------------------------------------------------------------------
    # GET /_crawler/settings — current configuration (credentials redacted)
    # ------------------------------------------------------------------

    @app.get("/_crawler/settings")
    def get_settings() -> dict[str, Any]:
        return {"fs": dataclasses.asdict(settings.fs)}

    # ------------------------------------------------------------------
    # GET / — server status
    # ------------------------------------------------------------------

    @app.get("/")
    def server_status() -> dict[str, Any]:
        try:
            info = client.info()
        except Exception as exc:
            logger.warning("Cannot reach Elasticsearch: %s", exc)
            raise HTTPException(status_code=503, detail="Elasticsearch unavailable") from exc

        es_version = info.get("version", {})
        if isinstance(es_version, dict):
            es_version = es_version.get("number", "unknown")

        return {
            "ok": True,
            "version": __version__,
            "elasticsearch": {"version": es_version},
            "settings": {"name": settings.name},
        }

    # ------------------------------------------------------------------
    # POST /_document — upload via multipart
    # ------------------------------------------------------------------

    @app.post("/_document")
    async def upload_document(
        request: Request,
        id: str | None = Query(default=None),
        index: str | None = Query(default=None),
        simulate: bool = Query(default=False),
        debug: bool = Query(default=False),
    ) -> dict[str, Any]:
        form_file = await _extract_file(request)
        return _handle_upload(
            filename=form_file.filename,
            data=form_file.data,
            content_type=form_file.content_type,
            doc_id=id,
            index=index,
            simulate=simulate,
            debug=debug,
            parser=parser,
            client=client,
            settings=settings,
        )

    # ------------------------------------------------------------------
    # PUT /_document/{id} — upload with explicit id
    # ------------------------------------------------------------------

    @app.put("/_document/{doc_id}")
    async def upload_document_with_id(
        doc_id: str,
        request: Request,
        index: str | None = Query(default=None),
        simulate: bool = Query(default=False),
        debug: bool = Query(default=False),
    ) -> dict[str, Any]:
        form_file = await _extract_file(request)
        return _handle_upload(
            filename=form_file.filename,
            data=form_file.data,
            content_type=form_file.content_type,
            doc_id=doc_id,
            index=index,
            simulate=simulate,
            debug=debug,
            parser=parser,
            client=client,
            settings=settings,
        )

    # ------------------------------------------------------------------
    # DELETE /_document — delete by filename
    # ------------------------------------------------------------------

    @app.delete("/_document")
    def delete_document_by_filename(
        filename: str = Query(...),
        index: str | None = Query(default=None),
    ) -> dict[str, Any]:
        idx = index or settings.elasticsearch.index
        client.delete(doc_id=filename, index=idx)
        logger.info("Deleted document filename=%s index=%s", filename, idx)
        return {"ok": True, "filename": filename}

    # ------------------------------------------------------------------
    # DELETE /_document/{id} — delete by document id
    # ------------------------------------------------------------------

    @app.delete("/_document/{doc_id}")
    def delete_document_by_id(
        doc_id: str,
        index: str | None = Query(default=None),
    ) -> dict[str, Any]:
        idx = index or settings.elasticsearch.index
        client.delete(doc_id=doc_id, index=idx)
        logger.info("Deleted document id=%s index=%s", doc_id, idx)
        return {"ok": True, "doc_id": doc_id}

    # ------------------------------------------------------------------
    # POST /_crawler/pause
    # ------------------------------------------------------------------

    @app.post("/_crawler/pause")
    def pause_crawler() -> dict[str, Any]:
        crawler_state.paused = True
        logger.info("Crawler paused via REST API")
        return {"ok": True, "message": "Crawler paused"}

    # ------------------------------------------------------------------
    # POST /_crawler/resume
    # ------------------------------------------------------------------

    @app.post("/_crawler/resume")
    def resume_crawler() -> dict[str, Any]:
        crawler_state.paused = False
        logger.info("Crawler resumed via REST API")
        return {"ok": True, "message": "Crawler resumed"}

    # ------------------------------------------------------------------
    # GET /_crawler/status
    # ------------------------------------------------------------------

    @app.get("/_crawler/status")
    def crawler_status() -> dict[str, Any]:
        status = "paused" if crawler_state.paused else "running"
        return {
            "ok": True,
            "status": status,
            "last_checkpoint": crawler_state.last_checkpoint,
        }

    # ------------------------------------------------------------------
    # DELETE /_crawler/checkpoint
    # ------------------------------------------------------------------

    @app.delete("/_crawler/checkpoint")
    def clear_checkpoint() -> dict[str, Any]:
        if not crawler_state.paused:
            raise HTTPException(
                status_code=409,
                detail="Crawler must be paused before clearing the checkpoint",
            )
        crawler_state.clear_checkpoint()
        logger.info("Checkpoint cleared via REST API")
        return {"ok": True, "message": "Checkpoint cleared"}

    return app


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _extract_file(request: Request) -> Any:
    """Read the request body and extract the first file field.

    Raises HTTPException(422) if no file part is found.
    """

    content_type = request.headers.get("content-type", "")
    body = await request.body()

    if not content_type.lower().startswith("multipart/form-data"):
        raise HTTPException(
            status_code=422,
            detail="Expected multipart/form-data request with a 'file' field",
        )

    try:
        files = parse_multipart(content_type, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not files:
        raise HTTPException(
            status_code=422,
            detail="No file field found in multipart body",
        )

    return files[0]


def _handle_upload(
    *,
    filename: str,
    data: bytes,
    content_type: str,
    doc_id: str | None,
    index: str | None,
    simulate: bool,
    debug: bool,
    parser: TikaParser,
    client: FsCrawlerClient,
    settings: FsSettings,
) -> dict[str, Any]:
    try:
        doc = parser.parse_bytes(filename=filename, data=data, content_type=content_type)
    except Exception as exc:
        logger.error("Failed to parse %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail=f"Failed to parse document: {exc}") from exc

    effective_id = doc_id or filename
    idx = index or settings.elasticsearch.index
    doc_url = f"/{idx}/_doc/{effective_id}"

    if not simulate:
        client.index(doc, doc_id=effective_id, index=idx)
        logger.info("Indexed %s as %s in %s", filename, effective_id, idx)

    response: dict[str, Any] = {"ok": True, "filename": filename, "url": doc_url}
    if debug:
        response["doc"] = doc.to_dict()
    return response
