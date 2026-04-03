# Licensed under the Apache License, Version 2.0
"""Elasticsearch / OpenSearch index and component template definitions.

Templates are compatible with both Elasticsearch 7.x/8.x/9.x and OpenSearch 1.x/2.x/3.x.

All template bodies live in _templates/*.json — this module loads them and
wires up the per-index naming.

Shared component templates (created once per cluster):
  fscrawler_settings_total_fields
  fscrawler_mapping_file
  fscrawler_mapping_path
  fscrawler_mapping_meta
  fscrawler_mapping_content
  fscrawler_mapping_attachment
  fscrawler_mapping_attributes
  fscrawler_mapping_history

Per-index component templates:
  fscrawler_{index}_alias

Index templates compose the shared components with a per-index alias:
  fscrawler_{index}_docs / fscrawler_{index}_folders / fscrawler_{index}_history
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TEMPLATES_DIR = Path(__file__).parent / "_templates"

# Shared component template names (no per-index prefix)
SHARED_COMPONENTS = [
    "settings_total_fields",
    "mapping_file",
    "mapping_path",
    "mapping_meta",
    "mapping_content",
    "mapping_attachment",
    "mapping_attributes",
    "mapping_history",
]


def _load(name: str) -> dict[str, Any]:
    """Load a JSON template file from the _templates directory."""
    with open(_TEMPLATES_DIR / f"{name}.json") as f:
        return json.load(f)  # type: ignore[no-any-return]


def _load_index_template(name: str, index_name: str) -> dict[str, Any]:
    """Load an index template JSON and substitute the per-index alias placeholder."""
    raw = (_TEMPLATES_DIR / f"{name}.json").read_text()
    raw = raw.replace("{{INDEX_ALIAS}}", f"fscrawler_{index_name}_alias")
    body = json.loads(raw)
    body["index_patterns"] = [index_name]
    return body  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Shared component templates (one per cluster, not per index)
# ---------------------------------------------------------------------------


def get_shared_component_templates() -> list[tuple[str, dict[str, Any]]]:
    """Return (name, body) tuples for all shared component templates."""
    return [(f"fscrawler_{name}", _load(name)) for name in SHARED_COMPONENTS]


def mapping_history_template() -> dict[str, Any]:
    """Return the history mapping component template body."""
    return _load("mapping_history")


# ---------------------------------------------------------------------------
# Per-index alias template
# ---------------------------------------------------------------------------


def alias_template(alias_name: str) -> dict[str, Any]:
    """Component template that creates an alias pointing to the job name."""
    return {"template": {"aliases": {alias_name: {}}}}


# ---------------------------------------------------------------------------
# Index templates
# ---------------------------------------------------------------------------


def get_index_templates(
    docs_index: str,
    folder_index: str,
    job_name: str,
    history_index: str = "",
) -> list[tuple[str, dict[str, Any]]]:
    """Return (name, body) tuples for all index templates and their per-index alias components."""
    result: list[tuple[str, dict[str, Any]]] = []

    # Per-index alias component templates
    for index_name in [docs_index, folder_index] + ([history_index] if history_index else []):
        result.append((f"fscrawler_{index_name}_alias", alias_template(job_name)))

    # Index templates — loaded from JSON, parameterised with the index name
    result.append((
        f"fscrawler_{docs_index}_docs",
        _load_index_template("index_template_docs", docs_index),
    ))
    result.append((
        f"fscrawler_{folder_index}_folders",
        _load_index_template("index_template_folders", folder_index),
    ))
    if history_index:
        result.append((
            f"fscrawler_{history_index}_history",
            _load_index_template("index_template_history", history_index),
        ))

    return result
