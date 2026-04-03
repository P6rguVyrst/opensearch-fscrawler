"""Unit tests for fscrawler.cli (Click entry point and mode dispatch).

Does NOT duplicate _crawl_once coverage (see test_pipeline.py).
Focuses on CLI argument parsing, mode dispatch, and error handling.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from fscrawler.cli import main


def _write_settings(tmp_path: Path, job_name: str = "test-job") -> Path:
    """Write a minimal _settings.yaml and return its path."""
    job_dir = tmp_path / job_name
    job_dir.mkdir(parents=True, exist_ok=True)
    settings_file = job_dir / "_settings.yaml"
    settings_file.write_text(
        f'name: "{job_name}"\n'
        "fs:\n  url: /data\n"
        "elasticsearch:\n  nodes:\n    - url: http://localhost:9200\n"
    )
    return settings_file


def _mock_settings() -> MagicMock:
    """Return a MagicMock that behaves like FsSettings."""
    settings = MagicMock()
    settings.name = "test-job"
    settings.fs.url = "/data"
    settings.fs.tika_url = None
    settings.fs.keep_history = False
    settings.elasticsearch.index = "test-job"
    settings.elasticsearch.index_folder = "test-job_folder"
    settings.elasticsearch.index_history = "test-job_history"
    settings.rest.url = "http://127.0.0.1:8080"
    return settings


class TestSetupMode:
    def test_setup_creates_settings_file(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "new-job"
        result = CliRunner().invoke(
            main, ["--config_dir", str(tmp_path), "--setup", "new-job"]
        )
        assert result.exit_code == 0
        assert (job_dir / "_settings.yaml").exists()

    def test_setup_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        settings_file = _write_settings(tmp_path, "existing-job")
        original_content = settings_file.read_text()

        result = CliRunner().invoke(
            main, ["--config_dir", str(tmp_path), "--setup", "existing-job"]
        )
        assert result.exit_code == 0
        assert settings_file.read_text() == original_content
        assert "already exists" in result.output


class TestMissingSettings:
    def test_missing_settings_exits_with_code_1(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            main, ["--config_dir", str(tmp_path), "no-such-job"]
        )
        assert result.exit_code == 1


class TestRunMode:
    def test_run_calls_crawl_once(self, tmp_path: Path) -> None:
        _write_settings(tmp_path)
        mock_settings = _mock_settings()
        with (
            patch("fscrawler.client.FsCrawlerClient") as mock_cls,
            patch("fscrawler.settings.FsSettings.from_file", return_value=mock_settings),
            patch("fscrawler.parser.TikaParser"),
            patch("fscrawler.cli._crawl_once") as mock_crawl,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            result = CliRunner().invoke(
                main, ["--config_dir", str(tmp_path), "test-job"]
            )
        assert result.exit_code == 0, result.output
        mock_crawl.assert_called_once()

    def test_loop_starts_observer(self, tmp_path: Path) -> None:
        _write_settings(tmp_path)
        mock_settings = _mock_settings()
        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = False

        with (
            patch("fscrawler.client.FsCrawlerClient"),
            patch("fscrawler.settings.FsSettings.from_file", return_value=mock_settings),
            patch("fscrawler.parser.TikaParser"),
            patch("fscrawler.cli._crawl_once"),
            patch("fscrawler.cli.Observer", return_value=mock_observer),
            patch("fscrawler.cli.FsEventHandler"),
            patch("fscrawler.cli.time"),
        ):
            result = CliRunner().invoke(
                main, ["--config_dir", str(tmp_path), "--loop", "test-job"]
            )
        assert result.exit_code == 0, result.output
        mock_observer.start.assert_called_once()
        mock_observer.stop.assert_called_once()


class TestRestMode:
    def test_rest_starts_uvicorn(self, tmp_path: Path) -> None:
        _write_settings(tmp_path)
        mock_settings = _mock_settings()
        with (
            patch("fscrawler.cli.uvicorn") as mock_uvicorn,
            patch("fscrawler.client.FsCrawlerClient"),
            patch("fscrawler.settings.FsSettings.from_file", return_value=mock_settings),
            patch("fscrawler.cli.create_app", return_value=MagicMock()),
            patch("fscrawler.cli.threading"),
        ):
            result = CliRunner().invoke(
                main, ["--config_dir", str(tmp_path), "--rest", "test-job"]
            )
        assert mock_uvicorn.run.called


class TestUnhandledException:
    def test_unhandled_error_exits_with_code_1(self, tmp_path: Path) -> None:
        _write_settings(tmp_path)
        mock_settings = _mock_settings()
        with (
            patch("fscrawler.client.FsCrawlerClient"),
            patch("fscrawler.settings.FsSettings.from_file", return_value=mock_settings),
            patch("fscrawler.parser.TikaParser"),
            patch("fscrawler.cli._crawl_once", side_effect=RuntimeError("boom")),
        ):
            result = CliRunner().invoke(
                main, ["--config_dir", str(tmp_path), "test-job"]
            )
        assert result.exit_code == 1
