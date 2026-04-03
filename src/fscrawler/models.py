# Licensed under the Apache License, Version 2.0
"""Data models for FSCrawler documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FileInfo:
    """Metadata about the physical file."""

    filename: str
    extension: str
    content_type: str
    filesize: int
    indexing_date: str
    last_modified: str
    created: str | None = None
    last_accessed: str | None = None
    checksum: str | None = None
    url: str = ""


@dataclass
class PathInfo:
    """Path information — real, root and virtual paths."""

    real: str
    root: str
    virtual: str


@dataclass
class Meta:
    """Document-level metadata extracted by Tika."""

    author: str | None = None
    date: str | None = None
    keywords: str | None = None
    title: str | None = None
    language: str | None = None
    format: str | None = None
    identifier: str | None = None
    contributor: str | None = None
    coverage: str | None = None
    modifier: str | None = None
    creator_tool: str | None = None
    publisher: str | None = None
    relation: str | None = None
    rights: str | None = None
    source: str | None = None
    type: str | None = None
    description: str | None = None
    created: str | None = None
    print_date: str | None = None
    metadata_date: str | None = None
    latitude: str | None = None
    longitude: str | None = None
    altitude: str | None = None
    rating: str | None = None
    comments: str | None = None


@dataclass
class FolderDocument:
    """A directory entry for the folder index."""

    path: PathInfo

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": {
                "real": self.path.real,
                "root": self.path.root,
                "virtual": self.path.virtual,
            }
        }


@dataclass
class Document:
    """A fully parsed document ready for indexing."""

    content: str | None
    file: FileInfo
    path: PathInfo
    meta: Meta
    attachment: bytes | None = None  # populated when store_source=True

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict suitable for OpenSearch indexing."""
        from dataclasses import asdict

        file_dict = {k: v for k, v in asdict(self.file).items() if v is not None}
        if self.content is not None:
            file_dict["indexed_chars"] = len(self.content)

        result: dict[str, Any] = {
            "file": file_dict,
            "path": asdict(self.path),
        }

        meta_dict = {k: v for k, v in asdict(self.meta).items() if v is not None}
        if meta_dict:
            result["meta"] = meta_dict

        if self.content is not None:
            result["content"] = self.content

        if self.attachment is not None:
            import base64

            result["attachment"] = base64.b64encode(self.attachment).decode()

        return result
