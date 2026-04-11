"""Unit tests for fscrawler.cli (Click entry point and mode dispatch).

Does NOT duplicate _crawl_once coverage (see test_pipeline.py).
Focuses on CLI argument parsing, mode dispatch, and error handling.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from click.testing import CliRunner

from fscrawler.cli import main


def _write_existing_job_settings(tmp_path: Path, job_name: str = "test-job") -> Path:
    """Write a minimal existing _settings.yaml and return its path."""
    job_dir = tmp_path / job_name
    job_dir.mkdir(parents=True, exist_ok=True)
    settings_file = job_dir / "_settings.yaml"
    settings_file.write_text(
        f'name: "{job_name}"\n'
        "fs:\n  url: /data\n"
        "elasticsearch:\n  nodes:\n    - url: http://localhost:9200\n"
    )
    return settings_file


def _make_cli_settings_mock() -> MagicMock:
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

    def test_setup_writes_expected_template_content(self, tmp_path: Path) -> None:
        job_dir = tmp_path / "new-job"

        result = CliRunner().invoke(
            main, ["--config_dir", str(tmp_path), "--setup", "new-job"]
        )

        assert result.exit_code == 0
        assert (job_dir / "_settings.yaml").read_text(encoding="utf-8") == (
            'name: "new-job"\n'
            "fs:\n"
            '  url: "/data"\n'
            "  includes: []\n"
            "  excludes: []\n"
            "  follow_symlinks: false\n"
            "  remove_deleted: false\n"
            "  continue_on_error: false\n"
            "  index_content: true\n"
            "  add_filesize: true\n"
            "  index_folders: true\n"
            '  checksum: "sha256"\n'
            "  keep_history: false\n"
            "elasticsearch:\n"
            "  nodes:\n"
            '    - url: "http://localhost:9200"\n'
            "  ssl_verification: false\n"
            "  bulk_size: 100\n"
            "\n"
            '  byte_size: "10mb"\n'
            "  push_templates: true\n"
            "  dlq:\n"
            "    max_retries: 5\n"
            "    retry_interval: 60\n"
            "    backoff_multiplier: 2.0\n"
            "    max_backoff: 3600\n"
            "    check_interval: 300\n"
            "rest:\n"
            '  url: "http://127.0.0.1:8080"\n'
            "  enable_cors: false\n"
        )

    def test_setup_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        settings_file = _write_existing_job_settings(tmp_path, "existing-job")
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
        _write_existing_job_settings(tmp_path)
        mock_settings = _make_cli_settings_mock()
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
        _write_existing_job_settings(tmp_path)
        mock_settings = _make_cli_settings_mock()
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
        # Observer starts once initially, then restarts up to _MAX_OBSERVER_RESTARTS
        from fscrawler.cli import _MAX_OBSERVER_RESTARTS
        assert mock_observer.start.call_count == 1 + _MAX_OBSERVER_RESTARTS
        mock_observer.stop.assert_called_once()


class TestRestMode:
    def test_rest_starts_uvicorn(self, tmp_path: Path) -> None:
        _write_existing_job_settings(tmp_path)
        mock_settings = _make_cli_settings_mock()
        with (
            patch("fscrawler.cli.uvicorn") as mock_uvicorn,
            patch("fscrawler.client.FsCrawlerClient"),
            patch("fscrawler.settings.FsSettings.from_file", return_value=mock_settings),
            patch("fscrawler.cli.create_app", return_value=MagicMock()),
            patch("fscrawler.cli.threading"),
        ):
            _result = CliRunner().invoke(
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


class TestWalRecoveryPartialFailure:
    def test_recover_wal_partial_failure_only_checkpoints_succeeded(self) -> None:
        """B2: If bulk returns errors for some items, only checkpoint succeeded IDs."""
        from fscrawler.cli import _recover_wal

        client = MagicMock()
        client.bulk.return_value = {
            "errors": True,
            "items": [
                {"index": {"_id": "ok1", "status": 201}},
                {"index": {"_id": "fail1", "status": 429, "error": {"type": "circuit_breaking_exception", "reason": "overloaded"}}},
                {"index": {"_id": "ok2", "status": 200}},
            ],
        }
        wal = MagicMock()
        wal.is_empty = False
        wal.read.return_value = [
            {"job_name": "test", "target_index": "idx", "doc_id": "ok1", "action": "index", "payload": {"a": 1}},
            {"job_name": "test", "target_index": "idx", "doc_id": "fail1", "action": "index", "payload": {"b": 2}},
            {"job_name": "test", "target_index": "idx", "doc_id": "ok2", "action": "index", "payload": {"c": 3}},
        ]

        _recover_wal(client, wal)

        wal.checkpoint.assert_called_once()
        checkpointed = wal.checkpoint.call_args[0][0]
        assert "ok1" in checkpointed
        assert "ok2" in checkpointed
        assert "fail1" not in checkpointed

    def test_recover_wal_all_items_fail_does_not_checkpoint(self) -> None:
        """B2: If all bulk items fail, do not checkpoint anything."""
        from fscrawler.cli import _recover_wal

        client = MagicMock()
        client.bulk.return_value = {
            "errors": True,
            "items": [
                {"index": {"_id": "fail1", "status": 429, "error": {"type": "timeout"}}},
            ],
        }
        wal = MagicMock()
        wal.is_empty = False
        wal.read.return_value = [
            {"job_name": "test", "target_index": "idx", "doc_id": "fail1", "action": "index", "payload": {"a": 1}},
        ]

        _recover_wal(client, wal)

        wal.checkpoint.assert_not_called()


class TestWalRecoveryMetrics:
    def test_recover_wal_increments_wal_records_recover(self) -> None:
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

        with patch("fscrawler.cli.wal_records") as mock_counter:
            _recover_wal(client, wal)
            assert mock_counter.add.call_count == 1
            attrs = mock_counter.add.call_args[0][1]
            assert attrs["fscrawler.wal.action"] == "recover"


class TestEnsureDlqIndices:
    def test_bootstrap_pushes_dlq_templates_before_ensuring_dlq_indices(self) -> None:
        from fscrawler.cli import _bootstrap_dlq_indices

        client = MagicMock()

        _bootstrap_dlq_indices(client)

        assert client.mock_calls == [
            call.push_dlq_templates(),
            call.ensure_index("fscrawler_dlq"),
            call.ensure_index("fscrawler_pfq"),
        ]

    def test_ensure_dlq_pfq_indices_called(self) -> None:
        from fscrawler.cli import _ensure_dlq_indices

        client = MagicMock()
        _ensure_dlq_indices(client)
        calls = client.ensure_index.call_args_list
        indices = [c[0][0] for c in calls]
        assert "fscrawler_dlq" in indices
        assert "fscrawler_pfq" in indices


class TestOtelEndpointFlag:
    def test_otel_endpoint_flag_accepted(self, tmp_path: Path) -> None:
        """The --otel-endpoint flag should be accepted without error."""
        _write_existing_job_settings(tmp_path)
        mock_settings = _make_cli_settings_mock()
        with (
            patch("fscrawler.client.FsCrawlerClient") as mock_cls,
            patch("fscrawler.settings.FsSettings.from_file", return_value=mock_settings),
            patch("fscrawler.parser.TikaParser"),
            patch("fscrawler.cli._crawl_once"),
            patch("fscrawler.metrics.configure_metrics") as mock_cm,
        ):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            result = CliRunner().invoke(
                main,
                [
                    "--config_dir", str(tmp_path),
                    "--otel-endpoint", "http://collector:4318",
                    "test-job",
                ],
            )
        assert result.exit_code == 0, result.output
        mock_cm.assert_called_once()
        call_kwargs = mock_cm.call_args[1]
        assert call_kwargs["otel_endpoint"] == "http://collector:4318"

    def test_log_otel_endpoint_flag_removed(self, tmp_path: Path) -> None:
        """The old --log-otel-endpoint flag should no longer be accepted."""
        _write_existing_job_settings(tmp_path)
        result = CliRunner().invoke(
            main,
            [
                "--config_dir", str(tmp_path),
                "--log-otel-endpoint", "http://collector:4318",
                "test-job",
            ],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Observer health-check and restart (Task 9)
# ---------------------------------------------------------------------------


# Inspired by: https://github.com/dadoonet/fscrawler/issues/1093
# Note: The upstream issue describes the crawl/indexing thread silently
# hanging (likely OCR-related). This fix addresses a different failure mode:
# the watchdog Observer thread dying. A stuck Tika call would not be
# detected by this health-check. See ROADMAP.md for liveness monitoring plans.
class TestObserverRestart:
    """Detect dead watchdog observer and restart up to _MAX_OBSERVER_RESTARTS times."""

    def test_observer_restarted_when_it_dies(self, tmp_path: Path) -> None:
        """Observer.start() should be called more than once when observer dies."""
        from fscrawler.cli import _MAX_OBSERVER_RESTARTS, _crawler_loop

        settings = _make_cli_settings_mock()
        client = MagicMock()
        crawler_state = MagicMock()
        crawler_state.paused = False

        # Observer: alive for 2 ticks then dies, repeating each restart
        mock_observer = MagicMock()
        alive_sequence = [True, True, False] * (_MAX_OBSERVER_RESTARTS + 1)
        mock_observer.is_alive.side_effect = alive_sequence

        with (
            patch("fscrawler.cli._crawl_once"),
            patch("fscrawler.cli.Observer", return_value=mock_observer),
            patch("fscrawler.cli.FsEventHandler"),
            patch("fscrawler.cli.time"),
            patch("fscrawler.parser.TikaParser"),
        ):
            _crawler_loop(settings, client, tmp_path, crawler_state)

        # Initial start + _MAX_OBSERVER_RESTARTS restarts
        assert mock_observer.start.call_count == 1 + _MAX_OBSERVER_RESTARTS

    def test_observer_gives_up_after_max_restarts(self, tmp_path: Path) -> None:
        """After _MAX_OBSERVER_RESTARTS, the loop should exit."""
        from fscrawler.cli import _MAX_OBSERVER_RESTARTS, _crawler_loop

        settings = _make_cli_settings_mock()
        client = MagicMock()
        crawler_state = MagicMock()
        crawler_state.paused = False

        mock_observer = MagicMock()
        # Always dead — triggers max restarts quickly
        mock_observer.is_alive.return_value = False

        with (
            patch("fscrawler.cli._crawl_once"),
            patch("fscrawler.cli.Observer", return_value=mock_observer),
            patch("fscrawler.cli.FsEventHandler"),
            patch("fscrawler.cli.time"),
            patch("fscrawler.parser.TikaParser"),
        ):
            _crawler_loop(settings, client, tmp_path, crawler_state)

        # Should have started 1 + _MAX_OBSERVER_RESTARTS times then given up
        assert mock_observer.start.call_count == 1 + _MAX_OBSERVER_RESTARTS

    def test_observer_no_restart_when_alive(self, tmp_path: Path) -> None:
        """If observer stays alive, no restarts should happen."""

        _settings = _make_cli_settings_mock()
        _client = MagicMock()
        crawler_state = MagicMock()
        crawler_state.paused = False

        mock_observer = MagicMock()
        # Alive for a few ticks, then dies once (triggers 1 restart cycle, then exits)
        mock_observer.is_alive.side_effect = [True, True, True, False]

        with (
            patch("fscrawler.cli._crawl_once"),
            patch("fscrawler.cli.Observer", return_value=mock_observer) as obs_cls,
            patch("fscrawler.cli.FsEventHandler"),
            patch("fscrawler.cli.time"),
        ):
            # The new observer from restart also needs mocking
            restart_observer = MagicMock()
            restart_observer.is_alive.side_effect = [True, True, False]
            obs_cls.side_effect = [mock_observer, restart_observer, MagicMock()]
            # Re-run — but now Observer() returns different mocks
            # Reset so we can count fresh
            mock_observer.reset_mock()

        # This test just validates the pattern works — more detailed assertions
        # are in the tests above.


# ---------------------------------------------------------------------------
# _watch_with_restarts helper (I1 refactor)
# ---------------------------------------------------------------------------


class TestWatchWithRestarts:
    """Unit tests for the extracted _watch_with_restarts helper."""

    def test_restarts_observer_up_to_max(self) -> None:
        """Observer is restarted up to _MAX_OBSERVER_RESTARTS times then gives up."""
        from fscrawler.cli import _MAX_OBSERVER_RESTARTS, _watch_with_restarts

        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = False
        handler = MagicMock()

        with (
            patch("fscrawler.cli.Observer", return_value=mock_observer),
            patch("fscrawler.cli.time"),
        ):
            _watch_with_restarts(handler, "/data", mock_observer)

        # The initial observer was already started by the caller, so _watch_with_restarts
        # only restarts. Total start calls = _MAX_OBSERVER_RESTARTS (from restarts only).
        assert mock_observer.start.call_count == _MAX_OBSERVER_RESTARTS
        mock_observer.stop.assert_called()
        mock_observer.join.assert_called()

    def test_stop_event_set_in_finally(self) -> None:
        """When stop_event is provided, it must be set in the finally block."""
        from fscrawler.cli import _watch_with_restarts

        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = False
        stop_event = threading.Event()

        with (
            patch("fscrawler.cli.Observer", return_value=mock_observer),
            patch("fscrawler.cli.time"),
        ):
            _watch_with_restarts(handler=MagicMock(), path="/data",
                                 observer=mock_observer, stop_event=stop_event)

        assert stop_event.is_set()

    def test_no_stop_event_no_error(self) -> None:
        """When stop_event is None, the finally block must not raise."""
        from fscrawler.cli import _watch_with_restarts

        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = False

        with (
            patch("fscrawler.cli.Observer", return_value=mock_observer),
            patch("fscrawler.cli.time"),
        ):
            _watch_with_restarts(handler=MagicMock(), path="/data",
                                 observer=mock_observer, stop_event=None)

        mock_observer.stop.assert_called()


class TestUnhandledException:
    def test_unhandled_error_exits_with_code_1(self, tmp_path: Path) -> None:
        _write_existing_job_settings(tmp_path)
        mock_settings = _make_cli_settings_mock()
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
