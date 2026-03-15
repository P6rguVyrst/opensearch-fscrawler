# FSCrawler — Python Edition

> **Disclaimer:** This is a prototype intended for local development and experimentation only. It is **not production-ready** and should not be used in production environments.

A Python 3.12 rewrite of [FSCrawler](https://fscrawler.readthedocs.io/), a file system crawler that indexes binary documents (PDF, MS Office, plain text, and more) into OpenSearch or Elasticsearch.

> **Migrating from the Java version?**
> `fs.filename_as_id` defaults to `true` here but `false` in Java. If you are pointing this at an
> existing index, set `fs.filename_as_id: false` in your `_settings.yaml` explicitly — otherwise
> documents will be re-indexed under new IDs and you will end up with duplicates.

## Features

- **Backwards-compatible** `_settings.yaml` format — drop-in replacement for the Java version
- **Event-driven crawling** — watches the filesystem for changes in real time using OS-native events; no polling or checkpoint files required
- **Apache Tika integration** — connects to a running Tika server over HTTP (no bundled JVM)
- **Bulk indexing** — buffers documents and flushes on document count or byte-size thresholds
- **Template management** — creates OpenSearch component and index templates automatically
- **Multi-arch Docker image** — Dockerfile supports linux/amd64 and linux/arm64 (`make build`)

## Quick start

### With Docker Compose

```bash
# Clone and enter the _python directory
cd _python

# Start OpenSearch, Tika, Dashboards, and FSCrawler
docker compose up -d

# Watch the logs
docker compose logs -f fscrawler
```

### Locally (development)

```bash
# Install
pip install -e ".[dev]"

# Create a job config
fscrawler --setup myjob
# Edit ~/.fscrawler/myjob/_settings.yaml

# Run once
fscrawler myjob

# Run continuously
fscrawler --loop myjob
```

## Requirements

- Python 3.12+
- A running [Apache Tika](https://tika.apache.org/) server (`docker run -p 9998:9998 apache/tika:latest-full`)
- A running OpenSearch or Elasticsearch cluster

## Configuration

See [docs/configuration.md](docs/configuration.md) for the full settings reference.

## Development

```bash
make install      # install with dev extras
make test         # run unit tests
make lint         # ruff check
make typecheck    # mypy
make test-all     # unit + integration (needs OPENSEARCH_URL)
```

### Integration tests

```bash
# Start services
docker compose up -d opensearch tika

# Run integration tests
OPENSEARCH_URL=http://localhost:9200 TIKA_URL=http://localhost:9998 make test-integration
```

## Architecture

```
src/fscrawler/
├── cli.py        CLI entry point (Click)
├── settings.py   YAML config loader with duration/byte parsing
├── models.py     Document, FileInfo, PathInfo, Meta dataclasses
├── templates.py  OpenSearch component and index template definitions
├── client.py     opensearch-py wrapper
├── crawler.py    Local filesystem walker with checkpoint tracking
├── parser.py     Apache Tika HTTP client
└── indexer.py    Bulk buffering/flushing processor
```

## Credits

This project (`opensearch-fscrawler`) is a Python rewrite of
**[FSCrawler](https://github.com/dadoonet/fscrawler)**, originally created by
**[David Pilato](https://github.com/dadoonet)** in 2012. The configuration format,
REST API design, crawl workflow, and checkpoint mechanism are all derived from his work.

If you need the full-featured Java version with Elasticsearch/OpenSearch 7–9 support, SSH/FTP
crawling, Apache Tika bundled, and a plugin system, use the original:
https://github.com/dadoonet/fscrawler

## License

Apache License 2.0 — same as the original FSCrawler project.
See [LICENSE](LICENSE) and [NOTICE](NOTICE) for full attribution details.
