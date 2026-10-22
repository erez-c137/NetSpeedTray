"""
The odometer's period key under a reset-day change and a wall clock that was wrong (2.2 RC checkup F1
+ F2 and their review B1 / S1 / S2 / S6 / L1; harnesses h2, h3, r1, r5).

F1: the stored period key only moved forward. After the data-cap reset day changed, the key computed
from the NEW day could be older than the stored one (15 -> 10 on Oct 21: stored 2026-10-15, computed
2026-10-10), so "the window began in an already-closed period" fired on every poll and nothing counted
toward the cap: "used this period" froze. A reset-day change is now an explicit re-key: the odometer's
own count, adjusted only by what recorded history can justify (a strict read - a failed one is never a
0; history may raise the count only where it meters exactly the odometer's adapters), and a toggle back
restores the exact anchor.

F2: one poll with the clock ahead advanced the stored key into the future and the period then never
reset until that date. A stored period starting days from now is re-keyed - but only with evidence the
clock is right NOW (B1: the same picture arises with the clock BEHIND, and re-keying then wiped the
period once the clock was corrected), and a re-key the next rollover proves wrong is undone.
Real WidgetState code throughout; only ``date`` is patched.
"""
import logging
import time
import types
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

import netspeedtray.core.widget_state as wsmod
from netspeedtray.core.db.history_reader import VolumeResult
from netspeedtray.core.widget_state import WidgetState

GB = 1e9


class FakeDate(date):
    today_value = date(2026, 10, 21)

    @classmethod
    def today(cls):
        return cls.today_value


@pytest.fixture(autouse=True)
def _fake_date():
    with patch.object(wsmod, "date", FakeDate):
        yield


class _Reader:
    """Recorded history: ``down_since[start date]`` GB down since that start (a tenth of it up);
    ``fail``: every strict read fails; ``stamp``: the writer's clock_trusted_ts."""
    def __init__(self, down_since=None, fail=False, stamp=None):
        self.down_since, self.fail, self.stamp = down_since or {}, fail, stamp
        self.calls = []

    def metered_volume_strict(self, start, end, scope):
        self.calls.append((start, scope))
        if self.fail:
            return None
        d = float(self.down_since.get(start.date(), 0.0)) * GB
        return VolumeResult(d / 10, d, d / 10, d, False)

    def interface_names(self):
        return ["Ethernet"]

    def clock_trusted_ts(self):
        return self.stamp


def _state(reset_day, key, reader=None, interface_mode="auto", used=0.0, lifetime=500.0):
    s = types.SimpleNamespace()
    s.config = {"data_cap_reset_day": reset_day, "interface_mode": interface_mode, "excluded_interfaces": []}
    s.db_worker = MagicMock()
    s.logger = logging.getLogger("test.rekey")
    s._usage = {"cumulative_up": lifetime * GB, "cumulative_down": lifetime * GB,
                "anchor_up": lifetime * GB, "anchor_down": (lifetime - used) * GB, "period_key": key}
    s._usage_loaded = True
    s._usage_last_persist = time.time()
    s._usage_live_counters = s._usage_live_sampled_at = s._usage_live_primary = None
    s._usage_reset_pending = False
    s._usage_pending = []
    s._compute_period_key = WidgetState._compute_period_key
    s._persist_usage_now = WidgetState._persist_usage_now.__get__(s)
    s._maybe_reanchor = WidgetState._maybe_reanchor.__get__(s)
    s._try_load_usage = lambda: True
    if reader is not None:
        s.history_reader = reader
    return s


def _poll(s, when, down):
    FakeDate.today_value = when.date()
    WidgetState.add_usage_bytes(s, 0.0, down, None, when.timestamp(), when.timestamp() - 1, None)


def _read(s, on=None):
    if on is not None:
        FakeDate.today_value = on
    return round(WidgetState.get_usage_this_period(s)[1] / GB, 3)


# --- F1: the h3 scenarios -----------------------------------------------------------------------

@pytest.mark.parametrize("old_day, key, new_day, change_on, days, expected", [
    (15, "2026-10-15", 10, 21, range(21, 32), 10 + 11 * 2),   # new period starts BEFORE the old one
    (1, "2026-10-01", 25, 5, range(5, 25), 10 + 20 * 2),      # 1 -> 25 on the 5th: Sep 25 period
])
def test_cap_keeps_counting_after_a_reset_day_change(old_day, key, new_day, change_on, days, expected):
    """Was frozen at 10.0 GB (h3). No history: the count is kept and every later poll counts."""
    s = _state(old_day, key, lifetime=0.0)
    _poll(s, datetime(2026, 10, change_on, 8), 10 * GB)
    assert _read(s) == 10
    s.config["data_cap_reset_day"] = new_day
    for d in days:
        _poll(s, datetime(2026, 10, d, 12), 2 * GB)
    assert _read(s) == expected
    assert s._usage["period_key"] == WidgetState._compute_period_key(new_day, FakeDate.today_value)
    assert s._usage["cumulative_down"] == expected * GB              # lifetime unchanged by the re-key


def test_alerts_see_the_new_period_key_at_once():
    s = _state(15, "2026-10-15")
    s.config["data_cap_reset_day"] = 10
    assert WidgetState.get_usage_period_key(s) == "2026-10-10"


def test_later_start_is_seeded_from_history():
    """1 -> 15 on Oct 21: the period now began Oct 15. It used to re-anchor at 'now' (cap 0 GB while
    'This Period' showed Oct 15-21); history saw everything the odometer counted, so it is exact."""
    reader = _Reader({date(2026, 10, 1): 30, date(2026, 10, 15): 7})
    s = _state(1, "2026-10-01", reader, used=30)
    s.config["data_cap_reset_day"] = 15
    assert _read(s) == 7
    assert reader.calls[0][0] == datetime(2026, 10, 15)
    _poll(s, datetime(2026, 10, 21, 9), 1 * GB)
    assert _read(s) == 8


def test_later_start_keeps_the_bytes_history_never_saw():
    """The odometer counted 5 GB more than history since the old start (recovered for a closed app):
    they stay counted (they may be in the new period) - history alone would read 7."""
    s = _state(1, "2026-10-01", _Reader({date(2026, 10, 1): 25, date(2026, 10, 15): 7}), used=30)
    s.config["data_cap_reset_day"] = 15
    assert _read(s) == 12


def test_later_start_never_exceeds_what_the_odometer_counted():
    s = _state(1, "2026-10-01", _Reader({date(2026, 10, 1): 60, date(2026, 10, 15): 50}), used=30)
    s.config["data_cap_reset_day"] = 15
    assert _read(s) == 30


@pytest.mark.parametrize("reader", [_Reader(fail=True), None])
def test_later_start_with_unusable_history_keeps_the_count(reader):
    """S1 / r5 case 1: the v8 reader returns zeros on any error; the re-key took that as "nothing moved
    since the new start" and wrote 0 GB. A failed or missing read now keeps the count."""
    s = _state(10, "2026-10-10", reader, used=30)
    s.config["data_cap_reset_day"] = 15
    assert _read(s) == 30


def test_earlier_start_adds_the_history_between_the_starts_when_it_is_exact():
    """15 -> 10 on Oct 21, all_physical mode (history meters exactly the odometer's adapters): the
    period now also holds Oct 10-14 = 24 - 9 GB of history."""
    reader = _Reader({date(2026, 10, 10): 24, date(2026, 10, 15): 9})
    s = _state(15, "2026-10-15", reader, interface_mode="all_physical", used=10)
    s.config["data_cap_reset_day"] = 10
    assert _read(s) == 10 + 15


def test_earlier_start_in_auto_mode_never_raises_the_count():
    """S2 / r5 case 3: auto meters ONE routing adapter; history (every physical adapter - a docked
    laptop's Wi-Fi LAN traffic too) cannot raise the cap above the odometer: it read 55 vs ~33."""
    reader = _Reader({date(2026, 10, 5): 55, date(2026, 10, 10): 50})
    s = _state(10, "2026-10-10", reader, interface_mode="auto", used=30)
    s.config["data_cap_reset_day"] = 5
    assert _read(s) == 30


def test_toggling_the_reset_day_back_restores_the_count():
    """L1 / r5 case 2: 10 -> 15 -> 10 lost the 5 GB history never saw (30 -> 13.6 -> 25)."""
    per_day = {date(2026, 10, d): (21 - d) * 25 / 11 for d in range(1, 22)}
    s = _state(10, "2026-10-10", _Reader(per_day), used=30)
    s.config["data_cap_reset_day"] = 15
    first = _read(s)
    assert first < 30
    s.config["data_cap_reset_day"] = 10
    assert _read(s) == 30


def test_reset_day_change_is_persisted_once():
    s = _state(15, "2026-10-15")
    _poll(s, datetime(2026, 10, 21, 8), 1 * GB)
    s.db_worker.reset_mock()
    s.config["data_cap_reset_day"] = 10
    WidgetState.get_usage_this_period(s)
    WidgetState.get_usage_this_period(s)
    payloads = [c.args[1] for c in s.db_worker.enqueue_task.call_args_list if c.args[0] == "persist_usage"]
    assert len(payloads) == 1 and payloads[0][4] == "2026-10-10"


def test_clamped_reset_day_is_not_a_change():
    """Reset day 31 is clamped to 28: a stored key on the 28th is the SAME reset day."""
    s = _state(31, "2026-09-28", lifetime=0.0)
    _poll(s, datetime(2026, 10, 21, 8), 3 * GB)
    _poll(s, datetime(2026, 10, 21, 9), 3 * GB)
    assert _read(s) == 6 and s._usage["period_key"] == "2026-09-28"


# --- F2: a clock that was AHEAD (h2) ---------------------------------------------------------------

def test_one_poll_with_the_clock_ahead_does_not_freeze_the_period():
    """h2: was stuck at period 2027-02-15 and 1.0 GB until March 2027. This session saw the jump, so
    the period gets its pre-jump anchor back: nothing is lost."""
    s = _state(15, "2026-10-15", lifetime=10.0, used=10)
    _poll(s, datetime(2026, 10, 21, 9), 1 * GB)
    assert _read(s) == 11
    _poll(s, datetime(2027, 3, 2, 9), 1 * GB)                         # one poll, clock ahead
    assert s._usage["period_key"] == "2027-02-15"
    for d in range(21, 32):                                            # clock corrected
        _poll(s, datetime(2026, 10, d, 12), 5 * GB)
    assert s._usage["period_key"] == "2026-10-15"
    assert _read(s) == 11 + 1 + 55
    for d in range(1, 30):
        _poll(s, datetime(2026, 11, d, 12), 5 * GB)
    assert s._usage["period_key"] == "2026-11-15"                     # it rolls over again on time
    assert _read(s) == 15 * 5


def test_future_key_from_an_earlier_session_needs_the_trusted_stamp():
    """Persisted by a previous session: without evidence the clock is right now, nothing changes."""
    s = _state(15, "2027-02-15", used=3)
    assert _read(s, date(2026, 10, 21)) == 3 and s._usage["period_key"] == "2027-02-15"
    s = _state(15, "2027-02-15", _Reader(stamp=int(time.time())), used=3)
    assert _read(s) == 3 and s._usage["period_key"] == "2026-10-15"


def test_future_key_rekey_is_raised_to_history_where_it_is_exact():
    """S6: the bytes before the jump (a previous session) come back from history - only where history
    meters exactly what the odometer does."""
    stamp = int(time.time())
    s = _state(15, "2027-02-15", _Reader({date(2026, 10, 15): 9}, stamp=stamp), interface_mode="selected", used=3)
    assert _read(s) == 9
    s = _state(15, "2027-02-15", _Reader({date(2026, 10, 15): 9}, stamp=stamp), interface_mode="auto", used=3)
    assert _read(s) == 3


def test_future_key_is_logged_once(caplog):
    s = _state(15, "2027-02-15", _Reader(stamp=int(time.time())))
    with caplog.at_level(logging.WARNING, logger="test.rekey"):
        _poll(s, datetime(2026, 10, 21, 12), 1 * GB)
        s._usage["period_key"] = "2027-05-15"
        _poll(s, datetime(2026, 10, 21, 13), 1 * GB)
    assert s._usage["period_key"] == "2026-10-15"
    assert sum("starts in the future" in r.message for r in caplog.records) == 1


@pytest.mark.parametrize("today", [date(2026, 10, 14), date(2026, 10, 13)])
def test_a_clock_a_little_slow_at_the_boundary_keeps_the_period(today):
    s = _state(15, "2026-10-15", lifetime=0.0)
    _poll(s, datetime(2026, 10, 15, 9), 3 * GB)
    _poll(s, datetime(today.year, today.month, today.day, 23), 1 * GB)
    assert s._usage["period_key"] == "2026-10-15"
    assert _read(s) == 4


# --- B1: a clock that is BEHIND (r1) -----------------------------------------------------------------

@pytest.mark.parametrize("wrong", [datetime(2009, 1, 1, 0, 5), datetime(2026, 10, 10, 9)])
def test_a_clock_behind_never_wipes_the_period_once_corrected(wrong):
    """r1: a dead CMOS battery / a VM snapshot restore. The stored period then looks like it starts in
    the future; re-keying away and anchoring at the total when NTP corrected the clock left 1.0 GB of
    31. No evidence the clock is right (no stamp, no jump seen): the period stays."""
    s = _state(15, "2026-10-15", used=30)
    _poll(s, wrong, 0.0)
    assert s._usage["period_key"] == "2026-10-15"
    _poll(s, datetime(2026, 10, 21, 9, 1), 1 * GB)
    assert _read(s) == 31


def test_a_clock_behind_its_trusted_stamp_does_not_rekey():
    s = _state(15, "2026-10-15", _Reader(stamp=int(time.time()) + 30 * 86400), used=30)
    _poll(s, datetime(2026, 10, 10, 9), 0.0)
    assert s._usage["period_key"] == "2026-10-15"


def test_a_rekey_the_next_rollover_proves_wrong_is_undone():
    """Evidence can be wrong (an old stamp). Re-keyed away from 10-15 on a clock behind; the clock is
    then corrected and rolls forward onto exactly 10-15 - far sooner than real time could have - so the
    re-key was wrong and the period's anchor comes back."""
    s = _state(15, "2026-10-15", _Reader(stamp=0), used=30)
    _poll(s, datetime(2026, 10, 10, 9), 0.0)
    assert s._usage["period_key"] == "2026-09-15"                     # re-keyed on the stale evidence
    _poll(s, datetime(2026, 10, 21, 9, 1), 1 * GB)
    assert s._usage["period_key"] == "2026-10-15"
    assert _read(s) == 31


# --- second review: N1, L-b, L-e -------------------------------------------------------------------

def test_a_usage_reset_never_lets_an_old_anchor_back():
    """N1 / r7: 5 GB used, 10 -> 15, usage reset, 6 GB used, 15 -> 10: the pre-reset anchor was
    restored onto the new, smaller total (cap 3.0 GB, not 6). Only the same running total may get a
    remembered anchor back."""
    s = _state(10, "2026-10-10", lifetime=8.0, used=5)
    s._usage.update(cumulative_up=0.0, anchor_up=0.0)                 # r7: a download-only total
    s.config["data_cap_reset_day"] = 15
    assert _read(s) == 5
    s._usage = {"cumulative_up": 0.0, "cumulative_down": 0.0, "anchor_up": 0.0, "anchor_down": 0.0,
                "period_key": "2026-10-15"}                            # the reset's effect, by hand
    for _ in range(6):
        WidgetState._add_usage_loaded(s, 0.0, 1 * GB, None, None, None, None)
    s.config["data_cap_reset_day"] = 10
    assert _read(s) == 6


def test_the_real_usage_reset_clears_the_memory():
    s = _state(10, "2026-10-10", lifetime=8.0, used=5)
    s.config["data_cap_reset_day"] = 15
    _read(s)
    assert WidgetState._usage_left_periods(s)
    s._usage_reset_pending, s._usage_reset_stash, s._usage_post_reset = True, [], [0.0, 2 * GB, None]
    WidgetState._on_worker_history_deleted(s, "deleted")
    assert s._usage_left == {}
    payload = s.db_worker.enqueue_task.call_args_list[-1].args[1]
    assert payload[7] == ""                                            # the stored memory goes too


def test_a_jump_the_writer_stamped_as_trusted_rekeys_once_the_stamp_agrees():
    """L-b, accepted cost (P1): a few days' jump inside a session is trusted by the history writer (it
    looks like sleep), so its stamp is AHEAD after the correction. The stamp decides: the early rollover
    waits until the writer re-accepts the clock, counting on meanwhile, and is then undone exactly."""
    reader = _Reader(stamp=int(time.time()) + 10 * 86400)
    s = _state(15, "2026-10-15", reader, lifetime=10.0, used=10)
    _poll(s, datetime(2026, 10, 21, 9), 1 * GB)
    _poll(s, datetime(2026, 11, 20, 9), 1 * GB)                       # crosses Nov 15: early rollover
    assert s._usage["period_key"] == "2026-11-15"
    _poll(s, datetime(2026, 10, 21, 10), 1 * GB)                      # corrected; the stamp is ahead
    assert s._usage["period_key"] == "2026-11-15" and _read(s) == 2   # waits, keeps counting
    reader.stamp = int(time.time())                                    # the writer re-accepted the clock
    assert _read(s) == 13 and s._usage["period_key"] == "2026-10-15"   # 10 + three 1 GB polls


class _RealClock:
    """``time.time`` for the r8 scenarios (the writer's stamp and the wall clock move together)."""
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.mark.parametrize("restart", [True, False])
def test_a_date_set_back_across_a_reset_day_never_merges_or_wipes_periods(restart):
    """P1 / r8 + r8b: a legit rollover Sep 15 -> Oct 15 leaves a 'rollover' entry for Sep 15. With the
    date then set back to Oct 9 (by hand, a VM restore) while the writer's stamp is real, that entry
    overrode the stamp: the cap re-keyed backwards and showed both periods merged (30 -> 50 GB), and
    after a restart and the correction the next rollover wiped the period (1.0 GB, should be 31)."""
    real = datetime(2026, 10, 21, 12).timestamp()
    clock = _RealClock(real - 7 * 86400)
    reader = _Reader()
    reader.clock_trusted_ts = lambda: int(stamp[0])
    stamp = [clock.t]
    with patch.object(wsmod.time, "time", clock):
        s = _state(15, "2026-09-15", reader, lifetime=400.0, used=20)
        _poll(s, datetime(2026, 10, 15, 9), 1 * GB)                   # legit rollover
        for _ in range(29):
            _poll(s, datetime(2026, 10, 18, 9), 1 * GB)
        clock.t = stamp[0] = real
        assert _read(s, date(2026, 10, 21)) == 30
        clock.t = real - 12 * 86400                                    # date set back to Oct 9
        assert _read(s, date(2026, 10, 9)) == 30                      # was 50: two periods merged
        assert s._usage["period_key"] == "2026-10-15"
        if restart:
            s._usage_left = WidgetState._decode_left_periods(
                WidgetState._encode_left_periods(WidgetState._usage_left_periods(s)))
        clock.t = real + 60
        _poll(s, datetime(2026, 10, 21, 12, 1), 1 * GB)
        assert s._usage["period_key"] == "2026-10-15"
        assert _read(s) == 31                                          # was 1.0 after a restart


def test_a_memory_from_a_larger_total_is_dropped_on_load():
    """Hardening: a memory taken on a total larger than the one loaded (a usage reset by a build that
    did not clear it - 2.2 beta.3) never restores an anchor of a count that no longer exists."""
    from netspeedtray.core.db.history_reader import UsageRecord
    memory = WidgetState._encode_left_periods({
        "2026-10-10": {"au": 0.0, "ad": 3 * GB, "cu": 0.0, "cd": 8 * GB, "mono": None, "why": "reset_day"},
        "2026-09-15": {"au": 0.0, "ad": 1 * GB, "cu": 0.0, "cd": 2 * GB, "mono": None, "why": "rollover"}})
    rec = UsageRecord((0.0, 6 * GB, 0.0, 0.0, "2026-10-15"), {}, memory)
    s = _state(15, "2026-10-15")
    s.history_reader = types.SimpleNamespace(load_usage=lambda: rec)
    s._usage_loaded = False
    assert WidgetState._try_load_usage(s)
    assert list(s._usage_left) == ["2026-09-15"]                       # 8 GB > 6 GB: gone
    s.config["data_cap_reset_day"] = 10
    assert _read(s) == 6


def test_the_memory_survives_a_restart():
    """L-e: the reset-day toggle-back restore no longer needs the same session."""
    s = _state(10, "2026-10-10", lifetime=100.0, used=30)
    s.config["data_cap_reset_day"] = 15
    _read(s)
    payload = s.db_worker.enqueue_task.call_args_list[-1].args[1]
    assert payload[7]                                                  # persisted with the total
    from netspeedtray.core.db.history_reader import UsageRecord
    u = s._usage
    rec = UsageRecord((u["cumulative_up"], u["cumulative_down"], u["anchor_up"], u["anchor_down"],
                       u["period_key"]), {}, payload[7])
    s2 = _state(15, u["period_key"])                                   # next launch
    s2.history_reader = types.SimpleNamespace(load_usage=lambda: rec)
    s2._usage_loaded = False
    assert WidgetState._try_load_usage(s2)
    s2.config["data_cap_reset_day"] = 10
    assert _read(s2) == 30


def test_a_garbled_memory_is_ignored():
    assert WidgetState._decode_left_periods("{not json") == {}
    assert WidgetState._decode_left_periods('{"2026-10-10": [1, 2, 3, 4, "future"]}') == {}
    assert WidgetState._decode_left_periods('{"x": [1, 2, 3, 4, "reset_day"]}') == {}
    assert WidgetState._decode_left_periods(None) == {}


@pytest.mark.parametrize("include_usage", [True, False])
def test_the_memory_round_trips_through_the_database_and_goes_with_a_usage_reset(tmp_path, include_usage):
    import sqlite3
    from netspeedtray import constants
    from netspeedtray.core.database import DatabaseWorker
    from netspeedtray.core.db.history_reader import V8HistoryReader
    db = tmp_path / "speed_history.db"
    w = DatabaseWorker(db)
    w._prepare_storage()
    try:
        memory = '{"2026-10-10":[470000000000.0,0,500000000000.0,0,"reset_day"]}'
        w._persist_usage((1.0, 2.0, 0.0, 0.0, "2026-10-15", 0, None, memory))
        reader = V8HistoryReader(lambda: sqlite3.connect(db), lambda: db)
        assert reader.load_usage().rekey_memory == memory
        w._persist_usage((1.0, 2.0, 0.0, 0.0, "2026-10-15", 0, None, None))     # None: untouched
        assert reader.load_usage().rekey_memory == memory
        w._writer.delete_history(include_usage=include_usage)
        key = constants.network.usage.REKEY_MEMORY_METADATA_KEY
        left = w.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        assert (left is None) == include_usage                     # "keep usage" keeps its memory
        w._persist_usage((1.0, 2.0, 0.0, 0.0, "2026-10-15", 0, None, ""))       # "": deleted
        assert w.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone() is None
    finally:
        w._close_connection()
