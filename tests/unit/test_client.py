"""Unit tests for fscrawler.client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from fscrawler.settings import FsSettings

DATA_DIR = Path(__file__).parent.parent / "data"


def load_fixture(name: str) -> dict[str, Any]:
    with open(DATA_DIR / name) as f:
        return json.load(f)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_settings(**kwargs: Any) -> FsSettings:
    base: dict[str, Any] = {"name": "test", "fs": {"url": "/data"}}
    base.update(kwargs)
    return FsSettings.from_dict(base)


# ---------------------------------------------------------------------------
# FsCrawlerClient initialisation
# ---------------------------------------------------------------------------


class TestClientInit:
    def test_client_created_with_single_node(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings(
            elasticsearch={"nodes": [{"url": "http://localhost:9200"}]}
        )
        client = FsCrawlerClient(settings)
        assert client is not None

    def test_client_uses_all_nodes(self) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings(
            elasticsearch={
                "nodes": [
                    {"url": "http://node1:9200"},
                    {"url": "http://node2:9200"},
                ]
            }
        )
        with patch("fscrawler.client.OpenSearch") as mock_cls:
            mock_cls.return_value = MagicMock()
            mock_cls.return_value.info.return_value = load_fixture("opensearch_info.json")
            FsCrawlerClient(settings)
            call_kwargs = mock_cls.call_args
            hosts = call_kwargs[1].get("hosts") or call_kwargs[0][0]
            assert len(hosts) == 2

    def test_ssl_verification_false_disables_verify(self) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings(
            elasticsearch={
                "nodes": [{"url": "https://localhost:9200"}],
                "ssl_verification": False,
            }
        )
        with patch("fscrawler.client.OpenSearch") as mock_cls:
            mock_cls.return_value = MagicMock()
            mock_cls.return_value.info.return_value = load_fixture("opensearch_info.json")
            FsCrawlerClient(settings)
            call_kwargs = mock_cls.call_args[1]
            # ssl_context or verify_certs should disable verification
            assert (
                call_kwargs.get("verify_certs") is False
                or call_kwargs.get("ssl_assert_hostname") is False
                or "ssl_context" in call_kwargs
            )


# ---------------------------------------------------------------------------
# get_info
# ---------------------------------------------------------------------------


class TestGetInfo:
    def test_get_info_parses_version(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        info = client.get_info()
        assert info["version"]["number"] == "3.5.0"
        assert info["version"]["distribution"] == "opensearch"

    def test_get_info_returns_cluster_name(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        info = client.get_info()
        assert info["cluster_name"] == "opensearch-cluster"


# ---------------------------------------------------------------------------
# push_templates
# ---------------------------------------------------------------------------


class TestPushTemplates:
    def test_creates_component_templates_when_missing(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        # Template does not exist → side_effect raises
        client.push_templates()
        assert mock_opensearch_client.cluster.put_component_template.called

    def test_skips_existing_component_template(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        # Make get_component_template succeed (template exists)
        mock_opensearch_client.cluster.get_component_template.side_effect = None
        mock_opensearch_client.cluster.get_component_template.return_value = load_fixture(
            "component_template_exists.json"
        )
        mock_opensearch_client.indices.get_index_template.side_effect = None
        mock_opensearch_client.indices.get_index_template.return_value = {
            "index_templates": [{"name": "fscrawler_test_docs"}]
        }
        client.push_templates(force=False)
        mock_opensearch_client.cluster.put_component_template.assert_not_called()

    def test_force_recreates_existing_templates(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        mock_opensearch_client.cluster.get_component_template.side_effect = None
        mock_opensearch_client.cluster.get_component_template.return_value = load_fixture(
            "component_template_exists.json"
        )
        client.push_templates(force=True)
        assert mock_opensearch_client.cluster.put_component_template.called

    def test_push_templates_disabled_when_setting_false(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings(
            elasticsearch={"nodes": [{"url": "http://localhost:9200"}], "push_templates": False}
        )
        client = FsCrawlerClient(settings)
        client.push_templates()
        mock_opensearch_client.cluster.put_component_template.assert_not_called()


# ---------------------------------------------------------------------------
# wait_for_cluster — exponential retry on startup
# ---------------------------------------------------------------------------


class TestWaitForCluster:
    def test_succeeds_immediately_when_cluster_ready(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        # info() already stubbed to succeed in the fixture — should return without sleeping
        with patch("fscrawler.client.time.sleep") as mock_sleep:
            client.wait_for_cluster()
        mock_sleep.assert_not_called()

    def test_retries_on_connection_error_then_succeeds(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        # Fail twice then succeed
        mock_opensearch_client.info.side_effect = [
            OSConnectionError("N/A", "refused", None),
            OSConnectionError("N/A", "refused", None),
            load_fixture("opensearch_info.json"),
        ]
        with patch("fscrawler.client.time.sleep"):
            client.wait_for_cluster()  # must not raise

    def test_raises_after_max_retries_exhausted(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        mock_opensearch_client.info.side_effect = OSConnectionError("N/A", "refused", None)
        with patch("fscrawler.client.time.sleep"):
            with pytest.raises(OSConnectionError):
                client.wait_for_cluster(max_retries=3)

    def test_sleep_duration_doubles_each_attempt(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        mock_opensearch_client.info.side_effect = [
            OSConnectionError("N/A", "refused", None),
            OSConnectionError("N/A", "refused", None),
            OSConnectionError("N/A", "refused", None),
            load_fixture("opensearch_info.json"),
        ]
        with patch("fscrawler.client.time.sleep") as mock_sleep:
            client.wait_for_cluster(base_delay=2.0)
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [2.0, 4.0, 8.0]

    def test_sleep_is_capped_at_max_delay(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        mock_opensearch_client.info.side_effect = [
            OSConnectionError("N/A", "refused", None),
            OSConnectionError("N/A", "refused", None),
            OSConnectionError("N/A", "refused", None),
            OSConnectionError("N/A", "refused", None),
            load_fixture("opensearch_info.json"),
        ]
        with patch("fscrawler.client.time.sleep") as mock_sleep:
            client.wait_for_cluster(base_delay=10.0, max_delay=15.0)
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert max(delays) <= 15.0

    def test_does_not_retry_on_auth_error(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from opensearchpy.exceptions import AuthenticationException

        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        mock_opensearch_client.info.side_effect = AuthenticationException(401, "Unauthorized")
        with patch("fscrawler.client.time.sleep") as mock_sleep:
            with pytest.raises(AuthenticationException):
                client.wait_for_cluster()
        mock_sleep.assert_not_called()

    def test_logs_warning_on_each_retry(
        self, mock_opensearch_client: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from opensearchpy.exceptions import ConnectionError as OSConnectionError

        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        mock_opensearch_client.info.side_effect = [
            OSConnectionError("N/A", "refused", None),
            load_fixture("opensearch_info.json"),
        ]
        with patch("fscrawler.client.time.sleep"):
            with caplog.at_level(logging.WARNING, logger="fscrawler.client"):
                client.wait_for_cluster()
        assert any("retry" in r.message.lower() or "attempt" in r.message.lower()
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# Bulk indexing
# ---------------------------------------------------------------------------


class TestBulkIndex:
    def test_bulk_index_calls_bulk_api(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        operations = [
            {"index": {"_index": "test_docs", "_id": "doc1"}},
            {"content": "hello"},
        ]
        result = client.bulk(operations)
        mock_opensearch_client.bulk.assert_called_once()
        assert result["errors"] is False

    def test_bulk_delete_included_in_operations(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        operations = [
            {"delete": {"_index": "test_docs", "_id": "doc_to_delete"}},
        ]
        client.bulk(operations)
        call_args = mock_opensearch_client.bulk.call_args
        body_arg = call_args[1].get("body") or call_args[0][0]
        assert any("delete" in str(op) for op in body_arg)


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


class TestIndexManagement:
    def test_index_exists_returns_false_when_missing(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient

        mock_opensearch_client.indices.exists.return_value = False
        settings = make_settings()
        client = FsCrawlerClient(settings)
        assert client.index_exists("some_index") is False

    def test_index_exists_returns_true_when_present(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient

        mock_opensearch_client.indices.exists.return_value = True
        settings = make_settings()
        client = FsCrawlerClient(settings)
        assert client.index_exists("some_index") is True

    def test_create_index_calls_indices_create(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        client.create_index("new_index")
        mock_opensearch_client.indices.create.assert_called_once_with(index="new_index")

    def test_delete_document(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient

        mock_opensearch_client.delete.return_value = {"result": "deleted"}
        settings = make_settings()
        client = FsCrawlerClient(settings)
        client.delete_document("test_docs", "doc1")
        mock_opensearch_client.delete.assert_called_once_with(index="test_docs", id="doc1")
