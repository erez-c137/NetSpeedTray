"""
Settings > Advanced > "Back up history..." (#334): a consistent copy of the live history file to a file the
user picks - for moving to a new PC, or keeping a copy before a reinstall.

Read-only on the live file, always: a dedicated ``mode=ro`` connection (``sqlite_ro_uri`` - never an
unencoded ``file:`` URI, which a ``#`` in the path turns into a read-write open of a truncated path)
runs ``VACUUM INTO`` to a temporary name beside the target. ``VACUUM INTO`` reads through that
connection, so it sees everything committed in the ``-wal`` sidecar that a file copy would lose, and in
WAL mode the app's writer keeps writing meanwhile: the copy is the one snapshot the statement read.
The copy is compacted, in rollback-journal mode, with no ``-wal`` / ``-shm`` of its own - one file to
carry. Proven 2026-10-23 on SQLite 3.50.4: ``VACUUM INTO`` is accepted on a ``mode=ro`` connection,
with uncheckpointed WAL rows and while another connection holds a write transaction. Only if SQLite
refuses the STATEMENT itself (read-only / not authorized - ``_statement_refused``) does it fall back to
``sqlite3.Connection.backup()`` from the same read-only connection (one step = one snapshot). Any other
failure - an interrupt, a full disk, an unwritable target - is final: a fallback would only run a second
full copy into the same failure (or past a cancel).

The copy is then checked before it gets the chosen name (``quick_check``, the source's ``db_version``,
every table the source has and - for v8 - the full v8 table set) and only then ``os.replace``-d over the
target, so a failed or partial copy never sits under the name the user will look for.

**Never the live folder's own names.** The live file's name and everything starting with it in its
folder (``-wal`` / ``-shm`` / ``-journal``, the app's ``.bak.v<N>_*`` backups that its clean-up prunes
on its own schedule) are refused as targets. Both paths are RESOLVED first (``os.path.realpath``: a
``\\\\?\\`` prefix, an 8.3 short name and a junction all name the same folder by another spelling) and
the folders compared by file identity (``os.path.samefile``); a path that cannot be resolved is refused.

**Cancellable.** ``cancel_all`` (the app's quit) interrupts every running copy's SQLite statement and
joins the backup threads for a bounded time; an interrupted copy deletes its temporary file and its
``-journal`` on the way out. Before each backup, leftovers of an earlier run that could not clean up
(``<name>.partial-*`` beside the target) are swept. Two backups to the same file at once are refused.

Errors are ``BackupError`` with a ``code`` (``ERR_*``) the UI maps to a translated sentence; the raw
detail (paths, WinError text) goes to the log only.

Qt-free: the Settings page runs ``backup_history`` on a worker thread and reports through signals.
"""
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set

from netspeedtray.core.db.schema_v8 import EXPECTED_TABLES_V8
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri

logger = logging.getLogger("NetSpeedTray.HistoryBackup")

STEP_COPY = "copy"        #: progress steps reported to ``progress`` (the UI turns them into text)
STEP_VERIFY = "verify"

# BackupError.code values (the UI maps each to a translated sentence).
ERR_REFUSED = "refused"        # the target is the live file / one of its siblings, or unresolvable
ERR_NO_FILE = "no_file"        # no history file yet
ERR_READ = "read"              # the history file could not be read (or is not a NetSpeedTray history)
ERR_VERIFY = "verify"          # the copy failed its check
ERR_IO = "io"                  # writing the copy failed (folder, permissions, disk space)
ERR_BUSY = "busy"              # a backup to the same file is already running
ERR_CANCELLED = "cancelled"    # interrupted (the app is quitting)

_TABLES_SQL = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
_BUSY_TIMEOUT_S = 10.0    # a read-only open waits this long on a WAL recovery by the writer
_PARTIAL = ".partial-"
_SQLITE_READONLY, _SQLITE_AUTH, _SQLITE_INTERRUPT = 8, 23, 9

_lock = threading.Lock()
_conns: Set[sqlite3.Connection] = set()        # every connection a running backup holds
_targets: Set[str] = set()                      # resolved targets with a backup in flight
_threads: List[threading.Thread] = []
_cancelled = False


class BackupError(Exception):
    """The backup did not produce a verified copy. ``code`` is one of ``ERR_*``; ``str()`` is the raw
    detail for the log, never shown to the user as it is."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class BackupResult:
    path: Path            # the verified copy, under the name the user chose
    size_bytes: int
    db_version: int
    method: str           # "vacuum_into" or "backup_api"


def default_backup_name(today) -> str:
    """``NetSpeedTray-history-<YYYY-MM-DD>.db``: never a name the app's own backup matcher
    (``speed_history.db.bak.v<N>_...`` / ``.stray_...``) or the live file could match."""
    return f"NetSpeedTray-history-{today.isoformat()}.db"


def _refused_target(src: Path, dest: Path) -> bool:
    """True for the live file itself, its sidecars and backups (anything named like it) in its folder -
    however either folder is spelled - and for a path that cannot be resolved (refuse rather than risk
    the live folder)."""
    try:
        rs, rd = Path(os.path.realpath(src)), Path(os.path.realpath(dest))
    except (OSError, ValueError):
        return True
    # Windows drops trailing dots / spaces from a file name: "speed_history.db." IS speed_history.db.
    if not rd.name.rstrip(" .").lower().startswith(rs.name.lower()):
        return False
    try:
        return os.path.samefile(rd.parent, rs.parent)
    except OSError:
        return os.path.normcase(str(rd.parent)) == os.path.normcase(str(rs.parent))


def _statement_refused(e: sqlite3.OperationalError) -> bool:
    """SQLite refused VACUUM INTO itself on the read-only connection - the one case the backup API can
    do better. Not an interrupt, a full disk or an unwritable target."""
    code = getattr(e, "sqlite_errorcode", None)
    if code is not None:
        return (int(code) & 0xFF) in (_SQLITE_READONLY, _SQLITE_AUTH)
    msg = str(e).lower()
    return "readonly" in msg or "read-only" in msg or "not authorized" in msg


def _interrupted(e: BaseException) -> bool:
    code = getattr(e, "sqlite_errorcode", None)
    if code is not None:
        return (int(code) & 0xFF) == _SQLITE_INTERRUPT
    return "interrupt" in str(e).lower()


def _connect(target: str, **kw) -> sqlite3.Connection:
    conn = sqlite3.connect(target, **kw)
    with _lock:
        _conns.add(conn)
    return conn


def _close(conn: Optional[sqlite3.Connection]) -> None:
    if conn is None:
        return
    with _lock:
        _conns.discard(conn)
    try:
        conn.close()
    except Exception:
        pass


def _remove_partial(path: Path) -> None:
    for p in (path, path.with_name(path.name + "-journal")):
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not remove an unfinished backup file %s: %s", p, e)


def _sweep_stale(dest: Path) -> None:
    """Leftovers of an earlier backup to this name that could not clean up (a hard kill)."""
    try:
        for p in dest.parent.iterdir():
            if p.name.lower().startswith((dest.name + _PARTIAL).lower()) and p.is_file():
                p.unlink(missing_ok=True)
                logger.info("Removed a stale unfinished backup file: %s", p)
    except OSError as e:
        logger.debug("Stale partial sweep skipped: %s", e)


def _db_version(conn: sqlite3.Connection) -> Optional[int]:
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()
        return int(row[0]) if row is not None else None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def _verify(copy_path: Path, src_version: Optional[int], src_tables: set) -> None:
    check = _connect(sqlite_ro_uri(copy_path), uri=True)
    try:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise BackupError(ERR_VERIFY, "the copy failed its integrity check")
        if _db_version(check) != src_version:
            raise BackupError(ERR_VERIFY, "the copy's schema version does not match the history file")
        tables = {r[0] for r in check.execute(_TABLES_SQL)}
        missing = sorted(src_tables - tables)
        if src_version == 8:
            missing = sorted(set(missing) | (set(EXPECTED_TABLES_V8) - tables))
        if missing:
            raise BackupError(ERR_VERIFY, "the copy is missing table(s): " + ", ".join(missing))
    finally:
        _close(check)


def backup_history(src: Path, dest: Path,
                   progress: Optional[Callable[[str], None]] = None) -> BackupResult:
    """Write a verified copy of the history file ``src`` to ``dest`` (replacing it). Raises
    ``BackupError`` on any failure; ``dest`` is then untouched and no temporary file (or its journal)
    is left behind. Never opens ``src`` for writing."""
    src, dest = Path(src), Path(dest)
    if _cancelled:
        raise BackupError(ERR_CANCELLED, "the app is quitting")
    if not src.is_file():
        raise BackupError(ERR_NO_FILE, f"no history file at {src}")
    if _refused_target(src, dest):
        raise BackupError(ERR_REFUSED, f"refused target {dest} (the live history's name or folder alias)")
    key = os.path.normcase(os.path.realpath(dest))
    with _lock:
        if key in _targets:
            raise BackupError(ERR_BUSY, f"a backup to {dest} is already running")
        _targets.add(key)
    tmp = dest.with_name(f"{dest.name}{_PARTIAL}{os.getpid()}")
    ro: Optional[sqlite3.Connection] = None
    method = "vacuum_into"
    stage = ERR_READ
    try:
        _sweep_stale(dest)
        ro = _connect(sqlite_ro_uri(src), uri=True, timeout=_BUSY_TIMEOUT_S)
        src_version = _db_version(ro)
        if src_version is None:
            raise BackupError(ERR_READ, "the history file has no schema version")
        src_tables = {r[0] for r in ro.execute(_TABLES_SQL)}
        if progress:
            progress(STEP_COPY)
        stage = ERR_IO
        try:
            ro.execute("VACUUM INTO ?", (str(tmp),))
        except sqlite3.OperationalError as e:
            if not _statement_refused(e):
                raise
            # Not expected on any shipped SQLite (see the module docstring).
            logger.warning("VACUUM INTO refused on the read-only connection (%s); using the backup API", e)
            _remove_partial(tmp)
            method = "backup_api"
            dst = _connect(str(tmp))
            try:
                ro.backup(dst)          # pages=-1: the whole file in one step = one snapshot
                dst.execute("PRAGMA journal_mode=DELETE")   # a single file to carry, no -wal beside it
            finally:
                _close(dst)
        _close(ro)
        ro = None
        if _cancelled:
            raise BackupError(ERR_CANCELLED, "the app is quitting")
        if progress:
            progress(STEP_VERIFY)
        stage = ERR_VERIFY
        _verify(tmp, src_version, src_tables)
        stage = ERR_IO
        os.replace(tmp, dest)
        size = dest.stat().st_size
        logger.info("History backed up (%s, v%s, %d bytes) to %s", method, src_version, size, dest)
        return BackupResult(dest, size, int(src_version), method)
    except BackupError as e:
        log = logger.info if e.code == ERR_CANCELLED else logger.error
        log("History backup failed (%s): %s", e.code, e)
        raise
    except (sqlite3.Error, OSError) as e:
        if _cancelled or _interrupted(e):
            logger.info("History backup cancelled: %s", e)
            raise BackupError(ERR_CANCELLED, str(e)) from e
        logger.error("History backup failed (%s): %s", stage, e, exc_info=True)
        raise BackupError(stage, str(e) or type(e).__name__) from e
    finally:
        _close(ro)
        _remove_partial(tmp)           # a failed copy never looks like a backup; nor does its journal
        with _lock:
            _targets.discard(key)


def run_in_thread(target: Callable[[], None]) -> threading.Thread:
    """Start ``target`` (a call to ``backup_history`` + its reporting) on a daemon thread that
    ``cancel_all`` knows about."""
    t = threading.Thread(target=target, name="NST-HistoryBackup", daemon=True)
    with _lock:
        _threads[:] = [x for x in _threads if x.is_alive()]
        _threads.append(t)
    t.start()
    return t


def cancel_all(wait_s: float) -> bool:
    """The app is quitting: refuse new backups, interrupt every running copy and join the backup
    threads for up to ``wait_s`` in total (each deletes its unfinished copy + journal on the way out).
    True when none is still running."""
    global _cancelled
    _cancelled = True
    with _lock:
        conns, threads = list(_conns), [t for t in _threads if t.is_alive()]
    for c in conns:
        try:
            c.interrupt()
        except Exception:
            pass
    deadline = time.monotonic() + max(0.0, wait_s)
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    still = [t for t in threads if t.is_alive()]
    if still:
        logger.warning("%d history backup thread(s) still running at quit; not waiting longer.", len(still))
    return not still
