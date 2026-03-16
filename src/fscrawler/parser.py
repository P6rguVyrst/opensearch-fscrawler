# Licensed under the Apache License, Version 2.0
"""Apache Tika-based document parser for FSCrawler.

Calls a running Tika server via its REST API (not the tika Python library which
auto-starts Java).  By default, the Tika server is assumed to be at
http://localhost:9998.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from fscrawler.models import Document, FileInfo, Meta, PathInfo
from fscrawler.settings import FsSettings

logger = logging.getLogger("fscrawler.parser")

_DEFAULT_TIKA_URL = "http://localhost:9998"

# Mapping from Tika metadata keys to Meta dataclass fields
_TIKA_META_MAP: dict[str, str] = {
    "dc:creator": "author",
    "Author": "author",
    "meta:author": "author",
    "dc:title": "title",
    "title": "title",
    "Creation-Date": "created",
    "created": "created",
    "dcterms:created": "created",
    "dc:date": "date",
    "dcterms:modified": "date",
    "Last-Modified": "date",
    "meta:last-modified": "date",
    "Last-Save-Date": "date",
    "Keywords": "keywords",
    "meta:keyword": "keywords",
    "dc:language": "language",
    "Content-Language": "language",
    "dc:format": "format",
    "dc:identifier": "identifier",
    "dc:contributor": "contributor",
    "dc:coverage": "coverage",
    "meta:last-author": "modifier",
    "xmp:CreatorTool": "creator_tool",
    "dc:publisher": "publisher",
    "dc:relation": "relation",
    "dc:rights": "rights",
    "dc:source": "source",
    "dc:type": "type",
    "dc:description": "description",
    "Print-Date": "print_date",
    "meta:print-date": "print_date",
    "xmp:MetadataDate": "metadata_date",
    "geo:lat": "latitude",
    "geo:long": "longitude",
    "geo:alt": "altitude",
    "xmpMM:Rating": "rating",
    "usercomment": "comments",
}


class TikaUnavailableError(RuntimeError):
    """Raised when the Tika server cannot be reached."""


class TikaParser:
    """Parse documents by calling a remote Tika server."""

    def __init__(
        self,
        settings: FsSettings,
        tika_url: str = _DEFAULT_TIKA_URL,
    ) -> None:
        self._settings = settings
        self._tika_url = tika_url.rstrip("/")

    def parse(self, file_path: Path) -> Document:
        """Parse a file and return a Document with extracted content and metadata."""
        fs = self._settings.fs
        raw_bytes = file_path.read_bytes()

        # ------------------------------------------------------------------
        # Call Tika
        # ------------------------------------------------------------------
        tika_meta = self._call_tika(raw_bytes)

        content_type = str(tika_meta.get("Content-Type", "application/octet-stream"))
        # Tika may return a list for Content-Type
        if isinstance(content_type, list):
            content_type = content_type[0]
        # Strip parameters (e.g. "; charset=UTF-8")
        content_type = content_type.split(";")[0].strip()

        # ------------------------------------------------------------------
        # Content
        # ------------------------------------------------------------------
        content: str | None = None
        if fs.index_content:
            raw_content = tika_meta.get("X-TIKA:content") or ""
            if isinstance(raw_content, list):
                raw_content = "\n".join(raw_content)
            raw_content = str(raw_content).strip()
            content = raw_content[: fs.indexed_chars] if fs.indexed_chars > 0 else raw_content  # -1 means unlimited

        # ------------------------------------------------------------------
        # File info
        # ------------------------------------------------------------------
        stat = file_path.stat()
        now = datetime.now(tz=UTC).isoformat()

        checksum: str | None = None
        if fs.checksum:
            algo = fs.checksum.lower().replace("-", "")
            try:
                h = hashlib.new(algo, raw_bytes)
                checksum = h.hexdigest()
            except ValueError:
                logger.warning("Unknown checksum algorithm: %s", fs.checksum)
        if fs.content_hash_as_id and checksum is None:
            checksum = hashlib.sha256(raw_bytes).hexdigest()

        created: str | None = None
        with contextlib.suppress(AttributeError):
            # Platform-specific birth time
            created = datetime.fromtimestamp(
                stat.st_birthtime, tz=UTC
            ).isoformat()

        last_accessed: str | None = None
        with contextlib.suppress(Exception):
            last_accessed = datetime.fromtimestamp(stat.st_atime, tz=UTC).isoformat()

        file_info = FileInfo(
            filename=file_path.name,
            extension=file_path.suffix.lstrip(".").lower(),
            content_type=content_type,
            filesize=stat.st_size,
            indexing_date=now,
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            created=created,
            last_accessed=last_accessed,
            checksum=checksum,
            url=str(file_path),
        )

        # ------------------------------------------------------------------
        # Path info
        # ------------------------------------------------------------------
        root = str(self._settings.fs.url)
        try:
            virtual = "/" + str(file_path.relative_to(root))
        except ValueError:
            virtual = "/" + file_path.name

        path_info = PathInfo(real=str(file_path), root=root, virtual=virtual)

        # ------------------------------------------------------------------
        # Metadata
        # ------------------------------------------------------------------
        meta = Meta()
        for tika_key, attr in _TIKA_META_MAP.items():
            val = tika_meta.get(tika_key)
            if val is not None:
                if isinstance(val, list):
                    val = val[0]
                setattr(meta, attr, str(val))

        # ------------------------------------------------------------------
        # Attachment
        # ------------------------------------------------------------------
        attachment: bytes | None = None
        if fs.store_source:
            attachment = raw_bytes

        return Document(
            content=content,
            file=file_info,
            path=path_info,
            meta=meta,
            attachment=attachment,
        )

    def parse_bytes(
        self, filename: str, data: bytes, content_type: str | None = None
    ) -> Document:
        """Parse raw bytes (e.g., from a REST upload) and return a Document.

        Unlike parse(), this method does not require a file on disk — all file
        metadata is derived from the provided filename and byte content.
        """
        fs = self._settings.fs
        tika_meta = self._call_tika(data)

        ct = content_type or str(tika_meta.get("Content-Type", "application/octet-stream"))
        if isinstance(ct, list):
            ct = ct[0]
        ct = ct.split(";")[0].strip()

        content: str | None = None
        if fs.index_content:
            raw_content = tika_meta.get("X-TIKA:content") or ""
            if isinstance(raw_content, list):
                raw_content = "\n".join(raw_content)
            raw_content = str(raw_content).strip()
            content = raw_content[: fs.indexed_chars] if fs.indexed_chars > 0 else raw_content

        now = datetime.now(tz=UTC).isoformat()
        name = Path(filename).name
        ext = Path(filename).suffix.lstrip(".").lower()

        checksum: str | None = None
        if fs.checksum:
            algo = fs.checksum.lower().replace("-", "")
            try:
                checksum = hashlib.new(algo, data).hexdigest()
            except ValueError:
                logger.warning("Unknown checksum algorithm: %s", fs.checksum)
        if fs.content_hash_as_id and checksum is None:
            checksum = hashlib.sha256(data).hexdigest()

        file_info = FileInfo(
            filename=name,
            extension=ext,
            content_type=ct,
            filesize=len(data),
            indexing_date=now,
            last_modified=now,
            checksum=checksum,
        )
        path_info = PathInfo(real=filename, root=str(fs.url), virtual=f"/{name}")

        meta = Meta()
        for tika_key, attr in _TIKA_META_MAP.items():
            val = tika_meta.get(tika_key)
            if val is not None:
                if isinstance(val, list):
                    val = val[0]
                setattr(meta, attr, str(val))

        return Document(content=content, file=file_info, path=path_info, meta=meta)

    def _call_tika(self, raw_bytes: bytes) -> dict[str, Any]:
        """POST file content to Tika's /rmeta/text endpoint and return parsed metadata."""
        url = f"{self._tika_url}/rmeta/text"
        headers = {
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.put(url, content=raw_bytes, headers=headers)
                response.raise_for_status()
                data = response.json()
                # /rmeta returns a list of metadata objects; use the first
                if isinstance(data, list) and data:
                    return data[0]  # type: ignore[no-any-return]
                return data  # type: ignore[no-any-return]
        except httpx.ConnectError as exc:
            raise TikaUnavailableError(
                f"Cannot connect to Tika server at {self._tika_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise TikaUnavailableError(
                f"Tika server returned error {exc.response.status_code}"
            ) from exc
