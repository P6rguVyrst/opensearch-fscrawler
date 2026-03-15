"""Unit tests for fscrawler.parser."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fscrawler.settings import FsSettings

DATA_DIR = Path(__file__).parent.parent / "data"


def load_fixture(name: str) -> dict[str, Any]:
    with open(DATA_DIR / name) as f:
        return json.load(f)  # type: ignore[no-any-return]


def make_settings(**fs_overrides: Any) -> FsSettings:
    fs: dict[str, Any] = {"url": "/data"}
    fs.update(fs_overrides)
    return FsSettings.from_dict({"name": "test", "fs": fs})


# ---------------------------------------------------------------------------
# TikaParser
# ---------------------------------------------------------------------------


class TestTikaParser:
    def test_extracts_content_from_response(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        settings = make_settings()
        parser = TikaParser(settings)
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"%PDF-1.4 content")

        result = parser.parse(test_file)
        assert result.content is not None
        assert "content of the test document" in result.content.lower()

    def test_extracts_title_metadata(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        settings = make_settings()
        parser = TikaParser(settings)
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"%PDF-1.4 content")

        result = parser.parse(test_file)
        assert result.meta.title == "Test Document"

    def test_extracts_author_metadata(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        settings = make_settings()
        parser = TikaParser(settings)
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"content")

        result = parser.parse(test_file)
        assert result.meta.author == "John Doe"

    def test_extracts_content_type(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        settings = make_settings()
        parser = TikaParser(settings)
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"content")

        result = parser.parse(test_file)
        assert result.file.content_type == "application/pdf"

    def test_respects_indexed_chars_limit(self, tmp_path: Path) -> None:
        """Content should be truncated to indexed_chars characters."""
        from fscrawler.parser import TikaParser

        tika_data = {"Content-Type": "text/plain", "X-TIKA:content": "A" * 1000}

        settings = make_settings(indexed_chars="10")
        parser = TikaParser(settings)

        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [tika_data]
            mock_client.put.return_value = mock_response

            test_file = tmp_path / "long.txt"
            test_file.write_bytes(b"A" * 1000)
            result = parser.parse(test_file)

        assert result.content is not None
        assert len(result.content) <= 10

    def test_returns_none_content_when_index_content_false(
        self, tmp_path: Path
    ) -> None:
        from fscrawler.parser import TikaParser

        settings = make_settings(index_content=False)
        parser = TikaParser(settings)

        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = [
                {"Content-Type": "application/pdf", "X-TIKA:content": "some text"}
            ]
            mock_client.put.return_value = mock_response

            test_file = tmp_path / "test.pdf"
            test_file.write_bytes(b"content")
            result = parser.parse(test_file)

        assert result.content is None

    def test_computes_md5_checksum_when_configured(
        self, mock_tika: Any, tmp_path: Path
    ) -> None:
        import hashlib

        from fscrawler.parser import TikaParser

        settings = make_settings(checksum="MD5")
        parser = TikaParser(settings)
        test_file = tmp_path / "test.txt"
        content = b"hello checksum"
        test_file.write_bytes(content)

        result = parser.parse(test_file)
        expected = hashlib.md5(content).hexdigest()
        assert result.file.checksum == expected

    def test_no_checksum_when_not_configured(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        settings = make_settings(checksum=None)
        parser = TikaParser(settings)
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"no checksum")

        result = parser.parse(test_file)
        assert result.file.checksum is None

    def test_extracts_file_size(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        settings = make_settings()
        parser = TikaParser(settings)
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"x" * 42)

        result = parser.parse(test_file)
        assert result.file.filesize == 42

    def test_path_info_populated(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        settings = make_settings(url=str(tmp_path))
        parser = TikaParser(settings)
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello")

        result = parser.parse(test_file)
        assert result.path.real == str(test_file)
        assert result.path.virtual is not None

    def test_tika_unavailable_raises(self, tmp_path: Path) -> None:
        import httpx

        from fscrawler.parser import TikaParser, TikaUnavailableError

        settings = make_settings()
        parser = TikaParser(settings)

        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.put.side_effect = httpx.ConnectError("connection refused")

            test_file = tmp_path / "test.pdf"
            test_file.write_bytes(b"content")
            with pytest.raises(TikaUnavailableError):
                parser.parse(test_file)
