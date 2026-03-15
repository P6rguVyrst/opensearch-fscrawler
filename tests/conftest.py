"""Shared pytest fixtures for FSCrawler tests."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent / "data"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture from tests/data/."""
    with open(DATA_DIR / name) as f:
        return json.load(f)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_settings_dict() -> dict[str, Any]:
    """Return a minimal valid settings dictionary."""
    return {
        "name": "test",
        "fs": {
            "url": "/tmp/testdata",
            "update_rate": "15m",
            "includes": ["*.pdf", "*.doc"],
            "excludes": ["*.tmp"],
            "json_support": False,
            "xml_support": False,
            "follow_symlinks": False,
            "remove_deleted": True,
            "continue_on_error": False,
            "ignore_above": "512mb",
            "filename_as_id": True,
            "index_content": True,
            "add_filesize": True,
            "attributes_support": False,
            "lang_detect": False,
            "store_source": False,
            "indexed_chars": "100000.0",
            "raw_metadata": False,
            "checksum": "MD5",
            "index_folders": True,
        },
        "elasticsearch": {
            "nodes": [{"url": "http://localhost:9200"}],
            "username": "",
            "password": "",
            "api_key": "",
            "ssl_verification": False,
            "index": "test_docs",
            "index_folder": "test_folder",
            "bulk_size": 100,
            "byte_size": "10mb",
            "push_templates": True,
        },
        "rest": {
            "url": "http://127.0.0.1:8080",
            "enable_cors": False,
        },
    }


@pytest.fixture()
def sample_settings(sample_settings_dict: dict[str, Any], tmp_path: Path) -> Any:
    """Return an FsSettings instance built from sample_settings_dict."""
    from fscrawler.settings import FsSettings

    # write a temporary _settings.yaml so we can test round-trip loading
    settings_file = tmp_path / "_settings.yaml"
    import yaml

    settings_file.write_text(yaml.dump(sample_settings_dict))
    return FsSettings.from_dict(sample_settings_dict)


# ---------------------------------------------------------------------------
# Temporary crawl directory
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_crawl_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with sample files for crawler tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Create a few text/pdf-like files
    (data_dir / "document1.pdf").write_bytes(b"%PDF-1.4 fake pdf content")
    (data_dir / "document2.txt").write_text("Hello, world!", encoding="utf-8")
    (data_dir / "notes.doc").write_bytes(b"DOC fake content")
    (data_dir / "ignored.tmp").write_text("temporary file", encoding="utf-8")

    subdir = data_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.pdf").write_bytes(b"%PDF-1.4 nested pdf")

    return data_dir


# ---------------------------------------------------------------------------
# Mock OpenSearch client
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_opensearch_client() -> Generator[MagicMock, None, None]:
    """Patch the OpenSearch client with a MagicMock."""
    with patch("fscrawler.client.OpenSearch") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance

        # Stub info() response
        info_data = load_fixture("opensearch_info.json")
        mock_instance.info.return_value = info_data

        # Stub cluster health
        health_data = load_fixture("opensearch_cluster_health.json")
        mock_instance.cluster.health.return_value = health_data

        # Stub bulk response
        bulk_data = load_fixture("bulk_response_success.json")
        mock_instance.bulk.return_value = bulk_data

        # Stub indices.exists
        mock_instance.indices.exists.return_value = False
        mock_instance.indices.create.return_value = {"acknowledged": True}

        # Stub component template methods — default: template does NOT exist
        mock_instance.cluster.get_component_template.side_effect = Exception(
            "resource_not_found_exception"
        )
        mock_instance.cluster.put_component_template.return_value = {"acknowledged": True}
        mock_instance.indices.get_index_template.side_effect = Exception(
            "resource_not_found_exception"
        )
        mock_instance.indices.put_index_template.return_value = {"acknowledged": True}

        yield mock_instance


# ---------------------------------------------------------------------------
# Mock Tika server
# ---------------------------------------------------------------------------


@pytest.fixture()
def tika_response_data() -> dict[str, Any]:
    return load_fixture("tika_response.json")


@pytest.fixture()
def mock_tika(tika_response_data: dict[str, Any]) -> Generator[Any, None, None]:
    """Mock httpx calls to the Tika server.

    Patches ``fscrawler.parser.httpx.Client`` so that the context manager
    returns a mock whose ``.put()`` method returns a response with the
    fixture data.
    """
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [tika_response_data]

    mock_client_instance = MagicMock()
    mock_client_instance.put.return_value = mock_response

    mock_client_ctx = MagicMock()
    mock_client_ctx.__enter__ = MagicMock(return_value=mock_client_instance)
    mock_client_ctx.__exit__ = MagicMock(return_value=False)

    with patch("fscrawler.parser.httpx.Client", return_value=mock_client_ctx) as _:
        yield mock_client_instance


# ---------------------------------------------------------------------------
# Integration: skip unless OPENSEARCH_URL is set
# ---------------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring a running OpenSearch instance",
    )


def pytest_collection_modifyitems(items: list[Any]) -> None:
    opensearch_url = os.environ.get("OPENSEARCH_URL")
    skip_marker = pytest.mark.skip(reason="Set OPENSEARCH_URL env var to run integration tests")
    for item in items:
        if "integration" in item.keywords and not opensearch_url:
            item.add_marker(skip_marker)
