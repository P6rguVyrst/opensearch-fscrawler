# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-04-04

### Added
- **Full virtual path matching:** Include/exclude patterns containing `/` now match against the full virtual path instead of filename only, fixing silent misses with upstream configs using path patterns. ([upstream context](https://github.com/dadoonet/fscrawler/issues/1300))
- **Default excludes:** Tilde-prefixed editor temp files (`~*`) are now excluded by default, matching Java upstream behavior.
- **`.fscrawlerignore` sentinel:** Directories containing a `.fscrawlerignore` file are skipped during crawl, including all subdirectories.
- **Symlink cycle detection:** Crawl tracks visited `(dev, inode)` pairs to detect and break symlink loops when `follow_symlinks` is enabled.
- **Clock skew tolerance:** `is_new_or_modified()` applies a configurable tolerance (default 2 s) for NFS/CIFS clock drift via `fs.clock_skew_seconds`, preventing silently missed files.
- **Special file detection:** Named pipes, Unix sockets, and device files are detected and skipped with a warning instead of blocking the crawl.
- **Unicode NFC normalization:** Filenames are normalized to NFC before virtual path computation, pattern matching, and document ID generation for cross-platform consistency (macOS HFS+ NFD vs Linux ext4 NFC).
- **`on_moved` handler:** Files moved/renamed within the crawl tree are now properly re-indexed at the new path instead of being silently lost. ([dadoonet/fscrawler#1300](https://github.com/dadoonet/fscrawler/issues/1300))
- **Observer health-check:** Watchdog observer crashes are detected and the observer is restarted up to 5 times with logging, instead of silently exiting. ([dadoonet/fscrawler#1093](https://github.com/dadoonet/fscrawler/issues/1093))
- **Metadata-only indexing for large files:** Files exceeding `ignore_above` now have path, size, and timestamp metadata indexed without Tika content extraction, instead of being silently dropped. ([dadoonet/fscrawler#1605](https://github.com/dadoonet/fscrawler/issues/1605))
- **Large file streaming:** Files larger than 64 KB are streamed to Tika with chunked checksum computation, preventing OOM on large files. ([dadoonet/fscrawler#566](https://github.com/dadoonet/fscrawler/issues/566), [dadoonet/fscrawler#890](https://github.com/dadoonet/fscrawler/issues/890))
- **File permissions as octal strings:** Permissions are stored as octal strings (e.g., `"644"`) and owner/group resolved to names via `pwd`/`grp` modules. ([dadoonet/fscrawler#956](https://github.com/dadoonet/fscrawler/issues/956), [dadoonet/fscrawler#955](https://github.com/dadoonet/fscrawler/issues/955))
- **Content whitespace normalization:** New `fs.content_normalize` setting (default `false`) collapses excessive whitespace and blank lines in Tika-extracted content. ([dadoonet/fscrawler#802](https://github.com/dadoonet/fscrawler/issues/802))
- **REST max body size:** New `rest.max_body_size` setting (default 100 MB) rejects oversized uploads with HTTP 413, including chunked transfer-encoded requests without `Content-Length`. ([dadoonet/fscrawler#1709](https://github.com/dadoonet/fscrawler/issues/1709))
- **Template validation tests:** All index/component template JSON files are validated for structural integrity and field type consistency.

### Fixed
- **Permissions mapping type:** Changed `permissions` field in mapping template from `integer` to `keyword` to match the new octal string format. ([dadoonet/fscrawler#904](https://github.com/dadoonet/fscrawler/issues/904))
- **Windows compatibility:** `grp`/`pwd` imports in parser are now conditional — falls back to numeric UID/GID on platforms where these modules are unavailable.
- **Metadata-only attributes:** `parse_metadata_only()` now populates file attributes (permissions, owner, group) when `attributes_support` is enabled, matching the full `parse()` path.
- **Integration test cleanup:** Test indices are now deleted after integration tests complete.

### Security
- **Symlink escape prevention (CWE-59):** When `follow_symlinks: true`, symlinks resolving outside the crawl root are now rejected. Prevents indexing of sensitive files like `/etc/shadow` via crafted symlinks.
- **Atomic checkpoint writes (CWE-669):** `save_checkpoint()` now uses temp file + fsync + atomic rename, matching the WAL pattern. Prevents checkpoint corruption on crash that could force a full re-crawl.
- **`--setup` template bind address (CWE-668):** Generated `_settings.yaml` now defaults `rest.url` to `127.0.0.1:8080` instead of `0.0.0.0:8080`, preventing accidental network exposure of the unauthenticated REST API.
- **Docker compose port binding (CWE-668):** All port mappings in `docker-compose.yml` now bind to `127.0.0.1` — services are accessible from the local machine but not from the wider network.

## [0.4.0] - 2026-04-04

### Added
- **Write-Ahead Log (WAL):** Every document is fsync'd to a local JSONL log before OpenSearch calls, providing crash-recovery durability. On startup, un-checkpointed WAL records are replayed via bulk API.
- **Dead Letter Queue (DLQ):** Failed documents are written to a dedicated `fscrawler_dlq` OpenSearch index with exponential-backoff retry (schedule: 60s, 120s, 240s, 480s, 960s). A background thread drains due records on a configurable interval.
- **Permanent Failure Queue (PFQ):** Non-retryable errors (e.g., `mapper_parsing_exception`) and max-retries-exceeded documents are promoted to `fscrawler_pfq` for human triage.
- **Error classification:** Retryable vs non-retryable error types determine DLQ vs PFQ routing.
- **OpenTelemetry metrics instrumentation** with 6 instruments:
  - `fscrawler.documents.processed` (Counter) — throughput + error rate with `status` and `error.type` attributes
  - `fscrawler.dlq.records` (Counter) — DLQ entries by error class
  - `fscrawler.pfq.records` (Counter) — permanent failures
  - `fscrawler.dlq.retries` (Counter) — retry effectiveness (`success`/`failure` outcome)
  - `fscrawler.wal.records` (Counter) — WAL operations (`append`/`checkpoint`/`recover`)
  - `fscrawler.bulk.duration` (Histogram) — bulk flush latency
- **Prometheus `/metrics` endpoint** on REST server, scrapable by Prometheus and Grafana Alloy
- **OTLP/HTTP metrics push** via `--otel-endpoint` flag for collector-based setups
- DLQ settings: `max_retries`, `retry_interval`, `backoff_multiplier`, `max_backoff`, `check_interval`

### Changed
- **Breaking:** `--log-otel-endpoint` / `FSCRAWLER_LOG_OTEL_ENDPOINT` renamed to `--otel-endpoint` / `FSCRAWLER_OTEL_ENDPOINT`
- Bulk errors now parsed per-item and routed to DLQ (retryable) or PFQ (non-retryable) instead of being logged and dropped

### Upstream Issues Addressed (retroactive)

The following open issues in the Java upstream
([dadoonet/fscrawler](https://github.com/dadoonet/fscrawler)) are resolved by
architectural decisions already present in this release:

- **[#987](https://github.com/dadoonet/fscrawler/issues/987) — Crawl statistics in a monitoring stack:**
  OpenTelemetry metrics (`fscrawler.documents.processed`, `fscrawler.dlq.records`,
  `fscrawler.bulk.duration`, etc.) with Prometheus `/metrics` endpoint and OTLP push.
- **[#868](https://github.com/dadoonet/fscrawler/issues/868) — Monitor progress from logs or terminal:**
  Structured JSON logging with per-document status, OTel log export, and metrics
  instrumentation provide full visibility into crawl progress.
- **[#1824](https://github.com/dadoonet/fscrawler/issues/1824) — Add/document support for OpenSearch:**
  This project is built for OpenSearch from the ground up — native `opensearch-py`
  client, OpenSearch-compatible index templates, and OpenSearch Dashboards integration.
- **[#399](https://github.com/dadoonet/fscrawler/issues/399) / [#943](https://github.com/dadoonet/fscrawler/issues/943) — Filesystem events (inotify/fsevents) instead of polling:**
  Watchdog-based event-driven indexing replaces the Java polling loop. Files are
  indexed on create/modify/delete events in real time via `--loop` mode.
- **[#529](https://github.com/dadoonet/fscrawler/issues/529) — Event-driven architecture with separate workers:**
  Crawling, parsing (Tika HTTP), and indexing (BulkIndexer) run as independent
  components. WAL provides crash-recovery durability between stages.
- **[#813](https://github.com/dadoonet/fscrawler/issues/813) — Load balancer URL for cluster:**
  The `opensearch-py` client natively supports load-balanced and proxied endpoints
  without special configuration.
- **[#331](https://github.com/dadoonet/fscrawler/issues/331) — Test for continue_on_error option:**
  DLQ/PFQ routing with error classification, plus unit tests covering
  `continue_on_error` behavior in crawler, watcher, and indexer.

### Dependencies
- `opentelemetry-api>=1.20`
- `opentelemetry-sdk>=1.20`
- `opentelemetry-exporter-prometheus>=0.50b0`
- `opentelemetry-exporter-otlp-proto-http>=1.20`

## [0.3.0] - 2026-04-03

### Added
- Content-addressed document IDs: `_id` is now SHA256 of the virtual path, replacing filename-based IDs
- Always compute content checksums (default algorithm: `sha256`), stored in `file.checksum`
- Optional document version history via `keep_history: true` — archives superseded documents to a history index with `superseded_date` and `superseded_by` metadata
- Top-level `@timestamp` field in document mapping for OpenSearch Dashboards Discovery compatibility
- Path hierarchy analyzer with `leading_slash` char_filter — strips leading `/` from path tokens so tickers like `AAPL` are searchable without the prefix
- Extract `make_doc_id()` helper to `models.py` — single source of truth for document ID generation
- Contract tests validating `Document`, `FileInfo`, `PathInfo` dataclass schemas
- CLI unit tests covering `--setup`, `--loop`, argument parsing, and error paths
- Multipart upload unit tests for the REST server
- Expanded indexer test coverage (bulk flush thresholds, history writes)

### Fixed
- REST upload endpoint (`POST /_document`) now uses SHA256 hash as document ID instead of raw filename, matching the content-addressed ID strategy
- REST delete endpoint (`DELETE /_document?filename=`) now hashes the filename to match content-addressed document IDs
- Watcher `on_deleted` handler now respects `remove_deleted: false` setting
- Watcher `_index` now guards against `IsADirectoryError` when watchdog misreports directory events as file events
- Integration test coverage threshold overridden (`--no-cov`) to avoid false failures
- Integration test import ordering and fixture cleanup

### Changed
- **Breaking:** Index naming convention changed from `{job}_docs` / `{job}_folder` to `fscrawler_docs_{job}` / `fscrawler_folders_{job}` / `fscrawler_history_{job}`
- **Breaking:** Default document ID strategy changed from filename to SHA256 of virtual path
- **Breaking:** `checksum` setting default changed from `null` to `"sha256"` — checksums are always computed
- **Breaking:** `remove_deleted` default changed from `true` to `false` — deletion is now opt-in
- **Breaking:** Removed `filename_as_id` and `content_hash_as_id` settings
- Index templates refactored: all template bodies moved to `src/fscrawler/_templates/*.json`, eliminating inline JSON from Python code
- Shared component templates created once per cluster instead of duplicated per index (54 → 11 API calls)
- Index templates use wildcard patterns (`fscrawler_docs_*`) — only 3 index templates needed regardless of job count
- `make up` now runs attached with `--build` — always rebuilds the fscrawler image and streams logs to the terminal
- README quick start updated to use `make up` instead of raw `docker compose` commands
- All callers (`crawler.py`, `watcher.py`, `rest_server.py`, `indexer.py`) refactored to use `make_doc_id()` from `models`

### Removed
- `filename_as_id` setting (superseded by content-addressed ID strategy)
- `content_hash_as_id` setting (superseded by content-addressed ID strategy)
- Per-index component template duplication — all indices now share the same 8 component templates

## [0.2.1] - 2026-04-03

### Fixed
- Use JSON-serialized byte size for bulk indexing threshold instead of unreliable `sys.getsizeof` estimate
- Make `CrawlerState.paused` thread-safe via `threading.Event` for compatibility with free-threaded Python (PEP 703)
- Narrow exception handling in `_template_exists` to propagate authentication and network errors instead of silently swallowing them
- Move inline imports out of `_crawl_once` hot loop to avoid repeated module lookups per folder
- Reuse HTTP client in OTLP log handler instead of opening a new TCP connection per log record

### Changed
- Add concrete type hints to `FsEventHandler` constructor replacing `Any` placeholders
- Consolidate duplicate `make_settings` / `make_document` / `load_fixture` test helpers into shared `conftest.py`
- Refactor `Document.to_dict()` to use `dataclasses.asdict()` instead of manually listing every field

### Security
- Pin Apache Tika, OpenSearch, and OpenSearch Dashboards Docker images by SHA256 digest in docker-compose

## [0.2.0] - 2026-04-03

### Changed
- Replace polling-based crawl loop with watchdog event-driven filesystem monitoring
- Remove `update_rate` setting — crawls now trigger on filesystem events instead of fixed intervals
- Upgrade docker-compose OpenSearch from 3.0.0 to 3.5.0 and Dashboards from 2.19.1 to 3.5.0
- Split single `fscrawler` service in docker-compose into three job-specific services (`fscrawler-markdown`, `fscrawler-pdf`, `fscrawler-catchall`)
- Read OpenSearch admin password from environment variable instead of hardcoding it
- Switch Tika healthcheck from `curl` to `wget` (curl not available in Tika image)

### Removed
- **Breaking:** Remove `update_rate` configuration option from `FsConfig` and settings parsing
- Remove `VOLUME` directive from Dockerfile — volumes are now managed by docker-compose

### Fixed
- Reject zero-byte files and uploads in `TikaParser` instead of sending empty payloads to Tika

### Security
- Bump transitive `requests` dependency to >=2.33.0 to fix CVE-2026-25645
- Pin Docker base image (`python:3.12-slim`) to digest hash to mitigate CVE-2026-0861
- Pin `ghcr.io/astral-sh/uv` to digest hash to prevent tag-poisoning attacks (see CVE-2026-33634 / Trivy supply chain incident)
