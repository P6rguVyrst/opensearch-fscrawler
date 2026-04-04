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

bulk_duration: Histogram = _meter.create_histogram(
    name="fscrawler.bulk.duration",
    description="Duration of bulk flush operations",
    unit="s",
)

# ---------------------------------------------------------------------------
# Prometheus ASGI app (for /metrics endpoint)
# ---------------------------------------------------------------------------

_prometheus_app: Any = None


def get_prometheus_app() -> Any:
    """Return the WSGI app that serves /metrics for Prometheus scraping.

    Returns None if configure_metrics() hasn't been called with enable_prometheus=True.
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
