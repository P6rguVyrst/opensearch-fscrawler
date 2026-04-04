# Licensed under the Apache License, Version 2.0
"""FSCrawler CLI entry point."""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from fscrawler.client import FsCrawlerClient
    from fscrawler.parser import TikaParser
    from fscrawler.settings import FsSettings

import click
import uvicorn
from watchdog.observers import Observer

from fscrawler import __version__
from fscrawler.dlq import run_retry_cycle
from fscrawler.metrics import wal_records
from fscrawler.rest_server import CrawlerState, create_app
from fscrawler.wal import WriteAheadLog
from fscrawler.watcher import FsEventHandler

logger = logging.getLogger("fscrawler.cli")

_MAX_OBSERVER_RESTARTS = 5


# I1: Shared observer health-check + restart loop used by both
# _crawler_loop() and _run() to eliminate duplication.
def _watch_with_restarts(
    handler: Any,
    path: str,
    observer: Observer,
    stop_event: threading.Event | None = None,
) -> None:
    """Monitor a watchdog Observer and restart it up to _MAX_OBSERVER_RESTARTS times.

    Parameters
    ----------
    handler:
        The watchdog event handler to schedule on each new Observer.
    path:
        Filesystem path to watch (recursive).
    observer:
        An already-started Observer instance.
    stop_event:
        Optional threading.Event to set in the finally block (used by --loop mode).
    """
    restarts = 0
    try:
        while True:
            if observer.is_alive():
                time.sleep(1)
                continue
            if restarts >= _MAX_OBSERVER_RESTARTS:
                logger.error(
                    "Watchdog observer died %d times, giving up", restarts
                )
                break
            restarts += 1
            logger.warning(
                "Watchdog observer died, restarting (attempt %d/%d)",
                restarts,
                _MAX_OBSERVER_RESTARTS,
            )
            observer = Observer()
            observer.schedule(handler, path, recursive=True)
            observer.start()
    finally:
        if stop_event is not None:
            stop_event.set()
        observer.stop()
        observer.join()


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
    help="Run continuously, watching for filesystem changes via watchdog.",
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
# TODO(#missing): --restart -- restart job as if it never ran (Java parity)
# TODO(#missing): --list   -- list all configured jobs (Java parity)
# TODO(#missing): --upgrade -- upgrade Elasticsearch indices (Java parity)
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
    "--otel-endpoint",
    default=None,
    envvar="FSCRAWLER_OTEL_ENDPOINT",
    help="OTLP/HTTP base URL (e.g. http://collector:4318). "
         "Logs sent to {URL}/v1/logs, metrics to {URL}/v1/metrics.",
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
    otel_endpoint: str | None,
) -> None:
    """FSCrawler -- index files into OpenSearch / Elasticsearch."""
    from fscrawler.logging_config import configure_logging, install_exception_hook

    configure_logging(
        level="DEBUG" if debug else "INFO",
        fmt=log_format,
        output=log_output,
        file_path=log_file,
        otel_endpoint=otel_endpoint,
    )
    install_exception_hook()

    from fscrawler.metrics import configure_metrics

    configure_metrics(otel_endpoint=otel_endpoint, enable_prometheus=rest)

    if config_dir is None:
        config_dir = Path.home() / ".fscrawler"

    job_dir = config_dir / job_name
    settings_file = job_dir / "_settings.yaml"

    if setup:
        _do_setup(job_dir, settings_file)
        return

    if not settings_file.exists():
        logger.error(
            "Settings file not found: %s -- run with --setup to create a template.",
            settings_file,
        )
        sys.exit(1)

    try:
        if rest:
            _run_rest(settings_file, job_dir, otel_endpoint=otel_endpoint)
        else:
            _run(job_name, settings_file, job_dir, loop)
    except Exception:
        logger.critical("fscrawler terminated -- unhandled error", exc_info=True)
        sys.exit(1)


def _ensure_dlq_indices(client: FsCrawlerClient) -> None:
    """Ensure the DLQ and PFQ indices exist."""
    from fscrawler.dlq import DLQ_INDEX, PFQ_INDEX

    client.ensure_index(DLQ_INDEX)
    client.ensure_index(PFQ_INDEX)


def _recover_wal(client: FsCrawlerClient, wal: WriteAheadLog) -> None:
    """Replay un-checkpointed WAL records on startup."""
    if wal.is_empty:
        return

    records = wal.read()
    logger.info("WAL: recovering %d records from %s", len(records), wal.path)

    operations: list[dict[str, Any]] = []
    doc_ids: set[str] = set()
    for record in records:
        doc_id = record["doc_id"]
        target_index = record["target_index"]
        action = record["action"]
        payload = record.get("payload")
        pipeline = record.get("pipeline")

        if action == "index" and payload:
            bulk_action: dict[str, Any] = {"index": {"_index": target_index, "_id": doc_id}}
            if pipeline:
                bulk_action["index"]["pipeline"] = pipeline
            operations.append(bulk_action)
            operations.append(payload)
        elif action == "delete":
            operations.append({"delete": {"_index": target_index, "_id": doc_id}})
        doc_ids.add(doc_id)

    if operations:
        try:
            response = client.bulk(operations)
        except Exception as exc:
            logger.error("WAL: recovery bulk failed: %s", exc)
            return

        succeeded_ids = set(doc_ids)
        if response.get("errors"):
            for item in response.get("items", []):
                op = item.get("index") or item.get("delete") or {}
                if op.get("error"):
                    failed_id = op.get("_id", "")
                    succeeded_ids.discard(failed_id)
                    logger.warning("WAL: recovery failed for %s: %s", failed_id, op["error"])

        if succeeded_ids:
            wal.checkpoint(succeeded_ids)
            logger.info("WAL: recovery complete, %d/%d records succeeded", len(succeeded_ids), len(records))
        else:
            logger.warning("WAL: all %d recovery records failed, nothing checkpointed", len(records))

        for _ in range(len(succeeded_ids)):
            wal_records.add(1, {"fscrawler.wal.action": "recover"})


def _start_dlq_retry_thread(
    client: FsCrawlerClient,
    config: Any,
    stop_event: threading.Event,
    job_name: str | None = None,
) -> threading.Thread:
    """Start a daemon thread that periodically drains the DLQ."""

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                run_retry_cycle(client, config, job_name=job_name)
            except Exception as exc:
                logger.error("DLQ retry thread error: %s", exc)
            stop_event.wait(timeout=config.check_interval)

    thread = threading.Thread(target=_loop, daemon=True, name="fscrawler-dlq-retry")
    thread.start()
    return thread


def _run_rest(settings_file: Path, job_dir: Path, *, otel_endpoint: str | None = None) -> None:
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

    if settings.fs.keep_history:
        client.ensure_index(settings.elasticsearch.index_history)

    _ensure_dlq_indices(client)

    wal = WriteAheadLog(job_dir / ".wal", job_name=settings.name)
    _recover_wal(client, wal)

    stop_event = threading.Event()
    _start_dlq_retry_thread(client, settings.elasticsearch.dlq, stop_event, job_name=settings.name)

    crawler_state = CrawlerState()

    # Background crawler thread -- mirrors Java's dual REST+crawl mode.
    # The thread is a daemon so it exits automatically when uvicorn shuts down.
    bg_thread = threading.Thread(
        target=_crawler_loop,
        args=(settings, client, job_dir, crawler_state, wal),
        daemon=True,
        name="fscrawler-bg",
    )
    bg_thread.start()
    logger.info("Background crawler started (watchdog event-driven)")

    app = create_app(settings=settings, client=client, crawler_state=crawler_state)

    parsed = urlparse(settings.rest.url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8080

    logger.info("FSCrawler REST service starting on %s:%d", host, port)
    # log_config=None prevents uvicorn from calling logging.config.dictConfig()
    # with its own LOGGING_CONFIG, which would install plain-text handlers on
    # the uvicorn.* loggers and break our OTel JSON formatter.
    uvicorn.run(app, host=host, port=port, log_config=None)  # pragma: no cover


def _crawler_loop(
    settings: FsSettings,
    client: FsCrawlerClient,
    job_dir: Path,
    crawler_state: CrawlerState,
    wal: WriteAheadLog | None = None,
) -> None:
    """Initial full scan followed by watchdog event-driven indexing.

    1. Runs one full scan on startup to index all existing files.
    2. Starts a watchdog Observer to immediately process create/modify/delete
       events as they happen -- no polling delay.
    3. Errors in the initial scan are logged but do not prevent the observer
       from starting.
    """
    from fscrawler.parser import TikaParser

    parser = TikaParser(settings, tika_url=settings.fs.tika_url)

    # Initial scan -- index everything already in the directory.
    try:
        _crawl_once(settings, client, parser, job_dir, wal=wal)
    except Exception as exc:
        logger.error("Initial crawl failed: %s", exc, exc_info=True)

    # Start watchdog observer for real-time event-driven indexing.
    handler = FsEventHandler(settings, client, parser, crawler_state, wal=wal)
    observer = Observer()
    observer.schedule(handler, str(settings.fs.url), recursive=True)
    observer.start()
    logger.info("Watchdog observer started on %s", settings.fs.url)

    _watch_with_restarts(handler, str(settings.fs.url), observer)


def _crawl_once(
    settings: FsSettings,
    client: FsCrawlerClient,
    parser: TikaParser,
    job_dir: Path,
    wal: WriteAheadLog | None = None,
) -> None:
    """Execute one full crawl pass: scan, index new/modified, delete removed."""
    from fscrawler.crawler import LocalCrawler
    from fscrawler.indexer import BulkIndexer
    from fscrawler.models import FolderDocument, PathInfo

    crawler = LocalCrawler(settings, config_dir=job_dir)
    root = Path(settings.fs.url)

    with BulkIndexer(client, settings, wal=wal) as indexer:
        for folder_path in crawler.scan_folders():
            rel = folder_path.relative_to(root)
            virtual = "/" if str(rel) == "." else "/" + rel.as_posix()
            indexer.add_folder(FolderDocument(path=PathInfo(
                real=str(folder_path),
                root=str(root),
                virtual=virtual,
            )))

        for file_path in crawler.scan():
            if crawler.is_new_or_modified(file_path):
                try:
                    if crawler.exceeds_size_limit(file_path):
                        doc = parser.parse_metadata_only(file_path)
                    else:
                        doc = parser.parse(file_path)
                    indexer.add(doc)
                except Exception as exc:
                    if settings.fs.continue_on_error:
                        logger.warning(
                            "Error parsing %s -- skipping (continue_on_error=true)",
                            file_path,
                            exc_info=exc,
                        )
                    else:
                        raise

        for virtual_path in crawler.get_deleted_files():
            indexer.delete(virtual_path)

    crawler.save_checkpoint()


def _run(job_name: str, settings_file: Path, job_dir: Path, loop: bool) -> None:
    """Load settings and start the crawl (single run or watchdog loop)."""
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

    if settings.fs.keep_history:
        client.ensure_index(settings.elasticsearch.index_history)

    _ensure_dlq_indices(client)

    wal = WriteAheadLog(job_dir / ".wal", job_name=settings.name)
    _recover_wal(client, wal)

    parser = TikaParser(settings, tika_url=settings.fs.tika_url)

    # Always do an initial full scan
    _crawl_once(settings, client, parser, job_dir, wal=wal)

    if not loop:
        logger.info("Single-run mode -- DLQ retry thread not started. Use --loop or --rest for automatic retries.")

    if loop:
        stop_event = threading.Event()
        _start_dlq_retry_thread(client, settings.elasticsearch.dlq, stop_event, job_name=settings.name)

        # Stay alive with watchdog event-driven indexing
        handler = FsEventHandler(settings, client, parser, CrawlerState(), wal=wal)
        observer = Observer()
        observer.schedule(handler, str(settings.fs.url), recursive=True)
        observer.start()
        logger.info("Watchdog observer started on %s", settings.fs.url)

        _watch_with_restarts(handler, str(settings.fs.url), observer, stop_event=stop_event)


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
  includes: []
  excludes: []
  follow_symlinks: false
  remove_deleted: false
  continue_on_error: false
  index_content: true
  add_filesize: true
  index_folders: true
  checksum: "sha256"
  keep_history: false
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


if __name__ == "__main__":  # pragma: no cover
    main()
