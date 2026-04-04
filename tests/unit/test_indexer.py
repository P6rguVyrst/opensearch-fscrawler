"""Unit tests for fscrawler.indexer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, call, patch

from tests.conftest import make_document, make_settings


# ---------------------------------------------------------------------------
# Buffering
# ---------------------------------------------------------------------------


class TestIndexerBuffering:
    def test_documents_buffered_until_bulk_size(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 3})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        for i in range(2):
            indexer.add(make_document(f"/data/doc{i}.txt"))

        # bulk should not have been called yet (only 2 docs, limit is 3)
        mock_opensearch_client.bulk.assert_not_called()

    def test_flush_triggered_at_bulk_size(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 3})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        for i in range(3):
            indexer.add(make_document(f"/data/doc{i}.txt"))

        # After adding the 3rd doc, bulk should have been called
        mock_opensearch_client.bulk.assert_called_once()

    def test_manual_flush_sends_remaining(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 10})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        indexer.add(make_document("/data/single.txt"))
        mock_opensearch_client.bulk.assert_not_called()

        indexer.flush()
        mock_opensearch_client.bulk.assert_called_once()

    def test_flush_on_empty_buffer_does_nothing(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 10})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        indexer.flush()
        mock_opensearch_client.bulk.assert_not_called()


# ---------------------------------------------------------------------------
# Document ID
# ---------------------------------------------------------------------------


class TestIndexerDocumentId:
    def test_id_is_sha256_of_virtual_path(self, mock_opensearch_client: MagicMock) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc = make_document("/data/myfile.txt")
        # make_document sets virtual to "/myfile.txt"
        indexer.add(doc)

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        index_actions = [op for op in body if "index" in op]
        expected_id = hashlib.sha256("/myfile.txt".encode()).hexdigest()
        assert index_actions[0]["index"]["_id"] == expected_id

    def test_same_virtual_path_same_id(self, mock_opensearch_client: MagicMock) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 10})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc1 = make_document("/data/myfile.txt", content="version 1")
        doc2 = make_document("/data/myfile.txt", content="version 2")
        indexer.add(doc1)
        indexer.add(doc2)
        indexer.flush()

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        ids = [body[i]["index"]["_id"] for i in range(0, len(body), 2)]
        assert ids[0] == ids[1]  # same virtual path → same ID

    def test_different_virtual_paths_different_ids(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 10})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc1 = make_document("/data/a.txt")
        doc2 = make_document("/data/b.txt")
        indexer.add(doc1)
        indexer.add(doc2)
        indexer.flush()

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        ids = [body[i]["index"]["_id"] for i in range(0, len(body), 2)]
        assert ids[0] != ids[1]


# ---------------------------------------------------------------------------
# Delete operations
# ---------------------------------------------------------------------------


class TestIndexerDelete:
    def test_delete_operation_included_in_bulk(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        indexer.delete("/gone.txt")  # virtual path

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        delete_ops = [op for op in body if "delete" in op]
        assert len(delete_ops) == 1

    def test_delete_uses_correct_index(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        indexer.delete("/gone.txt")  # virtual path

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        delete_ops = [op for op in body if "delete" in op]
        assert delete_ops[0]["delete"]["_index"] == "fscrawler_docs_test"


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Delete with new ID strategy
# ---------------------------------------------------------------------------


class TestIndexerDeleteNewId:
    def test_delete_uses_sha256_of_virtual_path(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        indexer.delete("/gone.txt")  # virtual path

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        delete_ops = [op for op in body if "delete" in op]
        expected_id = hashlib.sha256("/gone.txt".encode()).hexdigest()
        assert delete_ops[0]["delete"]["_id"] == expected_id


class TestByteEstimation:
    def test_byte_size_threshold_triggers_flush_accurately(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        """Verify flush triggers based on actual JSON-serialized size, not Python object size."""
        import json

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        # Set byte_size to 500 bytes — a single document's JSON should be ~300-400 bytes
        settings = make_settings(elasticsearch={"bulk_size": 1000, "byte_size": 500})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc = make_document("/data/doc.txt", content="x" * 200)
        doc_json_size = len(json.dumps(doc.to_dict()).encode("utf-8"))

        # Add documents until we expect to exceed 500 bytes
        docs_needed = (500 // doc_json_size) + 1
        for i in range(docs_needed):
            indexer.add(make_document(f"/data/doc{i}.txt", content="x" * 200))

        # Should have flushed by now
        assert mock_opensearch_client.bulk.called


# ---------------------------------------------------------------------------
# History support
# ---------------------------------------------------------------------------


class TestIndexerHistory:
    def _make_history_settings(self) -> Any:
        return make_settings(
            fs={"url": "/data", "keep_history": True},
            elasticsearch={
                "bulk_size": 100,
            },
        )

    def test_history_copies_old_version_before_update(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_history_settings()
        client = FsCrawlerClient(settings)

        # Simulate an existing document with a different checksum
        mock_opensearch_client.get.return_value = {
            "_source": {
                "file": {"checksum": "old_hash", "filename": "test.txt"},
                "path": {"virtual": "/test.txt", "real": "/data/test.txt", "root": "/data"},
                "content": "old content",
            }
        }

        indexer = BulkIndexer(client, settings)
        doc = make_document("/data/test.txt", content="new content")
        doc.file.checksum = "new_hash"
        indexer.add(doc)
        indexer.flush()

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        # Should have: history index action, history doc, main index action, main doc
        index_actions = [op for op in body if "index" in op]
        history_actions = [a for a in index_actions if a["index"]["_index"] == "fscrawler_history_test"]
        assert len(history_actions) == 1

    def test_history_skips_when_checksum_unchanged(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_history_settings()
        client = FsCrawlerClient(settings)

        # Existing doc has same checksum
        mock_opensearch_client.get.return_value = {
            "_source": {
                "file": {"checksum": "same_hash"},
                "path": {"virtual": "/test.txt"},
            }
        }

        indexer = BulkIndexer(client, settings)
        doc = make_document("/data/test.txt")
        doc.file.checksum = "same_hash"
        indexer.add(doc)
        indexer.flush()

        # Should still index (update in-place) but no history entry
        body = mock_opensearch_client.bulk.call_args[1]["body"]
        index_actions = [op for op in body if "index" in op]
        history_actions = [a for a in index_actions if a["index"]["_index"] == "fscrawler_history_test"]
        assert len(history_actions) == 0

    def test_history_not_written_when_keep_history_false(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 100})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc = make_document("/data/test.txt")
        doc.file.checksum = "new_hash"
        indexer.add(doc)
        indexer.flush()

        # get should never be called when history is off
        mock_opensearch_client.get.assert_not_called()

    def test_history_on_delete(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_history_settings()
        client = FsCrawlerClient(settings)

        mock_opensearch_client.get.return_value = {
            "_source": {
                "file": {"checksum": "old_hash", "filename": "test.txt"},
                "path": {"virtual": "/test.txt"},
                "content": "old content",
            }
        }

        indexer = BulkIndexer(client, settings)
        indexer.delete("/test.txt")
        indexer.flush()

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        # Should have: history index action, history doc, delete action
        index_actions = [op for op in body if "index" in op]
        history_actions = [a for a in index_actions if a["index"]["_index"] == "fscrawler_history_test"]
        assert len(history_actions) == 1
        delete_actions = [op for op in body if "delete" in op]
        assert len(delete_actions) == 1


    def test_archive_continues_on_get_error(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_history_settings()
        client = FsCrawlerClient(settings)

        # Simulate a network error when fetching existing doc
        mock_opensearch_client.get.side_effect = ConnectionError("cluster down")

        indexer = BulkIndexer(client, settings)
        doc = make_document("/data/test.txt")
        indexer.add(doc)
        indexer.flush()

        # Should still index the new doc despite the get failure
        body = mock_opensearch_client.bulk.call_args[1]["body"]
        index_actions = [op for op in body if "index" in op]
        assert len(index_actions) == 1


class TestIndexerContextManager:
    def test_context_manager_flushes_on_exit(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 100})
        client = FsCrawlerClient(settings)

        with BulkIndexer(client, settings) as indexer:
            indexer.add(make_document("/data/x.txt"))

        # Should flush on __exit__
        mock_opensearch_client.bulk.assert_called_once()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestIndexerConcurrency:
    def test_concurrent_adds_no_lost_writes(self, mock_opensearch_client: MagicMock) -> None:
        """Spawn N threads each calling add(); verify no documents are lost."""
        import threading

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        n_threads = 10
        settings = make_settings(elasticsearch={"bulk_size": 1000})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        def worker(i: int) -> None:
            indexer.add(make_document(f"/data/doc{i}.txt", content=f"content {i}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        indexer.flush()

        # Collect all index actions across all bulk calls
        total_docs = 0
        for call in mock_opensearch_client.bulk.call_args_list:
            body = call[1].get("body") or call[0][0]
            index_actions = [op for op in body if isinstance(op, dict) and "index" in op]
            total_docs += len(index_actions)
        assert total_docs == n_threads


# ---------------------------------------------------------------------------
# Bulk error handling
# ---------------------------------------------------------------------------


class TestBulkErrorHandling:
    def test_bulk_errors_logged_but_not_raised(self, mock_opensearch_client: MagicMock) -> None:
        """When bulk response has errors=True, indexer logs but does not raise."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        mock_opensearch_client.bulk.return_value = {
            "errors": True,
            "items": [{"index": {"_id": "1", "status": 429, "error": {"reason": "rejected"}}}],
        }

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        # Should not raise
        indexer.add(make_document("/data/test.txt"))

    def test_bulk_exception_logged_but_not_raised(self, mock_opensearch_client: MagicMock) -> None:
        """When client.bulk() raises, indexer catches and clears the buffer."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        mock_opensearch_client.bulk.side_effect = ConnectionError("cluster down")

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        # Should not raise
        indexer.add(make_document("/data/test.txt"))

        # Buffer should be cleared after failed flush
        assert len(indexer._buffer) == 0


# ---------------------------------------------------------------------------
# WAL + DLQ integration
# ---------------------------------------------------------------------------


class TestBulkIndexerWalIntegration:
    """Tests for WAL durability and DLQ/PFQ error routing in BulkIndexer."""

    def test_add_writes_to_wal(self, mock_opensearch_client: MagicMock) -> None:
        """When WAL is provided, add() appends a record to the WAL."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 100})
        client = FsCrawlerClient(settings)
        wal = MagicMock()

        indexer = BulkIndexer(client, settings, wal=wal)
        doc = make_document("/data/test.txt")
        indexer.add(doc)

        wal.append.assert_called_once()
        record = wal.append.call_args[0][0]
        assert record["job_name"] == "test"
        assert record["action"] == "index"
        assert record["target_index"] == settings.elasticsearch.index
        assert "doc_id" in record
        assert "payload" in record
        assert "ts" in record

    def test_successful_flush_checkpoints_wal(self, mock_opensearch_client: MagicMock) -> None:
        """After a successful bulk flush, WAL is checkpointed with succeeded doc IDs."""
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        wal = MagicMock()

        doc = make_document("/data/test.txt")
        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()

        # Mock bulk response with matching item
        mock_opensearch_client.bulk.return_value = {
            "took": 5,
            "errors": False,
            "items": [
                {"index": {"_index": "fscrawler_docs_test", "_id": expected_id, "status": 201, "result": "created"}}
            ],
        }

        indexer = BulkIndexer(client, settings, wal=wal)
        indexer.add(doc)

        wal.checkpoint.assert_called_once()
        checkpointed_ids = wal.checkpoint.call_args[0][0]
        assert expected_id in checkpointed_ids

    def test_failed_bulk_items_sent_to_dlq(self, mock_opensearch_client: MagicMock) -> None:
        """Retryable bulk errors route the failed item to the DLQ index."""
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.dlq import DLQ_INDEX
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        wal = MagicMock()

        doc = make_document("/data/test.txt")
        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()

        # Retryable error (e.g., 429 rejected_execution_exception)
        mock_opensearch_client.bulk.return_value = {
            "took": 5,
            "errors": True,
            "items": [
                {
                    "index": {
                        "_index": "fscrawler_docs_test",
                        "_id": expected_id,
                        "status": 429,
                        "error": {
                            "type": "rejected_execution_exception",
                            "reason": "rejected execution of bulk",
                        },
                    }
                }
            ],
        }

        indexer = BulkIndexer(client, settings, wal=wal)
        indexer.add(doc)

        # Verify index_raw was called with DLQ index
        mock_opensearch_client.index.assert_called_once()
        call_kwargs = mock_opensearch_client.index.call_args[1]
        assert call_kwargs["index"] == DLQ_INDEX
        assert "test:" in call_kwargs["id"]  # make_dlq_doc_id uses "job_name:doc_id"

    def test_non_retryable_error_goes_to_pfq(self, mock_opensearch_client: MagicMock) -> None:
        """Non-retryable errors (e.g., mapper_parsing_exception) go to PFQ index."""
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.dlq import PFQ_INDEX
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        wal = MagicMock()

        doc = make_document("/data/test.txt")
        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()

        # Non-retryable error
        mock_opensearch_client.bulk.return_value = {
            "took": 5,
            "errors": True,
            "items": [
                {
                    "index": {
                        "_index": "fscrawler_docs_test",
                        "_id": expected_id,
                        "status": 400,
                        "error": {
                            "type": "mapper_parsing_exception",
                            "reason": "failed to parse field [content]",
                        },
                    }
                }
            ],
        }

        indexer = BulkIndexer(client, settings, wal=wal)
        indexer.add(doc)

        # Verify index_raw was called with PFQ index
        mock_opensearch_client.index.assert_called_once()
        call_kwargs = mock_opensearch_client.index.call_args[1]
        assert call_kwargs["index"] == PFQ_INDEX

    def test_works_without_wal(self, mock_opensearch_client: MagicMock) -> None:
        """BulkIndexer works correctly when no WAL is provided (backward compat)."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        # No WAL parameter — should work as before
        indexer = BulkIndexer(client, settings)
        indexer.add(make_document("/data/test.txt"))

        mock_opensearch_client.bulk.assert_called_once()

    def test_delete_writes_to_wal(self, mock_opensearch_client: MagicMock) -> None:
        """When WAL is provided, delete() appends a record to the WAL."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 100})
        client = FsCrawlerClient(settings)
        wal = MagicMock()

        indexer = BulkIndexer(client, settings, wal=wal)
        indexer.delete("/gone.txt")

        wal.append.assert_called_once()
        record = wal.append.call_args[0][0]
        assert record["action"] == "delete"
        assert record["job_name"] == "test"
        assert "doc_id" in record

    def test_pending_cleared_after_flush(self, mock_opensearch_client: MagicMock) -> None:
        """The _pending dict is cleared after flush regardless of outcome."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        indexer = BulkIndexer(client, settings)
        indexer.add(make_document("/data/test.txt"))

        assert len(indexer._pending) == 0  # cleared after auto-flush


# ---------------------------------------------------------------------------
# OTel metrics instrumentation
# ---------------------------------------------------------------------------


class TestIndexerMetrics:
    def test_successful_flush_increments_documents_processed_success(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        mock_opensearch_client.bulk.return_value = {
            "took": 5, "errors": False,
            "items": [{"index": {"_index": "fscrawler_docs_test", "_id": expected_id, "status": 201}}],
        }

        with patch("fscrawler.indexer.documents_processed") as mock_counter:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))
            success_calls = [c for c in mock_counter.add.call_args_list if c[0][1].get("status") == "success"]
            assert len(success_calls) >= 1

    def test_failed_flush_increments_documents_processed_error(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        mock_opensearch_client.bulk.return_value = {
            "took": 5, "errors": True,
            "items": [{"index": {"_index": "fscrawler_docs_test", "_id": expected_id, "status": 429,
                "error": {"type": "circuit_breaking_exception", "reason": "oom"}}}],
        }

        with patch("fscrawler.indexer.documents_processed") as mock_counter:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))
            error_calls = [c for c in mock_counter.add.call_args_list if c[0][1].get("status") == "error"]
            assert len(error_calls) >= 1
            assert error_calls[0][0][1]["error.type"] == "circuit_breaking_exception"

    def test_bulk_duration_recorded_on_flush(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        with patch("fscrawler.indexer.bulk_duration") as mock_histogram:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))
            mock_histogram.record.assert_called_once()
            elapsed = mock_histogram.record.call_args[0][0]
            assert isinstance(elapsed, float) and elapsed >= 0
            assert mock_histogram.record.call_args[0][1]["status"] == "success"

    def test_bulk_exception_records_duration_with_error(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        mock_opensearch_client.bulk.side_effect = ConnectionError("cluster down")
        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        with patch("fscrawler.indexer.bulk_duration") as mock_histogram:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))
            mock_histogram.record.assert_called_once()
            attrs = mock_histogram.record.call_args[0][1]
            assert attrs["status"] == "error"
            assert attrs["error.type"] == "bulk_flush_error"

    def test_dlq_write_increments_dlq_records(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        mock_opensearch_client.bulk.return_value = {
            "took": 5, "errors": True,
            "items": [{"index": {"_index": "fscrawler_docs_test", "_id": expected_id, "status": 429,
                "error": {"type": "circuit_breaking_exception", "reason": "oom"}}}],
        }

        with patch("fscrawler.indexer.dlq_records") as mock_dlq:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))
            mock_dlq.add.assert_called_once()

    def test_pfq_write_increments_pfq_records(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        mock_opensearch_client.bulk.return_value = {
            "took": 5, "errors": True,
            "items": [{"index": {"_index": "fscrawler_docs_test", "_id": expected_id, "status": 400,
                "error": {"type": "mapper_parsing_exception", "reason": "bad field"}}}],
        }

        with patch("fscrawler.indexer.pfq_records") as mock_pfq:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))
            mock_pfq.add.assert_called_once()
