"""
Unit tests for the data-usage odometer (data-cap feature): period-key math, the
accumulate/re-anchor logic on WidgetState, and the DB persist round-trip.
"""
import sqlite3
import types
import logging
from datetime import date
from unittest.mock import MagicMock

from netspeedtray import constants
from netspeedtray.core.widget_state import WidgetState
from netspeedtray.core.database import DatabaseWorker

USAGE = constants.data.USAGE_COUNTER_TABLE


# --- period-key math ---------------------------------------------------------

def test_period_key_same_month_when_past_reset_day():
    # reset day 15, today the 20th -> period started this month on the 15th.
    assert WidgetState._compute_period_key(15, date(2026, 6, 20)) == "2026-06-15"


def test_period_key_previous_month_before_reset_day():
    # reset day 15, today the 10th -> period started LAST month on the 15th.
    assert WidgetState._compute_period_key(15, date(2026, 6, 10)) == "2026-05-15"


def test_period_key_january_rolls_to_december():
    assert WidgetState._compute_period_key(15, date(2026, 1, 5)) == "2025-12-15"


def test_period_key_clamps_reset_day_to_1_28():
    # reset day 31 -> clamped to 28; today the 20th is before the 28th, so the period
    # started LAST month on the 28th.
    assert WidgetState._compute_period_key(31, date(2026, 6, 20)) == "2026-05-28"
    # reset day 0 -> clamped to 1; today the 20th is past it -> this month.
    assert WidgetState._compute_period_key(0, date(2026, 6, 20)) == "2026-06-01"


# --- accumulate / re-anchor --------------------------------------------------

def _fake_state(reset_day=1, period_key="2026-06-01"):
    s = types.SimpleNamespace()
    s.config = {"data_cap_reset_day": reset_day}
    s.db_worker = MagicMock()
    s.logger = logging.getLogger("test.usage")
    s._usage = {"cumulative_up": 0.0, "cumulative_down": 0.0,
                "anchor_up": 0.0, "anchor_down": 0.0, "period_key": period_key}
    s._usage_loaded = True
    s._usage_last_persist = 1e18  # far future -> no persist during the test
    s._usage_live_counters = None      # launch-reconciliation snapshot (none fed in these tests)
    s._usage_live_sampled_at = None
    s._usage_live_primary = None
    s._persist_usage_now = WidgetState._persist_usage_now.__get__(s)
    s._maybe_reanchor = WidgetState._maybe_reanchor.__get__(s)
    return s


def test_accumulates_and_reads_this_period():
    s = _fake_state()
    # Pretend we're inside the same period the state is anchored to.
    s._compute_period_key = lambda *a, **k: "2026-06-01"
    WidgetState.add_usage_bytes(s, 1000.0, 2000.0)
    WidgetState.add_usage_bytes(s, 500.0, 500.0)
    up, down = WidgetState.get_usage_this_period(s)
    assert up == 1500.0
    assert down == 2500.0


def test_negative_deltas_are_clamped():
    s = _fake_state()
    s._compute_period_key = lambda *a, **k: "2026-06-01"
    WidgetState.add_usage_bytes(s, -50.0, 100.0)  # NIC reset glitch on up
    up, down = WidgetState.get_usage_this_period(s)
    assert up == 0.0
    assert down == 100.0


def test_new_period_reanchors_and_boundary_poll_counts_to_new_period():
    s = _fake_state(period_key="2026-05-01")  # state thinks it's last period
    s._compute_period_key = lambda *a, **k: "2026-06-01"  # ...but now it's a new one
    WidgetState.add_usage_bytes(s, 1000.0, 1000.0)
    # The rollover anchors BEFORE adding this poll, so the boundary poll's bytes land in the
    # NEW period (audit #15 - previously they were stranded at the anchor and read as 0).
    up, down = WidgetState.get_usage_this_period(s)
    assert up == 1000.0 and down == 1000.0
    assert s._usage["period_key"] == "2026-06-01"


def test_idle_across_reset_rolls_over_on_read():
    # No traffic this period, but the reset day passed - reading must roll the period over
    # (audit #5) instead of reporting last period's usage as this period's.
    s = _fake_state(period_key="2026-05-01")
    s._usage = {"cumulative_up": 5000.0, "cumulative_down": 9000.0,
                "anchor_up": 0.0, "anchor_down": 0.0, "period_key": "2026-05-01"}
    s._maybe_reanchor = WidgetState._maybe_reanchor.__get__(s)
    s._persist_usage_now = WidgetState._persist_usage_now.__get__(s)
    s._compute_period_key = lambda *a, **k: "2026-06-01"  # new period, zero traffic
    up, down = WidgetState.get_usage_this_period(s)
    assert up == 0.0 and down == 0.0                 # fresh period, not last period's 5000/9000
    assert s._usage["period_key"] == "2026-06-01"


def test_backward_period_does_not_reanchor():
    # A backward clock/DST shift yields an earlier period key - it must NOT wipe the
    # running total (only a strictly-newer period re-anchors).
    s = _fake_state(period_key="2026-06-01")
    s._compute_period_key = lambda *a, **k: "2026-06-01"  # same period
    WidgetState.add_usage_bytes(s, 1000.0, 1000.0)  # establish some usage in the period
    s._compute_period_key = lambda *a, **k: "2026-05-01"  # clock jumps backward
    WidgetState.add_usage_bytes(s, 500.0, 500.0)
    assert s._usage["period_key"] == "2026-06-01"   # unchanged
    up, down = WidgetState.get_usage_this_period(s)
    assert up == 1500.0 and down == 1500.0          # nothing wiped


def test_persist_enqueues_when_due():
    s = _fake_state()
    s._compute_period_key = lambda *a, **k: "2026-06-01"
    s._usage_last_persist = 0.0  # due immediately
    WidgetState.add_usage_bytes(s, 10.0, 10.0)
    assert s.db_worker.enqueue_task.called
    task, payload = s.db_worker.enqueue_task.call_args[0]
    assert task == "persist_usage"
    # cum_up, cum_down, anchor_up, anchor_down, period_key, ts, counter snapshot, re-key memory
    assert len(payload) == 8
    # Bytes with no counters to pair: the total moved past the stored snapshot, so it is deleted
    # (CLEAR_SNAPSHOT) - never left beside a total it no longer matches. (A zero-byte poll with no
    # counters leaves it untouched: test_odometer_accuracy.)
    from netspeedtray.core import usage_snapshot
    assert payload[6] == usage_snapshot.CLEAR_SNAPSHOT
    assert payload[7] == ""    # no period left yet -> no re-key memory stored


# --- DB persist round-trip ---------------------------------------------------

def test_persist_usage_db_round_trip():
    conn = sqlite3.connect(":memory:")
    conn.executescript(f"""
        CREATE TABLE {USAGE} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cumulative_up REAL NOT NULL DEFAULT 0, cumulative_down REAL NOT NULL DEFAULT 0,
            anchor_up REAL NOT NULL DEFAULT 0, anchor_down REAL NOT NULL DEFAULT 0,
            period_key TEXT NOT NULL DEFAULT '', updated_ts INTEGER NOT NULL DEFAULT 0);
    """)
    fake = types.SimpleNamespace(conn=conn, logger=logging.getLogger("test.db"))
    DatabaseWorker._persist_usage(fake, (111.0, 222.0, 10.0, 20.0, "2026-06-01", 1700))
    # Upsert again (same row) to prove ON CONFLICT works.
    DatabaseWorker._persist_usage(fake, (999.0, 888.0, 10.0, 20.0, "2026-06-01", 1800))
    row = conn.execute(f"SELECT cumulative_up, cumulative_down, period_key FROM {USAGE} WHERE id=1").fetchone()
    assert row == (999.0, 888.0, "2026-06-01")
    assert conn.execute(f"SELECT COUNT(*) FROM {USAGE}").fetchone()[0] == 1


def test_persist_usage_failures_are_one_log_episode(caplog):
    """A full disk logged "Failed to persist usage counter" every 30 s (2026-10-29): now one ERROR,
    the repeats at DEBUG, and one INFO when a write succeeds again."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(f"""
        CREATE TABLE {USAGE} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cumulative_up REAL NOT NULL DEFAULT 0, cumulative_down REAL NOT NULL DEFAULT 0,
            anchor_up REAL NOT NULL DEFAULT 0, anchor_down REAL NOT NULL DEFAULT 0,
            period_key TEXT NOT NULL DEFAULT '', updated_ts INTEGER NOT NULL DEFAULT 0);
        CREATE TRIGGER full BEFORE INSERT ON {USAGE}
            BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END;
    """)
    fake = types.SimpleNamespace(conn=conn, logger=logging.getLogger("test.db.usage_episode"))
    payload = (1.0, 2.0, 0.0, 0.0, "2026-10-01", 1700)
    with caplog.at_level(logging.INFO, logger="test.db.usage_episode"):
        for _ in range(20):
            DatabaseWorker._persist_usage(fake, payload)
        conn.execute("DROP TRIGGER full")
        DatabaseWorker._persist_usage(fake, payload)
        DatabaseWorker._persist_usage(fake, payload)
    msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert [lvl for lvl, _ in msgs] == [logging.ERROR, logging.INFO]
    assert msgs[0][1] == "Failed to persist usage counter: database or disk is full"
    assert msgs[1][1].startswith("Usage counter persist recovered after 20 failure(s) over ")
    assert conn.execute(f"SELECT cumulative_up FROM {USAGE}").fetchone() == (1.0,)
