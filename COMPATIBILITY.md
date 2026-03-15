# FSCrawler Python — Compatibility with Java Edition

This document describes the differences between the Python rewrite and the
canonical Java edition (`dadoonet/fscrawler`).  It is intended for operators
migrating from Java, and for developers working on closing the gaps.

---

## CLI flags

| Flag | Java | Python | Notes |
|---|---|---|---|
| `job_name` | Optional, defaults to `fscrawler` | Optional, defaults to `fscrawler` | ✅ Compatible |
| `--config_dir` | ✅ | ✅ | Also reads `FSCRAWLER_CONFIG_DIR` env var |
| `--rest` | ✅ | ✅ | Starts embedded REST API server |
| `--setup` | ✅ | ✅ | Creates template `_settings.yaml` |
| `--loop` | Integer — number of scan loops (`-1` = infinite) | Boolean flag — always infinite | ⚠️ `--loop 5` silently treats `5` as `job_name` in Python |
| `--restart` | ✅ Resets job state without wiping indices | ❌ Not implemented | See `TODO.md` |
| `--list` | ✅ Lists configured jobs | ❌ Not implemented | See `TODO.md` |
| `--upgrade` | ✅ Upgrades ES indices from old schema | ❌ Not implemented | See `TODO.md` |
| `--debug` | Deprecated; use `FS_JAVA_OPTS=-DLOG_LEVEL=debug` | ✅ Flag works directly | Different mechanism, same outcome |
| `--trace` | Deprecated flag | ❌ Not implemented | Use `--debug` |
| `--silent` | Suppresses all output | ❌ Not implemented | |
| `--log-format` | ❌ Not available | ✅ `json` or `text` | Python addition |
| `--log-output` | ❌ Not available | ✅ `stdout`, `stderr`, `file`, `otel` | Python addition |
| `--log-file` | ❌ Not available | ✅ | Python addition |
| `--log-otel-endpoint` | ❌ Not available | ✅ | Python addition |

---

## `_settings.yaml` — field compatibility

A Java `_settings.yaml` loads without error in Python.  Fields Python does not
understand are silently ignored.

### `fs` block

| Field | Java | Python | Notes |
|---|---|---|---|
| `url` | Optional, defaults to `/tmp/es` | Optional, defaults to `/tmp/es` | ✅ Compatible |
| `update_rate` | Duration string e.g. `15m` | ✅ Same format | ✅ Compatible |
| `includes` | `List<String>` glob patterns | ✅ | ⚠️ **Patterns match filename only** — `*/*.pdf` never matches. Use `*.pdf`. See note below. |
| `excludes` | `List<String>`, default `*/~*` | ✅ | ⚠️ Same filename-only matching. Use `~*` not `*/~*`. |
| `json_support` | `boolean` | ✅ | ✅ Compatible |
| `xml_support` | `boolean` | ✅ | ✅ Compatible |
| `follow_symlinks` | `boolean` | ✅ | ✅ Compatible |
| `remove_deleted` | `boolean` | ✅ | ✅ Compatible |
| `continue_on_error` | `boolean` | ✅ | ✅ Compatible |
| `ignore_above` | Byte size string e.g. `512mb` | ✅ Same format | ✅ Compatible |
| `filename_as_id` | `false` | `true` | ⚠️ **Default differs** — see below |
| `index_content` | `boolean` | ✅ | ✅ Compatible |
| `add_filesize` | `boolean` | ✅ | ✅ Compatible |
| `attributes_support` | `boolean` | ✅ | ✅ Compatible |
| `lang_detect` | `boolean` | ✅ | ✅ Compatible |
| `store_source` | `boolean` | ✅ | ✅ Compatible |
| `indexed_chars` | Percentage/float string | ✅ Parsed as float then int | ✅ Compatible |
| `raw_metadata` | `boolean` | ✅ | ✅ Compatible |
| `checksum` | `MD5`, `SHA-256`, etc. | ✅ | ✅ Compatible |
| `index_folders` | `boolean` | ✅ | ✅ Compatible |
| `tika_url` | ❌ Not available | ✅ URL of the Tika server | Python addition — defaults to `http://localhost:9998` |
| `content_hash_as_id` | ❌ Not available | ✅ `boolean` | Python addition — see ID mode table below |
| `filters` | Regex content filters | ❌ Silently ignored | |
| `add_as_inner_object` | JSON inner-object mode | ❌ Silently ignored | |
| `tika_config_path` | Custom Tika XML config | ❌ Silently ignored | |
| `temp_dir` | Temp directory for processing | ❌ Silently ignored | |
| `provider` | Plugin provider selection | ❌ Silently ignored | |
| `ocr.*` | Full OCR block (Tesseract) | ❌ Entire block silently ignored | See OCR section below |

#### Note: `includes` / `excludes` pattern matching

Java matches glob patterns against the **full file path**.  Python matches
against the **filename only** (no directory component).

| Pattern | Java | Python |
|---|---|---|
| `*.pdf` | Matches `*.pdf` anywhere | ✅ Matches `*.pdf` anywhere |
| `*/*.pdf` | Matches `*.pdf` one level deep | ❌ Never matches — `/` can't appear in a filename |
| `**/*.pdf` | Matches `*.pdf` at any depth | ❌ Never matches — same reason |
| `~*` | Matches filenames starting with `~` | ✅ Correct |
| `*/~*` | Java default — matches `~*` anywhere | ❌ Never matches in Python — use `~*` |

**Action required for migration:** strip any directory components from your
`includes` and `excludes` patterns.

#### Document ID modes

Python supports three `_id` strategies, compared to Java's two:

| Setting | `_id` value | File updated | File deleted |
|---|---|---|---|
| `filename_as_id: true` *(Python default)* | raw file path | overwrites same doc | deletes doc |
| `filename_as_id: false` *(Java default)* | MD5 of file path | overwrites same doc | deletes doc |
| `content_hash_as_id: true` *(Python only)* | MD5 of file content | new doc created | no-op |

`content_hash_as_id` takes precedence over `filename_as_id` when both are set.

### `elasticsearch` block

| Field | Java | Python | Notes |
|---|---|---|---|
| `urls` | `List<String>` — **current preferred key** | Accepted as alias for `nodes` | ✅ Compatible |
| `nodes` | `List<ServerUrl>` — deprecated | ✅ Primary key | ✅ Compatible |
| `index` | String | ✅ | ✅ Compatible |
| `index_folder` | String | ✅ | ✅ Compatible |
| `bulk_size` | Integer | ✅ | ✅ Compatible |
| `flush_interval` | Duration string | ❌ Removed | Not implemented — buffer flushes on count (`bulk_size`) or byte-size (`byte_size`) only. Setting is silently ignored. |
| `byte_size` | Byte size string | ✅ | ✅ Compatible |
| `api_key` | String | ✅ | ✅ Compatible |
| `username` | Deprecated | ✅ Still accepted | ✅ Compatible |
| `password` | Deprecated, `@JsonIgnore` in Java | ✅ Accepted | ✅ Compatible |
| `ssl_verification` | `boolean` | ✅ | ✅ Compatible |
| `push_templates` | `boolean` | ✅ | ✅ Compatible |
| `ca_certificate` | Path to CA cert file | ❌ Silently ignored | |
| `pipeline` | Ingest pipeline name | ❌ Silently ignored | |
| `path_prefix` | URL path prefix | ❌ Silently ignored | |
| `force_push_templates` | `boolean` | ❌ Silently ignored | |
| `semantic_search` | `boolean`, default `true` | ❌ Silently ignored | |

### `rest` block

| Field | Java | Python | Notes |
|---|---|---|---|
| `url` | `http://127.0.0.1:8080/fscrawler` | `http://127.0.0.1:8080` | ⚠️ Java default has `/fscrawler` path suffix |
| `enable_cors` | `boolean` | ✅ | ✅ Compatible |

### `server` block — ❌ Not implemented

The entire `server` block (SSH / FTP / SFTP remote crawling) is silently
ignored.  Python only supports local filesystem crawling.

| Field | Java |
|---|---|
| `hostname` | Remote host |
| `port` | Port (default `0`) |
| `username` | Auth username |
| `password` | Auth password |
| `protocol` | `local`, `ssh`, `ftp` |
| `pem_path` | SSH private key path |

### `tags` block — ❌ Not implemented

The `tags` block (per-directory `.meta.yml` metadata injection) is silently
ignored.

| Field | Java |
|---|---|
| `meta_filename` | Filename to look for (default `.meta.yml`) |
| `static_meta_filename` | Path to a static metadata file |

---

## Default value differences

These defaults differ between the two editions.  Explicit values in
`_settings.yaml` behave identically; only unset fields are affected.

| Setting | Java default | Python default | Risk |
|---|---|---|---|
| `fs.url` | `/tmp/es` | `/tmp/es` | ✅ Same |
| `fs.filename_as_id` | `false` | `true` | ⚠️ **High** — affects document IDs; existing Java indices will accumulate duplicates if migrated |
| `fs.update_rate` (crawl trigger) | Polls directory on timer | Event-driven via watchdog + initial scan on startup | ✅ **Python is faster** — files are indexed immediately on create/modify rather than waiting for the next poll cycle |
| `elasticsearch.urls` | `https://127.0.0.1:9200` (HTTPS) | `http://localhost:9200` (HTTP) | ⚠️ Medium — connection will fail if ES requires TLS and no URL is set |
| `rest.url` | `http://127.0.0.1:8080/fscrawler` | `http://127.0.0.1:8080` | ⚠️ Low — REST clients that call `/fscrawler/...` paths will 404 |

---

## Environment variable support

Both editions support `FSCRAWLER_` prefixed env vars to configure settings
without modifying `_settings.yaml`.  Python follows the same Java priority
rule: **YAML values take precedence over env vars**.

| Env var | Java | Python |
|---|---|---|
| `FSCRAWLER_CONFIG_DIR` | N/A (use `--config_dir`) | ✅ Wired to `--config_dir` |
| `FSCRAWLER_ELASTICSEARCH_URLS` | ✅ | ✅ |
| `FSCRAWLER_ELASTICSEARCH_USERNAME` | ✅ | ✅ |
| `FSCRAWLER_ELASTICSEARCH_PASSWORD` | ✅ | ✅ |
| `FSCRAWLER_ELASTICSEARCH_API_KEY` | ✅ | ✅ |
| `FSCRAWLER_ELASTICSEARCH_SSL_VERIFICATION` | ✅ | ✅ |
| `FSCRAWLER_ELASTICSEARCH_INDEX` | ✅ | ✅ |
| `FSCRAWLER_ELASTICSEARCH_BULK_SIZE` | ✅ | ✅ |
| `FSCRAWLER_ELASTICSEARCH_FLUSH_INTERVAL` | ✅ | ✅ |
| `FSCRAWLER_ELASTICSEARCH_BYTE_SIZE` | ✅ | ✅ |
| `FSCRAWLER_REST_URL` | ✅ | ✅ |
| `FSCRAWLER_REST_ENABLE_CORS` | ✅ | ✅ |
| `FSCRAWLER_FS_URL` | ✅ | ✅ |
| `FSCRAWLER_FS_TIKA_URL` | ❌ Not available | ✅ Sets `fs.tika_url` — Python addition |
| `FSCRAWLER_FS_CONTENT_HASH_AS_ID` | ❌ Not available | ✅ Sets `fs.content_hash_as_id` — Python addition |
| `FS_JAVA_OPTS` | ✅ JVM options | ❌ Not applicable |

---

## REST API compatibility

The embedded REST server (`--rest`) mirrors the Java surface.

| Endpoint | Java | Python |
|---|---|---|
| `GET /` | ✅ Server status | ✅ |
| `POST /_document` | ✅ Multipart upload | ✅ |
| `PUT /_document/{id}` | ✅ Upload with explicit ID | ✅ |
| `DELETE /_document` | ✅ Delete by filename | ✅ |
| `DELETE /_document/{id}` | ✅ Delete by ID | ✅ |
| `POST /_document` (JSON body) | ✅ Third-party provider upload | ❌ Not implemented |
| `POST /_crawler/pause` | ✅ | ✅ |
| `POST /_crawler/resume` | ✅ | ✅ |
| `GET /_crawler/status` | ✅ | ✅ |
| `DELETE /_crawler/checkpoint` | ✅ | ✅ |
| `GET /_crawler/settings` | ❌ Not available | ✅ Returns `fs` config block as JSON (Python addition) |

---

## Features not available in Python

These Java features have no Python equivalent and are not on the immediate
roadmap.  See `TODO.md` for tracking.

| Feature | Notes |
|---|---|
| **SSH / FTP / SFTP crawling** | `server` block — Python crawls local filesystem only |
| **S3 crawling** | Java plugin — not ported |
| **HTTP source crawling** | Java plugin — not ported |
| **OCR (Tesseract)** | `fs.ocr.*` block — Tika is called without OCR config |
| **Ingest pipeline** | `elasticsearch.pipeline` — documents are indexed directly |
| **Semantic search** | `elasticsearch.semantic_search` — not wired |
| **Per-directory metadata** | `tags.meta_filename` / `.meta.yml` files |
| **Content filters** | `fs.filters` regex matching on extracted text |
| **CA certificate** | `elasticsearch.ca_certificate` — custom TLS CA |
| **Index upgrade** | `--upgrade` CLI flag |
| **Job restart** | `--restart` CLI flag |
| **Job listing** | `--list` CLI flag |
| **Exact loop count** | `--loop <N>` integer form |
