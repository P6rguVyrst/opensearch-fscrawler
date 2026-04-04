# Licensed under the Apache License, Version 2.0
"""Dead Letter Queue and Permanent Failure Queue for FSCrawler."""

from __future__ import annotations

import copy
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("fscrawler.dlq")

_QUERIES_DIR = Path(__file__).parent / "_queries"

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


def run_retry_cycle(
    client: Any,
    config: Any,
    job_name: str | None = None,
) -> None:
    """Query DLQ for due records and retry each one."""
    with open(_QUERIES_DIR / "dlq_due_records.json") as f:
        query = json.load(f)

    query = copy.deepcopy(query)

    if job_name is not None:
        query["query"]["bool"]["filter"].append(
            {"term": {"job_name": job_name}}
        )

    try:
        result = client.search(index=DLQ_INDEX, body=query)
    except Exception:
        logger.exception("Failed to search DLQ for due records")
        return

    hits = result.get("hits", {}).get("hits", [])
    for hit in hits:
        dlq_doc_id = hit["_id"]
        source = hit["_source"]
        _retry_single_record(client, config, dlq_doc_id, source)


def _retry_single_record(
    client: Any,
    config: Any,
    dlq_doc_id: str,
    source: dict[str, Any],
) -> None:
    """Attempt to re-index/delete a single DLQ record."""
    doc_id = source["doc_id"]
    job_name = source["job_name"]
    retry_count = source["retry_count"]
    target_index = source["target_index"]
    action = source["action"]
    payload = source.get("payload")
    pipeline = source.get("pipeline", "")

    try:
        if action == "delete":
            client.delete_document(index=target_index, doc_id=doc_id)
        else:
            bulk_action: dict[str, Any] = {"index": {"_index": target_index, "_id": doc_id}}
            if pipeline:
                bulk_action["index"]["pipeline"] = pipeline
            client.bulk([bulk_action, payload])
    except Exception as exc:
        new_count = retry_count + 1
        error_msg = str(exc)

        if new_count >= config.max_retries:
            pfq_record = build_pfq_record(source, final_error=error_msg)
            pfq_doc_id = make_dlq_doc_id(job_name, doc_id)
            client.index_raw(index=PFQ_INDEX, doc_id=pfq_doc_id, body=pfq_record)
            client.delete_document(index=DLQ_INDEX, doc_id=dlq_doc_id)
            logger.warning(
                "Promoted %s to PFQ after %d retries: %s",
                dlq_doc_id, new_count, error_msg,
            )
        else:
            now = datetime.now(tz=UTC)
            delay = calculate_next_retry_delay(
                retry_count=new_count,
                base=config.retry_interval,
                multiplier=config.backoff_multiplier,
                cap=config.max_backoff,
            )
            source["retry_count"] = new_count
            source["last_retried"] = now.isoformat()
            source["next_retry"] = (now + timedelta(seconds=delay)).isoformat()
            source["error_message"] = error_msg
            client.index_raw(index=DLQ_INDEX, doc_id=dlq_doc_id, body=source)
            logger.info(
                "Retry %d for %s failed, next retry in %ds: %s",
                new_count, dlq_doc_id, delay, error_msg,
            )
        return

    # Success: remove from DLQ
    client.delete_document(index=DLQ_INDEX, doc_id=dlq_doc_id)
    logger.info("Successfully retried %s, removed from DLQ", dlq_doc_id)
