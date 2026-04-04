"""Cross-cutting contract tests.

These tests don't test a single module — they test invariants that span
multiple modules and must hold for the system to work correctly.
"""

from __future__ import annotations

import fnmatch
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from fscrawler.models import make_doc_id
from fscrawler.settings import FsSettings
from fscrawler.templates import get_index_templates

# ---------------------------------------------------------------------------
# Index naming <-> template patterns
# ---------------------------------------------------------------------------

JOB_NAMES = ["myjob", "test-job", "prod_crawler", "job123", "a"]


def _get_template_patterns() -> dict[str, list[str]]:
    """Load index_patterns from each index template, keyed by template name."""
    result = {}
    for name, body in get_index_templates():
        result[name] = body["index_patterns"]
    return result


def _make_settings(job_name: str) -> FsSettings:
    return FsSettings.from_dict({
        "name": job_name,
        "fs": {"url": "/data"},
        "elasticsearch": {"nodes": [{"url": "http://localhost:9200"}]},
    })


class TestIndexNamingMatchesTemplatePatterns:
    """Default index names must match exactly one index template pattern."""

    @pytest.mark.parametrize("job_name", JOB_NAMES)
    def test_docs_index_matches_docs_template(self, job_name: str) -> None:
        settings = _make_settings(job_name)
        patterns = _get_template_patterns()
        docs_patterns = patterns["fscrawler_index_template_docs"]
        assert any(
            fnmatch.fnmatch(settings.elasticsearch.index, p) for p in docs_patterns
        ), f"{settings.elasticsearch.index!r} does not match any of {docs_patterns}"

    @pytest.mark.parametrize("job_name", JOB_NAMES)
    def test_folders_index_matches_folders_template(self, job_name: str) -> None:
        settings = _make_settings(job_name)
        patterns = _get_template_patterns()
        folder_patterns = patterns["fscrawler_index_template_folders"]
        assert any(
            fnmatch.fnmatch(settings.elasticsearch.index_folder, p) for p in folder_patterns
        ), f"{settings.elasticsearch.index_folder!r} does not match any of {folder_patterns}"

    @pytest.mark.parametrize("job_name", JOB_NAMES)
    def test_history_index_matches_history_template(self, job_name: str) -> None:
        settings = _make_settings(job_name)
        patterns = _get_template_patterns()
        history_patterns = patterns["fscrawler_index_template_history"]
        assert any(
            fnmatch.fnmatch(settings.elasticsearch.index_history, p) for p in history_patterns
        ), f"{settings.elasticsearch.index_history!r} does not match any of {history_patterns}"

    @pytest.mark.parametrize("job_name", JOB_NAMES)
    def test_each_index_matches_exactly_one_template(self, job_name: str) -> None:
        settings = _make_settings(job_name)
        patterns = _get_template_patterns()
        all_patterns = []
        for pat_list in patterns.values():
            all_patterns.extend(pat_list)
        for index_name in [
            settings.elasticsearch.index,
            settings.elasticsearch.index_folder,
            settings.elasticsearch.index_history,
        ]:
            matches = [p for p in all_patterns if fnmatch.fnmatch(index_name, p)]
            assert len(matches) == 1, (
                f"{index_name!r} matched {len(matches)} patterns: {matches}"
            )


# ---------------------------------------------------------------------------
# Document ID consistency
# ---------------------------------------------------------------------------


class TestDocumentIdConsistency:
    """All code paths that generate document IDs must use make_doc_id()."""

    def test_indexer_uses_make_doc_id(self) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer
        from tests.conftest import make_settings

        settings = make_settings(elasticsearch={"bulk_size": 100})
        client = MagicMock(spec=FsCrawlerClient)
        indexer = BulkIndexer(client, settings)
        assert indexer._make_id("/test.txt") == make_doc_id("/test.txt")

    def test_watcher_index_uses_make_doc_id(self) -> None:
        from pathlib import Path

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

        call_kwargs = mock_client.index.call_args[1]
        assert call_kwargs["doc_id"] == make_doc_id(doc.path.virtual)

    def test_watcher_delete_uses_make_doc_id(self) -> None:
        from fscrawler.watcher import FsEventHandler
        from tests.conftest import make_settings

        settings = make_settings(fs={"url": "/data"})
        mock_client = MagicMock()
        mock_parser = MagicMock()
        mock_state = MagicMock()
        mock_state.paused = False

        handler = FsEventHandler(settings, mock_client, mock_parser, mock_state)
        handler._delete("/data/test.txt")

        call_kwargs = mock_client.delete.call_args[1]
        assert call_kwargs["doc_id"] == make_doc_id("/test.txt")

    def test_rest_upload_uses_make_doc_id(self) -> None:
        from fscrawler.rest_server import CrawlerState, create_app

        settings = _make_settings("test")
        mock_client = MagicMock()
        mock_client.info.return_value = {"version": {"number": "2.14.0"}}
        mock_parser = MagicMock()
        mock_doc = MagicMock()
        mock_doc.to_dict.return_value = {"content": "text"}
        mock_parser.parse_bytes.return_value = mock_doc

        app = create_app(settings=settings, client=mock_client, crawler_state=CrawlerState(), parser=mock_parser)
        tc = TestClient(app, raise_server_exceptions=True)

        boundary = "----TestBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="report.pdf"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + b"data" + f"\r\n--{boundary}--\r\n".encode()
        headers = {"content-type": f"multipart/form-data; boundary={boundary}"}

        tc.post("/_document", content=body, headers=headers)
        call_kwargs = mock_client.index.call_args[1]
        assert call_kwargs["doc_id"] == make_doc_id("/report.pdf")

    def test_rest_delete_uses_make_doc_id(self) -> None:
        from fscrawler.rest_server import CrawlerState, create_app

        settings = _make_settings("test")
        mock_client = MagicMock()
        mock_client.info.return_value = {"version": {"number": "2.14.0"}}

        app = create_app(settings=settings, client=mock_client, crawler_state=CrawlerState())
        tc = TestClient(app, raise_server_exceptions=True)

        tc.delete("/_document?filename=report.pdf")
        call_kwargs = mock_client.delete.call_args[1]
        assert call_kwargs["doc_id"] == make_doc_id("/report.pdf")


# ---------------------------------------------------------------------------
# Known limitation: watcher and REST skip history archival
# ---------------------------------------------------------------------------


class TestHistoryLimitationContract:
    """Codify the known limitation: only BulkIndexer supports keep_history.

    These tests serve as a contract — if someone adds history support to
    watcher or REST, they must update these tests to reflect the new behaviour.
    """

    def test_watcher_does_not_archive_on_create(self) -> None:
        from pathlib import Path

        from fscrawler.watcher import FsEventHandler
        from tests.conftest import make_document, make_settings

        settings = make_settings(fs={"url": "/data", "keep_history": True})
        mock_client = MagicMock()
        mock_parser = MagicMock()
        mock_state = MagicMock()
        mock_state.paused = False

        doc = make_document("/data/test.txt")
        mock_parser.parse.return_value = doc

        handler = FsEventHandler(settings, mock_client, mock_parser, mock_state)
        handler._index(Path("/data/test.txt"))

        mock_client.index.assert_called_once()
        mock_client.get_document_source.assert_not_called()

    def test_watcher_does_not_archive_on_delete(self) -> None:
        from fscrawler.watcher import FsEventHandler
        from tests.conftest import make_settings

        settings = make_settings(fs={"url": "/data", "keep_history": True, "remove_deleted": True})
        mock_client = MagicMock()
        mock_parser = MagicMock()
        mock_state = MagicMock()
        mock_state.paused = False

        handler = FsEventHandler(settings, mock_client, mock_parser, mock_state)
        handler._delete("/data/test.txt")

        mock_client.delete.assert_called_once()
        mock_client.get_document_source.assert_not_called()

    def test_rest_upload_does_not_archive(self) -> None:
        from fscrawler.rest_server import CrawlerState, create_app

        settings = FsSettings.from_dict({
            "name": "test",
            "fs": {"url": "/data", "keep_history": True},
            "elasticsearch": {"nodes": [{"url": "http://localhost:9200"}]},
        })
        mock_client = MagicMock()
        mock_client.info.return_value = {"version": {"number": "2.14.0"}}
        mock_parser = MagicMock()
        mock_doc = MagicMock()
        mock_doc.to_dict.return_value = {"content": "text"}
        mock_parser.parse_bytes.return_value = mock_doc

        app = create_app(settings=settings, client=mock_client, crawler_state=CrawlerState(), parser=mock_parser)
        tc = TestClient(app, raise_server_exceptions=True)

        boundary = "----TestBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="test.pdf"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + b"data" + f"\r\n--{boundary}--\r\n".encode()
        headers = {"content-type": f"multipart/form-data; boundary={boundary}"}

        tc.post("/_document", content=body, headers=headers)

        mock_client.index.assert_called_once()
        mock_client.get_document_source.assert_not_called()
