"""Unit tests for fscrawler.indexer."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from fscrawler.settings import FsSettings
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
    def test_id_is_file_path_when_filename_as_id_true(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = FsSettings.from_dict(
            {
                "name": "test",
                "fs": {"url": "/data", "filename_as_id": True},
                "elasticsearch": {
                    "nodes": [{"url": "http://localhost:9200"}],
                    "index": "test_docs",
                    "bulk_size": 1,
                },
            }
        )
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc = make_document("/data/myfile.txt")
        indexer.add(doc)

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        # The index action should use the file path as ID
        index_actions = [op for op in body if "index" in op]
        assert any("myfile.txt" in str(a["index"].get("_id", "")) for a in index_actions)

    def test_id_is_hash_when_filename_as_id_false(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = FsSettings.from_dict(
            {
                "name": "test",
                "fs": {"url": "/data", "filename_as_id": False},
                "elasticsearch": {
                    "nodes": [{"url": "http://localhost:9200"}],
                    "index": "test_docs",
                    "bulk_size": 1,
                },
            }
        )
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        path = "/data/myfile.txt"
        doc = make_document(path)
        indexer.add(doc)

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        index_actions = [op for op in body if "index" in op]
        expected_id = hashlib.sha256(path.encode()).hexdigest()
        assert any(a["index"].get("_id") == expected_id for a in index_actions)


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

        indexer.delete("/data/gone.txt")

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

        indexer.delete("/data/gone.txt")

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        delete_ops = [op for op in body if "delete" in op]
        assert delete_ops[0]["delete"]["_index"] == "test_docs"


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# content_hash_as_id
# ---------------------------------------------------------------------------


class TestContentHashAsId:
    def _make_settings(self, **fs_overrides: Any) -> FsSettings:
        fs: dict[str, Any] = {"url": "/data", "content_hash_as_id": True}
        fs.update(fs_overrides)
        return FsSettings.from_dict({"name": "test", "fs": fs, "elasticsearch": {"nodes": [{"url": "http://localhost:9200"}], "index": "test_docs", "bulk_size": 100}})

    def test_uses_checksum_as_doc_id(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_settings()
        doc = make_document("/data/file.txt")
        doc.file.checksum = "abc123"

        with BulkIndexer(client := FsCrawlerClient(settings), settings) as indexer:
            indexer.add(doc)

        action = mock_opensearch_client.bulk.call_args[1]["body"][0]
        assert action["index"]["_id"] == "abc123"

    def test_different_content_different_id(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_settings()
        doc_v1 = make_document("/data/file.txt")
        doc_v1.file.checksum = "hash_v1"
        doc_v2 = make_document("/data/file.txt")
        doc_v2.file.checksum = "hash_v2"

        with BulkIndexer(client := FsCrawlerClient(settings), settings) as indexer:
            indexer.add(doc_v1)
            indexer.add(doc_v2)

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        ids = [body[i]["index"]["_id"] for i in range(0, len(body), 2)]
        assert ids == ["hash_v1", "hash_v2"]

    def test_delete_is_noop(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_settings()
        with BulkIndexer(client := FsCrawlerClient(settings), settings) as indexer:
            indexer.delete("/data/file.txt")

        mock_opensearch_client.bulk.assert_not_called()


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
