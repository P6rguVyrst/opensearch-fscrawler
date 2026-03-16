# Licensed under the Apache License, Version 2.0
"""FSCrawler settings — YAML loading with backwards-compatible format."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("fscrawler.settings")

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h|d)?\s*$", re.IGNORECASE
)
_BYTE_RE = re.compile(
    r"^\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>b|kb|mb|gb|tb)?\s*$", re.IGNORECASE
)

_DURATION_MULTIPLIERS: dict[str, float] = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "": 1.0,  # plain number → seconds
}

_BYTE_MULTIPLIERS: dict[str, int] = {
    "b": 1,
    "": 1,
    "kb": 1024,
    "mb": 1024**2,
    "gb": 1024**3,
    "tb": 1024**4,
}


def parse_duration(value: str) -> float:
    """Parse a duration string like '15m', '5s', '2h' into seconds (float)."""
    m = _DURATION_RE.match(value)
    if not m:
        raise ValueError(f"Cannot parse duration: {value!r}")
    num = float(m.group("value"))
    unit = (m.group("unit") or "").lower()
    return num * _DURATION_MULTIPLIERS[unit]


def parse_byte_size(value: str) -> int:
    """Parse a byte-size string like '512mb', '10kb' into bytes (int)."""
    m = _BYTE_RE.match(str(value))
    if not m:
        raise ValueError(f"Cannot parse byte size: {value!r}")
    num = float(m.group("value"))
    unit = (m.group("unit") or "").lower()
    return int(num * _BYTE_MULTIPLIERS[unit])


def parse_indexed_chars(value: str) -> int:
    """Parse indexed_chars — float string like '100000.0' or '-1' → int."""
    return int(float(value))


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FsSettingsError(ValueError):
    """Raised when the settings file is invalid or missing required fields."""


# ---------------------------------------------------------------------------
# Settings dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FsConfig:
    """Configuration for the file system source (fs: block)."""

    url: str = ""
    update_rate: float = 900.0  # 15m default
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    json_support: bool = False
    xml_support: bool = False
    follow_symlinks: bool = False
    remove_deleted: bool = True
    continue_on_error: bool = False
    ignore_above: int | None = None  # bytes; None = no limit
    filename_as_id: bool = True
    index_content: bool = True
    add_filesize: bool = True
    attributes_support: bool = False
    lang_detect: bool = False
    store_source: bool = False
    indexed_chars: int = 100000
    raw_metadata: bool = False
    checksum: str | None = None
    index_folders: bool = True
    tika_url: str = "http://localhost:9998"
    content_hash_as_id: bool = False


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
    bulk_size: int = 100
    byte_size: int = 10 * 1024 * 1024  # 10mb
    push_templates: bool = True

    def __post_init__(self) -> None:
        if self.api_key and (self.username or self.password):
            logger.warning(
                "Both api_key and username/password are set; api_key takes precedence."
            )


@dataclass
class RestConfig:
    """Configuration for the embedded REST server."""

    url: str = "http://127.0.0.1:8080"
    enable_cors: bool = False


def _apply_env_to_raw(raw: dict[str, Any], env: dict[str, str]) -> None:
    """Overlay FSCRAWLER_* environment variables onto the raw settings dict.

    Only sets values that are *not* already present in the YAML — env vars
    are a fallback, not an override, matching the Java Gestalt priority order
    (YAML > env vars > defaults).
    """

    def _setdefault_nested(d: dict[str, Any], section: str, key: str, value: Any) -> None:
        sec = d.setdefault(section, {})
        if key not in sec:
            sec[key] = value

    if v := env.get("FSCRAWLER_ELASTICSEARCH_URLS"):
        urls = [u.strip() for u in v.split(",") if u.strip()]
        nodes_as_dicts = [{"url": u} for u in urls]
        raw.setdefault("elasticsearch", {})
        # Only apply if neither the Java-style "urls" nor Python-style "nodes" key is set
        if "urls" not in raw["elasticsearch"] and "nodes" not in raw["elasticsearch"]:
            raw["elasticsearch"]["nodes"] = nodes_as_dicts

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
        ("FSCRAWLER_FS_CONTENT_HASH_AS_ID", "fs", "content_hash_as_id"),
    ]:
        if v := env.get(env_key):
            _setdefault_nested(raw, section, field_name, v)

    for env_key, section, field_name in [
        ("FSCRAWLER_ELASTICSEARCH_SSL_VERIFICATION", "elasticsearch", "ssl_verification"),
        ("FSCRAWLER_REST_ENABLE_CORS", "rest", "enable_cors"),
    ]:
        if v := env.get(env_key):
            _setdefault_nested(raw, section, field_name, v.lower() not in ("false", "0", "no"))


@dataclass
class FsSettings:
    """Top-level FSCrawler settings object."""

    name: str
    fs: FsConfig = field(default_factory=FsConfig)
    elasticsearch: ElasticsearchSettings = field(default_factory=ElasticsearchSettings)
    rest: RestConfig = field(default_factory=RestConfig)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FsSettings:
        """Build FsSettings from a raw (YAML-parsed) dictionary."""
        # --- name ---
        name = data.get("name")
        if not name:
            raise FsSettingsError("'name' is required in settings")

        # --- fs ---
        fs_data: dict[str, Any] = data.get("fs") or {}
        # Java default is /tmp/es — do not require explicit fs.url (Java parity)
        fs = FsConfig(url=fs_data.get("url") or "/tmp/es")  # noqa: S108  # Java parity default

        if "update_rate" in fs_data:
            fs.update_rate = parse_duration(str(fs_data["update_rate"]))
        if "includes" in fs_data:
            fs.includes = list(fs_data["includes"])
        if "excludes" in fs_data:
            fs.excludes = list(fs_data["excludes"])
        if "json_support" in fs_data:
            fs.json_support = bool(fs_data["json_support"])
        if "xml_support" in fs_data:
            fs.xml_support = bool(fs_data["xml_support"])
        if "follow_symlinks" in fs_data:
            fs.follow_symlinks = bool(fs_data["follow_symlinks"])
        if "remove_deleted" in fs_data:
            fs.remove_deleted = bool(fs_data["remove_deleted"])
        if "continue_on_error" in fs_data:
            fs.continue_on_error = bool(fs_data["continue_on_error"])
        if "ignore_above" in fs_data:
            fs.ignore_above = parse_byte_size(str(fs_data["ignore_above"]))
        if "filename_as_id" in fs_data:
            fs.filename_as_id = bool(fs_data["filename_as_id"])
        if "index_content" in fs_data:
            fs.index_content = bool(fs_data["index_content"])
        if "add_filesize" in fs_data:
            fs.add_filesize = bool(fs_data["add_filesize"])
        if "attributes_support" in fs_data:
            fs.attributes_support = bool(fs_data["attributes_support"])
        if "lang_detect" in fs_data:
            fs.lang_detect = bool(fs_data["lang_detect"])
        if "store_source" in fs_data:
            fs.store_source = bool(fs_data["store_source"])
        if "indexed_chars" in fs_data:
            fs.indexed_chars = parse_indexed_chars(str(fs_data["indexed_chars"]))
        if "raw_metadata" in fs_data:
            fs.raw_metadata = bool(fs_data["raw_metadata"])
        if "checksum" in fs_data:
            val = fs_data["checksum"]
            fs.checksum = val if val else None
        if "index_folders" in fs_data:
            fs.index_folders = bool(fs_data["index_folders"])
        if "tika_url" in fs_data:
            fs.tika_url = str(fs_data["tika_url"])
        if "content_hash_as_id" in fs_data:
            fs.content_hash_as_id = bool(fs_data["content_hash_as_id"])

        # --- elasticsearch ---
        es_data: dict[str, Any] = data.get("elasticsearch") or {}
        es = ElasticsearchSettings()

        # Accept both "urls" (Java current) and "nodes" (Java deprecated / Python name)
        raw_nodes = es_data.get("urls") or es_data.get("nodes")
        if raw_nodes:
            parsed_nodes: list[str] = []
            for node in raw_nodes:
                if isinstance(node, dict):
                    parsed_nodes.append(node["url"])
                else:
                    parsed_nodes.append(str(node))
            es.nodes = parsed_nodes

        if "username" in es_data:
            es.username = es_data["username"] or ""
        if "password" in es_data:
            es.password = es_data["password"] or ""
        if "api_key" in es_data:
            es.api_key = es_data["api_key"] or ""
        if "ssl_verification" in es_data:
            es.ssl_verification = bool(es_data["ssl_verification"])
        if "index" in es_data and es_data["index"]:
            es.index = es_data["index"]
        if "index_folder" in es_data and es_data["index_folder"]:
            es.index_folder = es_data["index_folder"]
        if "bulk_size" in es_data:
            es.bulk_size = int(es_data["bulk_size"])

        if "byte_size" in es_data:
            es.byte_size = parse_byte_size(str(es_data["byte_size"]))
        if "push_templates" in es_data:
            es.push_templates = bool(es_data["push_templates"])

        # Apply defaults that depend on job name
        if not es.index:
            es.index = f"{name}_docs"
        if not es.index_folder:
            es.index_folder = f"{name}_folder"

        # Trigger __post_init__ warning after all fields are set
        if es.api_key and (es.username or es.password):
            logger.warning(
                "Both api_key and username/password are set; api_key takes precedence."
            )

        # --- rest ---
        rest_data: dict[str, Any] = data.get("rest") or {}
        rest = RestConfig()
        if "url" in rest_data:
            rest.url = rest_data["url"]
        if "enable_cors" in rest_data:
            rest.enable_cors = bool(rest_data["enable_cors"])

        return cls(name=str(name), fs=fs, elasticsearch=es, rest=rest)

    @classmethod
    def from_file(
        cls,
        path: Path | str,
        environ: dict[str, str] | None = None,
    ) -> FsSettings:
        """Load settings from a YAML file, then apply FSCRAWLER_* env var overrides.

        Environment variables (Java parity):
          FSCRAWLER_ELASTICSEARCH_URLS      – comma-separated node URLs
          FSCRAWLER_ELASTICSEARCH_USERNAME
          FSCRAWLER_ELASTICSEARCH_PASSWORD
          FSCRAWLER_ELASTICSEARCH_API_KEY
          FSCRAWLER_ELASTICSEARCH_SSL_VERIFICATION  – "true" / "false"
          FSCRAWLER_ELASTICSEARCH_INDEX
          FSCRAWLER_ELASTICSEARCH_BULK_SIZE
          FSCRAWLER_ELASTICSEARCH_FLUSH_INTERVAL
          FSCRAWLER_ELASTICSEARCH_BYTE_SIZE
          FSCRAWLER_REST_URL
          FSCRAWLER_REST_ENABLE_CORS                – "true" / "false"
          FSCRAWLER_FS_URL

        YAML values take precedence over env vars — env vars are a fallback
        for settings not explicitly specified in the file, matching the Java
        Gestalt configuration priority order.
        """
        import os

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Settings file not found: {p}")
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise FsSettingsError(f"Malformed YAML in {p}: {exc}") from exc
        if not isinstance(raw, dict):
            raise FsSettingsError(f"Settings file must be a YAML mapping, got: {type(raw)}")

        env = environ if environ is not None else dict(os.environ)
        _apply_env_to_raw(raw, env)

        return cls.from_dict(raw)
