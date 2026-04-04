"""Unit tests for fscrawler.wal."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


class TestWalAppend:
    def test_append_creates_file(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        wal.append({"doc_id": "abc123", "action": "index"})
        assert (tmp_path / ".wal").exists()

    def test_append_writes_jsonl(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        wal.append({"doc_id": "abc123", "action": "index", "payload": {"content": "hello"}})

        lines = (tmp_path / ".wal").read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["doc_id"] == "abc123"

    def test_append_multiple_records(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        for i in range(3):
            wal.append({"doc_id": f"doc{i}", "action": "index"})

        lines = (tmp_path / ".wal").read_text().strip().split("\n")
        assert len(lines) == 3


class TestWalRead:
    def test_read_empty(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal_path = tmp_path / ".wal"
        wal_path.write_text("")
        wal = WriteAheadLog(wal_path)
        assert wal.read() == []

    def test_read_nonexistent(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / "missing_wal")
        assert wal.read() == []

    def test_read_returns_all_records(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        for i in range(3):
            wal.append({"doc_id": f"doc{i}", "action": "index"})

        records = wal.read()
        assert len(records) == 3
        assert records[0]["doc_id"] == "doc0"
        assert records[2]["doc_id"] == "doc2"

    def test_read_skips_corrupt_lines(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal_path = tmp_path / ".wal"
        wal_path.write_text(
            '{"doc_id":"good1","action":"index"}\n'
            "this is not json\n"
            '{"doc_id":"good2","action":"index"}\n'
        )
        wal = WriteAheadLog(wal_path)
        records = wal.read()
        assert len(records) == 2
        assert records[0]["doc_id"] == "good1"
        assert records[1]["doc_id"] == "good2"


class TestWalCheckpoint:
    def test_checkpoint_removes_processed_ids(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        wal.append({"doc_id": "keep1", "action": "index"})
        wal.append({"doc_id": "remove1", "action": "index"})
        wal.append({"doc_id": "keep2", "action": "index"})

        wal.checkpoint(processed_doc_ids={"remove1"})

        records = wal.read()
        assert len(records) == 2
        ids = {r["doc_id"] for r in records}
        assert ids == {"keep1", "keep2"}

    def test_checkpoint_all_removes_everything(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        for i in range(5):
            wal.append({"doc_id": f"doc{i}", "action": "index"})

        wal.checkpoint(processed_doc_ids={f"doc{i}" for i in range(5)})

        records = wal.read()
        assert len(records) == 0

    def test_checkpoint_empty_wal_is_noop(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        wal.checkpoint(processed_doc_ids={"anything"})  # should not raise
        assert wal.read() == []

    def test_checkpoint_is_atomic(self, tmp_path: Path) -> None:
        """After checkpoint, no temp files should remain."""
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        wal.append({"doc_id": "a", "action": "index"})
        wal.checkpoint(processed_doc_ids={"a"})

        files = list(tmp_path.glob("*"))
        wal_files = [f for f in files if f.name.startswith(".wal")]
        assert len(wal_files) <= 1


class TestWalThreadSafety:
    def test_concurrent_appends(self, tmp_path: Path) -> None:
        import threading

        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        n_threads = 20

        def worker(i: int) -> None:
            wal.append({"doc_id": f"doc{i}", "action": "index"})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        records = wal.read()
        assert len(records) == n_threads


class TestWalIsEmpty:
    def test_nonexistent_is_empty(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / "missing")
        assert wal.is_empty is True

    def test_empty_file_is_empty(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal_path = tmp_path / ".wal"
        wal_path.write_text("")
        wal = WriteAheadLog(wal_path)
        assert wal.is_empty is True

    def test_nonempty_is_not_empty(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        wal.append({"doc_id": "x", "action": "index"})
        assert wal.is_empty is False


class TestWalMetrics:
    def test_append_increments_wal_records(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")

        with patch("fscrawler.wal.wal_records") as mock_counter:
            wal.append({"doc_id": "abc", "action": "index"})
            mock_counter.add.assert_called_once()
            attrs = mock_counter.add.call_args[0][1]
            assert attrs["fscrawler.wal.action"] == "append"

    def test_checkpoint_increments_wal_records(self, tmp_path: Path) -> None:
        from fscrawler.wal import WriteAheadLog

        wal = WriteAheadLog(tmp_path / ".wal")
        wal.append({"doc_id": "abc", "action": "index"})

        with patch("fscrawler.wal.wal_records") as mock_counter:
            wal.checkpoint({"abc"})
            mock_counter.add.assert_called_once()
            attrs = mock_counter.add.call_args[0][1]
            assert attrs["fscrawler.wal.action"] == "checkpoint"
