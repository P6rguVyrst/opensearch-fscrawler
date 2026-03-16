# Supply Chain Security

This document describes the security controls in place across the CI/CD pipeline, and known gaps that remain open.

---

## Controls in place

### Workflow hardening

| Control | Where | What it does |
|---|---|---|
| `permissions: read-all` default | `ci.yml`, `release.yml` | Denies all token permissions by default; jobs only escalate what they actually need |
| Job-scoped permissions | each job | `id-token: write`, `attestations: write`, `packages: write` only where required |
| Actions pinned to commit SHAs | `ci.yml`, `release.yml` | Prevents a compromised action maintainer from silently pushing malicious code to a floating tag (e.g. `@v4`) |
| Dependabot (Actions + pip) | `.github/dependabot.yml` | Opens weekly PRs to update pinned SHAs and Python deps; pins don't silently rot |

### Pre-release gates (all must pass before anything is published)

| Control | Step | What it catches |
|---|---|---|
| Lockfile integrity | `uv lock --check` | `pyproject.toml` and `uv.lock` out of sync — would mean the built package differs from what was tested |
| Security lint | `ruff check --select S` | OWASP-class code patterns: hardcoded secrets, shell injection, unsafe deserialisation |
| SAST | `bandit -r src/` | Deeper static analysis: SQL injection, weak crypto, subprocess misuse |
| Dependency CVE scan | `uv export --frozen --no-dev \| uvx pip-audit -r /dev/stdin` | Known CVEs in production dependencies, scanned against the exact locked set |
| Unit tests | `pytest tests/unit` | Regression gate; build fails if tests do |
| Docker image CVE scan | `trivy` (CRITICAL/HIGH, ignore-unfixed) | OS and Python CVEs baked into the container image before it is pushed |

### Publishing controls

| Control | Where | What it does |
|---|---|---|
| OIDC trusted publishing | `publish-pypi` | No long-lived PyPI API token stored anywhere; short-lived OIDC token issued per run |
| `GITHUB_TOKEN` for GHCR | `publish-docker` | No long-lived registry credential; scoped to the run |
| GitHub Environment (`release`) | `publish-pypi`, `publish-docker` | Both publish jobs require a human to approve via the GitHub UI before they run — even after all automated gates pass |
| SLSA provenance attestation | both publish jobs | Cryptographically links the published artifact to the exact source commit and workflow run; verifiable with `gh attestation verify` |
| SBOM | `publish-docker` | CycloneDX bill of materials attached to the GHCR image; consumers can audit what is inside the image |

---

## Known gaps

These controls are not yet in place. They are documented here so the trade-off is explicit rather than invisible.

### Workflow / repo settings

| Gap | Risk | Remediation |
|---|---|---|
| No branch protection on `main` | Anyone with write access can push directly to `main`, bypassing CI | Enable in *Settings → Branches*: require PR, require status checks, dismiss stale reviews |
| No tag protection rules | Anyone with write access can push a `v*` tag, triggering a release | Enable in *Settings → Tags*: restrict tag creation to maintainers |
| GitHub secret scanning not verified | Secrets committed to the repo may not be detected | Confirm *Settings → Security → Secret scanning* is enabled |

### Scanning gaps

| Gap | Risk | Remediation |
|---|---|---|
| Trivy uses `ignore-unfixed: true` | Known CVEs with no upstream fix are silently skipped and can ship | Acceptable short-term; revisit when the base image (`python:3.12-slim`) accumulates unfixed findings |
| No OpenSSF Scorecard | No automated scoring of the repo's overall security posture | Add `ossf/scorecard-action` on a weekly schedule; publish badge to README |

### Verification gaps

| Gap | Risk | Remediation |
|---|---|---|
| Attestations are generated but not verified on consume | A consumer pulling from GHCR or PyPI has no enforced check | Document `gh attestation verify` usage in README; consider policy enforcement via Sigstore if running in a controlled environment |

---

## Verifying attestations

```bash
# Verify a PyPI sdist or wheel
gh attestation verify dist/opensearch_fscrawler-*.whl --repo P6rguVyrst/opensearch-fscrawler

# Verify a container image pulled from GHCR
gh attestation verify oci://ghcr.io/p6rguvyrst/opensearch-fscrawler:latest --repo P6rguVyrst/opensearch-fscrawler
```
