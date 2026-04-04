"""Unit tests for fscrawler.cli (Click entry point and mode dispatch).

Does NOT duplicate _crawl_once coverage (see test_pipeline.py).
Focuses on CLI argument parsing, mode dispatch, and error handling.
"""

from __future__ import annotations

import threading
import time
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


class TestWalRecovery:
    def test_recover_wal_replays_records(self) -> None:
        from fscrawler.cli import _recover_wal

        client = MagicMock()
        client.bulk.return_value = {"errors": False, "items": []}
        wal = MagicMock()
        wal.is_empty = False
        wal.read.return_value = [
            {
                "job_name": "test",
                "target_index": "fscrawler_docs_test",
                "doc_id": "abc123",
                "action": "index",
                "payload": {"content": "hello"},
            },
        ]

        _recover_wal(client, wal)
        client.bulk.assert_called_once()
        wal.checkpoint.assert_called_once()

    def test_recover_wal_skips_when_empty(self) -> None:
        from fscrawler.cli import _recover_wal

        client = MagicMock()
        wal = MagicMock()
        wal.is_empty = True

        _recover_wal(client, wal)
        client.bulk.assert_not_called()

    def test_recover_wal_handles_delete_records(self) -> None:
        from fscrawler.cli import _recover_wal

        client = MagicMock()
        client.bulk.return_value = {"errors": False, "items": []}
        wal = MagicMock()
        wal.is_empty = False
        wal.read.return_value = [
            {
                "job_name": "test",
                "target_index": "fscrawler_docs_test",
                "doc_id": "del1",
                "action": "delete",
            },
        ]

        _recover_wal(client, wal)
        client.bulk.assert_called_once()
        ops = client.bulk.call_args[0][0]
        assert any("delete" in str(op) for op in ops)

    def test_recover_wal_bulk_failure_does_not_checkpoint(self) -> None:
        from fscrawler.cli import _recover_wal

        client = MagicMock()
        client.bulk.side_effect = Exception("cluster down")
        wal = MagicMock()
        wal.is_empty = False
        wal.read.return_value = [
            {
                "job_name": "test",
                "target_index": "fscrawler_docs_test",
                "doc_id": "abc",
                "action": "index",
                "payload": {"content": "hello"},
            },
        ]

        _recover_wal(client, wal)
        wal.checkpoint.assert_not_called()

    def test_recover_wal_with_pipeline(self) -> None:
        from fscrawler.cli import _recover_wal

        client = MagicMock()
        client.bulk.return_value = {"errors": False, "items": []}
        wal = MagicMock()
        wal.is_empty = False
        wal.read.return_value = [
            {
                "job_name": "test",
                "target_index": "fscrawler_docs_test_vector",
                "doc_id": "vec1",
                "action": "index",
                "payload": {"content": "hello"},
                "pipeline": "fscrawler_vector_pipeline",
            },
        ]

        _recover_wal(client, wal)
        ops = client.bulk.call_args[0][0]
        assert ops[0]["index"]["pipeline"] == "fscrawler_vector_pipeline"


class TestDlqRetryThread:
    def test_dlq_thread_calls_run_retry_cycle(self) -> None:
        from fscrawler.cli import _start_dlq_retry_thread

        client = MagicMock()
        config = MagicMock(check_interval=0.01, max_retries=5)
        stop_event = threading.Event()

        with patch("fscrawler.cli.run_retry_cycle") as mock_cycle:
            thread = _start_dlq_retry_thread(client, config, stop_event)
            time.sleep(0.05)
            stop_event.set()
            thread.join(timeout=1)

        assert mock_cycle.called

    def test_dlq_thread_passes_job_name(self) -> None:
        from fscrawler.cli import _start_dlq_retry_thread

        client = MagicMock()
        config = MagicMock(check_interval=0.01)
        stop_event = threading.Event()

        with patch("fscrawler.cli.run_retry_cycle") as mock_cycle:
            thread = _start_dlq_retry_thread(client, config, stop_event, job_name="myjob")
            time.sleep(0.05)
            stop_event.set()
            thread.join(timeout=1)

        if mock_cycle.called:
            _, kwargs = mock_cycle.call_args
            assert kwargs.get("job_name") == "myjob"

    def test_dlq_thread_survives_exception(self) -> None:
        from fscrawler.cli import _start_dlq_retry_thread

        client = MagicMock()
        config = MagicMock(check_interval=0.01)
        stop_event = threading.Event()

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("transient error")

        with patch("fscrawler.cli.run_retry_cycle", side_effect=side_effect):
            thread = _start_dlq_retry_thread(client, config, stop_event)
            time.sleep(0.05)
            stop_event.set()
            thread.join(timeout=1)

        assert call_count >= 2


class TestEnsureDlqIndices:
    def test_ensure_dlq_pfq_indices_called(self) -> None:
        from fscrawler.cli import _ensure_dlq_indices

        client = MagicMock()
        _ensure_dlq_indices(client)
        calls = client.ensure_index.call_args_list
        indices = [c[0][0] for c in calls]
        assert "fscrawler_dlq" in indices
        assert "fscrawler_pfq" in indices


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
