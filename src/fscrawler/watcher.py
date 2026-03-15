# Licensed under the Apache License, Version 2.0
"""Watchdog-based filesystem event handler for FSCrawler.

Listens for file create, modify, and delete events under fs.url and
immediately indexes or removes the affected document, rather than waiting
for the next polling cycle.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler

logger = logging.getLogger("fscrawler.watcher")


class FsEventHandler(FileSystemEventHandler):
    """Handle filesystem events by indexing or deleting the affected file."""

    def __init__(
        self,
        settings: Any,
        client: Any,
        parser: Any,
        crawler_state: Any,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._client = client
        self._parser = parser
        self._crawler_state = crawler_state

    # ------------------------------------------------------------------
    # watchdog callbacks
    # ------------------------------------------------------------------

    def on_created(self, event: Any) -> None:
        if event.is_directory or self._crawler_state.paused:
            return
        if not self._matches(Path(event.src_path).name):
            return
        self._index(Path(event.src_path))

    def on_modified(self, event: Any) -> None:
        if event.is_directory or self._crawler_state.paused:
            return
        if not self._matches(Path(event.src_path).name):
            return
        self._index(Path(event.src_path))

    def on_deleted(self, event: Any) -> None:
        if event.is_directory or self._crawler_state.paused:
            return
        self._delete(event.src_path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _matches(self, name: str) -> bool:
        """Return True if name passes the includes/excludes filters."""
        fs = self._settings.fs
        if fs.includes and not any(fnmatch.fnmatch(name, p) for p in fs.includes):
            return False
        if any(fnmatch.fnmatch(name, p) for p in fs.excludes):
            return False
        return True

    def _index(self, path: Path) -> None:
        try:
            doc = self._parser.parse(path)
            self._client.index(
                doc,
                doc_id=str(path),
                index=self._settings.elasticsearch.index,
            )
            logger.info("Indexed %s", path)
        except Exception as exc:
            logger.error("Failed to index %s: %s", path, exc, exc_info=True)

    def _delete(self, path: str) -> None:
        try:
            self._client.delete(
                doc_id=path,
                index=self._settings.elasticsearch.index,
            )
            logger.info("Deleted %s from index", path)
        except Exception as exc:
            logger.error("Failed to delete %s from index: %s", path, exc, exc_info=True)
