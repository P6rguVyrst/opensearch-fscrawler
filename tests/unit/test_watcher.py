# Licensed under the Apache License, Version 2.0
"""Unit tests for fscrawler.watcher (watchdog-based filesystem event handler)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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
    def test_deletes_from_index(self) -> None:
        handler, client, _ = make_handler()
        handler.on_deleted(_file_event(None, "/data/old.pdf"))
        client.delete.assert_called_once()

    def test_ignores_directory_events(self) -> None:
        handler, client, _ = make_handler()
        handler.on_deleted(_file_event(None, "/data/subdir", is_directory=True))
        client.delete.assert_not_called()

    def test_skipped_when_paused(self) -> None:
        handler, client, _ = make_handler(paused=True)
        handler.on_deleted(_file_event(None, "/data/old.pdf"))
        client.delete.assert_not_called()

    def test_delete_error_does_not_raise(self) -> None:
        handler, client, _ = make_handler()
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
