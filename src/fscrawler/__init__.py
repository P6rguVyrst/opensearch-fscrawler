# Licensed under the Apache License, Version 2.0
"""FSCrawler — Python rewrite of the file system crawler for OpenSearch/Elasticsearch."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("opensearch-fscrawler")
except PackageNotFoundError:
    __version__ = "unknown"
