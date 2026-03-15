# Licensed under the Apache License, Version 2.0
"""Unit tests for fscrawler.watcher (watchdog-based filesystem event handler)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fscrawler.settings import FsSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_settings(**overrides) -> FsSettings:
    base = {"name": "test", "fs": {"url": "/data"}}
    base.update(overrides)
    return FsSettings.from_dict(base)


def make_handler(settings=None, paused=False):
    from fscrawler.watcher import FsEventHandler
    from fscrawler.rest_server import CrawlerState

    s = settings or make_settings()
    client = MagicMock()
    parser = MagicMock()
    mock_doc = MagicMock()
    mock_doc.to_dict.return_value = {"content": "text"}
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
