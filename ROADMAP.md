# Roadmap

Items identified during v0.4.0 release review. Tracked here for follow-up.

## v0.4.1 — Important

### Release DLQ/PFQ writes from the indexer lock

`_flush_locked()` calls `_route_failure()` while holding `self._lock`. DLQ/PFQ writes
make synchronous HTTP requests to OpenSearch, blocking all indexer threads if the cluster
is slow. Collect failed items during the locked section, release the lock, then write to
DLQ/PFQ outside it.

**File:** `src/fscrawler/indexer.py` — `_flush_locked()` / `_route_failure()`

### Route documents to DLQ on bulk-level exceptions

When `client.bulk()` raises (connection refused, timeout), the `except` branch logs the
error but does not route any pending documents to the DLQ. With WAL enabled they are
recovered on restart, but without WAL they are silently lost. Route all `self._pending`
items to DLQ before clearing the buffer.

**File:** `src/fscrawler/indexer.py` — `_flush_locked()` except branch

### Clarify _pending scope with a comment

`succeeded_ids = set(self._pending.keys())` intentionally excludes folder and history
operations (they don't go through WAL and shouldn't count in `documents_processed`).
Add a brief comment so future maintainers understand this is deliberate.

**File:** `src/fscrawler/indexer.py` — `_flush_locked()` line ~246

## Backlog — Suggestions

### Cache DLQ query file at module level

`run_retry_cycle()` reads and parses `dlq_due_records.json` from disk on every invocation.
Load the query once at module level and `copy.deepcopy()` from the cached version.

**File:** `src/fscrawler/dlq.py`

### Add dlq section to --setup template

The `_do_setup` YAML template does not include a `dlq:` section, so users won't discover
DLQ configuration options through `fscrawler --setup`.

**File:** `src/fscrawler/cli.py` — `_do_setup()`

### Document WAL.read() thread-safety precondition

`read()` does not acquire `self._lock`. It is safe in current usage (only called at startup
when no other threads are active) but would be unsafe if called concurrently with `append()`.
Add a note to the docstring.

**File:** `src/fscrawler/wal.py` — `read()`

### Note advisory nature of histogram bucket boundaries

`explicit_bucket_boundaries_advisory` is an advisory hint in the OTel API. The default SDK
view honors it as of OTel SDK 1.20+, but a custom `View` could override it. Worth a code
comment for future reference.

**File:** `src/fscrawler/metrics.py` — `bulk_duration`
