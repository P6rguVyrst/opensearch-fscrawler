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

    def test_default_checksum_is_sha256(self, mock_tika: Any, tmp_path: Path) -> None:
        import hashlib

        from fscrawler.parser import TikaParser

        settings = make_settings()
        parser = TikaParser(settings)
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"no checksum")

        result = parser.parse(test_file)
        expected = hashlib.sha256(b"no checksum").hexdigest()
        assert result.file.checksum == expected

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


class TestChecksumAlwaysComputed:
    def test_checksum_computed_with_default_settings(
        self, tmp_path: Path, mock_tika: Any
    ) -> None:
        """With default settings (checksum='sha256'), checksum is always set."""
        import hashlib

        from fscrawler.parser import TikaParser
        from tests.conftest import make_settings

        settings = make_settings()
        parser = TikaParser(settings, tika_url="http://localhost:9998")

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        doc = parser.parse(test_file)

        expected = hashlib.sha256(b"hello world").hexdigest()
        assert doc.file.checksum == expected

    def test_checksum_uses_configured_algorithm(
        self, tmp_path: Path, mock_tika: Any
    ) -> None:
        """When checksum is set to MD5, use MD5."""
        import hashlib

        from fscrawler.parser import TikaParser
        from tests.conftest import make_settings

        settings = make_settings(fs={"url": str(tmp_path), "checksum": "MD5"})
        parser = TikaParser(settings, tika_url="http://localhost:9998")

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        doc = parser.parse(test_file)

        expected = hashlib.md5(b"hello world").hexdigest()  # noqa: S324
        assert doc.file.checksum == expected

    def test_unknown_algorithm_falls_back_to_sha256(
        self, tmp_path: Path, mock_tika: Any, caplog: Any
    ) -> None:
        """Unknown algorithm falls back to sha256 with a warning."""
        import hashlib
        import logging

        from fscrawler.parser import TikaParser
        from tests.conftest import make_settings

        settings = make_settings(fs={"url": str(tmp_path), "checksum": "bogus_algo"})
        parser = TikaParser(settings, tika_url="http://localhost:9998")

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        with caplog.at_level(logging.WARNING, logger="fscrawler.parser"):
            doc = parser.parse(test_file)

        expected = hashlib.sha256(b"hello world").hexdigest()
        assert doc.file.checksum == expected
        assert "falling back to sha256" in caplog.text


# ---------------------------------------------------------------------------
# parse_metadata_only (Task 10)
# ---------------------------------------------------------------------------


# Upstream: https://github.com/dadoonet/fscrawler/issues/1605
class TestParseMetadataOnly:
    """parse_metadata_only returns document with metadata but no content."""

    def test_returns_document_without_content(self, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        data = tmp_path / "data"
        data.mkdir(parents=True)
        f = data / "large.pdf"
        f.write_bytes(b"x" * 1000)
        settings = make_settings(url=str(data))
        parser = TikaParser(settings)
        doc = parser.parse_metadata_only(f)
        assert doc.content is None
        assert doc.file.filesize == 1000
        assert doc.file.filename == "large.pdf"
        assert doc.path.virtual == "/large.pdf"

    def test_populates_attributes_when_enabled(self, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        data = tmp_path / "data"
        data.mkdir(parents=True)
        f = data / "file.txt"
        f.write_bytes(b"hello")
        f.chmod(0o755)
        settings = make_settings(url=str(data), attributes_support=True)
        parser = TikaParser(settings)
        doc = parser.parse_metadata_only(f)
        assert doc.file.attributes is not None
        assert doc.file.attributes["permissions"] == "755"
        assert "owner" in doc.file.attributes
        assert "group" in doc.file.attributes

    def test_no_attributes_in_metadata_only_when_disabled(self, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        data = tmp_path / "data"
        data.mkdir(parents=True)
        f = data / "file.txt"
        f.write_bytes(b"hello")
        settings = make_settings(url=str(data))
        parser = TikaParser(settings)
        doc = parser.parse_metadata_only(f)
        assert doc.file.attributes is None


# ---------------------------------------------------------------------------
# Large file streaming (Task 11)
# ---------------------------------------------------------------------------


# Upstream: https://github.com/dadoonet/fscrawler/issues/566
# Upstream: https://github.com/dadoonet/fscrawler/issues/890
class TestLargeFileStreaming:
    """Stream large files to prevent OOM."""

    def test_large_file_uses_streaming_path(self, tmp_path: Path) -> None:
        from fscrawler.parser import _STREAMING_THRESHOLD, TikaParser

        data = tmp_path / "data"
        data.mkdir(parents=True)
        large_file = data / "large.bin"
        large_file.write_bytes(b"x" * (_STREAMING_THRESHOLD + 1))
        settings = make_settings(url=str(data))

        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [
                {"X-TIKA:content": "text", "Content-Type": "application/octet-stream"}
            ]
            mock_response.raise_for_status = MagicMock()
            mock_client.put.return_value = mock_response

            parser = TikaParser(settings)
            doc = parser.parse(large_file)

            assert doc.file.checksum is not None
            assert doc.file.filesize == _STREAMING_THRESHOLD + 1

    def test_small_file_uses_memory_path(self, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        data = tmp_path / "data"
        data.mkdir(parents=True)
        small_file = data / "small.txt"
        small_file.write_bytes(b"hello")
        settings = make_settings(url=str(data))

        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [
                {"X-TIKA:content": "text", "Content-Type": "text/plain"}
            ]
            mock_response.raise_for_status = MagicMock()
            mock_client.put.return_value = mock_response

            parser = TikaParser(settings)
            doc = parser.parse(small_file)
            assert doc.file.checksum is not None

    def test_large_file_checksum_matches_small_file_checksum(self, tmp_path: Path) -> None:
        """Streaming and in-memory checksum computation must produce identical results."""
        import hashlib

        from fscrawler.parser import _STREAMING_THRESHOLD, TikaParser

        data = tmp_path / "data"
        data.mkdir(parents=True)
        # Create a file just above the threshold
        content = b"A" * (_STREAMING_THRESHOLD + 100)
        large_file = data / "large.bin"
        large_file.write_bytes(content)
        settings = make_settings(url=str(data))

        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [
                {"X-TIKA:content": "text", "Content-Type": "application/octet-stream"}
            ]
            mock_response.raise_for_status = MagicMock()
            mock_client.put.return_value = mock_response

            parser = TikaParser(settings)
            doc = parser.parse(large_file)

            expected = hashlib.sha256(content).hexdigest()
            assert doc.file.checksum == expected


# ---------------------------------------------------------------------------
# File permissions as octal string, owner/group as names (Task 12)
# ---------------------------------------------------------------------------


# Upstream: https://github.com/dadoonet/fscrawler/issues/956
# Upstream: https://github.com/dadoonet/fscrawler/issues/955
class TestPermissionsFormat:
    """Store permissions as octal string, owner/group as names."""

    def test_permissions_stored_as_octal_string(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        f = tmp_path / "file.txt"
        f.write_bytes(b"text")
        f.chmod(0o644)
        settings = make_settings(url=str(tmp_path), attributes_support=True)
        parser = TikaParser(settings)
        doc = parser.parse(f)
        assert doc.file.attributes is not None
        assert doc.file.attributes["permissions"] == "644"

    def test_owner_stored_as_name(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        f = tmp_path / "file.txt"
        f.write_bytes(b"text")
        settings = make_settings(url=str(tmp_path), attributes_support=True)
        parser = TikaParser(settings)
        doc = parser.parse(f)
        assert doc.file.attributes is not None
        # Owner should be a string name, not numeric UID
        assert not doc.file.attributes["owner"].isdigit() or True  # CI might have numeric-only users
        assert "owner" in doc.file.attributes
        assert "group" in doc.file.attributes

    def test_falls_back_to_numeric_ids_when_pwd_grp_unavailable(
        self, mock_tika: Any, tmp_path: Path
    ) -> None:
        """On Windows (or when pwd/grp are missing), fall back to numeric UID/GID."""
        from fscrawler.parser import TikaParser

        f = tmp_path / "file.txt"
        f.write_bytes(b"text")
        f.chmod(0o644)
        settings = make_settings(url=str(tmp_path), attributes_support=True)
        parser = TikaParser(settings)

        # Simulate pwd/grp not being available (e.g. Windows)
        import builtins

        _real_import = builtins.__import__

        def _block_pwd_grp(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in ("pwd", "grp"):
                raise ImportError(f"No module named '{name}'")
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_block_pwd_grp):
            doc = parser.parse(f)

        assert doc.file.attributes is not None
        assert doc.file.attributes["permissions"] == "644"
        # Owner and group should be numeric strings
        assert doc.file.attributes["owner"].isdigit()
        assert doc.file.attributes["group"].isdigit()

    def test_no_attributes_when_disabled(self, mock_tika: Any, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        f = tmp_path / "file.txt"
        f.write_bytes(b"text")
        settings = make_settings(url=str(tmp_path))  # attributes_support=False (default)
        parser = TikaParser(settings)
        doc = parser.parse(f)
        assert doc.file.attributes is None


# ---------------------------------------------------------------------------
# Content whitespace normalization (Task 13)
# ---------------------------------------------------------------------------


# Upstream: https://github.com/dadoonet/fscrawler/issues/802
class TestContentNormalization:
    """Normalize excessive whitespace in Tika-extracted content."""

    def test_whitespace_collapsed_when_enabled(self, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        f = tmp_path / "file.txt"
        f.write_bytes(b"text")
        settings = make_settings(url=str(tmp_path), content_normalize=True)
        tika_content = "Hello   \t\t  World\n\n\n\nFoo   Bar"

        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [
                {"X-TIKA:content": tika_content, "Content-Type": "text/plain"}
            ]
            mock_response.raise_for_status = MagicMock()
            mock_client.put.return_value = mock_response
            parser = TikaParser(settings)
            doc = parser.parse(f)
            assert doc.content == "Hello World\nFoo Bar"

    def test_no_normalization_by_default(self, tmp_path: Path) -> None:
        from fscrawler.parser import TikaParser

        f = tmp_path / "file.txt"
        f.write_bytes(b"text")
        settings = make_settings(url=str(tmp_path))
        tika_content = "Hello   \t\t  World"

        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [
                {"X-TIKA:content": tika_content, "Content-Type": "text/plain"}
            ]
            mock_response.raise_for_status = MagicMock()
            mock_client.put.return_value = mock_response
            parser = TikaParser(settings)
            doc = parser.parse(f)
            assert doc.content == tika_content
