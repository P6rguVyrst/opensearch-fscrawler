# Licensed under the Apache License, Version 2.0
"""Elasticsearch / OpenSearch index and component template definitions.

Templates are compatible with both Elasticsearch 7.x/8.x/9.x and OpenSearch 1.x/2.x/3.x.
Component template names follow the pattern: fscrawler_{index}_{purpose}
Index template names follow the pattern: fscrawler_{index}_docs / fscrawler_{index}_folders
"""

from __future__ import annotations

from typing import Any


def alias_template(alias_name: str) -> dict[str, Any]:
    """Component template that creates an alias pointing to the job name."""
    return {"template": {"aliases": {alias_name: {}}}}


def settings_total_fields_template() -> dict[str, Any]:
    """Component template that raises the total fields mapping limit."""
    return {"template": {"settings": {"index.mapping.total_fields.limit": 2000}}}


def mapping_file_template() -> dict[str, Any]:
    """Component template with file metadata field mappings."""
    return {
        "template": {
            "mappings": {
                "properties": {
                    "file": {
                        "properties": {
                            "content_type": {"type": "keyword"},
                            "filename": {"type": "keyword", "store": True},
                            "extension": {"type": "keyword"},
                            "filesize": {"type": "long"},
                            "indexed_chars": {"type": "long"},
                            "indexing_date": {
                                "type": "date",
                                "format": "date_optional_time",
                            },
                            "created": {
                                "type": "date",
                                "format": "date_optional_time",
                            },
                            "last_modified": {
                                "type": "date",
                                "format": "date_optional_time",
                            },
                            "last_accessed": {
                                "type": "date",
                                "format": "date_optional_time",
                            },
                            "checksum": {"type": "keyword"},
                            "url": {"type": "keyword", "index": False},
                        }
                    }
                }
            }
        }
    }


def mapping_path_template() -> dict[str, Any]:
    """Component template with path field mappings including path_hierarchy analyzer."""
    return {
        "template": {
            "settings": {
                "analysis": {
                    "analyzer": {
                        "fscrawler_path": {
                            "tokenizer": "fscrawler_path",
                            "char_filter": ["windows_separator"],
                        }
                    },
                    "char_filter": {
                        "windows_separator": {
                            "type": "mapping",
                            "mappings": ["\\\\ => /"],
                        }
                    },
                    "tokenizer": {
                        "fscrawler_path": {"type": "path_hierarchy"}
                    },
                }
            },
            "mappings": {
                "properties": {
                    "path": {
                        "properties": {
                            "real": {
                                "type": "keyword",
                                "fields": {
                                    "tree": {
                                        "type": "text",
                                        "analyzer": "fscrawler_path",
                                        "fielddata": True,
                                    },
                                    "fulltext": {"type": "text"},
                                },
                            },
                            "root": {"type": "keyword"},
                            "virtual": {
                                "type": "keyword",
                                "fields": {
                                    "tree": {
                                        "type": "text",
                                        "analyzer": "fscrawler_path",
                                        "fielddata": True,
                                    },
                                    "fulltext": {"type": "text"},
                                },
                            },
                        }
                    }
                }
            },
        }
    }


def mapping_meta_template() -> dict[str, Any]:
    """Component template with document metadata field mappings."""
    return {
        "template": {
            "mappings": {
                "properties": {
                    "meta": {
                        "properties": {
                            "author": {"type": "text"},
                            "date": {"type": "date", "format": "date_optional_time"},
                            "keywords": {"type": "text"},
                            "title": {"type": "text"},
                            "language": {"type": "keyword"},
                            "format": {"type": "text"},
                            "identifier": {"type": "text"},
                            "contributor": {"type": "text"},
                            "coverage": {"type": "text"},
                            "modifier": {"type": "text"},
                            "creator_tool": {"type": "keyword"},
                            "publisher": {"type": "text"},
                            "relation": {"type": "text"},
                            "rights": {"type": "text"},
                            "source": {"type": "text"},
                            "type": {"type": "text"},
                            "description": {"type": "text"},
                            "created": {"type": "date", "format": "date_optional_time"},
                            "print_date": {"type": "date", "format": "date_optional_time"},
                            "metadata_date": {"type": "date", "format": "date_optional_time"},
                            "latitude": {"type": "keyword"},
                            "longitude": {"type": "keyword"},
                            "altitude": {"type": "keyword"},
                            "rating": {"type": "short"},
                            "comments": {"type": "text"},
                        }
                    }
                }
            }
        }
    }


def mapping_content_template() -> dict[str, Any]:
    """Component template for the full-text content field."""
    return {"template": {"mappings": {"properties": {"content": {"type": "text"}}}}}


def mapping_attachment_template() -> dict[str, Any]:
    """Component template for binary attachment storage."""
    return {
        "template": {
            "mappings": {
                "properties": {
                    "attachment": {"type": "binary", "doc_values": False}
                }
            }
        }
    }


def mapping_attributes_template() -> dict[str, Any]:
    """Component template for file attributes / ACL fields."""
    return {
        "template": {
            "mappings": {
                "properties": {
                    "attributes": {
                        "properties": {
                            "owner": {"type": "keyword"},
                            "group": {"type": "keyword"},
                            "permissions": {"type": "integer"},
                        }
                    }
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# Index template builders
# ---------------------------------------------------------------------------


def index_template_docs(index_name: str) -> dict[str, Any]:
    """Composable index template for the documents index."""
    return {
        "index_patterns": [index_name],
        "priority": 500,
        "composed_of": [
            f"fscrawler_{index_name}_alias",
            f"fscrawler_{index_name}_settings_total_fields",
            f"fscrawler_{index_name}_mapping_attributes",
            f"fscrawler_{index_name}_mapping_file",
            f"fscrawler_{index_name}_mapping_path",
            f"fscrawler_{index_name}_mapping_attachment",
            f"fscrawler_{index_name}_mapping_content",
            f"fscrawler_{index_name}_mapping_meta",
        ],
    }


def index_template_folders(index_name: str) -> dict[str, Any]:
    """Composable index template for the folder index."""
    return {
        "index_patterns": [index_name],
        "priority": 500,
        "composed_of": [
            f"fscrawler_{index_name}_alias",
            f"fscrawler_{index_name}_settings_total_fields",
            f"fscrawler_{index_name}_mapping_path",
        ],
    }


# ---------------------------------------------------------------------------
# Aggregate: all templates for a given job
# ---------------------------------------------------------------------------


def get_component_templates(index_name: str, job_name: str) -> list[tuple[str, dict[str, Any]]]:
    """Return a list of (template_name, body) tuples for all component templates."""
    return [
        (f"fscrawler_{index_name}_alias", alias_template(job_name)),
        (f"fscrawler_{index_name}_settings_total_fields", settings_total_fields_template()),
        (f"fscrawler_{index_name}_mapping_file", mapping_file_template()),
        (f"fscrawler_{index_name}_mapping_path", mapping_path_template()),
        (f"fscrawler_{index_name}_mapping_meta", mapping_meta_template()),
        (f"fscrawler_{index_name}_mapping_content", mapping_content_template()),
        (f"fscrawler_{index_name}_mapping_attachment", mapping_attachment_template()),
        (f"fscrawler_{index_name}_mapping_attributes", mapping_attributes_template()),
    ]


def get_index_templates(
    docs_index: str, folder_index: str
) -> list[tuple[str, dict[str, Any]]]:
    """Return a list of (template_name, body) tuples for the index templates."""
    return [
        (f"fscrawler_{docs_index}_docs", index_template_docs(docs_index)),
        (f"fscrawler_{folder_index}_folders", index_template_folders(folder_index)),
    ]
