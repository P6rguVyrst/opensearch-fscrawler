"""Unit tests for fscrawler.models."""

from __future__ import annotations

import base64
import hashlib

from tests.conftest import make_document

from fscrawler.models import FolderDocument, PathInfo


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


class TestDocumentToDict:
    def test_includes_timestamp(self) -> None:
        doc = make_document()
        result = doc.to_dict()
        assert "@timestamp" in result
        assert result["@timestamp"] == doc.file.indexing_date

    def test_includes_file_block(self) -> None:
        doc = make_document()
        result = doc.to_dict()
        assert "file" in result
        assert result["file"]["filename"] == "test.txt"

    def test_includes_path_block(self) -> None:
        doc = make_document()
        result = doc.to_dict()
        assert "path" in result
        assert "real" in result["path"]
        assert "root" in result["path"]
        assert "virtual" in result["path"]

    def test_includes_content_when_present(self) -> None:
        doc = make_document(content="some text")
        result = doc.to_dict()
        assert result["content"] == "some text"

    def test_omits_content_when_none(self) -> None:
        doc = make_document()
        doc.content = None
        result = doc.to_dict()
        assert "content" not in result

    def test_omits_meta_when_empty(self) -> None:
        doc = make_document()
        result = doc.to_dict()
        assert "meta" not in result

    def test_includes_meta_when_populated(self) -> None:
        doc = make_document()
        doc.meta.author = "Alice"
        result = doc.to_dict()
        assert result["meta"]["author"] == "Alice"

    def test_base64_encodes_attachment(self) -> None:
        doc = make_document()
        doc.attachment = b"\x00\x01\x02binary data"
        result = doc.to_dict()
        assert "attachment" in result
        decoded = base64.b64decode(result["attachment"])
        assert decoded == b"\x00\x01\x02binary data"

    def test_omits_attachment_when_none(self) -> None:
        doc = make_document()
        assert doc.attachment is None
        result = doc.to_dict()
        assert "attachment" not in result

    def test_indexed_chars_reflects_content_length(self) -> None:
        doc = make_document(content="hello")
        result = doc.to_dict()
        assert result["file"]["indexed_chars"] == 5

    def test_indexed_chars_zero_when_no_content(self) -> None:
        doc = make_document()
        doc.content = None
        result = doc.to_dict()
        assert result["file"]["indexed_chars"] == 0


class TestFolderDocumentToDict:
    def test_includes_path_block(self) -> None:
        folder = FolderDocument(path=PathInfo(real="/data/sub", root="/data", virtual="/sub"))
        result = folder.to_dict()
        assert result == {
            "path": {"real": "/data/sub", "root": "/data", "virtual": "/sub"}
        }

    def test_root_folder_virtual_path(self) -> None:
        folder = FolderDocument(path=PathInfo(real="/data", root="/data", virtual="/"))
        result = folder.to_dict()
        assert result["path"]["virtual"] == "/"
