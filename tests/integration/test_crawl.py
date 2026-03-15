"""Integration tests for FSCrawler end-to-end crawl.

These tests require a running OpenSearch (or Elasticsearch) instance.
Set the OPENSEARCH_URL environment variable to enable them, e.g.:

    OPENSEARCH_URL=http://localhost:9200 pytest tests/integration

A Tika server is also required.  Set TIKA_URL (default: http://localhost:9998).
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "")
TIKA_URL = os.environ.get("TIKA_URL", "http://localhost:9998")


def unique_name() -> str:
    return f"fscrawler_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def integration_settings(tmp_path: Path) -> Any:
    """Build FsSettings pointing at a real OpenSearch instance."""
    from fscrawler.settings import FsSettings

    job_name = unique_name()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return FsSettings.from_dict(
        {
            "name": job_name,
            "fs": {
                "url": str(data_dir),
                "update_rate": "1s",
                "index_content": True,
                "remove_deleted": True,
            },
            "elasticsearch": {
                "nodes": [{"url": OPENSEARCH_URL}],
                "index": f"{job_name}_docs",
                "index_folder": f"{job_name}_folder",
                "bulk_size": 10,
                "push_templates": True,
                "ssl_verification": False,
            },
        }
    )


@pytest.mark.integration
class TestFullCrawl:
    def test_full_crawl_indexes_files(
        self, integration_settings: Any, tmp_path: Path
    ) -> None:
        """Create temp files, crawl, verify they are indexed in OpenSearch."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.crawler import LocalCrawler
        from fscrawler.indexer import BulkIndexer
        from fscrawler.parser import TikaParser

        settings = integration_settings
        data_dir = Path(settings.fs.url)
        (data_dir / "hello.txt").write_text("Hello integration test", encoding="utf-8")
        (data_dir / "world.txt").write_text("World integration test", encoding="utf-8")

        client = FsCrawlerClient(settings)
        client.push_templates()

        parser = TikaParser(settings, tika_url=TIKA_URL)
        crawler = LocalCrawler(settings, config_dir=tmp_path)

        with BulkIndexer(client, settings) as indexer:
            for file_path in crawler.scan():
                if crawler.is_new_or_modified(file_path):
                    doc = parser.parse(file_path)
                    indexer.add(doc)
        crawler.save_checkpoint()

        # Give OpenSearch a moment to index
        time.sleep(1)

        # Verify the docs are in OpenSearch
        os_client = client._client
        os_client.indices.refresh(index=settings.elasticsearch.index)
        result = os_client.search(
            index=settings.elasticsearch.index,
            body={"query": {"match_all": {}}},
        )
        hits = result["hits"]["total"]["value"]
        assert hits == 2, f"Expected 2 documents, got {hits}"

    def test_incremental_crawl_indexes_new_file(
        self, integration_settings: Any, tmp_path: Path
    ) -> None:
        """After initial crawl, add a new file and verify it is indexed."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.crawler import LocalCrawler
        from fscrawler.indexer import BulkIndexer
        from fscrawler.parser import TikaParser

        settings = integration_settings
        data_dir = Path(settings.fs.url)
        (data_dir / "initial.txt").write_text("Initial file", encoding="utf-8")

        client = FsCrawlerClient(settings)
        client.push_templates()
        parser = TikaParser(settings, tika_url=TIKA_URL)

        def run_crawl() -> None:
            crawler = LocalCrawler(settings, config_dir=tmp_path)
            with BulkIndexer(client, settings) as indexer:
                for fp in crawler.scan():
                    if crawler.is_new_or_modified(fp):
                        indexer.add(parser.parse(fp))
            crawler.save_checkpoint()

        # First crawl
        run_crawl()
        time.sleep(1)
        os_client = client._client
        os_client.indices.refresh(index=settings.elasticsearch.index)
        count1 = os_client.search(
            index=settings.elasticsearch.index,
            body={"query": {"match_all": {}}},
        )["hits"]["total"]["value"]
        assert count1 == 1

        # Add a new file and crawl again
        (data_dir / "new_file.txt").write_text("New file added", encoding="utf-8")
        run_crawl()
        time.sleep(1)
        os_client.indices.refresh(index=settings.elasticsearch.index)
        count2 = os_client.search(
            index=settings.elasticsearch.index,
            body={"query": {"match_all": {}}},
        )["hits"]["total"]["value"]
        assert count2 == 2, f"Expected 2 documents after incremental crawl, got {count2}"

    def test_deleted_file_removed_from_index(
        self, integration_settings: Any, tmp_path: Path
    ) -> None:
        """Delete a file and verify it is removed from the index on next crawl."""
        from fscrawler.client import FsCrawlerClient
        from fscrawler.crawler import LocalCrawler
        from fscrawler.indexer import BulkIndexer
        from fscrawler.parser import TikaParser

        settings = integration_settings
        data_dir = Path(settings.fs.url)
        file_to_delete = data_dir / "to_delete.txt"
        file_to_delete.write_text("Delete me", encoding="utf-8")
        (data_dir / "keep.txt").write_text("Keep me", encoding="utf-8")

        client = FsCrawlerClient(settings)
        client.push_templates()
        parser = TikaParser(settings, tika_url=TIKA_URL)

        def run_crawl() -> None:
            crawler = LocalCrawler(settings, config_dir=tmp_path)
            with BulkIndexer(client, settings) as indexer:
                for fp in crawler.scan():
                    if crawler.is_new_or_modified(fp):
                        indexer.add(parser.parse(fp))
                for deleted_path in crawler.get_deleted_files():
                    indexer.delete(deleted_path)
            crawler.save_checkpoint()

        # First crawl — index 2 files
        run_crawl()
        time.sleep(1)
        os_client = client._client
        os_client.indices.refresh(index=settings.elasticsearch.index)
        count1 = os_client.search(
            index=settings.elasticsearch.index,
            body={"query": {"match_all": {}}},
        )["hits"]["total"]["value"]
        assert count1 == 2

        # Delete the file and re-crawl
        file_to_delete.unlink()
        run_crawl()
        time.sleep(1)
        os_client.indices.refresh(index=settings.elasticsearch.index)
        count2 = os_client.search(
            index=settings.elasticsearch.index,
            body={"query": {"match_all": {}}},
        )["hits"]["total"]["value"]
        assert count2 == 1, f"Expected 1 document after deletion, got {count2}"


# Type hint needed for the fixture
from typing import Any  # noqa: E402
