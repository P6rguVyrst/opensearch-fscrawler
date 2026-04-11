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

TEMPLATE_GROUPS = {
    "default": {
        "components": (
            "settings_total_fields",
            "mapping_file",
            "mapping_path",
            "mapping_meta",
            "mapping_content",
            "mapping_attachment",
            "mapping_attributes",
            "mapping_history",
            "mapping_dlq",
            "mapping_pfq",
        ),
        "indices": (
            "index_template_docs",
            "index_template_folders",
            "index_template_history",
            "index_template_dlq",
            "index_template_pfq",
        ),
    },
    "dlq": {
        "components": (
            "settings_total_fields",
            "mapping_dlq",
            "mapping_pfq",
        ),
        "indices": (
            "index_template_dlq",
            "index_template_pfq",
        ),
    },
}

SHARED_COMPONENTS = list(TEMPLATE_GROUPS["default"]["components"])
INDEX_TEMPLATES = list(TEMPLATE_GROUPS["default"]["indices"])


def _load(name: str) -> dict[str, Any]:
    """Load a JSON template file from the _templates directory."""
    with open(_TEMPLATES_DIR / f"{name}.json") as f:
        return json.load(f)  # type: ignore[no-any-return]


def get_template_group(
    group: str = "default",
) -> tuple[list[tuple[str, dict[str, Any]]], list[tuple[str, dict[str, Any]]]]:
    """Return the component and index templates for a named group."""
    template_group = TEMPLATE_GROUPS[group]
    component_templates = [
        (f"fscrawler_{name}", _load(name))
        for name in template_group["components"]
    ]
    index_templates = [
        (f"fscrawler_{name}", _load(name))
        for name in template_group["indices"]
    ]
    return component_templates, index_templates


def get_component_templates() -> list[tuple[str, dict[str, Any]]]:
    """Return (name, body) tuples for all component templates."""
    component_templates, _ = get_template_group()
    return component_templates


def get_index_templates() -> list[tuple[str, dict[str, Any]]]:
    """Return (name, body) tuples for all index templates."""
    _, index_templates = get_template_group()
    return index_templates
