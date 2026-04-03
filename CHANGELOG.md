# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
- Pin Docker base image (`python:3.12-slim`) to digest hash to mitigate CVE-2026-0861
