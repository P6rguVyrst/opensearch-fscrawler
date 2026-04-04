"""Unit tests for fscrawler.templates."""

from __future__ import annotations


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
