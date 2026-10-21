"""
The odometer's period key under a reset-day change and a wall clock that ran ahead (2.2 RC checkup,
F1 + F2; harnesses h3_reset_day_change / h2_odometer_future_clock).

F1: the stored period key only moved forward. After the data-cap reset day changed, the key computed
from the NEW day could be older than the stored one (15 -> 10 on Oct 21: stored 2026-10-15, computed
2026-10-10), so "the window began in an already-closed period" fired on every poll and nothing counted
toward the cap: "used this period" froze. A reset-day change is now an explicit re-key, seeded from the
metered adapters' recorded history where it can be.

F2: one poll with the clock ahead advanced the stored key into the future and the period then never
reset until that date. A stored period start more than two days after today is impossible: re-key.
Real WidgetState code throughout; only ``date`` (and, for F2 tests, nothing else) is patched.
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
    """Recorded history: ``down_since[start date]`` bytes down (and a tenth of that up) since it."""
    def __init__(self, down_since):
        self.down_since = down_since
        self.calls = []

    def metered_volume(self, start, end, scope, poll):
        self.calls.append((start, scope))
        d = float(self.down_since.get(start.date(), 0.0))
        return VolumeResult(d / 10, d, d / 10, d, False)


def _state(reset_day, key, reader=None, interface_mode="auto"):
    s = types.SimpleNamespace()
    s.config = {"data_cap_reset_day": reset_day, "interface_mode": interface_mode}
    s.db_worker = MagicMock()
    s.logger = logging.getLogger("test.rekey")
    s._usage = {"cumulative_up": 0.0, "cumulative_down": 0.0, "anchor_up": 0.0, "anchor_down": 0.0,
                "period_key": key}
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


def _used_down(s):
    return round(WidgetState.get_usage_this_period(s)[1] / GB, 3)


# --- F1: the h3 scenarios -----------------------------------------------------------------------

@pytest.mark.parametrize("old_day, key, new_day, change_on, days, expected", [
    (15, "2026-10-15", 10, 21, range(21, 32), 10 + 11 * 2),   # new period starts BEFORE the old one
    (1, "2026-10-01", 25, 5, range(5, 25), 10 + 20 * 2),      # 1 -> 25 on the 5th: Sep 25 period
])
def test_cap_keeps_counting_after_a_reset_day_change(old_day, key, new_day, change_on, days, expected):
    """Was frozen at 10.0 GB (h3). No history read here: the new period contains the old one, so what
    was already counted is kept (a lower bound) and every later poll counts."""
    s = _state(old_day, key)
    _poll(s, datetime(2026, 10, change_on, 8), 10 * GB)
    assert _used_down(s) == 10
    s.config["data_cap_reset_day"] = new_day
    for d in days:
        _poll(s, datetime(2026, 10, d, 12), 2 * GB)
    assert _used_down(s) == expected
    assert s._usage["period_key"] == WidgetState._compute_period_key(new_day, FakeDate.today_value)
    assert s._usage["cumulative_down"] == expected * GB              # lifetime unchanged by the re-key


def test_alerts_see_the_new_period_key_at_once():
    s = _state(15, "2026-10-15")
    s.config["data_cap_reset_day"] = 10
    assert WidgetState.get_usage_period_key(s) == "2026-10-10"


def test_later_start_is_seeded_from_history_so_cap_and_this_period_agree():
    """1 -> 15 on Oct 21: the period now began Oct 15. It used to re-anchor at 'now' (cap 0 GB while
    'This Period' showed Oct 15-21's traffic); it now reads the metered history since Oct 15."""
    reader = _Reader({date(2026, 10, 15): 7 * GB})
    s = _state(1, "2026-10-01", reader)
    _poll(s, datetime(2026, 10, 21, 8), 30 * GB)                      # 30 GB counted since Oct 1
    s.config["data_cap_reset_day"] = 15
    assert _used_down(s) == 7
    assert reader.calls[0][0] == datetime(2026, 10, 15)
    assert reader.calls[0][1] == WidgetState.metered_scope(s.config)  # the cap's adapters only
    _poll(s, datetime(2026, 10, 21, 9), 1 * GB)
    assert _used_down(s) == 8


def test_later_start_never_exceeds_what_the_odometer_counted():
    reader = _Reader({date(2026, 10, 15): 50 * GB})                   # history says more than the cap saw
    s = _state(1, "2026-10-01", reader)
    _poll(s, datetime(2026, 10, 21, 8), 30 * GB)
    s.config["data_cap_reset_day"] = 15
    assert _used_down(s) == 30


def test_later_start_without_history_counts_from_now():
    s = _state(1, "2026-10-01")                                        # no history reader at all
    _poll(s, datetime(2026, 10, 21, 8), 30 * GB)
    s.config["data_cap_reset_day"] = 15
    assert _used_down(s) == 0
    _poll(s, datetime(2026, 10, 21, 9), 1 * GB)
    assert _used_down(s) == 1


def test_earlier_start_takes_history_when_it_holds_more():
    """15 -> 10 on Oct 21: the period now began Oct 10 and holds Oct 10-14 too."""
    reader = _Reader({date(2026, 10, 10): 25 * GB})
    s = _state(15, "2026-10-15", reader)
    s._usage.update(cumulative_up=500 * GB, cumulative_down=500 * GB, anchor_up=500 * GB,
                    anchor_down=500 * GB)                             # a lifetime from earlier periods
    _poll(s, datetime(2026, 10, 21, 8), 10 * GB)
    s.config["data_cap_reset_day"] = 10
    assert _used_down(s) == 25


def test_earlier_start_never_drops_below_what_was_counted():
    reader = _Reader({date(2026, 10, 10): 4 * GB})                    # history missed recovered bytes
    s = _state(15, "2026-10-15", reader)
    _poll(s, datetime(2026, 10, 21, 8), 10 * GB)
    s.config["data_cap_reset_day"] = 10
    assert _used_down(s) == 10


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
    s = _state(31, "2026-09-28")
    _poll(s, datetime(2026, 10, 21, 8), 3 * GB)
    _poll(s, datetime(2026, 10, 21, 9), 3 * GB)
    assert _used_down(s) == 6 and s._usage["period_key"] == "2026-09-28"
