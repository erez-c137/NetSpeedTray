"""
Hardware history recording follows the user's choice (2.2 scope item C).

Before: `record_hardware_history` (default True, no UI) was OR-ed into the collection gate, so CPU/GPU/RAM
were polled AND persisted every tick even with CPU/GPU monitoring switched off - and the Monitor window's
forced collection persisted temps/power the user never enabled. Now:
  * persisted only when the toggle is on AND that metric's monitoring is on;
  * the in-memory live series still fill (mini graph, Monitor live tiles) - display is not persistence;
  * the thread collects hardware only for monitoring the user turned on, or while the Monitor forces it.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core.controller import StatsController, hardware_persist_allowed
from netspeedtray.core.monitor_thread import hardware_collection_gates
from netspeedtray.core.widget_state import WidgetState


def _cfg(**kw):
    c = {"record_hardware_history": True, "monitor_cpu_enabled": False, "monitor_gpu_enabled": False,
         "monitor_ram_enabled": False, "show_hardware_temps": False, "show_hardware_power": False}
    c.update(kw)
    return c


# --- the policy -----------------------------------------------------------------------------------

@pytest.mark.parametrize("stat", ["cpu", "gpu", "ram", "cpu_temp", "gpu_temp", "cpu_power", "gpu_power",
                                  "system_power", "total_power"])
def test_nothing_persists_with_all_monitoring_off(stat):
    assert hardware_persist_allowed(stat, _cfg()) is False


@pytest.mark.parametrize("stat", ["cpu", "gpu", "ram", "cpu_temp", "gpu_temp", "cpu_power", "gpu_power"])
def test_toggle_off_blocks_everything(stat):
    on = _cfg(record_hardware_history=False, monitor_cpu_enabled=True, monitor_gpu_enabled=True,
              monitor_ram_enabled=True, show_hardware_temps=True, show_hardware_power=True)
    assert hardware_persist_allowed(stat, on) is False


def test_cpu_on_records_cpu_only():
    c = _cfg(monitor_cpu_enabled=True)
    assert hardware_persist_allowed("cpu", c) is True
    assert hardware_persist_allowed("gpu", c) is False
    assert hardware_persist_allowed("ram", c) is False          # RAM monitoring is its own switch
    assert hardware_persist_allowed("cpu_temp", c) is False     # temps not enabled


def test_ram_needs_its_own_switch_and_the_cpu_line():
    assert hardware_persist_allowed("ram", _cfg(monitor_cpu_enabled=True, monitor_ram_enabled=True)) is True
    # RAM rides the CPU line (Settings greys it out with CPU off), so it is not being monitored.
    assert hardware_persist_allowed("ram", _cfg(monitor_ram_enabled=True)) is False


def test_temps_and_power_need_their_metric_and_their_own_switch():
    c = _cfg(monitor_gpu_enabled=True, show_hardware_temps=True)
    assert hardware_persist_allowed("gpu_temp", c) is True
    assert hardware_persist_allowed("cpu_temp", c) is False
    assert hardware_persist_allowed("gpu_power", c) is False
    p = _cfg(monitor_cpu_enabled=True, show_hardware_power=True)
    assert hardware_persist_allowed("cpu_power", p) is True
    assert hardware_persist_allowed("system_power", p) is True


def test_latency_is_not_governed_by_the_hardware_toggle():
    # The latency series have their own switch (latency_enabled); this policy never blocks them.
    assert hardware_persist_allowed("latency_gw", _cfg(record_hardware_history=False)) is True


def test_battery_drain_follows_the_power_display_switch():
    """The hover's "Today avg" source (#323) has its own consent switch: it fills under
    the PR's own defaults (CPU/GPU/power-stats off) and is NOT held hostage by
    show_hardware_power, which system_power still requires."""
    assert hardware_persist_allowed("battery_drain", _cfg(power_display_enabled=True)) is True
    assert hardware_persist_allowed("battery_drain", _cfg()) is False
    r = _cfg(power_display_enabled=True, record_hardware_history=False)
    assert hardware_persist_allowed("battery_drain", r) is False


def test_default_config_keeps_recording_for_monitored_stats():
    d = dict(constants.config.defaults.DEFAULT_CONFIG)
    assert d["record_hardware_history"] is True
    d["monitor_cpu_enabled"] = True
    assert hardware_persist_allowed("cpu", d) is True


# --- WidgetState: display without persistence -----------------------------------------------------

def test_add_hardware_stat_without_persist_keeps_live_series_only(q_app, tmp_path):
    from PyQt6.QtCore import QThread
    with patch.object(QThread, "start", lambda self: None), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        s = WidgetState(constants.config.defaults.DEFAULT_CONFIG.copy())
    try:
        s.add_hardware_stat("cpu", 55.0, now=datetime.now(), persist=False)
        assert len(s.cpu_history) == 1
        assert s._hw_batch == []
        s.add_hardware_stat("cpu", 56.0, now=datetime.now())
        assert len(s._hw_batch) == 1      # default still persists (latency + monitored stats)
    finally:
        s.cleanup()


# --- the controller applies the policy ------------------------------------------------------------

def _persisted(ws: MagicMock) -> dict:
    return {c.args[0]: c.kwargs.get("persist", True) for c in ws.add_hardware_stat.call_args_list}


def test_controller_forced_collection_with_monitoring_off_persists_nothing(q_app):
    ws = MagicMock()
    ctl = StatsController(_cfg(), ws)
    ctl.handle_stats({"cpu": 10.0, "gpu": 20.0, "ram_used": 4.0, "ram_total": 16.0,
                      "cpu_temp": 50.0, "gpu_temp": 60.0, "cpu_power": 15.0, "gpu_power": 30.0,
                      "system_power": 70.0})
    p = _persisted(ws)
    assert set(p) >= {"cpu", "gpu", "ram", "cpu_temp", "gpu_temp", "total_power"}  # live series fed
    assert not any(p.values()), f"persisted with monitoring off: {[k for k, v in p.items() if v]}"


def test_controller_persists_only_the_monitored_metric(q_app):
    ws = MagicMock()
    ctl = StatsController(_cfg(monitor_cpu_enabled=True), ws)
    ctl.handle_stats({"cpu": 10.0, "gpu": 20.0, "ram_used": 4.0, "ram_total": 16.0})
    p = _persisted(ws)
    assert p["cpu"] is True and p["gpu"] is False and p["ram"] is False


def test_controller_follows_a_config_change(q_app):
    ws = MagicMock()
    ctl = StatsController(_cfg(monitor_cpu_enabled=True), ws)
    ctl.apply_config(_cfg(monitor_cpu_enabled=True, record_hardware_history=False))
    ctl.handle_stats({"cpu": 10.0})
    assert _persisted(ws)["cpu"] is False


def test_total_power_needs_every_contributor_allowed(q_app):
    ws = MagicMock()
    ctl = StatsController(_cfg(monitor_cpu_enabled=True, show_hardware_power=True), ws)
    # GPU monitoring is off, but the Monitor forced a GPU power reading: the CPU+GPU sum would leak it.
    ctl.handle_stats({"cpu": 10.0, "cpu_power": 15.0, "gpu_power": 30.0})
    assert _persisted(ws)["total_power"] is False
    ws2 = MagicMock()
    StatsController(_cfg(monitor_cpu_enabled=True, show_hardware_power=True), ws2).handle_stats(
        {"cpu": 10.0, "cpu_power": 15.0})   # no GPU reading at all: the total is CPU-only and allowed
    assert _persisted(ws2)["total_power"] is True


# --- the thread no longer collects just to record -------------------------------------------------

def test_collection_follows_monitoring_not_the_record_flag():
    assert hardware_collection_gates(_cfg(record_hardware_history=True), force=False) == (False, False)
    assert hardware_collection_gates(_cfg(monitor_cpu_enabled=True), force=False) == (True, False)
    assert hardware_collection_gates(_cfg(monitor_gpu_enabled=True), force=False) == (False, True)
    # The Monitor window still forces live collection for its tiles.
    assert hardware_collection_gates(_cfg(), force=True) == (True, True)


def test_record_hardware_history_schema_rejects_non_bool(tmp_path):
    from netspeedtray.utils.config import ConfigManager
    cm = ConfigManager(tmp_path / "cfg.json")
    out = cm._validate_config({"record_hardware_history": "yes"})
    assert out["record_hardware_history"] is True    # coerced back to the default
    out = cm._validate_config({"record_hardware_history": False})
    assert out["record_hardware_history"] is False


# --- the battery-only poll reaches history through the REAL write path (3rd #323 review) -----------

def _live_state(tmp_path):
    """A real WidgetState on a throwaway DB, with the v8 write thread RUNNING - so the
    real write path (the worker's persist handler + the v8 schema) and the real summary
    reader are both exercised. The v7 internals this test used to poke
    (``_check_and_create_schema`` / ``_persist_hardware_batch``) are gone after the v8
    switch-over, so the port is: wait for ``db_worker.ready``, then ``flush_and_wait``."""
    with patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        s = WidgetState(constants.config.defaults.DEFAULT_CONFIG.copy())
    s.batch_persist_timer.stop()          # no background flush - the test drives the write
    s.db_worker.ready.wait(timeout=10)     # the v8 schema is live on disk
    return s


def test_battery_only_poll_reaches_history_through_the_real_write_path(q_app, tmp_path, monkeypatch):
    """With the PR's own defaults (power display on, CPU/GPU/power-stats off) a battery-only
    handle_stats poll must land in the history DB, and the hover's "Today avg" must read it
    back from disk - no summarize_hardware mock, no add_hardware_stat stub: the real write
    (the worker's persist handler, exactly what the thread runs) and the real summary."""
    from datetime import datetime, timedelta
    from netspeedtray.utils import power_utils
    import netspeedtray.core.widget_state as wsm

    state = _live_state(tmp_path)
    try:
        state.config["power_display_enabled"] = True
        ctl = StatsController(_cfg(power_display_enabled=True), state)
        ctl.set_view(MagicMock())
        monkeypatch.setattr(power_utils, "read_power_state",
                            lambda: power_utils.PowerState(power_utils.MODE_DISCHARGE, 63))

        # Two real polls a few "seconds" apart - same-second samples collapse into one
        # raw row (INSERT OR IGNORE on the timestamp), like they do at a real cadence.
        base = datetime.now() - timedelta(minutes=1)
        tick = {"n": 0}
        def _fake_now():
            tick["n"] += 1
            return base if tick["n"] <= 2 else base + timedelta(seconds=3)
        monkeypatch.setattr(wsm, "datetime", type("_DT", (), {"now": staticmethod(_fake_now)}))
        ctl.handle_stats({"system_power": 12.0})
        ctl.handle_stats({"system_power": 18.0})
        # The real persist path: flush the batch and wait until the write thread has it
        # on disk (the v8 writer + schema), exactly what the thread does on a live machine.
        state.flush_and_wait(timeout=5.0)
        assert not state._hw_batch, "the flush did not drain the batch"

        monkeypatch.undo()
        now = datetime.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        summary = state.summarize_hardware('battery_drain', midnight, now,
                                           poll_interval=1.0)
        assert summary is not None and summary.count == 2, "no battery_drain history on disk"
        assert summary.avg == pytest.approx(15.0)   # (12 + 18) / 2 - exactly the hover's read

        # The v8 metrics dimension labels it in watts like the *_power stats (4th #323 review),
        # not with the empty unit _unit_for gives an unknown name.
        import sqlite3
        from netspeedtray.core.db.schema_v8 import METRICS
        from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri
        conn = sqlite3.connect(sqlite_ro_uri(state._db_path), uri=True)
        try:
            unit = conn.execute(f"SELECT unit FROM {METRICS} WHERE name = 'battery_drain'").fetchone()
        finally:
            conn.close()
        assert unit == ("watt",)
    finally:
        state.cleanup()


def test_battery_only_poll_records_nothing_with_power_display_off(q_app, tmp_path, monkeypatch):
    """And the consent gate works in the same real path: power_display off -> no rows."""
    from netspeedtray.utils import power_utils

    state = _live_state(tmp_path)
    try:
        ctl = StatsController(_cfg(), state)
        ctl.set_view(MagicMock())
        monkeypatch.setattr(power_utils, "read_power_state",
                            lambda: power_utils.PowerState(power_utils.MODE_DISCHARGE, 63))

        ctl.handle_stats({"system_power": 12.0})
        types = [b[1] for b in state._hw_batch]
        assert 'battery_drain' not in types
        # Nothing to write for battery_drain - flush anyway so the reader sees the real
        # (empty) on-disk state rather than a pending batch.
        state.flush_and_wait(timeout=5.0)
        now = datetime.now()
        summary = state.summarize_hardware('battery_drain',
                                           now - timedelta(minutes=5), now, poll_interval=1.0)
        assert not summary or not summary.count
    finally:
        state.cleanup()
