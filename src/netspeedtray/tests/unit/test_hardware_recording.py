"""
Hardware history recording follows the user's choice (2.2 scope item C).

Before: `record_hardware_history` (default True, no UI) was OR-ed into the collection gate, so CPU/GPU/RAM
were polled AND persisted every tick even with CPU/GPU monitoring switched off - and the Monitor window's
forced collection persisted temps/power the user never enabled. Now:
  * persisted only when the toggle is on AND that metric's monitoring is on;
  * the in-memory live series still fill (mini graph, Monitor live tiles) - display is not persistence;
  * the thread collects hardware only for monitoring the user turned on, or while the Monitor forces it.
"""
from datetime import datetime
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
