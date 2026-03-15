# Agent Instructions: Secure Python Development with uv

## 1. Environment & Execution
- **Mandatory uv**: Use `uv` for all management. Never use `pip` or `python -m venv`.
- **Implicit Sync**: Use `uv run <command>` for all executions. This ensures the environment is synced with the lockfile automatically.
- **No Activation**: Do not use `source .venv/bin/activate`. Rely on `uv run` for context.

## 2. Deterministic Builds & Lockfiles
- **uv.lock Priority**: The `uv.lock` file is the source of truth for reproducibility. 
- **Commitment**: Always ensure `uv.lock` is updated and committed to version control.
- **Consistency**: Use `uv lock --check` in CI or before large merges to ensure `pyproject.toml` and `uv.lock` are in sync.

## 3. Software Security (OWASP Top 10 & SANS Top 25)
- **Injection & Shell**: Use parameterized queries. Never use `shell=True` in subprocesses.
- **Secrets**: Never hardcode credentials. Use environment variables via `.env`.
- **SAST Auditing**: Mandatory security scanning before any PR:
    - Use `ruff` with security rules (`S` prefix) enabled.
    - Use `bandit` for deep static analysis of common pitfalls.
- **Deserialization**: Avoid `pickle`; prefer `json` or `pydantic` with strict schemas.

## 4. Workflow & Architecture
- **TDD First**: Rewrite the test suite *before* source code. Use `pytest` and `conftest.py`.
- **Layout**: Source code in `src/`, tests in top-level `tests/`.
- **Mocks**: Store API response mocks as raw `.json` files in `tests/data/`.
- **Automation**: Prefer the **Makefile** for operational intent (build, test, lint, run).

## 5. Testing: Assert on Outputs, Not Mechanics

Unit tests on individual components cannot catch the gap where a feature is
structurally wired (an index is created, a template is pushed, a function is
called) but data never actually flows through it.

**Rule: every output channel must have a pipeline test that asserts data
arrives there after a realistic end-to-end pass.**

An output channel is anything that receives data as a result of the system
running: an OpenSearch index, a file written to disk, a REST response body,
a webhook payload.  If a channel exists but has no test asserting it received
real data, the feature is untested regardless of component-level coverage.

**How to apply:**

1. For each index in the system, write a test that runs `_crawl_once` (or
   the equivalent entry point) against a real temp filesystem with mocked
   external services, then asserts documents landed in that index.
2. Prefer asserting on the *content* of the output (`assert "path" in doc`)
   over asserting on call counts (`assert bulk.called`).
3. A failing pipeline test with a clear message ("No documents were written
   to the folder index") is more valuable than a passing unit test that only
   checks internal wiring.
4. See `tests/unit/test_pipeline.py` for the canonical pattern and the helper
   functions `indices_written()` and `docs_for_index()`.

**The failure mode this catches:** index created ✓, templates pushed ✓,
code path exists ✓ — but nothing ever calls the write method because it was
never wired into the crawl loop.

## 6. API Compatibility Reference
- **Reference Implementation**: https://github.com/dadoonet/fscrawler is the canonical FSCrawler project.
  All REST API endpoints, request/response shapes, and behaviour should be compatible with it.

## 7. Docker & Development
- **Multi-Arch**: Maintain a `Dockerfile` and `docker-compose.yml` supporting `linux/amd64` and `linux/arm64`.
- **Integration**: Develop against the local OpenSearch instance provided in the compose stack.
