# Licensed under the Apache License, Version 2.0
"""Elasticsearch / OpenSearch index and component template definitions.

Templates are compatible with both Elasticsearch 7.x/8.x/9.x and OpenSearch 1.x/2.x/3.x.

All template bodies live in _templates/*.json — this module loads them.

Component templates (created once per cluster):
  fscrawler_settings_total_fields
  fscrawler_mapping_file
  fscrawler_mapping_path
  fscrawler_mapping_meta
  fscrawler_mapping_content
  fscrawler_mapping_attachment
  fscrawler_mapping_attributes
  fscrawler_mapping_history

Index templates (created once per cluster, match by naming convention):
  fscrawler_docs     → fscrawler_docs_*
  fscrawler_folders  → fscrawler_folders_*
  fscrawler_history  → fscrawler_history_*
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TEMPLATES_DIR = Path(__file__).parent / "_templates"

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

INDEX_TEMPLATES = [
    "index_template_docs",
    "index_template_folders",
    "index_template_history",
]


def _load(name: str) -> dict[str, Any]:
    """Load a JSON template file from the _templates directory."""
    with open(_TEMPLATES_DIR / f"{name}.json") as f:
        return json.load(f)  # type: ignore[no-any-return]


def get_component_templates() -> list[tuple[str, dict[str, Any]]]:
    """Return (name, body) tuples for all component templates."""
    return [(f"fscrawler_{name}", _load(name)) for name in SHARED_COMPONENTS]


def get_index_templates() -> list[tuple[str, dict[str, Any]]]:
    """Return (name, body) tuples for all index templates."""
    return [(f"fscrawler_{name}", _load(name)) for name in INDEX_TEMPLATES]
