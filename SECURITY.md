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

~~**SAST-1 — Ruff `S` (security) rules not enabled**~~ *(resolved)*

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

~~**DOCKER-1 — Unpinned `:latest` image tags**~~ *(resolved — `python:3.12-slim` pinned to digest in `Dockerfile`)*

~~**CRYPTO-1 — MD5 used for document ID hashing**~~ *(resolved — replaced with SHA-256)*

**CFG-3 — Default crawl path is `/tmp/es`**
When `fs.url` is not set in `_settings.yaml`, the crawl root defaults to `/tmp/es`
to match Java FSCrawler behaviour. This is a world-writable directory on Linux and
should not be used in production deployments. Users must explicitly set `fs.url`.
Suppressed: `# noqa: S108` at `src/fscrawler/settings.py` (`FsSettings.from_dict`).

**REST-7 — `/_crawler/settings` endpoint dumps entire `fs` config block**
The endpoint comment states "credentials redacted" but serialises the full
`FsConfig` dataclass. If a credential field is ever added to `FsConfig` it will
be silently exposed. An explicit allowlist of safe fields should be used.
Affected: `src/fscrawler/rest_server.py:94-96`.

---

## Trivy Pre-push Hook

[Trivy](https://trivy.dev/) runs automatically before every push to catch known CVEs in
Python dependencies and the filesystem before they reach CI.

`make develop` installs the hook as part of first-time repository setup. To install it
separately:

```bash
make hooks
```

Trivy itself must be installed separately — it is not a Python dependency:

```bash
brew install trivy        # macOS
apt install trivy         # Debian / Ubuntu
```

**What the hook does:**

- **Every push** — runs `trivy fs .` against Python dependencies and the local filesystem.
  Exits non-zero (blocking the push) if any unfixed CRITICAL or HIGH CVE is found.
- **Pushes to `main` or a `v*.*.*` tag** — additionally builds the Docker image and runs
  `trivy image` against it, mirroring the exact CI gate.

If Trivy is not installed the hook skips gracefully with a warning.

To run the filesystem scan on demand:

```bash
make trivy
```

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

Please **do not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities privately via [GitHub's private vulnerability reporting](https://github.com/P6rguVyrst/opensearch-fscrawler/security/advisories/new).

