# Licensed under the Apache License, Version 2.0
"""Structured logging following the OpenTelemetry log data model.

Formats
-------
* ``json``  — newline-delimited JSON, OTel field names (default)
* ``text``  — human-readable ``asctime [level] logger — message``

Outputs
-------
* ``stdout``  — standard output (default)
* ``stderr``  — standard error
* ``file``    — rotating log file (requires *file_path*)
* ``otel``    — OTLP/HTTP JSON to an OpenTelemetry collector (requires *otel_endpoint*)

OTel log record fields emitted
-------------------------------
``time``             ISO-8601 UTC with millisecond precision and Z suffix
``severity``         OTel severity text  (TRACE / DEBUG / INFO / WARN / ERROR / FATAL)
``severityNumber``   OTel severity number per the specification
``body``             Formatted log message
``resource``         ``service.name``, ``service.version``
``attributes``       ``logger``, ``thread.id``; on exceptions also
                     ``exception.type``, ``exception.message``, ``exception.stacktrace``

References
----------
https://opentelemetry.io/docs/specs/otel/logs/data-model/
https://opentelemetry.io/docs/specs/semconv/general/logs/
https://opentelemetry.io/docs/specs/otel/protocol/exporter/
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from fscrawler import __version__

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_SERVICE_NAME = "fscrawler"

# Python logging level  →  (OTel severity number, OTel severity text)
# https://opentelemetry.io/docs/specs/otel/logs/data-model/#field-severitynumber
_LEVEL_TO_OTEL: dict[int, tuple[int, str]] = {
    logging.NOTSET: (0, "UNSPECIFIED"),
    logging.DEBUG: (5, "DEBUG"),
    logging.INFO: (9, "INFO"),
    logging.WARNING: (13, "WARN"),
    logging.ERROR: (17, "ERROR"),
    logging.CRITICAL: (21, "FATAL"),
}
_ORDERED_THRESHOLDS = sorted(_LEVEL_TO_OTEL.keys(), reverse=True)


def _otel_severity(level: int) -> tuple[int, str]:
    for threshold in _ORDERED_THRESHOLDS:
        if level >= threshold:
            return _LEVEL_TO_OTEL[threshold]
    return _LEVEL_TO_OTEL[logging.NOTSET]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _exc_attrs(record: logging.LogRecord) -> dict[str, str]:
    """Extract OTel exception.* attributes from a log record, or return {}."""
    ei = record.exc_info
    if not ei or not isinstance(ei, tuple) or ei[0] is None:
        return {}
    exc_type: type[BaseException] = ei[0]
    exc_value: BaseException | None = ei[1]
    exc_tb: TracebackType | None = ei[2]
    attrs: dict[str, str] = {
        "exception.type": f"{exc_type.__module__}.{exc_type.__qualname__}",
        "exception.stacktrace": "".join(
            traceback.format_exception(exc_type, exc_value, exc_tb)
        ).rstrip(),
    }
    if exc_value is not None:
        attrs["exception.message"] = str(exc_value)
    return attrs


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class OtelJsonFormatter(logging.Formatter):
    """Formats each log record as a single JSON object on one line.

    The output schema follows the OpenTelemetry log data model so that records
    can be ingested by any OTel-aware log pipeline without further enrichment.
    """

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        sev_num, sev_text = _otel_severity(record.levelno)
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        time_str = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"

        attributes: dict[str, Any] = {
            "logger": record.name,
            "thread.id": record.thread,
        }
        attributes.update(_exc_attrs(record))

        entry: dict[str, Any] = {
            "time": time_str,
            "severity": sev_text,
            "severityNumber": sev_num,
            "body": record.getMessage(),
            "resource": {
                "service.name": _SERVICE_NAME,
                "service.version": __version__,
            },
            "attributes": attributes,
        }
        return json.dumps(entry, default=str)


_TEXT_FORMATTER = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


# ---------------------------------------------------------------------------
# OTLP/HTTP handler (no extra dependencies — uses stdlib urllib)
# ---------------------------------------------------------------------------


class _OtlpHttpHandler(logging.Handler):
    """Sends log records to an OTLP/HTTP endpoint using the JSON encoding.

    Each ``emit()`` call fires a synchronous POST to ``{endpoint}/v1/logs``.
    Failures are handled by :meth:`logging.Handler.handleError` (prints to
    stderr) so that a broken collector never silences application logs.

    Reference: https://opentelemetry.io/docs/specs/otlp/#otlphttp
    """

    def __init__(self, endpoint: str) -> None:
        super().__init__()
        self._url = endpoint.rstrip("/") + "/v1/logs"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._send(record)
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def _send(self, record: logging.LogRecord) -> None:
        sev_num, sev_text = _otel_severity(record.levelno)
        ts_ns = int(record.created * 1_000_000_000)

        otel_attrs = [{"key": "logger", "value": {"stringValue": record.name}}]
        for k, v in _exc_attrs(record).items():
            otel_attrs.append({"key": k, "value": {"stringValue": v}})

        payload: dict[str, Any] = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": _SERVICE_NAME},
                            },
                            {
                                "key": "service.version",
                                "value": {"stringValue": __version__},
                            },
                        ]
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": record.name},
                            "logRecords": [
                                {
                                    "timeUnixNano": str(ts_ns),
                                    "severityNumber": sev_num,
                                    "severityText": sev_text,
                                    "body": {"stringValue": record.getMessage()},
                                    "attributes": otel_attrs,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            resp.read()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def configure_logging(
    *,
    level: str = "INFO",
    fmt: str = "json",
    output: str = "stdout",
    file_path: Path | None = None,
    otel_endpoint: str | None = None,
) -> None:
    """Configure the root logger for the fscrawler process.

    Parameters
    ----------
    level:
        Log level name: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, or
        ``CRITICAL``.  Case-insensitive.
    fmt:
        ``"json"`` (default) — OTel-compliant JSON, one object per line.
        ``"text"`` — human-readable timestamped text.
    output:
        ``"stdout"`` (default), ``"stderr"``, ``"file"``, or ``"otel"``.
    file_path:
        Destination file when *output* is ``"file"``.
    otel_endpoint:
        OTLP/HTTP base URL (e.g. ``http://collector:4318``) when *output* is
        ``"otel"``.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    formatter: logging.Formatter = OtelJsonFormatter() if fmt.lower() == "json" else _TEXT_FORMATTER

    handler: logging.Handler
    match output.lower():
        case "stderr":
            handler = logging.StreamHandler(sys.stderr)
        case "file":
            if file_path is None:
                raise ValueError("file_path must be provided when output='file'")
            handler = logging.FileHandler(file_path, encoding="utf-8")
        case "otel":
            if otel_endpoint is None:
                raise ValueError("otel_endpoint must be provided when output='otel'")
            handler = _OtlpHttpHandler(otel_endpoint)
        case _:  # "stdout" or anything unrecognised
            handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    root.addHandler(handler)

    # Route Python warnings (e.g. urllib3 InsecureRequestWarning) through
    # the logging system so they are formatted by our OTel JSON formatter
    # instead of being written as raw text to stderr.
    # Force-rearm by toggling off first: if a previous call left
    # logging._warnings_showwarning set but warnings.showwarning was restored
    # by a framework (e.g. pytest), captureWarnings(True) would be a no-op.
    logging.captureWarnings(False)
    logging.captureWarnings(True)

    # Suppress verbose third-party loggers unless debug level is requested
    if numeric_level > logging.DEBUG:
        for noisy in ("opensearch", "urllib3", "httpx", "httpcore", "uvicorn.access"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def install_exception_hook() -> None:
    """Install ``sys.excepthook`` to log uncaught exceptions via the root logger.

    Without this, an unhandled exception only produces a raw Python traceback
    on stderr — bypassing any structured formatter.  With this hook the record
    is emitted through the normal logging pipeline (JSON/text, chosen output
    target) before the interpreter exits.
    """

    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            # Let Ctrl-C pass through without a scary traceback
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("fscrawler").critical(
            "fscrawler terminated — unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = _hook
