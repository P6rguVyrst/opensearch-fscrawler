# Content-Addressed Document Indexing — Design Spec

## Problem

The current default document `_id` is the raw file path (e.g., `/data/earnings-updates.md`). This is:

- **Fragile** — tied to the absolute filesystem path
- **Not content-aware** — identical content at different paths produces different documents
- **Ugly** — path strings as IDs are hard to work with in queries

The existing `content_hash_as_id` mode uses content SHA256 as `_id`, but creates orphaned documents when files are edited (new hash = new document, old one is never cleaned up).

## Solution

### Document identity

**`_id`** = SHA256 of the file's **relative path from the crawl root**.

- `reports/Q1.pdf` always produces the same `_id`, regardless of where the crawl root is mounted
- Survives content changes (path doesn't change)
- Survives remounts (relative path is stable)
- Uses the existing `path.virtual` field as the source

### Content tracking

**`file.checksum`** = SHA256 of file content, **always computed**.

- Default `fs.checksum` changes from `null` to `"sha256"`
- Stored as a keyword field in the index mapping
- Used to detect whether a file has actually changed (vs. just touched)

### Version management

New setting: `fs.keep_history` (default: `false`)

**`keep_history: false` (default):**
When a file changes, the document is updated in-place (same `_id`, new checksum). No orphans, no history.

**`keep_history: true`:**
Before updating the current document, the existing version is copied to a separate history index (`{name}_docs_history`) with:
- `superseded_date` — ISO timestamp when this version was replaced
- `superseded_by` — checksum of the version that replaced it

Then the current document is updated in-place in the main index. The history index is append-only.

### New settings

```yaml
fs:
  checksum: "sha256"            # CHANGED default: was null, now "sha256"
  keep_history: false            # NEW: copy old version to history index before update

elasticsearch:
  index_history: ""              # NEW: auto-derived as "{name}_docs_history" if empty
```

### New/changed indexed fields

Main index (`{name}_docs`) — no new fields. Existing `file.checksum` now always populated.

History index (`{name}_docs_history`) — same mapping as main index plus:

```
superseded_date    — date    — when this version was replaced
superseded_by      — keyword — checksum of the replacing version
```

### ID generation logic

The `_make_id` method changes from:

```python
# Old:
def _make_id(self, file_path: str) -> str:
    if self._filename_as_id:
        return file_path
    return hashlib.sha256(file_path.encode()).hexdigest()
```

To:

```python
# New:
def _make_id(self, virtual_path: str) -> str:
    return hashlib.sha256(virtual_path.encode()).hexdigest()
```

The `virtual_path` is the relative path from the crawl root (e.g., `reports/Q1.pdf`), already computed as `path.virtual` in the Document model.

### History workflow (when `keep_history: true`)

1. Indexer receives a document with `_id = sha256(virtual_path)`
2. Before indexing, queries the main index for the existing document at that `_id`
3. If it exists and `file.checksum` differs from the new document's checksum:
   a. Reads the existing document
   b. Adds `superseded_date` and `superseded_by` fields
   c. Indexes it into `{name}_docs_history` with `_id = {original_id}_{old_checksum}` (unique per version)
4. Indexes the new document into the main index (overwrites the old version)

If the existing document has the same checksum, skip (no actual change).

### Delete handling

When a file is deleted:
- **`keep_history: false`**: delete from main index (same as today)
- **`keep_history: true`**: copy to history index with `superseded_date` and `superseded_by: "deleted"`, then delete from main index

## Breaking changes

| Change | Migration impact |
|--------|-----------------|
| `filename_as_id` default `true` → `false` | `_id` values change from raw paths to SHA256 hashes. Existing indices will have old-style IDs. Users must reindex. |
| `content_hash_as_id` removed | Setting is no longer recognized. Users using it should switch to the new default behavior. |
| `checksum` default `null` → `"sha256"` | Checksums now always computed and stored. Minor performance impact (negligible — file bytes are already read for Tika). |
| `_id` generation changed | All document IDs change. This is a full reindex event. |

### Migration path

Users upgrading from 0.2.x:
1. Delete existing indices (or create new ones with different names)
2. Remove `filename_as_id` and `content_hash_as_id` from `_settings.yaml`
3. Run a full crawl to reindex all documents with new IDs

## Scope

### In scope
- Change `_make_id` to use SHA256 of virtual path
- Always compute SHA256 checksum
- Add `keep_history` setting and history index support
- Add `index_history` setting to ElasticsearchSettings
- Create history index template
- Update COMPATIBILITY.md with breaking changes
- Update tests

### Out of scope
- History index cleanup/retention policies (future enhancement)
- REST API endpoints for querying history (use OpenSearch directly)
- Migrating existing indices automatically
