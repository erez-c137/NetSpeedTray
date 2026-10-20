"""
The v8 database schema - the app's schema since the switch-over (step 7, ``_DB_VERSION = 8`` in
``core/database.py``): a fresh install is created by :func:`create_v8_schema`, a v7 file is rebuilt
into it by ``migrate_v8``.

Design authority: ``Docs/reference/database-v8-final.md``. In short:

* History is stored as **bytes over a measured interval** (``net_raw.dt_ms``), not as rates.
* Every history table is ``WITHOUT ROWID`` keyed on small integers; adapter names and metric names
  live once, in the ``interfaces`` / ``metrics`` dimension tables.
* Rollup tiers keep the v7 rate columns (``*_avg`` / ``*_max`` / ``sample_count``) so migrated
  history carries over untouched, plus ``up_bytes`` / ``down_bytes`` / ``covered_ms`` which are
  **NULL for every pre-v8 row** - converting v7 rates to bytes would bake in a measured x1.53
  over-count, and backfilling coverage would invent data.
* Tiers are query-precedence layers, never additive: each sub-range is answered by exactly one tier,
  and the tier boundaries are WATERMARKS in ``metadata`` (``WATERMARK_KEYS`` below), not MIN(ts).
* ``usage_counter`` and ``metadata`` are byte-for-byte the v7 tables. ``bandwidth_history`` (never
  written to) is not carried over. The throughput histogram / ``ms_above`` tables were cut from 2.2.

**Rollback contract.** A 2.1.5-2.1.7 build that opens a v8 file sees ``db_version`` 8, goes
read-only (its downgrade guard), and keeps READING history through the six v7-named views below.
They use ``LEFT JOIN`` + ``COALESCE`` so a fact row whose dimension row is missing still shows,
``NULLIF(dt_ms, 0)`` as a second line behind the ``CHECK``, and ``entity = ''`` so per-GPU /
per-adapter metric rows never duplicate a ``(timestamp, stat_type)`` pair. Every column a 2.1.x
reader unpacks is non-NULL, as it was in v7.

**SQLite floor.** No STRICT tables, no generated columns, nothing newer than WITHOUT ROWID (3.8.2)
and ordinary views. Checked 2026-10-19: the 2.1.5 portable build bundles ``sqlite3.dll`` **3.50.4**
(file version read from the released ``NetSpeedTray-Portable-2.1.5.zip``; CPython 3.13.15). 2.1.6
and 2.1.7 were built by the same workflow on CPython 3.13.15 (release-run logs). The floor that
actually binds is third-party tools (``DATABASE.md`` invites DB Browser and friends), which is why
the bar is "3.8.2-era features only", far below what the app itself ships.
"""

import sqlite3
from typing import Tuple

from netspeedtray import constants

_D = constants.data

# ---- names -------------------------------------------------------------------------------------
INTERFACES = "interfaces"
METRICS = "metrics"
NET_RAW = "net_raw"
NET_MINUTE = "net_minute"
NET_HOUR = "net_hour"
HW_RAW = "hw_raw"
HW_MINUTE = "hw_minute"
HW_HOUR = "hw_hour"
APP_SESSIONS = "app_sessions"
LINK_EVENTS = "link_events"
METADATA = "metadata"

# Bytes per page. The SQLite default and the NTFS cluster size; v7 files are 4096 too. It can only
# be set on an empty file (or by a VACUUM outside WAL mode), so it is fixed here, at creation.
PAGE_SIZE = 4096

# net_raw.flags / net_minute.flags / net_hour.flags bits. Provenance of a pre-v8 row is
# "up_bytes IS NULL", not a flag.
FLAG_COUNTER_RESET = 1   # counter went backwards (reset / wrap / adapter re-enumerated)
FLAG_GAP = 2             # interval longer than the poll rate (missed polls, sleep, app stall)
FLAG_RESUMED = 4         # the system resumed from sleep inside the interval
FLAG_FIRST_SAMPLE = 8    # first sample of a session; its delta is not trustworthy

# Tier watermarks (metadata keys, value = unix epoch seconds as text). "Every row of this tier older
# than the watermark has been rolled up into the next-coarser tier", written in the SAME transaction
# as the roll-up that moves the rows. Readers answer a range with exactly one tier per sub-range:
# hour for [.., minute watermark), minute for [minute watermark, raw watermark), raw from the raw
# watermark on - never MIN(ts) guesses. The network and hardware families roll up independently
# (the v7 -> v8 migration folds the network raw tier but leaves hardware raw rows in place), so each
# family has its own pair. Always: raw watermark >= minute watermark; the minute watermark is hour
# aligned and the raw watermark minute aligned.
WATERMARK_RAW_KEY = "tier_watermark_raw"            # network: raw -> minute
WATERMARK_MINUTE_KEY = "tier_watermark_minute"      # network: minute -> hour
HW_WATERMARK_RAW_KEY = "hw_tier_watermark_raw"      # hardware: raw -> minute
HW_WATERMARK_MINUTE_KEY = "hw_tier_watermark_minute"  # hardware: minute -> hour
WATERMARK_KEYS = (WATERMARK_RAW_KEY, WATERMARK_MINUTE_KEY, HW_WATERMARK_RAW_KEY, HW_WATERMARK_MINUTE_KEY)

# The rest of the contract, as the reader (core/db/history_reader.V8HistoryReader) applies it.
# Per family, W_r = its raw watermark and W_m = its minute watermark:
#
# * Ranges: raw answers [W_r, inf), minute [W_m, W_r), hour (-inf, min(W_r, W_m)). On overlap the
#   coarser tier wins: a finer row below its watermark (left behind, or stamped there by a clock set
#   back after the pass) is ignored until the next pass rolls it up; a coarser row at/above the finer
#   watermark (a copy) is ignored.
# * Value: a base-10 integer string and nothing else ('1760000000'). The reader treats a value that is
#   not all digits, is <= 0, or more than WATERMARK_MAX_LEAD_SECONDS past its clock as ABSENT - a
#   watermark from a clock that ran ahead would declare every live raw row "already rolled up" and
#   blank the graph until the clock caught up. The lead exists because the migrator stamps the raw
#   watermark at the END of the newest minute bucket (the one the migration ran in), clamped at
#   now + the migrator's FUTURE_SLACK_SECONDS, then floored to a minute and +60: at most now + 360 s.
# * Atomicity: INSERT the coarser rows, DELETE (or keep) the finer ones, set the watermark, COMMIT - one
#   transaction, so a reader that sees the new rows sees the new watermark and vice versa.
# * The value is the cutoff of the LATEST pass, not a running max: it may move backwards (clock set
#   back) and is still a true statement.
# * Absent = no claim. The reader then falls back, for that boundary only, to the step-2 rule: the
#   finer tier's MIN(ts) inside its retention horizon (+ slack). The migrator writes all four in its
#   migration transaction; maintenance writes a family's pair in the same transaction as each roll-up.
WATERMARK_MAX_LEAD_SECONDS = 360   # >= the migrator's FUTURE_SLACK_SECONDS + 60 (pinned by a test)

# NET_TIER_UNITS - what net_minute / net_hour numbers mean (writer_v8 writes them; the reader's legacy
# estimate relies on them):
# * ``sample_count`` = the measured SECONDS the row stands for, capped at the bucket length (60 / 3600):
#   ROUND(SUM(dt_ms) / 1000) of its raw rows for a v8 row; v7's per-second sample count for a migrated
#   row (the unit LEGACY_SAMPLE_SECONDS = 1 already assumes). ``*_avg`` = the time-weighted rate over
#   those seconds, bytes/s. Hardware tiers are unaffected: there sample_count is a sample count.
# * Volume: SUM(bytes) where bytes is NOT NULL; else (legacy) ``avg x MIN(sample_count, bucket)`` -
#   the same capped expression in the reader and in every roll-up / merge of the writer, so a row's
#   estimate never changes when it is merged or rolled up: a merge (or a roll-up group) that includes a
#   legacy row stays legacy and stores avg = (sum of its parts' estimates) / MIN(n_total, bucket).

# The tables a v8 file is DEFINED to have. The per-version registry is
# DatabaseWorker.EXPECTED_TABLES in core/database.py (used by _verify_backup); its v8 entry is this
# tuple, imported from here. The dependency runs database.py -> schema_v8 only: this module stays
# free of Qt and of database.py, so it never forms an import cycle when the worker starts calling
# create_v8_schema (step 7).
EXPECTED_TABLES_V8: Tuple[str, ...] = (
    METADATA, _D.USAGE_COUNTER_TABLE, INTERFACES, METRICS, NET_RAW, NET_MINUTE, NET_HOUR,
    HW_RAW, HW_MINUTE, HW_HOUR, APP_SESSIONS, LINK_EVENTS,
)

# The v7-named read-only views a rolled-back 2.1.x build reads through.
COMPAT_VIEWS_V8: Tuple[str, ...] = (
    _D.SPEED_TABLE_RAW, _D.SPEED_TABLE_MINUTE, _D.SPEED_TABLE_HOUR,
    _D.HARDWARE_STATS_TABLE_RAW, _D.HARDWARE_STATS_TABLE_MINUTE, _D.HARDWARE_STATS_TABLE_HOUR,
)

# What "Delete all history" empties on a v8 file - equal to constants.data.HISTORY_TABLES (pinned by a
# test; that module cannot import this one).
# Facts first, then the dimensions: adapter friendly names are identifying (privacy.md), so a delete
# that kept `interfaces` would not delete everything it promised. Emptying the dimensions under a
# running writer is safe for readers (the views LEFT JOIN), but the writer must drop its cached ids.
# usage_counter (opt-in, decision D5) and metadata (bookkeeping) are deliberately absent.
HISTORY_TABLES_V8: Tuple[str, ...] = (
    NET_RAW, NET_MINUTE, NET_HOUR, HW_RAW, HW_MINUTE, HW_HOUR, LINK_EVENTS, APP_SESSIONS,
    INTERFACES, METRICS,
)

# ---- DDL ---------------------------------------------------------------------------------------
# One statement per entry: executescript() issues an implicit COMMIT, so it must never be used.

def _net_tier(name: str) -> str:
    return f"""CREATE TABLE {name} (
    ts           INTEGER NOT NULL,          -- bucket start, unix epoch seconds
    iface_id     INTEGER NOT NULL,
    up_bytes     INTEGER,                   -- exact bytes in the bucket; NULL = pre-v8 (rates only)
    down_bytes   INTEGER,
    up_avg       REAL    NOT NULL,          -- mean of the sample rates, bytes/s (the v7 column)
    down_avg     REAL    NOT NULL,
    up_max       REAL    NOT NULL,          -- peak sample rate, bytes/s
    down_max     REAL    NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 1,  -- SECONDS the row stands for (see NET_TIER_UNITS)
    covered_ms   INTEGER CHECK (covered_ms >= 0),  -- ms the app was running; NULL = unknown (pre-v8)
    flags        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts, iface_id)
) WITHOUT ROWID"""


def _hw_tier(name: str) -> str:
    return f"""CREATE TABLE {name} (
    ts           INTEGER NOT NULL,
    metric_id    INTEGER NOT NULL,
    avg_value    REAL    NOT NULL,
    min_value    REAL,                      -- NULL for pre-v8 rows (v7 never kept a min)
    max_value    REAL    NOT NULL,
    sample_count INTEGER NOT NULL,
    PRIMARY KEY (ts, metric_id)
) WITHOUT ROWID"""


TABLE_DDL: Tuple[str, ...] = (
    # metadata and usage_counter: identical to v7 (core/database.py) - migration copies them verbatim.
    f"CREATE TABLE {METADATA} (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    f"""CREATE TABLE {_D.USAGE_COUNTER_TABLE} (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cumulative_up REAL NOT NULL DEFAULT 0,
    cumulative_down REAL NOT NULL DEFAULT 0,
    anchor_up REAL NOT NULL DEFAULT 0,
    anchor_down REAL NOT NULL DEFAULT 0,
    period_key TEXT NOT NULL DEFAULT '',
    updated_ts INTEGER NOT NULL DEFAULT 0
)""",
    # Dimensions are ordinary rowid tables: INTEGER PRIMARY KEY *is* the rowid, the cheapest
    # possible surrogate key.
    f"""CREATE TABLE {INTERFACES} (
    iface_id    INTEGER PRIMARY KEY,
    stable_key  TEXT    NOT NULL UNIQUE,    -- NET_LUID hex when obtainable, else the friendly name
    name        TEXT    NOT NULL,           -- current display name; may change under us
    is_physical INTEGER NOT NULL DEFAULT 1,
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    alias_of    INTEGER                     -- another iface_id this one continues; NULL = none (2.2: always)
)""",
    f"""CREATE TABLE {METRICS} (
    metric_id INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,                -- 'cpu', 'gpu_temp', 'latency_anchor', 'errin', ...
    entity    TEXT NOT NULL DEFAULT '',     -- '' = the one/aggregate instance; 'gpu0', an interface, ...
    unit      TEXT NOT NULL DEFAULT '',     -- 'percent', 'celsius', 'watt', 'ms', 'ratio', 'count'
    UNIQUE (name, entity)
)""",
    # Network raw (24 h): bytes over the interval they were measured across. No secondary index -
    # a leading category column scatters the per-second write, and measured, the PK scan is faster
    # on the dominant adapter anyway (design §3).
    f"""CREATE TABLE {NET_RAW} (
    ts         INTEGER NOT NULL,            -- end of the sample interval, unix epoch seconds
    iface_id   INTEGER NOT NULL,
    up_bytes   INTEGER NOT NULL CHECK (up_bytes >= 0),
    down_bytes INTEGER NOT NULL CHECK (down_bytes >= 0),
    dt_ms      INTEGER NOT NULL CHECK (dt_ms > 0),
    flags      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ts, iface_id)
) WITHOUT ROWID""",
    _net_tier(NET_MINUTE),
    _net_tier(NET_HOUR),
    f"""CREATE TABLE {HW_RAW} (
    ts        INTEGER NOT NULL,
    metric_id INTEGER NOT NULL,
    value     REAL    NOT NULL,
    PRIMARY KEY (ts, metric_id)
) WITHOUT ROWID""",
    _hw_tier(HW_MINUTE),
    _hw_tier(HW_HOUR),
    # One row per app run. A surrogate key: two instances can start in the same second (portable
    # beside installed, the --apply-update relaunch). last_seen_ts is the heartbeat that bounds a
    # session ended by a crash or a taskkill.
    f"""CREATE TABLE {APP_SESSIONS} (
    session_id   INTEGER PRIMARY KEY,
    started_ts   INTEGER NOT NULL,
    last_seen_ts INTEGER NOT NULL,
    ended_ts     INTEGER,                   -- NULL while running or after an unclean exit
    version      TEXT    NOT NULL,          -- the build that wrote this session's rows
    poll_ms      INTEGER NOT NULL,          -- capture interval in force at start
    clean_exit   INTEGER NOT NULL DEFAULT 0
)""",
    # (iface_id, ts) key with the event as payload: with `event` in the key, a flapping link -
    # the failing-cable signature - would have its events dropped by INSERT OR IGNORE.
    f"""CREATE TABLE {LINK_EVENTS} (
    iface_id INTEGER NOT NULL,
    ts       INTEGER NOT NULL,
    event    INTEGER NOT NULL,              -- 0 = down, 1 = up
    PRIMARY KEY (iface_id, ts)
) WITHOUT ROWID""",
)

# Rollups are written in bulk by one INSERT...SELECT per maintenance pass, so a secondary index
# costs little there and serves the per-adapter / per-metric long-range reads.
INDEX_DDL: Tuple[str, ...] = (
    f"CREATE INDEX idx_{NET_MINUTE}_iface_ts ON {NET_MINUTE} (iface_id, ts)",
    f"CREATE INDEX idx_{NET_HOUR}_iface_ts ON {NET_HOUR} (iface_id, ts)",
    f"CREATE INDEX idx_{HW_MINUTE}_metric_ts ON {HW_MINUTE} (metric_id, ts)",
    f"CREATE INDEX idx_{HW_HOUR}_metric_ts ON {HW_HOUR} (metric_id, ts)",
)

# The interface name a fact row shows when its `interfaces` row is gone.
_IFACE_NAME = "COALESCE(i.name, 'interface ' || n.iface_id)"
_METRIC_NAME = "COALESCE(m.name, 'metric ' || h.metric_id)"


def _speed_tier_view(view: str, table: str) -> str:
    return f"""CREATE VIEW {view} AS
SELECT n.ts AS timestamp, {_IFACE_NAME} AS interface_name,
       n.up_avg AS upload_avg, n.down_avg AS download_avg,
       n.up_max AS upload_max, n.down_max AS download_max,
       n.sample_count AS sample_count
FROM {table} n LEFT JOIN {INTERFACES} i ON i.iface_id = n.iface_id"""


def _hw_tier_view(view: str, table: str) -> str:
    return f"""CREATE VIEW {view} AS
SELECT h.ts AS timestamp, {_METRIC_NAME} AS stat_type,
       h.avg_value AS avg_value, h.max_value AS max_value, h.sample_count AS sample_count
FROM {table} h LEFT JOIN {METRICS} m ON m.metric_id = h.metric_id
WHERE COALESCE(m.entity, '') = ''"""


VIEW_DDL: Tuple[str, ...] = (
    # NULLIF is belt-and-braces behind CHECK (dt_ms > 0): a zero can then only become NULL, never a
    # division error or an infinite rate.
    f"""CREATE VIEW {_D.SPEED_TABLE_RAW} AS
SELECT n.ts AS timestamp, {_IFACE_NAME} AS interface_name,
       n.up_bytes   * 1000.0 / NULLIF(n.dt_ms, 0) AS upload_bytes_sec,
       n.down_bytes * 1000.0 / NULLIF(n.dt_ms, 0) AS download_bytes_sec
FROM {NET_RAW} n LEFT JOIN {INTERFACES} i ON i.iface_id = n.iface_id""",
    _speed_tier_view(_D.SPEED_TABLE_MINUTE, NET_MINUTE),
    _speed_tier_view(_D.SPEED_TABLE_HOUR, NET_HOUR),
    f"""CREATE VIEW {_D.HARDWARE_STATS_TABLE_RAW} AS
SELECT h.ts AS timestamp, {_METRIC_NAME} AS stat_type, h.value AS value
FROM {HW_RAW} h LEFT JOIN {METRICS} m ON m.metric_id = h.metric_id
WHERE COALESCE(m.entity, '') = ''""",
    _hw_tier_view(_D.HARDWARE_STATS_TABLE_MINUTE, HW_MINUTE),
    _hw_tier_view(_D.HARDWARE_STATS_TABLE_HOUR, HW_HOUR),
)


class SchemaCreateError(RuntimeError):
    """create_v8_schema was handed a connection it cannot build a correct v8 file on."""


def create_v8_schema(conn: sqlite3.Connection) -> None:
    """
    Build the complete v8 schema on an EMPTY database file, in one transaction.

    ``page_size`` and ``auto_vacuum=INCREMENTAL`` only take effect before the file has a header.
    Anything that writes page 1 first - ``PRAGMA journal_mode=WAL`` on a new file does - turns both
    into silent no-ops (measured: ``auto_vacuum`` stays 0, disabling the incremental-vacuum strategy
    for the life of the file). So the file must have zero pages, and both values are read back and
    verified. Switch the file to WAL *after* this returns.

    Writes no ``metadata`` rows: a migration copies v7's verbatim and then stamps ``db_version``;
    a fresh install stamps it itself.

    Raises SchemaCreateError if the file is not empty, a transaction is open, or a pragma did not
    stick. On any error inside the transaction, everything is rolled back.
    """
    if conn.in_transaction:
        raise SchemaCreateError("create_v8_schema needs a connection with no open transaction")
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    if page_count != 0:
        raise SchemaCreateError(
            f"create_v8_schema needs an empty file (it has {page_count} pages); page_size and "
            "auto_vacuum cannot be changed once the header is written")

    conn.execute(f"PRAGMA page_size = {PAGE_SIZE}")
    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")

    # Explicit BEGIN/COMMIT around individual execute() calls; never executescript().
    conn.execute("BEGIN")
    try:
        for stmt in TABLE_DDL + INDEX_DDL + VIEW_DDL:
            conn.execute(stmt)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise

    got_page = conn.execute("PRAGMA page_size").fetchone()[0]
    got_av = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
    if got_page != PAGE_SIZE or got_av != 2:
        raise SchemaCreateError(
            f"v8 file built with page_size={got_page}, auto_vacuum={got_av} "
            f"(wanted {PAGE_SIZE}, 2 = INCREMENTAL)")
