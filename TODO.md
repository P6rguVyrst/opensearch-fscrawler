# TODO — AGENTS.md Compliance Gaps

Tracked against `/opt/github/fscrawler/_python/AGENTS.md`.

---

## Feature Backlog

### Multi-job support

Currently one container = one job. Two approaches to evaluate:

- **Option A — multiple config files**: Support a `--jobs` flag (or scan all subdirs of `--config_dir`)
  and run each job concurrently (separate threads or processes). Jobs must not share state: each gets
  its own checkpoint file, its own BulkIndexer, and its own watchdog Observer. The current
  `SETTINGS_YAML` env override needs per-job namespacing (e.g. `FSCRAWLER_JOB_<NAME>_*`).

- **Option B — multi-job settings file**: Extend `_settings.yaml` to accept a top-level `jobs:` list
  where each entry is a full job definition. A single fscrawler process fans out to N concurrent
  crawlers. Backwards-compatible: if `jobs:` is absent the existing single-job format is used as-is.

Either approach must ensure: no shared mutable state between jobs, independent error handling (one
job crashing must not kill others), and clear per-job log attributes (`job` field in every OTel record).

---

### Index naming strategy

Current behaviour: index name = job name (e.g. `security_now`). This makes cross-context index
patterns hard to build and doesn't convey hierarchy.

Proposed: support a structured naming convention with optional `namespace` and `tier` config fields:

```
<namespace>.<tier>.<job_name>   →  e.g.  media.podcasts.security_now
```

Rules:
- If neither `namespace` nor `tier` is set, fall back to bare `job_name` (fully backwards-compatible).
- All three components are lowercased and stripped of characters illegal in index names.
- Index pattern `media.podcasts.*` then spans all jobs in that namespace/tier naturally.
- Folder index follows the same convention: `<namespace>.<tier>.<job_name>_folder`.

The `namespace` and `tier` fields should also be emitted as document metadata fields so they are
filterable in dashboards.

---

### OTel-compatible metrics (Prometheus)

Add instrumentation so the running crawler exposes Prometheus-scrapeable metrics via an OTel
metrics pipeline. Required counters/gauges (minimum viable set):

| Metric | Type | Labels |
|--------|------|--------|
| `fscrawler_files_indexed_total` | Counter | `job`, `status` (`ok`/`error`) |
| `fscrawler_files_deleted_total` | Counter | `job` |
| `fscrawler_bulk_flush_total` | Counter | `job` |
| `fscrawler_bulk_flush_bytes_total` | Counter | `job` |
| `fscrawler_crawl_duration_seconds` | Histogram | `job` |
| `fscrawler_index_queue_size` | Gauge | `job` |

Implementation notes:
- Use `opentelemetry-sdk` + `opentelemetry-exporter-prometheus` so the same instrumentation can
  also export to an OTLP collector if one is configured (consistent with existing log pipeline).
- Expose metrics on a separate port (default `9090`, configurable via `rest.metrics_port`).
- Keep metrics instrumentation behind a feature flag (`rest.metrics: true/false`) so it's opt-in
  and doesn't add a hard dependency for users who don't want it.

---

## §1 · Environment & Execution

- [ ] **Dockerfile uses bare `pip install`** (§1: "Never use pip").
  Replace the `pip install` calls in the builder stage with `uv pip install` or
  switch to `uv sync --frozen` so the Docker build is consistent with the local
  `uv`-managed environment.

---

## §2 · Deterministic Builds & Lockfiles

- [ ] **No `uv lock --check` in CI** (§2: "Use `uv lock --check` in CI or before
  large merges").  Add a CI step (GitHub Actions or equivalent) that runs
  `uv lock --check` to catch `pyproject.toml` / `uv.lock` drift before merges.

---

## §3 · Software Security

- [ ] **`ruff` security ruleset (`S`) not enabled** (§3: "Use `ruff` with security
  rules (`S` prefix) enabled").  Add `"S"` to `[tool.ruff.lint] select` in
  `pyproject.toml` and resolve any findings.

- [ ] **`bandit` not installed or run** (§3: "Use `bandit` for deep static
  analysis").  Add `bandit` to `[project.optional-dependencies] dev` in
  `pyproject.toml` and wire it into the lint target in the Makefile (see §4
  below).

---

## §4 · Workflow & Architecture

- [ ] **No `Makefile` exists** (§4: "Prefer the Makefile for operational intent").
  Create a `Makefile` with at minimum the following targets:
  - `make build` — build the package (`uv build`)
  - `make test` — run the full test suite (`uv run pytest`)
  - `make lint` — run `ruff check` + `bandit`
  - `make run` — start fscrawler via `uv run fscrawler`

---

## §5 · Docker & Development

- [ ] **No `docker-compose.yml`** (§5: "Maintain a `docker-compose.yml`
  supporting `linux/amd64` and `linux/arm64`").  Create a compose file that
  brings up at minimum:
  - An OpenSearch single-node instance
  - The fscrawler service (built from the local `Dockerfile`)
  - Correct volume mounts for config and data dirs
  - Network configuration so fscrawler can reach OpenSearch

- [ ] **No local OpenSearch integration target** (§5: "Develop against the local
  OpenSearch instance provided in the compose stack").  Once the compose file
  exists, document the workflow and wire an integration test target into the
  Makefile that sets `OPENSEARCH_URL` from the compose service.

---

## §6 · Logging Noise (observed in Docker run)

- [ ] **opensearch-py emits one WARN per internal urllib3 retry attempt.**
  During `wait_for_cluster`, each of our retry cycles produces 3–4 lines from
  the `opensearch` logger (one per urllib3 connection attempt inside a single
  `client.info()` call).  This floods the log stream.  Options:
  - Raise the `opensearch` logger floor to `ERROR` during the retry loop, then
    restore it after the cluster is reachable.
  - Or suppress at the `urllib3.connectionpool` logger level instead, which is
    the actual source of the repeated lines.

- [ ] **`py.warnings` body contains embedded newlines.**
  `UserWarning` messages captured via `logging.captureWarnings(True)` (e.g.
  opensearch-py's `"When using ssl_context, all other SSL related kwargs are
  ignored"`) arrive with the Python warning format including a trailing
  `\n  warnings.warn(...)\n` snippet.  This multi-line string lands in the
  OTel `body` field.  Some log ingestion pipelines (Loki, OpenSearch ingestion
  pipelines) reject or mis-parse records where `body` is not a single line.
  Fix: strip the warning location suffix in `OtelJsonFormatter` when the record
  comes from the `py.warnings` logger.
