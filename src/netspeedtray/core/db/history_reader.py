"""
The read side of the history database, behind one interface (v8 rebuild, step 2).

``WidgetState`` used to hold the SQL for every history read. Those reads now go through a
``HistoryReader``, so the v8 schema can arrive as a second backend (step 3) and be switched in at one
place (step 7) instead of rewriting eight call sites against a new schema at once.

``V7HistoryReader`` is today's schema and must stay **behaviour-identical** to the methods it replaced:
``tests/unit/test_history_reader_golden.py`` diffs every method against a frozen copy of the old code
(``tests/unit/legacy_v7_reader_oracle.py``) on one rich fixture database written through the real
``DatabaseWorker``. Change a query here and that test is the thing to satisfy - not to edit.

``V8HistoryReader`` (step 3) is the v8 schema and, since the switch-over (step 7), the backend
``WidgetState`` uses; ``V7HistoryReader`` is only used while a v8 upgrade is deferred (the session is
read-only, the untouched v7 file live). ``tests/unit/test_history_reader_v8_parity.py`` writes one history into a v7
and a v8 file and asserts the backends agree everywhere except volume, where v8 is more exact (each
difference asserted). Its tier boundaries are the watermarks in ``schema_v8.WATERMARK_KEYS``, not MIN(ts).

Threading: the reader owns no connection. It asks ``conn_provider`` (``WidgetState._get_read_conn``)
for one on every call, so each calling thread keeps its own thread-local read connection exactly as
before - the Monitor's graph worker never shares a cursor with the GUI thread.

**Tier precedence.** The tiers are query-precedence layers, not additive: each tier answers only
``[its floor, the finer tiers' MIN(timestamp))`` - raw wherever raw has data, else minute, else hour.
The finer MIN only looks inside that tier's retention horizon (+ slack), so a row stamped far in the
past by a clock set back costs its own seconds and never hides the coarser history above it; and it
is a subquery of the statement it bounds, so boundary and data come from one snapshot. Readers that
need several statements run them in one read transaction (``_snapshot``), so an hourly roll-up that
commits mid-read can neither drop nor double-count the slice it moved.
v7 MOVES rows down the chain, so the tiers never overlap and the rule is a no-op on every real v7
database (the golden test proves it). It is here because v8 keeps independent per-tier retention, and
the design review measured an unguarded fan-out at exactly **2.00x** the true data-cap total
(database-v8-final.md 4.1) - every reader used to SUM every tier with no mutual exclusion.
``tests/unit/test_history_reader_precedence.py`` pins the 1.00x.

The side effects that belong to the app rather than to storage stay in ``WidgetState``: the
flush-before-read barrier, the ``db_worker`` presence guard, the poll-interval lookup from config, and
the odometer's in-memory state.
"""

import calendar
import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

from netspeedtray import constants
from netspeedtray.core import usage_snapshot
from netspeedtray.core.db import schema_v8 as S8

Resolution = Literal['auto', 'raw', 'minute', 'hour', 'day']
SpeedPoint = Tuple[Union[datetime, float], float, float]


@dataclass(frozen=True)
class UsageRecord:
    """The persisted data-usage odometer, exactly as stored - validation is the caller's job.

    ``counter_row`` is (cumulative_up, cumulative_down, anchor_up, anchor_down, period_key), or None
    when the table exists but holds no row yet. ``snapshot`` is the previous session's decoded NIC
    counter snapshot, {} when there is none or it is unreadable."""
    counter_row: Optional[Tuple[Any, ...]]
    snapshot: Dict[str, Any] = field(default_factory=dict)
    rekey_memory: Optional[str] = None      # the odometer's persisted re-key memory (raw JSON), if any


@dataclass(frozen=True)
class VolumeResult:
    """Bytes moved in a window, and how much of that is known rather than estimated (decision D5).

    ``up_bytes`` / ``down_bytes`` are the totals. ``exact_*`` is the part summed from stored byte counts
    (v8 rows); the rest was reconstructed from stored RATES (every v7 row, and v8 rollup rows migrated
    from v7). ``reconstructed`` is True when any part was estimated: the UI must then label the number
    "recorded history", never present it as exact - the odometer is the authority for volume claims."""
    up_bytes: float
    down_bytes: float
    exact_up_bytes: float
    exact_down_bytes: float
    reconstructed: bool


@dataclass(frozen=True)
class Coverage:
    """Whether the app was running over [start_ts, end_ts], from ``app_sessions`` (v8) - a recorded
    fact, never inferred from traffic and never backfilled. The three parts sum to the window:

    * ``running_seconds`` - inside a recorded session (a crashed session ends at its last heartbeat);
    * ``not_running_seconds`` - after recording began, outside every session: the app was NOT running;
    * ``unknown_seconds`` - before the first recorded session (pre-v8 history, or after "Delete all
      history"): no record either way. Never shown as "not running".

    ``end_ts`` is clipped at now: the future is not "not running". ``sessions`` = the merged running
    intervals inside the window."""
    start_ts: int
    end_ts: int
    running_seconds: int
    not_running_seconds: int
    unknown_seconds: int
    sessions: Tuple[Tuple[int, int], ...]


@dataclass(frozen=True)
class MeteredScope:
    """Which adapters a usage figure covers - the odometer's selection, as history can express it.

    ``names``: exactly these adapters (``WidgetState.metered_scope`` resolves every mode to names with
    the controller's classifier, exclusions and fallback rules). ``physical_only`` (names None): the
    stored ``interfaces.is_physical`` flag, every adapter when none is flagged - a coarser stand-in used
    only when the adapter names cannot be read."""
    names: Optional[Tuple[str, ...]] = None
    physical_only: bool = True


def subtract_spans(spans, holes) -> Tuple[Tuple[float, float], ...]:
    """``spans`` (sorted, disjoint (start, end) pairs) minus every ``holes`` interval."""
    out: List[Tuple[float, float]] = []
    cuts = sorted((float(a), float(b)) for a, b in holes if float(b) > float(a))
    for s, e in spans:
        parts = [(float(s), float(e))]
        for a, b in cuts:
            nxt = []
            for ps, pe in parts:
                if b <= ps or a >= pe:
                    nxt.append((ps, pe))
                    continue
                if a > ps:
                    nxt.append((ps, a))
                if b < pe:
                    nxt.append((b, pe))
            parts = nxt
            if not parts:
                break
        out.extend(parts)
    return tuple(out)


class HistoryReader(ABC):
    """Every read the app makes against the history database."""

    @abstractmethod
    def speed_history(self, start_time: Optional[datetime], end_time: Optional[datetime],
                      interface_name: Optional[str], return_raw: bool,
                      resolution: Resolution) -> List[SpeedPoint]:
        """(timestamp, up B/s, down B/s) binned to ``resolution``: stored rows only, never padded
        (a missing bucket is a silence; ``measured_spans`` says whether it was measured).
        ``timestamp`` is a datetime, or epoch seconds when ``return_raw``."""

    @abstractmethod
    def speed_resolution(self, start_time: Optional[datetime], end_time: Optional[datetime],
                         interface_name: Optional[str]) -> Optional[Resolution]:
        """The resolution ``speed_history(..., resolution='auto')`` reads this window at ('raw',
        'minute', 'hour', 'day'), or None when it cannot be read."""

    @abstractmethod
    def measured_spans(self, start_time: datetime,
                       end_time: datetime) -> Optional[Tuple[Tuple[float, float], ...]]:
        """Merged (start, end) epoch-second spans inside the window in which the app was measuring:
        a missing speed row there means no traffic. Outside them (app off, PC asleep, before the first
        recorded session) nothing was measured. None when the schema cannot say (v7)."""

    @abstractmethod
    def hardware_history(self, stat_type: str, start_time: Optional[datetime],
                         end_time: Optional[datetime]) -> List[Tuple[datetime, float]]:
        """(timestamp, value) for one hardware stat, binned by window length (1 s / 60 s / 3600 s)."""

    @abstractmethod
    def summarize_hardware(self, stat_type: str, start_time: datetime, end_time: datetime,
                           poll_interval: float = 1.0):
        """Honest WindowSummary for a hardware stat over [start, end] (see utils.summaries)."""

    @abstractmethod
    def summarize_network(self, direction: str, start_time: datetime, end_time: datetime,
                          interface_name: Optional[str] = None, poll_interval: float = 1.0):
        """Honest WindowSummary for 'download' or 'upload' B/s over [start, end], per interface or
        aggregated across interfaces when ``interface_name`` is None/'all'."""

    @abstractmethod
    def network_hourly_profile(self, direction: str, start_time: datetime, end_time: datetime,
                               interface_name: Optional[str] = None) -> Dict[int, float]:
        """{local clock-hour 0-23: time-weighted AVERAGE B/s} over [start, end], from the same samples and
        weights as ``summarize_network`` - the basis for "busiest hour". Never from graph points: those
        are per-bucket PEAKS, which made a 0.1 Mbps-average window claim an 80 Mbps busiest hour."""

    @abstractmethod
    def total_bandwidth(self, start_time: Optional[datetime], end_time: datetime,
                        interface_name: Optional[str], poll_interval: float) -> Tuple[float, float]:
        """(upload_bytes, download_bytes) moved in [start, end]. ``poll_interval`` is the seconds one
        raw row stands for (the caller resolves it from config)."""

    @abstractmethod
    def volume(self, start_time: Optional[datetime], end_time: datetime,
               interface_name: Optional[str], poll_interval: float) -> VolumeResult:
        """``total_bandwidth`` with its provenance: how much is exact, and whether any of it was
        reconstructed from rates (D5 labelling)."""

    def metered_volume(self, start_time: Optional[datetime], end_time: datetime,
                       scope: Optional["MeteredScope"], poll_interval: float) -> VolumeResult:
        """``volume`` over only the adapters the data-usage odometer meters (``scope``; None = every
        adapter). The usage rows next to the cap ("Today", "This Period") must count what the cap
        counts: summing every adapter double-counts a VPN / WSL / Hyper-V adapter whose bytes also
        ride the real NIC. A backend without adapter metadata (v7) answers for every adapter."""
        return self.volume(start_time, end_time, None, poll_interval)

    def metered_volume_strict(self, start_time: Optional[datetime], end_time: datetime,
                              scope: Optional["MeteredScope"]) -> Optional[VolumeResult]:
        """``metered_volume`` for decisions that move the odometer: None when history cannot answer (a
        failed read; a backend without adapter metadata) - ``metered_volume`` turns every error into
        zeros, which a re-key would take for "nothing moved"."""
        return None

    def interface_names(self) -> Optional[List[str]]:
        """Every adapter name history knows (for ``WidgetState.metered_scope``), or None."""
        return None

    def clock_trusted_ts(self) -> Optional[int]:
        """The history writer's last maintenance pass on a trusted clock (epoch s), or None."""
        return None

    @abstractmethod
    def coverage(self, start_time: datetime, end_time: datetime) -> Optional[Coverage]:
        """When the app was running in [start, end], or None when this schema records no sessions.
        No UI consumer yet (2.2): kept for the "the app was not running" coverage fact."""

    @abstractmethod
    def distinct_interfaces(self) -> List[str]:
        """Every interface name in any speed tier, sorted."""

    @abstractmethod
    def earliest_timestamp(self) -> Optional[datetime]:
        """The oldest speed sample in any tier, or None for an empty history."""

    @abstractmethod
    def load_usage(self) -> Optional[UsageRecord]:
        """The odometer row + counter snapshot, or None while the table does not exist yet (the
        caller defers). Storage errors propagate; a bad snapshot only empties ``snapshot``."""


# network_hourly_profile: (ts, rate, weight) rows from every tier -> weighted mean rate per LOCAL clock
# hour. SQLite's 'localtime' is the C runtime's, the same conversion datetime.fromtimestamp() makes.
def _local_bin_sql(col: str, interval: int) -> str:
    """SQL for the start of ``col``'s ``interval``-second bucket. Hour and day buckets are aligned to
    LOCAL time (SQLite's 'localtime' - the C runtime's, as datetime.fromtimestamp() - so DST is
    honoured): plain ``ts / 86400`` buckets
    started at UTC midnight - 02:00 or 03:00 in Israel, moving by an hour across a DST change - and
    hour buckets were half an hour off in +5:30 / +9:30 zones. Minute and second buckets are the same
    either way, so they keep the cheap integer form.

    DAY buckets come back as a local DAY NUMBER (days since 1970-01-01 on the local calendar), not a
    timestamp: one 'localtime' call per row, the same cost as an hour bucket. Every caller passes the
    grouped value through ``_bin_start()``, which turns each day number into its local midnight once
    per OUTPUT row. Day numbers and midnights map one to one, so GROUP BY / ORDER BY are unchanged."""
    if interval < 3600:
        return f"CAST({col} / {interval} AS INTEGER) * {interval}"
    local = f"CAST(strftime('%s', {col}, 'unixepoch', 'localtime') AS INTEGER)"
    if interval >= 86400:
        return f"CAST({local} / {interval} AS INTEGER)"
    # The row's own offset, applied to the row: start of its local hour as an epoch. Never later than
    # the row, and on a fall-back night the two 01:00-02:00 hours stay two buckets.
    return f"({col} - ({local} % {interval}))"


def _local_epoch(ts: int) -> int:
    """``ts`` as local wall-clock seconds since 1970 - the C runtime's localtime, as SQLite's."""
    return calendar.timegm(time.localtime(ts))


def _local_day_start(day: int) -> int:
    """The first instant (epoch s) whose local date is local day number ``day``: its local midnight,
    converted back with the MIDNIGHT's own offset - on a 23 h / 25 h day the offset at the day's
    other rows is not the midnight's. Where midnight is repeated (a fall-back at 01:00 -> 00:00) the
    earlier instance is taken, so a bucket never starts after its rows; where it is skipped (a
    spring-forward at 00:00) the day starts at its first valid local time."""
    target = day * 86400
    try:
        return _first_instant_at_or_after(target)
    except (OSError, OverflowError, ValueError):   # outside the C runtime's range (pre-1970 on Windows)
        return target


def _first_instant_at_or_after(target: int) -> int:
    # Every offset in force around that midnight (offsets are within +-14 h, transitions are never
    # less than a day apart), so one of these candidates is the midnight whenever it exists.
    offs = {_local_epoch(t) - t for t in (target - 15 * 3600, target, target + 15 * 3600)}
    hits = [target - o for o in offs if _local_epoch(target - o) == target]
    if hits:
        return min(hits)
    # Midnight skipped: the first instant whose local time is at or past it (binary search over the
    # transition, which lies between the two candidates).
    lo, hi = target - max(offs), target - min(offs)
    while _local_epoch(hi) < target:   # defensive: widen until the bracket holds
        hi += 3600
    while lo < hi:
        mid = (lo + hi) // 2
        if _local_epoch(mid) >= target:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _bin_start(value, interval: int):
    """A ``_local_bin_sql`` column value as its bucket's start (epoch s): day numbers -> local
    midnight; every shorter interval is already a timestamp. NULL stays NULL."""
    if value is None or interval < 86400:
        return value
    return _local_day_start(int(value))


_HOURLY_PROFILE_SQL = ("SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS h, "
                       "SUM(v * w), SUM(w) FROM ({union}) WHERE v IS NOT NULL AND w > 0 GROUP BY h")


def _hourly_rows(rows) -> Dict[int, float]:
    return {int(h): float(vw) / float(w) for h, vw, w in rows if h is not None and w}


def _bucket_rate_sql(avg: str, bytes_: str, sc: str) -> Tuple[str, str]:
    """(rate, weight) SQL aggregates for ONE network rollup bucket (GROUP BY its timestamp), across the
    interfaces that have a row in it. ``avg`` / ``bytes_`` / ``sc`` are per-row expressions: the stored
    mean rate, the bytes the row moved, and the seconds (samples) it stands for.

    The bucket's rate is the bytes every interface moved over the bucket's span: SUM(bytes) / MAX(sc),
    weighted by MAX(sc). NOT SUM(avg) weighted by SUM(sc): the per-interface rates do add up, but their
    spans overlap in time, so SUM(avg) x SUM(sc) exceeds the bucket's real SUM(avg x sc) = bytes whenever
    more than one interface moved data (Wi-Fi 1000 B/s + vEthernet 1 B/s over one minute weighed as
    1001 B/s for 120 s), overstating every multi-interface "all" average. MAX(sc) is the longest
    interface's span - the bucket's measured time, short of the true union only when the interfaces
    were active in disjoint seconds, which reads that bucket high by the union / MAX ratio (about 3%
    for a Wi-Fi active 90% of seconds beside a vEthernet active 30%,
    test_history_multi_interface_average); the rollup tiers keep no per-second union. A single-row bucket (one interface selected, or only one moved
    data) keeps its stored avg and sc exactly. (``* 1.0``: v8 bytes and sample_count are INTEGER, and
    SQLite divides two integers as integers.)"""
    rate = (f"CASE WHEN COUNT(*) = 1 THEN MAX({avg}) "
            f"ELSE COALESCE(SUM({bytes_}) * 1.0 / NULLIF(MAX({sc}), 0), SUM({avg})) END")
    return rate, f"MAX({sc})"


class HistoryNotReadyError(sqlite3.OperationalError):
    """The history file cannot be opened for reading YET (the worker is still creating/migrating it -
    a v7 -> v8 upgrade on the first 2.2 launch) or at all this session. Reads issued in that window
    return empty by design, so it is logged at INFO without a traceback, never as a failure. A
    subclass of OperationalError so every ``except sqlite3.Error`` still catches it."""


class _ReaderBase(HistoryReader):
    """What both backends share: connection/logger plumbing, the read snapshot, the retention
    horizons, and the odometer read (``usage_counter`` + ``metadata`` are identical in v7 and v8)."""

    def __init__(self, conn_provider: Callable[[], sqlite3.Connection],
                 db_path_provider: Callable[[], Path],
                 logger: Optional[logging.Logger] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self._conn = conn_provider
        self._db_path = db_path_provider
        # Epoch seconds "now" for the horizon / watermark-lead / open-ended-window rules. The app always
        # uses the wall clock; tests inject a fixed one so no result depends on when the suite runs.
        self._clock = clock or time.time
        # The owner's logger, so every message keeps the name and wording it had inside WidgetState.
        self.logger = logger or logging.getLogger("NetSpeedTray.HistoryReader")

    def _now_ts(self) -> int:
        return int(self._clock())

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock())

    def _log_read_failure(self, what: str, e: BaseException) -> None:
        """A read that failed and will return empty. Not-ready-yet is expected (INFO, no traceback);
        anything else is a real failure (ERROR, with traceback).

        ``OperationalError('interrupted')`` is expected too: the only sqlite3_interrupt in the app is
        WidgetState.interrupt_reads(), which stops a reader on purpose (Monitor close / quit while a
        graph query runs). Logging that as an ERROR with a traceback would read as a crash."""
        if isinstance(e, HistoryNotReadyError):
            self.logger.info("%s skipped, returning empty: %s", what, e)
        elif isinstance(e, sqlite3.OperationalError) and str(e).strip().lower() == "interrupted":
            self.logger.info("%s stopped (reader interrupted for close/quit), returning empty", what)
        else:
            self.logger.error("%s: %s", what, e, exc_info=True)

    _NO_BOUND = "9e18"   # past any epoch second: "no finer data inside the horizon" bounds nothing

    @staticmethod
    def _horizons(now_ts: int) -> Tuple[int, int]:
        """Oldest timestamp a legitimately-unrolled raw / minute row can have: the tier's retention,
        plus slack for the hourly maintenance cadence and the bucket floor. A finer row older than
        this is a stray (clock set back, VM snapshot, dead CMOS) and must not set a tier boundary."""
        d = constants.data
        slack = d.TIER_BOUNDARY_SLACK_SECONDS
        return (now_ts - d.RAW_TIER_RETENTION_SECONDS - slack,
                now_ts - d.MINUTE_TIER_RETENTION_SECONDS - slack)

    @staticmethod
    def _begin_snapshot(conn: sqlite3.Connection) -> bool:
        """Open a read transaction so every statement of one read sees the same snapshot (WAL). Returns
        whether this call opened it (and so must end it). Read connections never write."""
        if getattr(conn, "in_transaction", False):
            return False
        conn.execute("BEGIN")
        return True

    @staticmethod
    def _end_snapshot(conn: sqlite3.Connection, began: bool) -> None:
        if began:
            try:
                conn.execute("COMMIT")   # nothing written - just releases the snapshot
            except sqlite3.Error:
                pass

    # --- data-usage odometer ------------------------------------------------------------------

    def load_usage(self) -> Optional[UsageRecord]:
        conn = self._conn()
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (constants.data.USAGE_COUNTER_TABLE,))
        if cur.fetchone() is None:
            return None
        cur.execute(
            f"SELECT cumulative_up, cumulative_down, anchor_up, anchor_down, period_key "
            f"FROM {constants.data.USAGE_COUNTER_TABLE} WHERE id = 1")
        row = cur.fetchone()
        memory = None
        try:
            m = cur.execute("SELECT value FROM metadata WHERE key = ?",
                            (constants.network.usage.REKEY_MEMORY_METADATA_KEY,)).fetchone()
            memory = m[0] if m else None
        except Exception as e:
            self.logger.debug("No usage re-key memory readable: %s", e)
        return UsageRecord(tuple(row) if row else None, self._read_usage_snapshot(cur), memory)

    def _read_usage_snapshot(self, cur: sqlite3.Cursor) -> Dict[str, Any]:
        """The previous session's counter snapshot (metadata row), or {} - never raises, so a
        missing/garbled snapshot can only cost the reconciliation, never the odometer load."""
        try:
            cur.execute("SELECT value FROM metadata WHERE key = ?",
                        (constants.network.usage.SNAPSHOT_METADATA_KEY,))
            row = cur.fetchone()
            return usage_snapshot.decode_snapshot(row[0] if row else None)
        except Exception as e:
            self.logger.debug("No usage counter snapshot readable: %s", e)
            return {}


class V7HistoryReader(_ReaderBase):
    """The v7 schema: speed_history_{raw,minute,hour} + hardware_stats_{raw,minute,hour} +
    usage_counter + metadata.

    Since 2.2 the app only reads a v7 file while its v8 upgrade is deferred (read-only session);
    WidgetState then sets ``allow_fallback = False``, because the db_utils fallback opens a plain
    read-write connection, and the last read-write connection to close a WAL file checkpoints it."""

    allow_fallback = True

    # --- tier precedence ----------------------------------------------------------------------

    def _bounds(self, tables: Tuple[str, ...]) -> List[Tuple[str, tuple]]:
        """Per tier (``tables`` finest first), the SQL clause + params confining it under every FINER
        tier's earliest in-horizon row. The finest tier is unbounded. The MIN is an uncorrelated
        scalar subquery - evaluated once, inside the bounded statement's own snapshot - and a single
        seek on the (timestamp, ...) primary key."""
        horizons = self._horizons(self._now_ts())
        out: List[Tuple[str, tuple]] = [("", ())]
        for i in range(1, len(tables)):
            parts = [f"COALESCE((SELECT MIN(timestamp) FROM {tables[j]} WHERE timestamp >= ?), {self._NO_BOUND})"
                     for j in range(i)]
            expr = parts[0] if len(parts) == 1 else f"MIN({', '.join(parts)})"
            out.append((f" AND timestamp < {expr}", tuple(horizons[:i])))
        return out

    _SPEED_TIERS = (constants.data.SPEED_TABLE_RAW, constants.data.SPEED_TABLE_MINUTE,
                    constants.data.SPEED_TABLE_HOUR)
    _HW_TIERS = (constants.data.HARDWARE_STATS_TABLE_RAW, constants.data.HARDWARE_STATS_TABLE_MINUTE,
                 constants.data.HARDWARE_STATS_TABLE_HOUR)

    # The raw tier retains ~24h; windows within it summarize exactly (pro-stats: tier-aware honest
    # window summaries - exact <=24h raw, avg+max beyond).
    _RAW_SUMMARY_SECONDS = 24 * 3600

    # --- speed --------------------------------------------------------------------------------

    def speed_history(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None,
                      interface_name: Optional[str] = None, return_raw: bool = False,
                      resolution: Resolution = 'auto') -> List[SpeedPoint]:
        # 1. Timeline Setup
        _now_ts = self._now_ts()
        _start_ts = int(start_time.timestamp()) if start_time else 0
        _end_ts = int(end_time.timestamp()) if end_time else _now_ts

        # 2. Determine Resolution
        target_res = resolution
        if target_res == 'auto':
            target_res = constants.data.history_period.get_target_resolution(start_time, end_time)

        # Resolution -> Interval (seconds) mapping
        # 'day' maps to 86400, others to their standard seconds
        res_map = {'raw': 1, 'minute': 60, 'hour': 3600, 'day': 86400}
        target_interval = res_map.get(target_res, 60)

        # 3. Build TARGETED Query (Single Table based on Resolution)
        # For multi-tier queries (minute/hour), we query both the aggregated table AND raw table
        # to ensure we capture recent data that hasn't been moved to aggregates yet.
        is_all_ifaces = not interface_name or str(interface_name).lower() == "all"

        # Map resolution to primary table and columns
        table_map = {
            'raw': ("speed_history_raw", "upload_bytes_sec", "download_bytes_sec"),
            'minute': ("speed_history_minute", "upload_avg", "download_avg"),
            'hour': ("speed_history_hour", "upload_avg", "download_avg"),
            'day': ("speed_history_hour", "upload_avg", "download_avg"),
        }

        table, up_col, down_col = table_map.get(target_res, table_map['minute'])

        try:
            conn = self._conn()
            cursor = conn.cursor()

            # Time binning calculation
            time_calc = _local_bin_sql("timestamp", target_interval)   # local hour / day buckets

            # Build inner query. For aggregated resolutions, construct a UNION
            # and keep peak speed semantics (MAX) across tiers so timeline
            # changes do not dilute/reshape the same event differently.
            if target_res in ('minute', 'hour', 'day'):
                # Multi-tier merge with explicit peak-preserving logic.
                tier_queries = []
                params = []
                bound = dict(zip(self._SPEED_TIERS, self._bounds(self._SPEED_TIERS)))

                def add_tier_query(table_name: str, up_expr: str, down_expr: str) -> None:
                    below_sql, below_params = bound[table_name]
                    q = f"""
                        SELECT
                            {time_calc} as bin_ts,
                            interface_name,
                            {up_expr} as up,
                            {down_expr} as down
                        FROM {table_name}
                        WHERE timestamp BETWEEN ? AND ?{below_sql}
                    """
                    tier_params = [_start_ts, _end_ts, *below_params]
                    if not is_all_ifaces:
                        q += " AND interface_name = ?"
                        tier_params.append(interface_name)
                    tier_queries.append(q)
                    params.extend(tier_params)

                # Raw keeps exact per-second peaks.
                add_tier_query(constants.data.SPEED_TABLE_RAW, "upload_bytes_sec", "download_bytes_sec")
                # Aggregated tiers use preserved per-bucket maxima.
                add_tier_query(constants.data.SPEED_TABLE_MINUTE, "upload_max", "download_max")

                if target_res in ('hour', 'day'):
                    add_tier_query(constants.data.SPEED_TABLE_HOUR, "upload_max", "download_max")

                union_query = " UNION ALL ".join(tier_queries)
                inner_query = f"""
                    SELECT
                        bin_ts,
                        interface_name,
                        MAX(up) as up,
                        MAX(down) as down
                    FROM ({union_query})
                    GROUP BY bin_ts, interface_name
                """
            else:
                # Raw resolution: single table query
                inner_query = f"""
                    SELECT
                        {time_calc} as bin_ts,
                        interface_name,
                        AVG({up_col}) as up,
                        AVG({down_col}) as down
                    FROM {table}
                    WHERE timestamp BETWEEN ? AND ?
                """
                params = [_start_ts, _end_ts]
                if not is_all_ifaces:
                    inner_query += " AND interface_name = ?"
                    params.append(interface_name)
                inner_query += " GROUP BY bin_ts, interface_name"

            # Outer query: aggregate bins
            if is_all_ifaces:
                outer_query = f"""
                    SELECT bin_ts, COALESCE(SUM(up), 0), COALESCE(SUM(down), 0)
                    FROM ({inner_query})
                    GROUP BY bin_ts
                    ORDER BY bin_ts
                """
            else:
                outer_query = f"""
                    SELECT bin_ts, COALESCE(AVG(up), 0), COALESCE(AVG(down), 0)
                    FROM ({inner_query})
                    GROUP BY bin_ts
                    ORDER BY bin_ts
                """

            cursor.execute(outer_query, tuple(params))
            rows = cursor.fetchall()
            self.logger.debug("History query: target_res=%s fetched_rows=%d", target_res, len(rows))

            # Convert rows to standard format (Timestamp, Up, Down)
            valid_rows = [row for row in rows if row and row[0] is not None]
            if len(valid_rows) != len(rows):
                self.logger.warning(
                    "Dropping %d invalid graph rows with NULL timestamp (resolution=%s).",
                    len(rows) - len(valid_rows),
                    target_res
                )

            valid_rows = [(_bin_start(row[0], target_interval), row[1], row[2]) for row in valid_rows]
            data_points = []
            if return_raw:
                data_points = [(int(row[0]), float(row[1] or 0.0), float(row[2] or 0.0)) for row in valid_rows]
            else:
                data_points = [(datetime.fromtimestamp(int(row[0])), float(row[1] or 0.0), float(row[2] or 0.0)) for row in valid_rows]

            # If targeted-table query returned no rows and we targeted an aggregated
            # table (minute/hour/day), fall back to the more comprehensive
            # optimized query in utils.db_utils which unions tiers. This allows
            # freshly-started apps (with only raw data present) to still show
            # historical ranges by reading directly from raw where appropriate.
            # (Kept for parity. It reads a SUBSET of what the union above already read - raw from the
            # aggregation cutoff on, minute before it - so when the union is empty it is empty too.)
            if not data_points and target_res != 'raw' and self.allow_fallback:
                try:
                    from netspeedtray.utils.db_utils import get_speed_history as util_get_speed_history
                    self.logger.debug("Targeted query returned no rows; falling back to unified DB query.")
                    fallback = util_get_speed_history(self._db_path(), start_time=start_time, end_time=end_time, interface_name=interface_name)
                    self.logger.debug("Fallback unified DB query returned %d rows", len(fallback) if fallback else 0)
                    if fallback:
                        if return_raw:
                            data_points = [(int(dt.timestamp()), up, down) for dt, up, down in fallback]
                        else:
                            data_points = [(dt, up, down) for dt, up, down in fallback]
                except Exception:
                    self.logger.exception("Fallback unified DB query failed.")

            # No edge padding (rc.2 S4): stored rows only, as in V8HistoryReader. The synthetic zero
            # rows drew unmeasured time as a measured zero.
            return data_points

        except sqlite3.Error as e:
            self._log_read_failure("Unified graph query failed", e)
            return []

    def summarize_network(self, direction: str, start_time: datetime, end_time: datetime,
                          interface_name: Optional[str] = None, poll_interval: float = 1.0):
        from netspeedtray.utils import summaries as S
        col = "download" if direction == "download" else "upload"
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        iface = None if interface_name in (None, "all") else interface_name
        wh = "" if iface is None else " AND interface_name=?"
        params_tail = () if iface is None else (iface,)
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            cur = conn.cursor()

            def _raw_vals():
                # Sum per-timestamp across interfaces when aggregating, so an "all" summary matches the
                # widget's aggregate rather than mixing per-NIC samples.
                cur.execute(
                    f"SELECT SUM({col}_bytes_sec) FROM {constants.data.SPEED_TABLE_RAW} "
                    f"WHERE timestamp BETWEEN ? AND ?{wh} GROUP BY timestamp", (st, et) + params_tail)
                return [r[0] for r in cur.fetchall() if r[0] is not None]

            if win <= self._RAW_SUMMARY_SECONDS:
                vals = _raw_vals()
                return S.summarize_raw(vals, S.coverage_pct(len(vals), win, poll_interval))

            # Beyond the raw horizon the window spans tiers: the recent <24h is still in RAW, older data
            # in minute/hour. Union all three so the summary covers the WHOLE window instead of only the
            # rolled-up older half (which dropped the most recent ~24h and disagreed with the graph for
            # the same window).
            avgs, maxes, counts = [], [], []
            covered_seconds = 0.0   # for coverage: count distinct TIME buckets x their duration, NOT
            #                         SUM(sample_count) - which, for an "all" aggregate, double-counts by
            #                         NIC and inflates the evidence-admissibility figure past 100%. The
            #                         same double count is why a bucket's rate / weight come from
            #                         _bucket_rate (bytes / MAX(sample_count)), not SUM(avg) / SUM(count).
            _, min_bound, hour_bound = self._bounds(self._SPEED_TIERS)
            rate, weight = self._bucket_rate(col)
            for table, bucket_secs, (below_sql, below_params) in (
                    (constants.data.SPEED_TABLE_MINUTE, 60.0, min_bound),
                    (constants.data.SPEED_TABLE_HOUR, 3600.0, hour_bound)):
                cur.execute(
                    f"SELECT {rate}, MAX({col}_max), {weight} FROM {table} "
                    f"WHERE timestamp BETWEEN ? AND ?{below_sql}{wh} GROUP BY timestamp",
                    (st, et) + below_params + params_tail)
                for r in cur.fetchall():
                    if r[0] is None:
                        continue
                    avgs.append(r[0]); maxes.append(r[1]); counts.append(r[2] or 1)
                    covered_seconds += bucket_secs   # one distinct timestamp bucket of this tier
            raw_vals = _raw_vals()
            if not avgs:   # the whole window still fits the raw tier -> exact percentiles
                return S.summarize_raw(raw_vals, S.coverage_pct(len(raw_vals), win, poll_interval))
            avgs += raw_vals; maxes += raw_vals; counts += [1] * len(raw_vals)   # raw samples = count-1 buckets
            covered_seconds += len(raw_vals) * poll_interval
            coverage = min(100.0, (covered_seconds / win * 100.0)) if win > 0 else 0.0
            tier = "minute" if win <= 30 * 86400 else "hour"
            return S.summarize_rollup(avgs, maxes, counts, tier, coverage)
        except Exception as e:
            self._log_read_failure("summarize_network failed", e)
            return S.summarize_raw([])
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)

    @staticmethod
    def _bucket_rate(col: str) -> Tuple[str, str]:
        """(rate, weight) of one minute/hour bucket across interfaces (``_bucket_rate_sql``). A v7
        rollup's sample_count is its raw-row count, one per second at the default 1 s capture, so
        avg x sample_count is SUM(the stored per-second rates) - the bytes, in the same unit."""
        sc = "COALESCE(sample_count, 1)"
        return _bucket_rate_sql(f"{col}_avg", f"{col}_avg * {sc}", sc)

    def network_hourly_profile(self, direction: str, start_time: datetime, end_time: datetime,
                               interface_name: Optional[str] = None) -> Dict[int, float]:
        """summarize_network's samples and weights, per local clock-hour: raw rows weigh one sample;
        beyond 24 h each bounded minute / hour bucket is its bytes / MAX(sample_count), weighed by
        that MAX(sample_count) (_bucket_rate)."""
        col = "download" if direction == "download" else "upload"
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        iface = None if interface_name in (None, "all") else interface_name
        wh = "" if iface is None else " AND interface_name=?"
        tail = () if iface is None else (iface,)
        parts = [f"SELECT timestamp AS ts, SUM({col}_bytes_sec) AS v, 1 AS w FROM {constants.data.SPEED_TABLE_RAW} "
                 f"WHERE timestamp BETWEEN ? AND ?{wh} GROUP BY timestamp"]
        params: List[Any] = [st, et, *tail]
        if (end_time - start_time).total_seconds() > self._RAW_SUMMARY_SECONDS:
            _, min_bound, hour_bound = self._bounds(self._SPEED_TIERS)
            for table, (below_sql, below_params) in ((constants.data.SPEED_TABLE_MINUTE, min_bound),
                                                     (constants.data.SPEED_TABLE_HOUR, hour_bound)):
                rate, weight = self._bucket_rate(col)
                parts.append(f"SELECT timestamp AS ts, {rate} AS v, COALESCE({weight}, 1) AS w "
                             f"FROM {table} WHERE timestamp BETWEEN ? AND ?{below_sql}{wh} GROUP BY timestamp")
                params += [st, et, *below_params, *tail]
        query = _HOURLY_PROFILE_SQL.format(union=" UNION ALL ".join(parts))
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            return _hourly_rows(conn.execute(query, params).fetchall())
        except Exception as e:
            self._log_read_failure("network_hourly_profile failed", e)
            return {}
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)

    def total_bandwidth(self, start_time: Optional[datetime], end_time: datetime,
                        interface_name: Optional[str], poll_interval: float) -> Tuple[float, float]:
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            cursor = conn.cursor()

            start_ts = int(start_time.timestamp()) if start_time else 0
            end_ts = int(end_time.timestamp())

            total_up, total_down = 0.0, 0.0

            # Optimization: Only query tiers that could potentially have data for this range.
            now_ts = self._now_ts()

            # Bytes per tier = (sum of per-sample RATES) x (the seconds that sum represents). For the
            # MINUTE and HOUR rollups that span is the FIXED bucket duration (60 / 3600 s) - NOT
            # sample_count x the LIVE poll interval, which silently rescales ALL historical data when the
            # user changes the poll rate (1s->5s would 5x-over-report months of minute/hour history). The
            # raw tier has no stored capture interval, so it uses the current poll interval (it's <=24h,
            # where the rate rarely changes).
            #
            # KNOWN OVER-COUNT, deliberately kept (v8 step 2 is behaviour-identical): avg x 60 assumes
            # every second of the bucket was sampled, but the writer only stores a raw row when the NIC
            # moved >= 1 B/s, so avg is the mean over ACTIVE seconds and avg x 60 over-states a partly
            # idle minute. The v8 backend fixes it by storing bytes (SUM(bytes)), not by patching this.
            tiers = []
            if start_ts <= now_ts:                  # raw: SUM(bytes_sec) x poll_interval
                tiers.append(("speed_history_raw", "upload_bytes_sec", "download_bytes_sec", poll_interval))
            if start_ts < (now_ts - 24 * 3600):     # minute: SUM(avg) x 60
                tiers.append(("speed_history_minute", "upload_avg", "download_avg", 60.0))
            if start_ts < (now_ts - 30 * 86400):    # hour: SUM(avg) x 3600
                tiers.append(("speed_history_hour", "upload_avg", "download_avg", 3600.0))

            # Precedence: a tier is summed only below every finer tier's earliest row, so a range two
            # tiers both hold is billed once (raw where present, else minute, else hour).
            bound = dict(zip(self._SPEED_TIERS, self._bounds(self._SPEED_TIERS)))
            for table, up_expr, down_expr, secs in tiers:
                below_sql, below_params = bound[table]
                query = (f"SELECT SUM({up_expr}), SUM({down_expr}) FROM {table} "
                         f"WHERE timestamp BETWEEN ? AND ?{below_sql}")
                params = [start_ts, end_ts, *below_params]
                if interface_name and str(interface_name).lower() != "all":
                    query += " AND interface_name = ?"
                    params.append(interface_name)
                cursor.execute(query, params)
                row = cursor.fetchone()
                if row:
                    total_up += (row[0] or 0.0) * secs
                    total_down += (row[1] or 0.0) * secs

            return total_up, total_down

        except Exception as e:
            self._log_read_failure("Error calculating total bandwidth", e)
            return 0.0, 0.0
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)

    def volume(self, start_time: Optional[datetime], end_time: datetime,
               interface_name: Optional[str], poll_interval: float) -> VolumeResult:
        # v7 stores rates only, so every byte it reports is a rate x interval reconstruction (D5).
        up, down = self.total_bandwidth(start_time, end_time, interface_name, poll_interval)
        return VolumeResult(up, down, 0.0, 0.0, True)

    def coverage(self, start_time: datetime, end_time: datetime) -> Optional[Coverage]:
        return None   # v7 records no sessions; traffic rows are not evidence the app was running

    def measured_spans(self, start_time: datetime,
                       end_time: datetime) -> Optional[Tuple[Tuple[float, float], ...]]:
        return None   # no sessions: no silence can be called measured

    def speed_resolution(self, start_time: Optional[datetime], end_time: Optional[datetime],
                         interface_name: Optional[str]) -> Optional[Resolution]:
        return constants.data.history_period.get_target_resolution(start_time, end_time)

    def distinct_interfaces(self) -> List[str]:
        try:
            conn = self._conn()
            cursor = conn.cursor()

            # Query all three tables to be comprehensive
            cursor.execute("""
                SELECT DISTINCT interface_name FROM speed_history_raw
                UNION
                SELECT DISTINCT interface_name FROM speed_history_minute
                UNION
                SELECT DISTINCT interface_name FROM speed_history_hour
                ORDER BY interface_name
            """)
            interfaces = [row[0] for row in cursor.fetchall()]
            return interfaces
        except sqlite3.Error as e:
            self._log_read_failure("Error fetching distinct interfaces", e)
            return []

    def earliest_timestamp(self) -> Optional[datetime]:
        try:
            conn = self._conn()
            cursor = conn.cursor()

            query = """
                SELECT MIN(earliest_ts) FROM (
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_raw
                    UNION ALL
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_minute
                    UNION ALL
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_hour
                ) WHERE earliest_ts IS NOT NULL;
            """
            cursor.execute(query)
            result = cursor.fetchone()

            if result and result[0] is not None:
                earliest_ts = int(result[0])
                return datetime.fromtimestamp(earliest_ts)

        except sqlite3.Error as e:
            self._log_read_failure("Failed to retrieve the earliest timestamp from database", e)

        return None

    # --- hardware -----------------------------------------------------------------------------

    def hardware_history(self, stat_type: str, start_time: Optional[datetime] = None,
                         end_time: Optional[datetime] = None) -> List[Tuple[datetime, float]]:
        try:
            conn = self._conn()
            cursor = conn.cursor()

            _end = end_time or self._now()
            _start = start_time or (_end - timedelta(hours=24))

            start_ts = int(_start.timestamp())
            end_ts = int(_end.timestamp())

            # Target resolution by window length: raw (<=6h), minute (<=30d), hour (>30d).
            duration = end_ts - start_ts
            interval = 1 if duration <= 6 * 3600 else (60 if duration <= 30 * 86400 else 3600)

            # Union ALL tiers and bin to the target interval, mirroring speed_history. A
            # recent-but-long window (e.g. 48h or a week) has its most-recent <24h only in the RAW tier
            # and the older portion in minute/hour; reading a single tier silently dropped the recent
            # half (and the old fallback only fired when the tier was TOTALLY empty). Binning keeps the
            # point count bounded by window/interval regardless.
            HRAW = constants.data.HARDWARE_STATS_TABLE_RAW
            HMIN = constants.data.HARDWARE_STATS_TABLE_MINUTE
            HHOUR = constants.data.HARDWARE_STATS_TABLE_HOUR
            bin_ts = _local_bin_sql("timestamp", interval)
            _, (min_sql, min_params), (hour_sql, hour_params) = self._bounds(self._HW_TIERS)
            cursor.execute(f"""
                SELECT b, AVG(v) FROM (
                    SELECT {bin_ts} AS b, value AS v FROM {HRAW}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?
                    UNION ALL
                    SELECT {bin_ts} AS b, avg_value AS v FROM {HMIN}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?{min_sql}
                    UNION ALL
                    SELECT {bin_ts} AS b, avg_value AS v FROM {HHOUR}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?{hour_sql}
                ) GROUP BY b ORDER BY b ASC
            """, (stat_type, start_ts, end_ts) + (stat_type, start_ts, end_ts) + min_params
                 + (stat_type, start_ts, end_ts) + hour_params)
            rows = cursor.fetchall()

            return [(datetime.fromtimestamp(_bin_start(row[0], interval)), row[1]) for row in rows]
        except Exception as e:
            self._log_read_failure("Error fetching hardware history", e)
            return []

    def summarize_hardware(self, stat_type: str, start_time: datetime, end_time: datetime,
                           poll_interval: float = 1.0):
        from netspeedtray.utils import summaries as S
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            cur = conn.cursor()
            if win <= self._RAW_SUMMARY_SECONDS:
                cur.execute(f"SELECT value FROM {constants.data.HARDWARE_STATS_TABLE_RAW} "
                            f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?", (stat_type, st, et))
                vals = [r[0] for r in cur.fetchall()]
                return S.summarize_raw(vals, S.coverage_pct(len(vals), win, poll_interval))
            # Beyond the raw horizon the window spans tiers: the recent <24h is still in RAW, older data
            # in minute/hour. Union all three so the summary covers the WHOLE window instead of only the
            # rolled-up older half, which silently dropped the most recent ~24h and disagreed with the
            # graph.
            avgs, maxes, counts = [], [], []
            _, min_bound, hour_bound = self._bounds(self._HW_TIERS)
            for table, (below_sql, below_params) in ((constants.data.HARDWARE_STATS_TABLE_MINUTE, min_bound),
                                                     (constants.data.HARDWARE_STATS_TABLE_HOUR, hour_bound)):
                cur.execute(f"SELECT avg_value, max_value, sample_count FROM {table} "
                            f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?{below_sql}",
                            (stat_type, st, et) + below_params)
                for r in cur.fetchall():
                    if r[0] is None:
                        continue
                    avgs.append(r[0]); maxes.append(r[1]); counts.append(r[2] or 1)
            cur.execute(f"SELECT value FROM {constants.data.HARDWARE_STATS_TABLE_RAW} "
                        f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?", (stat_type, st, et))
            raw_vals = [r[0] for r in cur.fetchall() if r[0] is not None]
            if not avgs:   # the whole window still fits the raw tier (e.g. a <24h-old install) -> exact
                return S.summarize_raw(raw_vals, S.coverage_pct(len(raw_vals), win, poll_interval))
            avgs += raw_vals; maxes += raw_vals; counts += [1] * len(raw_vals)   # raw samples = count-1 buckets
            tier = "minute" if win <= 30 * 86400 else "hour"
            return S.summarize_rollup(avgs, maxes, counts, tier,
                                      S.coverage_pct(sum(counts), win, poll_interval))
        except Exception as e:
            self._log_read_failure("summarize_hardware failed", e)
            return S.summarize_raw([])
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)


# =====================================================================================================
# v8 backend (step 3) - the live backend since the switch-over (step 7, _DB_VERSION = 8).
# =====================================================================================================

# The legacy volume estimate for a rollup row with no stored bytes (every row migrated from v7):
#
#     bytes ~= avg x MIN(sample_count, bucket_seconds) x LEGACY_SAMPLE_SECONDS
#
# Why: a v7 rollup's avg is the mean over the raw rows it absorbed (the hour tier's avg is
# sample-weighted from the minutes), so avg x sample_count is exactly SUM(the stored per-sample rates).
# Each v7 raw row was one sample, and v7's write gate only stored a row when the adapter moved >= 1 B/s,
# so the seconds it skipped carried < 1 B each. At the 1 s capture the default (and fastest) preset
# uses, SUM(rates) x 1 s is therefore the bytes, to within < 1 B per skipped second. v7 instead bills
# avg x bucket_seconds, which also charges every skipped (idle) second at the active-seconds mean: the
# measured x1.53 over-count on the owner's hour tier (database-v8-final.md 5), worst row x2.7. At a
# slower capture (2/5/10 s presets) this estimate UNDER-states by that factor - the capture interval
# was never stored, and the live poll rate is not evidence for months-old rows - which is why these
# numbers are flagged ``reconstructed`` and labelled "recorded history" (D5), not passed off as exact.
# MIN(..., bucket) caps a garbled sample_count at a full bucket.
LEGACY_SAMPLE_SECONDS = 1


class V8HistoryReader(_ReaderBase):
    """The v8 schema (``core/db/schema_v8.py``): ``net_*`` / ``hw_*`` tiers keyed on ``interfaces`` /
    ``metrics`` dimension ids, bytes over a measured ``dt_ms`` in ``net_raw``, nullable bytes in the
    rollups, ``app_sessions`` for coverage.

    Same contract as ``V7HistoryReader`` for everything the schema change does not make more exact
    (``tests/unit/test_history_reader_v8_parity.py`` writes one history into both and diffs them):

    * Rates come from bytes: a raw row's B/s is ``bytes * 1000 / dt_ms``.
    * Interfaces are addressed by their display name (``interfaces.name``), the name v7 stored; a fact
      row whose dimension row is gone shows as ``interface <id>``, as in the compatibility views.
      ``alias_of`` is not followed (2.2: always NULL - no automatic adapter merge).
    * A hardware stat is the ``metrics`` row with that name and ``entity = ''``; per-entity rows (one per
      GPU, per adapter) never mix into the aggregate series.

    Where it differs, deliberately:

    * **Volume** is ``SUM(bytes)`` wherever bytes exist - exact, independent of the live poll rate - and
      the legacy estimate above for migrated rollup rows, flagged in ``volume().reconstructed``.
    * **Tier boundaries are watermarks** (``schema_v8.WATERMARK_KEYS``, one pair per family - network readers use
      the net pair, hardware readers the hw pair; the contract is written there):
      raw answers ``[W_r, inf)``, minute ``[W_m, W_r)``, hour ``(-inf, min(W_r, W_m))``. A missing or
      untrustworthy watermark falls back to the step-2 horizon rule for that boundary only.
    * ``coverage()`` reads ``app_sessions``.
    * No ``db_utils`` fallback in ``speed_history`` (it reads v7 table names, and only ever re-read a
      subset of what the union had already found empty).

    Every call is one statement, or one read transaction (``_snapshot``) when it needs several; the
    watermarks are scalar subqueries of the statements they bound, so a roll-up that commits mid-read
    (rows + watermark in one transaction, per the contract) can neither drop nor double-count."""

    _NET = (S8.NET_RAW, S8.NET_MINUTE, S8.NET_HOUR)
    _HW = (S8.HW_RAW, S8.HW_MINUTE, S8.HW_HOUR)
    # (raw, minute) watermark keys per tier family: they roll up independently (the migration folds
    # the network raw tier but leaves hardware raw rows in place).
    _WM_KEYS = {_NET: (S8.WATERMARK_RAW_KEY, S8.WATERMARK_MINUTE_KEY),
                _HW: (S8.HW_WATERMARK_RAW_KEY, S8.HW_WATERMARK_MINUTE_KEY)}
    _BUCKET_SECONDS = (None, 60, 3600)
    _RAW_SUMMARY_SECONDS = V7HistoryReader._RAW_SUMMARY_SECONDS

    _IFACE_NAME = "COALESCE(i.name, 'interface ' || n.iface_id)"
    _IFACE_JOIN = f"LEFT JOIN {S8.INTERFACES} i ON i.iface_id = n.iface_id"
    _RAW_RATE = "n.{d}_bytes * 1000.0 / NULLIF(n.dt_ms, 0)"
    _METRIC = (f"h.metric_id IN (SELECT metric_id FROM {S8.METRICS} WHERE name = ? AND entity = '')")

    @classmethod
    def _tier_bytes(cls, d: str, k: int) -> str:
        """Per-row bytes of a net rollup row (tier ``k``): the stored bytes, else the legacy estimate
        (module comment above LEGACY_SAMPLE_SECONDS; schema_v8.NET_TIER_UNITS)."""
        return (f"COALESCE(n.{d}_bytes, n.{d}_avg * MIN(n.sample_count, {cls._BUCKET_SECONDS[k]}) "
                f"* {LEGACY_SAMPLE_SECONDS})")

    @classmethod
    def _bucket_rate(cls, d: str, k: int) -> Tuple[str, str]:
        """(rate, weight) of one net rollup bucket across interfaces (``_bucket_rate_sql``): each row
        contributes avg x MIN(sample_count, bucket) - its rate over its (capped) measured seconds -
        NOT its stored bytes. writer_v8 rolls a FLAG_GAP raw row (an interval spanning standby) into
        its minute: the row keeps the whole gap's bytes, its avg = bytes / the gap's seconds, and its
        sample_count is capped at the bucket. Stored bytes / MAX(sample_count) would then bill hours
        of bytes to one minute (500 MB over a 2 h gap -> 4 MB/s); the avg-based figure keeps that row
        at its own average, as a one-row bucket reads. Stored bytes stay volume()'s (_tier_bytes)."""
        sc = f"MIN(n.sample_count, {cls._BUCKET_SECONDS[k]})"
        return _bucket_rate_sql(f"n.{d}_avg", f"n.{d}_avg * {sc}", "n.sample_count")

    # --- tier precedence by watermark --------------------------------------------------------------

    @staticmethod
    def _watermark(key: str, now_ts: int) -> Tuple[str, tuple]:
        """Scalar subquery: the watermark under ``key``, or NULL when absent or not trustworthy - not all
        digits, <= 0, or more than ``schema_v8.WATERMARK_MAX_LEAD_SECONDS`` in the future (a watermark from
        a clock that ran ahead would declare every live raw row 'already rolled up' and blank the graph
        until the clock caught up; the small lead is the migrator's end-of-current-minute stamp)."""
        return (f"(SELECT CAST(value AS INTEGER) FROM {S8.METADATA} WHERE key = ? AND value <> '' "
                f"AND value NOT GLOB '*[^0-9]*' AND CAST(value AS INTEGER) BETWEEN 1 AND ?)",
                (key, now_ts + S8.WATERMARK_MAX_LEAD_SECONDS))

    def _tier_bounds(self, tables: Tuple[str, str, str], alias: str) -> List[Tuple[str, tuple]]:
        """Per tier (finest first): `` AND ...`` confining ``alias.ts`` to the range that tier answers.

        Lower bound (raw, minute): the tier's own watermark - rows below it were rolled up already.
        Upper bound (minute, hour): every finer tier's ceiling, which is that tier's watermark, or - only
        when it has none - the step-2 rule: its earliest row inside its retention horizon."""
        now_ts = self._now_ts()
        horizons = self._horizons(now_ts)
        keys = self._WM_KEYS[tables]

        def ceiling(j: int) -> Tuple[str, tuple]:
            w_sql, w_p = self._watermark(keys[j], now_ts)
            return (f"COALESCE({w_sql}, (SELECT MIN(ts) FROM {tables[j]} WHERE ts >= ?), {self._NO_BOUND})",
                    w_p + (horizons[j],))

        out: List[Tuple[str, tuple]] = []
        for k in range(3):
            parts: List[str] = []
            params: List[Any] = []
            if k < 2:
                w_sql, w_p = self._watermark(keys[k], now_ts)
                parts.append(f"{alias}.ts >= COALESCE({w_sql}, -{self._NO_BOUND})")
                params += w_p
            if k > 0:
                cs = [ceiling(j) for j in range(k)]
                expr = cs[0][0] if k == 1 else f"MIN({', '.join(c[0] for c in cs)})"
                parts.append(f"{alias}.ts < {expr}")
                for c in cs:
                    params += c[1]
            out.append(("".join(f" AND {p}" for p in parts), tuple(params)))
        return out

    def _snapshot_call(self, fn, on_error):
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            return fn(conn.cursor())
        except Exception as e:
            self._log_read_failure("v8 history read failed", e)
            return on_error()
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)

    def _iface_filter(self, interface_name: Optional[str]) -> Tuple[str, tuple]:
        if not interface_name or str(interface_name).lower() == "all":
            return "", ()
        return f" AND {self._IFACE_NAME} = ?", (interface_name,)

    # --- speed --------------------------------------------------------------------------------------

    def speed_history(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None,
                      interface_name: Optional[str] = None, return_raw: bool = False,
                      resolution: Resolution = 'auto') -> List[SpeedPoint]:
        _now_ts = self._now_ts()
        _start_ts = int(start_time.timestamp()) if start_time else 0
        _end_ts = int(end_time.timestamp()) if end_time else _now_ts
        target_res = resolution
        is_all = not interface_name or str(interface_name).lower() == "all"
        iface_sql, iface_p = self._iface_filter(interface_name)
        bounds = self._tier_bounds(self._NET, "n")
        if target_res == 'auto':
            try:
                target_res = self._auto_resolution(start_time, end_time, interface_name)
            except sqlite3.Error as e:
                self._log_read_failure("Unified graph query failed", e)
                return []
        interval = {'raw': 1, 'minute': 60, 'hour': 3600, 'day': 86400}.get(target_res, 60)
        raw_up, raw_down = self._RAW_RATE.format(d="up"), self._RAW_RATE.format(d="down")

        def tier(k: int, up: str, down: str) -> Tuple[str, list]:
            return (f"SELECT {_local_bin_sql('n.ts', interval)} AS bin_ts, "
                    f"{self._IFACE_NAME} AS interface_name, {up} AS up, {down} AS down "
                    f"FROM {self._NET[k]} n {self._IFACE_JOIN} "
                    f"WHERE n.ts BETWEEN ? AND ?{bounds[k][0]}{iface_sql}",
                    [_start_ts, _end_ts, *bounds[k][1], *iface_p])

        if target_res in ('minute', 'hour', 'day'):
            # Raw keeps exact per-second peaks; the rollups their preserved per-bucket maxima.
            parts = [tier(0, raw_up, raw_down), tier(1, "n.up_max", "n.down_max")]
            if target_res in ('hour', 'day'):
                parts.append(tier(2, "n.up_max", "n.down_max"))
            agg = "MAX"
        else:
            parts = [tier(0, raw_up, raw_down)]
            agg = "AVG"
        union = " UNION ALL ".join(q for q, _ in parts)
        params = [p for _, ps in parts for p in ps]
        inner = (f"SELECT bin_ts, interface_name, {agg}(up) AS up, {agg}(down) AS down "
                 f"FROM ({union}) GROUP BY bin_ts, interface_name")
        outer_agg = "SUM" if is_all else "AVG"
        query = (f"SELECT bin_ts, COALESCE({outer_agg}(up), 0), COALESCE({outer_agg}(down), 0) "
                 f"FROM ({inner}) GROUP BY bin_ts ORDER BY bin_ts")
        try:
            rows = self._conn().execute(query, params).fetchall()
        except sqlite3.Error as e:
            self._log_read_failure("Unified graph query failed", e)
            return []
        self.logger.debug("History query: target_res=%s fetched_rows=%d", target_res, len(rows))

        valid = [(_bin_start(r[0], interval), r[1], r[2]) for r in rows if r and r[0] is not None]
        if len(valid) != len(rows):
            self.logger.warning("Dropping %d invalid graph rows with NULL timestamp (resolution=%s).",
                                len(rows) - len(valid), target_res)
        if return_raw:
            points: List[SpeedPoint] = [(int(r[0]), float(r[1] or 0.0), float(r[2] or 0.0)) for r in valid]
        else:
            points = [(datetime.fromtimestamp(int(r[0])), float(r[1] or 0.0), float(r[2] or 0.0)) for r in valid]

        # No edge padding (rc.2 S4): stored rows only. The zero rows this used to add at the window
        # edges (and the flat zero baseline it made for an empty window) drew time nobody measured as
        # a measured zero; the renderer now decides what a silence means from measured_spans().
        return points

    def _auto_resolution(self, start_time: Optional[datetime], end_time: Optional[datetime],
                         interface_name: Optional[str]) -> Resolution:
        """The resolution ``speed_history(resolution='auto')`` reads at. Raises sqlite3.Error."""
        target_res = constants.data.history_period.get_target_resolution(start_time, end_time)
        # A short window that reaches below the raw watermark (the hours just before the v7 -> v8
        # upgrade, folded into minute rows) is drawn at minute resolution: raw resolution reads only
        # the raw tier and drew those hours empty, while the window's summary (which reads every tier)
        # showed their traffic. Steady state never takes this branch (raw keeps >= 24 h).
        if target_res == 'raw':
            _start_ts = int(start_time.timestamp()) if start_time else 0
            _end_ts = int(end_time.timestamp()) if end_time else self._now_ts()
            iface_sql, iface_p = self._iface_filter(interface_name)
            bounds = self._tier_bounds(self._NET, "n")
            if self._conn().execute(
                    f"SELECT 1 FROM {self._NET[1]} n {self._IFACE_JOIN} "
                    f"WHERE n.ts BETWEEN ? AND ?{bounds[1][0]}{iface_sql} LIMIT 1",
                    (_start_ts, _end_ts, *bounds[1][1], *iface_p)).fetchone():
                target_res = 'minute'
        return target_res

    def speed_resolution(self, start_time: Optional[datetime], end_time: Optional[datetime],
                         interface_name: Optional[str]) -> Optional[Resolution]:
        try:
            return self._auto_resolution(start_time, end_time, interface_name)
        except sqlite3.Error as e:
            self._log_read_failure("Graph resolution query failed", e)
            return None

    def measured_spans(self, start_time: datetime,
                       end_time: datetime) -> Optional[Tuple[Tuple[float, float], ...]]:
        """The app's running sessions in the window (``coverage()``) minus what it recorded as NOT
        measured while a session was open: an interval flagged ``FLAG_GAP`` (standby, a stall - the
        poll that ends it spans far more than the poll rate). On the raw tier that stretch is exact,
        ``[ts - dt_ms, ts]``. A roll-up row keeps only the flag, so there the stretch runs from the end
        of the previous stored bucket to the flagged bucket: the PC slept somewhere in between, and
        when exactly is gone. None when the schema records no sessions or the read fails."""
        cov = self.coverage(start_time, end_time)
        if cov is None:
            return None
        if not cov.sessions:
            return ()
        st, et = cov.start_ts, cov.end_ts
        minute_s, hour_s = self._BUCKET_SECONDS[1], self._BUCKET_SECONDS[2]

        def run(cur):
            # Each tier only within the range it answers (the watermark contract, as speed_history):
            # a row a roll-up already moved on, or a stray below the watermark, is not read twice.
            bounds = self._tier_bounds(self._NET, "n")
            holes: List[Tuple[float, float]] = []
            # A wake row up to an hour past the window can still end a sleep that began inside it.
            cur.execute(f"SELECT n.ts, MAX(n.dt_ms) FROM {S8.NET_RAW} n WHERE n.ts >= ? AND n.ts <= ? "
                        f"AND (n.flags & {S8.FLAG_GAP}) != 0{bounds[0][0]} GROUP BY n.ts",
                        (st, et + hour_s, *bounds[0][1]))
            for ts, dt_ms in cur.fetchall():
                holes.append((int(ts) - int(dt_ms or 0) / 1000.0, float(ts)))
            for k in (1, 2):
                bucket = self._BUCKET_SECONDS[k]
                cur.execute(f"SELECT DISTINCT n.ts FROM {self._NET[k]} n WHERE n.ts >= ? AND n.ts <= ? "
                            f"AND (n.flags & {S8.FLAG_GAP}) != 0{bounds[k][0]}",
                            (st - bucket, et + hour_s, *bounds[k][1]))
                for (ts,) in cur.fetchall():
                    prev = cur.execute(
                        f"SELECT MAX(e) FROM (SELECT MAX(n.ts) + {minute_s} AS e FROM {S8.NET_MINUTE} n "
                        f"WHERE n.ts < ?{bounds[1][0]} UNION ALL SELECT MAX(n.ts) + {hour_s} "
                        f"FROM {S8.NET_HOUR} n WHERE n.ts < ?{bounds[2][0]})",
                        (ts, *bounds[1][1], ts, *bounds[2][1])).fetchone()[0]
                    begin = min(float(prev), float(ts)) if prev is not None else float(st)
                    holes.append((begin, float(ts)))
            return holes

        holes = self._snapshot_call(run, lambda: None)
        if holes is None:
            return None
        return subtract_spans(cov.sessions, holes)

    def summarize_network(self, direction: str, start_time: datetime, end_time: datetime,
                          interface_name: Optional[str] = None, poll_interval: float = 1.0):
        from netspeedtray.utils import summaries as S
        d = "down" if direction == "download" else "up"
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        iface_sql, iface_p = self._iface_filter(interface_name)
        bounds = self._tier_bounds(self._NET, "n")

        def run(cur):
            def raw_rows():
                # (rate, seconds) per raw timestamp: the rate summed across interfaces, so an "all"
                # summary matches the widget aggregate; the seconds the row stands for are its MEASURED
                # interval (dt_ms - the longest of the interfaces' at that instant, which share one poll),
                # clipped to the window (a row is the END of its interval: [ts - dt, ts]).
                # FLAG_GAP rows (an interval spanning standby / a stall, far past the poll rate) are not
                # samples: their bytes are real and count in volume(), but their bytes/dt is an average
                # over hours, and their dt would claim coverage for time nobody measured - excluded here.
                cur.execute(f"SELECT n.ts, SUM({self._RAW_RATE.format(d=d)}), MAX(n.dt_ms) / 1000.0 "
                            f"FROM {S8.NET_RAW} n {self._IFACE_JOIN} "
                            f"WHERE n.ts BETWEEN ? AND ? AND (n.flags & {S8.FLAG_GAP}) = 0"
                            f"{bounds[0][0]}{iface_sql} GROUP BY n.ts",
                            (st, et) + bounds[0][1] + iface_p)
                return [(r[1], max(0.0, min(float(r[2] or 0.0), float(r[0] - st))))
                        for r in cur.fetchall() if r[1] is not None]

            def pct(seconds: float) -> float:
                return min(100.0, seconds / win * 100.0) if win > 0 else 0.0

            # No "<= 24 h reads raw only" shortcut (v7 has one): on v8 the tier answering a range is set
            # by the WATERMARKS, not by the window's length. Right after the v7 -> v8 upgrade the last
            # 24 h are folded minute rows below the raw watermark, so a raw-only 12 h summary read only
            # the seconds recorded since the upgrade (2.2.0-beta.1 smoke test: peak 2.3 Mbps beside an
            # 87 Mbps graph spike, 2% coverage). Every window reads every tier through its bounds; when
            # no rollup row falls inside it - the steady state for <= 24 h, the raw watermark trailing
            # now by >= 24 h - the result is the exact raw summary, as before.
            avgs, maxes, counts = [], [], []
            covered_seconds = 0.0   # distinct time buckets x their duration (see V7HistoryReader)
            for k in (1, 2):
                rate, weight = self._bucket_rate(d, k)
                cur.execute(f"SELECT {rate}, MAX(n.{d}_max), {weight} "
                            f"FROM {self._NET[k]} n {self._IFACE_JOIN} "
                            f"WHERE n.ts BETWEEN ? AND ?{bounds[k][0]}{iface_sql} GROUP BY n.ts",
                            (st, et) + bounds[k][1] + iface_p)
                for r in cur.fetchall():
                    if r[0] is None:
                        continue
                    avgs.append(r[0]); maxes.append(r[1]); counts.append(r[2] or 1)
                    covered_seconds += float(self._BUCKET_SECONDS[k])
            rr = raw_rows()
            if not avgs:
                # Coverage from the measured intervals, not row count x the live poll rate (a 2/5/10 s
                # preset row stands for 2/5/10 s; the poll rate in force today is not evidence).
                return S.summarize_raw([v for v, _ in rr], pct(sum(s for _, s in rr)))
            # Step-7 note 8: one unit for every weight. A rollup row weighs its sample_count, which on
            # the net tiers is SECONDS (schema_v8.NET_TIER_UNITS); a raw row therefore weighs the
            # seconds it measured (dt_ms / 1000), not 1 - at a 10 s poll, 1 would under-weight the most
            # recent day tenfold against the minutes it is averaged with.
            avgs += [v for v, _ in rr]; maxes += [v for v, _ in rr]; counts += [s for _, s in rr]
            covered_seconds += sum(s for _, s in rr)
            coverage = pct(covered_seconds)
            return S.summarize_rollup(avgs, maxes, counts, "minute" if win <= 30 * 86400 else "hour", coverage)

        return self._snapshot_call(run, lambda: S.summarize_raw([]))

    def network_hourly_profile(self, direction: str, start_time: datetime, end_time: datetime,
                               interface_name: Optional[str] = None) -> Dict[int, float]:
        """Exactly summarize_network's samples and weights (raw: measured seconds clipped to the window,
        gap rows excluded; rollups: per-bucket bytes / seconds weighted by those seconds - _bucket_rate,
        sample_count being SECONDS on the net tiers), grouped by local clock-hour - so the hours
        average back to the summary's avg."""
        d = "down" if direction == "download" else "up"
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        iface_sql, iface_p = self._iface_filter(interface_name)
        bounds = self._tier_bounds(self._NET, "n")
        parts = [f"SELECT n.ts AS ts, SUM({self._RAW_RATE.format(d=d)}) AS v, "
                 f"MAX(0.0, MIN(MAX(n.dt_ms) / 1000.0, n.ts - ?)) AS w "
                 f"FROM {S8.NET_RAW} n {self._IFACE_JOIN} "
                 f"WHERE n.ts BETWEEN ? AND ? AND (n.flags & {S8.FLAG_GAP}) = 0"
                 f"{bounds[0][0]}{iface_sql} GROUP BY n.ts"]
        params: List[Any] = [st, st, et, *bounds[0][1], *iface_p]
        for k in (1, 2):
            rate, weight = self._bucket_rate(d, k)
            parts.append(f"SELECT n.ts AS ts, {rate} AS v, COALESCE({weight}, 1) AS w "
                         f"FROM {self._NET[k]} n {self._IFACE_JOIN} "
                         f"WHERE n.ts BETWEEN ? AND ?{bounds[k][0]}{iface_sql} GROUP BY n.ts")
            params += [st, et, *bounds[k][1], *iface_p]
        query = _HOURLY_PROFILE_SQL.format(union=" UNION ALL ".join(parts))
        return self._snapshot_call(lambda cur: _hourly_rows(cur.execute(query, params).fetchall()), dict)

    def volume(self, start_time: Optional[datetime], end_time: datetime,
               interface_name: Optional[str], poll_interval: float) -> VolumeResult:
        """``poll_interval`` is accepted for the interface and ignored: every v8 raw row carries the
        interval it was measured over (``dt_ms``), so the live poll rate has no say over stored bytes."""
        iface_sql, iface_p = self._iface_filter(interface_name)
        return self._volume(start_time, end_time, iface_sql, iface_p)

    def _volume(self, start_time: Optional[datetime], end_time: datetime,
                iface_sql: str, iface_p: tuple) -> VolumeResult:
        return self._snapshot_call(
            lambda cur: self._volume_run(cur, start_time, end_time, iface_sql, iface_p),
            lambda: VolumeResult(0.0, 0.0, 0.0, 0.0, False))

    def _volume_run(self, cur, start_time: Optional[datetime], end_time: datetime,
                    iface_sql: str, iface_p: tuple) -> VolumeResult:
        st = int(start_time.timestamp()) if start_time else 0
        et = int(end_time.timestamp())
        bounds = self._tier_bounds(self._NET, "n")

        def run(cur):
            tot, exact, recon = [0.0, 0.0], [0.0, 0.0], 0
            for k, table in enumerate(self._NET):
                if k == 0:
                    cols = ("SUM(n.up_bytes), SUM(n.down_bytes), SUM(n.up_bytes), SUM(n.down_bytes), 0")
                else:
                    cols = (f"SUM({self._tier_bytes('up', k)}), SUM({self._tier_bytes('down', k)}), "
                            "SUM(n.up_bytes), SUM(n.down_bytes), "
                            "SUM(n.up_bytes IS NULL OR n.down_bytes IS NULL)")
                cur.execute(f"SELECT {cols} FROM {table} n {self._IFACE_JOIN} "
                            f"WHERE n.ts BETWEEN ? AND ?{bounds[k][0]}{iface_sql}",
                            (st, et) + bounds[k][1] + iface_p)
                r = cur.fetchone()
                if r:
                    for i in range(2):
                        tot[i] += float(r[i] or 0.0)
                        exact[i] += float(r[2 + i] or 0.0)
                    recon += int(r[4] or 0)
            return VolumeResult(tot[0], tot[1], exact[0], exact[1], recon > 0)

        return run(cur)

    def metered_volume(self, start_time: Optional[datetime], end_time: datetime,
                       scope: Optional[MeteredScope], poll_interval: float) -> VolumeResult:
        if scope is not None and scope.names is not None and not scope.names:
            return VolumeResult(0.0, 0.0, 0.0, 0.0, False)   # nothing selected / no eligible adapter
        return self._volume(start_time, end_time, *self._scope_filter(scope))

    def metered_volume_strict(self, start_time: Optional[datetime], end_time: datetime,
                              scope: Optional[MeteredScope]) -> Optional[VolumeResult]:
        if scope is not None and scope.names is not None and not scope.names:
            return VolumeResult(0.0, 0.0, 0.0, 0.0, False)
        sql, params = self._scope_filter(scope)
        conn, began = None, False
        try:
            conn = self._conn()
            began = self._begin_snapshot(conn)
            return self._volume_run(conn.cursor(), start_time, end_time, sql, params)
        except Exception as e:
            self._log_read_failure("v8 history read failed", e)
            return None
        finally:
            if conn is not None:
                self._end_snapshot(conn, began)

    def interface_names(self) -> Optional[List[str]]:
        try:
            return [r[0] for r in self._conn().execute(f"SELECT name FROM {S8.INTERFACES}").fetchall()]
        except sqlite3.Error as e:
            self._log_read_failure("Error reading interface names", e)
            return None

    def clock_trusted_ts(self) -> Optional[int]:
        row = self._conn().execute(f"SELECT value FROM {S8.METADATA} WHERE key = 'clock_trusted_ts'").fetchone()
        return int(row[0]) if row and str(row[0]).isdigit() else None

    def _scope_filter(self, scope: Optional[MeteredScope]) -> Tuple[str, tuple]:
        if scope is None:
            return "", ()
        if scope.names is not None:
            names = tuple(str(n) for n in scope.names)
            return f" AND {self._IFACE_NAME} IN ({', '.join('?' * len(names))})", names
        return (f" AND (COALESCE(i.is_physical, 1) = 1 OR NOT EXISTS "
                f"(SELECT 1 FROM {S8.INTERFACES} WHERE is_physical = 1))", ())

    def total_bandwidth(self, start_time: Optional[datetime], end_time: datetime,
                        interface_name: Optional[str], poll_interval: float) -> Tuple[float, float]:
        v = self.volume(start_time, end_time, interface_name, poll_interval)
        return v.up_bytes, v.down_bytes

    def coverage(self, start_time: datetime, end_time: datetime) -> Optional[Coverage]:
        now_ts = self._now_ts()
        st = int(start_time.timestamp())
        et = max(st, min(int(end_time.timestamp()), now_ts))

        def run(cur):
            first = cur.execute(f"SELECT MIN(started_ts) FROM {S8.APP_SESSIONS}").fetchone()[0]
            # A session runs [started_ts, ended_ts], or to its last heartbeat when it never ended cleanly
            # (crash, taskkill). An end before the start (a clock step) is a zero-length session.
            cur.execute(f"SELECT started_ts, MAX(started_ts, COALESCE(ended_ts, last_seen_ts)) AS e "
                        f"FROM {S8.APP_SESSIONS} WHERE started_ts < ? AND e > ? ORDER BY started_ts", (et, st))
            merged: List[List[int]] = []
            for s, e in cur.fetchall():
                s, e = max(int(s), st), min(int(e), et)
                if e <= s:
                    continue
                if merged and s <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            known_from = et if first is None else min(et, max(st, int(first)))
            running = sum(e - s for s, e in merged)
            return Coverage(st, et, running, (et - known_from) - running, known_from - st,
                            tuple((s, e) for s, e in merged))

        return self._snapshot_call(run, lambda: None)

    def distinct_interfaces(self) -> List[str]:
        try:
            rows = self._conn().execute(
                f"SELECT DISTINCT {self._IFACE_NAME} AS interface_name FROM ("
                f"SELECT iface_id FROM {S8.NET_RAW} UNION SELECT iface_id FROM {S8.NET_MINUTE} "
                f"UNION SELECT iface_id FROM {S8.NET_HOUR}) n {self._IFACE_JOIN} "
                f"ORDER BY interface_name").fetchall()
            return [r[0] for r in rows]
        except sqlite3.Error as e:
            self._log_read_failure("Error fetching distinct interfaces", e)
            return []

    def earliest_timestamp(self) -> Optional[datetime]:
        try:
            row = self._conn().execute(
                f"SELECT MIN(m) FROM (SELECT MIN(ts) AS m FROM {S8.NET_RAW} UNION ALL "
                f"SELECT MIN(ts) FROM {S8.NET_MINUTE} UNION ALL SELECT MIN(ts) FROM {S8.NET_HOUR}) "
                f"WHERE m IS NOT NULL").fetchone()
            if row and row[0] is not None:
                return datetime.fromtimestamp(int(row[0]))
        except sqlite3.Error as e:
            self._log_read_failure("Failed to retrieve the earliest timestamp from database", e)
        return None

    # --- hardware -----------------------------------------------------------------------------------

    def hardware_history(self, stat_type: str, start_time: Optional[datetime] = None,
                         end_time: Optional[datetime] = None) -> List[Tuple[datetime, float]]:
        _end = end_time or self._now()
        _start = start_time or (_end - timedelta(hours=24))
        start_ts, end_ts = int(_start.timestamp()), int(_end.timestamp())
        duration = end_ts - start_ts
        interval = 1 if duration <= 6 * 3600 else (60 if duration <= 30 * 86400 else 3600)
        bounds = self._tier_bounds(self._HW, "h")
        parts, params = [], []
        for k, col in ((0, "h.value"), (1, "h.avg_value"), (2, "h.avg_value")):
            parts.append(f"SELECT {_local_bin_sql('h.ts', interval)} AS b, {col} AS v "
                         f"FROM {self._HW[k]} h WHERE {self._METRIC} AND h.ts BETWEEN ? AND ?{bounds[k][0]}")
            params += [stat_type, start_ts, end_ts, *bounds[k][1]]
        try:
            rows = self._conn().execute(
                f"SELECT b, AVG(v) FROM ({' UNION ALL '.join(parts)}) GROUP BY b ORDER BY b ASC", params).fetchall()
            return [(datetime.fromtimestamp(_bin_start(r[0], interval)), r[1]) for r in rows]
        except Exception as e:
            self._log_read_failure("Error fetching hardware history", e)
            return []

    def summarize_hardware(self, stat_type: str, start_time: datetime, end_time: datetime,
                           poll_interval: float = 1.0):
        from netspeedtray.utils import summaries as S
        win = max(0.0, (end_time - start_time).total_seconds())
        st, et = int(start_time.timestamp()), int(end_time.timestamp())
        bounds = self._tier_bounds(self._HW, "h")

        def run(cur):
            def raw_vals():
                cur.execute(f"SELECT h.value FROM {S8.HW_RAW} h WHERE {self._METRIC} "
                            f"AND h.ts BETWEEN ? AND ?{bounds[0][0]}", (stat_type, st, et) + bounds[0][1])
                return [r[0] for r in cur.fetchall() if r[0] is not None]

            # Watermark-bounded tiers for every window length, as summarize_network (no raw-only
            # shortcut): exact from raw whenever no rollup row falls inside the window.
            avgs, maxes, counts = [], [], []
            for k in (1, 2):
                cur.execute(f"SELECT h.avg_value, h.max_value, h.sample_count FROM {self._HW[k]} h "
                            f"WHERE {self._METRIC} AND h.ts BETWEEN ? AND ?{bounds[k][0]}",
                            (stat_type, st, et) + bounds[k][1])
                for r in cur.fetchall():
                    if r[0] is None:
                        continue
                    avgs.append(r[0]); maxes.append(r[1]); counts.append(r[2] or 1)
            rv = raw_vals()
            if not avgs:
                return S.summarize_raw(rv, S.coverage_pct(len(rv), win, poll_interval))
            avgs += rv; maxes += rv; counts += [1] * len(rv)
            return S.summarize_rollup(avgs, maxes, counts, "minute" if win <= 30 * 86400 else "hour",
                                      S.coverage_pct(sum(counts), win, poll_interval))

        return self._snapshot_call(run, lambda: S.summarize_raw([]))
