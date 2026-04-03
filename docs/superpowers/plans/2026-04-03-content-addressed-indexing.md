# Content-Addressed Document Indexing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace filename-based document IDs with SHA256 of the virtual path, always compute content checksums, and add optional document version history via a separate history index.

**Architecture:** The `_id` for every indexed document becomes `SHA256(path.virtual)`. Content checksums are always computed (default `checksum: "sha256"`). A new `keep_history` setting copies the previous version to a `{name}_docs_history` index before overwriting, enabling append-only version tracking. The old `filename_as_id` and `content_hash_as_id` settings are removed.

**Tech Stack:** Python 3.12+, opensearch-py, hashlib, dataclasses, pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/fscrawler/settings.py` | Modify | Remove `filename_as_id`/`content_hash_as_id`, add `keep_history`, change `checksum` default, add `index_history` |
| `src/fscrawler/parser.py` | Modify | Simplify checksum logic — always compute SHA256 |
| `src/fscrawler/indexer.py` | Modify | New `_make_id` using virtual path, remove old ID modes, add history support |
| `src/fscrawler/templates.py` | Modify | Add history index template with `superseded_date`/`superseded_by` fields |
| `src/fscrawler/client.py` | Modify | Push history index templates, ensure history index |
| `src/fscrawler/watcher.py` | Modify | Use virtual path for doc ID, support history on delete |
| `src/fscrawler/cli.py` | Modify | Ensure history index on startup, update `_do_setup` template |
| `COMPATIBILITY.md` | Modify | Document breaking changes and new ID strategy |
| `tests/conftest.py` | Modify | Update `make_settings`/`make_document` for new defaults |
| `tests/unit/test_settings.py` | Modify | Tests for new/removed settings |
| `tests/unit/test_indexer.py` | Modify | Tests for new ID generation, history support |
| `tests/unit/test_parser.py` | Modify | Tests for always-on checksum |
| `tests/unit/test_watcher.py` | Modify | Tests for virtual-path ID in watcher |

---

### Task 1: Update Settings — Remove Old ID Modes, Add New Settings

**Files:**
- Modify: `src/fscrawler/settings.py`
- Test: `tests/unit/test_settings.py`

- [ ] **Step 1: Write failing tests for new settings**

Add to `tests/unit/test_settings.py`:

```python
class TestContentAddressedSettings:
    def test_checksum_defaults_to_sha256(self) -> None:
        data = {"name": "myjob", "fs": {"url": "/data"}}
        settings = FsSettings.from_dict(data)
        assert settings.fs.checksum == "sha256"

    def test_keep_history_defaults_to_false(self) -> None:
        data = {"name": "myjob", "fs": {"url": "/data"}}
        settings = FsSettings.from_dict(data)
        assert settings.fs.keep_history is False

    def test_keep_history_parsed_from_yaml(self) -> None:
        data = {"name": "myjob", "fs": {"url": "/data", "keep_history": True}}
        settings = FsSettings.from_dict(data)
        assert settings.fs.keep_history is True

    def test_index_history_defaults_to_name_docs_history(self) -> None:
        data = {"name": "myjob", "fs": {"url": "/data"}}
        settings = FsSettings.from_dict(data)
        assert settings.elasticsearch.index_history == "myjob_docs_history"

    def test_index_history_explicit(self) -> None:
        data = {
            "name": "myjob",
            "fs": {"url": "/data"},
            "elasticsearch": {"index_history": "custom_history"},
        }
        settings = FsSettings.from_dict(data)
        assert settings.elasticsearch.index_history == "custom_history"

    def test_filename_as_id_not_recognized(self) -> None:
        """Old setting should be silently ignored — no attribute on FsConfig."""
        data = {"name": "myjob", "fs": {"url": "/data", "filename_as_id": True}}
        settings = FsSettings.from_dict(data)
        assert not hasattr(settings.fs, "filename_as_id")

    def test_content_hash_as_id_not_recognized(self) -> None:
        """Old setting should be silently ignored — no attribute on FsConfig."""
        data = {"name": "myjob", "fs": {"url": "/data", "content_hash_as_id": True}}
        settings = FsSettings.from_dict(data)
        assert not hasattr(settings.fs, "content_hash_as_id")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_settings.py::TestContentAddressedSettings -v`
Expected: FAIL — `FsConfig` still has old fields, missing new ones

- [ ] **Step 3: Update FsConfig dataclass**

In `src/fscrawler/settings.py`, replace the `FsConfig` dataclass:

```python
@dataclass
class FsConfig:
    """Configuration for the file system source (fs: block)."""

    url: str = ""
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    json_support: bool = False
    xml_support: bool = False
    follow_symlinks: bool = False
    remove_deleted: bool = True
    continue_on_error: bool = False
    ignore_above: int | None = None  # bytes; None = no limit
    index_content: bool = True
    add_filesize: bool = True
    attributes_support: bool = False
    lang_detect: bool = False
    store_source: bool = False
    indexed_chars: int = 100000
    raw_metadata: bool = False
    checksum: str = "sha256"
    index_folders: bool = True
    tika_url: str = "http://localhost:9998"
    keep_history: bool = False
```

Key changes:
- Removed `filename_as_id: bool = True`
- Removed `content_hash_as_id: bool = False`
- Changed `checksum: str | None = None` → `checksum: str = "sha256"`
- Added `keep_history: bool = False`

- [ ] **Step 4: Update ElasticsearchSettings dataclass**

Add `index_history` field to `ElasticsearchSettings`:

```python
@dataclass
class ElasticsearchSettings:
    """Configuration for the Elasticsearch / OpenSearch target."""

    nodes: list[str] = field(default_factory=lambda: ["http://localhost:9200"])
    username: str = ""
    password: str = ""
    api_key: str = ""
    ssl_verification: bool = True
    index: str = ""  # will be set to "{name}_docs" if empty
    index_folder: str = ""  # will be set to "{name}_folder" if empty
    index_history: str = ""  # will be set to "{name}_docs_history" if empty
    bulk_size: int = 100
    byte_size: int = 10 * 1024 * 1024  # 10mb
    push_templates: bool = True

    def __post_init__(self) -> None:
        if self.api_key and (self.username or self.password):
            logger.warning(
                "Both api_key and username/password are set; api_key takes precedence."
            )
```

- [ ] **Step 5: Update `from_dict` parsing**

In the `from_dict` method of `FsSettings`:

Remove these lines from the fs parsing section:
```python
        if "filename_as_id" in fs_data:
            fs.filename_as_id = bool(fs_data["filename_as_id"])
```
```python
        if "content_hash_as_id" in fs_data:
            fs.content_hash_as_id = bool(fs_data["content_hash_as_id"])
```

Add this line after the `checksum` parsing (replace the existing checksum block):
```python
        if "checksum" in fs_data:
            val = fs_data["checksum"]
            fs.checksum = str(val) if val else "sha256"
        if "keep_history" in fs_data:
            fs.keep_history = bool(fs_data["keep_history"])
```

Add `index_history` parsing in the elasticsearch section, after `index_folder`:
```python
        if "index_history" in es_data and es_data["index_history"]:
            es.index_history = es_data["index_history"]
```

Add the `index_history` default after the existing defaults:
```python
        if not es.index_history:
            es.index_history = f"{name}_docs_history"
```

- [ ] **Step 6: Update `_apply_env_to_raw`**

Remove the `FSCRAWLER_FS_CONTENT_HASH_AS_ID` entry from the env var mapping list. Add `FSCRAWLER_FS_KEEP_HISTORY`:

```python
    for env_key, section, field_name in [
        ("FSCRAWLER_ELASTICSEARCH_USERNAME", "elasticsearch", "username"),
        ("FSCRAWLER_ELASTICSEARCH_PASSWORD", "elasticsearch", "password"),
        ("FSCRAWLER_ELASTICSEARCH_API_KEY", "elasticsearch", "api_key"),
        ("FSCRAWLER_ELASTICSEARCH_INDEX", "elasticsearch", "index"),
        ("FSCRAWLER_ELASTICSEARCH_BULK_SIZE", "elasticsearch", "bulk_size"),

        ("FSCRAWLER_ELASTICSEARCH_BYTE_SIZE", "elasticsearch", "byte_size"),
        ("FSCRAWLER_REST_URL", "rest", "url"),
        ("FSCRAWLER_FS_URL", "fs", "url"),
        ("FSCRAWLER_FS_TIKA_URL", "fs", "tika_url"),
    ]:
```

Add a new boolean env var section entry for `FSCRAWLER_FS_KEEP_HISTORY`:

```python
    for env_key, section, field_name in [
        ("FSCRAWLER_ELASTICSEARCH_SSL_VERIFICATION", "elasticsearch", "ssl_verification"),
        ("FSCRAWLER_REST_ENABLE_CORS", "rest", "enable_cors"),
        ("FSCRAWLER_FS_KEEP_HISTORY", "fs", "keep_history"),
    ]:
        if v := env.get(env_key):
            _setdefault_nested(raw, section, field_name, v.lower() not in ("false", "0", "no"))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: All tests PASS (some existing tests that reference `filename_as_id` or `content_hash_as_id` will fail — fix in Step 8)

- [ ] **Step 8: Fix existing settings tests**

In `tests/unit/test_settings.py`, in `TestFsSettingsFromDict`:
- Remove any test that asserts `filename_as_id` behavior
- Update `test_defaults_applied` to check `checksum` default instead

In `tests/conftest.py`, update `sample_settings_dict` fixture:
- Remove `"filename_as_id": True` from the `fs` block
- The `checksum` field value "MD5" is fine — it's still a valid algorithm

- [ ] **Step 9: Run full test suite**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add src/fscrawler/settings.py tests/unit/test_settings.py tests/conftest.py
git commit -m "feat: update settings for content-addressed indexing

Remove filename_as_id and content_hash_as_id settings.
Add keep_history (fs) and index_history (elasticsearch).
Change checksum default from null to sha256."
```

---

### Task 2: Simplify Parser Checksum Logic

**Files:**
- Modify: `src/fscrawler/parser.py`
- Test: `tests/unit/test_parser.py`

- [ ] **Step 1: Write failing test for always-on checksum**

Add to `tests/unit/test_parser.py`:

```python
class TestChecksumAlwaysComputed:
    def test_checksum_computed_with_default_settings(
        self, tmp_path: Path, mock_tika: Any
    ) -> None:
        """With default settings (checksum='sha256'), checksum is always set."""
        import hashlib

        from tests.conftest import make_settings

        settings = make_settings()
        parser = TikaParser(settings, tika_url="http://localhost:9998")

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        doc = parser.parse(test_file)

        expected = hashlib.sha256(b"hello world").hexdigest()
        assert doc.file.checksum == expected

    def test_checksum_uses_configured_algorithm(
        self, tmp_path: Path, mock_tika: Any
    ) -> None:
        """When checksum is set to MD5, use MD5."""
        import hashlib

        from tests.conftest import make_settings

        settings = make_settings(fs={"url": str(tmp_path), "checksum": "MD5"})
        parser = TikaParser(settings, tika_url="http://localhost:9998")

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        doc = parser.parse(test_file)

        expected = hashlib.md5(b"hello world").hexdigest()  # noqa: S324
        assert doc.file.checksum == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_parser.py::TestChecksumAlwaysComputed -v`
Expected: First test FAILs because old default `checksum=None` means no checksum computed

- [ ] **Step 3: Simplify checksum logic in `parse()` method**

In `src/fscrawler/parser.py`, replace lines 122-131 (the checksum block in `parse()`) with:

```python
        algo = fs.checksum.lower().replace("-", "")
        try:
            h = hashlib.new(algo, raw_bytes)
            checksum = h.hexdigest()
        except ValueError:
            logger.warning("Unknown checksum algorithm %r, falling back to sha256", fs.checksum)
            checksum = hashlib.sha256(raw_bytes).hexdigest()
```

Remove the `content_hash_as_id` fallback (lines 130-131):
```python
        # DELETE these lines:
        if fs.content_hash_as_id and checksum is None:
            checksum = hashlib.sha256(raw_bytes).hexdigest()
```

- [ ] **Step 4: Simplify checksum logic in `parse_bytes()` method**

In `src/fscrawler/parser.py`, replace lines 226-234 (the checksum block in `parse_bytes()`) with:

```python
        algo = fs.checksum.lower().replace("-", "")
        try:
            checksum = hashlib.new(algo, data).hexdigest()
        except ValueError:
            logger.warning("Unknown checksum algorithm %r, falling back to sha256", fs.checksum)
            checksum = hashlib.sha256(data).hexdigest()
```

Remove the `content_hash_as_id` fallback:
```python
        # DELETE these lines:
        if fs.content_hash_as_id and checksum is None:
            checksum = hashlib.sha256(data).hexdigest()
```

Also remove `checksum: str | None = None` variable declarations — the checksum is now always assigned.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_parser.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/fscrawler/parser.py tests/unit/test_parser.py
git commit -m "feat: always compute content checksum (default sha256)

Remove content_hash_as_id fallback from parser.
Checksum is now always computed since fs.checksum defaults to 'sha256'.
Unknown algorithms fall back to sha256 with a warning."
```

---

### Task 3: Add History Index Template

**Files:**
- Modify: `src/fscrawler/templates.py`
- Test: `tests/unit/test_pipeline.py` (or new test file if needed)

- [ ] **Step 1: Write failing test for history template**

Add to `tests/unit/test_pipeline.py` (which already tests templates):

```python
from fscrawler.templates import (
    get_component_templates,
    get_index_templates,
    mapping_history_template,
)


class TestHistoryTemplate:
    def test_mapping_history_template_has_superseded_fields(self) -> None:
        template = mapping_history_template()
        props = template["template"]["mappings"]["properties"]
        assert "superseded_date" in props
        assert props["superseded_date"]["type"] == "date"
        assert "superseded_by" in props
        assert props["superseded_by"]["type"] == "keyword"

    def test_get_index_templates_includes_history(self) -> None:
        templates = get_index_templates("test_docs", "test_folder", "test_docs_history")
        names = [name for name, _ in templates]
        assert "fscrawler_test_docs_history_docs_history" in names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline.py::TestHistoryTemplate -v`
Expected: FAIL — `mapping_history_template` doesn't exist, `get_index_templates` has wrong signature

- [ ] **Step 3: Add history template function**

In `src/fscrawler/templates.py`, add after `mapping_attributes_template`:

```python
def mapping_history_template() -> dict[str, Any]:
    """Component template for document history (superseded version tracking)."""
    return {
        "template": {
            "mappings": {
                "properties": {
                    "superseded_date": {
                        "type": "date",
                        "format": "date_optional_time",
                    },
                    "superseded_by": {"type": "keyword"},
                }
            }
        }
    }
```

- [ ] **Step 4: Add history index template builder**

In `src/fscrawler/templates.py`, add after `index_template_folders`:

```python
def index_template_history(index_name: str) -> dict[str, Any]:
    """Composable index template for the document history index."""
    return {
        "index_patterns": [index_name],
        "priority": 500,
        "composed_of": [
            f"fscrawler_{index_name}_alias",
            f"fscrawler_{index_name}_settings_total_fields",
            f"fscrawler_{index_name}_mapping_attributes",
            f"fscrawler_{index_name}_mapping_file",
            f"fscrawler_{index_name}_mapping_path",
            f"fscrawler_{index_name}_mapping_attachment",
            f"fscrawler_{index_name}_mapping_content",
            f"fscrawler_{index_name}_mapping_meta",
            f"fscrawler_{index_name}_mapping_history",
        ],
    }
```

- [ ] **Step 5: Update `get_component_templates` for history**

No change needed — `get_component_templates` already generates all component templates for a given index name. But we need to include the history mapping component template. Update `get_component_templates`:

```python
def get_component_templates(index_name: str, job_name: str) -> list[tuple[str, dict[str, Any]]]:
    """Return a list of (template_name, body) tuples for all component templates."""
    return [
        (f"fscrawler_{index_name}_alias", alias_template(job_name)),
        (f"fscrawler_{index_name}_settings_total_fields", settings_total_fields_template()),
        (f"fscrawler_{index_name}_mapping_file", mapping_file_template()),
        (f"fscrawler_{index_name}_mapping_path", mapping_path_template()),
        (f"fscrawler_{index_name}_mapping_meta", mapping_meta_template()),
        (f"fscrawler_{index_name}_mapping_content", mapping_content_template()),
        (f"fscrawler_{index_name}_mapping_attachment", mapping_attachment_template()),
        (f"fscrawler_{index_name}_mapping_attributes", mapping_attributes_template()),
        (f"fscrawler_{index_name}_mapping_history", mapping_history_template()),
    ]
```

- [ ] **Step 6: Update `get_index_templates` to accept history index**

```python
def get_index_templates(
    docs_index: str, folder_index: str, history_index: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    """Return a list of (template_name, body) tuples for the index templates."""
    templates = [
        (f"fscrawler_{docs_index}_docs", index_template_docs(docs_index)),
        (f"fscrawler_{folder_index}_folders", index_template_folders(folder_index)),
    ]
    if history_index:
        templates.append(
            (f"fscrawler_{history_index}_docs_history", index_template_history(history_index))
        )
    return templates
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/unit/test_pipeline.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/fscrawler/templates.py tests/unit/test_pipeline.py
git commit -m "feat: add history index template with superseded fields

Add mapping_history_template with superseded_date and superseded_by.
Add index_template_history builder.
Update get_index_templates to optionally include history index."
```

---

### Task 4: Update Client to Push History Templates

**Files:**
- Modify: `src/fscrawler/client.py`
- Test: `tests/unit/test_client.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_client.py`:

```python
class TestHistoryTemplates:
    def test_push_templates_includes_history_index(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from tests.conftest import make_settings

        settings = make_settings(
            fs={"url": "/data", "keep_history": True},
        )
        client = FsCrawlerClient(settings)
        client.push_templates()

        # Should have pushed component templates for the history index
        put_calls = mock_opensearch_client.cluster.put_component_template.call_args_list
        template_names = [c.kwargs.get("name") or c.args[0] for c in put_calls]
        history_templates = [n for n in template_names if "history" in n]
        assert len(history_templates) > 0

    def test_push_templates_skips_history_when_keep_history_false(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from tests.conftest import make_settings

        settings = make_settings(fs={"url": "/data"})  # keep_history defaults to False
        client = FsCrawlerClient(settings)
        client.push_templates()

        put_index_calls = mock_opensearch_client.indices.put_index_template.call_args_list
        template_names = [c.kwargs.get("name") or c.args[0] for c in put_index_calls]
        history_templates = [n for n in template_names if "history" in n]
        assert len(history_templates) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_client.py::TestHistoryTemplates -v`
Expected: FAIL

- [ ] **Step 3: Update `push_templates` in client.py**

In `src/fscrawler/client.py`, update the import:
```python
from fscrawler.templates import get_component_templates, get_index_templates
```

Update `push_templates` method:

```python
    def push_templates(self, force: bool = False) -> None:
        """Create component and index templates if push_templates is enabled."""
        if not self._settings.elasticsearch.push_templates:
            logger.debug("push_templates is disabled — skipping.")
            return

        es = self._settings.elasticsearch
        index_name = es.index
        folder_index = es.index_folder
        history_index = es.index_history if self._settings.fs.keep_history else ""

        # Component templates for the docs index
        for name, body in get_component_templates(index_name, self._settings.name):
            self._put_component_template(name, body, force=force)

        # Component templates for the folder index (re-use same set)
        for name, body in get_component_templates(folder_index, self._settings.name):
            self._put_component_template(name, body, force=force)

        # Component templates for the history index
        if history_index:
            for name, body in get_component_templates(history_index, self._settings.name):
                self._put_component_template(name, body, force=force)

        # Index templates
        for name, body in get_index_templates(index_name, folder_index, history_index):
            self._put_index_template(name, body, force=force)
```

- [ ] **Step 4: Add `get_document_source` method to client**

This is needed for the history workflow (reading existing doc before overwriting). Add to `FsCrawlerClient`:

```python
    def get_document_source(self, index: str, doc_id: str) -> dict[str, Any] | None:
        """Retrieve a document's _source by ID, returning None if not found."""
        try:
            result = self._client.get(index=index, id=doc_id)
            return result["_source"]  # type: ignore[no-any-return]
        except Exception as exc:
            if hasattr(exc, "status_code") and exc.status_code == 404:
                return None
            if "not_found" in str(exc).lower():
                return None
            raise
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_client.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/fscrawler/client.py tests/unit/test_client.py
git commit -m "feat: push history index templates when keep_history enabled

Add history index component/index template support.
Add get_document method for reading existing docs (history workflow)."
```

---

### Task 5: Update Indexer — New ID Strategy and History Support

**Files:**
- Modify: `src/fscrawler/indexer.py`
- Test: `tests/unit/test_indexer.py`

- [ ] **Step 1: Write failing tests for new ID strategy**

Replace the entire `TestIndexerDocumentId` class and `TestContentHashAsId` class in `tests/unit/test_indexer.py` with:

```python
class TestIndexerDocumentId:
    def test_id_is_sha256_of_virtual_path(self, mock_opensearch_client: MagicMock) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc = make_document("/data/myfile.txt")
        # make_document sets virtual to "/myfile.txt"
        indexer.add(doc)

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        index_actions = [op for op in body if "index" in op]
        expected_id = hashlib.sha256("/myfile.txt".encode()).hexdigest()
        assert index_actions[0]["index"]["_id"] == expected_id

    def test_same_virtual_path_same_id(self, mock_opensearch_client: MagicMock) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 10})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc1 = make_document("/data/myfile.txt", content="version 1")
        doc2 = make_document("/data/myfile.txt", content="version 2")
        indexer.add(doc1)
        indexer.add(doc2)
        indexer.flush()

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        ids = [body[i]["index"]["_id"] for i in range(0, len(body), 2)]
        assert ids[0] == ids[1]  # same virtual path → same ID

    def test_different_virtual_paths_different_ids(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 10})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc1 = make_document("/data/a.txt")
        doc2 = make_document("/data/b.txt")
        indexer.add(doc1)
        indexer.add(doc2)
        indexer.flush()

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        ids = [body[i]["index"]["_id"] for i in range(0, len(body), 2)]
        assert ids[0] != ids[1]
```

- [ ] **Step 2: Write failing tests for delete with new ID**

```python
class TestIndexerDeleteNewId:
    def test_delete_uses_sha256_of_virtual_path(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        import hashlib

        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 1})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        indexer.delete("/gone.txt")  # virtual path

        call_args = mock_opensearch_client.bulk.call_args
        body = call_args[1].get("body") or call_args[0][0]
        delete_ops = [op for op in body if "delete" in op]
        expected_id = hashlib.sha256("/gone.txt".encode()).hexdigest()
        assert delete_ops[0]["delete"]["_id"] == expected_id
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_indexer.py::TestIndexerDocumentId tests/unit/test_indexer.py::TestIndexerDeleteNewId -v`
Expected: FAIL

- [ ] **Step 4: Update BulkIndexer `__init__`**

In `src/fscrawler/indexer.py`, update `__init__`:

```python
    def __init__(self, client: FsCrawlerClient, settings: FsSettings) -> None:
        self._client = client
        self._settings = settings
        self._buffer: list[dict[str, Any]] = []
        self._buffer_bytes: int = 0
        self._lock = threading.Lock()

        es = settings.elasticsearch
        self._bulk_size = es.bulk_size
        self._byte_limit = es.byte_size

        self._index = es.index
        self._folder_index = es.index_folder
        self._index_history = es.index_history
        self._keep_history = settings.fs.keep_history
```

Remove `self._filename_as_id` and `self._content_hash_as_id`.

- [ ] **Step 5: Update `add` method**

```python
    def add(self, doc: Document) -> None:
        """Add a document to the buffer; flush if threshold is reached."""
        doc_id = self._make_id(doc.path.virtual)
        action = {"index": {"_index": self._index, "_id": doc_id}}
        doc_body = doc.to_dict()

        # Estimate byte size: use actual JSON-serialized size
        estimated = len(json.dumps(doc_body, default=str).encode("utf-8"))

        with self._lock:
            self._buffer.append(action)
            self._buffer.append(doc_body)
            self._buffer_bytes += estimated

            if (
                len(self._buffer) // 2 >= self._bulk_size
                or self._buffer_bytes >= self._byte_limit
            ):
                self._flush_locked()
```

- [ ] **Step 6: Update `delete` method**

```python
    def delete(self, virtual_path: str) -> None:
        """Queue a delete operation for the given virtual path."""
        doc_id = self._make_id(virtual_path)
        action: dict[str, Any] = {"delete": {"_index": self._index, "_id": doc_id}}

        with self._lock:
            self._buffer.append(action)
            if len(self._buffer) >= self._bulk_size:
                self._flush_locked()
```

- [ ] **Step 7: Update `_make_id` method**

```python
    def _make_id(self, virtual_path: str) -> str:
        """Generate a stable document ID from the virtual path."""
        return hashlib.sha256(virtual_path.encode()).hexdigest()
```

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/unit/test_indexer.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add src/fscrawler/indexer.py tests/unit/test_indexer.py
git commit -m "feat: use SHA256 of virtual path as document ID

Replace filename_as_id and content_hash_as_id with stable
virtual-path-based ID generation. Delete now uses virtual path."
```

---

### Task 6: Add History Support to Indexer

**Files:**
- Modify: `src/fscrawler/indexer.py`
- Test: `tests/unit/test_indexer.py`

- [ ] **Step 1: Write failing tests for history**

Add to `tests/unit/test_indexer.py`:

```python
class TestIndexerHistory:
    def _make_history_settings(self) -> Any:
        return make_settings(
            fs={"url": "/data", "keep_history": True},
            elasticsearch={
                "bulk_size": 100,
                "index": "test_docs",
                "index_history": "test_docs_history",
            },
        )

    def test_history_copies_old_version_before_update(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_history_settings()
        client = FsCrawlerClient(settings)

        # Simulate an existing document with a different checksum
        mock_opensearch_client.get.return_value = {
            "_source": {
                "file": {"checksum": "old_hash", "filename": "test.txt"},
                "path": {"virtual": "/test.txt", "real": "/data/test.txt", "root": "/data"},
                "content": "old content",
            }
        }

        indexer = BulkIndexer(client, settings)
        doc = make_document("/data/test.txt", content="new content")
        doc.file.checksum = "new_hash"
        indexer.add(doc)
        indexer.flush()

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        # Should have: history index action, history doc, main index action, main doc
        index_actions = [op for op in body if "index" in op]
        history_actions = [a for a in index_actions if a["index"]["_index"] == "test_docs_history"]
        assert len(history_actions) == 1

    def test_history_skips_when_checksum_unchanged(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_history_settings()
        client = FsCrawlerClient(settings)

        # Existing doc has same checksum
        mock_opensearch_client.get.return_value = {
            "_source": {
                "file": {"checksum": "same_hash"},
                "path": {"virtual": "/test.txt"},
            }
        }

        indexer = BulkIndexer(client, settings)
        doc = make_document("/data/test.txt")
        doc.file.checksum = "same_hash"
        indexer.add(doc)
        indexer.flush()

        # Should still index (update in-place) but no history entry
        body = mock_opensearch_client.bulk.call_args[1]["body"]
        index_actions = [op for op in body if "index" in op]
        history_actions = [a for a in index_actions if a["index"]["_index"] == "test_docs_history"]
        assert len(history_actions) == 0

    def test_history_not_written_when_keep_history_false(
        self, mock_opensearch_client: MagicMock
    ) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = make_settings(elasticsearch={"bulk_size": 100})
        client = FsCrawlerClient(settings)
        indexer = BulkIndexer(client, settings)

        doc = make_document("/data/test.txt")
        doc.file.checksum = "new_hash"
        indexer.add(doc)
        indexer.flush()

        # get should never be called when history is off
        mock_opensearch_client.get.assert_not_called()

    def test_history_on_delete(self, mock_opensearch_client: MagicMock) -> None:
        from fscrawler.client import FsCrawlerClient
        from fscrawler.indexer import BulkIndexer

        settings = self._make_history_settings()
        client = FsCrawlerClient(settings)

        mock_opensearch_client.get.return_value = {
            "_source": {
                "file": {"checksum": "old_hash", "filename": "test.txt"},
                "path": {"virtual": "/test.txt"},
                "content": "old content",
            }
        }

        indexer = BulkIndexer(client, settings)
        indexer.delete("/test.txt")
        indexer.flush()

        body = mock_opensearch_client.bulk.call_args[1]["body"]
        # Should have: history index action, history doc, delete action
        index_actions = [op for op in body if "index" in op]
        history_actions = [a for a in index_actions if a["index"]["_index"] == "test_docs_history"]
        assert len(history_actions) == 1
        delete_actions = [op for op in body if "delete" in op]
        assert len(delete_actions) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_indexer.py::TestIndexerHistory -v`
Expected: FAIL

- [ ] **Step 3: Add history support to `add` method**

Update the `add` method in `BulkIndexer`:

```python
    def add(self, doc: Document) -> None:
        """Add a document to the buffer; flush if threshold is reached."""
        doc_id = self._make_id(doc.path.virtual)
        doc_body = doc.to_dict()

        # History: check for existing doc and archive if content changed
        if self._keep_history:
            self._archive_if_changed(doc_id, doc.file.checksum)

        action = {"index": {"_index": self._index, "_id": doc_id}}

        # Estimate byte size: use actual JSON-serialized size
        estimated = len(json.dumps(doc_body, default=str).encode("utf-8"))

        with self._lock:
            self._buffer.append(action)
            self._buffer.append(doc_body)
            self._buffer_bytes += estimated

            if (
                len(self._buffer) // 2 >= self._bulk_size
                or self._buffer_bytes >= self._byte_limit
            ):
                self._flush_locked()
```

- [ ] **Step 4: Add history support to `delete` method**

```python
    def delete(self, virtual_path: str) -> None:
        """Queue a delete operation for the given virtual path."""
        doc_id = self._make_id(virtual_path)

        # History: archive the deleted document
        if self._keep_history:
            self._archive_if_changed(doc_id, "deleted")

        action: dict[str, Any] = {"delete": {"_index": self._index, "_id": doc_id}}

        with self._lock:
            self._buffer.append(action)
            if len(self._buffer) >= self._bulk_size:
                self._flush_locked()
```

- [ ] **Step 5: Add `_archive_if_changed` helper**

Add to `BulkIndexer`:

```python
    def _archive_if_changed(self, doc_id: str, new_checksum: str | None) -> None:
        """Copy the existing document to the history index if its content has changed."""
        from datetime import UTC, datetime

        try:
            existing = self._client.get_document_source(self._index, doc_id)
        except Exception:
            return  # document doesn't exist yet — nothing to archive

        if existing is None:
            return

        old_checksum = existing.get("file", {}).get("checksum")
        if old_checksum == new_checksum:
            return  # content unchanged — skip

        # Add history metadata
        existing["superseded_date"] = datetime.now(tz=UTC).isoformat()
        existing["superseded_by"] = new_checksum or "deleted"

        # History doc ID: {original_id}_{old_checksum} for uniqueness
        history_id = f"{doc_id}_{old_checksum}" if old_checksum else doc_id
        history_action = {"index": {"_index": self._index_history, "_id": history_id}}
        estimated = len(json.dumps(existing, default=str).encode("utf-8"))

        with self._lock:
            self._buffer.append(history_action)
            self._buffer.append(existing)
            self._buffer_bytes += estimated
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_indexer.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/fscrawler/indexer.py src/fscrawler/client.py tests/unit/test_indexer.py
git commit -m "feat: add document history support to indexer

When keep_history=true, archive existing document to history index
before overwriting. History docs get superseded_date and superseded_by
fields. Deletes also archive before removal."
```

---

### Task 7: Update Watcher for New ID Strategy

**Files:**
- Modify: `src/fscrawler/watcher.py`
- Test: `tests/unit/test_watcher.py`

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_watcher.py`:

```python
class TestWatcherNewId:
    def test_index_uses_virtual_path_based_id(self) -> None:
        import hashlib
        from unittest.mock import MagicMock, patch

        from fscrawler.watcher import FsEventHandler
        from tests.conftest import make_document, make_settings

        settings = make_settings(fs={"url": "/data"})
        mock_client = MagicMock()
        mock_parser = MagicMock()
        mock_state = MagicMock()
        mock_state.paused = False

        doc = make_document("/data/test.txt")
        mock_parser.parse.return_value = doc

        handler = FsEventHandler(settings, mock_client, mock_parser, mock_state)
        handler._index(Path("/data/test.txt"))

        # Verify the doc_id passed to client.index is SHA256 of virtual path
        call_args = mock_client.index.call_args
        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        assert call_args.kwargs.get("doc_id") or call_args[1].get("doc_id") == expected_id

    def test_delete_uses_virtual_path(self) -> None:
        import hashlib
        from unittest.mock import MagicMock

        from fscrawler.watcher import FsEventHandler
        from tests.conftest import make_settings

        settings = make_settings(fs={"url": "/data"})
        mock_client = MagicMock()
        mock_parser = MagicMock()
        mock_state = MagicMock()
        mock_state.paused = False

        handler = FsEventHandler(settings, mock_client, mock_parser, mock_state)
        handler._delete("/data/test.txt")

        expected_id = hashlib.sha256("/test.txt".encode()).hexdigest()
        call_args = mock_client.delete.call_args
        assert call_args.kwargs.get("doc_id") == expected_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_watcher.py::TestWatcherNewId -v`
Expected: FAIL — watcher still uses raw path as doc_id

- [ ] **Step 3: Update watcher `_index` method**

```python
    def _index(self, path: Path) -> None:
        try:
            doc = self._parser.parse(path)
            doc_id = hashlib.sha256(doc.path.virtual.encode()).hexdigest()
            self._client.index(
                doc,
                doc_id=doc_id,
                index=self._settings.elasticsearch.index,
            )
            logger.info("Indexed %s", path)
        except Exception as exc:
            logger.error("Failed to index %s: %s", path, exc, exc_info=True)
```

- [ ] **Step 4: Update watcher `_delete` method**

```python
    def _delete(self, path: str) -> None:
        try:
            root = self._settings.fs.url
            try:
                virtual = "/" + str(Path(path).relative_to(root))
            except ValueError:
                virtual = "/" + Path(path).name
            doc_id = hashlib.sha256(virtual.encode()).hexdigest()
            self._client.delete(
                doc_id=doc_id,
                index=self._settings.elasticsearch.index,
            )
            logger.info("Deleted %s from index", path)
        except Exception as exc:
            logger.error("Failed to delete %s from index: %s", path, exc, exc_info=True)
```

- [ ] **Step 5: Add hashlib import to watcher**

At the top of `src/fscrawler/watcher.py`, add:
```python
import hashlib
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_watcher.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/fscrawler/watcher.py tests/unit/test_watcher.py
git commit -m "feat: watcher uses SHA256 of virtual path as doc ID

Index and delete operations now compute document ID from the
virtual path (relative to crawl root), matching the new indexer strategy."
```

---

### Task 8: Update CLI — History Index and Setup Template

**Files:**
- Modify: `src/fscrawler/cli.py`

- [ ] **Step 1: Update `_run_rest` to ensure history index**

In `src/fscrawler/cli.py`, add after `client.ensure_index(settings.elasticsearch.index_folder)` in `_run_rest`:

```python
    if settings.fs.keep_history:
        client.ensure_index(settings.elasticsearch.index_history)
```

- [ ] **Step 2: Update `_run` to ensure history index**

In `src/fscrawler/cli.py`, add after `client.ensure_index(settings.elasticsearch.index_folder)` in `_run`:

```python
    if settings.fs.keep_history:
        client.ensure_index(settings.elasticsearch.index_history)
```

- [ ] **Step 3: Update `_do_setup` template**

In `src/fscrawler/cli.py`, update the setup template in `_do_setup`:

```python
    template = f"""\
name: "{job_dir.name}"
fs:
  url: "/data"
  includes: []
  excludes: []
  follow_symlinks: false
  remove_deleted: true
  continue_on_error: false
  index_content: true
  add_filesize: true
  index_folders: true
  checksum: "sha256"
  keep_history: false
elasticsearch:
  nodes:
    - url: "http://localhost:9200"
  ssl_verification: false
  bulk_size: 100

  byte_size: "10mb"
  push_templates: true
rest:
  url: "http://0.0.0.0:8080"
  enable_cors: false
"""
```

Key changes: removed `"MD5"` checksum default → `"sha256"`, added `keep_history: false`.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/cli.py
git commit -m "feat: ensure history index on startup, update setup template

Create history index when keep_history is enabled.
Update --setup template with sha256 checksum and keep_history."
```

---

### Task 9: Update `_crawl_once` — Pass Virtual Path to Delete

**Files:**
- Modify: `src/fscrawler/cli.py`

- [ ] **Step 1: Verify current delete call**

In `_crawl_once`, the delete loop currently passes `deleted_path` (a string from the crawler checkpoint) to `indexer.delete()`. The indexer's `delete()` method now expects a virtual path. We need to ensure the crawler's `get_deleted_files()` returns paths that can be converted to virtual paths.

- [ ] **Step 2: Update delete loop in `_crawl_once`**

```python
        for deleted_path in crawler.get_deleted_files():
            try:
                virtual = "/" + str(Path(deleted_path).relative_to(root))
            except ValueError:
                virtual = "/" + Path(deleted_path).name
            indexer.delete(virtual)
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/fscrawler/cli.py
git commit -m "fix: convert deleted paths to virtual paths for new ID strategy

The indexer.delete() now expects virtual paths. Convert absolute
paths from the crawler checkpoint to virtual paths before deleting."
```

---

### Task 10: Update conftest and Fix Remaining Test Breakages

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/unit/test_indexer.py`

- [ ] **Step 1: Update `make_document` in conftest**

Ensure `make_document` produces documents compatible with new ID strategy. The current implementation already sets `path.virtual` — verify it works:

```python
def make_document(path: str = "/data/test.txt", content: str = "hello") -> Document:
    """Create a minimal Document for testing."""
    root = "/data"
    try:
        virtual = "/" + str(Path(path).relative_to(root))
    except ValueError:
        virtual = "/" + Path(path).name
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
        path=PathInfo(real=path, root=root, virtual=virtual),
        meta=Meta(),
    )
```

- [ ] **Step 2: Remove `sample_settings_dict` references to removed fields**

In `tests/conftest.py`, remove `"filename_as_id": True` from `sample_settings_dict` fixture (if still present).

- [ ] **Step 3: Remove `TestContentHashAsId` class**

In `tests/unit/test_indexer.py`, remove the entire `TestContentHashAsId` class — those tests are no longer relevant.

- [ ] **Step 4: Fix the old `TestIndexerDocumentId` tests**

Remove the old `test_id_is_file_path_when_filename_as_id_true` and `test_id_is_hash_when_filename_as_id_false` tests — they are replaced by the new tests from Task 5.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/unit/test_indexer.py
git commit -m "test: clean up tests for content-addressed indexing

Remove tests for removed filename_as_id and content_hash_as_id.
Update make_document to compute virtual path from root."
```

---

### Task 11: Update COMPATIBILITY.md

**Files:**
- Modify: `COMPATIBILITY.md`

- [ ] **Step 1: Update the Document ID modes table**

Replace the "Document ID modes" section (lines 85-96) with:

```markdown
#### Document ID strategy

In version 0.3.0, the document `_id` strategy changed fundamentally:

| Version | `_id` value | Behavior |
|---------|-------------|----------|
| 0.2.x `filename_as_id: true` (old default) | raw file path | Tied to absolute path; breaks on remount |
| 0.2.x `filename_as_id: false` | SHA256 of file path | Stable across remounts |
| 0.2.x `content_hash_as_id: true` | SHA256 of file content | Creates orphans on edits |
| **0.3.x (current)** | **SHA256 of virtual path** | **Stable, path-relative, survives edits** |

The `filename_as_id` and `content_hash_as_id` settings are **removed** in 0.3.0.
If present in `_settings.yaml`, they are silently ignored.

The `_id` is now always `SHA256(path.virtual)` where `path.virtual` is the
relative path from the crawl root (e.g., `/reports/Q1.pdf`).
```

- [ ] **Step 2: Update the fs block table**

Change the `filename_as_id` row to show it's removed:
```markdown
| `filename_as_id` | `false` | ❌ **Removed in 0.3.0** — ID is always SHA256 of virtual path | ⚠️ Setting silently ignored if present |
```

Change the `content_hash_as_id` row:
```markdown
| `content_hash_as_id` | ❌ Not available | ❌ **Removed in 0.3.0** — checksum stored as metadata, not as `_id` | ⚠️ Setting silently ignored if present |
```

Add new rows:
```markdown
| `keep_history` | ❌ Not available | ✅ `boolean`, default `false` | Python addition — copies old version to history index before update |
| `checksum` (default) | `null` (no checksum) | `"sha256"` (always computed) | ⚠️ **Default changed in 0.3.0** — checksum now always stored |
```

- [ ] **Step 3: Update elasticsearch block table**

Add row for `index_history`:
```markdown
| `index_history` | ❌ Not available | ✅ String, auto-derived as `{name}_docs_history` | Python addition — target index for document version history |
```

- [ ] **Step 4: Update Default value differences table**

Add entries:
```markdown
| `fs.filename_as_id` | `false` | ❌ Removed | ⚠️ **Breaking** — setting no longer exists; ID is always SHA256 of virtual path |
| `fs.checksum` | `null` | `"sha256"` | ⚠️ **Breaking** — checksums now always computed and stored |
| `fs.keep_history` | N/A | `false` | New in 0.3.0 |
```

Remove the old `fs.filename_as_id` row that showed different defaults.

- [ ] **Step 5: Add migration section**

Add a new section after "Default value differences":

```markdown
---

## Migration from 0.2.x to 0.3.0

### Breaking changes

1. **Document IDs have changed.** All `_id` values are now SHA256 hashes of the
   virtual path. Existing indices will have old-style IDs. You must reindex.

2. **`filename_as_id` removed.** If set in `_settings.yaml`, it is silently
   ignored. Remove it to avoid confusion.

3. **`content_hash_as_id` removed.** Same — silently ignored. Remove it.

4. **`checksum` default changed** from `null` to `"sha256"`. All documents now
   have a `file.checksum` field. This is a minor performance non-issue since
   file bytes are already read for Tika parsing.

### Migration steps

1. Delete existing indices (or create new ones with different names)
2. Remove `filename_as_id` and `content_hash_as_id` from `_settings.yaml`
3. Run a full crawl to reindex all documents with new IDs
```

- [ ] **Step 6: Update environment variable table**

Remove the `FSCRAWLER_FS_CONTENT_HASH_AS_ID` row. Add:
```markdown
| `FSCRAWLER_FS_KEEP_HISTORY` | ❌ Not available | ✅ Sets `fs.keep_history` — Python addition |
```

- [ ] **Step 7: Commit**

```bash
git add COMPATIBILITY.md
git commit -m "docs: document 0.3.0 breaking changes in COMPATIBILITY.md

Update document ID strategy, removed settings, new settings,
and migration steps for content-addressed indexing."
```

---

### Task 12: Full Test Suite Validation

**Files:** All test files

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest tests/ -v --tb=long`
Expected: All PASS, coverage ≥ 80%

- [ ] **Step 2: Run linting**

Run: `uv run ruff check src/ tests/`
Expected: No errors

- [ ] **Step 3: Run type checking**

Run: `uv run mypy src/fscrawler/`
Expected: No errors (or only pre-existing ones)

- [ ] **Step 4: Run security check**

Run: `uv run bandit -r src/fscrawler/ -c pyproject.toml`
Expected: No high/critical findings

- [ ] **Step 5: Verify no regressions in existing functionality**

Run: `uv run pytest tests/ -v -k "not integration"`
Expected: All PASS

- [ ] **Step 6: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address test/lint issues from content-addressed indexing"
```
