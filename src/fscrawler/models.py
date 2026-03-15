# Licensed under the Apache License, Version 2.0
"""Data models for FSCrawler documents."""

from __future__ import annotations

from dataclasses import dataclass, field
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
        result: dict[str, Any] = {
            "file": {
                k: v
                for k, v in {
                    "content_type": self.file.content_type,
                    "filename": self.file.filename,
                    "extension": self.file.extension,
                    "filesize": self.file.filesize,
                    "indexed_chars": len(self.content) if self.content is not None else 0,
                    "indexing_date": self.file.indexing_date,
                    "last_modified": self.file.last_modified,
                    "created": self.file.created,
                    "last_accessed": self.file.last_accessed,
                    "checksum": self.file.checksum,
                    "url": self.file.url,
                }.items()
                if v is not None
            },
            "path": {
                "real": self.path.real,
                "root": self.path.root,
                "virtual": self.path.virtual,
            },
        }

        # Meta — only include non-None fields
        meta_dict = {
            k: v
            for k, v in {
                "author": self.meta.author,
                "date": self.meta.date,
                "keywords": self.meta.keywords,
                "title": self.meta.title,
                "language": self.meta.language,
                "format": self.meta.format,
                "identifier": self.meta.identifier,
                "contributor": self.meta.contributor,
                "coverage": self.meta.coverage,
                "modifier": self.meta.modifier,
                "creator_tool": self.meta.creator_tool,
                "publisher": self.meta.publisher,
                "relation": self.meta.relation,
                "rights": self.meta.rights,
                "source": self.meta.source,
                "type": self.meta.type,
                "description": self.meta.description,
                "created": self.meta.created,
                "print_date": self.meta.print_date,
                "metadata_date": self.meta.metadata_date,
                "latitude": self.meta.latitude,
                "longitude": self.meta.longitude,
                "altitude": self.meta.altitude,
                "rating": self.meta.rating,
                "comments": self.meta.comments,
            }.items()
            if v is not None
        }
        if meta_dict:
            result["meta"] = meta_dict

        if self.content is not None:
            result["content"] = self.content

        if self.attachment is not None:
            import base64

            result["attachment"] = base64.b64encode(self.attachment).decode()

        return result
