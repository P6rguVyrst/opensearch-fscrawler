# Licensed under the Apache License, Version 2.0
"""Tests that essential non-Python data files are importable at runtime.

These files live in src/fscrawler/_templates/ and src/fscrawler/_queries/
and must be included in the wheel. If the build config changes and excludes
them, these tests catch the regression.
"""

from __future__ import annotations

from pathlib import Path

import fscrawler


def _package_dir() -> Path:
    """Return the installed fscrawler package directory."""
    return Path(fscrawler.__file__).parent


class TestTemplatesBundled:
    def test_templates_directory_exists(self) -> None:
        templates_dir = _package_dir() / "_templates"
        assert templates_dir.is_dir(), f"_templates dir missing: {templates_dir}"

    def test_all_component_templates_present(self) -> None:
        from fscrawler.templates import _TEMPLATES_DIR, SHARED_COMPONENTS

        for name in SHARED_COMPONENTS:
            path = _TEMPLATES_DIR / f"{name}.json"
            assert path.is_file(), f"Missing component template: {path}"

    def test_all_index_templates_present(self) -> None:
        from fscrawler.templates import _TEMPLATES_DIR, INDEX_TEMPLATES

        for name in INDEX_TEMPLATES:
            path = _TEMPLATES_DIR / f"{name}.json"
            assert path.is_file(), f"Missing index template: {path}"

    def test_templates_are_valid_json(self) -> None:
        import json

        from fscrawler.templates import _TEMPLATES_DIR

        for json_file in _TEMPLATES_DIR.glob("*.json"):
            with open(json_file) as f:
                data = json.load(f)
            assert isinstance(data, dict), f"{json_file.name} is not a JSON object"


class TestQueriesBundled:
    def test_queries_directory_exists(self) -> None:
        queries_dir = _package_dir() / "_queries"
        assert queries_dir.is_dir(), f"_queries dir missing: {queries_dir}"

    def test_dlq_due_records_query_present(self) -> None:
        import json

        queries_dir = _package_dir() / "_queries"
        path = queries_dir / "dlq_due_records.json"
        assert path.is_file(), f"Missing query file: {path}"
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)
