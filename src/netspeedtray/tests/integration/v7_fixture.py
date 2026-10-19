"""
A populated v7 database with a HOT WAL, and the manifest of what it holds - for the v8 migration
kill test (``test_migration_kill.py``) and the migrator's unit tests.

``child_main`` runs in its own process: it builds the file with the app's own ``DatabaseWorker``
(so the schema, PRAGMAs and indexes are exactly what 2.1.x creates), commits every manifest row
with ``wal_autocheckpoint=0`` so all of it sits in ``-wal``, opens one more transaction that is
never committed, and then ``TerminateProcess``es itself - the state ``taskkill /F`` (setup.iss)
leaves behind during an upgrade.

The manifest is pure data, computed the same way in every process. ``expected_v8_view_rows``
says what a migrated file must show through the v7-named compatibility views.
"""
import ctypes
import os
import sqlite3
import sys
from typing import Dict, List, Tuple

from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri

# Fixed epoch (2026-08-25 00:00 UTC-ish), minute and hour aligned.
T0 = 1_787_616_000
IFACES = ("Wi-Fi 3", "Tailscale", "Ethernet")
STATS = ("cpu", "gpu", "ram", "latency_gw", "latency_gw_timeout", "latency_anchor_timeout")
# The fractional loss shapes Gate 5 requires to survive untouched: 0/1 per probe (old) and a
# fraction per burst (new), including a non-dyadic third.
LOSS_VALUES = (0.0, 1.0, 0.25, 0.75, 1.0 / 3.0, 0.5)

RAW_SECONDS = 4000                 # per-second rows per adapter (2 adapters)
RAW_START = T0 + 30 * 86400        # the newest day
MINUTE_ROWS = 2500                 # per adapter (3 adapters)
HOUR_ROWS = 600                    # per adapter (2 adapters); ends before the minute tier
HW_RAW_SECONDS = 1500
HW_MIN_ROWS = 800
HW_HOUR_ROWS = 200

SPEED_RAW = "speed_history_raw"
SPEED_MIN = "speed_history_minute"
SPEED_HOUR = "speed_history_hour"
HW_RAW = "hardware_stats_raw"
HW_MIN = "hardware_stats_minute"
HW_HOUR = "hardware_stats_hour"


def manifest() -> Dict[str, List[Tuple]]:
    """Every committed row of the fixture, per v7 table, in the table's column order, sorted."""
    m: Dict[str, List[Tuple]] = {}
    m[SPEED_RAW] = sorted(
        (RAW_START + s, name, float((s * 37 + k * 11) % 90_000), float((s * 53 + k) % 900_000))
        for k, name in enumerate(IFACES[:2]) for s in range(RAW_SECONDS))
    # Minute tier: ends where the raw tier starts, EXCEPT Wi-Fi 3, whose last 5 minute rows sit
    # inside the raw tier's first minutes - the overlap the fold must merge, not duplicate.
    rows = []
    for k, name in enumerate(IFACES):
        base = RAW_START - MINUTE_ROWS * 60 + (300 if k == 0 else 0)
        for j in range(MINUTE_ROWS):
            avg_u, avg_d = 0.25 * j + k, 0.5 * j + 0.125
            rows.append((base + 60 * j, name, avg_u, avg_d, avg_u * 3.0, avg_d * 2.0, 1 + j % 60))
    m[SPEED_MIN] = sorted(rows)
    m[SPEED_HOUR] = sorted(
        (T0 + 3600 * j, name, 10.5 + j, 20.25 + j * k, 99.0 + j, 199.0 + j, 3600 - (j % 7))
        for k, name in enumerate(IFACES[:2]) for j in range(HOUR_ROWS))

    def hv(stat: str, i: int) -> float:
        if stat.endswith("_timeout"):
            return LOSS_VALUES[i % len(LOSS_VALUES)]
        return round(5.0 + (i * 7 % 900) / 10.0, 1) + 0.03125
    m[HW_RAW] = sorted((RAW_START + i, st, hv(st, i)) for st in STATS for i in range(HW_RAW_SECONDS))
    m[HW_MIN] = sorted((RAW_START - 60 * (HW_MIN_ROWS - i), st, hv(st, i), hv(st, i + 3), 60)
                       for st in STATS[:4] + STATS[4:5] for i in range(HW_MIN_ROWS))
    m[HW_HOUR] = sorted((T0 + 3600 * i, st, hv(st, i), hv(st, i + 1), 3600 - i)
                        for st in ("cpu", "latency_gw_timeout", "latency_anchor_timeout", "ram")
                        for i in range(HW_HOUR_ROWS))
    m["usage_counter"] = [(1, 123456789.5, 987654321.25, 1000.0, 2000.0, "2026-09-01", RAW_START)]
    m["bandwidth_history"] = [("Wi-Fi 3", 1.0, 2.0)]     # never written by the app; must not migrate
    m["metadata"] = sorted([
        ("db_version", "7"),
        ("created_at", str(T0)),
        ("current_retention_days", "36500"),
        ("last_maintenance_at", str(RAW_START)),
        ("usage_counter_snapshot", '{"counters": {"Wi-Fi 3": [123, 456]}, "ts": 1789000000.5}'),
        ("pending_history_compaction", "1"),
        ("note_unicode", "café — 日本"),
    ])
    return m


def expected_v8_view_rows(m: Dict[str, List[Tuple]] = None, watermarks: dict = None) -> Dict[str, List[Tuple]]:
    """What the compatibility views of a correctly migrated file return (v7 shapes). Defaults to
    this fixture; pass another manifest (``manifest_from_db``) and its watermarks, or None to leave
    the watermark rows out of the expected metadata."""
    if m is None:
        m = manifest()
        watermarks = expected_watermarks()
    out = {t: list(m[t]) for t in (SPEED_HOUR, HW_RAW, HW_MIN, HW_HOUR, "usage_counter")}
    out[SPEED_RAW] = []
    buckets: Dict[Tuple[int, str], List[Tuple]] = {}
    for ts, name, up, down in m[SPEED_RAW]:
        buckets.setdefault((ts // 60 * 60, name), []).append((up, down))
    minute: Dict[Tuple[int, str], Tuple] = {(r[0], r[1]): r for r in m[SPEED_MIN]}
    for key, vals in buckets.items():
        n = len(vals)
        ru = sum(v[0] for v in vals) / n
        rd = sum(v[1] for v in vals) / n
        mu, md = max(v[0] for v in vals), max(v[1] for v in vals)
        if key in minute:
            _, _, au, ad, xu, xd, c = minute[key]
            minute[key] = (key[0], key[1], (au * c + ru * n) / (c + n), (ad * c + rd * n) / (c + n),
                           max(xu, mu), max(xd, md), c + n)
        else:
            minute[key] = (key[0], key[1], ru, rd, mu, md, n)
    out[SPEED_MIN] = sorted(minute.values())
    out["metadata"] = sorted([(k, "8" if k == "db_version" else v) for k, v in m["metadata"]]
                             + [(k, str(v)) for k, v in (watermarks or {}).items()])
    return out


def expected_watermarks() -> dict:
    """Written out from the fixture's layout, not from the migrator's rule: the hour tiers end
    where the minute watermark sits; the network raw watermark is the end of the last folded raw
    minute; the hardware raw watermark is the end of the last hardware minute row (= RAW_START)."""
    m = manifest()
    net_hour_end = max(r[0] for r in m[SPEED_HOUR]) + 3600
    last_raw_minute = max(r[0] for r in m[SPEED_RAW]) // 60 * 60
    hw_hour_end = max(r[0] for r in m[HW_HOUR]) + 3600
    hw_minute_end = max(r[0] for r in m[HW_MIN]) + 60
    assert hw_minute_end == RAW_START == min(r[0] for r in m[HW_RAW])
    return {"tier_watermark_raw": last_raw_minute + 60, "tier_watermark_minute": net_hour_end,
            "hw_tier_watermark_raw": hw_minute_end, "hw_tier_watermark_minute": hw_hour_end}


def folded_minute_keys(m: Dict[str, List[Tuple]] = None) -> set:
    """Every minute bucket the raw fold writes (new or merged)."""
    m = m or manifest()
    return {(ts // 60 * 60, name) for ts, name, _u, _d in m[SPEED_RAW]}


V7_TABLES = (SPEED_RAW, SPEED_MIN, SPEED_HOUR, HW_RAW, HW_MIN, HW_HOUR, "usage_counter",
             "bandwidth_history", "metadata")


def manifest_from_db(path: str) -> Dict[str, List[Tuple]]:
    """The same shape as manifest(), read (read-only) from a real v7 file - the owner-copy run."""
    c = sqlite3.connect(sqlite_ro_uri(path), uri=True)
    try:
        return {t: sorted(tuple(r) for r in c.execute(f"SELECT * FROM {t}")) for t in V7_TABLES}
    finally:
        c.close()


def merged_minute_keys(m: Dict[str, List[Tuple]] = None) -> set:
    m = m or manifest()
    raw = {(ts // 60 * 60, name) for ts, name, _u, _d in m[SPEED_RAW]}
    return raw & {(r[0], r[1]) for r in m[SPEED_MIN]}


def populate(conn: sqlite3.Connection) -> None:
    """Write the manifest into an open v7 connection (schema already created), committing."""
    m = manifest()
    conn.execute("DELETE FROM metadata")
    for table, rows in m.items():
        if not rows:
            continue
        ph = ",".join("?" * len(rows[0]))
        for i in range(0, len(rows), 5000):        # several commits -> several WAL transactions
            conn.executemany(f"INSERT INTO {table} VALUES ({ph})", rows[i:i + 5000])
            conn.commit()


def _terminate_self(code: int = 137) -> None:
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    k32.TerminateProcess(k32.GetCurrentProcess(), code)


def child_main(directory: str) -> None:
    """Build <directory>/speed_history.db with the app's worker, leave the WAL hot, die."""
    from pathlib import Path
    from netspeedtray.core.database import DatabaseWorker
    w = DatabaseWorker(Path(directory) / "speed_history.db")
    w._initialize_connection()
    w._check_and_create_schema()
    w._ensure_indexes()
    w.conn.execute("PRAGMA wal_autocheckpoint = 0")
    populate(w.conn)
    # A transaction in flight at the kill: none of it is in the manifest, none of it may appear.
    w.conn.execute("INSERT INTO speed_history_raw VALUES (1, 'uncommitted', 1.0, 1.0)")
    w.conn.execute("INSERT INTO metadata VALUES ('uncommitted', 'x')")
    sys.stdout.flush()
    _terminate_self(137)


def hot_copy_main(args: str) -> None:
    """``<src>|<dest_dir>``: copy a real v7 file INTO a WAL-mode destination with checkpoints off
    (the backup API writes through the pager, so every page lands in -wal), then die - a hot WAL
    holding real data, without writing a single row that was not in the source."""
    src, dest_dir = args.split("|")
    s = sqlite3.connect(sqlite_ro_uri(src), uri=True)
    d = sqlite3.connect(os.path.join(dest_dir, "speed_history.db"))
    d.execute("PRAGMA journal_mode = WAL")
    d.execute("PRAGMA wal_autocheckpoint = 0")
    s.backup(d)
    _terminate_self(137)


def make_hot_v8_wal_main(db: str) -> None:
    """Open a (migrated) file the way the app's worker does and die with frames in its -wal."""
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA wal_autocheckpoint = 0")
    for i in range(50):
        c.execute("BEGIN")
        c.execute("INSERT INTO app_sessions (started_ts, last_seen_ts, version, poll_ms) "
                  "VALUES (?, ?, 'hot-wal', 1000)", (T0 + i, T0 + i))
        c.execute("COMMIT")
    # Touch the history too, so a naive restore that replays this WAL visibly breaks v7 tables.
    c.execute("BEGIN")
    c.execute("DELETE FROM net_hour WHERE ts < ?", (T0 + 3600 * 10,))
    c.execute("COMMIT")
    _terminate_self(137)


if __name__ == "__main__":
    {"build": child_main, "hot_v8": make_hot_v8_wal_main}[sys.argv[1]](sys.argv[2])
