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

## v0.5.0 — Crawling Hardening (shipped)

All items below were completed in v0.5.0 and audited in v0.5.1. Kept here
for traceability — see CHANGELOG.md for details.

- [x] Full virtual path matching for include/exclude patterns
- [x] Large file streaming (64 KB threshold) — [#566](https://github.com/dadoonet/fscrawler/issues/566), [#890](https://github.com/dadoonet/fscrawler/issues/890)
- [x] Clock skew tolerance for mtime comparison
- [x] Symlink cycle detection via `(dev, inode)` tracking
- [x] Unicode NFC normalization for filenames
- [x] `.fscrawlerignore` sentinel file
- [x] Default excludes (`~*`)
- [x] Special file type detection (pipes, sockets, devices)
- [x] `on_moved` handler — [#1300](https://github.com/dadoonet/fscrawler/issues/1300)
- [x] `ignore_above` metadata-only indexing — [#1605](https://github.com/dadoonet/fscrawler/issues/1605)
- [x] Observer health-check and restart — inspired by [#1093](https://github.com/dadoonet/fscrawler/issues/1093) (monitors Observer thread, not crawl thread)
- [x] File permissions as octal strings, owner/group as names — [#956](https://github.com/dadoonet/fscrawler/issues/956), [#955](https://github.com/dadoonet/fscrawler/issues/955)
- [x] REST max body size — inspired by [#1709](https://github.com/dadoonet/fscrawler/issues/1709) (Python-side safeguard, not Jackson fix)
- [x] Large file integer overflow prevention (`long` field types) — [#890](https://github.com/dadoonet/fscrawler/issues/890)
- [x] Mapping type validation and `file.filename` store=true — [#904](https://github.com/dadoonet/fscrawler/issues/904)
- [x] Content whitespace normalization (`fs.content_normalize`) — [#802](https://github.com/dadoonet/fscrawler/issues/802)

### Still open from v0.5.0 scope

#### Content filters (regex on extracted text)

Java supports `fs.filters` — a list of regex patterns applied to extracted
text. Documents whose content does not match any pattern are skipped (not
indexed). Useful for filtering noise.

**File:** `src/fscrawler/parser.py` or new filter step in `cli.py`

#### Checkpoint does not scale to 1M+ files

The checkpoint dict holds all file paths + mtimes in memory and serializes as
a single JSON blob. For million-file directories this causes OOM or
multi-second serialization pauses.

**Upstream:** [dadoonet/fscrawler#1429](https://github.com/dadoonet/fscrawler/issues/1429)
**File:** `src/fscrawler/crawler.py` — checkpoint storage

#### Alpine container silent Tika failures

Minimal Alpine-based Docker images may lack font/library dependencies that
Tika needs for certain document types. Extraction fails silently (empty
content). Validate Tika extraction of common formats in CI against our
Docker image.

**Upstream:** [dadoonet/fscrawler#942](https://github.com/dadoonet/fscrawler/issues/942)
**File:** `Dockerfile`, integration tests

#### Crawl thread liveness monitoring

The observer health-check (v0.5.0) monitors the watchdog thread, but a hung
Tika call or stalled indexing pipeline would not be detected. Add a heartbeat
or timeout on individual file processing to detect stuck crawl threads.

**Upstream:** [dadoonet/fscrawler#1093](https://github.com/dadoonet/fscrawler/issues/1093) (the actual failure mode described in the issue)

## v0.6.0 — Remote Filesystem Crawling (SFTP)

SFTP support via a filesystem abstraction layer. Replaces the Java upstream's
SSH + plain FTP with SFTP-only (encrypted transport, no cleartext credentials).
Plain FTP is intentionally excluded as insecure.

Design and implementation plan to be written separately. Key decisions:

- **Protocol:** SFTP only (not raw SSH exec, not plain FTP, not FTPS)
- **Auth:** password + key-based (PEM). Host key verification **enabled** by
  default (Java disables it — we improve on this)
- **Config compatibility:** reuse `server:` block from upstream configs
  (`hostname`, `port`, `username`, `password`, `pem_path`). Add
  `protocol: sftp` (accept `ssh` as alias for backwards compat)
- **Abstraction:** filesystem provider interface so future protocols (S3,
  WebDAV) can be added without modifying core crawler logic
- **Streaming:** remote files streamed through temp files, not loaded into
  memory (aligns with v0.5.0 large-file fix)
- **Metadata:** best-effort — SFTP provides mtime, size, uid/gid, permissions.
  No creation date, no ACLs.
- **Error handling:** connection retry with exponential backoff, consistent
  with existing OpenSearch client retry pattern

## Future — Tika Integration Improvements

Not prioritized for immediate work. Documenting for roadmap visibility.

### Embedded content extraction (no separate Tika server)

The current architecture requires a separate Apache Tika HTTP server. Java
bundles Tika as a JVM library — no external dependency. Embedding a JVM is
not viable for the Python rewrite.

**Possible approaches (not yet evaluated):**
- Python-native extraction libraries (python-magic + textract, or similar)
  for common formats, with Tika HTTP as fallback for exotic types
- Tika sidecar container auto-management (launch Tika container automatically
  if not already running)
- Improved documentation and docker-compose defaults to reduce friction

**Decision:** deferred. The Tika HTTP architecture is adequate for containerized
deployments. Revisit if user demand warrants.

## Backlog — Features (from upstream issue audit)

Items sourced from open issues in
[dadoonet/fscrawler](https://github.com/dadoonet/fscrawler/issues). Linked
for traceability and upstream community visibility.

### Parallel crawling / worker concurrency

Tika extraction is the bottleneck for large crawl jobs. Support configurable
worker concurrency (thread pool or async workers) for the parse → index
pipeline.

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
deployments. Trivial metadata addition (`file.hostname` field).

**Upstream:** [dadoonet/fscrawler#1000](https://github.com/dadoonet/fscrawler/issues/1000)

### Log full filesystem path of failed files in DLQ

DLQ records should include the original filesystem path (not just the virtual
path and document ID) so operators can locate and inspect failed files.

**Upstream:** [dadoonet/fscrawler#1253](https://github.com/dadoonet/fscrawler/issues/1253)

### Dry-run / noop mode

Run the crawler without requiring a live OpenSearch backend. Useful for
validating configs, testing Tika extraction, and debugging include/exclude
patterns.

**Upstream:** [dadoonet/fscrawler#1315](https://github.com/dadoonet/fscrawler/issues/1315)

### REST API authentication

Already flagged as CRITICAL in `SECURITY.md` (REST-1). Add configurable
authentication to the REST API before any production deployment.

**Upstream:** [dadoonet/fscrawler#2306](https://github.com/dadoonet/fscrawler/issues/2306)

### Upsert instead of overwrite on re-crawl

Crawler currently overwrites documents, destroying user-added metadata (custom
tags, annotations). Add `fs.upsert: true` option to merge crawler fields into
existing documents without overwriting user-enriched fields.

**Upstream:** [dadoonet/fscrawler#1867](https://github.com/dadoonet/fscrawler/issues/1867)

### Per-path/per-pattern tagging

Support custom metadata tags in config that apply to files matching specific
paths or patterns (e.g. tag all files under `/legal/` with
`department: legal`).

**Upstream:** [dadoonet/fscrawler#884](https://github.com/dadoonet/fscrawler/issues/884)

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
