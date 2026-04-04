# OpenTelemetry Metrics Instrumentation — Design Spec

**Date**: 2026-04-04
**Status**: Draft
**Related**: [DLQ/WAL Design](./2026-04-04-dlq-wal-design.md) (metrics replace the "Metrics (Future)" section)

## Problem

FSCrawler has no metrics instrumentation. All failure observability comes from structured logs, which cannot drive Prometheus alerts, Grafana dashboards, or capacity planning queries. We need counters, histograms, and attributes that answer:

- What is the document processing throughput and error rate?
- How many documents are stuck in DLQ or promoted to PFQ?
- Are retries effective or just burning cycles?
- Is the OpenSearch bulk API becoming a bottleneck (latency)?
- Do we need to scale the OpenSearch backend or tune bulk batch sizes?

## Design Principles

- **OTel semantic conventions first** — metric names, attribute keys, and instrument types follow the OpenTelemetry specification. See References section for exact documents.
- **One metric for success + failure** — per [open-telemetry/semantic-conventions: recording-errors.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/general/recording-errors.md), prefer a single metric over separate success/failure metrics. We add an explicit `status` attribute (`success` / `error`) for clean PromQL filtering, and `error.type` is present only on failures. This lets users derive throughput, error rates, and SLIs from one time series.
- **Low-cardinality attributes** — `error.type` values are a fixed, documented set (< 10 values). See [open-telemetry/semantic-conventions: error.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/registry/attributes/error.md) (Stable).
- **Always collected, export is optional** — instruments are initialized on startup regardless of config. The OTel SDK aggregates in-place (counters are atomic integers, histograms are fixed-size bucket arrays) — memory is O(number of unique metric+attribute combinations), not O(number of events). No exporter configured = no export, no memory growth.
- **Business logic stays clean** — all OTel SDK setup lives in `metrics.py`. Other modules import instruments and call `.add()` / `.record()`.

## Metric Definitions

### Primary Counter

| Metric | Type | Unit | Attributes | Purpose |
|--------|------|------|------------|---------|
| `fscrawler.documents.processed` | Counter | `{document}` | `status` (Required: `success` / `error`), `error.type` (Conditionally Required: only when `status=error`), `fscrawler.job.name` | Throughput + error rate + SLI in one query |

Per [open-telemetry/semantic-conventions: recording-errors.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/general/recording-errors.md): "It's RECOMMENDED to report one metric that includes successes and failures as opposed to reporting two (or more) metrics depending on the operation status."

Every increment carries an explicit `status` attribute:
- `status="success"` — document processed successfully (no `error.type` attribute)
- `status="error"` — document processing failed (`error.type` attribute present)

This avoids relying on attribute presence/absence for PromQL filtering, which is fragile. Instead, queries explicitly select `{status="success"}` or `{status="error"}`.

### Queue Counters

| Metric | Type | Unit | Attributes | Purpose |
|--------|------|------|------------|---------|
| `fscrawler.dlq.records` | Counter | `{record}` | `error.type`, `fscrawler.job.name` | DLQ entries by error class |
| `fscrawler.pfq.records` | Counter | `{record}` | `error.type`, `fscrawler.job.name` | Permanent failures |
| `fscrawler.dlq.retries` | Counter | `{attempt}` | `fscrawler.retry.outcome` (`success` / `failure`), `fscrawler.job.name` | Retry effectiveness |

The dedicated queue counters complement the primary counter. The primary counter gives you the overall error rate; the queue counters let you build DLQ/PFQ-specific dashboards and alerts (e.g., "PFQ records growing = documents need human intervention") without complex PromQL joins.

### WAL Counter

| Metric | Type | Unit | Attributes | Purpose |
|--------|------|------|------------|---------|
| `fscrawler.wal.records` | Counter | `{record}` | `fscrawler.wal.action` (`append` / `checkpoint` / `recover`), `fscrawler.job.name` | WAL throughput and recovery activity |

### Bulk Duration Histogram

| Metric | Type | Unit | Attributes | Purpose |
|--------|------|------|------------|---------|
| `fscrawler.bulk.duration` | Histogram | `s` | `status` (`success` / `error`), `error.type` (Conditionally Required: only when `status=error`), `fscrawler.job.name` | Flush latency — scaling signal for OpenSearch backend |

Per [open-telemetry/semantic-conventions: database-metrics.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/db/database-metrics.md): "Duration of database client operations. Batch operations SHOULD be recorded as a single operation." and "This metric SHOULD be specified with ExplicitBucketBoundaries advisory parameter."

Bucket boundaries (from the database conventions):

```
[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]
```

Use cases:
- Rising p95 = OpenSearch is under load, consider scaling or reducing bulk_size
- Rising p99 with error.type = transient failures correlate with latency spikes
- Flat p50 + rising p99 = tail latency problem (GC pressure, slow nodes)

## Attribute Definitions

### `error.type` Values

Per [open-telemetry/semantic-conventions: error.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/registry/attributes/error.md) (Stable): "SHOULD be predictable and have LOW cardinality."

| Value | Retryable | Source | Description |
|-------|-----------|--------|-------------|
| `connection_error` | yes | transient | Network unreachable, connection refused |
| `timeout` | yes | transient | Request timed out |
| `circuit_breaking_exception` | yes | OpenSearch | Memory pressure on cluster |
| `cluster_block_exception` | yes | OpenSearch | Read-only index (e.g., disk full) |
| `mapper_parsing_exception` | no | OpenSearch | Bad field type in document |
| `illegal_argument_exception` | no | OpenSearch | Invalid document content |
| `version_conflict_engine_exception` | no | OpenSearch | Concurrent modification |
| `bulk_flush_error` | yes | client | Total bulk request failure (exception, not per-item) |
| `parse_error` | no | Tika/watcher | Document parsing failed before indexing |

### Custom Attributes

| Attribute | Type | Values | Description |
|-----------|------|--------|-------------|
| `status` | string | `success`, `error` | Operation outcome — always present on `documents.processed` |
| `fscrawler.job.name` | string | | Job name from settings (e.g., `"my_job"`) |
| `fscrawler.wal.action` | string | `append`, `checkpoint`, `recover` | WAL operation type |
| `fscrawler.retry.outcome` | string | `success`, `failure` | Retry result |

## Export Architecture

### With `--rest` flag

The FastAPI REST server exposes a `/metrics` endpoint serving Prometheus exposition format. This uses `opentelemetry-exporter-prometheus`, which registers a Prometheus collector with the OTel MeterProvider and renders metrics on HTTP GET.

Grafana Alloy or Prometheus scrapes this endpoint directly — standard `scrape_configs` target.

If `--otel-endpoint` is also configured, metrics are additionally pushed via OTLP/HTTP to `{otel-endpoint}/v1/metrics`.

### Without `--rest` flag

No scrape endpoint is available. Metrics export requires `--otel-endpoint` to be set. Metrics are pushed via `opentelemetry-exporter-otlp-proto-http` to the collector.

If neither `--rest` nor `--otel-endpoint` is configured, metrics are collected internally but not exported. This has negligible overhead (see Design Principles).

### CLI Changes

#### New flag

```
--otel-endpoint URL
    OTLP/HTTP base URL (e.g., http://collector:4318).
    Logs are sent to {URL}/v1/logs, metrics to {URL}/v1/metrics.
    Env var: FSCRAWLER_OTEL_ENDPOINT
```

#### Removed flag (unreleased)

`--log-otel-endpoint` and `FSCRAWLER_LOG_OTEL_ENDPOINT` are removed. They were added in v0.3.1-dev but never released (latest release is v0.3.0). No deprecation alias needed — clean replacement.

### Settings Wiring

No new settings dataclass needed. The endpoint is a CLI flag / env var, not a per-job YAML setting. This matches the existing pattern where `--log-format`, `--log-output`, etc. are CLI-only.

## Code Structure

### New File: `src/fscrawler/metrics.py`

Single module responsible for all OTel metrics setup:

```
metrics.py
├── _meter: Meter              (module-level singleton)
├── documents_processed        (Counter)
├── dlq_records                (Counter)
├── pfq_records                (Counter)
├── dlq_retries                (Counter)
├── wal_records                (Counter)
├── bulk_duration              (Histogram)
├── configure_metrics()        (attach exporters based on config)
└── get_prometheus_app()       (return ASGI app for /metrics route)
```

`configure_metrics(otel_endpoint=None)` is called from `cli.py` during startup. It:
1. Creates a `MeterProvider` with appropriate exporters
2. If `otel_endpoint` is set: adds `OTLPMetricExporter` targeting `{endpoint}/v1/metrics`
3. Sets the global MeterProvider

`get_prometheus_app()` returns an ASGI-compatible app (or route handler) that `rest_server.py` mounts at `/metrics`. This uses `PrometheusMetricReader` from `opentelemetry-exporter-prometheus`.

Other modules import instruments directly:

```python
from fscrawler.metrics import documents_processed, bulk_duration
```

### Integration Points

| Instrument | File | Location | When |
|------------|------|----------|------|
| `documents_processed` | `indexer.py` | `_flush_locked()` | +1 per successful item: `status=success` |
| `documents_processed` | `indexer.py` | `_route_failure()` | +1 per failed item: `status=error`, `error.type=...` |
| `documents_processed` | `watcher.py` | `_index()` | +1 on success: `status=success` |
| `documents_processed` | `watcher.py` | `_index()` except block | +1 on failure: `status=error`, `error.type=...` |
| `dlq_records` | `indexer.py` | `_route_failure()` | +1 when writing retryable error to DLQ |
| `dlq_records` | `watcher.py` | `_index()`, `_delete()` | +1 on DLQ write in except block |
| `pfq_records` | `indexer.py` | `_route_failure()` | +1 when routing non-retryable error to PFQ |
| `pfq_records` | `dlq.py` | `_retry_single_record()` | +1 on max-retries promotion to PFQ |
| `dlq_retries` | `dlq.py` | `_retry_single_record()` | +1 with `outcome=success` or `outcome=failure` |
| `wal_records` | `wal.py` | `append()` | +1 with `action=append` |
| `wal_records` | `wal.py` | `checkpoint()` | +1 with `action=checkpoint` |
| `wal_records` | `cli.py` | `_recover_wal()` | +1 per record with `action=recover` |
| `bulk_duration` | `indexer.py` | `_flush_locked()` | Record elapsed time of `client.bulk()` call |
| `bulk_duration` | `indexer.py` | `_flush_locked()` except | Record elapsed time with `error.type=bulk_flush_error` |

### Startup Sequence

```
cli.py main()
  ├── configure_logging(otel_endpoint=...)     # existing
  ├── configure_metrics(otel_endpoint=...)     # new
  ├── ...
  └── if rest:
        app = create_app(...)                  # existing
        # rest_server.py mounts get_prometheus_app() at /metrics
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `opentelemetry-api` | Meter, Counter, Histogram interfaces |
| `opentelemetry-sdk` | MeterProvider, metric readers |
| `opentelemetry-exporter-prometheus` | `/metrics` endpoint for Prometheus scraping |
| `opentelemetry-exporter-otlp-proto-http` | OTLP/HTTP push for metrics (and logs) |

These are standard OTel Python packages. See [open-telemetry/opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python) for the SDK.

## Files Touched

| File | Change |
|------|--------|
| `src/fscrawler/metrics.py` | **New** — meter, instruments, configure_metrics(), get_prometheus_app() |
| `src/fscrawler/indexer.py` | **Modified** — increment documents_processed, dlq_records, pfq_records, bulk_duration |
| `src/fscrawler/dlq.py` | **Modified** — increment dlq_retries, pfq_records |
| `src/fscrawler/wal.py` | **Modified** — increment wal_records |
| `src/fscrawler/watcher.py` | **Modified** — increment documents_processed, dlq_records |
| `src/fscrawler/cli.py` | **Modified** — call configure_metrics(), increment wal_records on recovery, replace --log-otel-endpoint with --otel-endpoint |
| `src/fscrawler/rest_server.py` | **Modified** — mount /metrics endpoint |
| `src/fscrawler/logging_config.py` | **Modified** — accept otel_endpoint from unified flag |
| `pyproject.toml` | **Modified** — add opentelemetry dependencies |

## Example PromQL Queries

Note: Prometheus converts OTel metric names by replacing `.` with `_` and appending `_total` for counters, `_seconds` for duration histograms.

### Throughput and Error Rate

```promql
# Document processing throughput (successful documents per second)
rate(fscrawler_documents_processed_total{status="success"}[5m])

# Error rate (failed documents per second, broken down by error type)
sum by (error_type) (rate(fscrawler_documents_processed_total{status="error"}[5m]))

# Total processing rate (all documents)
rate(fscrawler_documents_processed_total[5m])
```

### SLIs (Service Level Indicators)

The `status` attribute enables clean SLI calculation following the standard formula: **good events / total events**.

```promql
# Document processing success rate (SLI) — ratio of good events to all events
# Target: 99.9% of documents processed successfully over a rolling window
rate(fscrawler_documents_processed_total{status="success"}[5m])
/
rate(fscrawler_documents_processed_total[5m])

# Same SLI over a longer window for SLO burn-rate alerting (e.g., 1h, 6h)
sum(increase(fscrawler_documents_processed_total{status="success"}[1h]))
/
sum(increase(fscrawler_documents_processed_total[1h]))

# DLQ retry effectiveness SLI — ratio of retries that succeed
rate(fscrawler_dlq_retries_total{fscrawler_retry_outcome="success"}[5m])
/
rate(fscrawler_dlq_retries_total[5m])
```

For SLO alerting, use multi-window burn-rate alerts per the Google SRE model. Example: if the SLO is 99.9% success rate over 30 days, alert when the 1h burn rate exceeds 14.4x (consuming the entire error budget in 5% of the window) AND the 5m burn rate also exceeds 14.4x (confirming the issue is current, not stale).

### Queue and WAL

```promql
# PFQ growth (documents needing human intervention)
increase(fscrawler_pfq_records_total[1h])

# WAL recovery events (indicates prior crash)
increase(fscrawler_wal_records_total{fscrawler_wal_action="recover"}[1h])

# DLQ inflow vs outflow (should trend toward zero in healthy state)
rate(fscrawler_dlq_records_total[5m])
-
rate(fscrawler_dlq_retries_total{fscrawler_retry_outcome="success"}[5m])
```

### Latency

```promql
# Bulk flush p95 latency
histogram_quantile(0.95, rate(fscrawler_bulk_duration_seconds_bucket[5m]))

# Bulk flush p99 latency (tail — scaling signal)
histogram_quantile(0.99, rate(fscrawler_bulk_duration_seconds_bucket[5m]))
```

## References

- [open-telemetry/semantic-conventions: recording-errors.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/general/recording-errors.md) — guidance on single metric for success+failure, error.type attribute
- [open-telemetry/semantic-conventions: error.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/registry/attributes/error.md) — error.type attribute definition (Stable)
- [open-telemetry/semantic-conventions: database-metrics.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/db/database-metrics.md) — operation duration histogram, bucket boundaries, batch recording
- [open-telemetry/semantic-conventions: messaging-metrics.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/messaging/messaging-metrics.md) — messaging counter patterns
- [open-telemetry/semantic-conventions: exceptions-logs.md](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/exceptions/exceptions-logs.md) — exception event logging
- [open-telemetry/opentelemetry-specification: metrics/api.md](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/metrics/api.md) — Counter, Histogram, UpDownCounter definitions
- [open-telemetry/opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python) — Python SDK
