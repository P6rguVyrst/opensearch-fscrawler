"""Unit tests for fscrawler.settings."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from fscrawler.settings import (
    ElasticsearchSettings,
    FsSettings,
    FsSettingsError,
    parse_byte_size,
    parse_duration,
    parse_indexed_chars,
)


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------


class TestParseDuration:
    def test_minutes(self) -> None:
        assert parse_duration("15m") == 900.0

    def test_seconds(self) -> None:
        assert parse_duration("5s") == 5.0

    def test_hours(self) -> None:
        assert parse_duration("2h") == 7200.0

    def test_days(self) -> None:
        assert parse_duration("1d") == 86400.0

    def test_plain_number_treated_as_seconds(self) -> None:
        assert parse_duration("30") == 30.0

    def test_milliseconds(self) -> None:
        assert parse_duration("500ms") == 0.5

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse duration"):
            parse_duration("badvalue")


# ---------------------------------------------------------------------------
# parse_byte_size
# ---------------------------------------------------------------------------


class TestParseByteSize:
    def test_megabytes(self) -> None:
        assert parse_byte_size("512mb") == 512 * 1024 * 1024

    def test_kilobytes(self) -> None:
        assert parse_byte_size("10kb") == 10 * 1024

    def test_gigabytes(self) -> None:
        assert parse_byte_size("1gb") == 1024 * 1024 * 1024

    def test_bytes_no_suffix(self) -> None:
        assert parse_byte_size("1024") == 1024

    def test_bytes_suffix(self) -> None:
        assert parse_byte_size("2048b") == 2048

    def test_case_insensitive(self) -> None:
        assert parse_byte_size("10MB") == parse_byte_size("10mb")

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse byte size"):
            parse_byte_size("notabyte")


# ---------------------------------------------------------------------------
# parse_indexed_chars
# ---------------------------------------------------------------------------


class TestParseIndexedChars:
    def test_float_string(self) -> None:
        assert parse_indexed_chars("100000.0") == 100000

    def test_negative_means_unlimited(self) -> None:
        assert parse_indexed_chars("-1") == -1

    def test_plain_int_string(self) -> None:
        assert parse_indexed_chars("50000") == 50000


# ---------------------------------------------------------------------------
# FsSettings loading
# ---------------------------------------------------------------------------


class TestFsSettingsFromDict:
    def test_valid_minimal(self) -> None:
        data = {"name": "myjob", "fs": {"url": "/data"}}
        settings = FsSettings.from_dict(data)
        assert settings.name == "myjob"
        assert settings.fs.url == "/data"

    def test_defaults_applied(self) -> None:
        data = {"name": "myjob", "fs": {"url": "/data"}}
        settings = FsSettings.from_dict(data)
        # Check a representative set of defaults
        assert settings.fs.update_rate == parse_duration("15m")
        assert settings.fs.remove_deleted is True
        assert settings.fs.index_content is True
        assert settings.elasticsearch.bulk_size == 100
        assert settings.elasticsearch.push_templates is True

    def test_fs_url_defaults_to_tmp_es(self) -> None:
        # Java parity: fs.url is not required, defaults to /tmp/es
        settings = FsSettings.from_dict({"name": "myjob", "fs": {}})
        assert settings.fs.url == "/tmp/es"

    def test_fs_url_defaults_when_no_fs_block(self) -> None:
        settings = FsSettings.from_dict({"name": "myjob"})
        assert settings.fs.url == "/tmp/es"

    def test_name_required(self) -> None:
        with pytest.raises(FsSettingsError, match="name"):
            FsSettings.from_dict({"fs": {"url": "/data"}})

    def test_update_rate_parsed(self) -> None:
        data = {"name": "j", "fs": {"url": "/", "update_rate": "15m"}}
        settings = FsSettings.from_dict(data)
        assert settings.fs.update_rate == 900.0

    def test_ignore_above_parsed(self) -> None:
        data = {"name": "j", "fs": {"url": "/", "ignore_above": "512mb"}}
        settings = FsSettings.from_dict(data)
        assert settings.fs.ignore_above == 512 * 1024 * 1024

    def test_indexed_chars_parsed(self) -> None:
        data = {"name": "j", "fs": {"url": "/", "indexed_chars": "100000.0"}}
        settings = FsSettings.from_dict(data)
        assert settings.fs.indexed_chars == 100000

    def test_elasticsearch_nodes_as_url_dicts(self) -> None:
        data = {
            "name": "j",
            "fs": {"url": "/"},
            "elasticsearch": {
                "nodes": [{"url": "http://localhost:9200"}],
            },
        }
        settings = FsSettings.from_dict(data)
        assert settings.elasticsearch.nodes == ["http://localhost:9200"]

    def test_index_defaults_to_name_docs(self) -> None:
        data = {"name": "myjob", "fs": {"url": "/data"}}
        settings = FsSettings.from_dict(data)
        assert settings.elasticsearch.index == "myjob_docs"

    def test_index_folder_defaults_to_name_folder(self) -> None:
        data = {"name": "myjob", "fs": {"url": "/data"}}
        settings = FsSettings.from_dict(data)
        assert settings.elasticsearch.index_folder == "myjob_folder"

    def test_explicit_index_not_overridden(self) -> None:
        data = {
            "name": "myjob",
            "fs": {"url": "/data"},
            "elasticsearch": {"index": "custom_idx"},
        }
        settings = FsSettings.from_dict(data)
        assert settings.elasticsearch.index == "custom_idx"


    def test_byte_size_parsed(self) -> None:
        data = {
            "name": "j",
            "fs": {"url": "/"},
            "elasticsearch": {"byte_size": "10mb"},
        }
        settings = FsSettings.from_dict(data)
        assert settings.elasticsearch.byte_size == 10 * 1024 * 1024


class TestFsSettingsFromFile:
    def test_load_from_yaml(self, tmp_path: Path) -> None:
        content = textwrap.dedent(
            """\
            name: filejob
            fs:
              url: /some/path
              update_rate: 10m
            elasticsearch:
              nodes:
                - url: http://localhost:9200
            """
        )
        settings_file = tmp_path / "_settings.yaml"
        settings_file.write_text(content)
        settings = FsSettings.from_file(settings_file)
        assert settings.name == "filejob"
        assert settings.fs.update_rate == 600.0
        assert settings.elasticsearch.nodes == ["http://localhost:9200"]

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            FsSettings.from_file(tmp_path / "nonexistent.yaml")

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "_settings.yaml"
        bad_file.write_text("name: [\nbroken yaml")
        with pytest.raises(FsSettingsError):
            FsSettings.from_file(bad_file)


# ---------------------------------------------------------------------------
# Environment variable overrides  (FSCRAWLER_ prefix — Java parity)
# ---------------------------------------------------------------------------


class TestEnvOverrides:
    """FSCRAWLER_* env vars must override values loaded from YAML."""

    def _base_yaml(self, tmp_path: Path) -> Path:
        f = tmp_path / "_settings.yaml"
        f.write_text(
            "name: myjob\n"
            "fs:\n  url: /original\n"
            "elasticsearch:\n  nodes:\n    - url: http://original:9200\n"
        )
        return f

    def test_elasticsearch_urls_fills_missing_nodes(self, tmp_path: Path) -> None:
        """Env var sets nodes when YAML has no elasticsearch.nodes entry."""
        f = tmp_path / "_settings.yaml"
        f.write_text("name: myjob\nfs:\n  url: /data\n")  # no nodes in YAML
        env = {"FSCRAWLER_ELASTICSEARCH_URLS": "http://newhost:9200"}
        s = FsSettings.from_file(f, environ=env)
        assert s.elasticsearch.nodes == ["http://newhost:9200"]

    def test_elasticsearch_urls_multiple_comma_separated(self, tmp_path: Path) -> None:
        f = tmp_path / "_settings.yaml"
        f.write_text("name: myjob\nfs:\n  url: /data\n")  # no nodes in YAML
        env = {"FSCRAWLER_ELASTICSEARCH_URLS": "http://a:9200,http://b:9200"}
        s = FsSettings.from_file(f, environ=env)
        assert s.elasticsearch.nodes == ["http://a:9200", "http://b:9200"]

    def test_elasticsearch_username_override(self, tmp_path: Path) -> None:
        f = self._base_yaml(tmp_path)
        s = FsSettings.from_file(f, environ={"FSCRAWLER_ELASTICSEARCH_USERNAME": "admin"})
        assert s.elasticsearch.username == "admin"

    def test_elasticsearch_password_override(self, tmp_path: Path) -> None:
        f = self._base_yaml(tmp_path)
        s = FsSettings.from_file(f, environ={"FSCRAWLER_ELASTICSEARCH_PASSWORD": "secret"})
        assert s.elasticsearch.password == "secret"

    def test_elasticsearch_api_key_override(self, tmp_path: Path) -> None:
        f = self._base_yaml(tmp_path)
        s = FsSettings.from_file(f, environ={"FSCRAWLER_ELASTICSEARCH_API_KEY": "mykey=="})
        assert s.elasticsearch.api_key == "mykey=="

    def test_elasticsearch_ssl_verification_false(self, tmp_path: Path) -> None:
        f = self._base_yaml(tmp_path)
        s = FsSettings.from_file(f, environ={"FSCRAWLER_ELASTICSEARCH_SSL_VERIFICATION": "false"})
        assert s.elasticsearch.ssl_verification is False

    def test_elasticsearch_ssl_verification_true(self, tmp_path: Path) -> None:
        f = self._base_yaml(tmp_path)
        s = FsSettings.from_file(f, environ={"FSCRAWLER_ELASTICSEARCH_SSL_VERIFICATION": "true"})
        assert s.elasticsearch.ssl_verification is True

    def test_rest_url_override(self, tmp_path: Path) -> None:
        f = self._base_yaml(tmp_path)
        s = FsSettings.from_file(f, environ={"FSCRAWLER_REST_URL": "http://0.0.0.0:9090"})
        assert s.rest.url == "http://0.0.0.0:9090"

    def test_fs_url_fills_missing_url(self, tmp_path: Path) -> None:
        """Env var sets fs.url when YAML has no fs section."""
        f = tmp_path / "_settings.yaml"
        # no fs.url in YAML — env var provides it so validation still passes
        f.write_text("name: myjob\nelasticsearch:\n  nodes:\n    - url: http://localhost:9200\n")
        s = FsSettings.from_file(f, environ={"FSCRAWLER_FS_URL": "/mnt/data"})
        assert s.fs.url == "/mnt/data"

    def test_yaml_takes_precedence_over_env(self, tmp_path: Path) -> None:
        """Env vars are a fallback; explicit YAML values win."""
        f = tmp_path / "_settings.yaml"
        f.write_text(
            "name: myjob\n"
            "fs:\n  url: /from-yaml\n"
            "elasticsearch:\n"
            "  nodes:\n    - url: http://yaml-host:9200\n"
            "  username: yaml-user\n"
        )
        s = FsSettings.from_file(f, environ={"FSCRAWLER_ELASTICSEARCH_USERNAME": "env-user"})
        assert s.elasticsearch.username == "yaml-user"

    def test_elasticsearch_urls_key_accepted(self, tmp_path: Path) -> None:
        """Java uses 'elasticsearch.urls', Python uses 'nodes' — both must work."""
        f = tmp_path / "_settings.yaml"
        f.write_text("name: myjob\nelasticsearch:\n  urls:\n    - http://java-host:9200\n")
        s = FsSettings.from_file(f, environ={})
        assert s.elasticsearch.nodes == ["http://java-host:9200"]

    def test_empty_env_changes_nothing(self, tmp_path: Path) -> None:
        f = self._base_yaml(tmp_path)
        s = FsSettings.from_file(f, environ={})
        assert s.elasticsearch.nodes == ["http://original:9200"]

    def test_unrelated_env_vars_ignored(self, tmp_path: Path) -> None:
        f = self._base_yaml(tmp_path)
        s = FsSettings.from_file(f, environ={"PATH": "/usr/bin", "HOME": "/root"})
        assert s.elasticsearch.nodes == ["http://original:9200"]


class TestElasticsearchSettings:
    def test_ssl_verification_default_true(self) -> None:
        s = ElasticsearchSettings()
        assert s.ssl_verification is True

    def test_api_key_and_basic_auth_mutually_exclusive_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        with caplog.at_level(logging.WARNING, logger="fscrawler"):
            ElasticsearchSettings(username="user", password="pass", api_key="key")
        assert any("api_key" in r.message or "username" in r.message for r in caplog.records)
