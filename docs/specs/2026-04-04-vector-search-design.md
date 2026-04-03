# Vector Search Support — Design Spec

**Date**: 2026-04-04
**Status**: Approved
**Related**: [DLQ + WAL Design](./2026-04-04-dlq-wal-design.md) (separate spec)

## Problem

fscrawler indexes documents ranging from 200 words to thousands of words. For short, semantically dense documents (200-500 words), vector search enables powerful semantic retrieval (RAG, agentic search). But not all jobs need it — vector search adds overhead (`index.knn: true` creates HNSW graph structures, consumes memory) and requires an embedding model + ingest pipeline.

There is no single right pattern. Vector search must be **opt-in per job**.

## Design Principles

- **No overhead unless configured** — base templates unchanged, no knn settings on regular indices
- **Fail fast, fail safe** — missing `dimension` or `pipeline` raises errors immediately, not at runtime
- **Fscrawler does NOT generate embeddings** — separation of concerns. OpenSearch ingest pipeline handles inference server-side
- **Failures are normal** — indexing failures route to DLQ (see separate spec)

## Configuration

New `vector_search` block in `_settings.yaml` under `elasticsearch`:

```yaml
elasticsearch:
  vector_search:
    enabled: true                          # force all docs through vector index
    dimension: 1536                        # required — must match embedding model
    pipeline: "my-embedding-pipeline"      # required — OpenSearch ingest pipeline name
    engine: lucene                         # optional, default: lucene
    space_type: cosinesimil                # optional, default: cosinesimil
    auto_detect: true                      # optional, default: true
```

### Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `enabled` | No | `false` | Force all docs in this job through vector index |
| `dimension` | Yes (when vector active) | — | Must match embedding model output. Range: [1, 16000] |
| `pipeline` | Yes (when vector active) | — | OpenSearch ingest pipeline name for embedding generation |
| `engine` | No | `lucene` | OpenSearch k-NN engine. Valid: `lucene`, `faiss` |
| `space_type` | No | `cosinesimil` | Distance metric. Valid: `l2`, `cosinesimil`, `innerproduct` |
| `auto_detect` | No | `true` | Auto-route `*.vector.*` files to vector index |

### Environment Variables

- `FSCRAWLER_ELASTICSEARCH_VSEARCH_ENABLED`
- `FSCRAWLER_ELASTICSEARCH_VSEARCH_DIMENSION`
- `FSCRAWLER_ELASTICSEARCH_VSEARCH_PIPELINE`

### Validation Rules

- If `enabled: true` and (`dimension == 0` or `pipeline` empty) → raise `FsSettingsError`
- If `auto_detect: true` and `dimension > 0` but `pipeline` empty → raise `FsSettingsError`
- If `dimension > 0` and `pipeline` set but `enabled: false` → valid (auto-detect mode)
- If `engine` not in `{lucene, faiss}` → raise `FsSettingsError` (nmslib deprecated)
- If `space_type` not in `{l2, cosinesimil, innerproduct}` → raise `FsSettingsError`

## Auto-Detect Routing (`*.vector.*`)

Files matching the `*.vector.*` glob pattern (checked on filename only, not full path) are automatically routed to a vector-enabled index. Examples: `profile.vector.txt`, `bio.vector.pdf`, `record.vector.md`.

### Routing Logic

```
if vector_search.enabled:
    all files → VECTOR index
elif vector_search.auto_detect and dimension > 0:
    *.vector.* files → VECTOR index
    everything else → REGULAR index
else:
    all files → REGULAR index
```

### Routing Table

| `enabled` | `auto_detect` | `*.vector.*` file | Other file |
|-----------|---------------|-------------------|------------|
| absent | `true` (default) | vector index | regular index |
| absent | `false` | regular index | regular index |
| `true` | irrelevant | vector index | vector index |

### Error Case

If a `*.vector.*` file is encountered but no `dimension` is configured → write failure to DLQ (see separate spec), index as regular document, log warning:

```
WARNING: File 'profile.vector.txt' matches *.vector.* but no
elasticsearch.vector_search.dimension is configured. Indexing
as regular document. Set dimension to match your embedding model.
```

## Template Architecture

### New Component Templates

**`settings_knn.json`** — enables the k-NN plugin on an index:

```json
{
  "template": {
    "settings": {
      "index.knn": true
    }
  }
}
```

**`mapping_embedding.json`** — defines the `knn_vector` field with placeholder values:

```json
{
  "template": {
    "mappings": {
      "properties": {
        "embedding": {
          "type": "knn_vector",
          "dimension": 0,
          "method": {
            "name": "hnsw",
            "engine": "lucene",
            "space_type": "cosinesimil"
          }
        }
      }
    }
  }
}
```

Both are added to `SHARED_COMPONENTS` and pushed to the cluster on startup. They are inert — `index.knn: true` only takes effect when composed into an index template, and `dimension: 0` is a placeholder patched at runtime.

### Per-Job Index Template

When vector search is active for a job, `templates.py` builds a job-specific index template:

- **Name**: `fscrawler_docs_{jobname}_vector`
- **Pattern**: `fscrawler_docs_{jobname}_vector` (targets only the vector index)
- **Priority**: 600 (overrides base `fscrawler_docs_*` at priority 500)
- **Composed of**: all base components + `fscrawler_settings_knn` + `fscrawler_mapping_embedding`

The `dimension`, `engine`, and `space_type` values from the job config are patched into the loaded `mapping_embedding.json` dict before pushing.

### Template Push Flow

```
client.push_templates():
  1. Push shared component templates (including settings_knn, mapping_embedding)
  2. Push base index templates (fscrawler_docs_*, fscrawler_folders_*, fscrawler_history_*)
  3. If vector_search active (enabled or dimension > 0):
     a. Build job-specific index template via get_vector_index_template()
     b. Patch dimension/engine/space_type into mapping_embedding
     c. Push fscrawler_docs_{jobname} at priority 600
```

## Index Naming

| Index | Purpose |
|-------|---------|
| `fscrawler_docs_{jobname}` | Regular documents (existing) |
| `fscrawler_docs_{jobname}_vector` | Vector-enabled documents (new) |
| `fscrawler_folders_{jobname}` | Folder entries (existing, unchanged) |
| `fscrawler_history_{jobname}` | Document history (existing, unchanged) |

## Indexing Request

Fscrawler sends the same document payload regardless of target index. The embedding field is populated server-side by the OpenSearch ingest pipeline.

**Bulk action for vector documents**:

```json
{"index": {"_index": "fscrawler_docs_myjob_vector", "_id": "sha256hash", "pipeline": "my-embedding-pipeline"}}
{"@timestamp": "2026-04-04T12:00:00Z", "content": "Jane Smith is a senior engineer...", "file": {...}, "path": {...}, "meta": {...}}
```

The `pipeline` parameter is always passed explicitly per action — never rely on `default_pipeline`.

**Bulk action for regular documents** (unchanged):

```json
{"index": {"_index": "fscrawler_docs_myjob", "_id": "sha256hash"}}
{"@timestamp": "2026-04-04T12:00:00Z", "content": "...", "file": {...}, "path": {...}, "meta": {...}}
```

## Settings Wiring

### New Dataclass

```python
@dataclass
class VectorSearchConfig:
    enabled: bool = False
    dimension: int = 0          # 0 = not set
    pipeline: str = ""
    engine: str = "lucene"
    space_type: str = "cosinesimil"
    auto_detect: bool = True
```

Added as a field on `ElasticsearchSettings`:

```python
vector_search: VectorSearchConfig = field(default_factory=VectorSearchConfig)
```

### Parsing in `from_dict`

The `vector_search` sub-dict is parsed from `elasticsearch.vector_search` in the YAML settings, with env var fallback for `FSCRAWLER_ELASTICSEARCH_VSEARCH_*`.

## Crawler Routing

### `crawler.py`

New constant and helper:

```python
VECTOR_FILE_PATTERN = "*.vector.*"

def is_vector_file(path: Path) -> bool:
    """Check if filename matches the *.vector.* convention."""
```

`LocalCrawler` yields files with a routing hint (`IndexTarget.REGULAR` or `IndexTarget.VECTOR`). The decision logic follows the routing table above.

### `indexer.py`

`BulkIndexer` accepts the routing hint per document and targets the correct index. For `VECTOR` targets, the `pipeline` parameter is included in the bulk action.

Bulk operations can mix targets — OpenSearch bulk API supports per-action index targeting.

## Dual-Job Pattern (Documented Example)

For users who want to separate vector and non-vector content from the same directory:

```yaml
# ~/.fscrawler/docs_job/_settings.yaml
name: docs_job
fs:
  url: /data/shared
  excludes:
    - "*.vector.*"
```

```yaml
# ~/.fscrawler/vector_job/_settings.yaml
name: vector_job
fs:
  url: /data/shared
  includes:
    - "*.vector.*"
elasticsearch:
  vector_search:
    enabled: true
    dimension: 1536
    pipeline: "my-embedding-pipeline"
```

## Files Touched

| File | Change |
|------|--------|
| `src/fscrawler/_templates/settings_knn.json` | **New** — `index.knn: true` component |
| `src/fscrawler/_templates/mapping_embedding.json` | **New** — `knn_vector` field component |
| `src/fscrawler/settings.py` | **Modified** — `VectorSearchConfig` dataclass, parsing, env vars |
| `src/fscrawler/templates.py` | **Modified** — `get_vector_index_template()`, updated `SHARED_COMPONENTS` |
| `src/fscrawler/client.py` | **Modified** — conditional vector template push in `push_templates()` |
| `src/fscrawler/crawler.py` | **Modified** — `is_vector_file()`, routing hint on yielded files |
| `src/fscrawler/indexer.py` | **Modified** — route to correct index, pass `pipeline` for vector targets |
| `README.md` | **Modified** — vector search documentation section |

## README Documentation Outline

1. **When to use vector search** — short docs, semantic search, RAG/agentic retrieval
2. **Quick start** — minimal config example
3. **The `*.vector.*` convention** — auto-detect, file naming, disabling
4. **Setting up OpenSearch for vector search**:
   - Deploy embedding model via ML Commons
   - Note model dimension
   - Create ingest pipeline with `text_embedding` processor
   - Configure job with matching `dimension` and `pipeline`
5. **Dual-job pattern** — separate vector/non-vector jobs
6. **Error handling** — cross-reference to DLQ spec

### Reference Links for Users

- [OpenSearch Vector Search Overview](https://docs.opensearch.org/latest/vector-search/)
- [Text Embedding Processor](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-embedding/)
- [Generating Embeddings Automatically (ML Commons)](https://docs.opensearch.org/latest/vector-search/getting-started/auto-generated-embeddings/)
- [k-NN Methods and Engines](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-methods-engines/)
- [k-NN Spaces](https://docs.opensearch.org/latest/mappings/supported-field-types/knn-spaces/)
- [Hybrid Search](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/)
