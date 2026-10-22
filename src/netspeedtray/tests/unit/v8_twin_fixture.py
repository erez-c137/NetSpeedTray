"""
One logical history, written twice: into a v7 file (through ``DatabaseWorker``'s schema) and into a v8
file (straight into the ``schema_v8`` tables, the way the migrator - step 4 - and the v8 writer - step 6 -
will lay it out). Shared by the v8 reader tests (``test_history_reader_v8_*.py``).

The v8 file holds the two kinds of rollup row the reader must tell apart:

* **migrated** rows - ``up_bytes`` / ``down_bytes`` / ``covered_ms`` NULL, rates exactly as v7 had them;
* **v8-native** rows - written after the switch-over, so they carry exact bytes. They were captured at
  2 s polling over a partly idle minute: 20 samples x 2 s = 40 s of a 60 s bucket. That is the case
  where all three volume formulas disagree, so each one is pinned:
  v7 ``avg x 60`` (over-counts the idle 20 s), the legacy estimate ``avg x sample_count x 1 s``
  (under-counts the 2 s samples), and the stored bytes (exact).

Raw rows are 1 s samples (dt_ms = 1000) on both sides, so a raw rate and raw bytes are the same number.
Watermarks sit in the gap between tiers, exactly where a move-only roll-up would put them.

**One clock.** Everything a test derives "now" from - the history, its windows, and the readers' own
horizon / watermark-lead rules - is ``fixed_now()``, read ONCE per process, and the readers are handed
it as a fixed clock. Two independent ``datetime.now()`` reads (module import vs fixture build) used
to decide, by the seconds that happened to pass between them mod the 15 s raw spacing, whether a
window start landed exactly on a raw row - a 1-in-15 flake (and a near-certain failure when the file
ran alone, where both reads share a second). Set ``NST_TEST_NOW`` (ISO-8601 local time, e.g.
``2026-10-20T00:00:30``) to run every v8 reader test at a chosen instant.
"""
import os
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from netspeedtray import constants
from netspeedtray.core import usage_snapshot
from netspeedtray.core.db import schema_v8 as S8
from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri

D = constants.data
DAY = 86400


def _read_test_now() -> datetime:
    env = os.environ.get("NST_TEST_NOW", "").strip()
    return datetime.fromisoformat(env) if env else datetime.now()


_TEST_NOW = _read_test_now()


def fixed_now() -> datetime:
    """The suite's single, fixed 'now' (see the module docstring)."""
    return _TEST_NOW


def clock_at(now: datetime):
    """A reader clock frozen at ``now`` (epoch seconds, as ``time.time``)."""
    ts = now.timestamp()
    return lambda: ts
NICS = ("Ethernet", "Wi-Fi 3")
NATIVE_SAMPLES, NATIVE_SAMPLE_SECONDS = 20, 2      # a v8-native minute: 20 samples x 2 s


@dataclass
class History:
    now_ts: int
    raw: List[Tuple[int, str, int, int]] = field(default_factory=list)            # ts, nic, up B/s, down B/s
    # ts, nic, up_avg, down_avg, up_max, down_max, count, up_bytes|None, down_bytes|None
    minute: List[tuple] = field(default_factory=list)
    hour: List[tuple] = field(default_factory=list)
    hw_raw: List[Tuple[int, str, float]] = field(default_factory=list)
    hw_minute: List[Tuple[int, str, float, float, int]] = field(default_factory=list)
    hw_hour: List[Tuple[int, str, float, float, int]] = field(default_factory=list)
    wm_raw: int = 0
    wm_minute: int = 0
    usage_row: Tuple = (1, 1.5e9, 7.5e9, 1.0e8, 5.0e8, "2026-10-01", 0)
    snapshot: Optional[str] = None


def make_history(now: datetime, seed: int = 3) -> History:
    """20 h of raw, 3 h of v8-native minute rows (25-28 h back), migrated minute rows 2-10 days back,
    migrated hour rows 35-40 days back. Speeds are integers so every expected total is exact."""
    rng = random.Random(seed)
    now_ts = int(now.timestamp())
    h = History(now_ts=now_ts)
    for nic_i, nic in enumerate(NICS):
        scale = 10 if nic_i == 0 else 1
        for t in range(now_ts - 20 * 3600, now_ts - 5, 15):
            h.raw.append((t, nic, rng.randint(1, 500) * scale, rng.randint(1, 5000) * scale))
        # v8-native minutes: bytes stored; avg = mean of the 20 sample rates; sample_count = the 40
        # measured SECONDS (schema_v8.NET_TIER_UNITS - what writer_v8 stores), so avg x count = bytes.
        m0 = (now_ts - 28 * 3600) // 60 * 60
        for t in range(m0, m0 + 3 * 3600, 60):
            ua, da = rng.randint(1, 400) * scale, rng.randint(1, 4000) * scale
            h.minute.append((t, nic, float(ua), float(da), float(ua * 3), float(da * 3),
                             NATIVE_SAMPLES * NATIVE_SAMPLE_SECONDS,
                             ua * NATIVE_SAMPLES * NATIVE_SAMPLE_SECONDS,
                             da * NATIVE_SAMPLES * NATIVE_SAMPLE_SECONDS))
        # migrated minutes: partly idle (count < 60), bytes unknown.
        m1 = (now_ts - 10 * DAY) // 60 * 60
        for t in range(m1, (now_ts - 2 * DAY) // 60 * 60, 180):
            ua, da, n = rng.randint(1, 300) * scale, rng.randint(1, 3000) * scale, rng.randint(1, 60)
            h.minute.append((t, nic, float(ua), float(da), float(ua * 2), float(da * 2), n, None, None))
        h0 = (now_ts - 40 * DAY) // 3600 * 3600
        for t in range(h0, (now_ts - 35 * DAY) // 3600 * 3600, 3600):
            ua, da, n = rng.randint(1, 200) * scale, rng.randint(1, 2000) * scale, rng.randint(100, 3600)
            h.hour.append((t, nic, float(ua), float(da), float(ua * 5), float(da * 5), n, None, None))
    for t in range(now_ts - 20 * 3600, now_ts - 5, 15):
        h.hw_raw.append((t, "cpu", float(rng.randint(0, 100))))
    for t in range((now_ts - 10 * DAY) // 60 * 60, (now_ts - 25 * 3600) // 60 * 60, 120):
        v = float(rng.randint(0, 100))
        h.hw_minute.append((t, "cpu", v, min(100.0, v + 7), rng.randint(1, 60)))
    for t in range((now_ts - 40 * DAY) // 3600 * 3600, (now_ts - 35 * DAY) // 3600 * 3600, 3600):
        v = float(rng.randint(0, 100))
        h.hw_hour.append((t, "cpu", v, min(100.0, v + 9), rng.randint(100, 3600)))
    h.wm_raw = (now_ts - 22 * 3600) // 60 * 60          # raw rows all >= it, minute rows all < it
    h.wm_minute = (now_ts - 20 * DAY) // 3600 * 3600    # minute rows all >= it, hour rows all < it
    h.usage_row = (1, 1.5e9, 7.5e9, 1.0e8, 5.0e8, "2026-10-01", now_ts)

    class _C:
        def __init__(self, s, r):
            self.bytes_sent, self.bytes_recv = s, r
    h.snapshot = usage_snapshot.encode_snapshot({"Ethernet": _C(123, 456)}, now_ts - 60, now_ts - 3600)
    return h


# --- writers ---------------------------------------------------------------------------------------

def write_v7(path: Path, h: History) -> None:
    from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
    w = LegacyV7Worker(path)
    w._initialize_connection()
    w._check_and_create_schema()
    c = w.conn.cursor()
    c.executemany(f"INSERT INTO {D.SPEED_TABLE_RAW} (timestamp, interface_name, upload_bytes_sec, "
                  "download_bytes_sec) VALUES (?, ?, ?, ?)", [(t, n, float(u), float(d)) for t, n, u, d in h.raw])
    for table, rows in ((D.SPEED_TABLE_MINUTE, h.minute), (D.SPEED_TABLE_HOUR, h.hour)):
        c.executemany(f"INSERT INTO {table} (timestamp, interface_name, upload_avg, download_avg, upload_max, "
                      "download_max, sample_count) VALUES (?, ?, ?, ?, ?, ?, ?)", [r[:7] for r in rows])
    c.executemany(f"INSERT INTO {D.HARDWARE_STATS_TABLE_RAW} (timestamp, stat_type, value) VALUES (?, ?, ?)",
                  h.hw_raw)
    for table, rows in ((D.HARDWARE_STATS_TABLE_MINUTE, h.hw_minute), (D.HARDWARE_STATS_TABLE_HOUR, h.hw_hour)):
        c.executemany(f"INSERT INTO {table} (timestamp, stat_type, avg_value, max_value, sample_count) "
                      "VALUES (?, ?, ?, ?, ?)", rows)
    c.execute(f"INSERT OR REPLACE INTO {D.USAGE_COUNTER_TABLE} (id, cumulative_up, cumulative_down, anchor_up, "
              "anchor_down, period_key, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?)", h.usage_row)
    if h.snapshot:
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                  (constants.network.usage.SNAPSHOT_METADATA_KEY, h.snapshot))
    w.conn.commit()
    w._close_connection()


class V8Writer:
    """Inserts into a fresh v8 file, assigning dimension ids on first sight (iface/metric ids are
    deliberately NOT 1..n in name order, so nothing can work by accident of id order)."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        S8.create_v8_schema(self.conn)
        self.conn.execute("PRAGMA journal_mode=WAL")   # after the schema, as create_v8_schema demands
        self._ifaces: Dict[str, int] = {}
        self._metrics: Dict[Tuple[str, str], int] = {}
        self.conn.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")

    def iface(self, name: str, ts: int = 0) -> int:
        if name not in self._ifaces:
            iid = 100 + 7 * len(self._ifaces)
            self.conn.execute(f"INSERT INTO {S8.INTERFACES} (iface_id, stable_key, name, is_physical, first_seen, "
                              "last_seen, alias_of) VALUES (?, ?, ?, 1, ?, ?, NULL)",
                              (iid, f"luid:{iid:x}", name, ts, ts))
            self._ifaces[name] = iid
        return self._ifaces[name]

    def metric(self, name: str, entity: str = "") -> int:
        key = (name, entity)
        if key not in self._metrics:
            mid = 50 + 3 * len(self._metrics)
            self.conn.execute(f"INSERT INTO {S8.METRICS} (metric_id, name, entity, unit) VALUES (?, ?, ?, '')",
                              (mid, name, entity))
            self._metrics[key] = mid
        return self._metrics[key]

    def net_raw(self, rows, dt_ms: int = 1000) -> None:
        self.conn.executemany(f"INSERT INTO {S8.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms) "
                              "VALUES (?, ?, ?, ?, ?)",
                              [(t, self.iface(n), int(u * dt_ms // 1000), int(d * dt_ms // 1000), dt_ms)
                               for t, n, u, d in rows])

    def net_tier(self, table: str, rows) -> None:
        self.conn.executemany(f"INSERT INTO {table} (ts, iface_id, up_avg, down_avg, up_max, down_max, "
                              "sample_count, up_bytes, down_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              [(r[0], self.iface(r[1]), *r[2:9]) for r in rows])

    def hw_raw(self, rows, entity: str = "") -> None:
        self.conn.executemany(f"INSERT INTO {S8.HW_RAW} (ts, metric_id, value) VALUES (?, ?, ?)",
                              [(t, self.metric(s, entity), v) for t, s, v in rows])

    def hw_tier(self, table: str, rows, entity: str = "") -> None:
        self.conn.executemany(f"INSERT INTO {table} (ts, metric_id, avg_value, max_value, sample_count) "
                              "VALUES (?, ?, ?, ?, ?)", [(t, self.metric(s, entity), a, m, n) for t, s, a, m, n in rows])

    _KEEP = object()

    def watermarks(self, raw: Optional[object], minute: Optional[object],
                   hw_raw: Optional[object] = _KEEP, hw_minute: Optional[object] = _KEEP) -> None:
        """Set the network pair, and the hardware pair (same values unless given)."""
        hw_raw = raw if hw_raw is self._KEEP else hw_raw
        hw_minute = minute if hw_minute is self._KEEP else hw_minute
        for key, val in ((S8.WATERMARK_RAW_KEY, raw), (S8.WATERMARK_MINUTE_KEY, minute),
                         (S8.HW_WATERMARK_RAW_KEY, hw_raw), (S8.HW_WATERMARK_MINUTE_KEY, hw_minute)):
            self.conn.execute("DELETE FROM metadata WHERE key = ?", (key,))
            if val is not None:
                self.conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (key, str(val)))

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def write_v8(path: Path, h: History, watermarks: bool = True) -> V8Writer:
    """Write ``h`` into a new v8 file. Returns the (still open) writer so a test can add strays; call
    ``close()``."""
    w = V8Writer(path)
    for nic in NICS:
        w.iface(nic, h.now_ts - 40 * DAY)
    w.net_raw(h.raw)
    w.net_tier(S8.NET_MINUTE, h.minute)
    w.net_tier(S8.NET_HOUR, h.hour)
    w.hw_raw(h.hw_raw)
    w.hw_tier(S8.HW_MINUTE, h.hw_minute)
    w.hw_tier(S8.HW_HOUR, h.hw_hour)
    # Per-entity rows of the same metric name: v8-only, and must never leak into the 'cpu' series.
    w.hw_raw([(t, s, 999.0) for t, s, _ in h.hw_raw[::5]], entity="core0")
    w.hw_tier(S8.HW_MINUTE, [(t, s, 999.0, 999.0, n) for t, s, _, _, n in h.hw_minute[::5]], entity="core0")
    w.conn.execute(f"INSERT INTO {D.USAGE_COUNTER_TABLE} (id, cumulative_up, cumulative_down, anchor_up, "
                   "anchor_down, period_key, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?)", h.usage_row)
    if h.snapshot:
        w.conn.execute("INSERT INTO metadata (key, value) VALUES (?, ?)",
                       (constants.network.usage.SNAPSHOT_METADATA_KEY, h.snapshot))
    if watermarks:
        w.watermarks(h.wm_raw, h.wm_minute)
    w.conn.commit()
    return w


def ro_conn(path: Path) -> sqlite3.Connection:
    """A read-only connection, like the app's read side (never writes)."""
    return sqlite3.connect(sqlite_ro_uri(path), uri=True, check_same_thread=False)


def readers(v7_path: Path, v8_path: Path, now: Optional[datetime] = None):
    """(v7 reader, v8 reader, [connections to close]), both on a clock frozen at ``now``
    (default ``fixed_now()``)."""
    from netspeedtray.core.db.history_reader import V7HistoryReader, V8HistoryReader
    c7, c8 = ro_conn(v7_path), ro_conn(v8_path)
    clock = clock_at(now or fixed_now())
    return (V7HistoryReader(lambda: c7, lambda: v7_path, clock=clock),
            V8HistoryReader(lambda: c8, lambda: v8_path, clock=clock), [c7, c8])
