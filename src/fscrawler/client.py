# Licensed under the Apache License, Version 2.0
"""OpenSearch / Elasticsearch client wrapper for FSCrawler."""

from __future__ import annotations

import logging
import ssl
import time
from typing import Any
from urllib.parse import urlparse

from opensearchpy import OpenSearch
from opensearchpy.exceptions import ConnectionError as OSConnectionError

from fscrawler.settings import FsSettings
from fscrawler.templates import get_component_templates, get_index_templates

logger = logging.getLogger("fscrawler.client")


def _parse_host(url: str) -> dict[str, Any]:
    """Parse a URL string into a host dict understood by opensearch-py."""
    parsed = urlparse(url)
    host: dict[str, Any] = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or (443 if parsed.scheme == "https" else 9200),
        "scheme": parsed.scheme or "http",
    }
    if parsed.path and parsed.path != "/":
        host["url_prefix"] = parsed.path.rstrip("/")
    return host


class FsCrawlerClient:
    """Thin wrapper around the opensearch-py client with FSCrawler-specific helpers."""

    def __init__(self, settings: FsSettings) -> None:
        self._settings = settings
        es = settings.elasticsearch
        hosts = [_parse_host(url) for url in es.nodes]

        kwargs: dict[str, Any] = {
            "hosts": hosts,
            "use_ssl": any(h["scheme"] == "https" for h in hosts),
        }

        # SSL verification
        if not es.ssl_verification:
            kwargs["verify_certs"] = False
            kwargs["ssl_assert_hostname"] = False
            kwargs["ssl_show_warn"] = False
            # Create an unverified SSL context for completeness
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            kwargs["ssl_context"] = ctx
        else:
            kwargs["verify_certs"] = True

        # Authentication
        if es.api_key:
            kwargs["http_auth"] = None
            kwargs["api_key"] = es.api_key
        elif es.username:
            kwargs["http_auth"] = (es.username, es.password)

        self._client: OpenSearch = OpenSearch(**kwargs)
        logger.debug("FsCrawlerClient initialised with nodes: %s", es.nodes)

    # ------------------------------------------------------------------
    # Cluster info
    # ------------------------------------------------------------------

    def wait_for_cluster(
        self,
        max_retries: int = 10,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
    ) -> None:
        """Block until the cluster is reachable, using exponential backoff.

        Only retries on connection-level errors (host unreachable, refused).
        Authentication or other API errors are raised immediately without retry,
        as they indicate a configuration problem that retrying won't fix.

        Parameters
        ----------
        max_retries:
            Maximum number of retry attempts before giving up (default 10).
        base_delay:
            Initial delay in seconds before the first retry (default 2).
        max_delay:
            Maximum delay between retries in seconds (default 60).
        """
        delay = base_delay
        for attempt in range(max_retries + 1):
            try:
                self.get_info()
                logger.info("Connected to OpenSearch/Elasticsearch cluster")
                return
            except OSConnectionError as exc:
                if attempt == max_retries:
                    logger.error(
                        "Cluster not reachable after %d attempts — giving up", max_retries
                    )
                    raise
                logger.warning(
                    "Cluster not reachable (attempt %d/%d), retrying in %.0fs: %s",
                    attempt + 1,
                    max_retries,
                    delay,
                    exc,
                )
                time.sleep(delay)
                delay = min(delay * 2, max_delay)

    def get_info(self) -> dict[str, Any]:
        """Return cluster info dict (version, cluster_name, etc.)."""
        return self._client.info()  # type: ignore[no-any-return]

    def info(self) -> dict[str, Any]:
        """Alias for get_info() — used by the REST server."""
        return self.get_info()

    # ------------------------------------------------------------------
    # Template management
    # ------------------------------------------------------------------

    def push_templates(self, force: bool = False) -> None:
        """Create component and index templates if push_templates is enabled."""
        if not self._settings.elasticsearch.push_templates:
            logger.debug("push_templates is disabled — skipping.")
            return

        es = self._settings.elasticsearch
        index_name = es.index
        folder_index = es.index_folder

        # Component templates for the docs index
        for name, body in get_component_templates(index_name, self._settings.name):
            self._put_component_template(name, body, force=force)

        # Component templates for the folder index (re-use same set)
        for name, body in get_component_templates(folder_index, self._settings.name):
            self._put_component_template(name, body, force=force)

        # Index templates
        for name, body in get_index_templates(index_name, folder_index):
            self._put_index_template(name, body, force=force)

    def _template_exists(self, name: str, kind: str) -> bool:
        """Return True if a component or index template already exists."""
        try:
            if kind == "component":
                self._client.cluster.get_component_template(name=name)
            else:
                self._client.indices.get_index_template(name=name)
            return True
        except Exception:
            return False

    def _put_component_template(
        self, name: str, body: dict[str, Any], force: bool = False
    ) -> None:
        if not force and self._template_exists(name, "component"):
            logger.debug("Component template %r already exists — skipping.", name)
            return
        logger.info("Putting component template: %s", name)
        self._client.cluster.put_component_template(name=name, body=body)

    def _put_index_template(
        self, name: str, body: dict[str, Any], force: bool = False
    ) -> None:
        if not force and self._template_exists(name, "index"):
            logger.debug("Index template %r already exists — skipping.", name)
            return
        logger.info("Putting index template: %s", name)
        self._client.indices.put_index_template(name=name, body=body)

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def index_exists(self, index: str) -> bool:
        """Return True if the named index exists."""
        return bool(self._client.indices.exists(index=index))

    def create_index(self, index: str) -> None:
        """Create an empty index."""
        self._client.indices.create(index=index)

    def ensure_index(self, index: str) -> None:
        """Create the index if it does not already exist."""
        if not self.index_exists(index):
            logger.info("Creating index: %s", index)
            self.create_index(index)

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    def bulk(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """Execute a bulk request and return the response."""
        return self._client.bulk(body=operations)  # type: ignore[no-any-return]

    def delete_document(self, index: str, doc_id: str) -> dict[str, Any]:
        """Delete a single document by ID."""
        return self._client.delete(index=index, id=doc_id)  # type: ignore[no-any-return]

    def index(self, doc: Any, doc_id: str, index: str | None = None) -> dict[str, Any]:
        """Index a single document."""
        idx = index or self._settings.elasticsearch.index
        body = doc.to_dict() if hasattr(doc, "to_dict") else doc
        return self._client.index(index=idx, id=doc_id, body=body)  # type: ignore[no-any-return]

    def delete(self, doc_id: str, index: str | None = None) -> dict[str, Any]:
        """Delete a document by ID, defaulting the index from settings."""
        idx = index or self._settings.elasticsearch.index
        return self.delete_document(index=idx, doc_id=doc_id)
