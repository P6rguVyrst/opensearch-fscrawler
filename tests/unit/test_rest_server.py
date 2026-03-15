# Licensed under the Apache License, Version 2.0
"""Unit tests for fscrawler.rest_server (FastAPI application).

Tests are written against the *interface* of rest_server.create_app() and
drive the implementation via TDD.  All external I/O (OpenSearch, Tika, disk)
is replaced by mocks.

REST surface mirrors the Java FSCrawler REST layer:
  GET  /                       – server status
  POST /_document              – upload document (multipart)
  PUT  /_document/{id}         – upload with explicit id
  DELETE /_document            – delete by filename
  DELETE /_document/{id}       – delete by id
  POST /_crawler/pause         – pause background crawler
  POST /_crawler/resume        – resume background crawler
  GET  /_crawler/status        – crawler state
  DELETE /_crawler/checkpoint  – clear checkpoint (requires pause/stop)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from fscrawler.settings import FsSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_settings(**overrides: Any) -> FsSettings:
    base: dict[str, Any] = {
        "name": "test-job",
        "fs": {"url": "/data"},
        "elasticsearch": {"nodes": [{"url": "http://localhost:9200"}]},
        "rest": {"url": "http://127.0.0.1:8080", "enable_cors": False},
    }
    base.update(overrides)
    return FsSettings.from_dict(base)


def make_mock_client() -> MagicMock:
    client = MagicMock()
    client.info.return_value = {"version": {"number": "2.14.0"}, "cluster_name": "test"}
    return client


def make_mock_parser(content: str = "extracted text") -> MagicMock:
    """Return a MagicMock that behaves like TikaParser."""
    mock_doc = MagicMock()
    mock_doc.to_dict.return_value = {"content": content, "file": {}}
    parser = MagicMock()
    parser.parse_bytes.return_value = mock_doc
    return parser


def make_mock_crawler_state(*, paused: bool = False) -> MagicMock:
    state = MagicMock()
    state.paused = paused
    state.last_checkpoint = None
    return state


def make_app(
    settings: FsSettings | None = None,
    client: MagicMock | None = None,
    crawler_state: MagicMock | None = None,
    parser: MagicMock | None = None,
) -> TestClient:
    from fscrawler.rest_server import create_app

    s = settings or make_settings()
    c = client or make_mock_client()
    cs = crawler_state or make_mock_crawler_state()
    p = parser or make_mock_parser()
    app = create_app(settings=s, client=c, crawler_state=cs, parser=p)
    return TestClient(app, raise_server_exceptions=True)


def _multipart_body(
    filename: str = "test.pdf",
    data: bytes = b"%PDF-1.4 fake",
    field: str = "file",
) -> tuple[dict[str, str], bytes]:
    """Build a minimal multipart/form-data body and matching Content-Type header.

    Uses Python stdlib only — no python-multipart dependency.
    """
    boundary = "----FSCrawlerTestBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    headers = {"content-type": f"multipart/form-data; boundary={boundary}"}
    return headers, body


# ---------------------------------------------------------------------------
# GET / – server status
# ---------------------------------------------------------------------------


class TestServerStatus:
    def test_returns_200(self) -> None:
        tc = make_app()
        assert tc.get("/").status_code == 200

    def test_response_contains_version(self) -> None:
        body = make_app().get("/").json()
        assert body.get("version", "") != ""

    def test_response_contains_ok_flag(self) -> None:
        assert make_app().get("/").json().get("ok") is True

    def test_response_contains_job_name(self) -> None:
        tc = make_app(settings=make_settings(name="my-job"))
        body = tc.get("/").json()
        assert body.get("settings", {}).get("name") == "my-job"

    def test_response_contains_elasticsearch_version(self) -> None:
        client = make_mock_client()
        client.info.return_value = {"version": {"number": "8.13.0"}, "cluster_name": "prod"}
        body = make_app(client=client).get("/").json()
        assert body["elasticsearch"]["version"] == "8.13.0"

    def test_client_error_returns_503(self) -> None:
        client = make_mock_client()
        client.info.side_effect = Exception("connection refused")
        assert make_app(client=client).get("/").status_code == 503


# ---------------------------------------------------------------------------
# POST /_document – document upload (multipart)
# ---------------------------------------------------------------------------


class TestDocumentUpload:
    def test_upload_returns_200(self) -> None:
        headers, body = _multipart_body()
        assert make_app().post("/_document", content=body, headers=headers).status_code == 200

    def test_upload_response_contains_filename(self) -> None:
        headers, body = _multipart_body("report.pdf")
        resp = make_app().post("/_document", content=body, headers=headers).json()
        assert resp.get("filename") == "report.pdf"

    def test_upload_response_contains_url(self) -> None:
        headers, body = _multipart_body("report.pdf")
        resp = make_app().post("/_document", content=body, headers=headers).json()
        assert "url" in resp
        assert "report.pdf" in resp["url"]

    def test_upload_without_file_returns_422(self) -> None:
        resp = make_app().post("/_document")
        assert resp.status_code == 422

    def test_upload_calls_client_index(self) -> None:
        client = make_mock_client()
        headers, body = _multipart_body()
        make_app(client=client).post("/_document", content=body, headers=headers)
        client.index.assert_called_once()

    def test_upload_simulate_does_not_index(self) -> None:
        client = make_mock_client()
        headers, body = _multipart_body()
        make_app(client=client).post("/_document?simulate=true", content=body, headers=headers)
        client.index.assert_not_called()

    def test_upload_simulate_returns_ok(self) -> None:
        headers, body = _multipart_body()
        resp = make_app().post("/_document?simulate=true", content=body, headers=headers).json()
        assert resp.get("ok") is True

    def test_upload_debug_returns_doc_in_response(self) -> None:
        parser = make_mock_parser()
        headers, body = _multipart_body()
        resp = make_app(parser=parser).post(
            "/_document?debug=true", content=body, headers=headers
        ).json()
        assert "doc" in resp

    def test_upload_explicit_id_in_query(self) -> None:
        client = make_mock_client()
        headers, body = _multipart_body()
        make_app(client=client).post("/_document?id=my-id-123", content=body, headers=headers)
        _, kwargs = client.index.call_args
        assert kwargs.get("doc_id") == "my-id-123"

    def test_upload_tika_failure_returns_500(self) -> None:
        parser = make_mock_parser()
        parser.parse_bytes.side_effect = RuntimeError("tika unavailable")
        headers, body = _multipart_body()
        resp = make_app(parser=parser).post("/_document", content=body, headers=headers)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# PUT /_document/{id} – upload with explicit path id
# ---------------------------------------------------------------------------


class TestDocumentUploadWithId:
    def test_put_returns_200(self) -> None:
        headers, body = _multipart_body()
        assert make_app().put("/_document/abc-123", content=body, headers=headers).status_code == 200

    def test_put_uses_path_id(self) -> None:
        client = make_mock_client()
        headers, body = _multipart_body()
        make_app(client=client).put("/_document/my-explicit-id", content=body, headers=headers)
        _, kwargs = client.index.call_args
        assert kwargs.get("doc_id") == "my-explicit-id"

    def test_put_without_file_returns_422(self) -> None:
        assert make_app().put("/_document/some-id").status_code == 422


# ---------------------------------------------------------------------------
# DELETE /_document – delete by filename
# ---------------------------------------------------------------------------


class TestDocumentDeleteByFilename:
    def test_delete_by_filename_returns_200(self) -> None:
        assert make_app().delete("/_document?filename=report.pdf").status_code == 200

    def test_delete_by_filename_calls_client_delete(self) -> None:
        client = make_mock_client()
        make_app(client=client).delete("/_document?filename=report.pdf")
        client.delete.assert_called_once()

    def test_delete_without_filename_returns_422(self) -> None:
        assert make_app().delete("/_document").status_code == 422

    def test_delete_response_contains_ok(self) -> None:
        assert make_app().delete("/_document?filename=report.pdf").json().get("ok") is True


# ---------------------------------------------------------------------------
# DELETE /_document/{id} – delete by document id
# ---------------------------------------------------------------------------


class TestDocumentDeleteById:
    def test_delete_by_id_returns_200(self) -> None:
        assert make_app().delete("/_document/abc-123").status_code == 200

    def test_delete_by_id_calls_client_delete_with_id(self) -> None:
        client = make_mock_client()
        make_app(client=client).delete("/_document/my-doc-id")
        _, kwargs = client.delete.call_args
        assert kwargs.get("doc_id") == "my-doc-id"

    def test_delete_by_id_response_contains_ok(self) -> None:
        assert make_app().delete("/_document/abc-123").json().get("ok") is True


# ---------------------------------------------------------------------------
# POST /_crawler/pause
# ---------------------------------------------------------------------------


class TestCrawlerPause:
    def test_pause_returns_200(self) -> None:
        assert make_app().post("/_crawler/pause").status_code == 200

    def test_pause_sets_paused_flag(self) -> None:
        state = make_mock_crawler_state(paused=False)
        make_app(crawler_state=state).post("/_crawler/pause")
        assert state.paused is True

    def test_pause_when_already_paused_is_idempotent(self) -> None:
        state = make_mock_crawler_state(paused=True)
        assert make_app(crawler_state=state).post("/_crawler/pause").status_code == 200

    def test_pause_response_contains_ok(self) -> None:
        assert make_app().post("/_crawler/pause").json().get("ok") is True


# ---------------------------------------------------------------------------
# POST /_crawler/resume
# ---------------------------------------------------------------------------


class TestCrawlerResume:
    def test_resume_returns_200(self) -> None:
        state = make_mock_crawler_state(paused=True)
        assert make_app(crawler_state=state).post("/_crawler/resume").status_code == 200

    def test_resume_clears_paused_flag(self) -> None:
        state = make_mock_crawler_state(paused=True)
        make_app(crawler_state=state).post("/_crawler/resume")
        assert state.paused is False

    def test_resume_when_not_paused_is_idempotent(self) -> None:
        state = make_mock_crawler_state(paused=False)
        assert make_app(crawler_state=state).post("/_crawler/resume").status_code == 200

    def test_resume_response_contains_ok(self) -> None:
        state = make_mock_crawler_state(paused=True)
        assert make_app(crawler_state=state).post("/_crawler/resume").json().get("ok") is True


# ---------------------------------------------------------------------------
# GET /_crawler/status
# ---------------------------------------------------------------------------


class TestCrawlerStatus:
    def test_status_returns_200(self) -> None:
        assert make_app().get("/_crawler/status").status_code == 200

    def test_status_reports_running_when_not_paused(self) -> None:
        state = make_mock_crawler_state(paused=False)
        assert make_app(crawler_state=state).get("/_crawler/status").json().get("status") == "running"

    def test_status_reports_paused_when_paused(self) -> None:
        state = make_mock_crawler_state(paused=True)
        assert make_app(crawler_state=state).get("/_crawler/status").json().get("status") == "paused"

    def test_status_includes_last_checkpoint(self) -> None:
        state = make_mock_crawler_state()
        state.last_checkpoint = "2024-01-15T10:30:00Z"
        body = make_app(crawler_state=state).get("/_crawler/status").json()
        assert body.get("last_checkpoint") == "2024-01-15T10:30:00Z"

    def test_status_last_checkpoint_none_when_never_run(self) -> None:
        state = make_mock_crawler_state()
        state.last_checkpoint = None
        body = make_app(crawler_state=state).get("/_crawler/status").json()
        assert body.get("last_checkpoint") is None


# ---------------------------------------------------------------------------
# DELETE /_crawler/checkpoint
# ---------------------------------------------------------------------------


class TestCrawlerCheckpoint:
    def test_clear_checkpoint_when_paused_returns_200(self) -> None:
        state = make_mock_crawler_state(paused=True)
        assert make_app(crawler_state=state).delete("/_crawler/checkpoint").status_code == 200

    def test_clear_checkpoint_calls_state_clear(self) -> None:
        state = make_mock_crawler_state(paused=True)
        make_app(crawler_state=state).delete("/_crawler/checkpoint")
        state.clear_checkpoint.assert_called_once()

    def test_clear_checkpoint_when_running_returns_409(self) -> None:
        state = make_mock_crawler_state(paused=False)
        assert make_app(crawler_state=state).delete("/_crawler/checkpoint").status_code == 409

    def test_clear_checkpoint_response_contains_ok(self) -> None:
        state = make_mock_crawler_state(paused=True)
        assert make_app(crawler_state=state).delete("/_crawler/checkpoint").json().get("ok") is True


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /_crawler/settings – expose current configuration as JSON
# ---------------------------------------------------------------------------


class TestSettingsEndpoint:
    def test_returns_200(self) -> None:
        assert make_app().get("/_crawler/settings").status_code == 200

    def test_content_type_is_json(self) -> None:
        resp = make_app().get("/_crawler/settings")
        assert "application/json" in resp.headers["content-type"]

    def test_response_contains_only_fs_section(self) -> None:
        resp = make_app().get("/_crawler/settings")
        assert set(resp.json().keys()) == {"fs"}

    def test_response_contains_fs_url(self) -> None:
        settings = make_settings(fs={"url": "/my/data"})
        resp = make_app(settings=settings).get("/_crawler/settings")
        assert resp.json()["fs"]["url"] == "/my/data"

    def test_elasticsearch_section_not_exposed(self) -> None:
        resp = make_app().get("/_crawler/settings")
        assert "elasticsearch" not in resp.json()

    def test_rest_section_not_exposed(self) -> None:
        resp = make_app().get("/_crawler/settings")
        assert "rest" not in resp.json()

    def test_credentials_not_exposed(self) -> None:
        settings = make_settings(
            elasticsearch={
                "nodes": [{"url": "http://localhost:9200"}],
                "username": "admin",
                "password": "s3cr3t",
                "api_key": "my-secret-key",
            }
        )
        raw = make_app(settings=settings).get("/_crawler/settings").text
        assert "s3cr3t" not in raw
        assert "my-secret-key" not in raw


class TestCors:
    def test_cors_disabled_no_header_on_get(self) -> None:
        settings = make_settings(rest={"url": "http://127.0.0.1:8080", "enable_cors": False})
        resp = make_app(settings=settings).get("/", headers={"Origin": "http://other.example.com"})
        assert "access-control-allow-origin" not in resp.headers

    def test_cors_enabled_sets_header_on_get(self) -> None:
        settings = make_settings(rest={"url": "http://127.0.0.1:8080", "enable_cors": True})
        resp = make_app(settings=settings).get("/", headers={"Origin": "http://other.example.com"})
        assert "access-control-allow-origin" in resp.headers


# ---------------------------------------------------------------------------
# CLI --rest flag
# ---------------------------------------------------------------------------


class TestCliRestFlag:
    def test_rest_flag_starts_uvicorn(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from click.testing import CliRunner

        from fscrawler.cli import main

        settings_file = tmp_path / "test-job" / "_settings.yaml"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            "name: test-job\nfs:\n  url: /data\n"
            "elasticsearch:\n  nodes:\n    - url: http://localhost:9200\n"
        )

        with (
            patch("fscrawler.cli.uvicorn") as mock_uvicorn,
            patch("fscrawler.client.FsCrawlerClient"),
            patch("fscrawler.cli.create_app", return_value=MagicMock()),
        ):
            result = CliRunner().invoke(
                main, ["--config_dir", str(tmp_path), "--rest", "test-job"]
            )
        assert mock_uvicorn.run.called, f"uvicorn.run not called; output: {result.output}"

    def test_rest_flag_passes_host_and_port_from_settings(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from click.testing import CliRunner

        from fscrawler.cli import main

        settings_file = tmp_path / "test-job" / "_settings.yaml"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            "name: test-job\nfs:\n  url: /data\n"
            "elasticsearch:\n  nodes:\n    - url: http://localhost:9200\n"
            "rest:\n  url: http://0.0.0.0:9090\n"
        )

        with (
            patch("fscrawler.cli.uvicorn") as mock_uvicorn,
            patch("fscrawler.client.FsCrawlerClient"),
            patch("fscrawler.cli.create_app", return_value=MagicMock()),
        ):
            CliRunner().invoke(main, ["--config_dir", str(tmp_path), "--rest", "test-job"])

        _, kwargs = mock_uvicorn.run.call_args
        assert kwargs.get("host") == "0.0.0.0"
        assert kwargs.get("port") == 9090

    def test_uvicorn_log_config_is_none(self, tmp_path: Path) -> None:
        """uvicorn must not install its own log handlers (log_config=None).

        Without this, uvicorn.config.LOGGING_CONFIG wipes our OTel JSON
        formatter from the uvicorn.* loggers, producing mixed plain-text and
        JSON lines in the same log stream.
        """
        from unittest.mock import patch

        from click.testing import CliRunner

        from fscrawler.cli import main

        settings_file = tmp_path / "test-job" / "_settings.yaml"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            "name: test-job\nfs:\n  url: /data\n"
            "elasticsearch:\n  nodes:\n    - url: http://localhost:9200\n"
        )

        with (
            patch("fscrawler.cli.uvicorn") as mock_uvicorn,
            patch("fscrawler.client.FsCrawlerClient"),
            patch("fscrawler.cli.create_app", return_value=MagicMock()),
        ):
            CliRunner().invoke(main, ["--config_dir", str(tmp_path), "--rest", "test-job"])

        _, kwargs = mock_uvicorn.run.call_args
        assert "log_config" in kwargs, "log_config must be explicitly passed to uvicorn.run()"
        assert kwargs["log_config"] is None

    def test_rest_mode_ensures_indices_exist(self, tmp_path: Path) -> None:
        """--rest must call ensure_index for the docs and folder indices."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from fscrawler.cli import main

        settings_file = tmp_path / "test-job" / "_settings.yaml"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            "name: test-job\nfs:\n  url: /data\n"
            "elasticsearch:\n  nodes:\n    - url: http://localhost:9200\n"
        )

        with (
            patch("fscrawler.cli.uvicorn"),
            patch("fscrawler.client.FsCrawlerClient") as mock_cls,
            patch("fscrawler.cli.create_app", return_value=MagicMock()),
            patch("fscrawler.cli.threading"),
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            CliRunner().invoke(main, ["--config_dir", str(tmp_path), "--rest", "test-job"])

        called_indices = [c.args[0] for c in mock_client.ensure_index.call_args_list]
        assert "test-job_docs" in called_indices
        assert "test-job_folder" in called_indices

    def test_rest_mode_starts_background_crawler_thread(self, tmp_path: Path) -> None:
        """--rest must start a daemon thread that runs the background crawler."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from fscrawler.cli import main

        settings_file = tmp_path / "test-job" / "_settings.yaml"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            "name: test-job\nfs:\n  url: /data\n"
            "elasticsearch:\n  nodes:\n    - url: http://localhost:9200\n"
        )

        with (
            patch("fscrawler.cli.uvicorn"),
            patch("fscrawler.client.FsCrawlerClient"),
            patch("fscrawler.cli.create_app", return_value=MagicMock()),
            patch("fscrawler.cli.threading") as mock_threading,
        ):
            CliRunner().invoke(main, ["--config_dir", str(tmp_path), "--rest", "test-job"])

        mock_threading.Thread.assert_called_once()
        _, kwargs = mock_threading.Thread.call_args
        assert kwargs.get("daemon") is True
        mock_threading.Thread.return_value.start.assert_called_once()


# ---------------------------------------------------------------------------
# Background crawler loop behaviour
# ---------------------------------------------------------------------------


class TestCrawlerLoop:
    """Test _crawler_loop: initial scan + watchdog observer lifecycle."""

    def _make_settings(self) -> Any:
        from fscrawler.settings import FsSettings
        return FsSettings.from_dict({"name": "test", "fs": {"url": "/data"}})

    def _run_loop(self, crawler_state, mock_crawl=None, crawl_error=None):
        """Run _crawler_loop with a mocked Observer that stops after is_alive returns False."""
        from unittest.mock import MagicMock, patch

        from fscrawler.cli import _crawler_loop

        settings = self._make_settings()
        client = MagicMock()

        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = False  # exits loop immediately

        crawl_side = crawl_error if crawl_error else None

        with (
            patch("fscrawler.cli._crawl_once", side_effect=crawl_side) as mock_crawl_fn,
            patch("fscrawler.cli.Observer", return_value=mock_observer),
            patch("fscrawler.cli.FsEventHandler"),
            patch("fscrawler.cli.time"),
        ):
            _crawler_loop(settings, client, Path("/tmp"), crawler_state)

        return mock_crawl_fn, mock_observer

    def test_initial_scan_always_runs(self) -> None:
        from fscrawler.rest_server import CrawlerState
        state = CrawlerState()
        mock_crawl, _ = self._run_loop(state)
        mock_crawl.assert_called_once()

    def test_observer_is_started(self) -> None:
        from fscrawler.rest_server import CrawlerState
        _, observer = self._run_loop(CrawlerState())
        observer.start.assert_called_once()

    def test_observer_is_stopped_on_exit(self) -> None:
        from fscrawler.rest_server import CrawlerState
        _, observer = self._run_loop(CrawlerState())
        observer.stop.assert_called_once()

    def test_initial_scan_error_does_not_prevent_observer_start(self) -> None:
        from fscrawler.rest_server import CrawlerState
        _, observer = self._run_loop(CrawlerState(), crawl_error=RuntimeError("disk full"))
        observer.start.assert_called_once()
