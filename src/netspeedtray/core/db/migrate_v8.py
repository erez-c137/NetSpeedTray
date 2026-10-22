"""
The v7 -> v8 database migration - LIVE since the switch-over (step 7, ``_DB_VERSION = 8``): the
database worker calls :func:`recover` on every launch before any connection to the live name, and
:func:`migrate` on a v7 file before it opens its own connection (``DatabaseWorker._prepare_storage``).

Design authority: ``Docs/reference/database-v8-final.md`` section 5; the execution order is step 4
of ``Docs/reference/database-v8-implementation-steps.md``; the checklist is Gate 2 of the 2.2
release plan.

**Build aside, then swap.** The v7 file is never modified in place (beyond the WAL checkpoint SQLite
itself performs). The v8 file is built next to it as ``speed_history.db.v8tmp``, verified against
the v7 source, and only then do two renames make it live::

    speed_history.db        -> speed_history.db.bak.v7_<YYYYmmdd_HHMMSS>   (the v7 backup)
    speed_history.db.v8tmp  -> speed_history.db

The renamed v7 file IS the pre-migration backup - it is not a copy, so it cannot be a WAL-blind one
(step 0's failure mode) and the peak disk cost is v7 + v8, not v7 + v8 + a backup. Its name matches
``DatabaseWorker``'s ``.db.bak.v{N}_`` pattern, so ``_find_newest_backup`` (a rolled-back 2.1.x
looks only at versions <= 7) and the version-aware ``_prune_old_backups`` treat it as the v7 backup.

**What carries over.** Dimension tables first (adapter names -> ``interfaces``, stat names ->
``metrics``). The <= 24 h of v7 per-second network rows is FOLDED into minute rows (owner default 2,
2026-10-19): v8 raw rows are bytes over a measured interval and v7 never measured either, so nothing
is invented - that day loses per-second detail instead. The fold is v7's own raw->minute
aggregation (AVG / MAX / COUNT per minute and adapter); a minute that already exists in v7's minute
tier is merged sample-weighted. Every pre-v8 tier row keeps ``up_bytes`` / ``down_bytes`` /
``covered_ms`` NULL (no rate->bytes conversion: measured x1.53 over-count). Hardware rows of every
tier are copied untouched - the fractional ``latency_*_timeout`` loss series included (Gate 5).
``metadata`` is copied verbatim (``usage_counter_snapshot``, ``pending_history_compaction`` and any
other key) and only then is ``db_version`` set to 8. ``usage_counter`` is copied as-is.
``bandwidth_history`` (created on every v7 file, never written) is not carried over.

**Crash safety.** Everything up to the swap happens in one explicit ``BEGIN .. COMMIT`` on the
``.v8tmp`` (never ``executescript``, whose implicit COMMIT destroys the rollback). A kill at any
point before the journal exists leaves the v7 file untouched; :func:`recover` deletes the
half-built ``.v8tmp``. The two renames are bracketed by ``migrate_v8.journal`` - see
:func:`recover` for the state machine that finishes (or undoes) a kill between them.

Kill-test hooks: :func:`_killpoint` is a no-op unless ``NST_TEST_HOOKS=1`` and ``NST_KILLPOINT``
names the point, in which case the process ends with ``TerminateProcess`` - no ``finally``, no
``atexit``, no SQLite close, exactly what ``taskkill /F`` does. Driven by
``tests/integration/test_migration_kill.py``.

Runnable for the harness and by hand (it never touches anything outside ``<dir>``)::

    python -I -m netspeedtray.core.db.migrate_v8 <dir> [--recover-only] [--simulate-first-persist]
"""

import ctypes
import json
import logging
import math
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from netspeedtray import constants
from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri

logger = logging.getLogger("NetSpeedTray.Core.MigrateV8")

_D = constants.data
_IF = constants.network.interface

V7 = 7
V8 = 8

JOURNAL_NAME = "migrate_v8.journal"
TMP_SUFFIX = ".v8tmp"
BAK_INFIX = ".bak.v7_"          # speed_history.db.bak.v7_<ts>: DatabaseWorker's .db.bak.v{N}_ pattern
JOURNAL_FORMAT = 1

# Free space needed before starting, as a multiple of db + wal (the hot WAL's content is copied
# too). Build-aside-and-swap writes only the new v8 file (plus its small rollback journal), so the
# peak is v7 + v8: measured 1.23x on the owner's 80 MB database (2026-10-19). The design's 1.62x
# (s5) was the IN-PLACE rebuild of revision 1, which this does not do; 1.5x keeps headroom for a
# database whose rows compress less well than that one's.
FREE_SPACE_FACTOR = 1.5

# A rename that hits a sharing violation (an AV scanner or the indexer holding the file) or access
# denied is retried with backoff for this long before the swap is deferred to the next launch.
RENAME_RETRY_SECONDS = 5.0
_ERROR_ACCESS_DENIED = 5
_ERROR_SHARING_VIOLATION = 32
_RETRYABLE_WINERRORS = (_ERROR_ACCESS_DENIED, _ERROR_SHARING_VIOLATION)

# Rows dated more than this past the migration's clock are a wrong clock, not data: they never move
# a tier watermark (one row from 2030 would otherwise hide every new raw row until 2030).
FUTURE_SLACK_SECONDS = 300

# A file found under the live name that recover() must move out of the way (never delete).
STRAY_INFIX = ".stray_"

# The v7 tables the migrator reads. Must equal DatabaseWorker.EXPECTED_TABLES[7] (a unit test pins
# it); kept here so this module stays importable without Qt.
EXPECTED_TABLES_V7: Tuple[str, ...] = (
    "metadata", _D.SPEED_TABLE_RAW, _D.SPEED_TABLE_MINUTE, _D.SPEED_TABLE_HOUR,
    _D.BANDWIDTH_TABLE, _D.HARDWARE_STATS_TABLE_RAW, _D.HARDWARE_STATS_TABLE_MINUTE,
    _D.HARDWARE_STATS_TABLE_HOUR, _D.USAGE_COUNTER_TABLE,
)

# The ten kill points of implementation step 5, plus one between the journal write and the first
# rename (state J1 below), which the step list does not name but the journal makes reachable.
KILLPOINTS: Tuple[str, ...] = (
    "after_free_space_check",
    "after_v8tmp_create",
    "mid_minute_copy",
    "after_hour_hw_tiers",
    "before_commit",
    "after_commit_pre_verify",
    "after_verify_pre_checkpoint",
    "after_journal_pre_rename",
    "after_bak_rename_pre_swap",
    "after_swap_pre_journal_delete",
    "first_v8_persist",
)


class MigrationError(RuntimeError):
    """The migration did not happen. The v7 file is untouched and still live - except for
    SwapIncomplete, which says otherwise and must be read."""


class InsufficientSpace(MigrationError):
    """Not enough free disk to build the v8 file aside."""


class FileInUse(MigrationError):
    """Another connection / program holds the v7 file (its WAL could not be checkpointed away). Nothing
    was swapped; the v7 file is untouched and still live."""


class VerificationFailed(MigrationError):
    """The built v8 file does not hold what the v7 file holds. It was deleted; v7 stays live."""


class SwapError(MigrationError):
    """A rename of the swap failed after retries. ``winerror`` is the Windows error code (0 if none)."""

    def __init__(self, msg: str, winerror: int = 0) -> None:
        super().__init__(msg)
        self.winerror = winerror


class SwapDeferred(SwapError):
    """The FIRST rename (live -> backup) failed. The v7 file is untouched and still live; the
    verified ``.v8tmp`` and the journal are KEPT (state J1), so the next launch's recover() finishes
    the swap without rebuilding - provided the v7 file has not changed in the meantime."""


class SwapIncomplete(SwapError):
    """The SECOND rename (``.v8tmp`` -> live) failed: the v7 file is already the backup and the live
    name is EMPTY (state J2). The caller must not open ``db_path`` (a plain connect would create an
    empty database there); the next launch's recover() completes the swap."""


class RecoveryError(RuntimeError):
    """A migration journal describes a state recover() refuses to guess about. Nothing was changed."""


@dataclass
class MigrationResult:
    status: str                      # 'migrated' | 'already_v8' | 'no_database' | 'not_v7'
    db_path: str = ""
    backup_path: str = ""
    source_version: int = 0
    v7_bytes: int = 0                # db + wal at the start
    v7_wal_bytes: int = 0
    v7_page_count: int = 0
    v8_bytes: int = 0
    v8_page_count: int = 0
    v8_freelist_count: int = 0
    peak_extra_bytes: int = 0        # largest .v8tmp + its rollback journal seen during the build
    free_bytes_before: int = 0
    counts: Dict[str, int] = field(default_factory=dict)
    timings: Dict[str, float] = field(default_factory=dict)
    recovered: str = ""              # what recover() did first, if anything


# ---- paths -------------------------------------------------------------------------------------

def tmp_path_for(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + TMP_SUFFIX)


def journal_path_for(db_path: Path) -> Path:
    return db_path.parent / JOURNAL_NAME


def _sidecars(p: Path) -> Tuple[Path, Path, Path]:
    return (Path(str(p) + "-wal"), Path(str(p) + "-shm"), Path(str(p) + "-journal"))


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


# ---- test hook ---------------------------------------------------------------------------------

def _killpoint(name: str) -> None:
    """End the process HERE, the way ``taskkill /F`` does, when the kill test asks for this point.

    Gated on two variables so a stray ``NST_KILLPOINT`` in a user's environment can never fire it.
    ``TerminateProcess`` and not ``os._exit``: ``os._exit`` still lets the C runtime flush and close
    file handles in-process; TerminateProcess is the kernel tearing the process down mid-instruction.
    """
    if os.environ.get("NST_TEST_HOOKS") != "1" or os.environ.get("NST_KILLPOINT") != name:
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    if sys.platform == "win32":
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        k32.TerminateProcess(k32.GetCurrentProcess(), 137)
    import signal
    os.kill(os.getpid(), getattr(signal, "SIGKILL", signal.SIGTERM))


# ---- file operations ---------------------------------------------------------------------------

_MOVEFILE_WRITE_THROUGH = 0x8


def _try_rename(src: Path, dst: Path) -> int:
    """One attempt. 0 on success, else the Windows error code (errno elsewhere). Never overwrites."""
    if sys.platform == "win32":
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.MoveFileExW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
        k32.MoveFileExW.restype = ctypes.c_int
        if k32.MoveFileExW(str(src), str(dst), _MOVEFILE_WRITE_THROUGH):
            return 0
        return ctypes.get_last_error() or -1
    if dst.exists():
        return 17   # EEXIST
    try:
        os.rename(src, dst)
        return 0
    except OSError as e:
        return e.errno or -1


def _rename(src: Path, dst: Path) -> None:
    """Rename that FAILS if ``dst`` exists and, on Windows, returns only once the move is on disk.

    A sharing violation / access denied (an AV scanner or the search indexer briefly holding the
    file) is retried with backoff for RENAME_RETRY_SECONDS. Then - or at once for any other error -
    SwapError (a MigrationError) is raised: never a raw OSError, which Windows reports for code 32 as
    BrokenPipeError (errno EPIPE) and which a caller handling MigrationError would miss."""
    deadline = time.monotonic() + RENAME_RETRY_SECONDS
    delay = 0.05
    while True:
        err = _try_rename(src, dst)
        if err == 0:
            return
        left = deadline - time.monotonic()
        if err not in _RETRYABLE_WINERRORS or left <= 0:
            detail = ctypes.FormatError(err) if sys.platform == "win32" and err > 0 else os.strerror(err) if err > 0 else "unknown error"
            raise SwapError(f"rename {src.name} -> {dst.name} failed (error {err}: {detail})", winerror=err)
        time.sleep(min(delay, left))
        delay = min(delay * 2, 0.5)


def _unlink_db_files(p: Path) -> None:
    """Delete a database file and every sidecar it may have.

    A sharing violation / access denied is retried with the same backoff and budget as ``_rename``
    (RENAME_RETRY_SECONDS): right after a killed migration the dead process's handle - or an AV scanner
    or the indexer - can hold the half-built ``.v8tmp`` for a moment, and recover() raised WinError 32 at
    once (the flaky test_migration_kill[1], rc.2 X1). Past the budget, or for any other error, the
    OSError propagates as before."""
    for f in (p,) + _sidecars(p):
        deadline = time.monotonic() + RENAME_RETRY_SECONDS
        delay = 0.05
        while True:
            try:
                f.unlink(missing_ok=True)
                break
            except OSError as e:
                left = deadline - time.monotonic()
                if getattr(e, "winerror", None) not in _RETRYABLE_WINERRORS or left <= 0:
                    raise
                time.sleep(min(delay, left))
                delay = min(delay * 2, 0.5)


def _write_journal(path: Path, data: Dict[str, Any]) -> None:
    """Atomic + durable: a reader sees either no journal or a complete one."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_version(path: Path) -> Optional[int]:
    """db_version of a file without changing it (a read-only open; None if unreadable)."""
    conn = None
    try:
        conn = sqlite3.connect(sqlite_ro_uri(path), uri=True)
        row = conn.execute("SELECT value FROM metadata WHERE key = 'db_version'").fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


def _live_is_disposable(path: Path) -> bool:
    """True when the file under the live name holds nothing a migration could lose: empty, not a
    database, no ``metadata`` table, or a db_version other than 7. A valid v7 file is NOT disposable
    (it may be a rolled-back 2.1.x that has been recording into a fresh file) - recover() then
    refuses to guess. Opened read-only; a -wal with content means "not disposable" outright."""
    if _size(_sidecars(path)[0]) > 0:
        return False
    if _size(path) == 0:
        return True
    conn = None
    try:
        conn = sqlite3.connect(sqlite_ro_uri(path), uri=True)
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone() is None:
            return True
        row = conn.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()
        return row is None or str(row[0]) != str(V7)
    except sqlite3.DatabaseError:
        return True          # not a database at all
    finally:
        if conn is not None:
            conn.close()


def _move_aside(path: Path) -> Path:
    """Rename ``path`` and its sidecars to ``<name>.stray_<ts>`` (never delete what we did not make)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = path.with_name(f"{path.name}{STRAY_INFIX}{stamp}")
    n = 0
    while dest.exists():
        n += 1
        dest = path.with_name(f"{path.name}{STRAY_INFIX}{stamp}_{n}")
    _rename(path, dest)
    for side, dside in zip(_sidecars(path), _sidecars(dest)):
        if side.exists():
            _rename(side, dside)
    return dest


def _fingerprint(path: Path) -> Dict[str, int]:
    st = path.stat()
    return {"live_size": st.st_size, "live_mtime_ns": st.st_mtime_ns}


def _unique_backup_path(db_path: Path, now: datetime) -> Path:
    stamp = now.strftime("%Y%m%d_%H%M%S")
    cand = db_path.with_name(f"{db_path.name}{BAK_INFIX}{stamp}")
    n = 0
    while cand.exists() or any(s.exists() for s in _sidecars(cand)):
        n += 1
        cand = db_path.with_name(f"{db_path.name}{BAK_INFIX}{stamp}_{n}")
    return cand


# ---- recovery ----------------------------------------------------------------------------------

def recover(db_path: Path) -> str:
    """
    Bring the directory back to a consistent state after a migration that was killed. Idempotent;
    call it before anything opens ``db_path``. Returns a short code naming what it did.

    **No journal** - the migration never reached the swap, so the live file is the untouched v7:

    * ``N1`` a ``.v8tmp`` (and/or its ``-journal``) is left: a half-built or built-but-unswapped v8
      file. Deleted with its sidecars; the next migrate() rebuilds it. -> ``"discarded_tmp"``
    * ``N0`` nothing to do. -> ``""``

    **Journal present** (written, fsync'd and atomically renamed into place only after the
    ``.v8tmp`` was committed, verified, and the v7 WAL checkpointed away). With L = live file,
    T = ``.v8tmp``, B = the journal's backup name:

    * ``J1`` L, T, not B - killed (or SwapDeferred) before the first rename. Roll FORWARD (both
      renames) when L has no WAL content, L's size + mtime still match the fingerprint the journal
      took after the checkpoint, and T still reads as v8; otherwise L has changed since T was
      verified, so roll BACK (delete T - it is stale). -> ``"completed"`` / ``"rolled_back"``
    * ``J2`` not L, T, B - killed between the renames: rename T -> L. -> ``"completed"``
    * ``J3`` L, not T, B - killed after the swap: delete the journal. -> ``"completed"``
    * ``J4`` not L, not T, B - the built file is gone: put the v7 backup back (B -> L).
      -> ``"restored_v7"``
    * ``J5`` L, T, B - B's name was unique when the journal was written, so B existing PROVES our
      first rename happened; L appeared afterwards (J2, then something plain-connected the live
      name and SQLite created a file). T is never deleted here. If L is disposable (empty, not a
      database, no metadata, not v7) it is moved aside to ``.stray_<ts>`` and T renamed in
      -> ``"completed"``; a valid v7 L raises RecoveryError and changes nothing.
    * ``J6`` not L, T, not B - the live file vanished before the first rename: T is verified and
      complete, rename it in. -> ``"completed"``
    * ``J7`` L, not T, not B - the journal outlived its migration: delete it. -> ``"stale_journal"``
    * ``J8`` none of the three - nothing to recover from; the journal is KEPT and RecoveryError
      raised, so a fresh database is never silently created over a loss.

    Before any rename INTO the live name, the live name's ``-wal`` / ``-shm`` are removed: a v7 WAL
    left beside the v8 file would be replayed into it (Gate 2's "quick_check: ok, tables missing").

    A rename that keeps failing raises SwapError (a MigrationError) and leaves the state as it was
    found, so the next call retries.
    """
    db_path = Path(db_path)
    tmp = tmp_path_for(db_path)
    jpath = journal_path_for(db_path)
    jpath.with_name(jpath.name + ".tmp").unlink(missing_ok=True)   # a journal write that never landed

    if not jpath.exists():
        if tmp.exists() or any(s.exists() for s in _sidecars(tmp)):
            logger.warning("v8 migration: discarding an unfinished %s (no journal - the v7 file "
                           "was never touched).", tmp.name)
            _unlink_db_files(tmp)
            return "discarded_tmp"
        return ""

    try:
        with open(jpath, "r", encoding="utf-8") as fh:
            j = json.load(fh)
        bak = db_path.with_name(j["backup"])
        if j.get("live") != db_path.name or j.get("tmp") != tmp.name:
            raise ValueError(f"journal names {j.get('live')!r}/{j.get('tmp')!r}")
    except Exception as e:
        # The journal is written atomically, so this is not a torn write - it is someone else's file
        # or a disk fault. Guessing could rename the wrong file over the user's data.
        raise RecoveryError(f"unreadable migration journal {jpath}: {e}") from e

    L, T, B = db_path.exists(), tmp.exists(), bak.exists()
    live_wal = _sidecars(db_path)[0]

    def _finish() -> str:
        _clear_live_sidecars(db_path)
        _rename(tmp, db_path)
        jpath.unlink(missing_ok=True)
        return "completed"

    if L and T and not B:                                            # J1
        fp = {k: j.get(k) for k in ("live_size", "live_mtime_ns")}
        if _size(live_wal) == 0 and fp == _fingerprint(db_path) and _read_version(tmp) == V8:
            _clear_live_sidecars(db_path)
            _rename(db_path, bak)
            logger.warning("v8 migration: finishing a swap killed before it started (%s).", bak.name)
            return _finish()
        logger.warning("v8 migration: rolling back an unswapped v8 build (live file changed).")
        _unlink_db_files(tmp)
        jpath.unlink(missing_ok=True)
        return "rolled_back"
    if not L and T and B:                                            # J2
        logger.warning("v8 migration: finishing a swap killed between its renames.")
        return _finish()
    if L and not T and B:                                            # J3
        jpath.unlink(missing_ok=True)
        return "completed"
    if not L and not T and B:                                        # J4
        logger.error("v8 migration: the built v8 file is gone; restoring the v7 backup %s.", bak.name)
        _clear_live_sidecars(db_path)
        _rename(bak, db_path)
        jpath.unlink(missing_ok=True)
        return "restored_v7"
    if L and T and B:                                                # J5
        if not _live_is_disposable(db_path):
            raise RecoveryError(
                f"{db_path.name} is a v7 database that appeared after the swap began ({bak.name} "
                f"is the migrated-from backup, {tmp.name} the verified v8 build); not guessing "
                "which one is the user's history")
        aside = _move_aside(db_path)
        logger.warning("v8 migration: moved a file created under the live name aside (%s) and "
                       "finished the swap.", aside.name)
        return _finish()
    if not L and T and not B:                                        # J6
        return _finish()
    if L and not T and not B:                                        # J7
        jpath.unlink(missing_ok=True)
        return "stale_journal"
    raise RecoveryError(                                             # J8
        f"migration journal {jpath.name} names {db_path.name}, {tmp.name} and {bak.name}, "
        "and none of them exists")


def _clear_live_sidecars(db_path: Path) -> None:
    """Remove ``-wal`` / ``-shm`` / ``-journal`` of the live name before a different file takes it.

    Only ever called when those sidecars belong to a file that has been checkpointed (the migrator
    confirms the v7 WAL is empty) or that no longer exists under this name."""
    for s in _sidecars(db_path):
        s.unlink(missing_ok=True)


# ---- the build ---------------------------------------------------------------------------------

def _guess_is_physical(name: str) -> int:
    """Name-only best guess, the same keywords as interface_classifier.classify_interface(name, None)
    (a unit test pins the agreement; that module is not imported here because it pulls in Qt). The
    v8 writer (step 6) overwrites it from the OS adapter table."""
    lname = (name or "").lower()
    if any(k and k in lname for k in _IF.PSEUDO_NAME_KEYWORDS + _IF.VIRTUAL_NAME_KEYWORDS):
        return 0
    return 1


def _fold_and_copy_minute(c: sqlite3.Connection, lo: int, hi: int) -> None:
    """net_minute rows for minute buckets in [lo, hi): v7 minute rows plus the folded v7 raw tier.

    A bucket with ONE source row keeps that row's numbers exactly (no avg*n/n round trip); only a
    bucket present in both v7 tiers is merged, sample-weighted - the same formula v7's own
    minute->hour rollup uses."""
    c.execute(f"""
        INSERT INTO main.{S.NET_MINUTE}
            (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count, flags)
        SELECT u.ts, i.iface_id,
               CASE WHEN COUNT(*) = 1 THEN MAX(u.up_avg)
                    ELSE COALESCE(SUM(u.up_avg * u.n) / NULLIF(SUM(u.n), 0), AVG(u.up_avg)) END,
               CASE WHEN COUNT(*) = 1 THEN MAX(u.down_avg)
                    ELSE COALESCE(SUM(u.down_avg * u.n) / NULLIF(SUM(u.n), 0), AVG(u.down_avg)) END,
               MAX(u.up_max), MAX(u.down_max), SUM(u.n), 0
        FROM (
            SELECT timestamp AS ts, interface_name AS name, upload_avg AS up_avg,
                   download_avg AS down_avg, upload_max AS up_max, download_max AS down_max,
                   sample_count AS n
            FROM v7.{_D.SPEED_TABLE_MINUTE} WHERE timestamp >= ? AND timestamp < ?
            UNION ALL
            SELECT (timestamp / 60) * 60, interface_name, AVG(upload_bytes_sec),
                   AVG(download_bytes_sec), MAX(upload_bytes_sec), MAX(download_bytes_sec), COUNT(*)
            FROM v7.{_D.SPEED_TABLE_RAW}
            WHERE (timestamp / 60) * 60 >= ? AND (timestamp / 60) * 60 < ?
            GROUP BY (timestamp / 60) * 60, interface_name
        ) u
        JOIN main.{S.INTERFACES} i ON i.stable_key = u.name
        GROUP BY u.ts, i.iface_id
        ORDER BY u.ts, i.iface_id
    """, (lo, hi, lo, hi))


def _wm_rule(hour_max: Optional[int], min_min: Optional[int], min_max: Optional[int],
             raw_min: Optional[int], now_ts: int) -> Tuple[int, int]:
    """(raw watermark, minute watermark) for one tier family, from what its tiers hold.

    The minute watermark is the END of the newest hour bucket (every hour row is older than it, so
    hour and minute never both answer a moment); with no hour rows, the oldest minute row's hour
    (else the oldest raw row's, else now), floored. The raw watermark is the END of the newest minute
    bucket - for the network family that is the last bucket the fold wrote, so the (empty) v8 raw
    tier owns exactly what comes after the migration; with no minute rows, the oldest raw row's
    minute (else now), floored. Clamped so raw >= minute. A coarser tier always wins an overlap:
    finer rows below a watermark (v7 late inserts) are counted as 'shadowed', never double-counted."""
    if hour_max is not None:
        wm_min = int(hour_max) // 3600 * 3600 + 3600
    else:
        first = next((v for v in (min_min, raw_min) if v is not None), now_ts)
        wm_min = int(first) // 3600 * 3600
    if min_max is not None:
        wm_raw = int(min_max) // 60 * 60 + 60
    else:
        wm_raw = int(raw_min if raw_min is not None else now_ts) // 60 * 60
    # Never past the clock (callers already ignore rows beyond the horizon; this is the backstop).
    horizon = int(now_ts) + FUTURE_SLACK_SECONDS
    wm_min = min(wm_min, horizon // 3600 * 3600)
    wm_raw = min(wm_raw, horizon // 60 * 60 + 60)
    return max(wm_raw, wm_min), wm_min


def _hour_horizon(now_ts: int) -> int:
    """The first hour bucket that counts as FUTURE: the one holding now + FUTURE_SLACK_SECONDS, floored.

    _wm_rule caps the minute watermark at that floor (the writer's own invariant: never past the clock's
    hour), so an hour row whose bucket starts at or after it can never lie below the minute watermark.
    Treating it as an ordinary row made the migration's verification fail ("hour rows lie above the
    minute watermark") whenever the clock was behind by about 30 days up to the retention - a clock set
    back into the hour tier (review (a), rc.2). Such rows are carried, counted as future rows, and move
    no watermark, like every other row past the horizon."""
    return int(now_ts + FUTURE_SLACK_SECONDS) // 3600 * 3600


def _watermarks_v8(c: sqlite3.Connection, now_ts: int, schema: str = "main") -> Dict[str, int]:
    """The four watermarks of a v8 file, from its own tiers (what _build stamps). Rows dated past
    now + FUTURE_SLACK_SECONDS do not count (see _wm_rule) - and an HOUR row counts only when its bucket
    began before the horizon's hour floor (``_hour_horizon``)."""
    horizon = now_ts + FUTURE_SLACK_SECONDS

    def one(sql: str) -> Optional[int]:
        return c.execute(sql + f" WHERE ts <= {int(horizon)}").fetchone()[0]

    def hour_max(table: str) -> Optional[int]:
        return c.execute(f"SELECT MAX(ts) FROM {schema}.{table} WHERE ts < ?",
                         (_hour_horizon(now_ts),)).fetchone()[0]
    out: Dict[str, int] = {}
    for (raw, minute, hour), (k_raw, k_min) in (
            ((S.NET_RAW, S.NET_MINUTE, S.NET_HOUR), (S.WATERMARK_RAW_KEY, S.WATERMARK_MINUTE_KEY)),
            ((S.HW_RAW, S.HW_MINUTE, S.HW_HOUR), (S.HW_WATERMARK_RAW_KEY, S.HW_WATERMARK_MINUTE_KEY))):
        out[k_raw], out[k_min] = _wm_rule(
            hour_max(hour), one(f"SELECT MIN(ts) FROM {schema}.{minute}"),
            one(f"SELECT MAX(ts) FROM {schema}.{minute}"), one(f"SELECT MIN(ts) FROM {schema}.{raw}"),
            now_ts)
    return out


def _watermarks_from_v7(c: sqlite3.Connection, now_ts: int) -> Dict[str, int]:
    """The same four, derived independently from the v7 SOURCE (verify's reference): the network
    minute tier there is v7 minute UNION the raw buckets the fold produces; v8 raw is empty."""
    horizon = int(now_ts + FUTURE_SLACK_SECONDS)

    def one(sql: str) -> Optional[int]:
        return c.execute(sql).fetchone()[0]
    sm, sr, sh = (f"v7.{_D.SPEED_TABLE_MINUTE}", f"v7.{_D.SPEED_TABLE_RAW}", f"v7.{_D.SPEED_TABLE_HOUR}")
    hm, hr, hh = (f"v7.{_D.HARDWARE_STATS_TABLE_MINUTE}", f"v7.{_D.HARDWARE_STATS_TABLE_RAW}",
                  f"v7.{_D.HARDWARE_STATS_TABLE_HOUR}")
    h = f" WHERE timestamp <= {horizon}"
    hr_ = f" WHERE timestamp / 60 * 60 <= {horizon}"     # a folded raw row's bucket
    net_min_min = one(f"SELECT MIN(t) FROM (SELECT MIN(timestamp) AS t FROM {sm}{h} "
                      f"UNION ALL SELECT MIN(timestamp) / 60 * 60 FROM {sr}{hr_})")
    net_min_max = one(f"SELECT MAX(t) FROM (SELECT MAX(timestamp) AS t FROM {sm}{h} "
                      f"UNION ALL SELECT MAX(timestamp) / 60 * 60 FROM {sr}{hr_})")
    out: Dict[str, int] = {}
    hh_ = f" WHERE timestamp < {_hour_horizon(now_ts)}"   # hour buckets: see _hour_horizon
    out[S.WATERMARK_RAW_KEY], out[S.WATERMARK_MINUTE_KEY] = _wm_rule(
        one(f"SELECT MAX(timestamp) FROM {sh}{hh_}"), net_min_min, net_min_max, None, now_ts)
    out[S.HW_WATERMARK_RAW_KEY], out[S.HW_WATERMARK_MINUTE_KEY] = _wm_rule(
        one(f"SELECT MAX(timestamp) FROM {hh}{hh_}"), one(f"SELECT MIN(timestamp) FROM {hm}{h}"),
        one(f"SELECT MAX(timestamp) FROM {hm}{h}"), one(f"SELECT MIN(timestamp) FROM {hr}{h}"), now_ts)
    return out


def _future_rows(c: sqlite3.Connection, now_ts: int, schema: str = "main") -> int:
    """Rows dated past the clock + slack (a wrong clock): carried over, but they move no watermark. An
    hour row is one from its bucket's start on (``_hour_horizon``), matching what the watermarks ignore."""
    horizon = int(now_ts + FUTURE_SLACK_SECONDS)
    n = sum(c.execute(f"SELECT COUNT(*) FROM {schema}.{t} WHERE ts > ?", (horizon,)).fetchone()[0]
            for t in (S.NET_RAW, S.NET_MINUTE, S.HW_RAW, S.HW_MINUTE))
    return n + sum(c.execute(f"SELECT COUNT(*) FROM {schema}.{t} WHERE ts >= ?",
                             (_hour_horizon(now_ts),)).fetchone()[0] for t in (S.NET_HOUR, S.HW_HOUR))


def _shadowed(c: sqlite3.Connection, wm: Dict[str, int], schema: str = "main") -> Dict[str, int]:
    """Rows a tier holds below its watermark (answered by the coarser tier instead). 0 on a clean v7."""
    def q(t: str, k: str) -> int:
        return c.execute(f"SELECT COUNT(*) FROM {schema}.{t} WHERE ts < ?", (wm[k],)).fetchone()[0]
    return {"net_minute_shadowed": q(S.NET_MINUTE, S.WATERMARK_MINUTE_KEY),
            "hw_minute_shadowed": q(S.HW_MINUTE, S.HW_WATERMARK_MINUTE_KEY),
            "hw_raw_shadowed": q(S.HW_RAW, S.HW_WATERMARK_RAW_KEY)}


def _build(c: sqlite3.Connection, now_ts: Optional[int] = None) -> Dict[str, int]:
    """Every INSERT of the migration. Runs inside the caller's transaction."""
    now_ts = int(time.time()) if now_ts is None else now_ts
    ifaces_sql = " UNION ALL ".join(
        f"SELECT interface_name AS name, MIN(timestamp) AS lo, MAX(timestamp) AS hi "
        f"FROM v7.{t} GROUP BY interface_name"
        for t in (_D.SPEED_TABLE_RAW, _D.SPEED_TABLE_MINUTE, _D.SPEED_TABLE_HOUR))
    names = c.execute(
        f"SELECT name, MIN(lo), MAX(hi) FROM ({ifaces_sql}) GROUP BY name ORDER BY MIN(lo), name"
    ).fetchall()
    c.executemany(
        f"INSERT INTO main.{S.INTERFACES} (stable_key, name, is_physical, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        [(n, n, _guess_is_physical(n), lo, hi) for n, lo, hi in names])

    metrics_sql = " UNION ".join(
        f"SELECT DISTINCT stat_type FROM v7.{t}"
        for t in (_D.HARDWARE_STATS_TABLE_RAW, _D.HARDWARE_STATS_TABLE_MINUTE,
                  _D.HARDWARE_STATS_TABLE_HOUR))
    c.execute(f"INSERT INTO main.{S.METRICS} (name, entity) "
              f"SELECT stat_type, '' FROM ({metrics_sql}) ORDER BY stat_type")

    # Network minute tier, in two halves so a kill can land between them (kill point 3).
    lo, hi = c.execute(
        f"SELECT MIN(t), MAX(t) FROM (SELECT MIN(timestamp) AS t FROM v7.{_D.SPEED_TABLE_MINUTE} "
        f"UNION ALL SELECT MAX(timestamp) FROM v7.{_D.SPEED_TABLE_MINUTE} "
        f"UNION ALL SELECT (MIN(timestamp) / 60) * 60 FROM v7.{_D.SPEED_TABLE_RAW} "
        f"UNION ALL SELECT (MAX(timestamp) / 60) * 60 FROM v7.{_D.SPEED_TABLE_RAW})").fetchone()
    if lo is not None:
        mid = (lo + (hi - lo) // 2) // 60 * 60
        _fold_and_copy_minute(c, lo, mid)
        _killpoint("mid_minute_copy")
        _fold_and_copy_minute(c, mid, hi + 60)

    c.execute(f"""
        INSERT INTO main.{S.NET_HOUR}
            (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count, flags)
        SELECT h.timestamp, i.iface_id, h.upload_avg, h.download_avg, h.upload_max, h.download_max,
               h.sample_count, 0
        FROM v7.{_D.SPEED_TABLE_HOUR} h JOIN main.{S.INTERFACES} i ON i.stable_key = h.interface_name
        ORDER BY h.timestamp, i.iface_id""")

    c.execute(f"""
        INSERT INTO main.{S.HW_RAW} (ts, metric_id, value)
        SELECT h.timestamp, m.metric_id, h.value
        FROM v7.{_D.HARDWARE_STATS_TABLE_RAW} h
        JOIN main.{S.METRICS} m ON m.name = h.stat_type AND m.entity = ''
        ORDER BY h.timestamp, m.metric_id""")
    for src, dst in ((_D.HARDWARE_STATS_TABLE_MINUTE, S.HW_MINUTE),
                     (_D.HARDWARE_STATS_TABLE_HOUR, S.HW_HOUR)):
        c.execute(f"""
            INSERT INTO main.{dst} (ts, metric_id, avg_value, max_value, sample_count)
            SELECT h.timestamp, m.metric_id, h.avg_value, h.max_value, h.sample_count
            FROM v7.{src} h JOIN main.{S.METRICS} m ON m.name = h.stat_type AND m.entity = ''
            ORDER BY h.timestamp, m.metric_id""")
    _killpoint("after_hour_hw_tiers")

    c.execute(f"INSERT INTO main.{_D.USAGE_COUNTER_TABLE} SELECT * FROM v7.{_D.USAGE_COUNTER_TABLE}")
    c.execute(f"INSERT INTO main.{S.METADATA} (key, value) SELECT key, value FROM v7.metadata")
    c.execute(f"UPDATE main.{S.METADATA} SET value = ? WHERE key = 'db_version'", (str(V8),))
    # Tier watermarks, in the same transaction as the fold that moved the raw rows (schema_v8).
    wm = _watermarks_v8(c, now_ts)
    c.executemany(f"INSERT OR REPLACE INTO main.{S.METADATA} (key, value) VALUES (?, ?)",
                  [(k, str(v)) for k, v in wm.items()])
    c.execute("ANALYZE main")

    counts = _shadowed(c, wm)
    counts["future_rows"] = _future_rows(c, now_ts)
    counts.update(wm)
    counts.update({t: c.execute(f"SELECT COUNT(*) FROM main.{t}").fetchone()[0]
                   for t in (S.INTERFACES, S.METRICS, S.NET_MINUTE, S.NET_HOUR, S.HW_RAW, S.HW_MINUTE,
                      S.HW_HOUR, _D.USAGE_COUNTER_TABLE, S.METADATA)})
    return counts


# ---- verification ------------------------------------------------------------------------------

def _close(a: float, b: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def verify(tmp: Path, v7_path: Path, now_ts: Optional[int] = None) -> Dict[str, int]:
    """Prove the v8 file holds what the v7 file holds. Raises VerificationFailed.

    Opens BOTH files fresh and read-only, so it checks what is on disk, not the build connection's
    cache. Never quick_check alone (an empty file is a valid one): counts per tier after the fold,
    SUM(avg * sample_count) and SUM(sample_count) per network tier, per-metric sums per hardware
    tier, and - stronger than any sum - row-for-row equality between every v7 table and the v8
    compatibility view of the same name, except the minute buckets the fold created or merged.
    """
    c = sqlite3.connect(sqlite_ro_uri(tmp), uri=True, isolation_level=None)
    try:
        c.execute("ATTACH DATABASE ? AS v7", (sqlite_ro_uri(v7_path),))
        problems: List[str] = []

        def one(sql: str, *a: Any) -> Any:
            return c.execute(sql, a).fetchone()[0]

        if one("PRAGMA main.quick_check") != "ok":
            problems.append("quick_check failed")
        if one("PRAGMA main.page_size") != S.PAGE_SIZE or one("PRAGMA main.auto_vacuum") != 2:
            problems.append("page_size/auto_vacuum not as built")
        tables = {r[0] for r in c.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
        missing = sorted(set(S.EXPECTED_TABLES_V8) - tables)
        if missing:
            problems.append(f"missing tables {missing}")

        # -- the network minute tier, after the fold --
        raw_buckets = (f"SELECT DISTINCT (timestamp / 60) * 60 AS ts, interface_name AS name "
                       f"FROM v7.{_D.SPEED_TABLE_RAW}")
        n_raw_buckets = one(f"SELECT COUNT(*) FROM ({raw_buckets})")
        n_overlap = one(f"SELECT COUNT(*) FROM ({raw_buckets}) r JOIN v7.{_D.SPEED_TABLE_MINUTE} m "
                        "ON m.timestamp = r.ts AND m.interface_name = r.name")
        want_minute = one(f"SELECT COUNT(*) FROM v7.{_D.SPEED_TABLE_MINUTE}") + n_raw_buckets - n_overlap
        got_minute = one(f"SELECT COUNT(*) FROM main.{S.NET_MINUTE}")
        if got_minute != want_minute:
            problems.append(f"{S.NET_MINUTE}: {got_minute} rows, expected {want_minute}")
        v7_m = c.execute(f"SELECT TOTAL(upload_avg * sample_count), TOTAL(download_avg * sample_count), "
                         f"TOTAL(sample_count), MAX(upload_max), MAX(download_max) "
                         f"FROM v7.{_D.SPEED_TABLE_MINUTE}").fetchone()
        v7_r = c.execute(f"SELECT TOTAL(upload_bytes_sec), TOTAL(download_bytes_sec), COUNT(*), "
                         f"MAX(upload_bytes_sec), MAX(download_bytes_sec) "
                         f"FROM v7.{_D.SPEED_TABLE_RAW}").fetchone()
        v8_m = c.execute(f"SELECT TOTAL(up_avg * sample_count), TOTAL(down_avg * sample_count), "
                         f"TOTAL(sample_count), MAX(up_max), MAX(down_max) "
                         f"FROM main.{S.NET_MINUTE}").fetchone()
        for k, label in enumerate(("up sum", "down sum", "samples")):
            if not _close(v8_m[k], v7_m[k] + v7_r[k]):
                problems.append(f"{S.NET_MINUTE} {label}: {v8_m[k]!r} vs {v7_m[k] + v7_r[k]!r}")
        for k, label in ((3, "up max"), (4, "down max")):
            want = max([x for x in (v7_m[k], v7_r[k]) if x is not None], default=None)
            if v8_m[k] != want:
                problems.append(f"{S.NET_MINUTE} {label}: {v8_m[k]!r} vs {want!r}")

        cols_speed = "timestamp, interface_name, upload_avg, download_avg, upload_max, download_max, sample_count"
        lost = one(f"SELECT COUNT(*) FROM (SELECT {cols_speed} FROM v7.{_D.SPEED_TABLE_MINUTE} "
                   f"EXCEPT SELECT {cols_speed} FROM main.{_D.SPEED_TABLE_MINUTE})")
        new = one(f"SELECT COUNT(*) FROM (SELECT {cols_speed} FROM main.{_D.SPEED_TABLE_MINUTE} "
                  f"EXCEPT SELECT {cols_speed} FROM v7.{_D.SPEED_TABLE_MINUTE})")
        if lost != n_overlap or new != n_raw_buckets:
            problems.append(f"{S.NET_MINUTE}: {lost} v7 rows altered (expected {n_overlap} merged), "
                            f"{new} rows new or merged (expected {n_raw_buckets})")

        # -- every tier that is copied, not folded: exact, row for row, through the views --
        exact = (
            (_D.SPEED_TABLE_HOUR, cols_speed),
            (_D.HARDWARE_STATS_TABLE_RAW, "timestamp, stat_type, value"),
            (_D.HARDWARE_STATS_TABLE_MINUTE, "timestamp, stat_type, avg_value, max_value, sample_count"),
            (_D.HARDWARE_STATS_TABLE_HOUR, "timestamp, stat_type, avg_value, max_value, sample_count"),
            (_D.USAGE_COUNTER_TABLE, "id, cumulative_up, cumulative_down, anchor_up, anchor_down, "
                                     "period_key, updated_ts"),
        )
        for table, cols in exact:
            n7 = one(f"SELECT COUNT(*) FROM v7.{table}")
            n8 = one(f"SELECT COUNT(*) FROM main.{table}")
            d1 = one(f"SELECT COUNT(*) FROM (SELECT {cols} FROM v7.{table} EXCEPT SELECT {cols} FROM main.{table})")
            d2 = one(f"SELECT COUNT(*) FROM (SELECT {cols} FROM main.{table} EXCEPT SELECT {cols} FROM v7.{table})")
            if n7 != n8 or d1 or d2:
                problems.append(f"{table}: {n8} rows vs {n7}, {d1} missing, {d2} unexpected")

        # -- hardware sums per metric, per tier (Gate 5: the fractional loss series untouched) --
        for table, val in ((_D.HARDWARE_STATS_TABLE_RAW, "value"),
                           (_D.HARDWARE_STATS_TABLE_MINUTE, "avg_value"),
                           (_D.HARDWARE_STATS_TABLE_HOUR, "avg_value")):
            agg = (f"SELECT stat_type, COUNT(*), TOTAL({val}), "
                   + ("TOTAL(max_value), TOTAL(sample_count)" if val == "avg_value" else "0, 0")
                   + " FROM {src}.{t} GROUP BY stat_type")
            a = {r[0]: r[1:] for r in c.execute(agg.format(src="v7", t=table))}
            b = {r[0]: r[1:] for r in c.execute(agg.format(src="main", t=table))}
            if a.keys() != b.keys() or any(
                    a[k][0] != b[k][0] or not all(_close(x, y) for x, y in zip(a[k][1:], b[k][1:]))
                    for k in a):
                problems.append(f"{table}: per-metric sums differ")

        # -- metadata: byte-equal except db_version --
        skip = "', '".join(("db_version",) + S.WATERMARK_KEYS)
        meta = "SELECT key, typeof(value), hex(value) FROM {s}.metadata WHERE key NOT IN ('" + skip + "')"
        m7 = set(c.execute(meta.format(s="v7")))
        m8 = set(c.execute(meta.format(s="main")))
        if m7 != m8:
            problems.append(f"metadata differs: missing {sorted(m7 - m8)}, extra {sorted(m8 - m7)}")
        if c.execute("SELECT value FROM main.metadata WHERE key='db_version'").fetchone() != (str(V8),):
            problems.append("db_version is not 8")

        # -- watermarks: stamped, and equal to what the v7 SOURCE implies (no gap, no overlap) --
        if now_ts is not None:
            want_wm = _watermarks_from_v7(c, now_ts)
            got_wm = dict(c.execute("SELECT key, value FROM main.metadata WHERE key IN ('"
                                    + "', '".join(S.WATERMARK_KEYS) + "')"))
            if got_wm != {k: str(v) for k, v in want_wm.items()}:
                problems.append(f"watermarks {got_wm} != expected {want_wm}")
            horizon = now_ts + FUTURE_SLACK_SECONDS     # wrong-clock rows are exempt (counted)
            if one(f"SELECT COUNT(*) FROM main.{S.NET_RAW}") or one(
                    f"SELECT COUNT(*) FROM main.{S.NET_MINUTE} WHERE ts + 60 > ? AND ts <= ?",
                    want_wm[S.WATERMARK_RAW_KEY], horizon):
                problems.append("folded network rows lie above the raw watermark")
            hour_h = _hour_horizon(now_ts)                  # an hour bucket from here on is future
            if one(f"SELECT COUNT(*) FROM main.{S.NET_HOUR} WHERE ts + 3600 > ? AND ts < ?",
                   want_wm[S.WATERMARK_MINUTE_KEY], hour_h) or one(
                    f"SELECT COUNT(*) FROM main.{S.HW_HOUR} WHERE ts + 3600 > ? AND ts < ?",
                    want_wm[S.HW_WATERMARK_MINUTE_KEY], hour_h):
                problems.append("hour rows lie above the minute watermark")

        if problems:
            raise VerificationFailed("; ".join(problems))
        return {"raw_buckets_folded": n_raw_buckets, "minute_buckets_merged": n_overlap}
    finally:
        c.close()


# ---- the migration -----------------------------------------------------------------------------

def migrate(db_path: Path, now: Optional[datetime] = None) -> MigrationResult:
    """
    Migrate ``db_path`` from v7 to v8, build-aside-and-swap. Nothing may have the file open.

    Returns a MigrationResult (``status`` says whether anything happened). Raises MigrationError
    (or a subclass) when it refuses or fails BEFORE the swap - the v7 file is then still live and
    untouched, and the ``.v8tmp`` is gone - and RecoveryError from :func:`recover`.
    """
    db_path = Path(db_path)
    res = MigrationResult(status="", db_path=str(db_path))
    t_start = time.perf_counter()
    res.recovered = recover(db_path)

    if not db_path.exists():
        res.status = "no_database"
        return res
    version = _open_version(db_path)
    res.source_version = version
    if version == V8:
        res.status = "already_v8"
        return res
    if version != V7:
        res.status = "not_v7"
        return res

    wal = _sidecars(db_path)[0]
    res.v7_wal_bytes = _size(wal)
    res.v7_bytes = _size(db_path) + res.v7_wal_bytes
    res.free_bytes_before = shutil.disk_usage(db_path.parent).free
    need = int(FREE_SPACE_FACTOR * res.v7_bytes)
    if res.free_bytes_before < need:
        raise InsufficientSpace(
            f"v8 migration needs {need} bytes free beside {db_path.name}, "
            f"{res.free_bytes_before} available")
    _killpoint("after_free_space_check")

    tmp = tmp_path_for(db_path)
    _unlink_db_files(tmp)                       # recover() already did; belt and braces
    c = sqlite3.connect(str(tmp), isolation_level=None)
    try:
        try:
            t = time.perf_counter()
            S.create_v8_schema(c)               # page_size + auto_vacuum on the empty file
            res.timings["create"] = time.perf_counter() - t
            _killpoint("after_v8tmp_create")
            c.execute("PRAGMA synchronous = FULL")     # COMMIT is durable before the journal exists
            c.execute("PRAGMA cache_size = -65536")    # 64 MB while building; the file is closed after
            c.execute("PRAGMA temp_store = MEMORY")
            # Read-write ATTACH (not mode=ro): a hot WAL left by taskkill /F needs its -shm rebuilt.
            c.execute("ATTACH DATABASE ? AS v7", (str(db_path),))
            if c.execute("SELECT value FROM v7.metadata WHERE key='db_version'").fetchone() != (str(V7),):
                raise MigrationError("source db_version changed under the migration")
            present = {r[0] for r in c.execute("SELECT name FROM v7.sqlite_master WHERE type='table'")}
            missing = sorted(set(EXPECTED_TABLES_V7) - present)
            if missing:
                raise MigrationError(f"v7 file is missing table(s) {missing}; not migrating it")
            res.v7_page_count = c.execute("PRAGMA v7.page_count").fetchone()[0]

            t = time.perf_counter()
            c.execute("BEGIN")
            now_ts = int((now or datetime.now()).timestamp())
            res.counts = _build(c, now_ts)
            res.peak_extra_bytes = _size(tmp) + _size(_sidecars(tmp)[2])
            _killpoint("before_commit")
            c.execute("COMMIT")
            res.timings["build"] = time.perf_counter() - t
            res.peak_extra_bytes = max(res.peak_extra_bytes, _size(tmp))
            _killpoint("after_commit_pre_verify")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()                 # also checkpoints the v7 WAL when it was the last reader

        t = time.perf_counter()
        res.counts.update(verify(tmp, db_path, now_ts))
        res.timings["verify"] = time.perf_counter() - t
        _killpoint("after_verify_pre_checkpoint")

        _checkpoint_away(db_path)
    except BaseException:
        _unlink_db_files(tmp)
        raise

    probe = sqlite3.connect(sqlite_ro_uri(tmp), uri=True)
    try:
        res.v8_page_count = probe.execute("PRAGMA page_count").fetchone()[0]
        res.v8_freelist_count = probe.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        probe.close()
    res.v8_bytes = _size(tmp)

    # ---- the swap ----
    bak = _unique_backup_path(db_path, now or datetime.now())
    jpath = journal_path_for(db_path)
    _write_journal(jpath, {"format": JOURNAL_FORMAT, "state": "swapping", "live": db_path.name,
                           "tmp": tmp.name, "backup": bak.name, "written_at": int(time.time()),
                           **_fingerprint(db_path)})
    _killpoint("after_journal_pre_rename")
    try:
        _clear_live_sidecars(db_path)     # zero-length after _checkpoint_away; must not follow v8
        _rename(db_path, bak)
    except SwapError as e:
        # Nothing renamed. The verified build and the journal stay (state J1): the next launch's
        # recover() finishes the swap if the v7 file is still exactly what was verified.
        raise SwapDeferred(f"v8 swap deferred to the next launch: {e}", e.winerror) from e
    _killpoint("after_bak_rename_pre_swap")
    try:
        _rename(tmp, db_path)
    except SwapError as e:
        raise SwapIncomplete(
            f"v8 swap half done - {db_path.name} is absent until the next launch's recover(): {e}",
            e.winerror) from e
    _killpoint("after_swap_pre_journal_delete")
    jpath.unlink(missing_ok=True)

    res.backup_path = str(bak)
    res.status = "migrated"
    res.timings["total"] = time.perf_counter() - t_start
    logger.info("Database migrated to v8 (%d -> %d bytes); v7 kept as %s.",
                res.v7_bytes, res.v8_bytes, bak.name)
    return res


def _open_version(db_path: Path) -> int:
    """db_version of the live file through a normal connection, so a hot WAL is replayed. 0 if the
    file has no metadata; raises MigrationError if it cannot be read at all (never guess)."""
    c = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone() is None:
            return 0
        row = c.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()
        return int(row[0]) if row else 0
    except (sqlite3.Error, ValueError) as e:
        raise MigrationError(f"cannot read the schema version of {db_path.name}: {e}") from e
    finally:
        c.close()


def _checkpoint_away(db_path: Path) -> None:
    """Checkpoint the v7 WAL into the main file (TRUNCATE) and confirm it is gone or empty.

    The v7 file is about to be renamed to the backup name, and its ``-wal`` would stay behind under
    the LIVE name - where SQLite would replay it into the v8 file."""
    c = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        busy, _log, _done = c.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise FileInUse("the v7 WAL could not be checkpointed (another connection is open)")
    finally:
        c.close()
    if _size(_sidecars(db_path)[0]):
        raise MigrationError("the v7 WAL still holds frames after the checkpoint")


def simulate_first_persist(db_path: Path) -> None:
    """Harness only (``--simulate-first-persist``): open the migrated file the way the app's worker
    does (WAL) and commit one ``app_sessions`` row with automatic checkpoints off, so a kill at
    ``first_v8_persist`` leaves a HOT v8 WAL beside the live file and the v7 backup."""
    c = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA wal_autocheckpoint = 0")
        now = int(time.time())
        c.execute("BEGIN")
        c.execute(f"INSERT INTO {S.APP_SESSIONS} (started_ts, last_seen_ts, version, poll_ms) "
                  "VALUES (?, ?, 'migrate_v8-harness', 1000)", (now, now))
        c.execute("COMMIT")
        _killpoint("first_v8_persist")
    finally:
        c.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="migrate_v8", description="Migrate <dir>/speed_history.db to v8.")
    ap.add_argument("directory")
    ap.add_argument("--db-name", default=_D.DB_FILENAME)
    ap.add_argument("--recover-only", action="store_true")
    ap.add_argument("--simulate-first-persist", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    db_path = Path(args.directory) / args.db_name
    try:
        if args.recover_only:
            print(json.dumps({"status": "recovered", "recovered": recover(db_path)}))
            return 0
        res = migrate(db_path)
        if args.simulate_first_persist and res.status in ("migrated", "already_v8"):
            simulate_first_persist(db_path)
        print(json.dumps(asdict(res)))
        return 0
    except (MigrationError, RecoveryError) as e:
        print(json.dumps({"status": "error", "error_type": type(e).__name__, "error": str(e)}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
