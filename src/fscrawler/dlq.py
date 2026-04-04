# Licensed under the Apache License, Version 2.0
"""Dead Letter Queue and Permanent Failure Queue for FSCrawler."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("fscrawler.dlq")

DLQ_INDEX = "fscrawler_dlq"
PFQ_INDEX = "fscrawler_pfq"

_NON_RETRYABLE_ERRORS = frozenset({
    "mapper_parsing_exception",
    "illegal_argument_exception",
    "version_conflict_engine_exception",
    "strict_dynamic_mapping_exception",
    "routing_missing_exception",
})


def is_retryable_error(error_type: str) -> bool:
    """Return True if the error type is eligible for DLQ retry."""
    return error_type not in _NON_RETRYABLE_ERRORS


def calculate_next_retry_delay(
    retry_count: int,
    base: int = 60,
    multiplier: float = 2.0,
    cap: int = 3600,
) -> int:
    """Calculate delay in seconds: min(base * multiplier^retry_count, cap)."""
    delay = base * (multiplier ** retry_count)
    return int(min(delay, cap))


def make_dlq_doc_id(job_name: str, doc_id: str) -> str:
    """Build DLQ/PFQ document _id: '{job_name}:{doc_id}'."""
    return f"{job_name}:{doc_id}"


def build_dlq_record(
    job_name: str,
    target_index: str,
    doc_id: str,
    action: str,
    payload: dict[str, Any] | None,
    error_message: str,
    error_type: str,
    source_path: str = "",
    pipeline: str = "",
    retry_interval: int = 60,
) -> dict[str, Any]:
    """Build a DLQ record for a failed document."""
    now = datetime.now(tz=UTC)
    next_retry = now + timedelta(seconds=retry_interval)
    return {
        "job_name": job_name,
        "target_index": target_index,
        "doc_id": doc_id,
        "action": action,
        "payload": payload,
        "pipeline": pipeline,
        "error_message": error_message,
        "error_type": error_type,
        "first_failed": now.isoformat(),
        "last_retried": now.isoformat(),
        "retry_count": 0,
        "next_retry": next_retry.isoformat(),
        "source_path": source_path,
    }


def build_pfq_record(
    dlq_record: dict[str, Any],
    final_error: str,
) -> dict[str, Any]:
    """Promote a DLQ record to a PFQ record."""
    now = datetime.now(tz=UTC)
    pfq = {k: v for k, v in dlq_record.items() if k != "next_retry"}
    pfq["promoted_at"] = now.isoformat()
    pfq["final_error"] = final_error
    return pfq
