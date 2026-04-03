"""Unit tests for fscrawler.models."""

from __future__ import annotations

import hashlib


class TestMakeDocId:
    def test_returns_sha256_hex_digest(self) -> None:
        from fscrawler.models import make_doc_id

        result = make_doc_id("/test.txt")
        expected = hashlib.sha256("/test.txt".encode()).hexdigest()
        assert result == expected

    def test_deterministic_for_same_path(self) -> None:
        from fscrawler.models import make_doc_id

        assert make_doc_id("/a/b.txt") == make_doc_id("/a/b.txt")

    def test_different_paths_different_ids(self) -> None:
        from fscrawler.models import make_doc_id

        assert make_doc_id("/a.txt") != make_doc_id("/b.txt")
