# Licensed under the Apache License, Version 2.0
"""Local filesystem crawler for FSCrawler."""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import stat as stat_module
import unicodedata
from collections.abc import Iterator
from pathlib import Path

from fscrawler.settings import FsSettings

logger = logging.getLogger("fscrawler.crawler")

_CHECKPOINT_FILENAME = ".fscrawler_checkpoint.json"
_IGNORE_SENTINEL = ".fscrawlerignore"
_CLOCK_SKEW_SECONDS = 2.0


def _normalize_name(name: str) -> str:
    """Normalize filename to NFC for cross-platform consistency."""
    return unicodedata.normalize("NFC", name)


def _matches_pattern(name: str, virtual_path: str, pattern: str) -> bool:
    """Match *pattern* against virtual path (if pattern contains '/') or filename."""
    if "/" in pattern:
        return fnmatch.fnmatch(virtual_path.lstrip("/"), pattern)
    return fnmatch.fnmatch(name, pattern)


class LocalCrawler:
    """Walk a local directory tree and detect new, modified, and deleted files."""

    def __init__(self, settings: FsSettings, config_dir: Path) -> None:
        self._settings = settings
        self._config_dir = config_dir
        self._root = Path(settings.fs.url)
        self._checkpoint_file = config_dir / _CHECKPOINT_FILENAME

        # Loaded at construction time from the previous run
        self._previous_checkpoint: dict[str, float] = self._load_checkpoint()
        # Built during the current scan
        self._current_checkpoint: dict[str, float] = {}
        # Track visited (dev, inode) pairs to detect symlink cycles
        self._visited: set[tuple[int, int]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> Iterator[Path]:
        """Yield Path objects for every eligible file under fs.url."""
        self._current_checkpoint = {}
        self._visited = set()
        try:
            root_stat = self._root.stat()
            self._visited.add((root_stat.st_dev, root_stat.st_ino))
        except OSError:
            pass

        for entry in self._walk(self._root):
            path = Path(entry)

            # Record mtime for checkpointing
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue

            self._current_checkpoint[unicodedata.normalize("NFC", str(path))] = mtime
            yield path

    def exceeds_size_limit(self, path: Path) -> bool:
        """Check if file exceeds ignore_above threshold."""
        if self._settings.fs.ignore_above is None:
            return False
        try:
            return path.stat().st_size > self._settings.fs.ignore_above
        except OSError:
            return False

    def is_new_or_modified(self, path: Path) -> bool:
        """Return True if the file is new or has been modified since the last checkpoint.

        A tolerance of ``_CLOCK_SKEW_SECONDS`` is applied so that files whose
        mtime drifted slightly backward (e.g. NFS/CIFS clock skew) are still
        picked up.
        """
        key = unicodedata.normalize("NFC", str(path))
        if key not in self._previous_checkpoint:
            return True
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            return False
        previous_mtime = self._previous_checkpoint[key]
        if current_mtime == previous_mtime:
            return False  # unchanged
        if current_mtime > previous_mtime:
            return True  # clearly modified
        # current_mtime < previous_mtime — clock may have drifted backward
        return (previous_mtime - current_mtime) <= self._settings.fs.clock_skew_seconds

    def get_deleted_files(self) -> list[str]:
        """Return virtual paths of files deleted since the last checkpoint.

        Virtual paths are relative to the crawl root with a leading slash,
        e.g. ``/subdir/report.pdf``.

        Only meaningful to call after ``scan()`` has been called.
        Returns an empty list when ``remove_deleted`` is False.
        """
        if not self._settings.fs.remove_deleted:
            return []
        deleted: list[str] = []
        for abs_path in self._previous_checkpoint:
            if abs_path not in self._current_checkpoint:
                try:
                    virtual = "/" + str(Path(abs_path).relative_to(self._root))
                except ValueError:
                    virtual = "/" + Path(abs_path).name
                deleted.append(virtual)
        return deleted

    def scan_folders(self) -> Iterator[Path]:
        """Yield all directories under fs.url (including the root itself)."""
        if not self._settings.fs.index_folders:
            return
        self._visited = set()
        try:
            root_stat = self._root.stat()
            self._visited.add((root_stat.st_dev, root_stat.st_ino))
        except OSError:
            pass
        yield self._root
        yield from self._walk_dirs(self._root)

    def save_checkpoint(self) -> None:
        """Persist the current checkpoint to disk."""
        self._checkpoint_file.write_text(
            json.dumps(self._current_checkpoint, indent=2),
            encoding="utf-8",
        )
        logger.debug("Checkpoint saved to %s", self._checkpoint_file)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_checkpoint(self) -> dict[str, float]:
        if not self._checkpoint_file.exists():
            return {}
        try:
            data = json.loads(self._checkpoint_file.read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Could not load checkpoint file %s: %s", self._checkpoint_file, exc)
            return {}

    def _walk_dirs(self, root: Path) -> Iterator[Path]:
        """Recursively yield subdirectories, respecting follow_symlinks and continue_on_error."""
        if (root / _IGNORE_SENTINEL).exists():
            logger.debug("Skipping %s — .fscrawlerignore found", root)
            return
        fs = self._settings.fs
        try:
            entries = list(os.scandir(root))
        except PermissionError as exc:
            if fs.continue_on_error:
                logger.warning("Permission denied scanning %s: %s", root, exc)
                return
            raise
        for entry in sorted(entries, key=lambda e: e.name):
            if entry.is_symlink() and not fs.follow_symlinks:
                continue
            if entry.is_dir(follow_symlinks=fs.follow_symlinks):
                try:
                    dir_stat = entry.stat(follow_symlinks=True)
                    dir_key = (dir_stat.st_dev, dir_stat.st_ino)
                except OSError:
                    continue
                if dir_key in self._visited:
                    logger.warning("Symlink cycle detected, skipping: %s", entry.path)
                    continue
                self._visited.add(dir_key)
                child = Path(entry.path)
                if (child / _IGNORE_SENTINEL).exists():
                    logger.debug("Skipping %s — .fscrawlerignore found", child)
                    continue
                yield child
                yield from self._walk_dirs(child)

    def _walk(self, root: Path) -> Iterator[str]:  # noqa: C901
        """Recursively walk root, applying includes/excludes filters."""
        if (root / _IGNORE_SENTINEL).exists():
            logger.debug("Skipping %s — .fscrawlerignore found", root)
            return
        fs = self._settings.fs

        try:
            entries = list(os.scandir(root))
        except PermissionError as exc:
            if fs.continue_on_error:
                logger.warning("Permission denied scanning %s: %s", root, exc)
                return
            raise

        for entry in sorted(entries, key=lambda e: e.name):
            if entry.is_symlink() and not fs.follow_symlinks:
                logger.debug("Skipping symlink: %s", entry.path)
                continue

            if entry.is_dir(follow_symlinks=fs.follow_symlinks):
                try:
                    dir_stat = entry.stat(follow_symlinks=True)
                    dir_key = (dir_stat.st_dev, dir_stat.st_ino)
                except OSError:
                    continue
                if dir_key in self._visited:
                    logger.warning("Symlink cycle detected, skipping: %s", entry.path)
                    continue
                self._visited.add(dir_key)
                yield from self._walk(Path(entry.path))
            elif entry.is_file(follow_symlinks=fs.follow_symlinks):
                name = _normalize_name(entry.name)
                virtual_path = "/" + Path(entry.path).relative_to(self._root).as_posix()

                # includes: if set, file must match at least one pattern
                if fs.includes and not any(
                    _matches_pattern(name, virtual_path, pat) for pat in fs.includes
                ):
                    continue

                # excludes: file must not match any pattern
                if any(_matches_pattern(name, virtual_path, pat) for pat in fs.excludes):
                    continue

                yield entry.path
            else:
                # Detect special files (pipes, sockets, devices) and warn
                try:
                    mode = entry.stat(follow_symlinks=fs.follow_symlinks).st_mode
                except OSError:
                    continue
                if (
                    stat_module.S_ISFIFO(mode)
                    or stat_module.S_ISSOCK(mode)
                    or stat_module.S_ISBLK(mode)
                    or stat_module.S_ISCHR(mode)
                ):
                    logger.warning("Skipping special file: %s", entry.path)
