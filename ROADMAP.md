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

## v0.5.0 — Crawling Hardening

Items identified via third-party security review comparing Python rewrite to
Java upstream (dadoonet/fscrawler, 14 years of edge-case handling), plus
bugs discovered by auditing upstream open issues.

### HIGH — Include/exclude patterns must match full virtual path

Python matches `fnmatch` against **filename only**. Java matches against the
full virtual path. This silently breaks upstream configs using path patterns
like `*/*.pdf`, `*/logs/*`, or `**/*.txt`.

**Fix:** match against the virtual path (relative from crawl root). Keep
filename-only as a fast path when the pattern contains no `/`.

**Files:** `src/fscrawler/crawler.py` — `_walk()`, `src/fscrawler/watcher.py`

### HIGH — Large files cause OOM (no streaming/temp-file path)

`parser.parse()` calls `Path.read_bytes()`, loading the entire file into
memory. Java uses temp files for content >64 KB when `checksum` or
`store_source` is enabled. A 2 GB PDF will crash the process.

**Fix:** stream file content through a temp file when size exceeds a threshold
(e.g. 64 KB). Compute checksum via `DigestInputStream`-style wrapper during
the single read pass. Pass the temp file path (or file-like object) to Tika
and clean up after.

**Files:** `src/fscrawler/parser.py` — `parse()`, `parse_bytes()`

### MEDIUM — Clock skew tolerance for mtime comparison

`is_new_or_modified()` uses exact mtime equality. Java subtracts 2 seconds
from the scan start time to tolerate NFS/CIFS clock drift. Files modified
within the skew window are silently missed.

**Fix:** subtract a configurable tolerance (default 2 s) from the comparison
timestamp.

**File:** `src/fscrawler/crawler.py` — `is_new_or_modified()`

### MEDIUM — Symlink cycle detection

Neither Java nor Python detect symlink cycles when `follow_symlinks: true`.
A symlink loop causes infinite directory traversal until the process is killed.

**Fix:** track visited `(dev, inode)` pairs during traversal. Skip directories
already visited and log a warning.

**File:** `src/fscrawler/crawler.py` — `_walk()`, `_walk_dirs()`

### MEDIUM — Unicode normalization (NFC vs NFD)

macOS HFS+ stores filenames in NFD; Linux ext4 stores NFC. The same filename
can produce different checksums, document IDs, and include/exclude mismatches
depending on platform. Java does not handle this either — opportunity to
improve.

**Fix:** normalize all filenames to NFC (`unicodedata.normalize("NFC", name)`)
before virtual path computation, pattern matching, and ID generation.

**Files:** `src/fscrawler/crawler.py`, `src/fscrawler/watcher.py`,
`src/fscrawler/parser.py`

### MEDIUM — Content filters (regex on extracted text)

Java supports `fs.filters` — a list of regex patterns applied to extracted
text. Documents whose content does not match any pattern are skipped (not
indexed). Useful for filtering noise.

**Fix:** after Tika extraction, apply configured regexes to the content field.
Skip indexing if no pattern matches (when filters are configured).

**File:** `src/fscrawler/parser.py` or new filter step in `cli.py`

### LOW — `.fscrawlerignore` sentinel file

Java skips an entire directory subtree if a `.fscrawlerignore` file is present.
This is a simple opt-out mechanism for operators.

**Fix:** check for `.fscrawlerignore` during directory traversal. If found,
skip the directory and all children.

**File:** `src/fscrawler/crawler.py` — `_walk()`, `_walk_dirs()`

### LOW — Default excludes (`~*`)

Java defaults `excludes` to `["*/~*"]` (skip tilde-prefixed temp files).
Python has no default excludes, so temp files from editors (Word, LibreOffice)
get indexed.

**Fix:** set default `excludes` to `["~*"]` (filename-only pattern until
full-path matching lands).

**File:** `src/fscrawler/settings.py` — `FsConfig` defaults

### LOW — Special file type detection (pipes, sockets, devices)

Neither Java nor Python filter named pipes, Unix sockets, or device files.
Reading a named pipe blocks indefinitely; reading a device file can produce
infinite data. Opportunity to improve on upstream.

**Fix:** check `entry.stat().st_mode` with `stat.S_ISFIFO`, `stat.S_ISSOCK`,
`stat.S_ISBLK`, `stat.S_ISCHR`. Skip non-regular files with a warning.

**File:** `src/fscrawler/crawler.py` — `_walk()`

### Upstream bugs to fix and cover with tests

The following bugs are open in the Java upstream and likely affect us too.
Each requires both a fix and a regression test referencing the upstream issue.

#### Missing `on_moved` handler — files moved within crawl tree silently lost

A file moved (renamed) within the crawl root fires a watchdog `MoveEvent`.
Our `FsEventHandler` does not implement `on_moved`, so the event is ignored.
The file disappears from the index (via `on_deleted`) but is never re-indexed
at its new path.

**Upstream:** [dadoonet/fscrawler#1300](https://github.com/dadoonet/fscrawler/issues/1300)
**Test:** `test_watcher.py` — moved file is re-indexed at new virtual path
**File:** `src/fscrawler/watcher.py`

#### `ignore_above` drops file entirely instead of indexing metadata

When a file exceeds `ignore_above`, the entire file is skipped. It should
index file metadata (path, size, mtime, permissions) without sending content
to Tika.

**Upstream:** [dadoonet/fscrawler#1605](https://github.com/dadoonet/fscrawler/issues/1605)
**Test:** `test_parser.py` / `test_crawler.py` — large file yields document
with `file.*` fields but no `content`
**File:** `src/fscrawler/crawler.py` — size check in `_walk()`

#### Checkpoint does not scale to 1M+ files

The checkpoint dict holds all file paths + mtimes in memory and serializes as
a single JSON blob. For million-file directories this causes OOM or
multi-second serialization pauses.

**Upstream:** [dadoonet/fscrawler#1429](https://github.com/dadoonet/fscrawler/issues/1429)
**Test:** `test_crawler.py` — checkpoint round-trip with large synthetic
file count (memory-bounded)
**File:** `src/fscrawler/crawler.py` — checkpoint storage

#### No observer health-check or restart on crash

If the watchdog observer thread dies (e.g. OS resource exhaustion on a very
large tree), the `while observer.is_alive()` loop exits silently. No error,
no restart, no metric.

**Upstream:** [dadoonet/fscrawler#1093](https://github.com/dadoonet/fscrawler/issues/1093)
**Test:** `test_cli.py` / `test_watcher.py` — observer crash triggers
restart or logged error
**File:** `src/fscrawler/cli.py` — observer loop

#### File permissions stored as raw int, owner/group as UID/GID

Permissions should be stored as octal string (e.g. `"644"`), and owner/group
should be resolved to names via `pwd`/`grp` modules where available.

**Upstream:** [dadoonet/fscrawler#956](https://github.com/dadoonet/fscrawler/issues/956),
[dadoonet/fscrawler#955](https://github.com/dadoonet/fscrawler/issues/955)
**Test:** `test_parser.py` — verify permissions format and owner/group
resolution
**File:** `src/fscrawler/parser.py` — metadata collection

#### REST upload has no max body size (OOM on large uploads)

The REST server reads the entire request body into memory with no size limit.
A multi-gigabyte upload will exhaust RAM. Should enforce a configurable
maximum (e.g. `rest.max_body_size: 100mb`).

**Upstream:** [dadoonet/fscrawler#1709](https://github.com/dadoonet/fscrawler/issues/1709)
**Test:** `test_rest_server.py` — upload exceeding limit returns 413
**File:** `src/fscrawler/rest_server.py`

### Upstream edge cases

#### Large file integer overflow

Ensure all file-size handling uses 64-bit integers. A 500 MB+ file should not
cause arithmetic overflow in size comparisons or byte-size formatting.

**Upstream:** [dadoonet/fscrawler#566](https://github.com/dadoonet/fscrawler/issues/566),
[dadoonet/fscrawler#890](https://github.com/dadoonet/fscrawler/issues/890)
**File:** `src/fscrawler/crawler.py`, `src/fscrawler/parser.py`

#### Alpine container silent Tika failures

Minimal Alpine-based Docker images may lack font/library dependencies that
Tika needs for certain document types. Extraction fails silently (empty
content). Validate Tika extraction of common formats in CI against our
Docker image.

**Upstream:** [dadoonet/fscrawler#942](https://github.com/dadoonet/fscrawler/issues/942)
**File:** `Dockerfile`, integration tests

#### Mapping type conflicts across document types

Different file types can produce conflicting metadata field types (e.g. a
string `author` from one file vs a list from another). Validate index
templates handle all Tika metadata variations without mapping exceptions.

**Upstream:** [dadoonet/fscrawler#904](https://github.com/dadoonet/fscrawler/issues/904)
**File:** `src/fscrawler/_templates/`

#### Whitespace normalization in extracted content

Tika-extracted content contains raw `\n`, `\t`, and excessive whitespace.
Add optional content normalization (collapse runs of whitespace, strip
leading/trailing) as a configurable post-extraction step.

**Upstream:** [dadoonet/fscrawler#802](https://github.com/dadoonet/fscrawler/issues/802)
**File:** `src/fscrawler/parser.py`

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
