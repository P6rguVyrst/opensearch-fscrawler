# Licensed under the Apache License, Version 2.0
"""Bulk document indexer for FSCrawler."""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
from typing import TYPE_CHECKING, Any

from fscrawler.client import FsCrawlerClient
from fscrawler.models import Document
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

        self._filename_as_id = settings.fs.filename_as_id
        self._content_hash_as_id = settings.fs.content_hash_as_id

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
        doc_id = doc.file.checksum if self._content_hash_as_id else self._make_id(doc.file.url)
        action = {"index": {"_index": self._index, "_id": doc_id}}
        doc_body = doc.to_dict()

        # Estimate byte size: rough approximation
        estimated = sys.getsizeof(str(doc_body))

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
        estimated = sys.getsizeof(str(doc_body))

        with self._lock:
            self._buffer.append(action)
            self._buffer.append(doc_body)
            self._buffer_bytes += estimated

            if (
                len(self._buffer) // 2 >= self._bulk_size
                or self._buffer_bytes >= self._byte_limit
            ):
                self._flush_locked()

    def delete(self, file_path: str) -> None:
        """Queue a delete operation for the given file path."""
        if self._content_hash_as_id:
            return  # content-addressed docs are immutable; deletion is a no-op
        doc_id = self._make_id(file_path)
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

    def _make_id(self, file_path: str) -> str:
        if self._filename_as_id:
            return file_path
        return hashlib.sha256(file_path.encode()).hexdigest()

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
