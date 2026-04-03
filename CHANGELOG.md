# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-04-03

### Added
- Content-addressed document IDs: `_id` is now SHA256 of the virtual path, replacing filename-based IDs
- Always compute content checksums (default algorithm: `sha256`), stored in `file.checksum`
- Optional document version history via `keep_history: true` — archives superseded documents to `{name}_docs_history` index with `superseded_date` and `superseded_by` metadata
- New `index_history` elasticsearch setting (auto-derived as `{name}_docs_history`)
- History index template with `superseded_date` (date) and `superseded_by` (keyword) fields

### Fixed
- REST upload endpoint (`POST /_document`) now uses SHA256 hash as document ID instead of raw filename, matching the content-addressed ID strategy
- REST delete endpoint (`DELETE /_document?filename=`) now hashes the filename to match content-addressed document IDs
- Watcher `on_deleted` handler now respects `remove_deleted: false` setting, allowing indexed data to persist after source files are removed from the filesystem

### Changed
- **Breaking:** Default document ID strategy changed from filename to SHA256 of virtual path
- **Breaking:** `checksum` setting default changed from `null` to `"sha256"` — checksums are always computed
- **Breaking:** `remove_deleted` default changed from `true` to `false` — deletion is now opt-in, supporting use cases where the filesystem is a transient staging area and the index is the system of record
- **Breaking:** Removed `filename_as_id` and `content_hash_as_id` settings

### Removed
- `filename_as_id` setting (superseded by content-addressed ID strategy)
- `content_hash_as_id` setting (superseded by content-addressed ID strategy)

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
