"""Unit tests for fscrawler.templates."""
# Upstream: https://github.com/dadoonet/fscrawler/issues/904

from __future__ import annotations

import json
from pathlib import Path

import pytest

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "src" / "fscrawler" / "_templates"


class TestDLQTemplates:
    def test_dlq_component_template_loaded(self) -> None:
        from fscrawler.templates import get_component_templates

        names = [name for name, _ in get_component_templates()]
        assert "fscrawler_mapping_dlq" in names

    def test_pfq_component_template_loaded(self) -> None:
        from fscrawler.templates import get_component_templates

        names = [name for name, _ in get_component_templates()]
        assert "fscrawler_mapping_pfq" in names

    def test_dlq_index_template_loaded(self) -> None:
        from fscrawler.templates import get_index_templates

        names = [name for name, _ in get_index_templates()]
        assert "fscrawler_index_template_dlq" in names

    def test_pfq_index_template_loaded(self) -> None:
        from fscrawler.templates import get_index_templates

        names = [name for name, _ in get_index_templates()]
        assert "fscrawler_index_template_pfq" in names

    def test_dlq_mapping_has_required_fields(self) -> None:
        from fscrawler.templates import get_component_templates

        templates = dict(get_component_templates())
        props = templates["fscrawler_mapping_dlq"]["template"]["mappings"]["properties"]
        assert "job_name" in props
        assert "error_message" in props
        assert "retry_count" in props
        assert "payload" in props
        assert props["payload"]["enabled"] is False

    def test_pfq_mapping_has_promoted_at(self) -> None:
        from fscrawler.templates import get_component_templates

        templates = dict(get_component_templates())
        props = templates["fscrawler_mapping_pfq"]["template"]["mappings"]["properties"]
        assert "promoted_at" in props
        assert "final_error" in props


class TestTemplateValidity:
    """All template JSON files must be valid and parseable."""

    @pytest.mark.parametrize("json_file", sorted(_TEMPLATES_DIR.glob("*.json")))
    def test_template_is_valid_json(self, json_file: Path) -> None:
        data = json.loads(json_file.read_text())
        assert isinstance(data, dict)


# Upstream: https://github.com/dadoonet/fscrawler/issues/890
# Related: https://github.com/dadoonet/fscrawler/issues/904 (store=true on filename)
class TestMappingTypes:
    """Validate field types prevent mapping conflicts."""

    def test_filesize_uses_long_type(self) -> None:
        data = json.loads((_TEMPLATES_DIR / "mapping_file.json").read_text())
        file_props = data["template"]["mappings"]["properties"]["file"]["properties"]
        assert file_props["filesize"]["type"] == "long"

    def test_indexed_chars_uses_long_type(self) -> None:
        data = json.loads((_TEMPLATES_DIR / "mapping_file.json").read_text())
        file_props = data["template"]["mappings"]["properties"]["file"]["properties"]
        assert file_props["indexed_chars"]["type"] == "long"

    def test_meta_fields_use_compatible_types(self) -> None:
        data = json.loads((_TEMPLATES_DIR / "mapping_meta.json").read_text())
        meta_props = data["template"]["mappings"]["properties"]["meta"]["properties"]
        allowed = {"keyword", "text", "date", "short", "object"}
        for name, defn in meta_props.items():
            assert defn.get("type", "object") in allowed, \
                f"meta.{name} type '{defn.get('type')}' not in allowed set"

    def test_permissions_is_keyword(self) -> None:
        data = json.loads((_TEMPLATES_DIR / "mapping_attributes.json").read_text())
        attrs_props = data["template"]["mappings"]["properties"]["attributes"]["properties"]
        assert attrs_props["permissions"]["type"] == "keyword"

    def test_filename_is_stored(self) -> None:
        data = json.loads((_TEMPLATES_DIR / "mapping_file.json").read_text())
        file_props = data["template"]["mappings"]["properties"]["file"]["properties"]
        assert file_props["filename"].get("store") is True
