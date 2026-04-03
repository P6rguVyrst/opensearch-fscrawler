"""Unit tests for fscrawler.indexer."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

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
