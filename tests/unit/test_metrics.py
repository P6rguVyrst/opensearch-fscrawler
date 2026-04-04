"""Unit tests for fscrawler.metrics."""

from __future__ import annotations

from unittest.mock import patch


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


class TestGetPrometheusApp:
    def test_returns_app_after_configure(self) -> None:
        from fscrawler.metrics import configure_metrics, get_prometheus_app
        configure_metrics(enable_prometheus=True)
        app = get_prometheus_app()
        assert app is not None
