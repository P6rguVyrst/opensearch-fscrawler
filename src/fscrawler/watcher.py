# Licensed under the Apache License, Version 2.0
"""Watchdog-based filesystem event handler for FSCrawler.

Listens for file create, modify, and delete events under fs.url and
immediately indexes or removes the affected document, rather than waiting
for the next polling cycle.
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchdog.events import FileSystemEventHandler

from fscrawler.dlq import DLQ_INDEX, build_dlq_record, make_dlq_doc_id
from fscrawler.models import make_doc_id

if TYPE_CHECKING:
    from fscrawler.client import FsCrawlerClient
    from fscrawler.parser import TikaParser
    from fscrawler.rest_server import CrawlerState
    from fscrawler.settings import FsSettings
    from fscrawler.wal import WriteAheadLog

logger = logging.getLogger("fscrawler.watcher")


class FsEventHandler(FileSystemEventHandler):
    """Handle filesystem events by indexing or deleting the affected file.

    Note: This handler calls client.index/delete directly (single-document ops),
    bypassing BulkIndexer. As a result, keep_history archival is not triggered
    for watchdog events — only the batch crawl path (via BulkIndexer) supports
    history. This is a known limitation.
    """

    def __init__(
        self,
        settings: FsSettings,
        client: FsCrawlerClient,
        parser: TikaParser,
        crawler_state: CrawlerState,
        wal: WriteAheadLog | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._client = client
        self._parser = parser
        self._crawler_state = crawler_state
        self._wal = wal

    # ------------------------------------------------------------------
    # watchdog callbacks
    # ------------------------------------------------------------------

    def on_created(self, event: Any) -> None:
        if event.is_directory or self._crawler_state.paused:
            return
        if not self._matches(Path(event.src_path).name):
            return
        self._index(Path(event.src_path))

    def on_modified(self, event: Any) -> None:
        if event.is_directory or self._crawler_state.paused:
            return
        if not self._matches(Path(event.src_path).name):
            return
        self._index(Path(event.src_path))

    def on_deleted(self, event: Any) -> None:
        if event.is_directory or self._crawler_state.paused:
            return
        if not self._settings.fs.remove_deleted:
            return
        self._delete(event.src_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _matches(self, name: str) -> bool:
        """Return True if name passes the includes/excludes filters."""
        fs = self._settings.fs
        if fs.includes and not any(fnmatch.fnmatch(name, p) for p in fs.includes):
            return False
        return not any(fnmatch.fnmatch(name, p) for p in fs.excludes)

    def _index(self, path: Path) -> None:
        if path.is_dir():
            return
        doc = None
        doc_id = None
        target_index = self._settings.elasticsearch.index
        job_name = self._settings.name
        try:
            doc = self._parser.parse(path)
            doc_id = make_doc_id(doc.path.virtual)
            doc_body = doc.to_dict()

            if self._wal:
                self._wal.append({
                    "ts": datetime.now(tz=UTC).isoformat(),
                    "job_name": job_name,
                    "target_index": target_index,
                    "doc_id": doc_id,
                    "action": "index",
                    "payload": doc_body,
                })

            self._client.index(
                doc,
                doc_id=doc_id,
                index=target_index,
            )
            logger.info("Indexed %s", path)

            if self._wal:
                self._wal.checkpoint({doc_id})

        except Exception as exc:
            logger.error("Failed to index %s: %s", path, exc, exc_info=True)
            if doc_id is None:
                doc_id = make_doc_id("/" + path.name)
            try:
                dlq_record = build_dlq_record(
                    job_name=job_name,
                    target_index=target_index,
                    doc_id=doc_id,
                    action="index",
                    payload=doc_body if doc is not None else None,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    source_path=str(path),
                )
                dlq_doc_id = make_dlq_doc_id(job_name, doc_id)
                self._client.index_raw(
                    index=DLQ_INDEX,
                    doc_id=dlq_doc_id,
                    body=dlq_record,
                )
                logger.info("Wrote failed doc %s to DLQ", doc_id)
            except Exception as dlq_exc:
                logger.error("Failed to write to DLQ for %s: %s", path, dlq_exc)

    def _delete(self, path: str) -> None:
        try:
            root = self._settings.fs.url
            try:
                virtual = "/" + str(Path(path).relative_to(root))
            except ValueError:
                virtual = "/" + Path(path).name
            doc_id = make_doc_id(virtual)
            target_index = self._settings.elasticsearch.index

            if self._wal:
                self._wal.append({
                    "ts": datetime.now(tz=UTC).isoformat(),
                    "job_name": self._settings.name,
                    "target_index": target_index,
                    "doc_id": doc_id,
                    "action": "delete",
                })

            self._client.delete(
                doc_id=doc_id,
                index=target_index,
            )
            logger.info("Deleted %s from index", path)

            if self._wal:
                self._wal.checkpoint({doc_id})

        except Exception as exc:
            logger.error("Failed to delete %s from index: %s", path, exc, exc_info=True)
