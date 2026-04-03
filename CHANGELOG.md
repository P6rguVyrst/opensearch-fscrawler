# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
