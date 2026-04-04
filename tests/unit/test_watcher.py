# Licensed under the Apache License, Version 2.0
"""Unit tests for fscrawler.watcher (watchdog-based filesystem event handler)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from tests.conftest import make_settings


def make_handler(settings=None, paused=False):
    from fscrawler.watcher import FsEventHandler
    from fscrawler.rest_server import CrawlerState

    s = settings or make_settings()
    client = MagicMock()
    parser = MagicMock()
    mock_doc = MagicMock()
    mock_doc.to_dict.return_value = {"content": "text"}
    mock_doc.path.virtual = "/doc.pdf"
    parser.parse.return_value = mock_doc

    state = CrawlerState()
    state.paused = paused

    return FsEventHandler(s, client, parser, state), client, parser


def _file_event(cls, path: str, is_directory: bool = False):
    evt = MagicMock()
    evt.src_path = path
    evt.is_directory = is_directory
    return evt


# ---------------------------------------------------------------------------
# on_created
# ---------------------------------------------------------------------------


class TestOnCreated:
    def test_indexes_new_file(self) -> None:
        handler, client, parser = make_handler()
        handler.on_created(_file_event(None, "/data/doc.pdf"))
        parser.parse.assert_called_once()
        client.index.assert_called_once()

    def test_ignores_directory_events(self) -> None:
        handler, client, _ = make_handler()
        handler.on_created(_file_event(None, "/data/subdir", is_directory=True))
        client.index.assert_not_called()

    def test_skipped_when_paused(self) -> None:
        handler, client, _ = make_handler(paused=True)
        handler.on_created(_file_event(None, "/data/doc.pdf"))
        client.index.assert_not_called()

    def test_parse_error_does_not_raise(self) -> None:
        handler, client, parser = make_handler()
        parser.parse.side_effect = RuntimeError("tika down")
        # Must not propagate — watchdog would kill the observer thread
        handler.on_created(_file_event(None, "/data/doc.pdf"))

    def test_excludes_pattern_skips_file(self) -> None:
        settings = make_settings(fs={"url": "/data", "excludes": ["*.tmp"]})
        handler, client, _ = make_handler(settings=settings)
        handler.on_created(_file_event(None, "/data/scratch.tmp"))
        client.index.assert_not_called()

    def test_includes_pattern_allows_matching_file(self) -> None:
        settings = make_settings(fs={"url": "/data", "includes": ["*.pdf"]})
        handler, client, _ = make_handler(settings=settings)
        handler.on_created(_file_event(None, "/data/report.pdf"))
        client.index.assert_called_once()

    def test_includes_pattern_blocks_non_matching_file(self) -> None:
        settings = make_settings(fs={"url": "/data", "includes": ["*.pdf"]})
        handler, client, _ = make_handler(settings=settings)
        handler.on_created(_file_event(None, "/data/notes.txt"))
        client.index.assert_not_called()


# ---------------------------------------------------------------------------
# on_modified
# ---------------------------------------------------------------------------


class TestOnModified:
    def test_reindexes_modified_file(self) -> None:
        handler, client, parser = make_handler()
        handler.on_modified(_file_event(None, "/data/doc.pdf"))
        parser.parse.assert_called_once()
        client.index.assert_called_once()

    def test_ignores_directory_events(self) -> None:
        handler, client, _ = make_handler()
        handler.on_modified(_file_event(None, "/data/subdir", is_directory=True))
        client.index.assert_not_called()

    def test_skipped_when_paused(self) -> None:
        handler, client, _ = make_handler(paused=True)
        handler.on_modified(_file_event(None, "/data/doc.pdf"))
        client.index.assert_not_called()


# ---------------------------------------------------------------------------
# on_deleted
# ---------------------------------------------------------------------------


class TestOnDeleted:
    def _make_delete_handler(self, **kwargs):
        """Create a handler with remove_deleted=True for delete tests."""
        settings = make_settings(fs={"url": "/data", "remove_deleted": True})
        return make_handler(settings=settings, **kwargs)

    def test_deletes_from_index(self) -> None:
        handler, client, _ = self._make_delete_handler()
        handler.on_deleted(_file_event(None, "/data/old.pdf"))
        client.delete.assert_called_once()

    def test_ignores_directory_events(self) -> None:
        handler, client, _ = self._make_delete_handler()
        handler.on_deleted(_file_event(None, "/data/subdir", is_directory=True))
        client.delete.assert_not_called()

    def test_skipped_when_paused(self) -> None:
        handler, client, _ = self._make_delete_handler(paused=True)
        handler.on_deleted(_file_event(None, "/data/old.pdf"))
        client.delete.assert_not_called()

    def test_delete_error_does_not_raise(self) -> None:
        handler, client, _ = self._make_delete_handler()
        client.delete.side_effect = RuntimeError("connection lost")
        handler.on_deleted(_file_event(None, "/data/old.pdf"))

    def test_skipped_when_remove_deleted_false(self) -> None:
        """When remove_deleted is False, on_deleted must not call client.delete."""
        settings = make_settings(fs={"url": "/data", "remove_deleted": False})
        handler, client, _ = make_handler(settings=settings)
        handler.on_deleted(_file_event(None, "/data/old.pdf"))
        client.delete.assert_not_called()


# ---------------------------------------------------------------------------
# New ID strategy (SHA256 of virtual path)
# ---------------------------------------------------------------------------


class TestWatcherNewId:
    def test_index_uses_virtual_path_based_id(self) -> None:
        import hashlib
        from pathlib import Path
        from unittest.mock import MagicMock

        from fscrawler.watcher import FsEventHandler
        from tests.conftest import make_document, make_settings

        settings = make_settings(fs={"url": "/data"})
        mock_client = MagicMock()
        mock_parser = MagicMock()
        mock_state = MagicMock()
        mock_state.paused = False

        doc = make_document("/data/test.txt")
        mock_parser.parse.return_value = doc

        handler = FsEventHandler(settings, mock_client, mock_parser, mock_state)
        handler._index(Path("/data/test.txt"))

        # Verify the doc_id passed to client.index is SHA256 of virtual path
        mock_client.index.assert_called_once()
        call_kwargs = mock_client.index.call_args[1]
        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        assert call_kwargs["doc_id"] == expected_id

    def test_delete_uses_virtual_path(self) -> None:
        import hashlib
        from unittest.mock import MagicMock

        from fscrawler.watcher import FsEventHandler
        from tests.conftest import make_settings

        settings = make_settings(fs={"url": "/data"})
        mock_client = MagicMock()
        mock_parser = MagicMock()
        mock_state = MagicMock()
        mock_state.paused = False

        handler = FsEventHandler(settings, mock_client, mock_parser, mock_state)
        handler._delete("/data/test.txt")

        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        call_kwargs = mock_client.delete.call_args[1]
        assert call_kwargs["doc_id"] == expected_id


# ---------------------------------------------------------------------------
# WAL + DLQ integration
# ---------------------------------------------------------------------------


class TestFsEventHandlerWalDlq:
    """Tests for WAL durability and DLQ routing in FsEventHandler."""

    def _make_handler(self, wal=None):
        from fscrawler.watcher import FsEventHandler

        settings = make_settings(fs={"url": "/data", "remove_deleted": True})
        client = MagicMock()
        parser = MagicMock()
        crawler_state = MagicMock(paused=False)

        mock_doc = MagicMock()
        mock_doc.path.virtual = "/test.txt"
        mock_doc.path.real = "/data/test.txt"
        mock_doc.to_dict.return_value = {"content": "hello"}
        parser.parse.return_value = mock_doc

        handler = FsEventHandler(settings, client, parser, crawler_state, wal=wal)
        return handler, client, parser, mock_doc

    def test_index_writes_to_wal_before_client(self) -> None:
        mock_wal = MagicMock()
        handler, client, parser, mock_doc = self._make_handler(wal=mock_wal)

        handler._index(Path("/data/test.txt"))

        mock_wal.append.assert_called_once()
        record = mock_wal.append.call_args[0][0]
        assert record["action"] == "index"
        assert record["doc_id"] is not None
        assert record["payload"] == {"content": "hello"}

    def test_index_success_checkpoints_wal(self) -> None:
        mock_wal = MagicMock()
        handler, client, parser, mock_doc = self._make_handler(wal=mock_wal)

        handler._index(Path("/data/test.txt"))

        mock_wal.checkpoint.assert_called_once()
        checkpoint_ids = mock_wal.checkpoint.call_args[0][0]
        assert isinstance(checkpoint_ids, set)
        assert len(checkpoint_ids) == 1

    def test_index_failure_writes_to_dlq(self) -> None:
        mock_wal = MagicMock()
        handler, client, parser, mock_doc = self._make_handler(wal=mock_wal)
        client.index.side_effect = RuntimeError("opensearch down")

        handler._index(Path("/data/test.txt"))

        # DLQ write via index_raw
        client.index_raw.assert_called_once()
        call_kwargs = client.index_raw.call_args[1]
        assert call_kwargs["index"] == "fscrawler_dlq"
        body = call_kwargs["body"]
        assert body["error_type"] == "RuntimeError"
        assert body["error_message"] == "opensearch down"

    def test_delete_writes_to_wal(self) -> None:
        mock_wal = MagicMock()
        handler, client, parser, mock_doc = self._make_handler(wal=mock_wal)

        handler._delete("/data/test.txt")

        mock_wal.append.assert_called_once()
        record = mock_wal.append.call_args[0][0]
        assert record["action"] == "delete"
        assert record["doc_id"] is not None

    def test_handler_works_without_wal(self) -> None:
        handler, client, parser, mock_doc = self._make_handler(wal=None)

        # Should work without error
        handler._index(Path("/data/test.txt"))
        client.index.assert_called_once()

        handler._delete("/data/test.txt")
        client.delete.assert_called_once()

    def test_index_to_dict_failure_writes_dlq_with_none_payload(self) -> None:
        """If doc.to_dict() raises, DLQ record should have payload=None (not UnboundLocalError)."""
        mock_wal = MagicMock()
        handler, client, parser, mock_doc = self._make_handler(wal=mock_wal)
        mock_doc.to_dict.side_effect = RuntimeError("serialization failed")

        handler._index(Path("/data/test.txt"))

        client.index_raw.assert_called_once()
        call_kwargs = client.index_raw.call_args[1]
        assert call_kwargs["index"] == "fscrawler_dlq"
        assert call_kwargs["body"]["payload"] is None

    def test_delete_failure_writes_to_dlq(self) -> None:
        """Failed deletes should be written to DLQ for retry."""
        mock_wal = MagicMock()
        handler, client, parser, mock_doc = self._make_handler(wal=mock_wal)
        client.delete.side_effect = RuntimeError("connection lost")

        handler._delete("/data/test.txt")

        client.index_raw.assert_called_once()
        call_kwargs = client.index_raw.call_args[1]
        assert call_kwargs["index"] == "fscrawler_dlq"
        body = call_kwargs["body"]
        assert body["action"] == "delete"
        assert body["error_type"] == "RuntimeError"

    def test_delete_early_exception_uses_fallback_doc_id(self) -> None:
        """B1: If _delete() fails before doc_id is assigned, use a fallback ID for DLQ."""
        import hashlib

        mock_wal = MagicMock()
        handler, client, parser, mock_doc = self._make_handler(wal=mock_wal)

        # Simulate: client.delete raises, but doc_id was never assigned
        # because make_doc_id raised on first call but works on fallback call
        call_count = 0
        original = handler._client.delete

        def fail_on_first(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("hash failed")
            return original(*a, **kw)

        with patch("fscrawler.watcher.make_doc_id", side_effect=fail_on_first):
            handler._delete("/data/test.txt")

        # DLQ write should still happen with a fallback doc_id
        client.index_raw.assert_called_once()
        call_kwargs = client.index_raw.call_args[1]
        assert call_kwargs["index"] == "fscrawler_dlq"
        body = call_kwargs["body"]
        assert body["doc_id"] is not None


# ---------------------------------------------------------------------------
# OTel metrics instrumentation
# ---------------------------------------------------------------------------


class TestWatcherMetrics:
    def test_index_success_increments_documents_processed(self) -> None:
        with patch("fscrawler.watcher.documents_processed") as mock_counter:
            handler, client, parser = make_handler()
            handler.on_created(_file_event(None, "/data/doc.pdf"))
            mock_counter.add.assert_called()
            call_args = mock_counter.add.call_args
            assert call_args[0][1]["status"] == "success"

    def test_index_failure_increments_documents_processed_error(self) -> None:
        with patch("fscrawler.watcher.documents_processed") as mock_counter:
            handler, client, parser = make_handler()
            parser.parse.side_effect = RuntimeError("tika down")
            handler.on_created(_file_event(None, "/data/doc.pdf"))
            mock_counter.add.assert_called()
            call_args = mock_counter.add.call_args
            assert call_args[0][1]["status"] == "error"
            assert call_args[0][1]["error.type"] == "RuntimeError"

    def test_index_failure_increments_dlq_records(self) -> None:
        with patch("fscrawler.watcher.dlq_records") as mock_dlq:
            handler, client, parser = make_handler()
            client.index.side_effect = RuntimeError("opensearch down")
            handler.on_created(_file_event(None, "/data/doc.pdf"))
            mock_dlq.add.assert_called_once()

    def test_delete_success_increments_documents_processed(self) -> None:
        """_delete() must emit documents_processed on success (I1)."""
        settings = make_settings(fs={"url": "/data", "remove_deleted": True})
        with patch("fscrawler.watcher.documents_processed") as mock_counter:
            handler, client, _ = make_handler(settings=settings)
            handler.on_deleted(_file_event(None, "/data/old.pdf"))
            mock_counter.add.assert_called()
            call_args = mock_counter.add.call_args
            assert call_args[0][1]["status"] == "success"

    def test_delete_failure_increments_documents_processed_error(self) -> None:
        """_delete() must emit documents_processed on error (I1)."""
        settings = make_settings(fs={"url": "/data", "remove_deleted": True})
        with patch("fscrawler.watcher.documents_processed") as mock_counter:
            handler, client, _ = make_handler(settings=settings)
            client.delete.side_effect = RuntimeError("connection lost")
            handler.on_deleted(_file_event(None, "/data/old.pdf"))
            mock_counter.add.assert_called()
            # Should have at least one error status call
            error_calls = [
                c for c in mock_counter.add.call_args_list
                if c[0][1].get("status") == "error"
            ]
            assert len(error_calls) >= 1


# ---------------------------------------------------------------------------
# Full-path pattern matching in watcher (Task 1)
# ---------------------------------------------------------------------------


class TestWatcherFullPathFilters:
    """Patterns containing '/' must match against the virtual path in the watcher."""

    def test_exclude_with_slash_skips_matching_path(self) -> None:
        settings = make_settings(fs={"url": "/data", "excludes": ["logs/*"]})
        handler, client, _ = make_handler(settings=settings)
        handler.on_created(_file_event(None, "/data/logs/debug.log"))
        client.index.assert_not_called()

    def test_exclude_with_slash_allows_non_matching_path(self) -> None:
        settings = make_settings(fs={"url": "/data", "excludes": ["logs/*"]})
        handler, client, _ = make_handler(settings=settings)
        handler.on_created(_file_event(None, "/data/keep/debug.log"))
        client.index.assert_called_once()

    def test_include_with_slash_matches_virtual_path(self) -> None:
        settings = make_settings(fs={"url": "/data", "includes": ["docs/*.pdf"]})
        handler, client, _ = make_handler(settings=settings)
        handler.on_created(_file_event(None, "/data/docs/report.pdf"))
        client.index.assert_called_once()

    def test_include_with_slash_blocks_non_matching_path(self) -> None:
        settings = make_settings(fs={"url": "/data", "includes": ["docs/*.pdf"]})
        handler, client, _ = make_handler(settings=settings)
        handler.on_created(_file_event(None, "/data/other/report.pdf"))
        client.index.assert_not_called()

    def test_on_modified_respects_path_patterns(self) -> None:
        settings = make_settings(fs={"url": "/data", "excludes": ["logs/*"]})
        handler, client, _ = make_handler(settings=settings)
        handler.on_modified(_file_event(None, "/data/logs/debug.log"))
        client.index.assert_not_called()


# ---------------------------------------------------------------------------
# on_moved (Task 8)
# ---------------------------------------------------------------------------


class TestOnMoved:
    """Handle file move events — reindex at new path.

    Upstream: https://github.com/dadoonet/fscrawler/issues/1300
    """

    def test_moved_file_reindexed_at_new_path(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / "new_name.txt").write_bytes(b"content")

        settings = make_settings(fs={"url": str(tmp_path / "data"), "remove_deleted": True})
        handler, client, parser = make_handler(settings=settings)

        event = MagicMock()
        event.is_directory = False
        event.src_path = str(tmp_path / "data" / "old_name.txt")
        event.dest_path = str(tmp_path / "data" / "new_name.txt")

        handler.on_moved(event)

        # Old path should be deleted
        client.delete.assert_called_once()
        # New path should be indexed
        client.index.assert_called_once()

    def test_moved_directory_ignored(self) -> None:
        settings = make_settings(fs={"url": "/data", "remove_deleted": True})
        handler, client, _ = make_handler(settings=settings)

        event = MagicMock()
        event.is_directory = True
        event.src_path = "/data/old_dir"
        event.dest_path = "/data/new_dir"

        handler.on_moved(event)

        client.delete.assert_not_called()
        client.index.assert_not_called()

    def test_moved_respects_paused_state(self) -> None:
        settings = make_settings(fs={"url": "/data", "remove_deleted": True})
        handler, client, _ = make_handler(settings=settings, paused=True)

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/data/old.txt"
        event.dest_path = "/data/new.txt"

        handler.on_moved(event)

        client.delete.assert_not_called()
        client.index.assert_not_called()

    def test_moved_skips_delete_when_remove_deleted_false(self, tmp_path: Path) -> None:
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / "new.txt").write_bytes(b"content")

        settings = make_settings(fs={"url": str(tmp_path / "data"), "remove_deleted": False})
        handler, client, parser = make_handler(settings=settings)

        event = MagicMock()
        event.is_directory = False
        event.src_path = str(tmp_path / "data" / "old.txt")
        event.dest_path = str(tmp_path / "data" / "new.txt")

        handler.on_moved(event)

        # Should NOT delete old path when remove_deleted is False
        client.delete.assert_not_called()
        # Should still index new path
        client.index.assert_called_once()
