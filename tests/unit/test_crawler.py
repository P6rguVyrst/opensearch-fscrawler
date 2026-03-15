"""Unit tests for fscrawler.crawler."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from fscrawler.settings import FsSettings


def make_settings(tmp_path: Path, **fs_overrides: Any) -> FsSettings:
    fs: dict[str, Any] = {"url": str(tmp_path / "data")}
    fs.update(fs_overrides)
    return FsSettings.from_dict({"name": "test", "fs": fs})


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


class TestCrawlerDiscovery:
    def test_discovers_files_in_directory(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "a.pdf").write_bytes(b"pdf")
        (data / "b.txt").write_bytes(b"txt")

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = list(crawler.scan())
        assert len(found) == 2
        filenames = {f.name for f in found}
        assert "a.pdf" in filenames
        assert "b.txt" in filenames

    def test_discovers_files_recursively(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        sub = data / "sub"
        sub.mkdir()
        (data / "root.txt").write_bytes(b"r")
        (sub / "nested.txt").write_bytes(b"n")

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = list(crawler.scan())
        assert len(found) == 2

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        assert list(crawler.scan()) == []


# ---------------------------------------------------------------------------
# includes / excludes
# ---------------------------------------------------------------------------


class TestCrawlerFilters:
    def test_respects_includes(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "keep.pdf").write_bytes(b"pdf")
        (data / "skip.txt").write_bytes(b"txt")

        settings = make_settings(tmp_path, includes=["*.pdf"])
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [f.name for f in crawler.scan()]
        assert found == ["keep.pdf"]

    def test_respects_excludes(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "keep.pdf").write_bytes(b"pdf")
        (data / "skip.tmp").write_bytes(b"tmp")

        settings = make_settings(tmp_path, excludes=["*.tmp"])
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [f.name for f in crawler.scan()]
        assert "skip.tmp" not in found
        assert "keep.pdf" in found

    def test_includes_and_excludes_combined(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "keep.pdf").write_bytes(b"pdf")
        (data / "also_pdf.pdf").write_bytes(b"pdf2")
        (data / "skip.txt").write_bytes(b"txt")

        settings = make_settings(tmp_path, includes=["*.pdf"], excludes=["also_pdf.pdf"])
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [f.name for f in crawler.scan()]
        assert found == ["keep.pdf"]


# ---------------------------------------------------------------------------
# ignore_above
# ---------------------------------------------------------------------------


class TestCrawlerIgnoreAbove:
    def test_skips_files_above_limit(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        small = data / "small.txt"
        big = data / "big.txt"
        small.write_bytes(b"x" * 10)
        big.write_bytes(b"x" * 200)

        # ignore_above = 100 bytes as string
        settings = make_settings(tmp_path, ignore_above="100b")
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [f.name for f in crawler.scan()]
        assert "small.txt" in found
        assert "big.txt" not in found

    def test_includes_files_at_limit(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        exact = data / "exact.txt"
        exact.write_bytes(b"x" * 100)

        settings = make_settings(tmp_path, ignore_above="100b")
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [f.name for f in crawler.scan()]
        assert "exact.txt" in found


# ---------------------------------------------------------------------------
# Checkpoint / incremental crawl
# ---------------------------------------------------------------------------


class TestCrawlerCheckpoint:
    def test_checkpoint_is_saved_after_scan(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "file.txt").write_bytes(b"hello")

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        list(crawler.scan())
        crawler.save_checkpoint()

        checkpoint_file = tmp_path / ".fscrawler_checkpoint.json"
        assert checkpoint_file.exists()
        data_cp = json.loads(checkpoint_file.read_text())
        assert any("file.txt" in k for k in data_cp)

    def test_checkpoint_is_loaded_on_next_run(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        file_path = data / "file.txt"
        file_path.write_bytes(b"hello")

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        # First run
        c1 = LocalCrawler(settings, config_dir=tmp_path)
        list(c1.scan())
        c1.save_checkpoint()

        # Second run — file unchanged
        c2 = LocalCrawler(settings, config_dir=tmp_path)
        new_files = [f for f in c2.scan() if c2.is_new_or_modified(f)]
        assert len(new_files) == 0

    def test_modified_file_detected(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        file_path = data / "file.txt"
        file_path.write_bytes(b"original")

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        # First run
        c1 = LocalCrawler(settings, config_dir=tmp_path)
        list(c1.scan())
        c1.save_checkpoint()

        # Modify the file (bump mtime)
        time.sleep(0.05)
        file_path.write_bytes(b"modified")
        # Ensure mtime actually differs by touching
        new_mtime = file_path.stat().st_mtime + 1
        os.utime(file_path, (new_mtime, new_mtime))

        # Second run — file should be detected as modified
        c2 = LocalCrawler(settings, config_dir=tmp_path)
        modified = [f for f in c2.scan() if c2.is_new_or_modified(f)]
        assert len(modified) == 1
        assert modified[0].name == "file.txt"

    def test_deleted_files_detected(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        file_path = data / "to_delete.txt"
        file_path.write_bytes(b"bye")

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        # First run — checkpoint includes the file
        c1 = LocalCrawler(settings, config_dir=tmp_path)
        list(c1.scan())
        c1.save_checkpoint()

        # Delete the file
        file_path.unlink()

        # Second run — deleted files should be reported
        c2 = LocalCrawler(settings, config_dir=tmp_path)
        list(c2.scan())
        deleted = c2.get_deleted_files()
        assert any("to_delete.txt" in str(p) for p in deleted)

    def test_remove_deleted_false_does_not_report_deleted(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        file_path = data / "stay.txt"
        file_path.write_bytes(b"keep")

        settings = make_settings(tmp_path, remove_deleted=False)
        from fscrawler.crawler import LocalCrawler

        c1 = LocalCrawler(settings, config_dir=tmp_path)
        list(c1.scan())
        c1.save_checkpoint()

        file_path.unlink()

        c2 = LocalCrawler(settings, config_dir=tmp_path)
        list(c2.scan())
        deleted = c2.get_deleted_files()
        assert deleted == []


# ---------------------------------------------------------------------------
# Symlinks
# ---------------------------------------------------------------------------


class TestCrawlerSymlinks:
    def test_follow_symlinks_true(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        real = tmp_path / "real.txt"
        real.write_bytes(b"real")
        link = data / "link.txt"
        link.symlink_to(real)

        settings = make_settings(tmp_path, follow_symlinks=True)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [f.name for f in crawler.scan()]
        assert "link.txt" in found

    def test_follow_symlinks_false(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        real = tmp_path / "real.txt"
        real.write_bytes(b"real")
        link = data / "link.txt"
        link.symlink_to(real)

        settings = make_settings(tmp_path, follow_symlinks=False)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [f.name for f in crawler.scan()]
        assert "link.txt" not in found


# ---------------------------------------------------------------------------
# Folder scanning
# ---------------------------------------------------------------------------


class TestScanFolders:
    def test_yields_root_and_subdirectories(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "sub1").mkdir()
        (data / "sub1" / "deep").mkdir()
        (data / "sub2").mkdir()

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [str(p) for p in crawler.scan_folders()]
        assert str(data) in found
        assert str(data / "sub1") in found
        assert str(data / "sub1" / "deep") in found
        assert str(data / "sub2") in found

    def test_yields_nothing_when_index_folders_false(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "sub").mkdir()

        settings = make_settings(tmp_path, index_folders=False)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        assert list(crawler.scan_folders()) == []

    def test_virtual_path_of_root_is_slash(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler
        from pathlib import Path as _Path

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        folders = list(crawler.scan_folders())
        root_folder = next(f for f in folders if f == _Path(settings.fs.url))
        rel = root_folder.relative_to(_Path(settings.fs.url))
        assert str(rel) == "."  # caller maps "." → "/"

    def test_does_not_yield_files(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        data.mkdir()
        (data / "file.txt").write_bytes(b"hello")
        (data / "sub").mkdir()

        settings = make_settings(tmp_path)
        from fscrawler.crawler import LocalCrawler

        crawler = LocalCrawler(settings, config_dir=tmp_path)
        for folder in crawler.scan_folders():
            assert folder.is_dir()
