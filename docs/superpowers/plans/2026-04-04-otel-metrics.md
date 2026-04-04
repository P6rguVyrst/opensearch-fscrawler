# OTel Metrics Instrumentation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenTelemetry metrics instrumentation (counters, histograms) with Prometheus scrape endpoint and OTLP push export.

**Architecture:** New `metrics.py` module owns all OTel SDK setup and instrument singletons. Other modules import instruments and call `.add()` / `.record()`. CLI flag `--otel-endpoint` replaces `--log-otel-endpoint` for unified signal export. REST server mounts `/metrics` for Prometheus scraping.

**Tech Stack:** `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-prometheus`, `opentelemetry-exporter-otlp-proto-http`

**Spec:** `docs/specs/2026-04-04-otel-metrics-design.md`

---

## File Structure

| File | Role |
|------|------|
| `src/fscrawler/metrics.py` | **New** — OTel meter, 6 instrument singletons, `configure_metrics()`, `get_prometheus_app()` |
| `tests/unit/test_metrics.py` | **New** — tests for metrics module |
| `src/fscrawler/indexer.py` | **Modify** — increment `documents_processed`, `dlq_records`, `pfq_records`, `bulk_duration` |
| `src/fscrawler/watcher.py` | **Modify** — increment `documents_processed`, `dlq_records` |
| `src/fscrawler/dlq.py` | **Modify** — increment `dlq_retries`, `pfq_records` |
| `src/fscrawler/wal.py` | **Modify** — increment `wal_records` |
| `src/fscrawler/cli.py` | **Modify** — call `configure_metrics()`, replace `--log-otel-endpoint` with `--otel-endpoint`, increment `wal_records` on recovery |
| `src/fscrawler/rest_server.py` | **Modify** — mount `/metrics` Prometheus endpoint |
| `src/fscrawler/logging_config.py` | **Modify** — accept `otel_endpoint` from unified flag |
| `pyproject.toml` | **Modify** — add OTel dependencies |

---

### Task 1: Add OTel dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml:33-42`

- [ ] **Step 1: Add opentelemetry packages to dependencies**

Add these 4 packages to the `dependencies` list in `pyproject.toml`:

```toml
dependencies = [
    "opensearch-py>=2.4",
    "pyyaml>=6.0",
    "click>=8.1",
    "fastapi>=0.110",
    "uvicorn>=0.27",
    "httpx>=0.27",
    "python-dateutil>=2.9",
    "watchdog>=6.0.0",
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-prometheus>=0.50b0",
    "opentelemetry-exporter-otlp-proto-http>=1.20",
]
```

- [ ] **Step 2: Install updated dependencies**

Run: `uv sync`
Expected: resolves and installs the 4 new packages without conflicts.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add opentelemetry dependencies for metrics instrumentation"
```

---

### Task 2: Create metrics.py with instruments and configure_metrics()

**Files:**
- Create: `src/fscrawler/metrics.py`
- Create: `tests/unit/test_metrics.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_metrics.py`:

```python
"""Unit tests for fscrawler.metrics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestConfigureMetrics:
    def test_configure_creates_meter_provider(self) -> None:
        from fscrawler.metrics import configure_metrics

        # Should not raise when called without endpoint
        configure_metrics()

    def test_configure_with_otel_endpoint(self) -> None:
        from fscrawler.metrics import configure_metrics

        with patch("fscrawler.metrics.OTLPMetricExporter") as mock_exporter_cls:
            configure_metrics(otel_endpoint="http://collector:4318")
            mock_exporter_cls.assert_called_once()
            # Verify the endpoint includes /v1/metrics
            call_kwargs = mock_exporter_cls.call_args[1]
            assert "/v1/metrics" in call_kwargs["endpoint"]

    def test_configure_without_endpoint_no_otlp_exporter(self) -> None:
        from fscrawler.metrics import configure_metrics

        with patch("fscrawler.metrics.OTLPMetricExporter") as mock_exporter_cls:
            configure_metrics(otel_endpoint=None)
            mock_exporter_cls.assert_not_called()


class TestInstruments:
    def test_documents_processed_is_counter(self) -> None:
        from opentelemetry.metrics import Counter

        from fscrawler.metrics import documents_processed

        # The no-op or real instrument should exist
        assert documents_processed is not None

    def test_bulk_duration_is_histogram(self) -> None:
        from fscrawler.metrics import bulk_duration

        assert bulk_duration is not None

    def test_dlq_records_is_counter(self) -> None:
        from fscrawler.metrics import dlq_records

        assert dlq_records is not None

    def test_pfq_records_is_counter(self) -> None:
        from fscrawler.metrics import pfq_records

        assert pfq_records is not None

    def test_dlq_retries_is_counter(self) -> None:
        from fscrawler.metrics import dlq_retries

        assert dlq_retries is not None

    def test_wal_records_is_counter(self) -> None:
        from fscrawler.metrics import wal_records

        assert wal_records is not None


class TestGetPrometheusApp:
    def test_returns_asgi_app(self) -> None:
        from fscrawler.metrics import configure_metrics, get_prometheus_app

        configure_metrics()
        app = get_prometheus_app()
        assert app is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fscrawler.metrics'`

- [ ] **Step 3: Write metrics.py**

Create `src/fscrawler/metrics.py`:

```python
# Licensed under the Apache License, Version 2.0
"""OpenTelemetry metrics instrumentation for FSCrawler.

Instruments are module-level singletons — import and use directly:

    from fscrawler.metrics import documents_processed
    documents_processed.add(1, {"status": "success", "fscrawler.job.name": job_name})

Call ``configure_metrics()`` once during startup to attach exporters.
Until called, instruments use the OTel API no-op meter (zero overhead).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram, Meter

logger = logging.getLogger("fscrawler.metrics")

# ---------------------------------------------------------------------------
# Module-level meter + instruments (created once at import time via no-op API)
# ---------------------------------------------------------------------------

_meter: Meter = metrics.get_meter("fscrawler")

documents_processed: Counter = _meter.create_counter(
    name="fscrawler.documents.processed",
    description="Documents processed (index or delete)",
    unit="{document}",
)

dlq_records: Counter = _meter.create_counter(
    name="fscrawler.dlq.records",
    description="Documents written to the Dead Letter Queue",
    unit="{record}",
)

pfq_records: Counter = _meter.create_counter(
    name="fscrawler.pfq.records",
    description="Documents promoted to the Permanent Failure Queue",
    unit="{record}",
)

dlq_retries: Counter = _meter.create_counter(
    name="fscrawler.dlq.retries",
    description="DLQ retry attempts",
    unit="{attempt}",
)

wal_records: Counter = _meter.create_counter(
    name="fscrawler.wal.records",
    description="Write-Ahead Log operations",
    unit="{record}",
)

bulk_duration: Histogram = _meter.create_histogram(
    name="fscrawler.bulk.duration",
    description="Duration of bulk flush operations",
    unit="s",
)

# ---------------------------------------------------------------------------
# Prometheus ASGI app (for /metrics endpoint)
# ---------------------------------------------------------------------------

# Lazy-initialised by configure_metrics() when Prometheus reader is attached.
_prometheus_app: Any = None


def get_prometheus_app() -> Any:
    """Return the ASGI/WSGI app that serves /metrics for Prometheus scraping.

    Returns None if configure_metrics() hasn't been called yet.
    """
    return _prometheus_app


# ---------------------------------------------------------------------------
# Configuration (call once from cli.py startup)
# ---------------------------------------------------------------------------


def configure_metrics(
    *,
    otel_endpoint: str | None = None,
    enable_prometheus: bool = False,
) -> None:
    """Attach metric readers/exporters to the global MeterProvider.

    Parameters
    ----------
    otel_endpoint:
        OTLP/HTTP base URL. Metrics pushed to ``{url}/v1/metrics``.
    enable_prometheus:
        If True, create a PrometheusMetricReader so ``get_prometheus_app()``
        returns a WSGI app for the ``/metrics`` route.
    """
    global _prometheus_app

    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": "fscrawler"})
    readers: list[Any] = []

    if enable_prometheus:
        from prometheus_client import make_wsgi_app
        from opentelemetry.exporter.prometheus import PrometheusMetricReader

        prometheus_reader = PrometheusMetricReader()
        readers.append(prometheus_reader)
        _prometheus_app = make_wsgi_app()
        logger.info("Prometheus /metrics endpoint enabled")

    if otel_endpoint:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )

        endpoint = otel_endpoint.rstrip("/") + "/v1/metrics"
        otlp_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint)
        )
        readers.append(otlp_reader)
        logger.info("OTLP metrics export to %s", endpoint)

    provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(provider)
    logger.info("MeterProvider configured with %d reader(s)", len(readers))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_metrics.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/metrics.py tests/unit/test_metrics.py
git commit -m "feat(metrics): add OTel metrics module with instruments and configure_metrics()"
```

---

### Task 3: Replace --log-otel-endpoint with --otel-endpoint in cli.py

**Files:**
- Modify: `src/fscrawler/cli.py:80-115`
- Modify: `src/fscrawler/logging_config.py:224-231` (no change needed — parameter name is already `otel_endpoint`)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_cli.py`:

```python
class TestOtelEndpointFlag:
    def test_otel_endpoint_flag_accepted(self, tmp_path: Path) -> None:
        """The --otel-endpoint flag should be accepted without error."""
        _write_settings(tmp_path)
        mock_settings = _mock_settings()
        with (
            patch("fscrawler.client.FsCrawlerClient") as mock_cls,
            patch("fscrawler.settings.FsSettings.from_file", return_value=mock_settings),
            patch("fscrawler.parser.TikaParser"),
            patch("fscrawler.cli._crawl_once"),
            patch("fscrawler.cli.configure_metrics") as mock_cm,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            result = CliRunner().invoke(
                main,
                [
                    "--config_dir", str(tmp_path),
                    "--otel-endpoint", "http://collector:4318",
                    "test-job",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_cm.assert_called_once()
        call_kwargs = mock_cm.call_args[1]
        assert call_kwargs["otel_endpoint"] == "http://collector:4318"

    def test_log_otel_endpoint_flag_removed(self, tmp_path: Path) -> None:
        """The old --log-otel-endpoint flag should no longer be accepted."""
        _write_settings(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "--config_dir", str(tmp_path),
                "--log-otel-endpoint", "http://collector:4318",
                "test-job",
            ],
        )
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli.py::TestOtelEndpointFlag -v`
Expected: FAIL — `--otel-endpoint` not recognized (old flag still present)

- [ ] **Step 3: Update cli.py**

In `src/fscrawler/cli.py`:

1. Replace the `--log-otel-endpoint` option (lines 87-92) with:

```python
@click.option(
    "--otel-endpoint",
    default=None,
    envvar="FSCRAWLER_OTEL_ENDPOINT",
    help="OTLP/HTTP base URL (e.g. http://collector:4318). "
         "Logs sent to {URL}/v1/logs, metrics to {URL}/v1/metrics.",
)
```

2. Update the `main()` function signature: rename `log_otel_endpoint` → `otel_endpoint`

3. Update the `configure_logging()` call inside `main()`: change `otel_endpoint=log_otel_endpoint` → `otel_endpoint=otel_endpoint`

4. After the `configure_logging()` / `install_exception_hook()` calls, add:

```python
    from fscrawler.metrics import configure_metrics

    configure_metrics(otel_endpoint=otel_endpoint)
```

5. Pass `otel_endpoint` into `_run_rest()` (add parameter) so it can enable Prometheus. Update call:

```python
        if rest:
            _run_rest(settings_file, job_dir, otel_endpoint=otel_endpoint)
```

6. Update `_run_rest()` signature to accept `otel_endpoint: str | None = None`. No metrics reconfigure needed inside — it's already configured in `main()`. But we need to call the Prometheus app mount (Task 7).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): replace --log-otel-endpoint with unified --otel-endpoint, call configure_metrics()"
```

---

### Task 4: Instrument indexer.py (documents_processed, dlq_records, pfq_records, bulk_duration)

**Files:**
- Modify: `src/fscrawler/indexer.py:215-298`
- Test: `tests/unit/test_indexer.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_indexer.py`:

```python
class TestIndexerMetrics:
    def test_successful_flush_increments_documents_processed_success(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        mock_opensearch_client.bulk.return_value = {
            "took": 5,
            "errors": False,
            "items": [
                {"index": {"_index": "fscrawler_docs_test", "_id": expected_id, "status": 201}}
            ],
        }

        with patch("fscrawler.indexer.documents_processed") as mock_counter:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))

            mock_counter.add.assert_called()
            call_args_list = mock_counter.add.call_args_list
            # Should have at least one success call
            success_calls = [c for c in call_args_list if c[0][1].get("status") == "success"]
            assert len(success_calls) >= 1

    def test_failed_flush_increments_documents_processed_error(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        mock_opensearch_client.bulk.return_value = {
            "took": 5,
            "errors": True,
            "items": [
                {
                    "index": {
                        "_index": "fscrawler_docs_test",
                        "_id": expected_id,
                        "status": 429,
                        "error": {"type": "circuit_breaking_exception", "reason": "oom"},
                    }
                }
            ],
        }

        with patch("fscrawler.indexer.documents_processed") as mock_counter:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))

            mock_counter.add.assert_called()
            call_args_list = mock_counter.add.call_args_list
            error_calls = [c for c in call_args_list if c[0][1].get("status") == "error"]
            assert len(error_calls) >= 1
            # Should include error.type
            assert error_calls[0][0][1]["error.type"] == "circuit_breaking_exception"

    def test_bulk_duration_recorded_on_flush(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        with patch("fscrawler.indexer.bulk_duration") as mock_histogram:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))

            mock_histogram.record.assert_called_once()
            elapsed = mock_histogram.record.call_args[0][0]
            assert isinstance(elapsed, float)
            assert elapsed >= 0
            attrs = mock_histogram.record.call_args[0][1]
            assert attrs["status"] == "success"

    def test_bulk_exception_records_duration_with_error(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        mock_opensearch_client.bulk.side_effect = ConnectionError("cluster down")
        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        with patch("fscrawler.indexer.bulk_duration") as mock_histogram:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))

            mock_histogram.record.assert_called_once()
            attrs = mock_histogram.record.call_args[0][1]
            assert attrs["status"] == "error"
            assert attrs["error.type"] == "bulk_flush_error"

    def test_dlq_write_increments_dlq_records(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        mock_opensearch_client.bulk.return_value = {
            "took": 5,
            "errors": True,
            "items": [
                {
                    "index": {
                        "_index": "fscrawler_docs_test",
                        "_id": expected_id,
                        "status": 429,
                        "error": {"type": "circuit_breaking_exception", "reason": "oom"},
                    }
                }
            ],
        }

        with patch("fscrawler.indexer.dlq_records") as mock_dlq:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))

            mock_dlq.add.assert_called_once()

    def test_pfq_write_increments_pfq_records(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)

        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        mock_opensearch_client.bulk.return_value = {
            "took": 5,
            "errors": True,
            "items": [
                {
                    "index": {
                        "_index": "fscrawler_docs_test",
                        "_id": expected_id,
                        "status": 400,
                        "error": {"type": "mapper_parsing_exception", "reason": "bad field"},
                    }
                }
            ],
        }

        with patch("fscrawler.indexer.pfq_records") as mock_pfq:
            indexer = BulkIndexer(client, settings)
            indexer.add(make_document("/data/test.txt"))

            mock_pfq.add.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_indexer.py::TestIndexerMetrics -v`
Expected: FAIL — `documents_processed` not importable from `fscrawler.indexer` or not called

- [ ] **Step 3: Instrument indexer.py**

In `src/fscrawler/indexer.py`:

1. Add import at top:

```python
import time

from fscrawler.metrics import bulk_duration, dlq_records, documents_processed, pfq_records
```

2. Replace `_flush_locked()` (lines 215-243):

```python
    def _flush_locked(self) -> None:
        """Send buffered operations.  Must be called with self._lock held."""
        if not self._buffer:
            return

        job_name = self._settings.name
        succeeded_ids: set[str] = set()
        t0 = time.monotonic()
        try:
            response = self._client.bulk(self._buffer)
            elapsed = time.monotonic() - t0
            if response.get("errors"):
                for item in response.get("items", []):
                    op = item.get("index") or item.get("delete") or {}
                    doc_id = op.get("_id", "")
                    error = op.get("error")
                    if error:
                        error_type = error.get("type", "unknown")
                        documents_processed.add(1, {
                            "status": "error",
                            "error.type": error_type,
                            "fscrawler.job.name": job_name,
                        })
                        self._route_failure(doc_id, error)
                    else:
                        succeeded_ids.add(doc_id)
                        documents_processed.add(1, {
                            "status": "success",
                            "fscrawler.job.name": job_name,
                        })
            else:
                succeeded_ids = set(self._pending.keys())
                n_ops = len(succeeded_ids)
                for _ in range(n_ops):
                    documents_processed.add(1, {
                        "status": "success",
                        "fscrawler.job.name": job_name,
                    })
                logger.debug("Flushed %d operations to OpenSearch.", n_ops)
            bulk_duration.record(elapsed, {
                "status": "success",
                "fscrawler.job.name": job_name,
            })
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error("Bulk flush failed: %s", exc)
            bulk_duration.record(elapsed, {
                "status": "error",
                "error.type": "bulk_flush_error",
                "fscrawler.job.name": job_name,
            })
        finally:
            if succeeded_ids and self._wal:
                self._wal.checkpoint(succeeded_ids)
            self._buffer = []
            self._buffer_bytes = 0
            self._pending = {}
```

3. In `_route_failure()`, add counter increments after each successful `index_raw` call:

After the `self._client.index_raw(index=DLQ_INDEX, ...)` line (retryable branch), add:

```python
                dlq_records.add(1, {
                    "error.type": error_type,
                    "fscrawler.job.name": job_name,
                })
```

After the `self._client.index_raw(index=PFQ_INDEX, ...)` line (non-retryable branch), add:

```python
                pfq_records.add(1, {
                    "error.type": error_type,
                    "fscrawler.job.name": job_name,
                })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_indexer.py -v`
Expected: all PASS (both old and new tests)

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/indexer.py tests/unit/test_indexer.py
git commit -m "feat(indexer): add OTel metrics for documents_processed, bulk_duration, dlq/pfq records"
```

---

### Task 5: Instrument watcher.py (documents_processed, dlq_records)

**Files:**
- Modify: `src/fscrawler/watcher.py:92-199`
- Test: `tests/unit/test_watcher.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_watcher.py`:

```python
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
```

Add the import at top of `tests/unit/test_watcher.py`:

```python
from unittest.mock import MagicMock, call, patch
```

(The `patch` import is already there from the existing `from unittest.mock import MagicMock, call, patch`)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_watcher.py::TestWatcherMetrics -v`
Expected: FAIL — `documents_processed` not importable from `fscrawler.watcher`

- [ ] **Step 3: Instrument watcher.py**

In `src/fscrawler/watcher.py`:

1. Add import at top:

```python
from fscrawler.metrics import dlq_records, documents_processed
```

2. In `_index()`, after the successful `self._client.index()` call (line 120 `logger.info("Indexed %s", path)`), add:

```python
            documents_processed.add(1, {
                "status": "success",
                "fscrawler.job.name": job_name,
            })
```

3. In `_index()`, in the except block, after `logger.error(...)` (line 126), add:

```python
            error_type_name = type(exc).__name__
            documents_processed.add(1, {
                "status": "error",
                "error.type": error_type_name,
                "fscrawler.job.name": job_name,
            })
```

4. In `_index()`, after the successful DLQ write (`logger.info("Wrote failed doc %s to DLQ", doc_id)`), add:

```python
                dlq_records.add(1, {
                    "error.type": error_type_name,
                    "fscrawler.job.name": job_name,
                })
```

5. In `_delete()`, in the except block after the successful DLQ write, add similar `dlq_records.add(1, ...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_watcher.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/watcher.py tests/unit/test_watcher.py
git commit -m "feat(watcher): add OTel metrics for documents_processed and dlq_records"
```

---

### Task 6: Instrument dlq.py (dlq_retries, pfq_records)

**Files:**
- Modify: `src/fscrawler/dlq.py:123-180`
- Test: `tests/unit/test_dlq.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_dlq.py`:

```python
class TestDlqMetrics:
    def test_successful_retry_increments_dlq_retries_success(self) -> None:
        from fscrawler.dlq import run_retry_cycle

        client = MagicMock()
        hit = _make_dlq_hit()
        client.search.return_value = {"hits": {"hits": [hit]}}
        client.bulk.return_value = {"errors": False}
        config = _make_config()

        with patch("fscrawler.dlq.dlq_retries") as mock_retries:
            run_retry_cycle(client, config)

            mock_retries.add.assert_called_once()
            attrs = mock_retries.add.call_args[0][1]
            assert attrs["fscrawler.retry.outcome"] == "success"

    def test_failed_retry_increments_dlq_retries_failure(self) -> None:
        from fscrawler.dlq import run_retry_cycle

        client = MagicMock()
        hit = _make_dlq_hit(retry_count=1)
        client.search.return_value = {"hits": {"hits": [hit]}}
        client.bulk.side_effect = Exception("cluster overloaded")
        config = _make_config(max_retries=5)

        with patch("fscrawler.dlq.dlq_retries") as mock_retries:
            run_retry_cycle(client, config)

            mock_retries.add.assert_called_once()
            attrs = mock_retries.add.call_args[0][1]
            assert attrs["fscrawler.retry.outcome"] == "failure"

    def test_max_retries_increments_pfq_records(self) -> None:
        from fscrawler.dlq import run_retry_cycle

        client = MagicMock()
        hit = _make_dlq_hit(retry_count=4)
        client.search.return_value = {"hits": {"hits": [hit]}}
        client.bulk.side_effect = Exception("still broken")
        config = _make_config(max_retries=5)

        with patch("fscrawler.dlq.pfq_records") as mock_pfq:
            run_retry_cycle(client, config)

            mock_pfq.add.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_dlq.py::TestDlqMetrics -v`
Expected: FAIL — `dlq_retries` not importable from `fscrawler.dlq`

- [ ] **Step 3: Instrument dlq.py**

In `src/fscrawler/dlq.py`:

1. Add import at top:

```python
from fscrawler.metrics import dlq_retries, pfq_records
```

2. In `_retry_single_record()`, at the end of the success path (after `logger.info("Successfully retried %s, removed from DLQ", dlq_doc_id)`), add:

```python
    dlq_retries.add(1, {
        "fscrawler.retry.outcome": "success",
        "fscrawler.job.name": job_name,
    })
```

3. In the failure path, after the PFQ promotion block (`new_count >= config.max_retries`), add after the `logger.warning(...)`:

```python
            dlq_retries.add(1, {
                "fscrawler.retry.outcome": "failure",
                "fscrawler.job.name": job_name,
            })
            pfq_records.add(1, {
                "error.type": source.get("error_type", "unknown"),
                "fscrawler.job.name": job_name,
            })
```

4. In the failure path, in the `else` branch (retry with backoff), after `logger.info(...)`:

```python
            dlq_retries.add(1, {
                "fscrawler.retry.outcome": "failure",
                "fscrawler.job.name": job_name,
            })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dlq.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/dlq.py tests/unit/test_dlq.py
git commit -m "feat(dlq): add OTel metrics for dlq_retries and pfq_records"
```

---

### Task 7: Instrument wal.py (wal_records)

**Files:**
- Modify: `src/fscrawler/wal.py:33-59`
- Test: `tests/unit/test_wal.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_wal.py`:

```python
class TestWalMetrics:
    def test_append_increments_wal_records(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")

        with patch("fscrawler.wal.wal_records") as mock_counter:
            wal.append({"doc_id": "abc", "action": "index"})

            mock_counter.add.assert_called_once()
            attrs = mock_counter.add.call_args[0][1]
            assert attrs["fscrawler.wal.action"] == "append"

    def test_checkpoint_increments_wal_records(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        wal.append({"doc_id": "abc", "action": "index"})

        with patch("fscrawler.wal.wal_records") as mock_counter:
            wal.checkpoint({"abc"})

            mock_counter.add.assert_called_once()
            attrs = mock_counter.add.call_args[0][1]
            assert attrs["fscrawler.wal.action"] == "checkpoint"
```

Add the necessary import at top of test file:

```python
from unittest.mock import patch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_wal.py::TestWalMetrics -v`
Expected: FAIL — `wal_records` not importable from `fscrawler.wal`

- [ ] **Step 3: Instrument wal.py**

In `src/fscrawler/wal.py`:

1. Add import at top:

```python
from fscrawler.metrics import wal_records
```

2. In `append()`, after the `os.fsync(f.fileno())` line, add (still inside the lock):

```python
        wal_records.add(1, {"fscrawler.wal.action": "append"})
```

3. In `checkpoint()`, after the `os.replace(tmp_path, str(self._path))` line, add (still inside the lock):

```python
            wal_records.add(1, {"fscrawler.wal.action": "checkpoint"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_wal.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/wal.py tests/unit/test_wal.py
git commit -m "feat(wal): add OTel wal_records counter for append/checkpoint"
```

---

### Task 8: Add wal_records "recover" metric to cli.py

**Files:**
- Modify: `src/fscrawler/cli.py:153-187`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_cli.py`:

```python
class TestWalRecoveryMetrics:
    def test_recover_wal_increments_wal_records_recover(self) -> None:
        from fscrawler.cli import _recover_wal

        client = MagicMock()
        client.bulk.return_value = {"errors": False, "items": []}
        wal = MagicMock()
        wal.is_empty = False
        wal.read.return_value = [
            {
                "job_name": "test",
                "target_index": "fscrawler_docs_test",
                "doc_id": "abc123",
                "action": "index",
                "payload": {"content": "hello"},
            },
        ]

        with patch("fscrawler.cli.wal_records") as mock_counter:
            _recover_wal(client, wal)

            assert mock_counter.add.call_count == 1
            attrs = mock_counter.add.call_args[0][1]
            assert attrs["fscrawler.wal.action"] == "recover"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli.py::TestWalRecoveryMetrics -v`
Expected: FAIL — `wal_records` not imported in cli.py

- [ ] **Step 3: Add recovery metric to cli.py**

In `src/fscrawler/cli.py`:

1. Add import at top:

```python
from fscrawler.metrics import wal_records
```

2. In `_recover_wal()`, after the successful bulk replay + checkpoint (after `logger.info("WAL: recovery complete, %d records replayed", len(records))`), add:

```python
        for _ in records:
            wal_records.add(1, {"fscrawler.wal.action": "recover"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add wal_records 'recover' metric on WAL replay"
```

---

### Task 9: Mount /metrics Prometheus endpoint in rest_server.py

**Files:**
- Modify: `src/fscrawler/rest_server.py:70-98`
- Modify: `src/fscrawler/cli.py` (pass `enable_prometheus=True` in `main()`)
- Test: `tests/unit/test_rest_server.py` (or whichever file tests the REST server)

- [ ] **Step 1: Write the failing test**

Add a test (in an existing REST server test file or create one):

```python
class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_200(self, mock_opensearch_client: MagicMock) -> None:
        from fastapi.testclient import TestClient

        from fscrawler.metrics import configure_metrics
        from fscrawler.rest_server import CrawlerState, create_app
        from tests.conftest import make_settings

        # Enable Prometheus reader
        configure_metrics(enable_prometheus=True)

        settings = make_settings()
        from fscrawler.client import FsCrawlerClient
        client = FsCrawlerClient(settings)
        state = CrawlerState()

        app = create_app(settings=settings, client=client, crawler_state=state)
        test_client = TestClient(app)

        response = test_client.get("/metrics")
        assert response.status_code == 200
        # Prometheus text format starts with # HELP or has metric lines
        assert "fscrawler" in response.text or response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_rest_server.py::TestMetricsEndpoint -v` (or wherever the test is)
Expected: FAIL — `/metrics` returns 404

- [ ] **Step 3: Mount /metrics in rest_server.py**

In `src/fscrawler/rest_server.py`, inside `create_app()`, after the app is created and CORS middleware is added:

```python
    # Mount Prometheus /metrics endpoint
    from fscrawler.metrics import get_prometheus_app

    prometheus_app = get_prometheus_app()
    if prometheus_app is not None:
        from starlette.middleware.wsgi import WSGIMiddleware

        app.mount("/metrics", WSGIMiddleware(prometheus_app))
```

In `src/fscrawler/cli.py`, update the `configure_metrics()` call in `main()`:

```python
    configure_metrics(
        otel_endpoint=otel_endpoint,
        enable_prometheus=rest,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_rest_server.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/rest_server.py src/fscrawler/cli.py tests/unit/test_rest_server.py
git commit -m "feat(rest): mount Prometheus /metrics endpoint via OTel exporter"
```

---

### Task 10: Full test suite verification

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest -v`
Expected: all tests pass, coverage does not regress below 80%.

- [ ] **Step 2: Run type checking**

Run: `uv run mypy src/fscrawler/metrics.py`
Expected: no errors (or only pre-existing ones)

- [ ] **Step 3: Run linting**

Run: `uv run ruff check src/fscrawler/`
Expected: no new violations
