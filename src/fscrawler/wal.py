# Licensed under the Apache License, Version 2.0
"""Write-Ahead Log for FSCrawler — local durability before OpenSearch calls."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from fscrawler.metrics import wal_records

logger = logging.getLogger("fscrawler.wal")


class WriteAheadLog:
    """Append-only JSONL write-ahead log with atomic checkpoint.

    Every record is fsync'd individually to guarantee durability.
    Checkpoint uses atomic rewrite (write to temp, fsync, rename)
    to remove processed records safely.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: dict[str, Any]) -> None:
        """Append a single record to the WAL. Fsync'd for durability."""
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with self._lock:
            with open(self._path, "a") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            wal_records.add(1, {"fscrawler.wal.action": "append"})

    def read(self) -> list[dict[str, Any]]:
        """Read all valid records from the WAL. Skips corrupt lines."""
        if not self._path.exists():
            return []

        records: list[dict[str, Any]] = []
        with open(self._path) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("WAL: corrupt line %d, skipping: %s", line_num, line[:200])
        return records

    def checkpoint(self, processed_doc_ids: set[str]) -> None:
        """Remove processed records via atomic rewrite.

        1. Read current WAL
        2. Filter out records whose doc_id is in processed_doc_ids
        3. Write survivors to temp file, fsync
        4. Atomic rename temp -> WAL path
        """
        if not self._path.exists():
            return

        with self._lock:
            records = []
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("doc_id") not in processed_doc_ids:
                        records.append(record)

            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".wal_tmp_"
            )
            try:
                with os.fdopen(fd, "w") as f:
                    for record in records:
                        f.write(json.dumps(record, separators=(",", ":")) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(self._path))
                wal_records.add(1, {"fscrawler.wal.action": "checkpoint"})
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    @property
    def is_empty(self) -> bool:
        """Return True if WAL doesn't exist or has no content."""
        if not self._path.exists():
            return True
        return self._path.stat().st_size == 0
