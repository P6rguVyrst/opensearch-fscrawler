"""End-to-end pipeline tests for _crawl_once.

These tests exist to catch a specific class of bug: an output channel (an
index, a file, an API endpoint) is wired up structurally — the index is
created, templates are pushed — but nothing ever writes to it.

Unit tests on individual components cannot catch this because they test each
piece in isolation. These tests run the full _crawl_once pipeline against
mocked OpenSearch and Tika and assert on *what ended up in each index*, not
on which internal functions were called.

Rule: every index that exists must have a test that asserts documents land
in it after a realistic crawl.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from fscrawler.settings import FsSettings


def make_settings(url: str, **overrides: Any) -> FsSettings:
    fs: dict[str, Any] = {"url": url, "index_folders": True}
    fs.update(overrides)
    return FsSettings.from_dict({
        "name": "test",
        "fs": fs,
        "elasticsearch": {
            "nodes": [{"url": "http://localhost:9200"}],
            "index": "test_docs",
            "index_folder": "test_folder",
            "bulk_size": 100,
            "byte_size": "10mb",
        },
    })


def indices_written(mock_os: MagicMock) -> set[str]:
    """Return the set of index names that received at least one bulk operation."""
    indices: set[str] = set()
    for call in mock_os.bulk.call_args_list:
        body = call[1].get("body") or call[0][0]
        for item in body:
            if isinstance(item, dict):
                for op in ("index", "delete", "update"):
                    if op in item:
                        indices.add(item[op]["_index"])
    return indices


def docs_for_index(mock_os: MagicMock, index: str) -> list[dict[str, Any]]:
    """Return the document bodies written to a specific index."""
    docs = []
    for call in mock_os.bulk.call_args_list:
        body = call[1].get("body") or call[0][0]
        i = 0
        while i < len(body) - 1:
            action = body[i]
            doc = body[i + 1]
            if isinstance(action, dict) and "index" in action:
                if action["index"].get("_index") == index:
                    docs.append(doc)
            i += 2
    return docs


# ---------------------------------------------------------------------------
# Core pipeline: both indices receive documents
# ---------------------------------------------------------------------------


class TestBothIndicesReceiveDocuments:
    """Catch the pattern: index created and templated but nothing ever writes to it."""

    def test_docs_index_receives_file_documents(
        self,
        tmp_path: Path,
        mock_opensearch_client: MagicMock,
        mock_tika: MagicMock,
    ) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "report.txt").write_text("hello", encoding="utf-8")

        from fscrawler.cli import _crawl_once
        from fscrawler.client import FsCrawlerClient
        from fscrawler.parser import TikaParser

        settings = make_settings(str(data))
        client = FsCrawlerClient(settings)
        parser = TikaParser(settings, tika_url=settings.fs.tika_url)
        _crawl_once(settings, client, parser, tmp_path)

        assert "test_docs" in indices_written(mock_opensearch_client), (
            "No documents were written to the docs index"
        )

    def test_folder_index_receives_directory_documents(
        self,
        tmp_path: Path,
        mock_opensearch_client: MagicMock,
        mock_tika: MagicMock,
    ) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "sub").mkdir()
        (data / "sub" / "file.txt").write_text("hello", encoding="utf-8")

        from fscrawler.cli import _crawl_once
        from fscrawler.client import FsCrawlerClient
        from fscrawler.parser import TikaParser

        settings = make_settings(str(data))
        client = FsCrawlerClient(settings)
        parser = TikaParser(settings, tika_url=settings.fs.tika_url)
        _crawl_once(settings, client, parser, tmp_path)

        assert "test_folder" in indices_written(mock_opensearch_client), (
            "No documents were written to the folder index — "
            "index_folders=True but folder documents never reached OpenSearch"
        )

    def test_folder_index_empty_when_index_folders_false(
        self,
        tmp_path: Path,
        mock_opensearch_client: MagicMock,
        mock_tika: MagicMock,
    ) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "sub").mkdir()
        (data / "sub" / "file.txt").write_text("hello", encoding="utf-8")

        from fscrawler.cli import _crawl_once
        from fscrawler.client import FsCrawlerClient
        from fscrawler.parser import TikaParser

        settings = make_settings(str(data), index_folders=False)
        client = FsCrawlerClient(settings)
        parser = TikaParser(settings, tika_url=settings.fs.tika_url)
        _crawl_once(settings, client, parser, tmp_path)

        assert "test_folder" not in indices_written(mock_opensearch_client)


# ---------------------------------------------------------------------------
# Folder document shape
# ---------------------------------------------------------------------------


class TestFolderDocumentShape:
    """Folder documents must have the correct structure."""

    def _run(
        self,
        tmp_path: Path,
        mock_opensearch_client: MagicMock,
        mock_tika: MagicMock,
    ) -> list[dict[str, Any]]:
        data = tmp_path / "data"
        data.mkdir()
        (data / "sub").mkdir()
        (data / "sub" / "file.txt").write_text("hello", encoding="utf-8")

        from fscrawler.cli import _crawl_once
        from fscrawler.client import FsCrawlerClient
        from fscrawler.parser import TikaParser

        settings = make_settings(str(data))
        client = FsCrawlerClient(settings)
        parser = TikaParser(settings, tika_url=settings.fs.tika_url)
        _crawl_once(settings, client, parser, tmp_path)
        return docs_for_index(mock_opensearch_client, "test_folder")

    def test_folder_document_has_path_block(
        self, tmp_path: Path, mock_opensearch_client: MagicMock, mock_tika: MagicMock
    ) -> None:
        docs = self._run(tmp_path, mock_opensearch_client, mock_tika)
        assert all("path" in d for d in docs)

    def test_folder_document_path_has_real_root_virtual(
        self, tmp_path: Path, mock_opensearch_client: MagicMock, mock_tika: MagicMock
    ) -> None:
        docs = self._run(tmp_path, mock_opensearch_client, mock_tika)
        for doc in docs:
            assert "real" in doc["path"]
            assert "root" in doc["path"]
            assert "virtual" in doc["path"]

    def test_root_directory_virtual_path_is_slash(
        self, tmp_path: Path, mock_opensearch_client: MagicMock, mock_tika: MagicMock
    ) -> None:
        docs = self._run(tmp_path, mock_opensearch_client, mock_tika)
        virtuals = [d["path"]["virtual"] for d in docs]
        assert "/" in virtuals

    def test_subdirectory_virtual_path_is_relative(
        self, tmp_path: Path, mock_opensearch_client: MagicMock, mock_tika: MagicMock
    ) -> None:
        docs = self._run(tmp_path, mock_opensearch_client, mock_tika)
        virtuals = [d["path"]["virtual"] for d in docs]
        assert "/sub" in virtuals
