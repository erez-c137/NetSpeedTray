"""
The v8 schema module (database v8 step 1 - dormant: nothing in the app calls it yet).

Two halves:

* The physical design: a fresh file gets ``auto_vacuum=INCREMENTAL`` and the chosen page size
  (both silently become no-ops once a file has a header - most easily by switching it to WAL
  first), every history table is ``WITHOUT ROWID``, ``dt_ms`` rejects zero and negatives.
* The rollback contract: a 2.1.5-2.1.7 build opening a v8 file goes read-only (its downgrade
  guard) and keeps READING through the v7-named compatibility views. ``V217_QUERIES`` below are
  the SELECTs those builds actually issue, copied verbatim from ``git show v2.1.7:...`` (the
  reader code is byte-identical in v2.1.5, v2.1.6 and v2.1.7). They must run against the views
  and return the shapes the old Python code unpacks - several of those call sites do
  ``float(row[1])`` with no None guard, so a NULL where v7 had NOT NULL is a crash, not a blank.
"""
import os
import sqlite3
import sys

import pytest

sys.path.append(os.path.abspath("src"))
from netspeedtray import constants
from netspeedtray.core.db import schema_v8 as S

D = constants.data
T0 = 1_760_000_000  # a fixed epoch second (Oct 2025); minute- and hour-aligned below
T_MIN = T0 - T0 % 3600


def _fresh(tmp_path, name="v8.db"):
    conn = sqlite3.connect(tmp_path / name)
    S.create_v8_schema(conn)
    return conn


def _populate(conn):
    """A small but complete v8 file: two adapters, every tier, hardware with per-entity rows."""
    c = conn
    c.execute("INSERT INTO metadata (key, value) VALUES ('db_version', '8')")
    c.execute(f"INSERT INTO {S.INTERFACES} (iface_id, stable_key, name, is_physical, first_seen, last_seen) "
              "VALUES (1, 'luid:1', 'Wi-Fi 3', 1, ?, ?), (2, 'luid:2', 'Tailscale', 0, ?, ?)",
              (T_MIN - 7200, T0, T_MIN - 7200, T0))
    # raw: 1 s samples on Wi-Fi, one 2 s sample (a SMART-mode / missed-poll interval) on Tailscale
    for k in range(5):
        c.execute(f"INSERT INTO {S.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms) VALUES (?, 1, ?, ?, 1000)",
                  (T0 + k, 100 * (k + 1), 1000 * (k + 1)))
    c.execute(f"INSERT INTO {S.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms) VALUES (?, 2, 50, 400, 2000)",
              (T0 + 1,))
    # minute: one v8-native row (bytes known) and one migrated v7 row (bytes/covered_ms NULL)
    c.execute(f"INSERT INTO {S.NET_MINUTE} (ts, iface_id, up_bytes, down_bytes, up_avg, down_avg, up_max, "
              "down_max, sample_count, covered_ms) VALUES (?, 1, 6000, 60000, 100.0, 1000.0, 300.0, 5000.0, 60, 60000)",
              (T_MIN - 3600,))
    c.execute(f"INSERT INTO {S.NET_MINUTE} (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
              "VALUES (?, 2, 1.5, 2.5, 3.0, 4.0, 12)", (T_MIN - 3540,))
    c.execute(f"INSERT INTO {S.NET_HOUR} (ts, iface_id, up_avg, down_avg, up_max, down_max, sample_count) "
              "VALUES (?, 1, 10.0, 20.0, 30.0, 40.0, 3600)", (T_MIN - 7200,))
    # hardware: aggregate cpu/gpu + two per-entity GPUs that must NOT leak into the v7 views
    c.execute(f"INSERT INTO {S.METRICS} (metric_id, name, entity, unit) VALUES "
              "(1, 'cpu', '', 'percent'), (2, 'gpu', '', 'percent'), "
              "(3, 'gpu', 'gpu0', 'percent'), (4, 'gpu', 'gpu1', 'percent')")
    for k in range(3):
        for mid, v in ((1, 10.0 + k), (2, 50.0), (3, 90.0), (4, 10.0)):
            c.execute(f"INSERT INTO {S.HW_RAW} (ts, metric_id, value) VALUES (?, ?, ?)", (T0 + k, mid, v))
    for tbl, ts in ((S.HW_MINUTE, T_MIN - 3600), (S.HW_HOUR, T_MIN - 7200)):
        for mid in (1, 2, 3):
            c.execute(f"INSERT INTO {tbl} (ts, metric_id, avg_value, max_value, sample_count) VALUES (?, ?, ?, ?, 60)",
                      (ts, mid, 20.0 + mid, 40.0 + mid))
    c.execute(f"INSERT INTO {D.USAGE_COUNTER_TABLE} (id, cumulative_up, cumulative_down, anchor_up, anchor_down, "
              "period_key, updated_ts) VALUES (1, 1e9, 5e9, 1e8, 5e8, '2025-10-01', ?)", (T0,))
    c.commit()


# --------------------------------------------------------------------------------------------
# Physical design
# --------------------------------------------------------------------------------------------

def test_fresh_file_gets_incremental_auto_vacuum_and_page_size(tmp_path):
    _fresh(tmp_path).close()
    conn = sqlite3.connect(tmp_path / "v8.db")   # a NEW connection reads what is on disk
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    assert conn.execute("PRAGMA page_size").fetchone()[0] == S.PAGE_SIZE
    # Switching to WAL afterwards (what the app does on every open) must keep both.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO metadata (key, value) VALUES ('k', 'v')")
    conn.commit()
    assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    assert conn.execute("PRAGMA page_size").fetchone()[0] == S.PAGE_SIZE


def test_refuses_a_file_that_already_has_a_header(tmp_path):
    """journal_mode=WAL on an empty file writes page 1, after which page_size and auto_vacuum are
    silent no-ops (measured: auto_vacuum stays 0). The function must refuse, not quietly build a
    file without the incremental-vacuum strategy."""
    conn = sqlite3.connect(tmp_path / "walfirst.db")
    conn.execute("PRAGMA journal_mode=WAL")
    with pytest.raises(S.SchemaCreateError):
        S.create_v8_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0


def test_refuses_inside_an_open_transaction(tmp_path):
    conn = sqlite3.connect(tmp_path / "tx.db", isolation_level=None)
    conn.execute("BEGIN")
    with pytest.raises(S.SchemaCreateError):
        S.create_v8_schema(conn)


def test_creates_exactly_the_expected_tables_and_views(tmp_path):
    conn = _fresh(tmp_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert tables == set(S.EXPECTED_TABLES_V8)
    assert views == set(S.COMPAT_VIEWS_V8)
    # The views carry the v7 history-table names, so a 2.1.x reader finds every name it queries.
    assert set(S.COMPAT_VIEWS_V8) == {
        D.SPEED_TABLE_RAW, D.SPEED_TABLE_MINUTE, D.SPEED_TABLE_HOUR,
        D.HARDWARE_STATS_TABLE_RAW, D.HARDWARE_STATS_TABLE_MINUTE, D.HARDWARE_STATS_TABLE_HOUR}
    # Cut from 2.2 (owner default 1): no throughput histogram / ms_above side tables.
    assert not any("hist" in t or "above" in t for t in tables)
    assert D.BANDWIDTH_TABLE not in tables  # never written to; not carried into v8


def test_the_worker_registry_holds_this_v8_set_not_a_copy():
    """One home for the per-version table sets: DatabaseWorker.EXPECTED_TABLES (step 0). Its v8
    entry must BE this module's tuple, so the two can never drift. (The v7 entry is pinned to a
    fresh v7 build by test_v7_hardening.)"""
    from netspeedtray.core.database import DatabaseWorker
    assert DatabaseWorker.EXPECTED_TABLES[8] is S.EXPECTED_TABLES_V8
    assert len(set(S.EXPECTED_TABLES_V8)) == len(S.EXPECTED_TABLES_V8)


def test_history_tables_v8_are_tables_and_keep_odometer_and_metadata(tmp_path):
    assert set(S.HISTORY_TABLES_V8) <= set(S.EXPECTED_TABLES_V8)
    assert "metadata" not in S.HISTORY_TABLES_V8
    assert D.USAGE_COUNTER_TABLE not in S.HISTORY_TABLES_V8
    # Adapter friendly names are history too (privacy.md treats them as identifying).
    assert S.INTERFACES in S.HISTORY_TABLES_V8


WITHOUT_ROWID = {S.NET_RAW, S.NET_MINUTE, S.NET_HOUR, S.HW_RAW, S.HW_MINUTE, S.HW_HOUR, S.LINK_EVENTS}


def test_every_history_table_is_without_rowid_as_designed(tmp_path):
    conn = _fresh(tmp_path)
    for t in S.EXPECTED_TABLES_V8:
        with_rowid = True
        try:
            conn.execute(f"SELECT rowid FROM {t} LIMIT 0")
        except sqlite3.OperationalError:
            with_rowid = False
        assert with_rowid is (t not in WITHOUT_ROWID), t


def test_no_strict_tables_and_no_raw_secondary_indexes(tmp_path):
    conn = _fresh(tmp_path)
    for (sql,) in conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"):
        assert "STRICT" not in sql.upper()
    idx_tables = {r[0] for r in conn.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    assert S.NET_RAW not in idx_tables and S.HW_RAW not in idx_tables  # PK scan only (design §3)


def test_link_events_key_is_iface_then_ts(tmp_path):
    """event is payload, not key: a flapping link (down/up in one second) must not have its
    second event silently dropped by INSERT OR IGNORE - but two events for the same interface at
    the same second still collide, which is what the PK is for."""
    conn = _fresh(tmp_path)
    pk = [r[1] for r in sorted(
        (r for r in conn.execute(f"PRAGMA table_info({S.LINK_EVENTS})") if r[5]), key=lambda r: r[5])]
    assert pk == ["iface_id", "ts"]


def test_app_sessions_have_a_surrogate_key(tmp_path):
    conn = _fresh(tmp_path)
    for _ in range(2):  # two instances starting in the same second (portable + installed)
        conn.execute(f"INSERT INTO {S.APP_SESSIONS} (started_ts, last_seen_ts, version, poll_ms) "
                     "VALUES (?, ?, '2.2.0', 1000)", (T0, T0))
    assert conn.execute(f"SELECT COUNT(*) FROM {S.APP_SESSIONS}").fetchone()[0] == 2


def test_interfaces_alias_of_is_nullable(tmp_path):
    conn = _fresh(tmp_path)
    conn.execute(f"INSERT INTO {S.INTERFACES} (stable_key, name, first_seen, last_seen) VALUES ('k', 'n', 0, 0)")
    assert conn.execute(f"SELECT alias_of FROM {S.INTERFACES}").fetchone()[0] is None


@pytest.mark.parametrize("dt_ms", [0, -1000])
def test_zero_or_negative_dt_ms_is_rejected(tmp_path, dt_ms):
    conn = _fresh(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO {S.NET_RAW} (ts, iface_id, up_bytes, down_bytes, dt_ms) VALUES (1, 1, 0, 0, ?)",
                     (dt_ms,))


def test_hardware_values_are_real(tmp_path):
    conn = _fresh(tmp_path)
    conn.execute(f"INSERT INTO {S.HW_RAW} (ts, metric_id, value) VALUES (1, 1, 42)")  # an int goes in
    assert conn.execute(f"SELECT typeof(value) FROM {S.HW_RAW}").fetchone()[0] == "real"


# --------------------------------------------------------------------------------------------
# Compatibility views
# --------------------------------------------------------------------------------------------

def test_view_row_survives_a_missing_interfaces_row(tmp_path):
    """With an INNER JOIN, a fact row whose dimension row is gone (Delete all history clears
    `interfaces`) vanishes from every v7 query - measured in the design review: 4 rows in, 3 out."""
    conn = _fresh(tmp_path)
    _populate(conn)
    conn.execute(f"DELETE FROM {S.INTERFACES} WHERE iface_id = 2")
    raw = conn.execute(f"SELECT COUNT(*) FROM {D.SPEED_TABLE_RAW}").fetchone()[0]
    assert raw == conn.execute(f"SELECT COUNT(*) FROM {S.NET_RAW}").fetchone()[0] == 6
    name = conn.execute(f"SELECT interface_name FROM {D.SPEED_TABLE_RAW} WHERE timestamp=? AND "
                        "upload_bytes_sec=25.0", (T0 + 1,)).fetchone()[0]
    assert name is not None and name != ""
    assert conn.execute(f"SELECT COUNT(*) FROM {D.SPEED_TABLE_MINUTE}").fetchone()[0] == 2
    # ...and the same for a missing metrics row.
    conn.execute(f"DELETE FROM {S.METRICS} WHERE metric_id = 1")
    assert conn.execute(f"SELECT COUNT(*) FROM {D.HARDWARE_STATS_TABLE_RAW} WHERE stat_type IS NOT NULL"
                        ).fetchone()[0] == 6  # cpu (orphaned) + gpu aggregate, 3 timestamps each


def test_raw_view_rate_uses_each_rows_own_interval(tmp_path):
    conn = _fresh(tmp_path)
    _populate(conn)
    row = conn.execute(f"SELECT upload_bytes_sec, download_bytes_sec FROM {D.SPEED_TABLE_RAW} "
                       "WHERE interface_name='Tailscale'").fetchone()
    assert row == (25.0, 200.0)  # 50 B / 400 B over 2000 ms, not over an assumed 1 s


def test_per_entity_metrics_do_not_duplicate_view_rows(tmp_path):
    conn = _fresh(tmp_path)
    _populate(conn)
    for view in (D.HARDWARE_STATS_TABLE_RAW, D.HARDWARE_STATS_TABLE_MINUTE, D.HARDWARE_STATS_TABLE_HOUR):
        dupes = conn.execute(f"SELECT COUNT(*) FROM (SELECT timestamp, stat_type FROM {view} "
                             "GROUP BY timestamp, stat_type HAVING COUNT(*) > 1)").fetchone()[0]
        assert dupes == 0, view
    # The aggregate gpu reads 50, not the average of 50, 90 and 10.
    assert conn.execute(f"SELECT AVG(value) FROM {D.HARDWARE_STATS_TABLE_RAW} WHERE stat_type='gpu'"
                        ).fetchone()[0] == 50.0


def test_views_are_read_only(tmp_path):
    """A rolled-back build's writes must fail loudly, not land somewhere."""
    conn = _fresh(tmp_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute(f"INSERT INTO {D.SPEED_TABLE_RAW} (timestamp, interface_name, upload_bytes_sec, "
                     "download_bytes_sec) VALUES (1, 'x', 1.0, 1.0)")


# --------------------------------------------------------------------------------------------
# 2.1.5-2.1.7's own read queries, verbatim, against a v8 file
# --------------------------------------------------------------------------------------------
# Each entry: (where it lives in v2.1.7, SQL as the f-string renders it, params, expected column
# count). Interval-dependent SQL is rendered for every interval the old code can choose.

def _bin(i):
    return f"CAST(timestamp / {i} AS INTEGER) * {i}"


def _hw_history_sql(interval):  # widget_state.get_hardware_history
    b = _bin(interval)
    return f"""
                SELECT b, AVG(v) FROM (
                    SELECT {b} AS b, value AS v FROM {D.HARDWARE_STATS_TABLE_RAW}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?
                    UNION ALL
                    SELECT {b} AS b, avg_value AS v FROM {D.HARDWARE_STATS_TABLE_MINUTE}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?
                    UNION ALL
                    SELECT {b} AS b, avg_value AS v FROM {D.HARDWARE_STATS_TABLE_HOUR}
                        WHERE stat_type = ? AND timestamp BETWEEN ? AND ?
                ) GROUP BY b ORDER BY b ASC
            """


def _speed_history_sql(res, iface):  # widget_state.get_speed_history
    interval = {"raw": 1, "minute": 60, "hour": 3600, "day": 86400}[res]
    tc = _bin(interval)
    params = []
    if res in ("minute", "hour", "day"):
        tiers = []

        def add(tbl, up, down):
            q = f"""
                        SELECT
                            {tc} as bin_ts,
                            interface_name,
                            {up} as up,
                            {down} as down
                        FROM {tbl}
                        WHERE timestamp BETWEEN ? AND ?
                    """
            p = [T_MIN - 86400, T0 + 60]
            if iface:
                q += " AND interface_name = ?"
                p.append(iface)
            tiers.append(q)
            params.extend(p)
        add(D.SPEED_TABLE_RAW, "upload_bytes_sec", "download_bytes_sec")
        add(D.SPEED_TABLE_MINUTE, "upload_max", "download_max")
        if res in ("hour", "day"):
            add(D.SPEED_TABLE_HOUR, "upload_max", "download_max")
        inner = f"""
                    SELECT
                        bin_ts,
                        interface_name,
                        MAX(up) as up,
                        MAX(down) as down
                    FROM ({" UNION ALL ".join(tiers)})
                    GROUP BY bin_ts, interface_name
                """
    else:
        inner = f"""
                    SELECT
                        {tc} as bin_ts,
                        interface_name,
                        AVG(upload_bytes_sec) as up,
                        AVG(download_bytes_sec) as down
                    FROM speed_history_raw
                    WHERE timestamp BETWEEN ? AND ?
                """
        params = [T_MIN - 86400, T0 + 60]
        if iface:
            inner += " AND interface_name = ?"
            params.append(iface)
        inner += " GROUP BY bin_ts, interface_name"
    agg = "SUM" if not iface else "AVG"
    outer = f"""
                    SELECT bin_ts, COALESCE({agg}(up), 0), COALESCE({agg}(down), 0)
                    FROM ({inner})
                    GROUP BY bin_ts
                    ORDER BY bin_ts
                """
    return outer, tuple(params)


def _v217_queries():
    win = (T_MIN - 86400, T0 + 60)
    q = []
    # database.py - the version read that sends 2.1.x into its read-only downgrade path
    q += [("database._get_current_db_version",
           "SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'", (), 1),
          ("database._get_current_db_version",
           "SELECT value FROM metadata WHERE key = 'db_version'", (), 1)]
    # widget_state._try_load_usage
    q += [("widget_state._try_load_usage",
           "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (D.USAGE_COUNTER_TABLE,), 1),
          ("widget_state._try_load_usage",
           f"SELECT cumulative_up, cumulative_down, anchor_up, anchor_down, period_key "
           f"FROM {D.USAGE_COUNTER_TABLE} WHERE id = 1", (), 5)]
    for interval in (1, 60, 3600):
        q.append((f"widget_state.get_hardware_history[{interval}]", _hw_history_sql(interval),
                  ("cpu",) + win + ("cpu",) + win + ("cpu",) + win, 2))
    # widget_state.summarize_hardware
    q += [("widget_state.summarize_hardware:raw",
           f"SELECT value FROM {D.HARDWARE_STATS_TABLE_RAW} WHERE stat_type=? AND timestamp BETWEEN ? AND ?",
           ("cpu",) + win, 1)]
    for t in (D.HARDWARE_STATS_TABLE_MINUTE, D.HARDWARE_STATS_TABLE_HOUR):
        q.append(("widget_state.summarize_hardware:tier",
                  f"SELECT avg_value, max_value, sample_count FROM {t} "
                  f"WHERE stat_type=? AND timestamp BETWEEN ? AND ?", ("cpu",) + win, 3))
    # widget_state.summarize_network (aggregate and per-interface)
    for col in ("download", "upload"):
        for iface in (None, "Wi-Fi 3"):
            wh = "" if iface is None else " AND interface_name=?"
            tail = () if iface is None else (iface,)
            q.append(("widget_state.summarize_network:raw",
                      f"SELECT SUM({col}_bytes_sec) FROM {D.SPEED_TABLE_RAW} "
                      f"WHERE timestamp BETWEEN ? AND ?{wh} GROUP BY timestamp", win + tail, 1))
            for t in (D.SPEED_TABLE_MINUTE, D.SPEED_TABLE_HOUR):
                q.append(("widget_state.summarize_network:tier",
                          f"SELECT SUM({col}_avg), MAX(t.mx), SUM(sample_count) FROM "
                          f"(SELECT timestamp, {col}_avg, {col}_max AS mx, sample_count FROM {t} "
                          f" WHERE timestamp BETWEEN ? AND ?{wh}) t GROUP BY t.timestamp", win + tail, 3))
    # widget_state.get_total_bandwidth_for_period
    for table, up, down in (("speed_history_raw", "upload_bytes_sec", "download_bytes_sec"),
                            ("speed_history_minute", "upload_avg", "download_avg"),
                            ("speed_history_hour", "upload_avg", "download_avg")):
        base = f"SELECT SUM({up}), SUM({down}) FROM {table} WHERE timestamp BETWEEN ? AND ?"
        q.append(("widget_state.get_total_bandwidth_for_period", base, win, 2))
        q.append(("widget_state.get_total_bandwidth_for_period", base + " AND interface_name = ?",
                  win + ("Wi-Fi 3",), 2))
    for res in ("raw", "minute", "hour", "day"):
        for iface in (None, "Wi-Fi 3"):
            sql, params = _speed_history_sql(res, iface)
            q.append((f"widget_state.get_speed_history[{res},{iface}]", sql, params, 3))
    q += [("widget_state.get_distinct_interfaces", """
                SELECT DISTINCT interface_name FROM speed_history_raw
                UNION
                SELECT DISTINCT interface_name FROM speed_history_minute
                UNION
                SELECT DISTINCT interface_name FROM speed_history_hour
                ORDER BY interface_name
            """, (), 1),
          ("widget_state.get_earliest_data_timestamp", """
                SELECT MIN(earliest_ts) FROM (
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_raw
                    UNION ALL
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_minute
                    UNION ALL
                    SELECT MIN(timestamp) as earliest_ts FROM speed_history_hour
                ) WHERE earliest_ts IS NOT NULL;
            """, (), 1)]
    # utils/db_utils.get_speed_history (the fallback), with and without an interface
    for iface in (None, "Wi-Fi 3"):
        raw_q = (f"SELECT timestamp, upload_bytes_sec, download_bytes_sec FROM {D.SPEED_TABLE_RAW} "
                 f"WHERE timestamp BETWEEN ? AND ?")
        agg_q = (f"SELECT timestamp, upload_avg as upload, download_avg as download FROM "
                 f"{D.SPEED_TABLE_MINUTE} WHERE timestamp BETWEEN ? AND ?")
        rp, ap = list(win), [0, T0]
        if iface:
            raw_q += " AND interface_name = ?"; rp.append(iface)
            agg_q += " AND interface_name = ?"; ap.append(iface)
        q.append(("db_utils.get_speed_history", " UNION ALL ".join([raw_q, agg_q]) + " ORDER BY timestamp ASC",
                  tuple(rp + ap), 3))
    return q


V217_QUERIES = _v217_queries()


@pytest.fixture
def v8_conn_as_217_reads_it(tmp_path):
    w = _fresh(tmp_path)
    _populate(w)
    w.close()
    # Exactly the read connection 2.1.7's WidgetState._get_read_conn opens.
    conn = sqlite3.connect(tmp_path / "v8.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


@pytest.mark.parametrize("where,sql,params,ncols", V217_QUERIES, ids=[q[0] for q in V217_QUERIES])
def test_217_read_query_runs_on_v8_and_returns_its_shape(v8_conn_as_217_reads_it, where, sql, params, ncols):
    rows = v8_conn_as_217_reads_it.execute(sql, params).fetchall()
    assert rows, f"{where} returned nothing against a populated v8 file"
    for r in rows:
        assert len(r) == ncols
        # The old code unpacks these with float()/int()/datetime.fromtimestamp() - no None guard on
        # db_utils' float(row[1]) - so every value it reads must be non-NULL.
        if "sqlite_master" not in sql and "usage_counter" not in sql:
            assert all(v is not None for v in r), (where, tuple(r))


def test_217_results_carry_the_right_values(v8_conn_as_217_reads_it):
    c = v8_conn_as_217_reads_it
    assert c.execute("SELECT value FROM metadata WHERE key = 'db_version'").fetchone()[0] == "8"
    assert [r[0] for r in c.execute(V217_QUERIES_BY_NAME["widget_state.get_distinct_interfaces"])] == \
        ["Tailscale", "Wi-Fi 3"]
    assert c.execute(V217_QUERIES_BY_NAME["widget_state.get_earliest_data_timestamp"]).fetchone()[0] == T_MIN - 7200
    win = (T_MIN - 86400, T0 + 60)
    # Raw volume per 2.1.x arithmetic: SUM(rate). Wi-Fi rates are bytes/1 s.
    up, down = c.execute("SELECT SUM(upload_bytes_sec), SUM(download_bytes_sec) FROM speed_history_raw "
                         "WHERE timestamp BETWEEN ? AND ? AND interface_name = ?", win + ("Wi-Fi 3",)).fetchone()
    assert (up, down) == (1500.0, 15000.0)
    # Minute tier exposes the stored avg/max/sample_count unchanged, including for migrated rows.
    assert tuple(c.execute("SELECT upload_avg, download_avg, upload_max, download_max, sample_count "
                           "FROM speed_history_minute WHERE interface_name='Tailscale'").fetchone()) == \
        (1.5, 2.5, 3.0, 4.0, 12)
    # Hardware: cpu raw three samples 10, 11, 12; tiers carry avg/max/sample_count.
    assert [r[0] for r in c.execute("SELECT value FROM hardware_stats_raw WHERE stat_type='cpu' ORDER BY timestamp")] \
        == [10.0, 11.0, 12.0]
    assert tuple(c.execute("SELECT avg_value, max_value, sample_count FROM hardware_stats_hour "
                           "WHERE stat_type='cpu'").fetchone()) == (21.0, 41.0, 60)


V217_QUERIES_BY_NAME = {w: s for (w, s, _p, _n) in V217_QUERIES}


def test_217_worker_goes_read_only_on_a_v8_file(tmp_path):
    """2.1.x's worker (frozen in tests/legacy_v7_worker - its downgrade branch is the code shipped in
    2.1.5-2.1.7) stands in for a rolled-back install: it must see version 8, flag the file
    incompatible, write no backup, and change nothing. (The live check on the real 2.1.7 source is in
    the step-7 PR; step 8 repeats it with the signed build.)"""
    import hashlib
    from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
    w = _fresh(tmp_path)
    _populate(w)
    w.close()
    c = sqlite3.connect(tmp_path / "v8.db")
    c.execute("PRAGMA journal_mode = WAL")   # as 2.2 leaves every file it writes
    c.close()
    before = hashlib.sha256((tmp_path / "v8.db").read_bytes()).hexdigest()
    worker = LegacyV7Worker(tmp_path / "v8.db")
    worker._initialize_connection()
    worker._check_and_create_schema()
    assert worker._schema_incompatible is True
    worker._close_connection()
    assert hashlib.sha256((tmp_path / "v8.db").read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob("v8.db.bak*"))
    conn = sqlite3.connect(tmp_path / "v8.db")
    assert conn.execute(f"SELECT COUNT(*) FROM {S.NET_RAW}").fetchone()[0] == 6
    assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")} == set(S.COMPAT_VIEWS_V8)
    conn.close()
