"""Unit tests for fscrawler.dlq."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestErrorClassification:
    def test_connection_error_is_retryable(self) -> None:
        from fscrawler.dlq import is_retryable_error

        assert is_retryable_error("connection_error") is True

    def test_timeout_is_retryable(self) -> None:
        from fscrawler.dlq import is_retryable_error

        assert is_retryable_error("timeout") is True

    def test_circuit_breaking_is_retryable(self) -> None:
        from fscrawler.dlq import is_retryable_error

        assert is_retryable_error("circuit_breaking_exception") is True

    def test_cluster_block_is_retryable(self) -> None:
        from fscrawler.dlq import is_retryable_error

        assert is_retryable_error("cluster_block_exception") is True

    def test_mapper_parsing_not_retryable(self) -> None:
        from fscrawler.dlq import is_retryable_error

        assert is_retryable_error("mapper_parsing_exception") is False

    def test_illegal_argument_not_retryable(self) -> None:
        from fscrawler.dlq import is_retryable_error

        assert is_retryable_error("illegal_argument_exception") is False

    def test_version_conflict_not_retryable(self) -> None:
        from fscrawler.dlq import is_retryable_error

        assert is_retryable_error("version_conflict_engine_exception") is False

    def test_unknown_error_defaults_to_retryable(self) -> None:
        from fscrawler.dlq import is_retryable_error

        assert is_retryable_error("some_unknown_error") is True


class TestBackoffCalculation:
    def test_first_retry(self) -> None:
        from fscrawler.dlq import calculate_next_retry_delay

        assert calculate_next_retry_delay(retry_count=0, base=60, multiplier=2.0, cap=3600) == 60

    def test_second_retry(self) -> None:
        from fscrawler.dlq import calculate_next_retry_delay

        assert calculate_next_retry_delay(retry_count=1, base=60, multiplier=2.0, cap=3600) == 120

    def test_cap_applied(self) -> None:
        from fscrawler.dlq import calculate_next_retry_delay

        assert calculate_next_retry_delay(retry_count=100, base=60, multiplier=2.0, cap=3600) == 3600


class TestDLQRecordBuilder:
    def test_build_dlq_record(self) -> None:
        from fscrawler.dlq import build_dlq_record

        record = build_dlq_record(
            job_name="myjob",
            target_index="fscrawler_docs_myjob",
            doc_id="abc123",
            action="index",
            payload={"content": "hello"},
            error_message="rejected",
            error_type="circuit_breaking_exception",
            source_path="/data/test.txt",
        )
        assert record["job_name"] == "myjob"
        assert record["doc_id"] == "abc123"
        assert record["retry_count"] == 0
        assert "first_failed" in record
        assert "next_retry" in record

    def test_build_pfq_record(self) -> None:
        from fscrawler.dlq import build_pfq_record

        dlq_record = {
            "job_name": "myjob",
            "target_index": "fscrawler_docs_myjob",
            "doc_id": "abc123",
            "action": "index",
            "payload": {"content": "hello"},
            "error_message": "rejected",
            "error_type": "mapper_parsing_exception",
            "first_failed": "2026-04-04T12:00:00Z",
            "retry_count": 5,
            "source_path": "/data/test.txt",
            "next_retry": "2026-04-04T13:00:00Z",
        }
        record = build_pfq_record(dlq_record, final_error="permanent failure after 5 retries")
        assert record["promoted_at"] is not None
        assert record["final_error"] == "permanent failure after 5 retries"
        assert record["job_name"] == "myjob"
        assert "next_retry" not in record


class TestDLQDocId:
    def test_dlq_doc_id_format(self) -> None:
        from fscrawler.dlq import make_dlq_doc_id

        assert make_dlq_doc_id("myjob", "abc123") == "myjob:abc123"


def _make_config(max_retries=5, retry_interval=60, backoff_multiplier=2.0, max_backoff=3600):
    """Build a mock config object with DLQ-related attributes."""
    cfg = MagicMock()
    cfg.max_retries = max_retries
    cfg.retry_interval = retry_interval
    cfg.backoff_multiplier = backoff_multiplier
    cfg.max_backoff = max_backoff
    return cfg


def _make_dlq_hit(
    dlq_doc_id="myjob:abc123",
    job_name="myjob",
    target_index="fscrawler_docs_myjob",
    doc_id="abc123",
    action="index",
    payload=None,
    pipeline="",
    retry_count=0,
    error_message="rejected",
    error_type="circuit_breaking_exception",
):
    """Build a fake search hit dict mimicking OpenSearch response."""
    if payload is None:
        payload = {"content": "hello"}
    return {
        "_id": dlq_doc_id,
        "_source": {
            "job_name": job_name,
            "target_index": target_index,
            "doc_id": doc_id,
            "action": action,
            "payload": payload,
            "pipeline": pipeline,
            "retry_count": retry_count,
            "error_message": error_message,
            "error_type": error_type,
            "first_failed": "2026-04-04T12:00:00+00:00",
            "last_retried": "2026-04-04T12:00:00+00:00",
            "next_retry": "2026-04-04T12:01:00+00:00",
            "source_path": "/data/test.txt",
        },
    }


class TestRunRetryCycle:
    def test_retry_query_loaded_once_at_module_import(self) -> None:
        from fscrawler import dlq

        client = MagicMock()
        client.search.return_value = {"hits": {"hits": []}}
        config = _make_config()

        query_file = dlq._QUERIES_DIR / "dlq_due_records.json"
        real_open = open
        open_calls = 0

        def counting_open(path: str | Path, *args, **kwargs):
            nonlocal open_calls
            if Path(path) == query_file:
                open_calls += 1
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=counting_open):
            importlib.reload(dlq)
            dlq.run_retry_cycle(client, config)
            dlq.run_retry_cycle(client, config)

        assert open_calls == 1

    def test_no_due_records_is_noop(self) -> None:
        from fscrawler.dlq import run_retry_cycle

        client = MagicMock()
        client.search.return_value = {"hits": {"hits": []}}
        config = _make_config()

        run_retry_cycle(client, config)

        client.bulk.assert_not_called()
        client.delete_document.assert_not_called()
        client.index_raw.assert_not_called()

    def test_successful_retry_deletes_from_dlq(self) -> None:
        from fscrawler.dlq import DLQ_INDEX, run_retry_cycle

        client = MagicMock()
        hit = _make_dlq_hit()
        client.search.return_value = {"hits": {"hits": [hit]}}
        # bulk succeeds (no exception)
        client.bulk.return_value = {"errors": False}
        config = _make_config()

        run_retry_cycle(client, config)

        client.bulk.assert_called_once()
        client.delete_document.assert_called_once_with(
            index=DLQ_INDEX, doc_id="myjob:abc123"
        )

    def test_failed_retry_increments_count(self) -> None:
        from fscrawler.dlq import DLQ_INDEX, run_retry_cycle

        client = MagicMock()
        hit = _make_dlq_hit(retry_count=1)
        client.search.return_value = {"hits": {"hits": [hit]}}
        client.bulk.side_effect = Exception("cluster overloaded")
        config = _make_config(max_retries=5)

        run_retry_cycle(client, config)

        # Should write updated record back to DLQ
        client.index_raw.assert_called_once()
        call_kwargs = client.index_raw.call_args
        assert call_kwargs[1]["index"] == DLQ_INDEX or call_kwargs[0][0] == DLQ_INDEX
        # Extract the body that was written
        if "body" in call_kwargs[1]:
            body = call_kwargs[1]["body"]
        else:
            # positional: index, doc_id, body
            body = call_kwargs[0][2] if len(call_kwargs[0]) > 2 else call_kwargs[1]["body"]
        assert body["retry_count"] == 2
        # next_retry must be set and represent a valid ISO timestamp
        assert "next_retry" in body
        assert isinstance(body["next_retry"], str)
        assert len(body["next_retry"]) > 0

    def test_max_retries_promotes_to_pfq(self) -> None:
        from fscrawler.dlq import DLQ_INDEX, PFQ_INDEX, run_retry_cycle

        client = MagicMock()
        hit = _make_dlq_hit(retry_count=4)
        client.search.return_value = {"hits": {"hits": [hit]}}
        client.bulk.side_effect = Exception("still broken")
        config = _make_config(max_retries=5)

        run_retry_cycle(client, config)

        # Should write to PFQ
        pfq_calls = [
            c for c in client.index_raw.call_args_list
            if (c[1].get("index") == PFQ_INDEX or (c[0] and c[0][0] == PFQ_INDEX))
        ]
        assert len(pfq_calls) == 1

        # Should delete from DLQ
        dlq_delete_calls = [
            c for c in client.delete_document.call_args_list
            if (c[1].get("index") == DLQ_INDEX or (c[0] and c[0][0] == DLQ_INDEX))
        ]
        assert len(dlq_delete_calls) == 1

    def test_search_failure_logs_and_returns(self) -> None:
        from fscrawler.dlq import run_retry_cycle

        client = MagicMock()
        client.search.side_effect = Exception("connection refused")
        config = _make_config()

        # Must not raise
        run_retry_cycle(client, config)

        client.bulk.assert_not_called()
        client.delete_document.assert_not_called()

    def test_job_name_filter_added_when_provided(self) -> None:
        from fscrawler.dlq import run_retry_cycle

        client = MagicMock()
        client.search.return_value = {"hits": {"hits": []}}
        config = _make_config()

        run_retry_cycle(client, config, job_name="myjob")

        search_body = client.search.call_args[1].get(
            "body", client.search.call_args[0][1] if len(client.search.call_args[0]) > 1 else None
        )
        filters = search_body["query"]["bool"]["filter"]
        term_filters = [f for f in filters if "term" in f]
        assert any(f["term"]["job_name"] == "myjob" for f in term_filters)

    def test_delete_action_retry(self) -> None:
        from fscrawler.dlq import DLQ_INDEX, run_retry_cycle

        client = MagicMock()
        hit = _make_dlq_hit(action="delete", payload=None)
        client.search.return_value = {"hits": {"hits": [hit]}}
        # delete_document succeeds
        client.delete_document.return_value = {"result": "deleted"}
        config = _make_config()

        run_retry_cycle(client, config)

        # Should call delete_document on the target index (not bulk)
        client.bulk.assert_not_called()
        target_delete_calls = [
            c for c in client.delete_document.call_args_list
            if (c[1].get("index") == "fscrawler_docs_myjob")
        ]
        assert len(target_delete_calls) == 1
        # Should also delete from DLQ on success
        dlq_delete_calls = [
            c for c in client.delete_document.call_args_list
            if (c[1].get("index") == DLQ_INDEX)
        ]
        assert len(dlq_delete_calls) == 1


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
