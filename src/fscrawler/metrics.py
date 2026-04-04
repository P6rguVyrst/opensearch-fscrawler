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
from typing import Any

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

BULK_DURATION_BOUNDARIES = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]

bulk_duration: Histogram = _meter.create_histogram(
    name="fscrawler.bulk.duration",
    description="Duration of bulk flush operations",
    unit="s",
    explicit_bucket_boundaries_advisory=BULK_DURATION_BOUNDARIES,
)

# ---------------------------------------------------------------------------
# Prometheus ASGI app (for /metrics endpoint)
# ---------------------------------------------------------------------------

_prometheus_app: Any = None


def get_prometheus_app() -> Any:
    """Return the ASGI app that serves /metrics for Prometheus scraping.

    Returns None if configure_metrics() hasn't been called with enable_prometheus=True.
    """
    return _prometheus_app


# ---------------------------------------------------------------------------
# Configuration (call once from cli.py startup)
# ---------------------------------------------------------------------------


_configured: bool = False


def configure_metrics(
    *,
    otel_endpoint: str | None = None,
    enable_prometheus: bool = False,
) -> None:
    """Attach metric readers/exporters to the global MeterProvider.

    Safe to call multiple times — subsequent calls are no-ops.

    Parameters
    ----------
    otel_endpoint:
        OTLP/HTTP base URL. Metrics pushed to ``{url}/v1/metrics``.
    enable_prometheus:
        If True, create a PrometheusMetricReader so ``get_prometheus_app()``
        returns an ASGI app for the ``/metrics`` route.
    """
    global _prometheus_app, _configured

    if _configured:
        logger.debug("configure_metrics() already called — skipping")
        return
    _configured = True

    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": "fscrawler"})
    readers: list[Any] = []

    if enable_prometheus:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from prometheus_client import make_asgi_app

        prometheus_reader = PrometheusMetricReader()
        readers.append(prometheus_reader)
        _prometheus_app = make_asgi_app()
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
