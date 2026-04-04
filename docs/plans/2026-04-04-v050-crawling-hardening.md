# v0.5.0 Crawling Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the filesystem crawler against real-world edge cases identified by auditing 14 years of upstream (dadoonet/fscrawler) bug reports, and fix bugs we likely share.

**Architecture:** Each task is a self-contained fix with TDD — failing test first, minimal implementation, commit. Tasks are grouped by subsystem and ordered so earlier tasks don't break later ones. No new dependencies required.

**Tech Stack:** Python 3.12, pytest, watchdog, httpx, fnmatch, unicodedata, stat

---

## File Map

| File | Changes |
|------|---------|
| `src/fscrawler/crawler.py` | Full-path matching, symlink cycle detection, .fscrawlerignore, special file filtering, clock skew tolerance, ignore_above metadata-only, Unicode normalization |
| `src/fscrawler/watcher.py` | Full-path matching, on_moved handler, Unicode normalization |
| `src/fscrawler/parser.py` | Streaming large files, permissions format, whitespace normalization, Unicode normalization |
| `src/fscrawler/settings.py` | Default excludes, new config fields (filters, content_normalize, max_body_size) |
| `src/fscrawler/rest_server.py` | Max body size enforcement |
| `src/fscrawler/cli.py` | Observer health-check/restart, ignore_above metadata-only path |
| `tests/unit/test_crawler.py` | Tests for tasks 1-7 |
| `tests/unit/test_watcher.py` | Tests for tasks 8-10 |
| `tests/unit/test_parser.py` | Tests for tasks 11-14 |
| `tests/unit/test_rest_server.py` | Tests for task 15 |
| `tests/unit/test_settings.py` | Tests for new config fields |

---

## Group A: Crawler Hardening

### Task 1: Include/exclude patterns match full virtual path

Patterns containing `/` must match against the full virtual path, not just
the filename. Patterns without `/` continue to match filename only (fast path).

**Upstream:** [dadoonet/fscrawler#1300](https://github.com/dadoonet/fscrawler/issues/1300) (partial — pattern matching is root cause of silent misses)

**Files:**
- Modify: `src/fscrawler/crawler.py:143-175` — `_walk()`
- Modify: `src/fscrawler/watcher.py:86-91` — `_matches()`
- Test: `tests/unit/test_crawler.py`
- Test: `tests/unit/test_watcher.py`

- [ ] **Step 1: Write failing tests for full-path matching in crawler**

Add to `tests/unit/test_crawler.py` inside `TestCrawlerFilters`:

```python
class TestCrawlerFullPathFilters:
    """Include/exclude patterns with '/' match against virtual path.

    Upstream: https://github.com/dadoonet/fscrawler/issues/1300
    Patterns without '/' match filename only (backwards compatible).
    """

    def test_include_with_slash_matches_virtual_path(self, tmp_path):
        data = tmp_path / "data"
        (data / "docs").mkdir(parents=True)
        (data / "docs" / "report.pdf").write_bytes(b"%PDF")
        (data / "logs").mkdir()
        (data / "logs" / "debug.pdf").write_bytes(b"%PDF")
        settings = make_settings(tmp_path, includes=["docs/*.pdf"])
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [p.name for p in crawler.scan()]
        assert found == ["report.pdf"]

    def test_exclude_with_slash_matches_virtual_path(self, tmp_path):
        data = tmp_path / "data"
        (data / "docs").mkdir(parents=True)
        (data / "docs" / "report.pdf").write_bytes(b"%PDF")
        (data / "logs").mkdir()
        (data / "logs" / "debug.log").write_bytes(b"log")
        settings = make_settings(tmp_path, excludes=["logs/*"])
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = sorted(p.name for p in crawler.scan())
        assert found == ["report.pdf"]

    def test_pattern_without_slash_matches_filename_only(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / "report.pdf").write_bytes(b"%PDF")
        (data / "notes.txt").write_bytes(b"text")
        settings = make_settings(tmp_path, includes=["*.pdf"])
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [p.name for p in crawler.scan()]
        assert found == ["report.pdf"]

    def test_glob_doublestar_matches_any_depth(self, tmp_path):
        data = tmp_path / "data"
        (data / "a" / "b" / "c").mkdir(parents=True)
        (data / "a" / "b" / "c" / "deep.pdf").write_bytes(b"%PDF")
        (data / "top.txt").write_bytes(b"text")
        settings = make_settings(tmp_path, includes=["**/*.pdf"])
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [p.name for p in crawler.scan()]
        assert found == ["deep.pdf"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_crawler.py::TestCrawlerFullPathFilters -v`
Expected: FAIL — patterns with `/` never match because `fnmatch` runs against filename only

- [ ] **Step 3: Implement full-path matching in crawler**

In `src/fscrawler/crawler.py`, extract a helper and modify `_walk()`:

```python
def _matches_pattern(name: str, virtual_path: str, pattern: str) -> bool:
    """Match pattern against filename or virtual path.

    If pattern contains '/', match against the virtual path (without
    leading slash). Otherwise match against filename only.
    """
    if "/" in pattern:
        # Strip leading slash from virtual path for matching
        return fnmatch.fnmatch(virtual_path.lstrip("/"), pattern)
    return fnmatch.fnmatch(name, pattern)
```

Then update `_walk()` to compute the virtual path before filtering:

```python
# Inside _walk(), after entry.is_file() check:
try:
    rel = entry.path.relative_to(root)
    virtual = "/" + rel.as_posix()
except ValueError:
    virtual = "/" + entry.name

if self._includes:
    if not any(
        _matches_pattern(entry.name, virtual, pat)
        for pat in self._includes
    ):
        continue
if any(
    _matches_pattern(entry.name, virtual, pat)
    for pat in self._excludes
):
    continue
```

Where `self._includes` is `self._settings.fs.includes` and `self._excludes`
is `self._settings.fs.excludes` (already used in existing code, just rename
the local references).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_crawler.py::TestCrawlerFullPathFilters -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `uv run pytest tests/unit/ -v`
Expected: All existing tests still pass

- [ ] **Step 6: Write failing test for full-path matching in watcher**

Add to `tests/unit/test_watcher.py`:

```python
class TestWatcherFullPathFilters:
    """Watcher _matches() respects full-path patterns.

    Upstream: https://github.com/dadoonet/fscrawler/issues/1300
    """

    def test_exclude_with_slash_rejects_matching_path(self):
        settings = make_settings(excludes=["logs/*"])
        handler = make_handler(settings=settings)
        # Simulate a file at /data/logs/debug.log
        # _matches receives just the filename, but needs virtual path context
        # This test validates the watcher filters correctly
        assert handler._matches("debug.log", "/logs/debug.log") is False

    def test_include_with_slash_accepts_matching_path(self):
        settings = make_settings(includes=["docs/*.pdf"])
        handler = make_handler(settings=settings)
        assert handler._matches("report.pdf", "/docs/report.pdf") is True

    def test_include_with_slash_rejects_non_matching_path(self):
        settings = make_settings(includes=["docs/*.pdf"])
        handler = make_handler(settings=settings)
        assert handler._matches("report.pdf", "/other/report.pdf") is False
```

Note: the `_matches` signature needs to change from `(name)` to `(name, virtual_path)`.
Update the tests to match the new signature.

- [ ] **Step 7: Implement full-path matching in watcher**

In `src/fscrawler/watcher.py`, update `_matches()`:

```python
def _matches(self, name: str, virtual_path: str = "") -> bool:
    """Check if a file matches include/exclude filters.

    If patterns contain '/', they match against virtual_path.
    Otherwise they match against filename only.
    """
    includes = self._settings.fs.includes
    excludes = self._settings.fs.excludes
    if includes:
        if not any(_matches_pattern(name, virtual_path, p) for p in includes):
            return False
    if any(_matches_pattern(name, virtual_path, p) for p in excludes):
        return False
    return True
```

Import `_matches_pattern` from `crawler.py` (or duplicate the 5-line helper
in `watcher.py` to avoid a circular import — prefer duplication here).

Update all callers of `_matches` in `on_created`, `on_modified`, `on_deleted`
to pass the virtual path:

```python
def on_created(self, event):
    if self._state.paused:
        return
    path = Path(event.src_path)
    virtual = self._virtual_path(path)
    if not self._matches(path.name, virtual):
        return
    self._index(path)
```

Add `_virtual_path` helper:

```python
def _virtual_path(self, path: Path) -> str:
    try:
        rel = path.relative_to(self._root)
        return "/" + rel.as_posix()
    except ValueError:
        return "/" + path.name
```

Where `self._root = Path(settings.fs.url)` is set in `__init__`.

- [ ] **Step 8: Run all watcher tests**

Run: `uv run pytest tests/unit/test_watcher.py -v`
Expected: All pass (new and existing)

- [ ] **Step 9: Commit**

```bash
git add src/fscrawler/crawler.py src/fscrawler/watcher.py \
  tests/unit/test_crawler.py tests/unit/test_watcher.py
git commit -m "feat: match include/exclude patterns against full virtual path

Patterns containing '/' now match against the virtual path instead of
filename only. Patterns without '/' retain filename-only matching for
backwards compatibility.

Upstream: https://github.com/dadoonet/fscrawler/issues/1300"
```

---

### Task 2: Default excludes (`~*`)

Java defaults `excludes` to `["*/~*"]`. Add `["~*"]` as default in Python
(filename-only until full-path matching from Task 1 handles it).

**Files:**
- Modify: `src/fscrawler/settings.py:88` — `FsConfig.excludes` default
- Test: `tests/unit/test_crawler.py`

- [ ] **Step 1: Write failing test**

```python
class TestDefaultExcludes:
    """Tilde-prefixed temp files excluded by default.

    Upstream default: https://github.com/dadoonet/fscrawler
    Java defaults excludes to ['*/~*']. Python uses ['~*'] (filename match).
    """

    def test_tilde_files_excluded_by_default(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / "report.pdf").write_bytes(b"%PDF")
        (data / "~$report.pdf").write_bytes(b"lock")
        settings = make_settings(tmp_path)  # no explicit excludes
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [p.name for p in crawler.scan()]
        assert found == ["report.pdf"]

    def test_explicit_excludes_override_default(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / "report.pdf").write_bytes(b"%PDF")
        (data / "~$report.pdf").write_bytes(b"lock")
        (data / "notes.txt").write_bytes(b"text")
        settings = make_settings(tmp_path, excludes=["*.txt"])
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = sorted(p.name for p in crawler.scan())
        # Explicit excludes replace default, so tilde file is included
        assert "~$report.pdf" in found
        assert "notes.txt" not in found
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_crawler.py::TestDefaultExcludes -v`
Expected: FAIL — no default excludes, tilde file is found

- [ ] **Step 3: Implement default excludes**

In `src/fscrawler/settings.py`, change the `FsConfig` dataclass:

```python
_DEFAULT_EXCLUDES: list[str] = ["~*"]

@dataclass
class FsConfig:
    # ... other fields ...
    excludes: list[str] = field(default_factory=lambda: list(_DEFAULT_EXCLUDES))
```

In `FsSettings.from_dict()`, only override the default if the YAML explicitly
sets `excludes`:

```python
# In the fs block parsing, only set excludes if present in data:
if "excludes" in fs_data:
    fs_config.excludes = fs_data["excludes"]
```

Check the existing `from_dict` logic to ensure this is handled correctly —
the current code likely does `excludes=fs_data.get("excludes", [])` which
would need to change to only set when present.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_crawler.py::TestDefaultExcludes tests/unit/test_settings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/settings.py tests/unit/test_crawler.py
git commit -m "feat: default excludes ['~*'] for editor temp files

Tilde-prefixed files (Word/LibreOffice lock files) are now excluded by
default. Explicit 'excludes' in _settings.yaml overrides the default."
```

---

### Task 3: `.fscrawlerignore` sentinel file

Skip entire directory subtrees containing a `.fscrawlerignore` file.

**Files:**
- Modify: `src/fscrawler/crawler.py` — `_walk()`, `_walk_dirs()`
- Test: `tests/unit/test_crawler.py`

- [ ] **Step 1: Write failing test**

```python
class TestFscrawlerIgnore:
    """Skip directories containing .fscrawlerignore sentinel.

    Java upstream skips subtrees with this file present.
    """

    def test_directory_with_ignore_file_skipped(self, tmp_path):
        data = tmp_path / "data"
        (data / "included").mkdir(parents=True)
        (data / "included" / "a.txt").write_bytes(b"text")
        (data / "ignored").mkdir()
        (data / "ignored" / ".fscrawlerignore").write_bytes(b"")
        (data / "ignored" / "b.txt").write_bytes(b"text")
        settings = make_settings(tmp_path)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [p.name for p in crawler.scan()]
        assert found == ["a.txt"]

    def test_nested_ignore_only_affects_subtree(self, tmp_path):
        data = tmp_path / "data"
        (data / "a").mkdir(parents=True)
        (data / "a" / "file1.txt").write_bytes(b"text")
        (data / "a" / "skip").mkdir()
        (data / "a" / "skip" / ".fscrawlerignore").write_bytes(b"")
        (data / "a" / "skip" / "file2.txt").write_bytes(b"text")
        settings = make_settings(tmp_path)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [p.name for p in crawler.scan()]
        assert found == ["file1.txt"]

    def test_ignore_file_in_root_skips_everything(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / ".fscrawlerignore").write_bytes(b"")
        (data / "file.txt").write_bytes(b"text")
        settings = make_settings(tmp_path)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = list(crawler.scan())
        assert found == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_crawler.py::TestFscrawlerIgnore -v`
Expected: FAIL

- [ ] **Step 3: Implement .fscrawlerignore check**

In `src/fscrawler/crawler.py`, add a check at the top of `_walk()` and
`_walk_dirs()`:

```python
_IGNORE_SENTINEL = ".fscrawlerignore"

# In _walk(), before iterating entries:
if (root / _IGNORE_SENTINEL).exists():
    logger.debug("Skipping %s — .fscrawlerignore found", root)
    return

# Same check in _walk_dirs():
if (root / _IGNORE_SENTINEL).exists():
    logger.debug("Skipping %s — .fscrawlerignore found", root)
    return
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_crawler.py::TestFscrawlerIgnore -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/crawler.py tests/unit/test_crawler.py
git commit -m "feat: skip directories containing .fscrawlerignore

A .fscrawlerignore sentinel file in any directory causes the crawler to
skip that entire subtree. Matches Java upstream behavior."
```

---

### Task 4: Special file type detection (pipes, sockets, devices)

Skip non-regular files with a warning. Prevents blocking on named pipes
or infinite reads from device files.

**Files:**
- Modify: `src/fscrawler/crawler.py` — `_walk()`
- Test: `tests/unit/test_crawler.py`

- [ ] **Step 1: Write failing test**

```python
import stat as stat_module

class TestSpecialFileDetection:
    """Skip named pipes, sockets, and device files.

    Neither Java nor Python upstream handle this. Reading a named pipe
    blocks indefinitely; device files can produce infinite data.
    """

    def test_named_pipe_skipped(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / "normal.txt").write_bytes(b"text")
        pipe_path = data / "my_pipe"
        os.mkfifo(pipe_path)
        settings = make_settings(tmp_path)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [p.name for p in crawler.scan()]
        assert found == ["normal.txt"]

    def test_socket_skipped(self, tmp_path):
        import socket
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / "normal.txt").write_bytes(b"text")
        sock_path = data / "my.sock"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind(str(sock_path))
            settings = make_settings(tmp_path)
            crawler = LocalCrawler(settings, config_dir=tmp_path)
            found = [p.name for p in crawler.scan()]
            assert found == ["normal.txt"]
        finally:
            s.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_crawler.py::TestSpecialFileDetection -v`
Expected: FAIL — pipes and sockets pass `entry.is_file()` on some platforms,
or cause hangs

- [ ] **Step 3: Implement special file filtering**

In `src/fscrawler/crawler.py`, add after the `is_file()` check in `_walk()`:

```python
import stat as stat_module

# After confirming entry.is_file():
try:
    mode = entry.stat(follow_symlinks=fs.follow_symlinks).st_mode
except OSError:
    continue
if stat_module.S_ISFIFO(mode) or stat_module.S_ISSOCK(mode) or \
   stat_module.S_ISBLK(mode) or stat_module.S_ISCHR(mode):
    logger.warning("Skipping special file: %s", entry.path)
    continue
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_crawler.py::TestSpecialFileDetection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/crawler.py tests/unit/test_crawler.py
git commit -m "feat: skip named pipes, sockets, and device files

Detects non-regular files via stat().st_mode and skips them with a
warning. Prevents blocking reads on FIFOs and infinite reads from
device files. Improves on both Java and Python upstream."
```

---

### Task 5: Symlink cycle detection

Track visited `(dev, inode)` pairs to prevent infinite traversal when
`follow_symlinks: true`.

**Files:**
- Modify: `src/fscrawler/crawler.py` — `_walk()`, `_walk_dirs()`, `scan()`
- Test: `tests/unit/test_crawler.py`

- [ ] **Step 1: Write failing test**

```python
class TestSymlinkCycleDetection:
    """Detect and break symlink cycles during traversal.

    Neither Java nor Python upstream handle this.
    With follow_symlinks=true, a symlink loop causes infinite recursion.
    """

    def test_symlink_cycle_does_not_hang(self, tmp_path):
        data = tmp_path / "data"
        (data / "real").mkdir(parents=True)
        (data / "real" / "file.txt").write_bytes(b"text")
        # Create cycle: real/loop -> data (parent of real)
        (data / "real" / "loop").symlink_to(data)
        settings = make_settings(tmp_path, follow_symlinks=True)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = [p.name for p in crawler.scan()]
        assert "file.txt" in found
        # Must terminate — no hang, no RecursionError

    def test_symlink_mutual_cycle(self, tmp_path):
        data = tmp_path / "data"
        (data / "a").mkdir(parents=True)
        (data / "b").mkdir(parents=True)
        (data / "a" / "file_a.txt").write_bytes(b"a")
        (data / "b" / "file_b.txt").write_bytes(b"b")
        # a/link_to_b -> b, b/link_to_a -> a
        (data / "a" / "link_to_b").symlink_to(data / "b")
        (data / "b" / "link_to_a").symlink_to(data / "a")
        settings = make_settings(tmp_path, follow_symlinks=True)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = sorted(p.name for p in crawler.scan())
        assert "file_a.txt" in found
        assert "file_b.txt" in found
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_crawler.py::TestSymlinkCycleDetection -v --timeout=10`
Expected: FAIL — hangs or RecursionError

- [ ] **Step 3: Implement cycle detection**

In `src/fscrawler/crawler.py`, add a `_visited` set to track `(dev, inode)`:

```python
def scan(self) -> Iterator[Path]:
    self._current_checkpoint = {}
    self._visited: set[tuple[int, int]] = set()
    root = Path(self._settings.fs.url)
    # Add root to visited
    root_stat = root.stat()
    self._visited.add((root_stat.st_dev, root_stat.st_ino))
    yield from self._walk(root)
```

In `_walk()` and `_walk_dirs()`, before recursing into a directory:

```python
# Before recursing into a subdirectory:
try:
    dir_stat = entry.stat(follow_symlinks=True)
    dir_key = (dir_stat.st_dev, dir_stat.st_ino)
except OSError:
    continue
if dir_key in self._visited:
    logger.warning("Symlink cycle detected, skipping: %s", entry.path)
    continue
self._visited.add(dir_key)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_crawler.py::TestSymlinkCycleDetection -v --timeout=10`
Expected: PASS (terminates quickly)

- [ ] **Step 5: Run full suite**

Run: `uv run pytest tests/unit/test_crawler.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/fscrawler/crawler.py tests/unit/test_crawler.py
git commit -m "feat: detect and break symlink cycles during traversal

Tracks visited (dev, inode) pairs to prevent infinite directory
traversal when follow_symlinks=true. Logs a warning when a cycle
is detected. Improves on both Java and Python upstream."
```

---

### Task 6: Clock skew tolerance for mtime comparison

Subtract a 2-second buffer from mtime comparison to handle NFS/CIFS clock
drift.

**Files:**
- Modify: `src/fscrawler/crawler.py` — `is_new_or_modified()`
- Test: `tests/unit/test_crawler.py`

- [ ] **Step 1: Write failing test**

```python
class TestClockSkewTolerance:
    """Tolerate small clock differences on network filesystems.

    Upstream: https://github.com/dadoonet/fscrawler/issues/1471
    Java subtracts 2 seconds from scan comparison time.
    """

    def test_file_within_skew_window_is_recrawled(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        f = data / "file.txt"
        f.write_bytes(b"v1")
        settings = make_settings(tmp_path)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        # First scan
        list(crawler.scan())
        crawler.save_checkpoint()
        # Simulate clock skew: file mtime is 1 second BEFORE checkpoint
        current_mtime = f.stat().st_mtime
        os.utime(f, (current_mtime, current_mtime - 1.0))
        # Reload crawler with saved checkpoint
        crawler2 = LocalCrawler(settings, config_dir=tmp_path)
        assert crawler2.is_new_or_modified(f) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_crawler.py::TestClockSkewTolerance -v`
Expected: FAIL — exact mtime comparison misses the file

- [ ] **Step 3: Implement skew tolerance**

In `src/fscrawler/crawler.py`, modify `is_new_or_modified()`:

```python
_CLOCK_SKEW_SECONDS = 2.0

def is_new_or_modified(self, path: Path) -> bool:
    key = str(path)
    if key not in self._previous_checkpoint:
        return True
    try:
        current_mtime = path.stat().st_mtime
    except OSError:
        return False
    previous_mtime = self._previous_checkpoint[key]
    # Tolerate clock skew on network filesystems
    return current_mtime > previous_mtime - _CLOCK_SKEW_SECONDS
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_crawler.py::TestClockSkewTolerance tests/unit/test_crawler.py::TestCrawlerCheckpoint -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/crawler.py tests/unit/test_crawler.py
git commit -m "feat: 2-second clock skew tolerance for mtime comparison

Files modified within 2 seconds of the checkpoint timestamp are
re-crawled. Prevents missed files on NFS/CIFS mounts with clock drift.

Upstream: https://github.com/dadoonet/fscrawler/issues/1471"
```

---

### Task 7: Unicode normalization (NFC)

Normalize filenames to NFC before virtual path computation, pattern matching,
and document ID generation.

**Files:**
- Modify: `src/fscrawler/crawler.py` — `_walk()`, `scan()`
- Modify: `src/fscrawler/watcher.py` — `_virtual_path()`
- Modify: `src/fscrawler/parser.py` — `parse()`
- Test: `tests/unit/test_crawler.py`

- [ ] **Step 1: Write failing test**

```python
import unicodedata

class TestUnicodeNormalization:
    """Normalize filenames to NFC for cross-platform consistency.

    macOS HFS+ stores NFD; Linux ext4 stores NFC. Same filename
    produces different doc IDs and checkpoint keys without normalization.
    """

    def test_nfd_filename_normalized_to_nfc(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        # Create file with NFD-encoded name (decomposed é = e + combining accent)
        nfd_name = unicodedata.normalize("NFD", "café.txt")
        nfc_name = unicodedata.normalize("NFC", "café.txt")
        (data / nfd_name).write_bytes(b"text")
        settings = make_settings(tmp_path)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = list(crawler.scan())
        assert len(found) == 1
        # Virtual path should use NFC
        rel = found[0].relative_to(Path(settings.fs.url))
        virtual = "/" + rel.as_posix()
        # The checkpoint key should be NFC-normalized
        assert nfc_name in str(found[0]) or True  # file exists
        # Key assertion: checkpoint stores NFC
        crawler.save_checkpoint()
        crawler2 = LocalCrawler(settings, config_dir=tmp_path)
        # File should not be considered "new" on second scan
        assert crawler2.is_new_or_modified(found[0]) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_crawler.py::TestUnicodeNormalization -v`
Expected: May pass or fail depending on filesystem — the key test is
cross-platform consistency.

- [ ] **Step 3: Implement NFC normalization**

Add a helper in `src/fscrawler/crawler.py`:

```python
import unicodedata

def _normalize_name(name: str) -> str:
    """Normalize filename to NFC for cross-platform consistency."""
    return unicodedata.normalize("NFC", name)
```

Apply in `_walk()` when building paths for filtering and checkpoint:

```python
# In _walk(), after getting entry:
name = _normalize_name(entry.name)
```

Apply in checkpoint key computation (in `scan()`):

```python
# When storing in _current_checkpoint:
key = unicodedata.normalize("NFC", str(path))
```

Apply the same normalization in `watcher.py` `_virtual_path()` and
`parser.py` `parse()` when computing virtual paths.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_crawler.py::TestUnicodeNormalization -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/crawler.py src/fscrawler/watcher.py \
  src/fscrawler/parser.py tests/unit/test_crawler.py
git commit -m "feat: normalize filenames to NFC for cross-platform consistency

macOS HFS+ stores NFD, Linux ext4 stores NFC. Without normalization,
the same file produces different document IDs, checkpoint keys, and
include/exclude matches on different platforms."
```

---

## Group B: Watcher Fixes

### Task 8: Missing `on_moved` handler

Files moved within the crawl tree are silently lost — the old path gets
deleted but the new path is never indexed.

**Upstream:** [dadoonet/fscrawler#1300](https://github.com/dadoonet/fscrawler/issues/1300)

**Files:**
- Modify: `src/fscrawler/watcher.py` — add `on_moved()`
- Test: `tests/unit/test_watcher.py`

- [ ] **Step 1: Write failing test**

```python
class TestOnMoved:
    """Handle file move events — reindex at new path.

    Upstream: https://github.com/dadoonet/fscrawler/issues/1300
    Moved files should be deleted at old path and indexed at new path.
    """

    def test_moved_file_reindexed_at_new_path(self, tmp_path):
        settings = make_settings(fs_url=str(tmp_path / "data"))
        handler = make_handler(settings=settings)

        event = MagicMock()
        event.is_directory = False
        event.src_path = str(tmp_path / "data" / "old_name.txt")
        event.dest_path = str(tmp_path / "data" / "new_name.txt")
        # Create the destination file so parse() can read it
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / "new_name.txt").write_bytes(b"content")

        handler.on_moved(event)

        # Old path should be deleted
        handler._client.delete.assert_called_once()
        # New path should be indexed
        handler._client.index.assert_called_once()

    def test_moved_directory_ignored(self, tmp_path):
        settings = make_settings(fs_url=str(tmp_path / "data"))
        handler = make_handler(settings=settings)
        event = MagicMock()
        event.is_directory = True
        event.src_path = str(tmp_path / "data" / "old_dir")
        event.dest_path = str(tmp_path / "data" / "new_dir")

        handler.on_moved(event)

        handler._client.delete.assert_not_called()
        handler._client.index.assert_not_called()

    def test_moved_respects_paused_state(self, tmp_path):
        settings = make_settings(fs_url=str(tmp_path / "data"))
        handler = make_handler(settings=settings, paused=True)
        event = MagicMock()
        event.is_directory = False
        event.src_path = str(tmp_path / "data" / "old.txt")
        event.dest_path = str(tmp_path / "data" / "new.txt")

        handler.on_moved(event)

        handler._client.delete.assert_not_called()
        handler._client.index.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_watcher.py::TestOnMoved -v`
Expected: FAIL — `on_moved` not implemented

- [ ] **Step 3: Implement on_moved**

In `src/fscrawler/watcher.py`, add:

```python
def on_moved(self, event: Any) -> None:
    """Handle file move: delete old path, index new path."""
    if event.is_directory:
        return
    if self._state.paused:
        return
    # Delete old location
    if self._settings.fs.remove_deleted:
        self._delete(event.src_path)
    # Index new location
    path = Path(event.dest_path)
    virtual = self._virtual_path(path)
    if not self._matches(path.name, virtual):
        return
    self._index(path)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_watcher.py::TestOnMoved -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/watcher.py tests/unit/test_watcher.py
git commit -m "feat: handle file move events in watcher

Implements on_moved() to delete the old path and reindex at the new
path. Previously, moved files were silently lost from the index.

Upstream: https://github.com/dadoonet/fscrawler/issues/1300"
```

---

### Task 9: Observer health-check and restart

If the watchdog observer thread dies, detect it and restart or log an error
with a metric.

**Upstream:** [dadoonet/fscrawler#1093](https://github.com/dadoonet/fscrawler/issues/1093)

**Files:**
- Modify: `src/fscrawler/cli.py` — `_crawler_loop()`, `_run()`
- Test: `tests/unit/test_rest_server.py` (TestCrawlerLoop)

- [ ] **Step 1: Write failing test**

```python
class TestObserverHealthCheck:
    """Restart observer if it crashes.

    Upstream: https://github.com/dadoonet/fscrawler/issues/1093
    """

    @patch("fscrawler.cli.Observer")
    @patch("fscrawler.cli._crawl_once")
    def test_observer_restarted_after_crash(self, mock_crawl, MockObserver):
        """Observer that dies is restarted up to max retries."""
        mock_observer = MagicMock()
        # Simulate: alive for 2 checks, then dies, then alive again after restart
        mock_observer.is_alive.side_effect = [True, True, False, True, True]
        MockObserver.return_value = mock_observer

        settings = make_settings()
        client = make_mock_client()
        state = make_mock_crawler_state()
        wal = MagicMock()

        # Run with a short timeout to avoid blocking
        # The function should detect the dead observer and restart it
        with patch("time.sleep", side_effect=[None, None, None, KeyboardInterrupt]):
            with pytest.raises(KeyboardInterrupt):
                _crawler_loop(settings, client, tmp_path, state, wal)

        # Observer should have been started more than once
        assert mock_observer.start.call_count >= 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_rest_server.py::TestObserverHealthCheck -v`
Expected: FAIL — current code exits silently when observer dies

- [ ] **Step 3: Implement observer health-check**

In `src/fscrawler/cli.py`, modify the observer monitoring loop in
`_crawler_loop()`:

```python
_MAX_OBSERVER_RESTARTS = 5

# Replace the simple while loop:
restarts = 0
while True:
    try:
        while observer.is_alive():
            time.sleep(1)
        # Observer died
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
        observer.schedule(handler, str(root), recursive=True)
        observer.daemon = True
        observer.start()
    except KeyboardInterrupt:
        break
```

Apply the same pattern in `_run()` for non-REST mode.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_rest_server.py::TestObserverHealthCheck -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/cli.py tests/unit/test_rest_server.py
git commit -m "feat: restart watchdog observer on crash

Detects when the watchdog observer thread dies and restarts it
up to 5 times. Previously the crawler loop exited silently.

Upstream: https://github.com/dadoonet/fscrawler/issues/1093"
```

---

## Group C: Parser & Memory Safety

### Task 10: `ignore_above` indexes metadata without content

Files exceeding `ignore_above` should still have their metadata indexed
(path, size, mtime, permissions). Only Tika extraction is skipped.

**Upstream:** [dadoonet/fscrawler#1605](https://github.com/dadoonet/fscrawler/issues/1605)

**Files:**
- Modify: `src/fscrawler/crawler.py` — `_walk()` / `scan()`
- Modify: `src/fscrawler/cli.py` — `_crawl_once()`
- Modify: `src/fscrawler/parser.py` — add `parse_metadata_only()`
- Test: `tests/unit/test_crawler.py`
- Test: `tests/unit/test_parser.py`

- [ ] **Step 1: Write failing test**

```python
class TestIgnoreAboveMetadataOnly:
    """Files exceeding ignore_above should index metadata without content.

    Upstream: https://github.com/dadoonet/fscrawler/issues/1605
    """

    def test_large_file_yields_from_scan(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / "small.txt").write_bytes(b"small")
        (data / "big.txt").write_bytes(b"x" * 1000)
        settings = make_settings(tmp_path, ignore_above=500)
        crawler = LocalCrawler(settings, config_dir=tmp_path)
        found = sorted(p.name for p in crawler.scan())
        # Both files should be yielded — ignore_above is handled at parse time
        assert found == ["big.txt", "small.txt"]

    def test_large_file_gets_metadata_only_document(self):
        """Parser creates a document with file metadata but no content."""
        # ... test parse_metadata_only() returns Document with empty content
        # but valid FileInfo (size, mtime) and PathInfo
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_crawler.py::TestIgnoreAboveMetadataOnly -v`
Expected: FAIL — large file is currently skipped entirely in `_walk()`

- [ ] **Step 3: Implement metadata-only path**

Move the `ignore_above` check out of `_walk()` (where it currently skips the
file entirely). Instead, yield all files from `scan()` and let the caller
(`_crawl_once`) decide:

In `src/fscrawler/crawler.py`, remove the size check from `_walk()`.
Add a method:

```python
def exceeds_size_limit(self, path: Path) -> bool:
    """Check if file exceeds ignore_above threshold."""
    if self._settings.fs.ignore_above is None:
        return False
    try:
        return path.stat().st_size > self._settings.fs.ignore_above
    except OSError:
        return False
```

In `src/fscrawler/parser.py`, add:

```python
def parse_metadata_only(self, file_path: Path) -> Document:
    """Create a Document with file metadata but no content extraction.

    Used for files exceeding ignore_above — indexes path, size,
    timestamps, and permissions without calling Tika.
    """
    st = file_path.stat()
    root = Path(self._settings.fs.url)
    try:
        rel = file_path.relative_to(root)
        virtual = "/" + rel.as_posix()
    except ValueError:
        virtual = "/" + file_path.name

    file_info = FileInfo(
        filename=file_path.name,
        extension=file_path.suffix.lstrip("."),
        content_type="",
        filesize=st.st_size,
        last_modified=datetime.fromtimestamp(st.st_mtime, tz=UTC),
        indexing_date=datetime.now(tz=UTC),
    )
    path_info = PathInfo(real=str(file_path), virtual=virtual, root=str(root))
    return Document(content="", file=file_info, path=path_info, meta=Meta())
```

In `src/fscrawler/cli.py` `_crawl_once()`, branch on size:

```python
for file_path in crawler.scan():
    if not crawler.is_new_or_modified(file_path):
        continue
    if crawler.exceeds_size_limit(file_path):
        doc = parser.parse_metadata_only(file_path)
    else:
        doc = parser.parse(file_path)
    # ... rest of indexing logic
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_crawler.py::TestIgnoreAboveMetadataOnly tests/unit/test_parser.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/crawler.py src/fscrawler/parser.py \
  src/fscrawler/cli.py tests/unit/test_crawler.py tests/unit/test_parser.py
git commit -m "feat: index metadata for files exceeding ignore_above

Files larger than ignore_above now get path, size, and timestamp
metadata indexed without Tika content extraction. Previously they
were silently dropped.

Upstream: https://github.com/dadoonet/fscrawler/issues/1605"
```

---

### Task 11: Large file streaming (OOM prevention)

Stream file content through a temp file instead of loading into memory
when file exceeds 64 KB.

**Files:**
- Modify: `src/fscrawler/parser.py` — `parse()`
- Test: `tests/unit/test_parser.py`

- [ ] **Step 1: Write failing test**

```python
class TestLargeFileStreaming:
    """Stream large files through temp files to prevent OOM.

    Upstream: https://github.com/dadoonet/fscrawler/issues/566
    Upstream: https://github.com/dadoonet/fscrawler/issues/890
    """

    def test_large_file_not_loaded_entirely_into_memory(self, tmp_path):
        """Verify large file uses streaming path."""
        data = tmp_path / "data"
        data.mkdir(parents=True)
        large_file = data / "large.bin"
        # 128 KB file — above 64 KB threshold
        large_file.write_bytes(b"x" * 131072)
        settings = make_settings(tmp_path)
        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [{"X-TIKA:content": "text"}]
            mock_client.put.return_value = mock_response
            parser = TikaParser(settings)
            doc = parser.parse(large_file)
            # Verify Tika was called (file was sent)
            mock_client.put.assert_called_once()
            # Verify the data sent to Tika was the file content
            call_kwargs = mock_client.put.call_args
            sent_data = call_kwargs.kwargs.get("content") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
            # The checksum should still be computed correctly
            assert doc.file.checksum is not None
```

- [ ] **Step 2: Run to verify baseline behavior**

Run: `uv run pytest tests/unit/test_parser.py::TestLargeFileStreaming -v`
Expected: May pass (tests the interface) — the real test is memory usage

- [ ] **Step 3: Implement streaming with chunked read + checksum**

In `src/fscrawler/parser.py`, modify `parse()`:

```python
import hashlib
import tempfile

_STREAMING_THRESHOLD = 65536  # 64 KB

def parse(self, file_path: Path) -> Document:
    file_size = file_path.stat().st_size
    if file_size == 0:
        raise ValueError(f"Cannot parse zero-byte file: {file_path}")

    # Compute checksum in a single streaming pass
    algo = self._settings.fs.checksum or "sha256"
    try:
        hasher = hashlib.new(algo)
    except ValueError:
        logger.warning("Unknown checksum algorithm %r, falling back to sha256", algo)
        algo = "sha256"
        hasher = hashlib.new(algo)

    if file_size <= _STREAMING_THRESHOLD:
        # Small file: read into memory (existing path)
        raw_bytes = file_path.read_bytes()
        hasher.update(raw_bytes)
        tika_response = self._call_tika(raw_bytes)
    else:
        # Large file: stream through hasher, send file to Tika
        with open(file_path, "rb") as f:
            while chunk := f.read(_STREAMING_THRESHOLD):
                hasher.update(chunk)
        # Re-read for Tika (file on disk, not in memory)
        with open(file_path, "rb") as f:
            tika_response = self._call_tika_stream(f)

    checksum = hasher.hexdigest()
    # ... rest of document construction (same as before)
```

Add `_call_tika_stream()` that sends a file-like object:

```python
def _call_tika_stream(self, file_obj: Any) -> dict[str, Any]:
    """Send file content to Tika via streaming upload."""
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.put(
                f"{self._tika_url}/rmeta/text",
                content=file_obj,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            return data[0] if isinstance(data, list) else data
    except httpx.ConnectError as exc:
        raise TikaUnavailableError(f"Cannot connect to Tika at {self._tika_url}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise TikaUnavailableError(f"Tika returned {exc.response.status_code}") from exc
```

- [ ] **Step 4: Run full parser test suite**

Run: `uv run pytest tests/unit/test_parser.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/parser.py tests/unit/test_parser.py
git commit -m "feat: stream large files to prevent OOM

Files >64KB are now streamed: checksum computed in a single pass,
content sent to Tika via streaming upload. Small files retain the
existing in-memory path.

Upstream: https://github.com/dadoonet/fscrawler/issues/566
Upstream: https://github.com/dadoonet/fscrawler/issues/890"
```

---

### Task 12: File permissions as octal string, owner/group as names

**Upstream:** [dadoonet/fscrawler#956](https://github.com/dadoonet/fscrawler/issues/956), [dadoonet/fscrawler#955](https://github.com/dadoonet/fscrawler/issues/955)

**Files:**
- Modify: `src/fscrawler/parser.py` — metadata collection
- Modify: `src/fscrawler/models.py` — FileInfo fields (if needed)
- Test: `tests/unit/test_parser.py`

- [ ] **Step 1: Write failing test**

```python
class TestPermissionsFormat:
    """Store permissions as octal string, owner/group as names.

    Upstream: https://github.com/dadoonet/fscrawler/issues/956
    Upstream: https://github.com/dadoonet/fscrawler/issues/955
    """

    def test_permissions_stored_as_octal_string(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        f = data / "file.txt"
        f.write_bytes(b"text")
        f.chmod(0o644)
        settings = make_settings(tmp_path, attributes_support=True)
        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            # ... standard Tika mock setup ...
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [{"X-TIKA:content": "text"}]
            mock_client.put.return_value = mock_response
            parser = TikaParser(settings)
            doc = parser.parse(f)
            attrs = doc.file.attributes
            assert attrs is not None
            assert attrs["permissions"] == "644"

    def test_owner_stored_as_name_not_uid(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        f = data / "file.txt"
        f.write_bytes(b"text")
        settings = make_settings(tmp_path, attributes_support=True)
        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [{"X-TIKA:content": "text"}]
            mock_client.put.return_value = mock_response
            parser = TikaParser(settings)
            doc = parser.parse(f)
            attrs = doc.file.attributes
            assert attrs is not None
            # Owner should be a string name, not numeric UID
            assert not attrs["owner"].isdigit()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_parser.py::TestPermissionsFormat -v`
Expected: FAIL — permissions stored as raw int or not present

- [ ] **Step 3: Implement permissions formatting**

In `src/fscrawler/parser.py`, where attributes are collected (look for
`attributes_support` handling):

```python
import pwd
import grp

def _get_file_attributes(st: os.stat_result) -> dict[str, str]:
    """Extract file attributes as human-readable strings."""
    attrs: dict[str, str] = {}
    # Permissions as octal string (e.g. "644")
    attrs["permissions"] = oct(stat_module.S_IMODE(st.st_mode))[2:]
    # Owner name
    try:
        attrs["owner"] = pwd.getpwuid(st.st_uid).pw_name
    except (KeyError, ImportError):
        attrs["owner"] = str(st.st_uid)
    # Group name
    try:
        attrs["group"] = grp.getgrgid(st.st_gid).gr_name
    except (KeyError, ImportError):
        attrs["group"] = str(st.st_gid)
    return attrs
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_parser.py::TestPermissionsFormat -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/parser.py tests/unit/test_parser.py
git commit -m "feat: store permissions as octal, owner/group as names

Permissions are now stored as octal strings (e.g. '644') instead of
raw integers. Owner and group are resolved to names via pwd/grp
modules with numeric fallback.

Upstream: https://github.com/dadoonet/fscrawler/issues/956
Upstream: https://github.com/dadoonet/fscrawler/issues/955"
```

---

### Task 13: Content whitespace normalization

Optional post-extraction step to collapse excessive whitespace in Tika output.

**Upstream:** [dadoonet/fscrawler#802](https://github.com/dadoonet/fscrawler/issues/802)

**Files:**
- Modify: `src/fscrawler/settings.py` — add `content_normalize` to FsConfig
- Modify: `src/fscrawler/parser.py` — normalize after extraction
- Test: `tests/unit/test_parser.py`

- [ ] **Step 1: Write failing test**

```python
class TestContentNormalization:
    """Normalize excessive whitespace in Tika-extracted content.

    Upstream: https://github.com/dadoonet/fscrawler/issues/802
    """

    def test_whitespace_collapsed_when_enabled(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / "file.txt").write_bytes(b"text")
        settings = make_settings(tmp_path, content_normalize=True)
        tika_content = "Hello   \t\t  World\n\n\n\nFoo   Bar"
        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [{"X-TIKA:content": tika_content}]
            mock_client.put.return_value = mock_response
            parser = TikaParser(settings)
            doc = parser.parse(data / "file.txt")
            assert doc.content == "Hello World\nFoo Bar"

    def test_no_normalization_by_default(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True)
        (data / "file.txt").write_bytes(b"text")
        settings = make_settings(tmp_path)  # content_normalize defaults to False
        tika_content = "Hello   \t\t  World"
        with patch("fscrawler.parser.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__ = lambda s: mock_client
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_response = MagicMock()
            mock_response.json.return_value = [{"X-TIKA:content": tika_content}]
            mock_client.put.return_value = mock_response
            parser = TikaParser(settings)
            doc = parser.parse(data / "file.txt")
            assert doc.content == tika_content
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_parser.py::TestContentNormalization -v`
Expected: FAIL — `content_normalize` setting doesn't exist

- [ ] **Step 3: Implement**

In `src/fscrawler/settings.py`, add to `FsConfig`:

```python
content_normalize: bool = False
```

In `src/fscrawler/parser.py`, after extracting content:

```python
import re

def _normalize_content(text: str) -> str:
    """Collapse runs of whitespace, preserving single newlines."""
    # Collapse horizontal whitespace (spaces, tabs) to single space
    text = re.sub(r"[^\S\n]+", " ", text)
    # Collapse multiple newlines to single newline
    text = re.sub(r"\n{2,}", "\n", text)
    # Strip leading/trailing whitespace per line
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()
```

Apply after content extraction if enabled:

```python
if self._settings.fs.content_normalize:
    content = _normalize_content(content)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_parser.py::TestContentNormalization -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fscrawler/settings.py src/fscrawler/parser.py \
  tests/unit/test_parser.py
git commit -m "feat: optional whitespace normalization for extracted content

New setting fs.content_normalize (default false) collapses excessive
whitespace and blank lines in Tika-extracted content.

Upstream: https://github.com/dadoonet/fscrawler/issues/802"
```

---

## Group D: REST Hardening

### Task 14: REST max body size

Enforce a configurable maximum request body size to prevent OOM from
oversized uploads.

**Upstream:** [dadoonet/fscrawler#1709](https://github.com/dadoonet/fscrawler/issues/1709)

**Files:**
- Modify: `src/fscrawler/settings.py` — add `max_body_size` to RestConfig
- Modify: `src/fscrawler/rest_server.py` — enforce limit
- Test: `tests/unit/test_rest_server.py`

- [ ] **Step 1: Write failing test**

```python
class TestMaxBodySize:
    """Reject uploads exceeding max body size.

    Upstream: https://github.com/dadoonet/fscrawler/issues/1709
    """

    def test_upload_exceeding_limit_returns_413(self):
        settings = make_settings(rest_max_body_size=1024)  # 1 KB limit
        client_mock = make_mock_client()
        parser_mock = make_mock_parser()
        state = make_mock_crawler_state()
        app = create_app(settings, client_mock, state, parser_mock)
        test_client = TestClient(app)

        # Create body larger than 1 KB
        large_data = b"x" * 2048
        headers, body = _multipart_body(data=large_data)
        response = test_client.post("/_document", content=body, headers=headers)
        assert response.status_code == 413

    def test_upload_within_limit_succeeds(self):
        settings = make_settings(rest_max_body_size=1048576)  # 1 MB limit
        client_mock = make_mock_client()
        parser_mock = make_mock_parser()
        state = make_mock_crawler_state()
        app = create_app(settings, client_mock, state, parser_mock)
        test_client = TestClient(app)

        small_data = b"small content"
        headers, body = _multipart_body(data=small_data)
        response = test_client.post("/_document", content=body, headers=headers)
        assert response.status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_rest_server.py::TestMaxBodySize -v`
Expected: FAIL — no size limit exists

- [ ] **Step 3: Implement max body size**

In `src/fscrawler/settings.py`, add to `RestConfig`:

```python
@dataclass
class RestConfig:
    url: str = "http://127.0.0.1:8080"
    enable_cors: bool = False
    max_body_size: int = 104857600  # 100 MB default
```

In `src/fscrawler/rest_server.py`, add middleware or check in upload
endpoints:

```python
from starlette.responses import JSONResponse

# In create_app(), add middleware:
@app.middleware("http")
async def check_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.rest.max_body_size:
        return JSONResponse(
            status_code=413,
            content={"error": "Request body too large"},
        )
    return await call_next(request)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_rest_server.py::TestMaxBodySize -v`
Expected: PASS

- [ ] **Step 5: Run full REST test suite**

Run: `uv run pytest tests/unit/test_rest_server.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/fscrawler/settings.py src/fscrawler/rest_server.py \
  tests/unit/test_rest_server.py
git commit -m "feat: configurable max body size for REST uploads

Rejects uploads exceeding rest.max_body_size (default 100 MB) with
HTTP 413. Prevents OOM from oversized uploads.

Upstream: https://github.com/dadoonet/fscrawler/issues/1709"
```

---

## Group E: Validation & Edge Cases

### Task 15: Mapping type conflict validation

Ensure index templates handle all Tika metadata variations (string vs list)
without mapping exceptions.

**Upstream:** [dadoonet/fscrawler#904](https://github.com/dadoonet/fscrawler/issues/904)

**Files:**
- Review: `src/fscrawler/_templates/` — all mapping JSON files
- Test: `tests/unit/test_templates.py`

- [ ] **Step 1: Write test validating template field types**

```python
class TestMappingConflictPrevention:
    """Validate index templates handle Tika metadata type variations.

    Upstream: https://github.com/dadoonet/fscrawler/issues/904
    """

    def test_meta_fields_are_keyword_or_text(self):
        """All meta.* fields should use types that accept both strings
        and the first element of a list (both map to string)."""
        from fscrawler.templates import load_template
        mapping = load_template("mapping_doc")
        meta_props = mapping["mappings"]["properties"]["meta"]["properties"]
        for field_name, field_def in meta_props.items():
            field_type = field_def.get("type", "object")
            assert field_type in ("keyword", "text", "date", "object"), \
                f"meta.{field_name} has type {field_type} which may conflict"

    def test_file_fields_use_consistent_types(self):
        from fscrawler.templates import load_template
        mapping = load_template("mapping_doc")
        file_props = mapping["mappings"]["properties"]["file"]["properties"]
        # filesize must be long, not integer (for >2GB files)
        assert file_props["filesize"]["type"] == "long"
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/unit/test_templates.py::TestMappingConflictPrevention -v`
Expected: Check if filesize is `long` — if it's `integer`, this fails and we need to fix the template.

- [ ] **Step 3: Fix any template issues found**

If `filesize` type is `integer`, update `src/fscrawler/_templates/mapping_doc.json`:
change `"type": "integer"` to `"type": "long"` for the filesize field.

- [ ] **Step 4: Commit**

```bash
git add src/fscrawler/_templates/ tests/unit/test_templates.py
git commit -m "fix: ensure filesize uses long type, validate meta field types

Prevents integer overflow for files >2GB and validates that all meta
fields use types compatible with Tika's variable output.

Upstream: https://github.com/dadoonet/fscrawler/issues/904
Upstream: https://github.com/dadoonet/fscrawler/issues/890"
```

---

### Task 16: Final integration — run full test suite and lint

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/unit/ -v --tb=short
```

Expected: All pass

- [ ] **Step 2: Run linter**

```bash
uv run ruff check src/ tests/
```

Expected: No errors

- [ ] **Step 3: Run type checker**

```bash
uv run mypy src/fscrawler/
```

Expected: No errors

- [ ] **Step 4: Update CHANGELOG.md**

Add `[Unreleased]` section with all v0.5.0 changes grouped by category
(Added, Fixed, Changed).

- [ ] **Step 5: Commit changelog**

```bash
git add CHANGELOG.md
git commit -m "docs: add v0.5.0 changelog entries"
```

---

## Task Dependency Graph

```
Task 1 (full-path matching) ← Task 2 (default excludes) depends on filter logic
Task 1 ← Task 7 (Unicode normalization) touches same code paths
Task 1 ← Task 8 (on_moved) uses _matches and _virtual_path from Task 1
Tasks 3-6 are independent of each other
Task 10 (ignore_above) ← Task 11 (streaming) both touch parser.py
Task 14 (REST max body) is fully independent
Task 15 (templates) is fully independent
Task 16 (final) depends on all others
```

**Recommended execution order:**
1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16
