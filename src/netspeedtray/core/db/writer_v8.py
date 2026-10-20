"""
The v8 history writer - LIVE since the switch-over (step 7, ``_DB_VERSION = 8``). The
``DatabaseWorker`` builds one :class:`WriterV8` on its own write connection, on its own thread, right
after ``migrate_v8.recover()`` / ``migrate()``, and routes its persist / maintenance / delete-history
tasks through it (``DatabaseWorker._persist_v8`` / ``_run_maintenance`` / ``_delete_history``). All SQLite
writes therefore still happen on the worker thread (CLAUDE.md). The class is Qt-free and thread
agnostic, but NOT thread-safe: the worker thread is its only caller.

Design authority: ``Docs/reference/database-v8-final.md`` (bytes at capture s1, coverage s4.2,
err/drop s4.6, DDL s4.5); execution order: step 6 of ``Docs/reference/database-v8-implementation-
steps.md``. The schema and the tier-watermark contract are in ``schema_v8.py``.

**The seam (what step 7 calls)**::

    w = WriterV8(conn, version=__version__, poll_ms=..., is_physical=classifier_fn)
                                               # ^ ON the worker thread: a call from any other raises
    w.run_maintenance()                        # right after migrate_v8.migrate(), then hourly
    w.start_session()                          # once per app start -> a new app_sessions row
    w.add_net_sample(NetSample(...))           # every poll (GUI batches -> worker queue -> here)
    w.add_hw_sample(ts, 'cpu', 12.5)           # every hardware sample that may be recorded
    w.flush()                                  # the 10 s persist tick: rows + heartbeat, ONE commit
    w.run_maintenance(retention_days=...)      # the hourly pass: roll-ups + watermarks + prune + vacuum
    w.delete_history(include_usage=...)        # "Delete all history"; drops every cached id
    w.end_session()                            # clean exit

**What a poll becomes.**

* ``net_raw``: one row per interface with the EXACT byte delta the controller counted for the
  odometer (``byte_deltas`` - pre-spike-filter, negative-diff clamped, sanity capped; #348) over the
  measured interval ``dt_ms`` (the controller's ``sample_elapsed``). Never a rate: the live poll rate
  has no say over stored volume, so a 1/2/5/10 s preset change cannot skew it. All-zero rows are not
  written unless flagged (coverage comes from ``app_sessions``, not from rows). Two intervals that END in the same
  second merge into one row (bytes and ``dt_ms`` add) instead of one being dropped.
* ``FLAG_GAP`` when ``dt_ms`` is past the controller's validity threshold
  (``max(RATE_VALIDITY_MIN_SECONDS, RATE_VALIDITY_POLL_MULTIPLE x poll)``): the bytes are real and
  kept, the reader can decline to draw ``bytes / dt`` as a point. ``FLAG_COUNTER_RESET`` when the OS
  counter went backwards in that interval.
* ``errin`` / ``errout`` / ``dropin`` / ``dropout``: per-interval deltas of psutil's counters as
  ``hw_raw`` rows of the metric ``(name, entity = 'iface:<iface_id>')``, unit ``count``. Clamped like
  the odometer (a counter that went backwards counts 0 for that interval). Only non-zero deltas are
  written - errors are rare and four zero rows per adapter per second would double ``hw_raw``; absent
  while the app ran (``app_sessions``) means 0. Two deltas in the same second add up.
* ``link_events``: one row per ``isup`` CHANGE, compared against the last event stored for that
  interface (so a restart into the same state writes nothing; the first ever observation writes the
  state it starts in). ``(iface_id, ts)`` key, latest state in a second wins.
* Link speed / duplex (step-1 decision: per-interface metrics rows; the key chosen here):
  ``('link_speed', 'iface:<id>')`` in Mbps as psutil reports it (0 = unknown, not written) and
  ``('link_duplex', 'iface:<id>')`` as psutil's NicDuplex (0 unknown, 1 half, 2 full). Written on the
  first observation of a session and then on change, at most once per ``LINK_METRIC_MIN_INTERVAL_S``
  per interface (Wi-Fi renegotiates its rate every few seconds; the latest differing value is
  written once the interval has passed). The entity is the surrogate id, not the adapter name: a
  rename keeps the series, and no friendly name is copied into ``metrics``.
* Hardware samples -> ``hw_raw (ts, metric_id, value)`` exactly as given (WidgetState already clamps
  the percentage stats), including the fractional ``latency_*_timeout`` loss series (Gate 5).
  Non-finite values are skipped (NaN would become NULL and fail the whole transaction).

**The 10 s persist tick** (:meth:`flush`) writes everything buffered and sets
``app_sessions.last_seen_ts`` = now in the SAME transaction. That heartbeat is the coverage source:
a session that ends in a crash / ``taskkill /F`` keeps ``ended_ts`` NULL and ``clean_exit`` 0, and
its last heartbeat bounds it (at most one tick of under-claim, never an over-claim).

**Maintenance** (:meth:`run_maintenance`) MOVES data, one transaction per move, the watermark key
written inside it (``schema_v8`` contract): ``net_raw -> net_minute`` (SUM bytes, time-weighted avg rate,
MAX rate, ``sample_count`` = measured SECONDS capped at the bucket, ``covered_ms`` from the union of
session intervals, OR of flags) at ``now - 24 h``, ``net_minute -> net_hour`` at ``now - 30 d``
(SUM bytes, seconds-weighted avg, MAX, seconds capped at 3600, ``covered_ms`` from sessions), and the
same for ``hw_*`` (avg/min/max/count - there ``sample_count`` stays a sample count). Cutoffs are
bucket-floored; a cutoff never moves below a still-valid watermark (the migration's raw watermark is
the end of the minute it ran in - lowering it would orphan the folded minutes), and an old watermark
is honoured up to the lead the migrator can stamp (``schema_v8.WATERMARK_MAX_LEAD_SECONDS``, 360 s).
Every move MERGES into a coarser bucket that already exists (the migration minute, a clock set back,
a re-run) - never ignored, never duplicated. Exact + exact: bytes add. Either side legacy (bytes
NULL): the row stays legacy, so the reader keeps flagging it ``reconstructed``, and its avg is set
so the reader's legacy estimate (avg x sample_count x 1 s) grows by exactly the new bytes - each part
counted once (see ``_net_merge``). Then retention pruning of the hour
tiers / ``link_events`` / ``app_sessions``, then ``PRAGMA incremental_vacuum(N)`` - never a full
VACUUM (the file is ``auto_vacuum=INCREMENTAL``; ``.fetchall()`` is required or it frees one page).

Kill hooks: ``migrate_v8._killpoint`` with the names in :data:`KILLPOINTS`
(``tests/integration/test_writer_v8_kill.py``).
"""

import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from netspeedtray import constants
from netspeedtray.core.db import schema_v8 as S
from netspeedtray.core.db.migrate_v8 import FUTURE_SLACK_SECONDS, _guess_is_physical, _killpoint

logger = logging.getLogger("NetSpeedTray.Core.WriterV8")

_D = constants.data

# ---- metric keys -------------------------------------------------------------------------------
ERR_DROP_FIELDS: Tuple[str, ...] = ("errin", "errout", "dropin", "dropout")
LINK_SPEED_METRIC = "link_speed"       # Mbps, psutil net_if_stats().speed
LINK_DUPLEX_METRIC = "link_duplex"     # psutil NicDuplex: 0 unknown, 1 half, 2 full
LINK_METRIC_MIN_INTERVAL_S = 60
# Metrics whose samples are COUNTS over an interval: two in the same second add up instead of the
# first one winning (the gauge rule, inherited from v7's INSERT OR IGNORE).
_COUNT_METRICS = frozenset(ERR_DROP_FIELDS)

# SQLite INTEGER is signed 64-bit; a larger Python int raises OverflowError at bind time - not a
# sqlite3.Error. Capture refuses such values (a garbage counter), so they never reach a transaction.
_SQL_INT_MAX = (1 << 63) - 1

# A failed flush keeps its rows for the next tick, up to this many (then the oldest are dropped):
# a locked file must not lose 10 s of history, a permanently failing one must not grow unbounded.
MAX_PENDING_ROWS = 200_000

KILLPOINTS: Tuple[str, ...] = (
    "writer_flush_before_commit",
    "rollup_net_raw_moved", "rollup_net_raw_before_commit",
    "rollup_net_minute_moved", "rollup_net_minute_before_commit",
    "rollup_hw_raw_moved", "rollup_hw_raw_before_commit",
    "rollup_hw_minute_moved", "rollup_hw_minute_before_commit",
)


def iface_entity(iface_id: int) -> str:
    """``metrics.entity`` of a per-interface metric row."""
    return f"iface:{int(iface_id)}"


def gap_threshold_ms(poll_ms: int) -> int:
    """The controller's rate-validity threshold, in ms: an interval longer than this is a gap."""
    spd = constants.network.speed
    return int(max(spd.RATE_VALIDITY_MIN_SECONDS * 1000.0,
                   float(poll_ms) * spd.RATE_VALIDITY_POLL_MULTIPLE))


def _unit_for(name: str) -> str:
    if name in ("cpu", "gpu", "ram", "vram"):
        return "percent"
    if name.endswith("_timeout"):
        return "ratio"
    if name.startswith("latency_"):
        return "ms"
    if name.endswith("_temp"):
        return "celsius"
    if name.endswith("_power"):
        return "watt"
    if name in _COUNT_METRICS:
        return "count"
    if name == LINK_SPEED_METRIC:
        return "mbps"
    if name == LINK_DUPLEX_METRIC:
        return "enum"
    return ""


def _finite(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@dataclass(frozen=True)
class NetSample:
    """One poll, as the controller sees it after ``_odometer_byte_deltas``.

    ``ts``: wall clock of the sample (the END of the interval; the controller's ``sample_wall``).
    ``dt_ms``: the measured interval (``sample_elapsed`` x 1000). ``byte_deltas``: name ->
    (up, down) exact bytes. ``counters``: psutil ``net_io_counters(pernic=True)`` of this poll (for
    err/drop deltas and counter-reset detection) - optional. ``if_stats``: psutil ``net_if_stats()``
    of this poll - optional (step 7 decides how often to read it). ``flags``: extra row flags the
    caller knows about (``FLAG_RESUMED``). A sample with no ``byte_deltas`` only primes the counter
    baselines (the controller's first poll)."""
    ts: float
    dt_ms: int
    byte_deltas: Mapping[str, Tuple[float, float]]
    counters: Optional[Mapping[str, Any]] = None
    if_stats: Optional[Mapping[str, Any]] = None
    flags: int = 0


class WriterV8:
    """Writes v8 history on ONE connection (the DatabaseWorker's). See the module docstring."""

    def __init__(self, conn: sqlite3.Connection, *, version: str, poll_ms: int,
                 is_physical: Optional[Callable[[str], bool]] = None,
                 log: Optional[logging.Logger] = None) -> None:
        self.conn = conn
        # Thread affinity: every call must come from the thread that owns the connection (the
        # DatabaseWorker's run loop). The GUI side never touches this object; it hands batches to the
        # worker queue. Construct it on the worker thread, or call bind_to_current_thread() there.
        self._owner = threading.get_ident()
        self.version = str(version)
        self.poll_ms = max(1, int(poll_ms))
        self._is_physical = is_physical or (lambda n: bool(_guess_is_physical(n)))
        self.logger = log or logger
        self.session_id: Optional[int] = None
        # Dimension caches. Emptied by invalidate_caches(): after a delete-history (the dimension tables
        # are history - adapter names are identifying) and after any rolled-back transaction (an id
        # INSERTed inside it no longer exists).
        self._iface_ids: Dict[str, int] = {}
        self._metric_ids: Dict[Tuple[str, str], int] = {}
        self._link_state: Dict[int, int] = {}           # iface_id -> last isup persisted
        # In-memory per-interface baselines (by name - no DB ids needed): previous counters.
        self._last_counters: Dict[str, Tuple[int, int, Tuple[int, ...]]] = {}
        # Buffers, written by flush(). Interfaces are resolved at flush time, inside the transaction.
        self._net_rows: List[Tuple[int, str, int, int, int, int]] = []      # ts, name, up, down, dt, flags
        self._hw_rows: List[Tuple[int, str, str, float]] = []               # ts, metric, entity-spec, value
        self._link_obs: List[Tuple[int, str, int]] = []                     # ts, name, isup
        # Capture-side link state, by name: the last isup seen, and per (name, metric) the last link
        # speed / duplex value queued and when. Decided here, not at flush, so the rate limit holds
        # however many polls one flush carries.
        self._last_isup: Dict[str, int] = {}
        self._link_metric_last: Dict[Tuple[str, str], Tuple[float, int]] = {}

    # ---- threads / transactions ----------------------------------------------------------------

    def bind_to_current_thread(self) -> None:
        """Make the calling thread the owner (for a writer built before the worker thread started)."""
        self._owner = threading.get_ident()

    def _check_thread(self) -> None:
        if threading.get_ident() != self._owner:
            raise RuntimeError("WriterV8 used off its owning thread - every call must come from the "
                               "DatabaseWorker thread (hand samples over through its queue)")

    def _begin(self) -> None:
        """BEGIN IMMEDIATE. A transaction already open on the connection is NOT this writer's (each of its
        own units commits or rolls back before returning): it is the worker's implicit legacy-mode
        transaction, e.g. a v7-style write the step-7 worker left uncommitted. It is COMMITTED first,
        with a warning, so this writer's statements are never folded into someone else's unit of work
        and a later rollback here can never undo it."""
        if self.conn.in_transaction:
            self.logger.warning("v8 writer: committing a transaction left open on its connection by "
                                "another code path before starting its own.")
            self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")

    def _rollback(self) -> None:
        try:
            if self.conn.in_transaction:
                self.conn.rollback()
        finally:
            self.invalidate_caches()

    def invalidate_caches(self) -> None:
        """Forget every cached dimension id. Called after a delete-history and after a rollback; the
        next write looks each id up again (or re-creates the dimension row)."""
        self._iface_ids.clear()
        self._metric_ids.clear()
        self._link_state.clear()

    # ---- dimensions --------------------------------------------------------------------------

    def _iface_id(self, name: str, ts: int) -> int:
        iid = self._iface_ids.get(name)
        if iid is not None:
            return iid
        row = self.conn.execute(f"SELECT iface_id FROM {S.INTERFACES} WHERE stable_key = ?",
                                (name,)).fetchone()
        if row is not None:
            iid = int(row[0])
            try:
                phys = 1 if self._is_physical(name) else 0
                self.conn.execute(f"UPDATE {S.INTERFACES} SET is_physical = ? WHERE iface_id = ? "
                                  f"AND is_physical <> ?", (phys, iid, phys))
            except Exception as e:   # a classifier failure must not cost the write
                self.logger.debug("Interface classification failed for an adapter: %s", e)
        else:
            try:
                phys = 1 if self._is_physical(name) else 0
            except Exception:
                phys = _guess_is_physical(name)
            cur = self.conn.execute(
                f"INSERT INTO {S.INTERFACES} (stable_key, name, is_physical, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?)", (name, name, phys, ts, ts))
            iid = int(cur.lastrowid)
        self._iface_ids[name] = iid
        return iid

    def _metric_id(self, name: str, entity: str) -> int:
        key = (name, entity)
        mid = self._metric_ids.get(key)
        if mid is not None:
            return mid
        row = self.conn.execute(f"SELECT metric_id FROM {S.METRICS} WHERE name = ? AND entity = ?",
                                key).fetchone()
        if row is not None:
            mid = int(row[0])
        else:
            cur = self.conn.execute(f"INSERT INTO {S.METRICS} (name, entity, unit) VALUES (?, ?, ?)",
                                    (name, entity, _unit_for(name)))
            mid = int(cur.lastrowid)
        self._metric_ids[key] = mid
        return mid

    # ---- sessions ----------------------------------------------------------------------------

    def _insert_session(self, now_ts: int) -> int:
        cur = self.conn.execute(
            f"INSERT INTO {S.APP_SESSIONS} (started_ts, last_seen_ts, version, poll_ms) VALUES (?, ?, ?, ?)",
            (now_ts, now_ts, self.version, self.poll_ms))
        return int(cur.lastrowid)

    def start_session(self, now_ts: Optional[float] = None) -> int:
        """A new ``app_sessions`` row for this app run. A previous run that crashed keeps its row as
        it was: ``ended_ts`` NULL, bounded by its last heartbeat."""
        self._check_thread()
        now = int(time.time() if now_ts is None else now_ts)
        self._begin()
        try:
            self.session_id = self._insert_session(now)
            self.conn.commit()
        except BaseException:
            self._rollback()
            self.session_id = None
            raise
        return self.session_id

    def _heartbeat(self, now: int) -> None:
        if self.session_id is not None:
            cur = self.conn.execute(f"UPDATE {S.APP_SESSIONS} SET last_seen_ts = ? WHERE session_id = ?",
                                    (now, self.session_id))
            if cur.rowcount:
                return
        # No session yet, or its row is gone (history deleted elsewhere): this run is a session again.
        self.session_id = self._insert_session(now)

    def end_session(self, now_ts: Optional[float] = None) -> None:
        """Clean exit: flush what is buffered, then close the session row."""
        self._check_thread()
        now = int(time.time() if now_ts is None else now_ts)
        self.flush(now)
        if self.session_id is None:
            return
        self._begin()
        try:
            self.conn.execute(f"UPDATE {S.APP_SESSIONS} SET ended_ts = ?, last_seen_ts = ?, clean_exit = 1 "
                              "WHERE session_id = ?", (now, now, self.session_id))
            self.conn.commit()
        except BaseException as e:
            self._rollback()
            if not isinstance(e, sqlite3.Error):
                raise
            self.logger.error("Could not close the app session: %s", e)

    def set_poll_ms(self, poll_ms: int) -> None:
        """The update-rate preset changed. Only the gap threshold depends on it; stored bytes never do."""
        self.poll_ms = max(1, int(poll_ms))

    # ---- capture (memory only) ---------------------------------------------------------------

    def add_net_sample(self, s: NetSample) -> None:
        self._check_thread()
        ts = int(s.ts)
        dt_ms = max(1, int(round(s.dt_ms)))
        if not (0 <= ts <= _SQL_INT_MAX and dt_ms <= _SQL_INT_MAX):
            self.logger.warning("v8 writer: a sample with an impossible timestamp/interval was dropped.")
            return
        base_flags = int(s.flags) | (S.FLAG_GAP if dt_ms > gap_threshold_ms(self.poll_ms) else 0)
        reset: Dict[str, bool] = {}
        if s.counters:
            for name, c in s.counters.items():
                try:
                    sent, recv = int(c.bytes_sent), int(c.bytes_recv)
                    errs = tuple(int(getattr(c, f, 0) or 0) for f in ERR_DROP_FIELDS)
                except (AttributeError, TypeError, ValueError):
                    continue
                last = self._last_counters.get(name)
                if last is not None:
                    reset[name] = sent < last[0] or recv < last[1]
                    for f, now_v, prev_v in zip(ERR_DROP_FIELDS, errs, last[2]):
                        d = now_v - prev_v
                        if d > 0:   # negative = the counter restarted: 0 for this interval, like the odometer
                            self._hw_rows.append((ts, f, name, float(d)))
                self._last_counters[name] = (sent, recv, errs)
        for name, (up, down) in s.byte_deltas.items():
            u, d = _finite(up), _finite(down)
            if u is None or d is None:
                continue
            u, d = max(0, int(round(u))), max(0, int(round(d)))
            if u > _SQL_INT_MAX or d > _SQL_INT_MAX:
                self.logger.warning("v8 writer: an impossible byte delta was dropped (counter glitch).")
                continue
            flags = base_flags | (S.FLAG_COUNTER_RESET if reset.get(name) else 0)
            if u == 0 and d == 0 and not flags:
                continue   # an idle poll says nothing a session row does not (a flagged one does)
            self._net_rows.append((ts, name, u, d, dt_ms, flags))
        if s.if_stats:
            for name, st in s.if_stats.items():
                try:
                    isup, speed, duplex = (1 if st.isup else 0), int(st.speed or 0), int(st.duplex or 0)
                except (AttributeError, TypeError, ValueError):
                    continue
                if self._last_isup.get(name) != isup:
                    self._last_isup[name] = isup
                    self._link_obs.append((ts, name, isup))   # vs the DB's last event at flush
                for metric, v in ((LINK_SPEED_METRIC, speed), (LINK_DUPLEX_METRIC, duplex)):
                    if metric == LINK_SPEED_METRIC and v <= 0:
                        continue   # 0 = psutil does not know (or the link is down): not a measurement
                    prev = self._link_metric_last.get((name, metric))
                    if prev is not None and (prev[0] == v or ts - prev[1] < LINK_METRIC_MIN_INTERVAL_S):
                        continue
                    self._link_metric_last[(name, metric)] = (float(v), ts)
                    self._hw_rows.append((ts, metric, name, float(v)))   # entity = this interface
        self._trim()

    def add_hw_sample(self, ts: float, name: str, value: float, entity: str = "") -> None:
        """One hardware / latency sample, stored as given (``entity`` '' = the aggregate series)."""
        self._check_thread()
        v = _finite(value)
        if v is None or not name or not (0 <= ts <= _SQL_INT_MAX):
            return
        self._hw_rows.append((int(ts), str(name), "=" + str(entity), v))
        self._trim()

    def pending_rows(self) -> int:
        return len(self._net_rows) + len(self._hw_rows) + len(self._link_obs)

    def _trim(self) -> None:
        over = self.pending_rows() - MAX_PENDING_ROWS
        if over > 0:
            for buf in (self._hw_rows, self._net_rows, self._link_obs):
                k = min(over, len(buf))
                del buf[:k]
                over -= k
            self.logger.warning("v8 writer: pending rows over the cap; the oldest were dropped.")

    # ---- the persist tick --------------------------------------------------------------------

    def flush(self, now_ts: Optional[float] = None) -> bool:
        """Write everything buffered + the session heartbeat, in ONE transaction. True on success. On a
        storage error the rows stay buffered for the next tick (bounded by MAX_PENDING_ROWS)."""
        self._check_thread()
        now = int(time.time() if now_ts is None else now_ts)
        net, hw, links = self._net_rows, self._hw_rows, self._link_obs
        self._net_rows, self._hw_rows, self._link_obs = [], [], []
        try:
            self._begin()
            self._write_net(net)
            self._write_links(links)
            self._write_hw(hw)
            self._heartbeat(now)
            _killpoint("writer_flush_before_commit")
            self.conn.commit()
            return True
        except BaseException as e:
            # ANY failure - not only sqlite3.Error - rolls back here: a transaction left open would be
            # committed, half-written, by the next _begin().
            self._rollback()
            self._net_rows[:0], self._hw_rows[:0], self._link_obs[:0] = net, hw, links
            self._trim()
            if not isinstance(e, sqlite3.Error):
                raise
            self.logger.error("v8 persist failed (rows kept for the next tick): %s", e)
            return False

    def _write_net(self, rows: Sequence[Tuple[int, str, int, int, int, int]]) -> None:
        if not rows:
            return
        seen: Dict[int, int] = {}
        out = []
        for ts, name, up, down, dt, flags in rows:
            iid = self._iface_id(name, ts)
            seen[iid] = max(seen.get(iid, ts), ts)
            out.append((ts, iid, up, down, dt, flags))
        self.conn.executemany(
            f"INSERT INTO {S.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms, flags) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (ts, iface_id) DO UPDATE SET "
            "up_bytes = up_bytes + excluded.up_bytes, down_bytes = down_bytes + excluded.down_bytes, "
            "dt_ms = dt_ms + excluded.dt_ms, flags = flags | excluded.flags", out)
        self.conn.executemany(f"UPDATE {S.INTERFACES} SET last_seen = MAX(last_seen, ?) WHERE iface_id = ?",
                              [(ts, iid) for iid, ts in seen.items()])

    def _write_hw(self, rows: Sequence[Tuple[int, str, str, float]]) -> None:
        if not rows:
            return
        gauges, counts = [], []
        for ts, name, ent, value in rows:
            if ent.startswith("="):
                entity = ent[1:]
            else:   # an interface NAME, resolved to its surrogate id here, inside the transaction
                entity = iface_entity(self._iface_id(ent, ts))
            row = (ts, self._metric_id(name, entity), value)
            (counts if name in _COUNT_METRICS else gauges).append(row)
        if gauges:
            self.conn.executemany(f"INSERT OR IGNORE INTO {S.HW_RAW} (ts, metric_id, value) VALUES (?, ?, ?)",
                                  gauges)
        if counts:
            self.conn.executemany(
                f"INSERT INTO {S.HW_RAW} (ts, metric_id, value) VALUES (?, ?, ?) "
                "ON CONFLICT (ts, metric_id) DO UPDATE SET value = value + excluded.value", counts)

    def _write_links(self, obs: Sequence[Tuple[int, str, int]]) -> None:
        for ts, name, isup in obs:
            iid = self._iface_id(name, ts)
            last = self._link_state.get(iid)
            if last is None:
                row = self.conn.execute(f"SELECT event FROM {S.LINK_EVENTS} WHERE iface_id = ? "
                                        "ORDER BY ts DESC LIMIT 1", (iid,)).fetchone()
                last = None if row is None else int(row[0])
            if last != isup:
                self.conn.execute(f"INSERT OR REPLACE INTO {S.LINK_EVENTS} (iface_id, ts, event) "
                                  "VALUES (?, ?, ?)", (iid, ts, isup))
            self._link_state[iid] = isup

    # ---- delete all history ------------------------------------------------------------------

    def delete_history(self, include_usage: bool = False, now_ts: Optional[float] = None) -> None:
        """Empty every v8 history table (``schema_v8.HISTORY_TABLES_V8``, dimensions included - adapter
        names are identifying) in one transaction, with ``secure_delete`` on so the freed pages are
        zeroed, then hand the free pages back (``incremental_vacuum``). Everything buffered before the
        request is discarded, every cached id is dropped, and the running app becomes a new session
        (inside the same transaction, so coverage resumes from the delete). Checkpointing the WAL stays
        the worker's job, as in v7. Raises sqlite3.Error after rolling back."""
        self._check_thread()
        now = int(time.time() if now_ts is None else now_ts)
        self._net_rows, self._hw_rows, self._link_obs = [], [], []
        self._last_isup.clear()
        self._link_metric_last.clear()
        prev_secure = self.conn.execute("PRAGMA secure_delete").fetchone()[0]
        self.conn.execute("PRAGMA secure_delete = ON")
        try:
            self._begin()
            tables = list(S.HISTORY_TABLES_V8) + ([_D.USAGE_COUNTER_TABLE] if include_usage else [])
            for t in tables:
                self.conn.execute(f"DELETE FROM {t}")
            self.invalidate_caches()
            self.session_id = self._insert_session(now)
            self.conn.commit()
        except BaseException:
            self._rollback()
            raise
        finally:
            self.invalidate_caches()
            self.conn.execute(f"PRAGMA secure_delete = {int(prev_secure)}")
        self.incremental_vacuum(None)

    # ---- maintenance -------------------------------------------------------------------------

    def incremental_vacuum(self, pages: Optional[int] = None) -> int:
        """Return up to ``pages`` free pages to the OS (all of them when None). Pages actually freed.
        ``.fetchall()`` is load-bearing: the pragma frees one page per step (measured: 1 vs 4,078)."""
        self._check_thread()
        if pages is not None and int(pages) <= 0:
            return 0   # NOT passed through: incremental_vacuum(0) means "the whole freelist"
        if self.conn.in_transaction:
            self.conn.commit()
        before = int(self.conn.execute("PRAGMA freelist_count").fetchone()[0])
        if before <= 0:
            return 0
        arg = "" if pages is None else f"({int(pages)})"
        self.conn.execute(f"PRAGMA incremental_vacuum{arg}").fetchall()
        if self.conn.in_transaction:
            self.conn.commit()
        return before - int(self.conn.execute("PRAGMA freelist_count").fetchone()[0])

    def _watermark(self, key: str, horizon: int) -> Optional[int]:
        row = self.conn.execute(f"SELECT value FROM {S.METADATA} WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        v = str(row[0])
        if not v.isdigit():
            return None
        v_int = int(v)
        return v_int if 0 < v_int <= horizon else None

    def _cutoffs(self, now: int, raw_keep: int, minute_keep: int, k_raw: str, k_min: str) -> Tuple[int, int]:
        """(raw cutoff, minute cutoff) for one family: the bucket-floored retention cutoffs, never below a
        still-valid watermark, clamped like the migrator, and raw >= minute."""
        horizon = now + FUTURE_SLACK_SECONDS
        c_raw = (now - raw_keep) // 60 * 60
        c_min = (now - minute_keep) // 3600 * 3600
        # A stored watermark is honoured up to the lead the migrator can stamp (end of the minute it ran
        # in, clamped at now + FUTURE_SLACK_SECONDS: at most now + 360 s) - the reader's
        # WATERMARK_MAX_LEAD_SECONDS. Rejecting it would lower the cutoff below the folded minutes.
        lead = now + S.WATERMARK_MAX_LEAD_SECONDS
        w_raw, w_min = self._watermark(k_raw, lead), self._watermark(k_min, lead)
        if w_raw is not None:
            c_raw = max(c_raw, w_raw // 60 * 60)
        if w_min is not None:
            c_min = max(c_min, w_min // 3600 * 3600)
        c_min = min(c_min, max(horizon // 3600 * 3600, w_min or 0))
        c_raw = max(min(max(c_raw, c_min), max(horizon // 60 * 60 + 60, w_raw or 0)), c_min)
        return c_raw, c_min

    def _session_coverage(self, buckets: Iterable[int], size: int) -> Dict[int, int]:
        """covered_ms per bucket start: overlap of [b, b + size) with the UNION of session intervals
        (a session runs to ``ended_ts``, or its last heartbeat). Buckets with no overlap are absent."""
        bl = sorted(set(int(b) for b in buckets))
        if not bl:
            return {}
        lo, hi = bl[0], bl[-1] + size
        rows = self.conn.execute(
            f"SELECT started_ts, MAX(started_ts, COALESCE(ended_ts, last_seen_ts)) AS e FROM {S.APP_SESSIONS} "
            "WHERE started_ts < ? AND MAX(started_ts, COALESCE(ended_ts, last_seen_ts)) > ? "
            "ORDER BY started_ts", (hi, lo)).fetchall()
        merged: List[List[int]] = []
        for s, e in rows:
            s, e = int(s), int(e)
            if e <= s:
                continue
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        out: Dict[int, int] = {}
        j = 0
        for b in bl:
            be = b + size
            while j < len(merged) and merged[j][1] <= b:
                j += 1
            k, tot = j, 0
            while k < len(merged) and merged[k][0] < be:
                tot += max(0, min(be, merged[k][1]) - max(b, merged[k][0]))
                k += 1
            if tot > 0:
                out[b] = min(size, tot) * 1000
        return out

    def _fill_cov(self, cov: Dict[int, int]) -> None:
        self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS v8w_cov (ts INTEGER PRIMARY KEY, covered_ms INTEGER)")
        self.conn.execute("DELETE FROM temp.v8w_cov")
        self.conn.executemany("INSERT INTO temp.v8w_cov (ts, covered_ms) VALUES (?, ?)", cov.items())

    _FLAGS_OR = ("(MAX(r.flags & 1) | MAX(r.flags & 2) | MAX(r.flags & 4) | MAX(r.flags & 8) "
                 "| MAX(r.flags & 16) | MAX(r.flags & 32) | MAX(r.flags & 64) | MAX(r.flags & 128))")
    # Units (schema_v8 contract): a net tier's ``sample_count`` is the MEASURED SECONDS the row stands
    # for - SUM(dt_ms) / 1000 of its raw rows, rounded, capped at the bucket length - and its avg the
    # time-weighted rate over them. That is the unit the legacy estimator already assumes for v7 rows
    # (avg x MIN(sample_count, bucket) x LEGACY_SAMPLE_SECONDS = 1 s), so exact and legacy rows weigh
    # the same way and an avg stays a rate at 2/5/10 s polls.
    #
    # Every roll-up may land on a bucket that ALREADY EXISTS in the coarser tier: the minute the
    # migration ran (the folded v7 minute ends at the raw watermark, and the first v8 polls land below
    # it), a clock set back, a re-run. Rows are MERGED, never ignored, never duplicated:
    # * both exact (bytes present): bytes add, avg = seconds-weighted mean;
    # * either side legacy (bytes NULL - migrated, an estimate): the merged row stays legacy (bytes NULL,
    #   so the reader keeps flagging it `reconstructed`). Its sample_count is MIN(n + n', bucket) and its
    #   avg = (each side's reader estimate - bytes when exact, avg x MIN(n, bucket) when legacy) / that,
    #   so the reader's estimate of the merged row is EXACTLY the sum of the two parts' - each part once,
    #   even when a clock set back makes n + n' exceed the bucket (the avg then reads high; volume holds).
    # max = MAX, covered_ms = MAX when both known else NULL, flags OR.
    @staticmethod
    def _net_merge(bucket: int) -> str:
        n = f"MIN(sample_count + excluded.sample_count, {int(bucket)})"
        avg = ", ".join(
            f"{d}_avg = CASE WHEN {d}_bytes IS NOT NULL AND excluded.{d}_bytes IS NOT NULL "
            f"THEN ({d}_avg * sample_count + excluded.{d}_avg * excluded.sample_count) "
            f"/ (sample_count + excluded.sample_count) "
            f"ELSE (COALESCE({d}_bytes, {d}_avg * MIN(sample_count, {int(bucket)})) "
            f"+ COALESCE(excluded.{d}_bytes, excluded.{d}_avg * MIN(excluded.sample_count, {int(bucket)}))) "
            f"/ {n} END" for d in ("up", "down"))
        return ("ON CONFLICT (ts, iface_id) DO UPDATE SET "
                "up_bytes = CASE WHEN up_bytes IS NULL OR excluded.up_bytes IS NULL THEN NULL "
                "ELSE up_bytes + excluded.up_bytes END, "
                "down_bytes = CASE WHEN down_bytes IS NULL OR excluded.down_bytes IS NULL THEN NULL "
                "ELSE down_bytes + excluded.down_bytes END, "
                f"{avg}, "
                "up_max = MAX(up_max, excluded.up_max), down_max = MAX(down_max, excluded.down_max), "
                f"sample_count = {n}, "
                "covered_ms = CASE WHEN covered_ms IS NULL OR excluded.covered_ms IS NULL THEN NULL "
                "ELSE MAX(covered_ms, excluded.covered_ms) END, "
                "flags = flags | excluded.flags")

    _HW_MERGE = (
        "ON CONFLICT (ts, metric_id) DO UPDATE SET "
        "avg_value = (avg_value * sample_count + excluded.avg_value * excluded.sample_count) "
        "/ (sample_count + excluded.sample_count), "
        "min_value = CASE WHEN min_value IS NULL OR excluded.min_value IS NULL THEN NULL "
        "ELSE MIN(min_value, excluded.min_value) END, "
        "max_value = MAX(max_value, excluded.max_value), "
        "sample_count = sample_count + excluded.sample_count")

    def _move(self, name: str, wm_key: str, cutoff: int, body: Callable[[], int]) -> int:
        """One roll-up: rows + DELETE + watermark, ONE transaction (the schema_v8 contract)."""
        self._begin()
        try:
            n = body()
            _killpoint(f"rollup_{name}_moved")
            self.conn.execute(f"INSERT OR REPLACE INTO {S.METADATA} (key, value) VALUES (?, ?)",
                              (wm_key, str(int(cutoff))))
            _killpoint(f"rollup_{name}_before_commit")
            self.conn.commit()
            return n
        except BaseException:
            self._rollback()
            raise

    def _roll_net_raw(self, cutoff: int) -> int:
        buckets = [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT ts / 60 * 60 FROM {S.NET_RAW} WHERE ts < ?", (cutoff,))]
        if not buckets:
            return 0
        self._fill_cov(self._session_coverage(buckets, 60))
        rate = "r.{d}_bytes * 1000.0 / r.dt_ms"
        # avg: time-weighted (bytes over the measured interval); sample_count: measured seconds (>= 1,
        # <= the bucket - a gap row's long interval is not 'more than a minute of this minute').
        self.conn.execute(f"""
            INSERT INTO {S.NET_MINUTE} (ts, iface_id, up_bytes, down_bytes, up_avg, down_avg, up_max,
                                        down_max, sample_count, covered_ms, flags)
            SELECT r.ts / 60 * 60 AS b, r.iface_id, SUM(r.up_bytes), SUM(r.down_bytes),
                   SUM(r.up_bytes) * 1000.0 / SUM(r.dt_ms), SUM(r.down_bytes) * 1000.0 / SUM(r.dt_ms),
                   MAX({rate.format(d='up')}), MAX({rate.format(d='down')}),
                   MIN(MAX(1, CAST(ROUND(SUM(r.dt_ms) / 1000.0) AS INTEGER)), 60),
                   MAX(c.covered_ms), {self._FLAGS_OR}
            FROM {S.NET_RAW} r LEFT JOIN temp.v8w_cov c ON c.ts = r.ts / 60 * 60
            WHERE r.ts < ?
            GROUP BY b, r.iface_id
            {self._net_merge(60)}""", (cutoff,))
        return self.conn.execute(f"DELETE FROM {S.NET_RAW} WHERE ts < ?", (cutoff,)).rowcount

    def _roll_net_minute(self, cutoff: int) -> int:
        buckets = [r[0] for r in self.conn.execute(
            f"SELECT DISTINCT ts / 3600 * 3600 FROM {S.NET_MINUTE} WHERE ts < ?", (cutoff,))]
        if not buckets:
            return 0
        self._fill_cov(self._session_coverage(buckets, 3600))
        exact = "CASE WHEN COUNT(r.{c}) = COUNT(*) THEN SUM(r.{c}) END"
        n = "MIN(SUM(r.sample_count), 3600)"
        # All-exact groups: the seconds-weighted mean. A group with a legacy (NULL-bytes) row becomes a
        # legacy row whose avg x sample_count is the exact bytes of its exact rows plus the reader's
        # estimate of each legacy minute (avg x MIN(n, 60)) - the same rule as _net_merge.
        wavg = ("CASE WHEN COUNT(r.{d}_bytes) = COUNT(*) THEN "
                "COALESCE(SUM(r.{d}_avg * r.sample_count) / NULLIF(SUM(r.sample_count), 0), AVG(r.{d}_avg)) "
                "ELSE COALESCE(SUM(COALESCE(r.{d}_bytes, r.{d}_avg * MIN(r.sample_count, 60))) "
                f"/ NULLIF({n}, 0), AVG(r.{{d}}_avg)) END")
        self.conn.execute(f"""
            INSERT INTO {S.NET_HOUR} (ts, iface_id, up_bytes, down_bytes, up_avg, down_avg, up_max,
                                      down_max, sample_count, covered_ms, flags)
            SELECT r.ts / 3600 * 3600 AS b, r.iface_id, {exact.format(c='up_bytes')},
                   {exact.format(c='down_bytes')}, {wavg.format(d='up')}, {wavg.format(d='down')},
                   MAX(r.up_max), MAX(r.down_max), {n},
                   CASE WHEN COUNT(r.covered_ms) = COUNT(*) THEN MAX(c.covered_ms) END, {self._FLAGS_OR}
            FROM {S.NET_MINUTE} r LEFT JOIN temp.v8w_cov c ON c.ts = r.ts / 3600 * 3600
            WHERE r.ts < ?
            GROUP BY b, r.iface_id
            {self._net_merge(3600)}""", (cutoff,))
        return self.conn.execute(f"DELETE FROM {S.NET_MINUTE} WHERE ts < ?", (cutoff,)).rowcount

    def _roll_hw_raw(self, cutoff: int) -> int:
        self.conn.execute(f"""
            INSERT INTO {S.HW_MINUTE} (ts, metric_id, avg_value, min_value, max_value, sample_count)
            SELECT ts / 60 * 60 AS b, metric_id, AVG(value), MIN(value), MAX(value), COUNT(*)
            FROM {S.HW_RAW} WHERE ts < ?
            GROUP BY b, metric_id
            {self._HW_MERGE}""", (cutoff,))
        return self.conn.execute(f"DELETE FROM {S.HW_RAW} WHERE ts < ?", (cutoff,)).rowcount

    def _roll_hw_minute(self, cutoff: int) -> int:
        self.conn.execute(f"""
            INSERT INTO {S.HW_HOUR} (ts, metric_id, avg_value, min_value, max_value, sample_count)
            SELECT ts / 3600 * 3600 AS b, metric_id,
                   COALESCE(SUM(avg_value * sample_count) / NULLIF(SUM(sample_count), 0), AVG(avg_value)),
                   CASE WHEN COUNT(min_value) = COUNT(*) THEN MIN(min_value) END,
                   MAX(max_value), SUM(sample_count)
            FROM {S.HW_MINUTE} WHERE ts < ?
            GROUP BY b, metric_id
            {self._HW_MERGE}""", (cutoff,))
        return self.conn.execute(f"DELETE FROM {S.HW_MINUTE} WHERE ts < ?", (cutoff,)).rowcount

    def run_maintenance(self, now_ts: Optional[float] = None, retention_days: Optional[float] = None,
                        vacuum_pages: Optional[int] = None) -> Dict[str, int]:
        """The hourly pass - and step 7 also calls it ONCE right after ``migrate_v8.migrate()`` succeeds,
        before the first flush, so the first v8 polls that land in the folded minute (below the raw
        watermark, invisible to readers until rolled) are merged within one tick rather than an hour.
        ``retention_days``: the hour tiers' retention the worker has already resolved through its
        grace-period logic (``_prune_data_with_grace_period``); None prunes nothing. Returns counts
        (rows moved / pruned, pages vacuumed, the watermarks written)."""
        self._check_thread()
        now = int(time.time() if now_ts is None else now_ts)
        out: Dict[str, int] = {}
        raw_keep, min_keep = _D.RAW_TIER_RETENTION_SECONDS, _D.MINUTE_TIER_RETENTION_SECONDS
        net_raw_c, net_min_c = self._cutoffs(now, raw_keep, min_keep, S.WATERMARK_RAW_KEY, S.WATERMARK_MINUTE_KEY)
        hw_raw_c, hw_min_c = self._cutoffs(now, raw_keep, min_keep, S.HW_WATERMARK_RAW_KEY,
                                           S.HW_WATERMARK_MINUTE_KEY)
        # Finer first: raw rows below the minute cutoff reach the hour tier in this same pass.
        out["net_raw_moved"] = self._move("net_raw", S.WATERMARK_RAW_KEY, net_raw_c,
                                          lambda: self._roll_net_raw(net_raw_c))
        out["net_minute_moved"] = self._move("net_minute", S.WATERMARK_MINUTE_KEY, net_min_c,
                                             lambda: self._roll_net_minute(net_min_c))
        out["hw_raw_moved"] = self._move("hw_raw", S.HW_WATERMARK_RAW_KEY, hw_raw_c,
                                         lambda: self._roll_hw_raw(hw_raw_c))
        out["hw_minute_moved"] = self._move("hw_minute", S.HW_WATERMARK_MINUTE_KEY, hw_min_c,
                                            lambda: self._roll_hw_minute(hw_min_c))
        out.update({S.WATERMARK_RAW_KEY: net_raw_c, S.WATERMARK_MINUTE_KEY: net_min_c,
                    S.HW_WATERMARK_RAW_KEY: hw_raw_c, S.HW_WATERMARK_MINUTE_KEY: hw_min_c})
        if retention_days is not None:
            out["pruned"] = self._prune(now, retention_days)
        out["vacuumed_pages"] = self.incremental_vacuum(
            _D.V8_INCREMENTAL_VACUUM_PAGES if vacuum_pages is None else vacuum_pages)
        self.conn.execute("DROP TABLE IF EXISTS temp.v8w_cov")
        return out

    def _prune(self, now: int, retention_days: float) -> int:
        try:
            cutoff = max(0, int(float(now) - float(retention_days) * 86400.0))
        except (TypeError, ValueError, OverflowError):
            return 0
        if cutoff <= 0:
            return 0
        self._begin()
        try:
            n = 0
            for t in (S.NET_HOUR, S.HW_HOUR, S.LINK_EVENTS):
                n += self.conn.execute(f"DELETE FROM {t} WHERE ts < ?", (cutoff,)).rowcount
            n += self.conn.execute(
                f"DELETE FROM {S.APP_SESSIONS} WHERE MAX(started_ts, COALESCE(ended_ts, last_seen_ts)) < ? "
                "AND session_id IS NOT ?", (cutoff, self.session_id)).rowcount
            self.conn.commit()
            return n
        except BaseException:
            self._rollback()
            raise
