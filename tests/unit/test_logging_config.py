# Licensed under the Apache License, Version 2.0
"""Unit tests for fscrawler.logging_config."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import TracebackType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fscrawler.logging_config import (
    OtelJsonFormatter,
    _OtlpHttpHandler,
    _otel_severity,
    configure_logging,
    install_exception_hook,
)


# ---------------------------------------------------------------------------
# _otel_severity
# ---------------------------------------------------------------------------


class TestOtelSeverity:
    def test_debug(self) -> None:
        assert _otel_severity(logging.DEBUG) == (5, "DEBUG")

    def test_info(self) -> None:
        assert _otel_severity(logging.INFO) == (9, "INFO")

    def test_warning(self) -> None:
        assert _otel_severity(logging.WARNING) == (13, "WARN")

    def test_error(self) -> None:
        assert _otel_severity(logging.ERROR) == (17, "ERROR")

    def test_critical(self) -> None:
        assert _otel_severity(logging.CRITICAL) == (21, "FATAL")

    def test_between_levels_rounds_down(self) -> None:
        # A level between INFO(20) and WARNING(30) should map to INFO
        assert _otel_severity(25) == (9, "INFO")

    def test_notset(self) -> None:
        assert _otel_severity(0) == (0, "UNSPECIFIED")


# ---------------------------------------------------------------------------
# OtelJsonFormatter
# ---------------------------------------------------------------------------


def _make_record(
    msg: str = "hello",
    level: int = logging.INFO,
    name: str = "test.logger",
    exc_info: Any = None,
) -> logging.LogRecord:
    record = logging.LogRecord(name, level, "", 0, msg, (), exc_info)
    return record


class TestOtelJsonFormatter:
    fmt = OtelJsonFormatter()

    def _parse(self, record: logging.LogRecord) -> dict[str, Any]:
        return json.loads(self.fmt.format(record))  # type: ignore[no-any-return]

    def test_required_top_level_fields(self) -> None:
        out = self._parse(_make_record())
        assert set(out.keys()) == {"time", "severity", "severityNumber", "body", "resource", "attributes"}

    def test_time_format(self) -> None:
        out = self._parse(_make_record())
        # ISO-8601 UTC with Z suffix, millisecond precision
        assert out["time"].endswith("Z")
        assert "T" in out["time"]
        assert len(out["time"]) == len("2026-01-01T00:00:00.000Z")

    def test_body_is_message(self) -> None:
        out = self._parse(_make_record("my message"))
        assert out["body"] == "my message"

    def test_severity_info(self) -> None:
        out = self._parse(_make_record(level=logging.INFO))
        assert out["severity"] == "INFO"
        assert out["severityNumber"] == 9

    def test_severity_error(self) -> None:
        out = self._parse(_make_record(level=logging.ERROR))
        assert out["severity"] == "ERROR"
        assert out["severityNumber"] == 17

    def test_resource_fields(self) -> None:
        out = self._parse(_make_record())
        assert out["resource"]["service.name"] == "fscrawler"
        assert "service.version" in out["resource"]

    def test_logger_attribute(self) -> None:
        out = self._parse(_make_record(name="fscrawler.crawler"))
        assert out["attributes"]["logger"] == "fscrawler.crawler"

    def test_exception_attributes_present(self) -> None:
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            record = _make_record(level=logging.ERROR, exc_info=sys.exc_info())
        out = self._parse(record)
        attrs = out["attributes"]
        assert "exception.type" in attrs
        assert "exception.message" in attrs
        assert "exception.stacktrace" in attrs
        assert "RuntimeError" in attrs["exception.type"]
        assert attrs["exception.message"] == "boom"

    def test_exception_stacktrace_contains_raise(self) -> None:
        try:
            raise ValueError("oops")
        except ValueError:
            record = _make_record(level=logging.ERROR, exc_info=sys.exc_info())
        out = self._parse(record)
        assert "ValueError" in out["attributes"]["exception.stacktrace"]

    def test_no_exception_fields_when_no_exc(self) -> None:
        out = self._parse(_make_record())
        assert "exception.type" not in out["attributes"]
        assert "exception.message" not in out["attributes"]
        assert "exception.stacktrace" not in out["attributes"]

    def test_output_is_single_line_json(self) -> None:
        try:
            raise KeyError("k")
        except KeyError:
            record = _make_record(level=logging.ERROR, exc_info=sys.exc_info())
        result = self.fmt.format(record)
        assert "\n" not in result.rstrip("\n")


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def setup_method(self) -> None:
        # Reset root logger between tests
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.NOTSET)

    def test_stdout_json_default(self) -> None:
        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, OtelJsonFormatter)
        assert root.handlers[0].stream is sys.stdout  # type: ignore[attr-defined]

    def test_stderr_output(self) -> None:
        configure_logging(output="stderr")
        assert logging.getLogger().handlers[0].stream is sys.stderr  # type: ignore[attr-defined]

    def test_text_formatter(self) -> None:
        configure_logging(fmt="text")
        fmt = logging.getLogger().handlers[0].formatter
        assert not isinstance(fmt, OtelJsonFormatter)

    def test_debug_level(self) -> None:
        configure_logging(level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_file_output(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        configure_logging(output="file", file_path=log_file)
        root = logging.getLogger()
        assert isinstance(root.handlers[0], logging.FileHandler)
        root.handlers[0].close()

    def test_file_output_requires_path(self) -> None:
        with pytest.raises(ValueError, match="file_path"):
            configure_logging(output="file")

    def test_otel_output_requires_endpoint(self) -> None:
        with pytest.raises(ValueError, match="otel_endpoint"):
            configure_logging(output="otel")

    def test_otel_output_creates_handler(self) -> None:
        configure_logging(output="otel", otel_endpoint="http://collector:4318")
        root = logging.getLogger()
        assert isinstance(root.handlers[0], _OtlpHttpHandler)

    def test_unknown_output_falls_back_to_stdout(self) -> None:
        configure_logging(output="unknown_value")
        assert logging.getLogger().handlers[0].stream is sys.stdout  # type: ignore[attr-defined]

    def test_warnings_captured_through_logging(self) -> None:
        """configure_logging must activate Python-to-logging warning routing."""
        from unittest.mock import patch as _patch

        with _patch("logging.captureWarnings") as mock_cap:
            configure_logging()
        # Must be called with True so that Python warnings flow through the
        # logging system (e.g. urllib3 InsecureRequestWarning → OTel JSON).
        mock_cap.assert_called_with(True)


# ---------------------------------------------------------------------------
# _OtlpHttpHandler
# ---------------------------------------------------------------------------


class TestOtlpHttpHandler:
    def test_endpoint_normalization(self) -> None:
        h = _OtlpHttpHandler("http://collector:4318/")
        assert h._url == "http://collector:4318/v1/logs"

    def test_send_posts_json(self) -> None:
        h = _OtlpHttpHandler("http://collector:4318")
        record = _make_record("msg", logging.INFO)

        with patch("httpx.post") as mock_post:
            h.emit(record)

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["headers"] == {"Content-Type": "application/json"}
        body = json.loads(kwargs["content"])
        assert "resourceLogs" in body
        log_record = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        assert log_record["body"]["stringValue"] == "msg"

    def test_send_includes_exception_attributes(self) -> None:
        h = _OtlpHttpHandler("http://collector:4318")
        try:
            raise TypeError("bad type")
        except TypeError:
            record = _make_record("err", logging.ERROR, exc_info=sys.exc_info())

        with patch("httpx.post") as mock_post:
            h.emit(record)

        _, kwargs = mock_post.call_args
        body = json.loads(kwargs["content"])
        attrs = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
        keys = {a["key"] for a in attrs}
        assert "exception.type" in keys
        assert "exception.message" in keys

    def test_emit_handles_network_error_gracefully(self) -> None:
        h = _OtlpHttpHandler("http://dead:9999")
        record = _make_record("msg")
        # handleError writes to stderr — just ensure no exception propagates
        with patch("httpx.post", side_effect=OSError("refused")):
            h.emit(record)  # must not raise


# ---------------------------------------------------------------------------
# install_exception_hook
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fatal connection error log — structure validation against real-world fixture
# ---------------------------------------------------------------------------


class TestFatalConnectionErrorLogStructure:
    """Verify that the JSON log output for an unhandled ConnectionError matches
    the structure captured from a real failure (tests/data/fatal_connection_error.json).

    This fixture documents the log contract: any change to the log format that
    breaks these assertions indicates a regression in observability.
    """

    FIXTURE = Path(__file__).parent.parent / "data" / "fatal_connection_error.json"

    def _load_fixture(self) -> dict[str, Any]:
        with open(self.FIXTURE) as f:
            return json.load(f)  # type: ignore[no-any-return]

    def test_fixture_has_required_top_level_keys(self) -> None:
        doc = self._load_fixture()
        for key in ("time", "severity", "severityNumber", "body", "resource", "attributes"):
            assert key in doc, f"Missing top-level key: {key!r}"

    def test_fixture_severity_is_fatal(self) -> None:
        doc = self._load_fixture()
        assert doc["severity"] == "FATAL"
        assert doc["severityNumber"] == 21

    def test_fixture_resource_has_service_fields(self) -> None:
        doc = self._load_fixture()
        assert "service.name" in doc["resource"]
        assert "service.version" in doc["resource"]
        assert doc["resource"]["service.name"] == "fscrawler"

    def test_fixture_attributes_has_exception_fields(self) -> None:
        doc = self._load_fixture()
        attrs = doc["attributes"]
        assert "exception.type" in attrs
        assert "exception.stacktrace" in attrs
        assert "exception.message" in attrs
        assert "logger" in attrs

    def test_fixture_exception_type_is_connection_error(self) -> None:
        doc = self._load_fixture()
        assert "ConnectionError" in doc["attributes"]["exception.type"]

    def test_fixture_contains_no_sensitive_hostnames(self) -> None:
        """Ensure the fixture was properly sanitised before committing."""
        raw = self.FIXTURE.read_text()
        # The original hostname from the real error must not appear
        assert "opencontext" not in raw.lower()

    def test_otel_formatter_produces_matching_structure(self) -> None:
        """OtelJsonFormatter output must match the fixture's key structure."""
        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        formatter = OtelJsonFormatter()
        try:
            raise OSConnectionError(
                "N/A",
                "HTTPSConnection(host='opensearch-host', port=9200): "
                "Failed to establish a new connection: [Errno 111] Connection refused",
                None,
            )
        except OSConnectionError:
            import sys

            record = logging.LogRecord(
                name="fscrawler.cli",
                level=logging.FATAL,
                pathname="cli.py",
                lineno=126,
                msg="fscrawler terminated \u2014 unhandled error",
                args=(),
                exc_info=sys.exc_info(),
            )

        output = json.loads(formatter.format(record))
        fixture = self._load_fixture()

        # Structure must match — values may differ (timestamps, thread ids, etc.)
        assert set(output.keys()) == set(fixture.keys())
        assert set(output["resource"].keys()) == set(fixture["resource"].keys())
        assert set(output["attributes"].keys()) >= {
            "logger", "exception.type", "exception.stacktrace", "exception.message"
        }

    def test_retry_prevents_fatal_for_transient_connection_error(self) -> None:
        """After adding wait_for_cluster(), a transient ConnectionError must NOT
        produce a FATAL log — the retry loop should absorb it."""
        from unittest.mock import MagicMock, patch

        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        from fscrawler.client import FsCrawlerClient
        from fscrawler.settings import FsSettings

        settings = FsSettings.from_dict({"name": "test", "fs": {"url": "/data"}})

        with patch("fscrawler.client.OpenSearch") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            # Fail once then succeed — simulates ES starting up after fscrawler
            mock_instance.info.side_effect = [
                OSConnectionError("N/A", "refused", None),
                {"version": {"number": "2.14.0"}, "cluster_name": "test"},
            ]
            client = FsCrawlerClient(settings)
            with patch("fscrawler.client.time.sleep"):
                # Must not raise — retry absorbs the transient error
                client.wait_for_cluster()


class TestInstallExceptionHook:
    def test_installs_hook(self) -> None:
        original = sys.excepthook
        try:
            install_exception_hook()
            assert sys.excepthook is not original
        finally:
            sys.excepthook = original

    def test_keyboard_interrupt_uses_default_hook(self) -> None:
        install_exception_hook()
        with patch("sys.__excepthook__") as mock_default:
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
            mock_default.assert_called_once()

    def test_exception_logged_as_critical(self) -> None:
        configure_logging(fmt="json", output="stdout")
        install_exception_hook()

        with patch.object(logging.getLogger("fscrawler"), "critical") as mock_crit:
            exc = RuntimeError("crash")
            sys.excepthook(RuntimeError, exc, None)
            mock_crit.assert_called_once()
            assert "unhandled" in mock_crit.call_args[0][0].lower()
