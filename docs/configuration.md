# FSCrawler Configuration Reference

FSCrawler reads job settings from a YAML file at:

```
<config_dir>/<job_name>/_settings.yaml
```

The default `config_dir` is `~/.fscrawler`.

## Full example

```yaml
name: "myjob"

fs:
  url: "/data"
  update_rate: "15m"
  includes: ["*.pdf", "*.doc", "*.docx"]
  excludes: ["*.tmp", "~*"]
  json_support: false
  xml_support: false
  follow_symlinks: false
  remove_deleted: true
  continue_on_error: false
  ignore_above: "512mb"
  filename_as_id: true
  index_content: true
  add_filesize: true
  attributes_support: false
  lang_detect: false
  store_source: false
  indexed_chars: "100000.0"
  raw_metadata: false
  checksum: "MD5"
  index_folders: true
  tika_url: "http://localhost:9998"
  content_hash_as_id: false

elasticsearch:
  nodes:
    - url: "http://localhost:9200"
  username: ""
  password: ""
  api_key: ""
  ssl_verification: false
  index: "myjob_docs"
  index_folder: "myjob_folder"
  bulk_size: 100

  byte_size: "10mb"
  push_templates: true

rest:
  url: "http://127.0.0.1:8080"
  enable_cors: false
```

---

## `fs` — File System settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | `/tmp/es` | Root directory to crawl. |
| `update_rate` | duration | `15m` | How often to re-crawl in loop mode. |
| `includes` | list[string] | `[]` | Glob patterns matched against the **filename only**. Empty = all files. Example: `["*.pdf", "*.docx"]` |
| `excludes` | list[string] | `[]` | Glob patterns matched against the **filename only**. Matching files are skipped. Example: `["~*", "*.tmp"]` |
| `json_support` | bool | `false` | Index JSON files as structured documents. |
| `xml_support` | bool | `false` | Index XML files as structured documents. |
| `follow_symlinks` | bool | `false` | Follow symbolic links. |
| `remove_deleted` | bool | `true` | Remove from index when files are deleted. |
| `continue_on_error` | bool | `false` | Skip unreadable files instead of aborting. |
| `ignore_above` | byte size | `null` | Skip files larger than this threshold. |
| `filename_as_id` | bool | `true` | Use file path as document `_id`. `false` = MD5 hash of the file path. Ignored when `content_hash_as_id` is `true`. |
| `index_content` | bool | `true` | Extract and index text content. |
| `add_filesize` | bool | `true` | Include file size in indexed document. |
| `attributes_support` | bool | `false` | Index POSIX file attributes (owner, permissions). |
| `lang_detect` | bool | `false` | Detect and store the document language. |
| `store_source` | bool | `false` | Store the raw binary as a Base64 attachment field. |
| `indexed_chars` | int | `100000` | Maximum characters of content to index. `-1` = unlimited. |
| `raw_metadata` | bool | `false` | Store all raw Tika metadata fields. |
| `checksum` | string | `null` | Compute file checksum and store it on the document (`MD5`, `SHA-1`, `SHA-256`, etc.). |
| `index_folders` | bool | `true` | Index directory entries in a separate folder index. |
| `tika_url` | string | `http://localhost:9998` | URL of the Apache Tika server used for content extraction. |
| `content_hash_as_id` | bool | `false` | Use MD5 of file content as the document `_id`. Each unique version of a file gets its own document — changed files are added as new documents, deleted files are never removed. Takes precedence over `filename_as_id`. |

### Duration format

Durations are expressed as a number followed by a unit:

| Suffix | Meaning |
|--------|---------|
| `ms`   | milliseconds |
| `s`    | seconds |
| `m`    | minutes |
| `h`    | hours |
| `d`    | days |

Examples: `15m`, `1h`, `30s`, `500ms`.

### Byte size format

| Suffix | Meaning |
|--------|---------|
| `b`    | bytes |
| `kb`   | kibibytes (1024) |
| `mb`   | mebibytes (1024²) |
| `gb`   | gibibytes (1024³) |
| `tb`   | tebibytes (1024⁴) |

Examples: `512mb`, `10kb`, `1gb`.

---

## `elasticsearch` — Target cluster settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `nodes` | list[{url}] | `[{url: "http://localhost:9200"}]` | List of OpenSearch/ES node URLs. |
| `username` | string | `""` | HTTP Basic auth username. |
| `password` | string | `""` | HTTP Basic auth password. |
| `api_key` | string | `""` | API key (takes precedence over username/password). |
| `ssl_verification` | bool | `true` | Verify TLS certificates (set `false` for self-signed). |
| `index` | string | `{name}_docs` | Index for document data. |
| `index_folder` | string | `{name}_folder` | Index for folder entries. |
| `bulk_size` | int | `100` | Number of documents per bulk request. |

| `byte_size` | byte size | `10mb` | Flush when the buffer reaches this size. |
| `push_templates` | bool | `true` | Create/update index and component templates on startup. |

---

## `rest` — Embedded REST server

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | `http://127.0.0.1:8080` | Bind address for the REST API. |
| `enable_cors` | bool | `false` | Enable CORS headers. |

---

## Environment variable overrides

The following environment variables are supported:

| Variable | Equivalent setting | Description |
|----------|--------------------|-------------|
| `FSCRAWLER_CONFIG_DIR` | — | Override the default config directory (`~/.fscrawler`). |
| `FSCRAWLER_FS_URL` | `fs.url` | Root directory to crawl. |
| `FSCRAWLER_FS_TIKA_URL` | `fs.tika_url` | Tika server URL. |
| `FSCRAWLER_FS_CONTENT_HASH_AS_ID` | `fs.content_hash_as_id` | `true` / `false`. |
| `FSCRAWLER_ELASTICSEARCH_URLS` | `elasticsearch.nodes` | Comma-separated node URLs. |
| `FSCRAWLER_ELASTICSEARCH_USERNAME` | `elasticsearch.username` | |
| `FSCRAWLER_ELASTICSEARCH_PASSWORD` | `elasticsearch.password` | |
| `FSCRAWLER_ELASTICSEARCH_API_KEY` | `elasticsearch.api_key` | |
| `FSCRAWLER_ELASTICSEARCH_SSL_VERIFICATION` | `elasticsearch.ssl_verification` | `true` / `false`. |
| `FSCRAWLER_ELASTICSEARCH_INDEX` | `elasticsearch.index` | |
| `FSCRAWLER_ELASTICSEARCH_BULK_SIZE` | `elasticsearch.bulk_size` | |

| `FSCRAWLER_ELASTICSEARCH_BYTE_SIZE` | `elasticsearch.byte_size` | |
| `FSCRAWLER_REST_URL` | `rest.url` | |
| `FSCRAWLER_REST_ENABLE_CORS` | `rest.enable_cors` | `true` / `false`. |

> **Priority:** YAML values take precedence over environment variables.
