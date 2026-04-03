# Code Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Address 7 Important issues and 9 Suggestions identified during the full codebase review.

**Architecture:** Each task is a focused fix to an existing module with corresponding test updates. No new modules are created. Changes are grouped by dependency — independent fixes first, then fixes that touch shared code.

**Tech Stack:** Python 3.12+, pytest, uv, ruff, mypy

---

### Task 1: Fix unreliable byte-size estimation in BulkIndexer (I-3)

**Files:**
- Modify: `src/fscrawler/indexer.py:70` and `src/fscrawler/indexer.py:87`
- Test: `tests/unit/test_indexer.py`

**Problem:** `sys.getsizeof(str(doc_body))` measures Python object overhead, not serialized JSON size. The `str()` of a dict produces Python repr (single quotes, no JSON encoding), and `sys.getsizeof` adds ~50 bytes of object header. This makes the `byte_size` threshold check unreliable.

- [ ] **Step 1: Write a failing test that proves the current estimate is wrong**

Add to `tests/unit/test_indexer.py`:

```python
class TestByteEstimation:
    def test_byte_size_threshold_triggers_flush_accurately(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        """Verify flush triggers based on actual JSON-serialized size, not Python object size."""
        import json
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        # Set byte_size to 500 bytes — a single document's JSON should be ~300-400 bytes
        settings = make_settings(bulk_size=1000, byte_size=500)
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc = make_document("/data/doc.txt", content="x" * 200)
        doc_json_size = len(json.dumps(doc.to_dict()).encode("utf-8"))

        # Add documents until we expect to exceed 500 bytes
        docs_needed = (500 // doc_json_size) + 1
        for i in range(docs_needed):
            indexer.add(make_document(f"/data/doc{i}.txt", content="x" * 200))

        # Should have flushed by now
        assert mock_opensearch_client.bulk.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_indexer.py::TestByteEstimation::test_byte_size_threshold_triggers_flush_accurately -v`
Expected: FAIL (current estimation inflates sizes due to `sys.getsizeof` overhead)

- [ ] **Step 3: Fix the byte-size estimation**

In `src/fscrawler/indexer.py`, add `import json` at the top, then replace both occurrences of the estimation:

```python
# In add() method (line 70):
estimated = len(json.dumps(doc_body, default=str).encode("utf-8"))

# In add_folder() method (line 87):
estimated = len(json.dumps(doc_body, default=str).encode("utf-8"))
```

Also remove `import sys` from the imports since it's no longer used.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_indexer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `uv run pytest tests/unit -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/fscrawler/indexer.py tests/unit/test_indexer.py
git commit -m "fix: use JSON-serialized size for bulk byte-size threshold estimation"
```

---

### Task 2: Make CrawlerState.paused thread-safe (I-6)

**Files:**
- Modify: `src/fscrawler/rest_server.py:41-49`
- Modify: `src/fscrawler/watcher.py:42,49,56` (read sites)
- Test: `tests/unit/test_rest_server.py`

**Problem:** `CrawlerState.paused` is a bare boolean read from the watchdog thread and written from FastAPI threads. This is not safe on free-threaded Python (PEP 703). `threading.Event` is the correct primitive.

- [ ] **Step 1: Write a test for thread-safe pause/resume**

Add to `tests/unit/test_rest_server.py`:

```python
class TestCrawlerStateThreadSafety:
    def test_pause_sets_event(self) -> None:
        from fscrawler.rest_server import CrawlerState
        state = CrawlerState()
        assert not state.paused
        state.paused = True
        assert state.paused

    def test_resume_clears_event(self) -> None:
        from fscrawler.rest_server import CrawlerState
        state = CrawlerState()
        state.paused = True
        state.paused = False
        assert not state.paused

    def test_paused_is_thread_safe(self) -> None:
        """Verify paused uses threading.Event under the hood."""
        import threading
        from fscrawler.rest_server import CrawlerState
        state = CrawlerState()
        assert hasattr(state, '_paused_event')
        assert isinstance(state._paused_event, threading.Event)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_rest_server.py::TestCrawlerStateThreadSafety -v`
Expected: FAIL (`_paused_event` doesn't exist yet)

- [ ] **Step 3: Refactor CrawlerState to use threading.Event**

Replace the `CrawlerState` class in `src/fscrawler/rest_server.py`:

```python
import threading

class CrawlerState:
    """Mutable state shared between the background crawler thread and REST endpoints."""

    def __init__(self) -> None:
        self._paused_event = threading.Event()
        self.last_checkpoint: str | None = None

    @property
    def paused(self) -> bool:
        return self._paused_event.is_set()

    @paused.setter
    def paused(self, value: bool) -> None:
        if value:
            self._paused_event.set()
        else:
            self._paused_event.clear()

    def clear_checkpoint(self) -> None:
        self.last_checkpoint = None
```

Add `import threading` to the imports at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_rest_server.py -v && uv run pytest tests/unit/test_watcher.py -v`
Expected: ALL PASS (the property interface is backwards-compatible)

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/rest_server.py tests/unit/test_rest_server.py
git commit -m "fix: use threading.Event for CrawlerState.paused for thread safety"
```

---

### Task 3: Add proper type hints to FsEventHandler (I-5)

**Files:**
- Modify: `src/fscrawler/watcher.py:24-29`

**Problem:** Constructor uses `Any` for all parameters, defeating mypy strict mode. Use concrete types with `TYPE_CHECKING` guard to avoid circular imports.

- [ ] **Step 1: Add proper type hints**

Replace the `__init__` method in `src/fscrawler/watcher.py`:

```python
from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEventHandler

if TYPE_CHECKING:
    from fscrawler.client import FsCrawlerClient
    from fscrawler.parser import TikaParser
    from fscrawler.rest_server import CrawlerState
    from fscrawler.settings import FsSettings

logger = logging.getLogger("fscrawler.watcher")


class FsEventHandler(FileSystemEventHandler):
    """Handle filesystem events by indexing or deleting the affected file."""

    def __init__(
        self,
        settings: FsSettings,
        client: FsCrawlerClient,
        parser: TikaParser,
        crawler_state: CrawlerState,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._client = client
        self._parser = parser
        self._crawler_state = crawler_state
```

Remove `from typing import Any` from the imports. Keep the `Any` type hint on the watchdog event methods (`on_created`, `on_modified`, `on_deleted`) since the watchdog library's event types are not always cleanly typed.

- [ ] **Step 2: Run mypy to verify type checking works**

Run: `uv run mypy src/fscrawler/watcher.py --strict`
Expected: No errors (or only pre-existing ones unrelated to this change)

- [ ] **Step 3: Run existing tests**

Run: `uv run pytest tests/unit/test_watcher.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/fscrawler/watcher.py
git commit -m "fix: add concrete type hints to FsEventHandler constructor"
```

---

### Task 4: Move inline imports out of hot loop in _crawl_once (I-4)

**Files:**
- Modify: `src/fscrawler/cli.py:235-247`

**Problem:** `Path`, `FolderDocument`, and `PathInfo` are imported inside the `for folder_path in crawler.scan_folders()` loop on every iteration.

- [ ] **Step 1: Move imports to function scope**

Replace the `_crawl_once` function in `src/fscrawler/cli.py`:

```python
def _crawl_once(
    settings: FsSettings,
    client: FsCrawlerClient,
    parser: TikaParser,
    job_dir: Path,
) -> None:
    """Execute one full crawl pass: scan, index new/modified, delete removed."""
    from fscrawler.crawler import LocalCrawler
    from fscrawler.indexer import BulkIndexer
    from fscrawler.models import FolderDocument, PathInfo

    crawler = LocalCrawler(settings, config_dir=job_dir)
    root = Path(settings.fs.url)

    with BulkIndexer(client, settings) as indexer:
        for folder_path in crawler.scan_folders():
            rel = folder_path.relative_to(root)
            virtual = "/" if str(rel) == "." else "/" + rel.as_posix()
            indexer.add_folder(FolderDocument(path=PathInfo(
                real=str(folder_path),
                root=str(root),
                virtual=virtual,
            )))

        for file_path in crawler.scan():
            if crawler.is_new_or_modified(file_path):
                try:
                    doc = parser.parse(file_path)
                    indexer.add(doc)
                except Exception as exc:
                    if settings.fs.continue_on_error:
                        logger.warning(
                            "Error parsing %s — skipping (continue_on_error=true)",
                            file_path,
                            exc_info=exc,
                        )
                    else:
                        raise

        for deleted_path in crawler.get_deleted_files():
            indexer.delete(deleted_path)

    crawler.save_checkpoint()
```

- [ ] **Step 2: Run tests to verify nothing breaks**

Run: `uv run pytest tests/unit/test_pipeline.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/fscrawler/cli.py
git commit -m "fix: move imports out of scan_folders hot loop in _crawl_once"
```

---

### Task 5: Narrow exception handling in _template_exists (S-1)

**Files:**
- Modify: `src/fscrawler/client.py:151-160`
- Test: `tests/unit/test_client.py`

**Problem:** Bare `except Exception` catches authentication errors, network errors, etc., silently returning `False` and triggering unnecessary template re-creation.

- [ ] **Step 1: Write a test that verifies auth errors propagate**

Add to `tests/unit/test_client.py`:

```python
class TestTemplateExistsErrorHandling:
    def test_auth_error_propagates_from_template_check(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from opensearchpy.exceptions import AuthenticationException
        from fscrawler.client import FsCrawlerClient

        settings = make_settings()
        client = FsCrawlerClient(settings)
        mock_opensearch_client.cluster.get_component_template.side_effect = (
            AuthenticationException(401, "Unauthorized")
        )
        with pytest.raises(AuthenticationException):
            client.push_templates()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_client.py::TestTemplateExistsErrorHandling -v`
Expected: FAIL (auth error swallowed, returns False, tries to put template instead of raising)

- [ ] **Step 3: Narrow the exception catch**

Replace `_template_exists` in `src/fscrawler/client.py`:

```python
def _template_exists(self, name: str, kind: str) -> bool:
    """Return True if a component or index template already exists."""
    try:
        if kind == "component":
            self._client.cluster.get_component_template(name=name)
        else:
            self._client.indices.get_index_template(name=name)
        return True
    except Exception as exc:
        # Only treat "not found" responses as template-missing.
        # Re-raise auth errors, network errors, etc.
        if hasattr(exc, "status_code") and exc.status_code == 404:
            return False
        # opensearch-py raises generic Exception with "resource_not_found" message
        if "resource_not_found" in str(exc).lower() or "index_template_missing" in str(exc).lower():
            return False
        raise
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_client.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/client.py tests/unit/test_client.py
git commit -m "fix: narrow exception handling in _template_exists to propagate auth/network errors"
```

---

### Task 6: Consolidate duplicate test helpers (I-7)

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/unit/test_client.py` (remove duplicate `load_fixture`, `make_settings`)
- Modify: `tests/unit/test_indexer.py` (remove duplicate `make_settings`, `make_document`)
- Modify: `tests/unit/test_watcher.py` (remove duplicate `make_settings`)

**Problem:** `load_fixture()` is defined in `conftest.py` and re-defined in `test_client.py`. `make_settings()` has 5+ slightly different variants across test files.

- [ ] **Step 1: Add shared helpers to conftest.py**

Add to `tests/conftest.py`, below the existing `load_fixture`:

```python
from fscrawler.models import Document, FileInfo, Meta, PathInfo


def make_settings(**overrides: Any) -> Any:
    """Build an FsSettings instance with sensible defaults.

    Accepts top-level keys (fs, elasticsearch, rest) as keyword overrides.
    """
    from fscrawler.settings import FsSettings

    base: dict[str, Any] = {
        "name": "test",
        "fs": {"url": "/data"},
        "elasticsearch": {
            "nodes": [{"url": "http://localhost:9200"}],
            "index": "test_docs",
            "index_folder": "test_folder",
            "bulk_size": 100,
            "byte_size": "10mb",
        },
    }
    base.update(overrides)
    return FsSettings.from_dict(base)


def make_document(path: str = "/data/test.txt", content: str = "hello") -> Document:
    """Create a minimal Document for testing."""
    return Document(
        content=content,
        file=FileInfo(
            filename=Path(path).name,
            extension=Path(path).suffix.lstrip("."),
            content_type="text/plain",
            filesize=len(content),
            indexing_date="2024-01-01T00:00:00Z",
            created=None,
            last_modified="2024-01-01T00:00:00Z",
            last_accessed=None,
            checksum=None,
            url=path,
        ),
        path=PathInfo(real=path, root="/data", virtual="/" + Path(path).name),
        meta=Meta(),
    )
```

- [ ] **Step 2: Remove duplicate helpers from test files**

In `tests/unit/test_client.py`:
- Remove the `DATA_DIR` constant (line 14)
- Remove the `load_fixture` function (lines 17-19)
- Remove the `make_settings` function (lines 27-30)
- Add import: `from tests.conftest import load_fixture, make_settings`

In `tests/unit/test_indexer.py`:
- Remove the `make_settings` function (lines 16-25)
- Remove the `make_document` function (lines 28-45)
- Add import: `from tests.conftest import make_settings, make_document`
- Update `make_settings` calls that pass `bulk_size` etc. as direct kwargs to use the `elasticsearch={"bulk_size": 3}` pattern, since the shared helper takes top-level keys.

In `tests/unit/test_watcher.py`:
- Remove the `make_settings` function (lines 19-22)
- Add import: `from tests.conftest import make_settings`

- [ ] **Step 3: Run all tests**

Run: `uv run pytest tests/unit -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/unit/test_client.py tests/unit/test_indexer.py tests/unit/test_watcher.py
git commit -m "refactor: consolidate duplicate test helpers into conftest.py"
```

---

### Task 7: Pin Tika image in docker-compose.yml (I-1)

**Files:**
- Modify: `docker-compose.yml:51`

**Problem:** Tika uses `apache/tika:latest-full` which is unpinned. This contradicts the careful digest pinning done for the Python base image in the Dockerfile.

- [ ] **Step 1: Look up the current digest for apache/tika:latest-full**

Run: `docker manifest inspect apache/tika:latest-full` or check Docker Hub for the current SHA256 digest. The user should verify the provenance per the supply chain security checklist in CLAUDE.md.

**IMPORTANT:** Do NOT guess the digest. Look it up from Docker Hub or the official Apache Tika repository.

- [ ] **Step 2: Pin the image by digest**

In `docker-compose.yml`, replace:
```yaml
image: apache/tika:latest-full
```
with:
```yaml
image: apache/tika:latest-full@sha256:<VERIFIED_DIGEST>
```

Also consider pinning the OpenSearch and Dashboards images by digest:
```yaml
# opensearch (line 13)
image: opensearchproject/opensearch:3.5.0@sha256:<VERIFIED_DIGEST>

# dashboards (line 39)
image: opensearchproject/opensearch-dashboards:3.5.0@sha256:<VERIFIED_DIGEST>
```

- [ ] **Step 3: Verify docker compose config is valid**

Run: `docker compose config --quiet`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "fix: pin Tika and OpenSearch Docker images by SHA256 digest"
```

---

### Task 8: Add connection reuse to _OtlpHttpHandler (I-2)

**Files:**
- Modify: `src/fscrawler/logging_config.py:147-208`
- Test: `tests/unit/test_logging_config.py`

**Problem:** Each `emit()` call fires a new `httpx.post()`. Under high log volume, this creates a new TCP connection per log record.

- [ ] **Step 1: Write a test for connection reuse**

Add to `tests/unit/test_logging_config.py`:

```python
class TestOtlpHttpHandlerConnectionReuse:
    def test_reuses_http_client_across_emits(self) -> None:
        """Verify the handler uses a persistent httpx.Client, not httpx.post per emit."""
        from unittest.mock import patch, MagicMock
        import logging

        from fscrawler.logging_config import _OtlpHttpHandler

        handler = _OtlpHttpHandler("http://collector:4318")

        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test message", args=(), exc_info=None,
        )

        with patch.object(handler, '_client') as mock_client:
            mock_client.post.return_value = MagicMock(status_code=200)
            handler.emit(record)
            handler.emit(record)
            # Same client instance should be used both times
            assert mock_client.post.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_logging_config.py::TestOtlpHttpHandlerConnectionReuse -v`
Expected: FAIL (no `_client` attribute)

- [ ] **Step 3: Refactor _OtlpHttpHandler to use persistent client**

Replace the `_OtlpHttpHandler` class in `src/fscrawler/logging_config.py`:

```python
class _OtlpHttpHandler(logging.Handler):
    """Sends log records to an OTLP/HTTP endpoint using the JSON encoding.

    Uses a persistent ``httpx.Client`` for connection reuse (HTTP keep-alive).
    Failures are handled by :meth:`logging.Handler.handleError` (prints to
    stderr) so that a broken collector never silences application logs.

    Reference: https://opentelemetry.io/docs/specs/otlp/#otlphttp
    """

    def __init__(self, endpoint: str) -> None:
        super().__init__()
        self._url = endpoint.rstrip("/") + "/v1/logs"
        self._client = httpx.Client(timeout=5)

    def close(self) -> None:
        """Close the underlying HTTP client when the handler is removed."""
        try:
            self._client.close()
        finally:
            super().close()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._send(record)
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def _send(self, record: logging.LogRecord) -> None:
        sev_num, sev_text = _otel_severity(record.levelno)
        ts_ns = int(record.created * 1_000_000_000)

        otel_attrs = [{"key": "logger", "value": {"stringValue": record.name}}]
        for k, v in _exc_attrs(record).items():
            otel_attrs.append({"key": k, "value": {"stringValue": v}})

        payload: dict[str, Any] = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": _SERVICE_NAME},
                            },
                            {
                                "key": "service.version",
                                "value": {"stringValue": __version__},
                            },
                        ]
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": record.name},
                            "logRecords": [
                                {
                                    "timeUnixNano": str(ts_ns),
                                    "severityNumber": sev_num,
                                    "severityText": sev_text,
                                    "body": {"stringValue": record.getMessage()},
                                    "attributes": otel_attrs,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        body = json.dumps(payload).encode()
        self._client.post(self._url, content=body, headers={"Content-Type": "application/json"})
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_logging_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/logging_config.py tests/unit/test_logging_config.py
git commit -m "fix: reuse HTTP client in OTLP handler for connection pooling"
```

---

### Task 9: Use dataclasses.asdict for Document.to_dict (S-5)

**Files:**
- Modify: `src/fscrawler/models.py:92-162`
- Test: `tests/unit/test_parser.py` (existing tests cover Document serialization)

**Problem:** `Document.to_dict()` manually lists every field of `FileInfo` and `Meta`, duplicating the dataclass field definitions. Using `dataclasses.asdict()` with post-filtering is more maintainable.

- [ ] **Step 1: Refactor Document.to_dict**

Replace the `to_dict` method in `src/fscrawler/models.py`:

```python
def to_dict(self) -> dict[str, Any]:
    """Serialise to a dict suitable for OpenSearch indexing."""
    from dataclasses import asdict

    file_dict = {k: v for k, v in asdict(self.file).items() if v is not None}
    if self.content is not None:
        file_dict["indexed_chars"] = len(self.content)

    result: dict[str, Any] = {
        "file": file_dict,
        "path": asdict(self.path),
    }

    meta_dict = {k: v for k, v in asdict(self.meta).items() if v is not None}
    if meta_dict:
        result["meta"] = meta_dict

    if self.content is not None:
        result["content"] = self.content

    if self.attachment is not None:
        import base64
        result["attachment"] = base64.b64encode(self.attachment).decode()

    return result
```

- [ ] **Step 2: Run all tests to verify output is unchanged**

Run: `uv run pytest tests/unit -v`
Expected: ALL PASS (serialization output should be identical)

- [ ] **Step 3: Commit**

```bash
git add src/fscrawler/models.py
git commit -m "refactor: use dataclasses.asdict in Document.to_dict for maintainability"
```

---

### Task 10: Fix misplaced import in integration test (S-6)

**Files:**
- Modify: `tests/integration/test_crawl.py`

**Problem:** `from typing import Any  # noqa: E402` appears at the bottom of the file instead of at the top with other imports.

- [ ] **Step 1: Move the import**

Move `from typing import Any` to the top of `tests/integration/test_crawl.py` with the other imports. Remove the `# noqa: E402` comment.

- [ ] **Step 2: Run linter**

Run: `uv run ruff check tests/integration/test_crawl.py`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_crawl.py
git commit -m "fix: move misplaced import to top of integration test file"
```

---

### Task 11: Clarify docker-compose command argument ordering (S-3)

**Files:**
- Modify: `docker-compose.yml:76,93,110`

**Problem:** The command `["--config_dir", "/home/fscrawler/.fscrawler", "--loop", "markdown"]` looks like `"markdown"` is a value for `--loop`. It works because Click treats the positional arg separately, but it's confusing.

- [ ] **Step 1: Add -- separator for clarity**

In `docker-compose.yml`, update all three fscrawler service commands:

```yaml
# fscrawler-markdown (line 76):
command: ["--config_dir", "/home/fscrawler/.fscrawler", "--loop", "--", "markdown"]

# fscrawler-pdf (line 93):
command: ["--config_dir", "/home/fscrawler/.fscrawler", "--loop", "--", "pdf"]

# fscrawler-catchall (line 110):
command: ["--config_dir", "/home/fscrawler/.fscrawler", "--loop", "--", "catchall"]
```

- [ ] **Step 2: Verify docker compose config is valid**

Run: `docker compose config --quiet`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "fix: clarify CLI argument ordering in docker-compose commands"
```

---

### Task 12: Add coverage pragma for untestable CLI paths (S-9)

**Files:**
- Modify: `src/fscrawler/cli.py`

**Problem:** The `cli.py` module contains orchestration code (daemon threads, uvicorn startup, signal handling) that is difficult to unit test, but the 80% coverage threshold applies globally.

- [ ] **Step 1: Add pragma comments**

Add `# pragma: no cover` to the uvicorn startup line and the `if __name__` block:

In `src/fscrawler/cli.py`:
```python
# Line 181 (uvicorn.run):
    uvicorn.run(app, host=host, port=port, log_config=None)  # pragma: no cover

# Lines 344-345:
if __name__ == "__main__":  # pragma: no cover
    main()
```

- [ ] **Step 2: Run tests with coverage to verify**

Run: `uv run pytest tests/unit -v --cov=fscrawler --cov-report=term-missing`
Expected: ALL PASS, coverage should not count the pragma'd lines

- [ ] **Step 3: Commit**

```bash
git add src/fscrawler/cli.py
git commit -m "refactor: add coverage pragmas for untestable CLI orchestration paths"
```

---

## Issue Tracking

| Issue | Task | Type |
|-------|------|------|
| I-3: Unreliable byte-size estimation | Task 1 | Important |
| I-6: Thread-unsafe CrawlerState.paused | Task 2 | Important |
| I-5: Any type hints on FsEventHandler | Task 3 | Important |
| I-4: Inline imports in hot loop | Task 4 | Important |
| S-1: _template_exists swallows all exceptions | Task 5 | Suggestion |
| I-7: Duplicate test helpers | Task 6 | Important |
| I-1: Unpinned Tika image | Task 7 | Important |
| I-2: OTLP handler new connection per record | Task 8 | Important |
| S-5: Manual field listing in Document.to_dict | Task 9 | Suggestion |
| S-6: Misplaced import in integration test | Task 10 | Suggestion |
| S-3: Confusing CLI arg ordering in compose | Task 11 | Suggestion |
| S-9: Coverage threshold vs untestable CLI | Task 12 | Suggestion |

### Suggestions NOT included in plan (rationale)

| Issue | Why excluded |
|-------|-------------|
| S-2: Repetitive from_dict parsing | Risk of breaking backwards compatibility; current code is clear if verbose |
| S-4: Missing __all__ exports | Nice but not needed for a non-library project |
| S-7: Rate limiting on OTLP handler | Task 8 addresses the main issue (connection reuse); rate limiting is a future enhancement |
| S-8: Extract folder indexing to helper | Minor code organization; not worth the churn for a few lines |
