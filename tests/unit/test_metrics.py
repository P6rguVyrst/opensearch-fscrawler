"""Unit tests for fscrawler.metrics."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import fscrawler.metrics as metrics_mod


@pytest.fixture(autouse=True)
def _reset_configure_guard() -> None:
    """Reset the idempotency guard so each test can call configure_metrics()."""
    metrics_mod._configured = False


class TestConfigureMetrics:
    def test_configure_creates_meter_provider(self) -> None:
        from fscrawler.metrics import configure_metrics
        configure_metrics()

    def test_configure_with_otel_endpoint(self) -> None:
        from fscrawler.metrics import configure_metrics
        with patch(
            "opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter"
        ) as mock_exporter_cls:
            configure_metrics(otel_endpoint="http://collector:4318")
            mock_exporter_cls.assert_called_once()
            call_kwargs = mock_exporter_cls.call_args[1]
            assert "/v1/metrics" in call_kwargs["endpoint"]

    def test_configure_without_endpoint_no_otlp_exporter(self) -> None:
        from fscrawler.metrics import configure_metrics
        with patch(
            "opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter"
        ) as mock_exporter_cls:
            configure_metrics(otel_endpoint=None)
            mock_exporter_cls.assert_not_called()

    def test_configure_is_idempotent(self) -> None:
        """Second call to configure_metrics() is a no-op."""
        from fscrawler.metrics import configure_metrics
        with patch("opentelemetry.sdk.metrics.MeterProvider") as mock_provider:
            configure_metrics()
            first_call_count = mock_provider.call_count
            configure_metrics()
            assert mock_provider.call_count == first_call_count


class TestInstruments:
    def test_documents_processed_exists(self) -> None:
        from fscrawler.metrics import documents_processed
        assert documents_processed is not None

    def test_bulk_duration_exists(self) -> None:
        from fscrawler.metrics import bulk_duration
        assert bulk_duration is not None

    def test_dlq_records_exists(self) -> None:
        from fscrawler.metrics import dlq_records
        assert dlq_records is not None

    def test_pfq_records_exists(self) -> None:
        from fscrawler.metrics import pfq_records
        assert pfq_records is not None

    def test_dlq_retries_exists(self) -> None:
        from fscrawler.metrics import dlq_retries
        assert dlq_retries is not None

    def test_wal_records_exists(self) -> None:
        from fscrawler.metrics import wal_records
        assert wal_records is not None


class TestHistogramBucketBoundaries:
    def test_bulk_duration_has_explicit_bucket_boundaries(self) -> None:
        """I2: bulk_duration histogram must use the OTel database convention boundaries."""
        from fscrawler.metrics import BULK_DURATION_BOUNDARIES
        expected = [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10]
        assert expected == BULK_DURATION_BOUNDARIES


class TestGetPrometheusApp:
    def test_returns_app_after_configure(self) -> None:
        from fscrawler.metrics import configure_metrics, get_prometheus_app
        configure_metrics(enable_prometheus=True)
        app = get_prometheus_app()
        assert app is not None
