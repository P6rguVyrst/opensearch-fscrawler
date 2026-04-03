# Licensed under the Apache License, Version 2.0
"""Bulk document indexer for FSCrawler."""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from fscrawler.client import FsCrawlerClient
from fscrawler.models import Document, make_doc_id
from fscrawler.settings import FsSettings

if TYPE_CHECKING:
    from fscrawler.models import FolderDocument

logger = logging.getLogger("fscrawler.indexer")


class BulkIndexer:
    """Buffer documents and flush them to OpenSearch in bulk batches.

    Usage as context manager guarantees that the buffer is flushed on exit::

        with BulkIndexer(client, settings) as indexer:
            for doc in documents:
                indexer.add(doc)
    """

    def __init__(self, client: FsCrawlerClient, settings: FsSettings) -> None:
        self._client = client
        self._settings = settings
        self._buffer: list[dict[str, Any]] = []
        self._buffer_bytes: int = 0
        self._lock = threading.Lock()

        es = settings.elasticsearch
        self._bulk_size = es.bulk_size
        self._byte_limit = es.byte_size

        self._index = es.index
        self._folder_index = es.index_folder
        self._index_history = es.index_history
        self._keep_history = settings.fs.keep_history

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> BulkIndexer:
        return self

    def __exit__(self, *args: Any) -> None:
        self.flush()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, doc: Document) -> None:
        """Add a document to the buffer; flush if threshold is reached."""
        doc_id = self._make_id(doc.path.virtual)
        doc_body = doc.to_dict()

        # History: check for existing doc and archive if content changed
        if self._keep_history:
            self._archive_if_changed(doc_id, doc.file.checksum)

        action = {"index": {"_index": self._index, "_id": doc_id}}

        # Estimate byte size: use actual JSON-serialized size
        estimated = len(json.dumps(doc_body, default=str).encode("utf-8"))

        with self._lock:
            self._buffer.append(action)
            self._buffer.append(doc_body)
            self._buffer_bytes += estimated

            if (
                len(self._buffer) // 2 >= self._bulk_size
                or self._buffer_bytes >= self._byte_limit
            ):
                self._flush_locked()

    def add_folder(self, folder_doc: FolderDocument) -> None:
        """Index a directory entry into the folder index."""
        action = {"index": {"_index": self._folder_index, "_id": folder_doc.path.real}}
        doc_body = folder_doc.to_dict()
        estimated = len(json.dumps(doc_body, default=str).encode("utf-8"))

        with self._lock:
            self._buffer.append(action)
            self._buffer.append(doc_body)
            self._buffer_bytes += estimated

            if (
                len(self._buffer) // 2 >= self._bulk_size
                or self._buffer_bytes >= self._byte_limit
            ):
                self._flush_locked()

    def delete(self, virtual_path: str) -> None:
        """Queue a delete operation for the given virtual path."""
        doc_id = self._make_id(virtual_path)

        # History: archive the deleted document
        if self._keep_history:
            self._archive_if_changed(doc_id, "deleted")

        action: dict[str, Any] = {"delete": {"_index": self._index, "_id": doc_id}}

        with self._lock:
            self._buffer.append(action)
            # Check whether we've hit bulk_size (delete counts as one operation)
            if len(self._buffer) >= self._bulk_size:
                self._flush_locked()

    def flush(self) -> None:
        """Flush any remaining buffered operations to OpenSearch."""
        with self._lock:
            self._flush_locked()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_id(self, virtual_path: str) -> str:
        """Generate a stable document ID from the virtual path."""
        return make_doc_id(virtual_path)

    def _archive_if_changed(self, doc_id: str, new_checksum: str | None) -> None:
        """Copy the existing document to the history index if its content has changed."""
        from datetime import UTC, datetime

        try:
            existing = self._client.get_document_source(self._index, doc_id)
        except Exception:
            logger.debug("Cannot retrieve existing doc %s for history — skipping", doc_id)
            return

        if existing is None:
            return

        old_checksum = existing.get("file", {}).get("checksum")
        if old_checksum == new_checksum:
            return  # content unchanged — skip

        # Add history metadata
        existing["superseded_date"] = datetime.now(tz=UTC).isoformat()
        existing["superseded_by"] = new_checksum or "deleted"

        # History doc ID: {original_id}_{old_checksum} for uniqueness
        history_id = f"{doc_id}_{old_checksum}" if old_checksum else doc_id
        history_action = {"index": {"_index": self._index_history, "_id": history_id}}
        estimated = len(json.dumps(existing, default=str).encode("utf-8"))

        with self._lock:
            self._buffer.append(history_action)
            self._buffer.append(existing)
            self._buffer_bytes += estimated

    def _flush_locked(self) -> None:
        """Send buffered operations.  Must be called with self._lock held."""
        if not self._buffer:
            return
        try:
            response = self._client.bulk(self._buffer)
            if response.get("errors"):
                logger.error("Bulk indexing had errors: %s", response)
            else:
                n_ops = len([op for op in self._buffer if "index" in op or "delete" in op])
                logger.debug("Flushed %d operations to OpenSearch.", n_ops)
        except Exception as exc:
            logger.error("Bulk flush failed: %s", exc)
        finally:
            self._buffer = []
            self._buffer_bytes = 0
