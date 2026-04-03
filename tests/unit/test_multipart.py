"""Unit tests for fscrawler.multipart (stdlib-only multipart/form-data parser)."""

from __future__ import annotations

import pytest

from fscrawler.multipart import parse_multipart


def _build_multipart(
    parts: list[tuple[str, str, bytes, str]],
    boundary: str = "----TestBoundary",
) -> tuple[str, bytes]:
    """Build a multipart body from a list of (field_name, filename, data, content_type) tuples."""
    chunks: list[bytes] = []
    for field_name, filename, data, ct in parts:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
        )
        chunks.append(f"Content-Type: {ct}\r\n\r\n".encode())
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    content_type = f"multipart/form-data; boundary={boundary}"
    return content_type, b"".join(chunks)


class TestParseMultipart:
    def test_single_file(self) -> None:
        ct, body = _build_multipart([
            ("file", "report.pdf", b"%PDF-1.4 content", "application/pdf"),
        ])
        files = parse_multipart(ct, body)
        assert len(files) == 1
        assert files[0].filename == "report.pdf"
        assert files[0].data == b"%PDF-1.4 content"
        assert files[0].field_name == "file"

    def test_multiple_files(self) -> None:
        ct, body = _build_multipart([
            ("file1", "a.txt", b"aaa", "text/plain"),
            ("file2", "b.txt", b"bbb", "text/plain"),
        ])
        files = parse_multipart(ct, body)
        assert len(files) == 2
        assert files[0].filename == "a.txt"
        assert files[1].filename == "b.txt"

    def test_skips_non_file_fields(self) -> None:
        boundary = "----TestBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="comment"\r\n'
            f"\r\n"
            f"just a text field\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="doc.pdf"\r\n'
            f"Content-Type: application/pdf\r\n"
            f"\r\n"
        ).encode() + b"pdf data" + f"\r\n--{boundary}--\r\n".encode()
        ct = f"multipart/form-data; boundary={boundary}"

        files = parse_multipart(ct, body)
        assert len(files) == 1
        assert files[0].filename == "doc.pdf"

    def test_binary_content_preserved(self) -> None:
        binary = bytes(range(256))
        ct, body = _build_multipart([
            ("file", "binary.bin", binary, "application/octet-stream"),
        ])
        files = parse_multipart(ct, body)
        assert files[0].data == binary

    def test_not_multipart_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="Expected multipart/form-data"):
            parse_multipart("application/json", b'{"key": "value"}')

    def test_missing_boundary_raises_valueerror(self) -> None:
        with pytest.raises(ValueError, match="missing boundary"):
            parse_multipart("multipart/form-data", b"some body")

    def test_empty_body_returns_empty_list(self) -> None:
        ct = "multipart/form-data; boundary=----Empty"
        body = b"------Empty--\r\n"
        files = parse_multipart(ct, body)
        assert files == []
