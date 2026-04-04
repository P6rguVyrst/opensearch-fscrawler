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

**REST-3 — Unvalidated `index` query parameter**
The `index` query parameter on upload and delete endpoints is passed to
OpenSearch without validation or allowlisting. A caller can write to or delete
from any index, including system indices (`CWE-20`, `CWE-943`).
Affected: `src/fscrawler/rest_server.py:170,223,238`.

**CFG-1 — SSRF via `tika_url`**
The `tika_url` setting (and its `FSCRAWLER_FS_TIKA_URL` env override) is
accepted without URL validation. Every document's raw bytes are forwarded to
this URL. An attacker who controls settings or environment can redirect uploads
to arbitrary internal hosts (`CWE-918`).
Affected: `src/fscrawler/settings.py:274`, `src/fscrawler/parser.py:344`.

---

### MEDIUM

~~**SAST-1 — Ruff `S` (security) rules not enabled**~~ *(resolved)*

~~**REST-2 — Unbounded request body (DoS / OOM)**~~ *(partially resolved —
`rest.max_body_size` now enforced for Content-Length requests)*
Residual: for chunked transfer-encoded requests the full body is read into
memory (`await request.body()`) *before* the size check fires. The OOM damage
is done before the 413 response is sent. Full mitigation requires streaming
size enforcement or an upstream reverse proxy with body size limits.
Affected: `src/fscrawler/rest_server.py:112-114`.

**REST-4 — CORS wildcard, no configurable origin list**
When `rest.enable_cors: true`, `allow_origins=["*"]` is hardcoded. There is no
way to restrict CORS to a known set of origins (`CWE-942`).
Affected: `src/fscrawler/rest_server.py:94-99`.

**REST-5 — Raw exception detail returned in HTTP 500 responses**
Internal exception messages (which may contain file paths or system detail) are
forwarded to the HTTP caller in `detail` fields (`CWE-209`).
Affected: `src/fscrawler/rest_server.py:346`.

**REST-6 — `?debug=true` exposes full document content without authentication**
Any unauthenticated caller can pass `?debug=true` to receive the complete
extracted text and metadata of an uploaded file.
Affected: `src/fscrawler/rest_server.py:361`.

**CFG-2 — `ssl_verification: false` default in `--setup` template**
The generated `_settings.yaml` disables TLS certificate verification, leaving
new deployments silently vulnerable to MITM on the OpenSearch connection
(`CWE-295`).
Affected: `src/fscrawler/cli.py:490`.

**WAL-1 — Unbounded WAL growth (disk exhaustion)**
The WAL `append()` method writes records (including full document payloads)
without any size limit or rotation. If OpenSearch is unreachable for an extended
period during a large crawl, the WAL grows without bound and can exhaust disk
space (`CWE-400`).
Affected: `src/fscrawler/wal.py:36-44`.

---

### LOW

~~**DOCKER-1 — Unpinned `:latest` image tags**~~ *(resolved — `python:3.12-slim` pinned to digest in `Dockerfile`)*

~~**CRYPTO-1 — MD5 used for document ID hashing**~~ *(resolved — replaced with SHA-256)*

~~**CRAWL-1 — Symlink escape: `follow_symlinks=True` allowed traversal outside crawl root**~~ *(resolved — boundary check added via `resolve().is_relative_to()`)*

~~**CRAWL-2 — Non-atomic checkpoint writes (corruption on crash)**~~ *(resolved — `save_checkpoint()` now uses temp + fsync + atomic rename, matching the WAL pattern)*

~~**DOCKER-2 — Dev compose ports bound to `0.0.0.0`**~~ *(resolved — all port
mappings now bind to `127.0.0.1`)*
Network binding uses a two-layer model: the process inside the container binds
`0.0.0.0` (required — `127.0.0.1` inside a container means only reachable from
within the container itself, making the port mapping useless). The
`docker-compose.yml` host-side mapping (e.g. `127.0.0.1:8080:8080`) controls
what is reachable from outside — localhost only. This keeps services accessible
from the developer's browser/curl but not from the wider network. See the
header comment in `docker-compose.yml` and inline comments in `config/` YAML
files for details.

~~**CFG-4 — `--setup` template bound REST to `0.0.0.0`**~~ *(resolved — template
now defaults to `127.0.0.1:8080`)*
The `--setup` template is for native (non-Docker) use where `127.0.0.1` is the
correct safe default. Docker deployments should use their own `_settings.yaml`
with `0.0.0.0` and rely on docker-compose port mappings for access control.

**CFG-3 — Default crawl path is `/tmp/es`**
When `fs.url` is not set in `_settings.yaml`, the crawl root defaults to `/tmp/es`
to match Java FSCrawler behaviour. This is a world-writable directory on Linux and
should not be used in production deployments. Users must explicitly set `fs.url`.
Suppressed: `# noqa: S108` at `src/fscrawler/settings.py` (`FsSettings.from_dict`).

**REST-7 — `/_crawler/settings` endpoint dumps entire `fs` config block**
The endpoint comment states "credentials redacted" but serialises the full
`FsConfig` dataclass. If a credential field is ever added to `FsConfig` it will
be silently exposed. An explicit allowlist of safe fields should be used.
Affected: `src/fscrawler/rest_server.py:137`.

**CI-1 — No dependency vulnerability scan in CI pipeline**
`pip-audit` runs only in `release.yml`, not in `ci.yml`. A vulnerable dependency
can be merged to `main` and only caught at release time. The Trivy pre-push hook
partially compensates but is optional (`CWE-1395`).

**LOG-1 — Credentials in URLs logged at DEBUG level**
If a user embeds credentials in an OpenSearch node URL (e.g.
`https://user:pass@host:9200`), the raw URL is logged before parsing strips
them. Only emitted at DEBUG level (`CWE-532`).
Affected: `src/fscrawler/client.py:68`.

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
