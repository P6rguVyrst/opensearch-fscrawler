# Roadmap

Open work only. Shipped work belongs in `CHANGELOG.md`.

## Near-Term Fixes

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

### Sequence DLQ config policy after runtime outage semantics

DLQ config policy should not be revisited until runtime outage semantics are correct.
Fail-closed or breaking config changes should not ship ahead of fixing the actual
primary-cluster bulk-failure path in `_flush_locked()`.

### Boolean parsing trap in future DLQ config-policy work

The abandoned fail-closed config path on `moar-hardening` parsed
`allow_shared_cluster` with `bool(...)`, which would treat strings like
`"false"` as truthy in direct `from_dict()` usage. If DLQ config-policy work
resumes, use explicit boolean parsing instead of Python truthiness.

**File:** future follow-up in `src/fscrawler/settings.py`

### CI / supply-chain hardening as a separate salvage track

Keep CI and supply-chain improvements on a separate track from DLQ runtime work.
If that work is resumed, it should land as a dedicated hardening branch rather
than being mixed into DLQ runtime or release-policy changes.

## Next Minor Candidates

### Content filters (regex on extracted text)

Java supports `fs.filters` — a list of regex patterns applied to extracted
text. Documents whose content does not match any pattern are skipped (not
indexed). Useful for filtering noise.

**File:** `src/fscrawler/parser.py` or new filter step in `cli.py`

### Checkpoint does not scale to 1M+ files

The checkpoint dict holds all file paths + mtimes in memory and serializes as
a single JSON blob. For million-file directories this causes OOM or
multi-second serialization pauses.

**Upstream:** [dadoonet/fscrawler#1429](https://github.com/dadoonet/fscrawler/issues/1429)
**File:** `src/fscrawler/crawler.py` — checkpoint storage

### Alpine container silent Tika failures

Minimal Alpine-based Docker images may lack font/library dependencies that
Tika needs for certain document types. Extraction fails silently (empty
content). Validate Tika extraction of common formats in CI against our
Docker image.

**Upstream:** [dadoonet/fscrawler#942](https://github.com/dadoonet/fscrawler/issues/942)
**File:** `Dockerfile`, integration tests

### Crawl thread liveness monitoring

The observer health-check monitors the watchdog thread, but a hung Tika call or
stalled indexing pipeline would not be detected. Add a heartbeat or timeout on
individual file processing to detect stuck crawl threads.

**Upstream:** [dadoonet/fscrawler#1093](https://github.com/dadoonet/fscrawler/issues/1093)

### Remote Filesystem Crawling (SFTP)

SFTP support via a filesystem abstraction layer. Plain FTP remains excluded as
insecure.

Key constraints:

- protocol: SFTP only
- auth: password and PEM key support
- host key verification enabled by default
- reuse upstream-style `server:` config where practical
- stream remote files through temp files instead of loading into memory
- keep metadata best-effort and transport-specific

## Longer-Term Backlog

### Parallel crawling / worker concurrency

Tika extraction is the bottleneck for large crawl jobs. Support configurable
worker concurrency for the parse → index pipeline.

**Upstream:** [dadoonet/fscrawler#627](https://github.com/dadoonet/fscrawler/issues/627)

### Configurable change detection timestamp (ctime vs mtime)

NFS/Samba file copies preserve mtime, so `ctime` is the only reliable change
indicator on network filesystems. Add `fs.change_detection: mtime|ctime`
setting (default `mtime` for backwards compatibility).

**Upstream:** [dadoonet/fscrawler#1471](https://github.com/dadoonet/fscrawler/issues/1471)

### Skip hidden files/dotfiles option

Add `fs.skip_hidden: true|false` setting to exclude dotfiles and hidden
directories from crawling.

**Upstream:** [dadoonet/fscrawler#833](https://github.com/dadoonet/fscrawler/issues/833)

### Add server hostname to indexed documents

Include the crawler's hostname in indexed documents for multi-server
deployments.

**Upstream:** [dadoonet/fscrawler#1000](https://github.com/dadoonet/fscrawler/issues/1000)

### Log full filesystem path of failed files in DLQ

DLQ records should include the original filesystem path so operators can
locate and inspect failed files.

**Upstream:** [dadoonet/fscrawler#1253](https://github.com/dadoonet/fscrawler/issues/1253)

### Dry-run / noop mode

Run the crawler without requiring a live OpenSearch backend. Useful for
validating configs, testing Tika extraction, and debugging include/exclude
patterns.

**Upstream:** [dadoonet/fscrawler#1315](https://github.com/dadoonet/fscrawler/issues/1315)

### REST API authentication

Add configurable authentication to the REST API before any production
deployment.

**Upstream:** [dadoonet/fscrawler#2306](https://github.com/dadoonet/fscrawler/issues/2306)

### Upsert instead of overwrite on re-crawl

Crawler currently overwrites documents, destroying user-added metadata. Add an
option to merge crawler fields into existing documents without overwriting
user-enriched fields.

**Upstream:** [dadoonet/fscrawler#1867](https://github.com/dadoonet/fscrawler/issues/1867)

### Per-path/per-pattern tagging

Support custom metadata tags in config that apply to files matching specific
paths or patterns.

**Upstream:** [dadoonet/fscrawler#884](https://github.com/dadoonet/fscrawler/issues/884)

## Deferred Ideas

### Tika integration improvements

The current architecture depends on a separate Apache Tika HTTP server.
Possible future directions include Python-native extraction for common formats,
automatic Tika sidecar management, or better operational defaults and docs.

This is not prioritized right now.
