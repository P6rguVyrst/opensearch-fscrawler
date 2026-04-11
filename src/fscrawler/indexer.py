# Licensed under the Apache License, Version 2.0
"""Bulk document indexer for FSCrawler."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fscrawler.client import FsCrawlerClient
from fscrawler.dlq import (
    DLQ_INDEX,
    PFQ_INDEX,
    build_dlq_record,
    build_pfq_record,
    is_retryable_error,
    make_dlq_doc_id,
)
from fscrawler.metrics import bulk_duration, dlq_records, documents_processed, pfq_records
from fscrawler.models import Document, make_doc_id
from fscrawler.settings import FsSettings

if TYPE_CHECKING:
    from fscrawler.models import FolderDocument
    from fscrawler.wal import WriteAheadLog

logger = logging.getLogger("fscrawler.indexer")


class BulkIndexer:
    """Buffer documents and flush them to OpenSearch in bulk batches.

    Usage as context manager guarantees that the buffer is flushed on exit::

        with BulkIndexer(client, settings) as indexer:
            for doc in documents:
                indexer.add(doc)
    """

    def __init__(
        self,
        client: FsCrawlerClient,
        settings: FsSettings,
        wal: WriteAheadLog | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._wal = wal
        self._buffer: list[dict[str, Any]] = []
        self._buffer_bytes: int = 0
        self._pending: dict[str, dict[str, Any]] = {}
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

        if self._wal:
            self._wal.append({
                "ts": datetime.now(tz=UTC).isoformat(),
                "job_name": self._settings.name,
                "target_index": self._index,
                "doc_id": doc_id,
                "action": "index",
                "payload": doc_body,
            })

        # History: check for existing doc and archive if content changed
        if self._keep_history:
            self._archive_if_changed(doc_id, doc.file.checksum)

        action = {"index": {"_index": self._index, "_id": doc_id}}

        # Estimate byte size: use actual JSON-serialized size
        estimated = len(json.dumps(doc_body, default=str).encode("utf-8"))

        with self._lock:
            self._pending[doc_id] = {
                "job_name": self._settings.name,
                "target_index": self._index,
                "action": "index",
                "payload": doc_body,
                "source_path": doc.path.real,
            }
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

        if self._wal:
            self._wal.append({
                "ts": datetime.now(tz=UTC).isoformat(),
                "job_name": self._settings.name,
                "target_index": self._index,
                "doc_id": doc_id,
                "action": "delete",
            })

        # History: archive the deleted document
        if self._keep_history:
            self._archive_if_changed(doc_id, "deleted")

        action: dict[str, Any] = {"delete": {"_index": self._index, "_id": doc_id}}

        with self._lock:
            self._pending[doc_id] = {
                "job_name": self._settings.name,
                "target_index": self._index,
                "action": "delete",
                "payload": None,
                "source_path": virtual_path,
            }
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

        job_name = self._settings.name
        succeeded_ids: set[str] = set()
        t0 = time.monotonic()
        try:
            response = self._client.bulk(self._buffer)
            elapsed = time.monotonic() - t0
            if response.get("errors"):
                for item in response.get("items", []):
                    op = item.get("index") or item.get("delete") or {}
                    doc_id = op.get("_id", "")
                    error = op.get("error")
                    if error:
                        error_type = error.get("type", "unknown")
                        documents_processed.add(1, {
                            "status": "error",
                            "error.type": error_type,
                            "fscrawler.job.name": job_name,
                        })
                        self._route_failure(doc_id, error)
                    else:
                        succeeded_ids.add(doc_id)
                        documents_processed.add(1, {
                            "status": "success",
                            "fscrawler.job.name": job_name,
                        })
            else:
                # _pending tracks only document index/delete operations.
                # Folder/history writes are intentionally excluded because they
                # do not participate in WAL checkpointing or document metrics.
                succeeded_ids = set(self._pending.keys())
                n_ops = len(succeeded_ids)
                for _ in range(n_ops):
                    documents_processed.add(1, {
                        "status": "success",
                        "fscrawler.job.name": job_name,
                    })
                logger.debug("Flushed %d operations to OpenSearch.", n_ops)
            bulk_duration.record(elapsed, {
                "status": "success",
                "fscrawler.job.name": job_name,
            })
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error("Bulk flush failed: %s", exc)
            self._route_bulk_exception(exc)
            bulk_duration.record(elapsed, {
                "status": "error",
                "error.type": "bulk_flush_error",
                "fscrawler.job.name": job_name,
            })
        finally:
            if succeeded_ids and self._wal:
                self._wal.checkpoint(succeeded_ids)
            self._buffer = []
            self._buffer_bytes = 0
            self._pending = {}

    def _route_bulk_exception(self, exc: Exception) -> None:
        """Route all pending document operations when bulk() fails outright."""
        error = {
            "type": "bulk_flush_error",
            "reason": str(exc),
        }

        for doc_id in list(self._pending):
            documents_processed.add(1, {
                "status": "error",
                "error.type": error["type"],
                "fscrawler.job.name": self._settings.name,
            })
            self._route_failure(doc_id, error)

    def _route_failure(self, doc_id: str, error: dict[str, Any]) -> None:
        """Route a failed bulk item to DLQ (retryable) or PFQ (non-retryable)."""
        error_type = error.get("type", "unknown")
        error_message = error.get("reason", str(error))
        meta = self._pending.get(doc_id, {})
        job_name = meta.get("job_name", self._settings.name)
        target_index = meta.get("target_index", self._index)
        action = meta.get("action", "index")
        payload = meta.get("payload")
        source_path = meta.get("source_path", "")
        dlq_doc_id = make_dlq_doc_id(job_name, doc_id)

        if is_retryable_error(error_type):
            record = build_dlq_record(
                job_name=job_name,
                target_index=target_index,
                doc_id=doc_id,
                action=action,
                payload=payload,
                error_message=error_message,
                error_type=error_type,
                source_path=source_path,
                retry_interval=self._settings.elasticsearch.dlq.retry_interval,
            )
            try:
                self._client.index_raw(index=DLQ_INDEX, doc_id=dlq_doc_id, body=record)
                logger.warning(
                    "DLQ: %s (%s) failed: %s — %s",
                    doc_id, job_name, error_type, error_message,
                )
                dlq_records.add(1, {
                    "error.type": error_type,
                    "fscrawler.job.name": job_name,
                })
            except Exception as exc:
                logger.error("DLQ: failed to write %s: %s", doc_id, exc)
        else:
            record = build_dlq_record(
                job_name=job_name,
                target_index=target_index,
                doc_id=doc_id,
                action=action,
                payload=payload,
                error_message=error_message,
                error_type=error_type,
                source_path=source_path,
            )
            pfq_record = build_pfq_record(
                record, final_error=f"non-retryable: {error_type} — {error_message}"
            )
            try:
                self._client.index_raw(index=PFQ_INDEX, doc_id=dlq_doc_id, body=pfq_record)
                logger.warning(
                    "PFQ: %s (%s) non-retryable: %s",
                    doc_id, job_name, error_type,
                )
                pfq_records.add(1, {
                    "error.type": error_type,
                    "fscrawler.job.name": job_name,
                })
            except Exception as exc:
                logger.error("PFQ: failed to write %s: %s", doc_id, exc)
