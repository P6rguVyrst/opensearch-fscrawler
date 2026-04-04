"""Unit tests for fscrawler.dlq."""

from __future__ import annotations


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
