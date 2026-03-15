# Security

## Scope

This project is a **prototype for local development and experimentation only**.
It is not hardened for production or internet-facing deployments.

The issues below are known, documented, and unresolved.
They exist because hardening them is outside the current scope — not because
they are unknown or considered acceptable for production use.

---

## Known Issues

### CRITICAL

**REST-1 — No authentication on the REST API**
All REST endpoints (document upload, deletion, crawler control, settings) are
unauthenticated. Any process that can reach the server port can index or delete
documents, pause the crawler, or read configuration.
Affected: `src/fscrawler/rest_server.py` — all endpoints.

---

### HIGH

**REST-2 — Unbounded request body (DoS / OOM)**
`POST /_document` and `PUT /_document/{id}` read the entire upload body into
memory without a size cap. A single oversized request can exhaust process memory.
Affected: `src/fscrawler/rest_server.py:265`.

**REST-3 — Unvalidated `index` query parameter**
The `index` query parameter on upload and delete endpoints is passed to
OpenSearch without validation or allowlisting. A caller can write to or delete
from any index, including system indices.
Affected: `src/fscrawler/rest_server.py:129,155`.

**CFG-1 — SSRF via `tika_url`**
The `tika_url` setting (and its `FSCRAWLER_FS_TIKA_URL` env override) is
accepted without URL validation. Every document's raw bytes are forwarded to
this URL. An attacker who controls settings or environment can redirect uploads
to arbitrary internal hosts.
Affected: `src/fscrawler/settings.py:174`, `src/fscrawler/parser.py`.

---

### MEDIUM

**SAST-1 — Ruff `S` (security) rules not enabled**
`pyproject.toml` does not include ruff's `S`-prefix rules, contrary to the
requirement in `AGENTS.md §3`. `bandit` is enabled and wired into the
pre-commit hook; ruff `S` rules are not yet active.
Affected: `pyproject.toml:83`.

**REST-4 — CORS wildcard, no configurable origin list**
When `rest.enable_cors: true`, `allow_origins=["*"]` is hardcoded. There is no
way to restrict CORS to a known set of origins.
Affected: `src/fscrawler/rest_server.py:80-85`.

**REST-5 — Raw exception detail returned in HTTP 500 responses**
Internal exception messages (which may contain file paths or system detail) are
forwarded to the HTTP caller in `detail` fields.
Affected: `src/fscrawler/rest_server.py:304`.

**REST-6 — `?debug=true` exposes full document content without authentication**
Any unauthenticated caller can pass `?debug=true` to receive the complete
extracted text and metadata of an uploaded file.
Affected: `src/fscrawler/rest_server.py:315`.

**CFG-2 — `ssl_verification: false` default in `--setup` template**
The generated `_settings.yaml` disables TLS certificate verification, leaving
new deployments silently vulnerable to MITM on the OpenSearch connection.
Affected: `src/fscrawler/cli.py:321`.

---

### LOW

**DOCKER-1 — Unpinned `:latest` image tags**
`Dockerfile` pulls `ghcr.io/astral-sh/uv:latest` and `docker-compose.yml` uses
`apache/tika:latest-full`. Both should be pinned to a specific version or digest
for reproducible, supply-chain-safe builds.
Affected: `Dockerfile:9`, `docker-compose.yml:53`.

**CRYPTO-1 — MD5 used for document ID hashing**
When `filename_as_id: false`, document IDs are derived with MD5, which is
collision-prone. SHA-256 should be used instead.
Affected: `src/fscrawler/indexer.py:126`.

**REST-7 — `/_crawler/settings` endpoint dumps entire `fs` config block**
The endpoint comment states "credentials redacted" but serialises the full
`FsConfig` dataclass. If a credential field is ever added to `FsConfig` it will
be silently exposed. An explicit allowlist of safe fields should be used.
Affected: `src/fscrawler/rest_server.py:94-96`.

---

## SAST Pre-commit Hook

[bandit](https://bandit.readthedocs.io/) runs automatically before every commit.
The hook compares findings against `.security-baseline.json` and blocks the commit
if any new issues are detected, requiring the committer to explicitly acknowledge them.

`make develop` installs the hook as part of first-time repository setup. To install it
separately:

```bash
make hooks
```

**When a commit is blocked:**

```
Security scan: NEW findings not in baseline:

  [a3f1c2e4b5d6f7a8] B324 (HIGH/HIGH)
    src/fscrawler/indexer.py:42
    Use of weak MD5 hash for security. Consider usedforsecurity=False
    More info: https://bandit.readthedocs.io/en/latest/plugins/b324_hashlib.html

To acknowledge these findings and allow the commit, run:
  uv run python scripts/update_security_baseline.py
Then stage .security-baseline.json and commit again.
```

Fix the issue, or explicitly acknowledge it and carry the record forward:

```bash
# Acknowledge and record
make update-security-baseline
git add .security-baseline.json
git commit ...
```

Each acknowledged entry in `.security-baseline.json` includes the date it was first
accepted. The git history of that file is the audit trail of who acknowledged what and when.

To run the scan outside of a commit:

```bash
make security
```

---

## Reporting

This is a prototype; there is no formal security disclosure process.
Open an issue at <https://github.com/P6rguVyrst/opensearch-fscrawler/issues>
and tag it `security`.
