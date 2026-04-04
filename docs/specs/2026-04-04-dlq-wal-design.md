# DLQ + WAL — Design Spec

**Date**: 2026-04-04
**Status**: Draft
**Related**: [Vector Search Design](./2026-04-04-vector-search-design.md) (references DLQ for indexing failures)

## Problem

Fscrawler has multiple failure modes that silently drop data:

- **Bulk flush failures** in `BulkIndexer._flush_locked()` — the entire buffer is cleared on error, losing all buffered documents
- **Watchdog event failures** in `FsEventHandler._index()` — logged and silently dropped, never retried
- **Parse failures** with `continue_on_error: true` — skipped files are forgotten
- **Partial bulk responses** — individual item errors within a successful bulk request are logged but the documents are never retried

There is no durability guarantee between "file parsed" and "document indexed." A crash, network blip, or transient OpenSearch error means data loss.

## Design Principles

- **WAL is the guarantee** — every document is fsync'd to local disk before being sent to OpenSearch. If fscrawler crashes, the WAL survives.
- **DLQ is the queryable view** — failed documents are indexed to a shared OpenSearch index for visibility, dashboards, and alerting
- **PFQ is the graveyard** — permanently failed documents (max retries exceeded) are promoted to a separate index requiring human intervention
- **No overhead for happy path** — WAL is append-only JSONL, fsync'd. DLQ/PFQ indices are only written to on failure.
- **Shared indices** — `fscrawler_dlq` and `fscrawler_pfq` serve all jobs; `job_name` is a field, not part of the index name

## Architecture

```
                  ┌─────────┐
                  │  Parse  │
                  └────┬────┘
                       │
                  ┌────▼────┐
                  │   WAL   │  ← fsync'd append, JSONL
                  │ (local) │
                  └────┬────┘
                       │
                  ┌────▼────┐
               ┌──│  Bulk   │──┐
               │  │ Indexer  │  │
               │  └─────────┘  │
          success          failure
               │               │
        ┌──────▼──────┐  ┌─────▼─────┐
        │  Checkpoint  │  │    DLQ    │  ← retryable failures
        │  WAL remove  │  │  (index)  │
        └─────────────┘  └─────┬─────┘
                               │ retry loop
                          ┌────▼────┐
                     ┌────│  Retry  │────┐
                     │    └─────────┘    │
                success             max retries
                     │                   │
              ┌──────▼──────┐     ┌──────▼──────┐
              │ Index to     │     │     PFQ     │  ← permanent failures
              │ original     │     │   (index)   │
              │ target       │     └─────────────┘
              │ Remove from  │
              │ DLQ          │
              └─────────────┘
```

## WAL (Write-Ahead Log)

### Purpose

Local durability layer. Every document is written to the WAL **before** being sent to OpenSearch. If fscrawler crashes between parse and index, the WAL allows recovery on restart.

### Location

```
~/.fscrawler/.wal
```

Single shared WAL file for all jobs. Trade-off: per-job WAL files would allow independent recovery but add complexity (multiple files to manage, coordinate, checkpoint). A single file is simpler and sufficient — the `job_name` field in each record identifies the owning job.

### Format

Append-only JSONL (one JSON object per line):

```json
{"ts": "2026-04-04T12:00:00.123Z", "job_name": "my_job", "target_index": "fscrawler_docs_my_job", "doc_id": "sha256hash", "action": "index", "payload": {"@timestamp": "...", "content": "...", "file": {}, "path": {}, "meta": {}}}
{"ts": "2026-04-04T12:00:00.456Z", "job_name": "my_job", "target_index": "fscrawler_docs_my_job", "doc_id": "sha256hash2", "action": "delete"}
```

### WAL Record Fields

| Field | Type | Description |
|-------|------|-------------|
| `ts` | ISO 8601 | When the record was written |
| `job_name` | string | Owning job name |
| `target_index` | string | Destination index (e.g., `fscrawler_docs_myjob` or `fscrawler_docs_myjob_vector`) |
| `doc_id` | string | Document `_id` (SHA256 of virtual path) |
| `action` | string | `"index"` or `"delete"` |
| `payload` | object | Full document body (omitted for deletes) |
| `pipeline` | string | Optional ingest pipeline name (for vector documents) |

### Write Protocol

```python
def wal_append(record: dict) -> None:
    line = json.dumps(record, separators=(",", ":")) + "\n"
    with open(WAL_PATH, "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
```

Every write is individually fsync'd. This is the durability guarantee — if fscrawler crashes after `wal_append()` returns, the record is on disk.

### Checkpoint (WAL Compaction)

After a successful bulk flush, processed records are removed from the WAL. This uses atomic rewrite, not in-place editing:

```
1. Read WAL file
2. Filter out flushed doc_ids
3. Write surviving records to temp file
4. fsync temp file
5. Atomic rename temp → WAL path
```

If fscrawler crashes during checkpoint, the worst case is replaying already-indexed records — which is idempotent (same `doc_id` = upsert).

### Recovery on Startup

```
1. If WAL file exists and is non-empty:
   a. Read all records
   b. Group by job_name
   c. Re-submit to bulk indexer
   d. Checkpoint after successful flush
2. Then proceed with normal crawl
```

## DLQ Index (`fscrawler_dlq`)

### Purpose

Queryable store for documents that failed to index but are eligible for retry. Provides visibility into failures via OpenSearch dashboards, alerting, and API queries.

### Index Mapping

```json
{
  "mappings": {
    "properties": {
      "job_name":       { "type": "keyword" },
      "target_index":   { "type": "keyword" },
      "doc_id":         { "type": "keyword" },
      "action":         { "type": "keyword" },
      "payload":        { "type": "object", "enabled": false },
      "pipeline":       { "type": "keyword" },
      "error_message":  { "type": "text" },
      "error_type":     { "type": "keyword" },
      "first_failed":   { "type": "date" },
      "last_retried":   { "type": "date" },
      "retry_count":    { "type": "integer" },
      "next_retry":     { "type": "date" },
      "source_path":    { "type": "keyword" }
    }
  }
}
```

`payload` is stored with `enabled: false` — not indexed or searchable, but preserved in full for exact replay. This avoids mapping conflicts from arbitrary document content.

### DLQ Record Fields

| Field | Type | Description |
|-------|------|-------------|
| `job_name` | keyword | Job that produced this failure |
| `target_index` | keyword | Where the document should have gone |
| `doc_id` | keyword | Document `_id` for the target index |
| `action` | keyword | `"index"` or `"delete"` |
| `payload` | object (disabled) | Complete document body for replay |
| `pipeline` | keyword | Ingest pipeline (if applicable) |
| `error_message` | text | Error description from OpenSearch |
| `error_type` | keyword | Error class (e.g., `mapper_parsing_exception`, `version_conflict`) |
| `first_failed` | date | When this document first failed |
| `last_retried` | date | When the last retry was attempted |
| `retry_count` | integer | Number of retry attempts so far |
| `next_retry` | date | Scheduled next retry time |
| `source_path` | keyword | Original file path (for human reference) |

### DLQ Document `_id`

The DLQ document `_id` is `{job_name}:{doc_id}` — this ensures that repeated failures for the same document update the existing DLQ record rather than creating duplicates.

## PFQ Index (`fscrawler_pfq`)

### Purpose

Permanent Failure Queue. Documents promoted here have exhausted all retries and require human intervention (fix the pipeline, fix the mapping, fix the source file, then manually re-index or re-crawl).

### Index Mapping

Same as DLQ mapping, plus:

```json
{
  "promoted_at":    { "type": "date" },
  "final_error":    { "type": "text" }
}
```

### PFQ Document `_id`

Same scheme as DLQ: `{job_name}:{doc_id}`.

## Retry Logic

### Configuration

New `dlq` block in `_settings.yaml` under `elasticsearch`:

```yaml
elasticsearch:
  dlq:
    max_retries: 5              # default: 5
    retry_interval: 60          # seconds, default: 60
    backoff_multiplier: 2.0     # default: 2.0
    max_backoff: 3600           # seconds (1 hour), default: 3600
    check_interval: 300         # seconds (5 minutes), default: 300
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_retries` | `5` | Maximum retry attempts before promoting to PFQ |
| `retry_interval` | `60` | Base interval between retries (seconds) |
| `backoff_multiplier` | `2.0` | Exponential backoff multiplier |
| `max_backoff` | `3600` | Maximum backoff cap (seconds) |
| `check_interval` | `300` | How often the DLQ retry loop checks for due retries (seconds) |

### Backoff Schedule

```
retry 1: 60s
retry 2: 120s
retry 3: 240s
retry 4: 480s
retry 5: 960s → promote to PFQ
```

Formula: `min(retry_interval * backoff_multiplier ^ (retry_count - 1), max_backoff)`

### Retry Loop

The DLQ retry loop runs independently of the crawl loop on its own check interval:

```
every check_interval:
  1. Query fscrawler_dlq for records where next_retry <= now
  2. For each record:
     a. Re-submit to target_index with original payload and pipeline
     b. On success:
        - Delete from fscrawler_dlq
        - Log info: "DLQ record {doc_id} for {job_name} successfully re-indexed"
     c. On failure:
        - Increment retry_count
        - Update last_retried, next_retry, error_message
        - If retry_count >= max_retries:
          - Copy record to fscrawler_pfq with promoted_at timestamp
          - Delete from fscrawler_dlq
          - Log warning: "DLQ record {doc_id} for {job_name} promoted to PFQ after {max_retries} retries"
```

### Error Classification

Not all errors are retryable. The DLQ should classify errors:

| Error Type | Retryable | Example |
|------------|-----------|---------|
| `connection_error` | Yes | Network blip, node down |
| `timeout` | Yes | Slow cluster |
| `circuit_breaking_exception` | Yes | Memory pressure |
| `cluster_block_exception` | Yes | Read-only index (disk full) |
| `mapper_parsing_exception` | No → PFQ immediately | Bad field type |
| `illegal_argument_exception` | No → PFQ immediately | Invalid document |
| `version_conflict` | No → PFQ immediately | Concurrent modification |

Non-retryable errors skip the DLQ entirely and go straight to PFQ.

## Integration Points

### `BulkIndexer` Changes

1. **Before flush**: all buffered operations are already in the WAL (written at `add()` time)
2. **After flush**: parse bulk response for per-item errors
3. **Successful items**: checkpoint (remove from WAL)
4. **Failed items**: write to DLQ index with error details

```python
def _flush_locked(self) -> None:
    response = self._client.bulk(self._buffer)
    if response.get("errors"):
        for item in response["items"]:
            op = item.get("index") or item.get("delete")
            if op.get("error"):
                self._send_to_dlq(op)
            else:
                self._wal_checkpoint(op["_id"])
    else:
        self._wal_checkpoint_all()
```

### `FsEventHandler` Changes (Watchdog)

1. Write to WAL before calling `client.index()`
2. On failure, write to DLQ instead of silently dropping
3. On success, checkpoint WAL

### `cli.py` Changes

1. **Startup**: run WAL recovery before first crawl
2. **DLQ retry thread**: start alongside crawl loop, runs on its own `check_interval`
3. **Shutdown**: flush any pending WAL entries, stop retry thread gracefully

### `client.py` Changes

1. `push_templates()` — add DLQ and PFQ index templates
2. New methods: `dlq_write()`, `dlq_query_due()`, `dlq_delete()`, `pfq_write()`

## Settings Wiring

### New Dataclass

```python
@dataclass
class DLQConfig:
    max_retries: int = 5
    retry_interval: int = 60          # seconds
    backoff_multiplier: float = 2.0
    max_backoff: int = 3600           # seconds
    check_interval: int = 300         # seconds
```

Added as a field on `ElasticsearchSettings`:

```python
dlq: DLQConfig = field(default_factory=DLQConfig)
```

### Environment Variables

- `FSCRAWLER_ELASTICSEARCH_DLQ_MAX_RETRIES`
- `FSCRAWLER_ELASTICSEARCH_DLQ_RETRY_INTERVAL`
- `FSCRAWLER_ELASTICSEARCH_DLQ_BACKOFF_MULTIPLIER`
- `FSCRAWLER_ELASTICSEARCH_DLQ_MAX_BACKOFF`
- `FSCRAWLER_ELASTICSEARCH_DLQ_CHECK_INTERVAL`

## Template Architecture

### New Component Templates

**`mapping_dlq.json`** — DLQ index mapping (stored in `src/fscrawler/_templates/`):

Fields as specified in the DLQ index mapping section above.

**`mapping_pfq.json`** — PFQ index mapping (DLQ fields + `promoted_at`, `final_error`).

### New Index Templates

| Template | Pattern | Priority | Description |
|----------|---------|----------|-------------|
| `fscrawler_dlq` | `fscrawler_dlq` | 500 | Dead letter queue |
| `fscrawler_pfq` | `fscrawler_pfq` | 500 | Permanent failure queue |

Created during `push_templates()` alongside existing templates.

## Index Naming

| Index | Purpose |
|-------|---------|
| `fscrawler_docs_{jobname}` | Regular documents (existing) |
| `fscrawler_docs_{jobname}_vector` | Vector-enabled documents (vector search spec) |
| `fscrawler_folders_{jobname}` | Folder entries (existing) |
| `fscrawler_history_{jobname}` | Document history (existing) |
| `fscrawler_dlq` | **New** — retryable failures (shared across all jobs) |
| `fscrawler_pfq` | **New** — permanent failures (shared across all jobs) |

## Files Touched

| File | Change |
|------|--------|
| `src/fscrawler/_templates/mapping_dlq.json` | **New** — DLQ index mapping |
| `src/fscrawler/_templates/mapping_pfq.json` | **New** — PFQ index mapping |
| `src/fscrawler/wal.py` | **New** — WAL append, checkpoint, recovery logic |
| `src/fscrawler/dlq.py` | **New** — DLQ/PFQ write, query, retry loop, error classification |
| `src/fscrawler/settings.py` | **Modified** — `DLQConfig` dataclass, parsing, env vars |
| `src/fscrawler/templates.py` | **Modified** — DLQ/PFQ index templates added |
| `src/fscrawler/client.py` | **Modified** — DLQ/PFQ methods, template push |
| `src/fscrawler/indexer.py` | **Modified** — WAL integration, per-item error handling, DLQ routing |
| `src/fscrawler/watcher.py` | **Modified** — WAL integration, DLQ on failure |
| `src/fscrawler/cli.py` | **Modified** — WAL recovery on startup, DLQ retry thread lifecycle |

## Observability

### Logging

| Event | Level | Message |
|-------|-------|---------|
| WAL append | DEBUG | `WAL: appended {action} for {doc_id} ({job_name})` |
| WAL recovery start | INFO | `WAL: recovering {n} records from {path}` |
| WAL recovery complete | INFO | `WAL: recovery complete, {n} records replayed` |
| DLQ write | WARNING | `DLQ: {doc_id} ({job_name}) failed: {error_type} — {error_message}` |
| DLQ retry success | INFO | `DLQ: {doc_id} ({job_name}) re-indexed successfully after {retry_count} retries` |
| PFQ promotion | WARNING | `PFQ: {doc_id} ({job_name}) promoted after {max_retries} retries — requires manual intervention` |
| PFQ immediate | WARNING | `PFQ: {doc_id} ({job_name}) non-retryable error: {error_type} — sent directly to PFQ` |

### Metrics (Future)

- `fscrawler_wal_records_total` — counter of WAL writes
- `fscrawler_dlq_records_total` — counter of DLQ entries by `error_type`
- `fscrawler_dlq_retry_success_total` — counter of successful retries
- `fscrawler_pfq_records_total` — counter of PFQ promotions

## Edge Cases

### Concurrent Jobs Writing to Shared WAL

The WAL file is shared. If multiple jobs run concurrently (e.g., via REST API), writes must be serialized. Use a `threading.Lock` around WAL append — the fsync cost already serializes I/O, so the lock adds negligible overhead.

### WAL File Grows Unbounded

If OpenSearch is down for an extended period, the WAL grows. Mitigation:
- Log a warning when WAL exceeds 100MB
- The retry loop will eventually drain the WAL once OpenSearch recovers
- WAL compaction (checkpoint) runs after every successful flush

### Corrupt WAL Records

If a line in the WAL is not valid JSON (e.g., partial write before crash):
- Skip the corrupt line
- Log warning with the raw line content
- Continue processing remaining records

### DLQ Index Unavailable

If fscrawler cannot write to the DLQ index (e.g., OpenSearch is completely down):
- The document remains in the WAL
- WAL recovery on next startup will re-attempt
- This is the "WAL is the guarantee" principle in action

## Trade-offs

| Decision | Alternative | Rationale |
|----------|-------------|-----------|
| Single shared WAL | Per-job WAL files | Simpler. One file to manage, one lock. Job isolation isn't worth the complexity. |
| Shared DLQ/PFQ indices | Per-job DLQ indices | Simpler. `job_name` field provides filtering. Avoids index proliferation. |
| Full payload in DLQ | Reference to WAL or source file | Enables exact replay without re-parsing. Source file may have changed. WAL is ephemeral. |
| fsync per WAL write | Batch fsync | Durability guarantee per document. The latency cost (~1ms per fsync) is acceptable for the guarantee. |
| JSONL format | SQLite, binary format | Human-readable, debuggable, trivial to parse. No external dependencies. |
| Atomic rewrite for checkpoint | In-place truncation | Crash-safe. If crash during rewrite, old WAL is intact. |
