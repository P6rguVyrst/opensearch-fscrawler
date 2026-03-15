# Licensed under the Apache License, Version 2.0
"""FSCrawler CLI entry point."""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from watchdog.observers import Observer

import click
import uvicorn

from fscrawler import __version__
from fscrawler.rest_server import CrawlerState, create_app
from fscrawler.watcher import FsEventHandler

logger = logging.getLogger("fscrawler.cli")


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("job_name", default="fscrawler")
@click.option(
    "--config_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    envvar="FSCRAWLER_CONFIG_DIR",
    help="Base configuration directory. Defaults to ~/.fscrawler",
)
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
@click.option(
    "--loop",
    is_flag=True,
    default=False,
    help="Run continuously, sleeping update_rate seconds between crawls.",
)
@click.option(
    "--rest",
    is_flag=True,
    default=False,
    help="Start the REST API server (host/port from rest.url in _settings.yaml).",
)
@click.option(
    "--setup",
    is_flag=True,
    default=False,
    help="Create an example _settings.yaml and exit.",
)
# TODO(#missing): --restart — restart job as if it never ran (Java parity)
# TODO(#missing): --list   — list all configured jobs (Java parity)
# TODO(#missing): --upgrade — upgrade Elasticsearch indices (Java parity)
# TODO(#missing): --loop <N> integer form — run exactly N loops (Java parity; currently boolean)
@click.option(
    "--log-format",
    type=click.Choice(["json", "text"], case_sensitive=False),
    default="json",
    show_default=True,
    envvar="FSCRAWLER_LOG_FORMAT",
    help="Log format: 'json' (OTel-structured) or 'text' (human-readable).",
)
@click.option(
    "--log-output",
    type=click.Choice(["stdout", "stderr", "file", "otel"], case_sensitive=False),
    default="stdout",
    show_default=True,
    envvar="FSCRAWLER_LOG_OUTPUT",
    help="Log destination: stdout, stderr, file, or otel (OTLP/HTTP collector).",
)
@click.option(
    "--log-file",
    default=None,
    type=click.Path(path_type=Path),
    envvar="FSCRAWLER_LOG_FILE",
    help="Path to log file. Required when --log-output=file.",
)
@click.option(
    "--log-otel-endpoint",
    default=None,
    envvar="FSCRAWLER_LOG_OTEL_ENDPOINT",
    help="OTLP/HTTP base URL (e.g. http://collector:4318). Required when --log-output=otel.",
)
@click.version_option(__version__, prog_name="fscrawler")
def main(
    job_name: str,
    config_dir: Path | None,
    debug: bool,
    loop: bool,
    rest: bool,
    setup: bool,
    log_format: str,
    log_output: str,
    log_file: Path | None,
    log_otel_endpoint: str | None,
) -> None:
    """FSCrawler — index files into OpenSearch / Elasticsearch."""
    from fscrawler.logging_config import configure_logging, install_exception_hook

    configure_logging(
        level="DEBUG" if debug else "INFO",
        fmt=log_format,
        output=log_output,
        file_path=log_file,
        otel_endpoint=log_otel_endpoint,
    )
    install_exception_hook()

    if config_dir is None:
        config_dir = Path.home() / ".fscrawler"

    job_dir = config_dir / job_name
    settings_file = job_dir / "_settings.yaml"

    if setup:
        _do_setup(job_dir, settings_file)
        return

    if not settings_file.exists():
        logger.error(
            "Settings file not found: %s — run with --setup to create a template.",
            settings_file,
        )
        sys.exit(1)

    try:
        if rest:
            _run_rest(settings_file, job_dir)
        else:
            _run(job_name, settings_file, job_dir, loop)
    except Exception:
        logger.critical("fscrawler terminated — unhandled error", exc_info=True)
        sys.exit(1)


def _run_rest(settings_file: Path, job_dir: Path) -> None:
    """Load settings, start background crawler, build the FastAPI app and serve it."""
    import os

    from fscrawler.client import FsCrawlerClient
    from fscrawler.settings import FsSettings

    settings = FsSettings.from_file(settings_file, environ=dict(os.environ))
    logger.info("Starting REST server for job: %s", settings.name)

    client = FsCrawlerClient(settings)
    client.wait_for_cluster()
    client.push_templates()
    client.ensure_index(settings.elasticsearch.index)
    client.ensure_index(settings.elasticsearch.index_folder)

    crawler_state = CrawlerState()

    # Background crawler thread — mirrors Java's dual REST+crawl mode.
    # The thread is a daemon so it exits automatically when uvicorn shuts down.
    bg_thread = threading.Thread(
        target=_crawler_loop,
        args=(settings, client, job_dir, crawler_state),
        daemon=True,
        name="fscrawler-bg",
    )
    bg_thread.start()
    logger.info("Background crawler started (update_rate=%.0fs)", settings.fs.update_rate)

    app = create_app(settings=settings, client=client, crawler_state=crawler_state)

    parsed = urlparse(settings.rest.url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8080

    logger.info("FSCrawler REST service starting on %s:%d", host, port)
    # log_config=None prevents uvicorn from calling logging.config.dictConfig()
    # with its own LOGGING_CONFIG, which would install plain-text handlers on
    # the uvicorn.* loggers and break our OTel JSON formatter.
    uvicorn.run(app, host=host, port=port, log_config=None)


def _crawler_loop(
    settings: object,
    client: object,
    job_dir: Path,
    crawler_state: CrawlerState,
) -> None:
    """Initial full scan followed by watchdog event-driven indexing.

    1. Runs one full scan on startup to index all existing files.
    2. Starts a watchdog Observer to immediately process create/modify/delete
       events as they happen — no polling delay.
    3. Errors in the initial scan are logged but do not prevent the observer
       from starting.
    """
    from fscrawler.parser import TikaParser
    from fscrawler.settings import FsSettings

    assert isinstance(settings, FsSettings)
    parser = TikaParser(settings, tika_url=settings.fs.tika_url)

    # Initial scan — index everything already in the directory.
    try:
        _crawl_once(settings, client, parser, job_dir)  # type: ignore[arg-type]
    except Exception as exc:
        logger.error("Initial crawl failed: %s", exc, exc_info=True)

    # Start watchdog observer for real-time event-driven indexing.
    handler = FsEventHandler(settings, client, parser, crawler_state)
    observer = Observer()
    observer.schedule(handler, str(settings.fs.url), recursive=True)
    observer.start()
    logger.info("Watchdog observer started on %s", settings.fs.url)

    try:
        while observer.is_alive():
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()


def _crawl_once(
    settings: object,
    client: object,
    parser: object,
    job_dir: Path,
) -> None:
    """Execute one full crawl pass: scan, index new/modified, delete removed."""
    from fscrawler.client import FsCrawlerClient
    from fscrawler.crawler import LocalCrawler
    from fscrawler.indexer import BulkIndexer
    from fscrawler.parser import TikaParser
    from fscrawler.settings import FsSettings

    assert isinstance(settings, FsSettings)
    assert isinstance(client, FsCrawlerClient)
    assert isinstance(parser, TikaParser)

    crawler = LocalCrawler(settings, config_dir=job_dir)
    with BulkIndexer(client, settings) as indexer:
        for folder_path in crawler.scan_folders():
            from fscrawler.models import FolderDocument, PathInfo as _PathInfo
            from pathlib import Path as _Path
            _root = _Path(settings.fs.url)
            _rel = folder_path.relative_to(_root)
            virtual = "/" if str(_rel) == "." else "/" + _rel.as_posix()
            indexer.add_folder(FolderDocument(path=_PathInfo(
                real=str(folder_path),
                root=str(_root),
                virtual=virtual,
            )))

        for file_path in crawler.scan():
            if crawler.is_new_or_modified(file_path):
                try:
                    doc = parser.parse(file_path)
                    indexer.add(doc)
                except Exception as exc:
                    if settings.fs.continue_on_error:
                        logger.warning(
                            "Error parsing %s — skipping (continue_on_error=true)",
                            file_path,
                            exc_info=exc,
                        )
                    else:
                        raise

        for deleted_path in crawler.get_deleted_files():
            indexer.delete(deleted_path)

    crawler.save_checkpoint()


def _run(job_name: str, settings_file: Path, job_dir: Path, loop: bool) -> None:
    """Load settings and start the crawl (single run or loop)."""
    import os

    from fscrawler.client import FsCrawlerClient
    from fscrawler.parser import TikaParser
    from fscrawler.settings import FsSettings

    settings = FsSettings.from_file(settings_file, environ=dict(os.environ))
    logger.info("Loaded settings for job: %s", settings.name)

    client = FsCrawlerClient(settings)
    client.wait_for_cluster()
    client.push_templates()
    client.ensure_index(settings.elasticsearch.index)
    client.ensure_index(settings.elasticsearch.index_folder)

    parser = TikaParser(settings, tika_url=settings.fs.tika_url)

    if loop:
        while True:
            _crawl_once(settings, client, parser, job_dir)
            sleep_secs = settings.fs.update_rate
            logger.info("Sleeping %.0f seconds until next crawl…", sleep_secs)
            time.sleep(sleep_secs)
    else:
        _crawl_once(settings, client, parser, job_dir)


def _do_setup(job_dir: Path, settings_file: Path) -> None:
    """Write a template _settings.yaml to the job directory."""
    job_dir.mkdir(parents=True, exist_ok=True)
    if settings_file.exists():
        click.echo(f"Settings file already exists: {settings_file}")
        return

    template = f"""\
name: "{job_dir.name}"
fs:
  url: "/data"
  update_rate: "15m"
  includes: []
  excludes: []
  follow_symlinks: false
  remove_deleted: true
  continue_on_error: false
  index_content: true
  add_filesize: true
  index_folders: true
  checksum: "MD5"
elasticsearch:
  nodes:
    - url: "http://localhost:9200"
  ssl_verification: false
  bulk_size: 100

  byte_size: "10mb"
  push_templates: true
rest:
  url: "http://0.0.0.0:8080"
  enable_cors: false
"""
    settings_file.write_text(template, encoding="utf-8")
    click.echo(f"Created settings file: {settings_file}")
    click.echo("Edit it and then run fscrawler without --setup.")


if __name__ == "__main__":
    main()
