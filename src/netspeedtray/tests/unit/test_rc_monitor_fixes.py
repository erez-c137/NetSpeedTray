"""
2.2 release-candidate checkup - Monitor window fixes (M1-M9). Each test reproduces a reviewer
harness finding (rc-check-settings/harness/monitor) and fails without its fix.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings

DEFAULTS = constants.config.defaults.DEFAULT_CONFIG


def _cfg(**over):
    c = dict(DEFAULTS)
    c.update(over)
    return c


def _mw(config):
    m = MagicMock()
    m.config = config
    m.config_manager = MagicMock()
    m.config_controller.update_config.side_effect = lambda u, **k: config.update(u)
    return m


# --- M2: a chosen Session is honoured; a fresh install still opens the Overview on 24 h -------------

def test_saved_period_index_tells_chosen_session_from_never_chosen():
    from netspeedtray.views.monitor.overview.tab import saved_period_index
    assert saved_period_index(_cfg()) == 2                                   # fresh install: 24 h
    assert saved_period_index(_cfg(history_period_slider_value=0, history_period_chosen=True)) == 0
    assert saved_period_index(_cfg(history_period_slider_value=3, history_period_chosen=True)) == 3
    assert saved_period_index(_cfg(history_period_slider_value=3)) == 3      # pre-flag configs keep theirs


def test_picking_a_period_marks_it_chosen(q_app):
    from netspeedtray.views.monitor.overview.tab import OverviewTab, saved_period_index
    cfg = _cfg()
    tab = OverviewTab(_mw(cfg), cfg, I18nStrings("en_US"))
    try:
        tab._on_period_changed(0)                       # the user picks Session
        assert cfg["history_period_chosen"] is True
        assert saved_period_index(cfg) == 0             # ...and it is still Session next open
    finally:
        tab.teardown()


def test_graph_host_marks_its_period_pick_chosen():
    from netspeedtray.views.monitor.graph_host import GraphHost
    cfg = _cfg()
    mw = _mw(cfg)
    host = GraphHost(mw, cfg, I18nStrings("en_US"))
    host.config_handler.queue_config_update({"history_period_slider_value": 0})
    assert mw.config["history_period_chosen"] is True


# --- M1: a period picked on the Overview reaches the Network / Hardware graphs ----------------------

def test_graph_host_adopts_a_period_chosen_elsewhere():
    from netspeedtray.views.monitor.graph_host import GraphHost
    cfg = _cfg(history_period_slider_value=2)
    host = GraphHost(_mw(cfg), cfg, I18nStrings("en_US"))
    assert host._history_period_value == 2
    cfg.update(history_period_slider_value=3, history_period_chosen=True)   # Overview: Week
    host.sync_period_from_config()
    assert host._history_period_value == 3
    assert host._accept_from_seq > host._current_request_id   # older-period replies are dropped


def test_graph_host_keeps_its_own_view_until_a_period_is_chosen():
    from netspeedtray.views.monitor.graph_host import GraphHost
    cfg = _cfg(history_period_slider_value=0)
    host = GraphHost(_mw(cfg), cfg, I18nStrings("en_US"))
    host._history_period_value = 2
    host.sync_period_from_config()
    assert host._history_period_value == 2


def test_network_and_hardware_tabs_sync_the_period_on_show():
    import inspect
    from netspeedtray.views.monitor.hardware.tab import HardwareTab
    from netspeedtray.views.monitor.network.tab import NetworkTab
    for cls in (NetworkTab, HardwareTab):
        assert "sync_period_from_config" in inspect.getsource(cls.showEvent), cls.__name__


# --- M3: System uptime starts at the real boot ------------------------------------------------------

def test_overview_uptime_window_starts_at_boot(monkeypatch):
    import psutil
    from netspeedtray.views.monitor.overview.tab import _resolve_window
    now = datetime(2026, 10, 21, 12, 0)
    boot = now - timedelta(hours=53)
    monkeypatch.setattr(psutil, "boot_time", lambda: boot.timestamp())
    ws = MagicMock()
    ws.get_earliest_data_timestamp.return_value = now - timedelta(days=30)
    start, end, _ = _resolve_window(ws, "TIMELINE_SYSTEM_UPTIME", now, None)
    assert abs((start - boot).total_seconds()) < 1
    assert end == now


# --- M5: Session totals are speed x time, not a sum of B/s samples -----------------------------------

@pytest.mark.parametrize("rate", [1.0, 2.0, 5.0, 10.0])
def test_session_totals_do_not_depend_on_the_update_rate(q_app, rate):
    from types import SimpleNamespace
    from netspeedtray.views.graph.request import DataRequest
    from netspeedtray.views.graph.worker import GraphDataWorker
    start = datetime.now() - timedelta(minutes=10)
    samples = [SimpleNamespace(timestamp=start + timedelta(seconds=i * rate), upload=100_000.0,
                               download=1_000_000.0) for i in range(int(600 / rate))]
    ws = MagicMock()
    ws.get_aggregated_speed_history.return_value = samples
    wk = GraphDataWorker(ws)
    got = {}
    wk.data_ready.connect(lambda d, up, dn, seq: got.update(up=up, dn=dn))
    wk.process_data(DataRequest(start_time=start - timedelta(seconds=1), end_time=datetime.now(),
                                interface_name=None, is_session_view=True, sequence_id=1,
                                stat_type="network"))
    assert got["dn"] == pytest.approx(600e6, rel=0.01)   # 1 MB/s for 10 minutes
    assert got["up"] == pytest.approx(60e6, rel=0.01)


def test_session_volume_does_not_count_a_sleep_gap():
    from netspeedtray.views.graph.worker import GraphDataWorker
    s = [(0.0, 0.0, 1000.0), (2.0, 0.0, 1000.0), (4.0, 0.0, 1000.0), (4004.0, 0.0, 1000.0)]
    _, down = GraphDataWorker._session_volume(s)
    assert down == pytest.approx(8000.0)   # four samples x 2 s typical, not 4000 s of 1000 B/s


# --- M7: closing the Monitor frees its graph host + coordinator ------------------------------------

def test_graph_host_is_collected_after_teardown(q_app):
    import gc
    import weakref
    from PyQt6.QtCore import QCoreApplication, QEvent
    from netspeedtray.views.monitor.graph_host import GraphHost
    refs = []
    for _ in range(4):
        cfg = _cfg()
        mw = _mw(cfg)
        mw.widget_state = MagicMock(spec=["get_earliest_data_timestamp", "release_read_conn",
                                          "interrupt_reads"])
        host = GraphHost(mw, cfg, I18nStrings("en_US"))
        host.ensure_loaded()
        refs.append((weakref.ref(host), weakref.ref(host.coordinator)))
        host.teardown()
        del host, mw
        for _ in range(3):
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            q_app.processEvents()
        gc.collect()
    alive = [i for i, (h, c) in enumerate(refs) if h() is not None or c() is not None]
    assert alive == [], f"graph host / coordinator still alive after teardown: cycles {alive}"


# --- M4: "All" then "Since boot" ---------------------------------------------------------------------

def test_since_boot_after_all_uses_the_boot_time(monkeypatch):
    from netspeedtray.views.graph.logic import GraphLogic
    from netspeedtray.views.monitor.graph_host import GraphHost
    boot = datetime.now() - timedelta(hours=10)
    monkeypatch.setattr(GraphLogic, "get_boot_time", staticmethod(lambda: boot))
    cfg = _cfg(history_period_slider_value=5)                  # opens on All
    mw = _mw(cfg)
    mw.widget_state.get_earliest_data_timestamp.return_value = datetime.now() - timedelta(days=40)
    host = GraphHost(mw, cfg, I18nStrings("en_US"))
    s_all, _ = host._time_range()
    assert (datetime.now() - s_all).days >= 39
    host._history_period_value = 1                            # then Since boot
    s_boot, _ = host._time_range()
    assert abs((s_boot - boot).total_seconds()) < 1
