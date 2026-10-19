"""
Data-usage odometer accuracy (Docs/reference/data-usage-accuracy-audit.md, work-package items 1-3
plus the IPv6-only zero count from item 5). Decision D5: the odometer is authoritative for volume
claims, so every byte the NIC counters carry must reach it - and nothing they don't.

1. Count across gaps - a long poll gap (Modern Standby, the app suspended) used to re-prime and
   throw away the gap's byte delta plus two more polls. The cumulative counters carry the exact
   answer across the gap; only the RATE display has a reason to re-prime.
2. Same-boot launch reconciliation - the counters are cumulative since boot, so the bytes that
   moved while the app was closed are recoverable within one boot session.
3. Per-poll sanity cap - a garbage counter jump must not add unbounded phantom gigabytes.
5b. An IPv6-only primary must resolve, or auto mode meters zero forever.
"""
import json
import logging
import socket
import sqlite3
import time
from collections import namedtuple
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core.controller import StatsController
from netspeedtray.core.widget_state import WidgetState

MAX_BPS = constants.network.interface.MAX_REASONABLE_SPEED_BPS


class NetIO:
    """psutil snetio stand-in (only the two fields the controller reads)."""
    def __init__(self, sent: int, recv: int):
        self.bytes_sent = sent
        self.bytes_recv = recv


class FakeState:
    """Records what the controller feeds the odometer. A real class (not a MagicMock) so the
    launch-snapshot handshake returns real values."""
    def __init__(self, snapshot: Optional[dict] = None, loaded: bool = True):
        self.adds: List[Tuple[float, float]] = []
        self.add_kwargs: List[Dict[str, Any]] = []
        self.speed_calls = 0
        self._snapshot = snapshot
        self.loaded = loaded

    def add_usage_bytes(self, up, down, **kwargs):
        self.adds.append((up, down))
        self.add_kwargs.append(kwargs)

    def add_speed_data(self, *a, **k):
        self.speed_calls += 1

    def add_net_sample(self, ts, dt_ms, byte_deltas, **k):
        # The v8 history feed: the SAME exact deltas the odometer gets, per interface.
        self.net_samples = getattr(self, "net_samples", [])
        self.net_samples.append((ts, dt_ms, dict(byte_deltas)))

    def get_usage_launch_snapshot(self):
        if not self.loaded:
            return None
        return self._snapshot or {}

    def totals(self) -> Tuple[float, float]:
        return (sum(a[0] for a in self.adds), sum(a[1] for a in self.adds))


def _controller(state, mode="all_physical", **cfg) -> StatsController:
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    config["interface_mode"] = mode
    config["update_rate"] = 1.0
    config.update(cfg)
    c = StatsController(config=config, widget_state=state)
    c.set_view(MagicMock())
    return c


def _poll(c: StatsController, counters, mono: float):
    with patch("time.monotonic", return_value=mono):
        c._handle_network_counters(counters)


# --- 1. count across gaps ------------------------------------------------------------------

def test_two_hour_gap_adds_exactly_the_counter_delta():
    state = FakeState()
    c = _controller(state)
    t0 = 1000.0
    _poll(c, {"Wi-Fi": NetIO(1_000, 2_000)}, t0)
    # Two hours of Modern Standby: the network stack kept moving bytes, the poll loop did not run.
    _poll(c, {"Wi-Fi": NetIO(1_000 + 5_000_000_000, 2_000 + 40_000_000_000)}, t0 + 7200.0)
    assert state.totals() == (5_000_000_000, 40_000_000_000)


def test_gap_and_the_two_reprime_polls_lose_nothing():
    """The old gate discarded the gap AND the next two polls. Every byte between the first and the
    last sample must reach the odometer."""
    state = FakeState()
    c = _controller(state)
    t0 = 1000.0
    _poll(c, {"Wi-Fi": NetIO(0, 0)}, t0)
    _poll(c, {"Wi-Fi": NetIO(1_000_000, 9_000_000)}, t0 + 600.0)   # gap
    _poll(c, {"Wi-Fi": NetIO(1_100_000, 9_500_000)}, t0 + 601.0)   # reprime 1
    _poll(c, {"Wi-Fi": NetIO(1_150_000, 9_700_000)}, t0 + 602.0)   # reprime 2
    _poll(c, {"Wi-Fi": NetIO(1_200_000, 9_800_000)}, t0 + 603.0)   # normal
    assert state.totals() == (1_200_000, 9_800_000)


def test_auto_mode_counts_the_gap_even_when_the_route_is_not_back_yet():
    """Waking from standby: the poll carrying the whole standby window is the one where the
    routing lookup fails (no route yet). The gap's bytes go to the last primary that WAS found."""
    state = FakeState()
    c = _controller(state, mode="auto")
    t0 = 1000.0
    with patch("netspeedtray.core.controller.get_primary_interface_name", return_value="Wi-Fi"):
        _poll(c, {"Wi-Fi": NetIO(0, 0)}, t0)
        _poll(c, {"Wi-Fi": NetIO(100, 100)}, t0 + 1.0)
    with patch("netspeedtray.core.controller.get_primary_interface_name", return_value=None):
        _poll(c, {"Wi-Fi": NetIO(2_000_100, 7_000_100)}, t0 + 3600.0)
    assert state.totals() == (2_000_100, 7_000_100)


def test_rate_display_still_reprimes_after_a_gap():
    """Only the odometer changed: the display still shows 0 for the gap poll and the two reprime
    polls (a rate across a gap is meaningless), and no speed sample is recorded for them."""
    state = FakeState()
    c = _controller(state)
    view = c.view
    t0 = 1000.0
    _poll(c, {"Wi-Fi": NetIO(0, 0)}, t0)
    for i, (up, down) in enumerate([(5_000_000, 5_000_000), (5_100_000, 5_100_000),
                                    (5_200_000, 5_200_000)]):
        _poll(c, {"Wi-Fi": NetIO(up, down)}, t0 + 600.0 + i)
        assert view.update_display_speeds.call_args[0] == (0.0, 0.0)
    assert state.speed_calls == 0
    _poll(c, {"Wi-Fi": NetIO(5_300_000, 5_300_000)}, t0 + 603.0)
    assert state.speed_calls == 1
    assert view.update_display_speeds.call_args[0][0] > 0


def test_counter_reset_clamp_is_per_direction():
    """Finding 6: real NICs restart their counters without a reboot. A reset in one direction adds
    zero for that direction and must not discard the other direction's real bytes."""
    state = FakeState()
    c = _controller(state)
    t0 = 1000.0
    _poll(c, {"Wi-Fi": NetIO(10_000_000, 10_000_000)}, t0)
    _poll(c, {"Wi-Fi": NetIO(500, 10_250_000)}, t0 + 1.0)            # upload counter reset
    assert state.adds[-1] == (0.0, 250_000.0)
    # ...and across a gap too: the reset clamp runs before the gate now.
    _poll(c, {"Wi-Fi": NetIO(400, 10_750_000)}, t0 + 900.0)
    assert state.adds[-1] == (0.0, 500_000.0)


# --- 3. per-poll sanity cap ----------------------------------------------------------------

def test_a_10_tb_glitch_in_one_second_is_capped():
    state = FakeState()
    c = _controller(state)
    t0 = 1000.0
    _poll(c, {"Ethernet": NetIO(0, 0)}, t0)
    _poll(c, {"Ethernet": NetIO(10 * 10**12, 3_000_000)}, t0 + 1.0)
    up, down = state.adds[-1]
    assert up == pytest.approx(MAX_BPS * 1.0)        # clipped to the 100 Gbps ceiling x 1 s
    assert down == 3_000_000                          # the other direction is untouched


def test_the_cap_logs_once_per_session(caplog):
    state = FakeState()
    c = _controller(state)
    t0 = 1000.0
    _poll(c, {"Ethernet": NetIO(0, 0)}, t0)
    with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.StatsController"):
        _poll(c, {"Ethernet": NetIO(10 * 10**12, 0)}, t0 + 1.0)
        _poll(c, {"Ethernet": NetIO(20 * 10**12, 0)}, t0 + 2.0)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "cap" in r.message.lower()]
    assert len(warnings) == 1


def test_cap_uses_the_sampling_span_not_the_handling_span():
    """#13's real mechanism: the GUI thread can handle two samples closer together than they were
    taken (a late delivery of the previous one). The display rate is then inflated past the ceiling
    and dropped, but the bytes are real - the cap is measured over when the counters were SAMPLED."""
    state = FakeState()
    c = _controller(state)
    t0 = 1000.0
    with patch("time.monotonic", return_value=t0):
        c._handle_network_counters({"Wi-Fi": NetIO(0, 0)}, sample_time=(t0 - 2.0, 5_000.0))
    huge = 30 * 1024**3   # 30 GiB: over the ceiling for the 1 s handling span, under it for 3 s sampled
    with patch("time.monotonic", return_value=t0 + 1.0):
        c._handle_network_counters({"Wi-Fi": NetIO(huge, 0)}, sample_time=(t0 + 1.0, 5_003.0))
    assert state.speed_calls == 0                    # over-ceiling rate -> display-dropped
    assert state.adds[-1] == (huge, 0)                # ...but every byte counted


# --- 2. same-boot launch reconciliation ---------------------------------------------------

BOOT = 1_760_000_000.0


def _snapshot(counters: Dict[str, Tuple[int, int]], age_s: float = 3600.0, boot: float = BOOT) -> dict:
    return {"v": 1, "boot_time": boot, "ts": time.time() - age_s,
            "counters": {k: [v[0], v[1]] for k, v in counters.items()}}


def _launch(state, counters, boot_now=BOOT + 0.6, mode="all_physical", **cfg):
    c = _controller(state, mode=mode, **cfg)
    with patch("netspeedtray.core.usage_snapshot.psutil.boot_time", return_value=boot_now):
        _poll(c, counters, 1000.0)
    return c


def test_restart_within_the_same_boot_recovers_the_delta():
    state = FakeState(_snapshot({"Wi-Fi": (1_000, 2_000)}))
    _launch(state, {"Wi-Fi": NetIO(1_000 + 1_000_000_000, 2_000 + 3_000_000_000)})
    assert state.totals() == (1_000_000_000, 3_000_000_000)


def test_a_new_boot_adds_nothing():
    state = FakeState(_snapshot({"Wi-Fi": (1_000, 2_000)}))
    _launch(state, {"Wi-Fi": NetIO(9_000_000_000, 9_000_000_000)}, boot_now=BOOT + 86_400.0)
    assert state.totals() == (0, 0)


def test_counters_that_went_backwards_add_nothing():
    """Same boot but the adapter restarted its counters while the app was closed: the delta is
    unknowable, so this interface adds nothing (both directions - they reset together)."""
    state = FakeState(_snapshot({"Wi-Fi": (5_000_000, 5_000_000)}))
    _launch(state, {"Wi-Fi": NetIO(100, 9_000_000)})
    assert state.totals() == (0, 0)


def test_reconcile_counts_only_interfaces_the_current_mode_meters():
    state = FakeState(_snapshot({"Wi-Fi": (0, 0), "Ethernet": (0, 0)}))
    _launch(state, {"Wi-Fi": NetIO(100, 200), "Ethernet": NetIO(7_000, 8_000)},
            mode="selected", selected_interfaces=["Wi-Fi"])
    assert state.totals() == (100, 200)


def test_reconcile_skips_an_interface_that_is_gone():
    state = FakeState(_snapshot({"Wi-Fi": (0, 0), "Ethernet": (0, 0)}))
    _launch(state, {"Wi-Fi": NetIO(100, 200)})
    assert state.totals() == (100, 200)


def test_reconcile_is_capped_by_the_time_since_the_snapshot():
    state = FakeState(_snapshot({"Wi-Fi": (0, 0)}, age_s=2.0))
    _launch(state, {"Wi-Fi": NetIO(10 * 10**12, 10)})
    up, down = state.totals()
    assert up <= MAX_BPS * 3.0          # ~2 s elapsed (plus test runtime) x ceiling, not 10 TB
    assert down == 10


def test_reconcile_waits_for_the_odometer_and_runs_exactly_once():
    """If the odometer isn't loaded at the first poll, reconcile later against the LAUNCH counters
    (the live polls count from there), and only once."""
    state = FakeState(_snapshot({"Wi-Fi": (0, 0)}), loaded=False)
    c = _controller(state)
    with patch("netspeedtray.core.usage_snapshot.psutil.boot_time", return_value=BOOT):
        _poll(c, {"Wi-Fi": NetIO(1_000, 1_000)}, 1000.0)
        assert state.adds == []
        state.loaded = True
        _poll(c, {"Wi-Fi": NetIO(1_500, 1_500)}, 1001.0)
        _poll(c, {"Wi-Fi": NetIO(1_700, 1_700)}, 1002.0)
    # 1,000 recovered + 500 + 200 live, and the reconcile did not run a second time.
    assert state.totals() == (1_700, 1_700)


def test_the_odometer_is_told_which_counters_its_total_matches():
    """The snapshot persisted with the cumulative must be the counters that total was counted up to
    - otherwise the next launch double-counts or loses the tail."""
    state = FakeState()
    c = _controller(state)
    _poll(c, {"Wi-Fi": NetIO(0, 0)}, 1000.0)
    latest = {"Wi-Fi": NetIO(10, 20)}
    _poll(c, latest, 1001.0)
    assert state.add_kwargs[-1]["counters"] is latest


# --- WidgetState: snapshot persisted with the cumulative, loaded once --------------------

def _fake_widget_state():
    import types
    s = types.SimpleNamespace()
    s.config = {"data_cap_reset_day": 1}
    s.db_worker = MagicMock()
    s.logger = logging.getLogger("test.usage")
    s._usage = {"cumulative_up": 0.0, "cumulative_down": 0.0,
                "anchor_up": 0.0, "anchor_down": 0.0, "period_key": "2026-06-01"}
    s._usage_loaded = True
    s._usage_last_persist = 0.0
    s._usage_live_counters = None
    s._usage_live_sampled_at = None
    s._usage_live_primary = None
    s._usage_launch_snapshot = {}
    # "Today" is in the June period; an explicit date (a measurement window's start) uses the real math.
    s._compute_period_key = lambda rd, today=None: (
        "2026-06-01" if today is None else WidgetState._compute_period_key(rd, today))
    s._persist_usage_now = WidgetState._persist_usage_now.__get__(s)
    s._maybe_reanchor = WidgetState._maybe_reanchor.__get__(s)
    return s


def test_persist_carries_the_counter_snapshot():
    s = _fake_widget_state()
    with patch("netspeedtray.core.usage_snapshot.psutil.boot_time", return_value=BOOT):
        WidgetState.add_usage_bytes(s, 10.0, 20.0, counters={"Wi-Fi": NetIO(111, 222)}, sampled_at=1234.5)
    task, payload = s.db_worker.enqueue_task.call_args[0]
    assert task == "persist_usage"
    snap = json.loads(payload[6])
    assert snap["boot_time"] == BOOT and snap["ts"] == 1234.5
    assert snap["counters"] == {"Wi-Fi": [111, 222]}


# --- DB level, through the real DatabaseWorker in a temp dir -----------------------------

def _session(tmp_path, config):
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        return WidgetState(config)


def test_restart_recovery_through_the_real_database_worker(tmp_path, q_app):
    """End to end: session 1 counts and persists (cumulative + counter snapshot in ONE transaction
    on the DB thread); session 2 launches in the same boot and recovers what moved in between."""
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    config["interface_mode"] = "all_physical"
    config["update_rate"] = 1.0
    db = tmp_path / "speed_history.db"

    with patch("netspeedtray.core.usage_snapshot.psutil.boot_time", return_value=BOOT):
        s1 = _session(tmp_path, config)
        try:
            for _ in range(100):                      # wait for the worker to create the schema
                if s1._try_load_usage():
                    break
                time.sleep(0.02)
            c1 = StatsController(config=config, widget_state=s1)
            _poll(c1, {"Wi-Fi": NetIO(1_000, 1_000)}, 1000.0)
            _poll(c1, {"Wi-Fi": NetIO(4_000, 6_000)}, 1001.0)            # +3,000 / +5,000 live
        finally:
            s1.cleanup()                              # final persist drains on the worker thread

        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = ro.execute("SELECT value FROM metadata WHERE key = ?",
                             (constants.network.usage.SNAPSHOT_METADATA_KEY,)).fetchone()
            cum = ro.execute(f"SELECT cumulative_up, cumulative_down FROM "
                             f"{constants.data.USAGE_COUNTER_TABLE} WHERE id = 1").fetchone()
        finally:
            ro.close()
        assert json.loads(row[0])["counters"] == {"Wi-Fi": [4_000, 6_000]}
        assert cum == (3_000.0, 5_000.0)

        s2 = _session(tmp_path, config)
        try:
            # Reads are held off until the worker has opened (recover / migrate) the file; this test
            # is about the reconciliation, not that window (test_bytes_before_the_odometer_loads_...).
            assert s2.db_worker.ready.wait(10)
            c2 = StatsController(config=config, widget_state=s2)
            # The app was closed while 1 MB up / 2 MB down moved.
            _poll(c2, {"Wi-Fi": NetIO(1_004_000, 2_006_000)}, 5000.0)
            assert s2.get_usage_this_period() == (1_003_000.0, 2_005_000.0)
        finally:
            s2.cleanup()

        # A third launch with NO traffic since must add nothing - the snapshot moved with the total.
        s3 = _session(tmp_path, config)
        try:
            assert s3.db_worker.ready.wait(10)
            c3 = StatsController(config=config, widget_state=s3)
            _poll(c3, {"Wi-Fi": NetIO(1_004_000, 2_006_000)}, 9000.0)
            assert s3.get_usage_this_period() == (1_003_000.0, 2_005_000.0)
        finally:
            s3.cleanup()


# --- PR #348 review ------------------------------------------------------------------------

def _usage_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(f"""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE {constants.data.USAGE_COUNTER_TABLE} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cumulative_up REAL NOT NULL DEFAULT 0, cumulative_down REAL NOT NULL DEFAULT 0,
            anchor_up REAL NOT NULL DEFAULT 0, anchor_down REAL NOT NULL DEFAULT 0,
            period_key TEXT NOT NULL DEFAULT '', updated_ts INTEGER NOT NULL DEFAULT 0);
    """)
    import types
    from netspeedtray.core.database import DatabaseWorker
    fake = types.SimpleNamespace(conn=conn, logger=logging.getLogger("test.db"))
    return conn, (lambda payload: DatabaseWorker._persist_usage(fake, payload))


def _stored_snapshot(conn):
    row = conn.execute("SELECT value FROM metadata WHERE key = ?",
                       (constants.network.usage.SNAPSHOT_METADATA_KEY,)).fetchone()
    return row[0] if row else None


def test_unencodable_snapshot_deletes_the_stored_one_instead_of_leaving_it_stale():
    """Review #1: boot_time() unreadable -> no snapshot can be encoded. Leaving the OLD snapshot
    next to an advanced total would make the next same-boot launch re-add (now - old) = double
    count. The stored snapshot must be removed in the same transaction."""
    conn, persist = _usage_db()
    s = _fake_widget_state()
    with patch("netspeedtray.core.usage_snapshot.psutil.boot_time", return_value=BOOT):
        WidgetState.add_usage_bytes(s, 10.0, 10.0, counters={"Wi-Fi": NetIO(100, 100)}, sampled_at=1.0)
    persist(s.db_worker.enqueue_task.call_args[0][1])
    assert _stored_snapshot(conn) is not None

    s._usage_last_persist = 0.0
    with patch("netspeedtray.core.usage_snapshot.psutil.boot_time", side_effect=OSError("no")):
        WidgetState.add_usage_bytes(s, 500.0, 500.0, counters={"Wi-Fi": NetIO(600, 600)}, sampled_at=2.0)
    persist(s.db_worker.enqueue_task.call_args[0][1])
    assert _stored_snapshot(conn) is None
    assert conn.execute(f"SELECT cumulative_up FROM {constants.data.USAGE_COUNTER_TABLE}").fetchone()[0] == 510.0


def test_no_counters_seen_yet_leaves_the_stored_snapshot_untouched():
    conn, persist = _usage_db()
    conn.execute("INSERT INTO metadata VALUES (?, 'kept')", (constants.network.usage.SNAPSHOT_METADATA_KEY,))
    s = _fake_widget_state()
    WidgetState.add_usage_bytes(s, 1.0, 1.0)            # no counters fed
    persist(s.db_worker.enqueue_task.call_args[0][1])
    assert _stored_snapshot(conn) == "kept"


def _may30() -> float:
    from datetime import datetime
    return datetime(2026, 5, 30, 12, 0).timestamp()


def _june2() -> float:
    from datetime import datetime
    return datetime(2026, 6, 2, 12, 0).timestamp()


def test_bytes_are_charged_to_the_period_their_window_began_in():
    """Review #2: a window (closed app / standby gap) that began before the reset day must not land
    in the new period - that is what fires an early overage alert."""
    s = _fake_widget_state()
    s._usage.update(cumulative_up=100.0, cumulative_down=100.0, period_key="2026-05-01")
    WidgetState.add_usage_bytes(s, 1_000.0, 2_000.0, window_start=_may30())
    assert s._usage["period_key"] == "2026-06-01"
    assert WidgetState.get_usage_this_period(s) == (0.0, 0.0)
    assert (s._usage["cumulative_up"], s._usage["cumulative_down"]) == (1_100.0, 2_100.0)


def test_previous_period_window_holds_even_if_a_read_rolled_the_period_first():
    s = _fake_widget_state()
    s._usage.update(cumulative_up=100.0, cumulative_down=100.0, period_key="2026-05-01")
    assert WidgetState.get_usage_this_period(s) == (0.0, 0.0)      # the read path rolls over first
    WidgetState.add_usage_bytes(s, 1_000.0, 2_000.0, window_start=_may30())
    assert WidgetState.get_usage_this_period(s) == (0.0, 0.0)


def test_a_window_that_began_in_this_period_counts_in_it():
    s = _fake_widget_state()
    s._usage.update(period_key="2026-05-01")
    WidgetState.add_usage_bytes(s, 1_000.0, 2_000.0, window_start=_june2())
    assert WidgetState.get_usage_this_period(s) == (1_000.0, 2_000.0)


def test_controller_passes_each_window_start():
    """Live polls: the window began at the previous sample. Launch: at the snapshot."""
    snap = _snapshot({"Wi-Fi": (0, 0)})
    state = FakeState(snap)
    c = _launch(state, {"Wi-Fi": NetIO(10, 10)})
    assert state.add_kwargs[0]["window_start"] == snap["ts"]
    with patch("time.monotonic", return_value=1001.0):
        c._handle_network_counters({"Wi-Fi": NetIO(20, 20)}, sample_time=(1001.0, 7_000.0))
    with patch("time.monotonic", return_value=1002.0):
        c._handle_network_counters({"Wi-Fi": NetIO(30, 30)}, sample_time=(1002.0, 7_001.0))
    assert state.add_kwargs[-1]["window_start"] == 7_000.0


def test_rollover_persists_keep_total_and_snapshot_consistent():
    """Review #4: the persist inside the rollover must not pair THIS poll's counters with a total
    that does not include this poll's bytes yet."""
    s = _fake_widget_state()
    s._usage.update(period_key="2026-06-01")
    c1, c2 = {"Wi-Fi": NetIO(1_000, 1_000)}, {"Wi-Fi": NetIO(1_500, 1_500)}
    with patch("netspeedtray.core.usage_snapshot.psutil.boot_time", return_value=BOOT):
        WidgetState.add_usage_bytes(s, 1_000.0, 1_000.0, counters=c1, sampled_at=1.0)
        s.db_worker.enqueue_task.reset_mock()
        s._compute_period_key = lambda rd, today=None: (
            "2026-07-01" if today is None else WidgetState._compute_period_key(rd, today))
        WidgetState.add_usage_bytes(s, 500.0, 500.0, counters=c2, sampled_at=2.0)
    payloads = [call[0][1] for call in s.db_worker.enqueue_task.call_args_list]
    assert payloads
    for p in payloads:
        snap_counter = json.loads(p[6])["counters"]["Wi-Fi"][0]
        assert p[0] == snap_counter      # cumulative_up == counter it was counted up to (from 0)


def test_persisted_snapshot_records_the_auto_mode_primary():
    s = _fake_widget_state()
    with patch("netspeedtray.core.usage_snapshot.psutil.boot_time", return_value=BOOT):
        WidgetState.add_usage_bytes(s, 1.0, 1.0, counters={"Wi-Fi": NetIO(1, 1)}, sampled_at=1.0,
                                    primary="Wi-Fi")
    assert json.loads(s.db_worker.enqueue_task.call_args[0][1][6])["primary"] == "Wi-Fi"


def test_controller_hands_the_odometer_its_auto_primary():
    state = FakeState()
    c = _controller(state, mode="auto")
    with patch("netspeedtray.core.controller.get_primary_interface_name", return_value="Wi-Fi"):
        _poll(c, {"Wi-Fi": NetIO(0, 0)}, 1000.0)
        _poll(c, {"Wi-Fi": NetIO(5, 5)}, 1001.0)
    assert state.add_kwargs[-1]["primary"] == "Wi-Fi"


def _auto_launch(snapshot_primary, current_primary):
    snap = _snapshot({"Wi-Fi": (0, 0), "Ethernet": (0, 0)})
    if snapshot_primary is not None:
        snap["primary"] = snapshot_primary
    state = FakeState(snap)
    with patch("netspeedtray.core.controller.get_primary_interface_name", return_value=current_primary):
        _launch(state, {"Wi-Fi": NetIO(1_000, 2_000), "Ethernet": NetIO(3_000, 4_000)}, mode="auto")
    return state


def test_auto_mode_skips_launch_recovery_when_the_primary_changed(caplog):
    """Review #3: the closed window's traffic may have gone over the OLD primary; charging the
    new primary's whole counter delta to the odometer overcounts."""
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.StatsController"):
        state = _auto_launch("Ethernet", "Wi-Fi")
    assert state.totals() == (0, 0)
    assert any("primary" in r.message.lower() for r in caplog.records)


def test_auto_mode_recovers_when_the_primary_is_the_same():
    assert _auto_launch("Wi-Fi", "Wi-Fi").totals() == (1_000, 2_000)


def test_auto_mode_skips_a_snapshot_without_a_primary():
    assert _auto_launch(None, "Wi-Fi").totals() == (0, 0)


# --- 5b. IPv6-only primary -----------------------------------------------------------------

_Addr = namedtuple("_Addr", "family address netmask broadcast ptp")


class _Sock:
    """IPv4 has no route (IPv6-only network); IPv6 routes via the given local address."""
    def __init__(self, family, *a, **k):
        self.family = family
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def settimeout(self, *a): pass
    def connect(self, addr):
        if self.family == socket.AF_INET:
            raise OSError("[WinError 10051] A socket operation was attempted to an unreachable network")
    def getsockname(self):
        return ("2001:db8:1::42", 54321, 0, 0)


def test_an_ipv6_only_network_resolves_the_primary_interface():
    from netspeedtray.utils import network_utils as nu
    nu._unreachable_since = None
    addrs = {
        "Ethernet": [_Addr(socket.AF_INET6, "fe80::1%7", None, None, None)],
        "Wi-Fi": [_Addr(socket.AF_INET6, "2001:DB8:1:0:0:0:0:42", None, None, None)],
    }
    with patch("socket.socket", _Sock), patch("psutil.net_if_addrs", return_value=addrs):
        assert nu.get_primary_interface_name() == "Wi-Fi"
    nu._unreachable_since = None


def test_bytes_before_the_odometer_loads_are_kept_not_dropped(tmp_path, q_app):
    """v8 step 7: the readers are held off while the worker recovers / migrates the file (seconds on
    a large v7 database), so the odometer row cannot load yet. Polls in that window are real usage:
    they are buffered and added once it loads - never dropped, never written as a fresh zero total."""
    from PyQt6.QtCore import QThread
    from netspeedtray.tests.state_helpers import prepare_v8
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    with patch.object(QThread, "start", lambda self: None), \
         patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        ws = WidgetState(config)
    try:
        assert not ws.db_worker.ready.is_set()
        ws.add_usage_bytes(1_000.0, 2_000.0, window_start=time.time() - 1)   # worker not ready
        ws.add_usage_bytes(10.0, 20.0, window_start=time.time() - 1)
        assert not ws._usage_loaded and len(ws._usage_pending) == 2
        prepare_v8(ws)                                                       # the worker is up
        ws.add_usage_bytes(100.0, 200.0, window_start=time.time() - 1)
        assert ws.get_usage_this_period() == (1_110.0, 2_220.0)
        assert ws._usage_pending == []
    finally:
        ws.db_worker._close_connection()
        ws.cleanup()
