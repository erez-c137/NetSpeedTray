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


# --- M9: the Network header fits the Monitor's minimum width in every locale -------------------------

def _locales():
    from pathlib import Path
    return sorted(p.stem for p in (Path(constants.__file__).parent / "locales").glob("*.json"))


@pytest.mark.parametrize("lang", _locales())
def test_network_header_controls_never_overlap_at_minimum_width(q_app, lang):
    from PyQt6.QtWidgets import QApplication
    from netspeedtray.views.monitor.network.header import NetworkHeader
    host = MagicMock()
    host.is_live = True
    hdr = NetworkHeader(I18nStrings(lang), graph_host=host)
    from PyQt6.QtWidgets import QWidget
    # The test platform's fallback font is far wider than Segoe UI, so absolute widths can't be
    # asserted here (the rc harness checks 660-960 px in all 16 locales with real fonts). What is
    # font-independent: the band can go compact, it then needs much less room, and at ANY width down
    # to that minimum the controls never overlap and only the interface button gives up text.
    box = QWidget()
    hdr = NetworkHeader(I18nStrings(lang), parent=box, graph_host=host)
    hdr.set_interfaces(["Ethernet 3 (Realtek PCIe 2.5GbE Family Controller)", "Wi-Fi"])
    hdr._select_iface("Ethernet 3 (Realtek PCIe 2.5GbE Family Controller)", "")
    hdr.set_totals(123.4e9, 456.7e9, "TIMELINE_24_HOURS")
    try:
        box.show()
        hdr.resize(4000, constants.layout.MONITOR_HEADER_BAND_HEIGHT)
        QApplication.processEvents()
        assert not hdr._compact
        full_min = hdr.layout().minimumSize().width()
        hdr._fit(full_min - 1)
        hdr.layout().activate()
        compact_min = hdr.layout().minimumSize().width()
        assert hdr._compact and compact_min < full_min - 40
        assert hdr._iface_btn.minimumSizeHint().width() == constants.layout.MONITOR_IFACE_BUTTON_MIN_WIDTH
        for width in (compact_min, compact_min + 60, full_min - 1):
            hdr.resize(width, constants.layout.MONITOR_HEADER_BAND_HEIGHT)
            QApplication.processEvents()
            ctrls = [hdr._check_btn, hdr._iface_btn, hdr._timeline, hdr._live]
            rects = [c.geometry() for c in ctrls]
            for i in range(len(rects)):
                assert rects[i].right() < width, (lang, width, i)
                for j in range(i + 1, len(rects)):
                    assert not rects[i].intersects(rects[j]), (lang, width, i, j)
            for c in (hdr._check_btn, hdr._timeline, hdr._live):
                assert c.width() + 1 >= c.minimumSizeHint().width(), (lang, width, type(c).__name__)
        assert hdr._iface_btn.text().endswith("▾")   # elided label, chevron kept
    finally:
        box.hide()
        box.deleteLater()


# --- later list: quitting with the Stats sheet open ------------------------------------------------

def test_stats_sheet_deleted_during_exec_does_not_raise(q_app, monkeypatch):
    """App quit closes the Monitor (deleting the modal sheet with it) while sheet.exec() runs; the
    click handler's deleteLater() on the dead wrapper raised and was logged CRITICAL."""
    from PyQt6 import sip
    from netspeedtray.views.monitor import stats_detail as SD
    from netspeedtray.views.monitor.overview.tab import OverviewTab

    def fake_exec(self):
        sip.delete(self)   # what the Monitor's teardown does to its child sheet
        return 0
    monkeypatch.setattr(SD.StatsDetailSheet, "exec", fake_exec)
    cfg = _cfg()
    mw = _mw(cfg)
    tab = OverviewTab(mw, cfg, I18nStrings("en_US"))
    errors = []
    monkeypatch.setattr(tab.logger, "error", lambda *a, **k: errors.append(a))
    try:
        tab._open_detail("cpu")      # must not raise
        assert not errors
    finally:
        tab.teardown()


# --- later list: day / hour buckets on LOCAL boundaries (DST) ----------------------------------------

def test_day_and_hour_buckets_start_on_local_boundaries():
    """Day buckets were ts // 86400 - UTC midnight, i.e. 02:00 / 03:00 in Israel, moving by an hour
    across DST; hour buckets were half an hour off in +5:30 zones. Checked against Python's own
    local-time conversion (the one the graph's axis labels use) across both DST changes of a year.
    (On a UTC machine - CI - the old form happens to agree; the owner's UTC+2/+3 PC is where it bit.)"""
    import sqlite3
    from netspeedtray.core.db.history_reader import _bin_start, _local_bin_sql
    conn = sqlite3.connect(":memory:")
    base = int(datetime(2026, 1, 1).timestamp())
    for i in range(0, 366 * 24, 7):                      # every 7 hours through a year
        ts = base + i * 3600 + 1234
        local = datetime.fromtimestamp(ts)
        day = _bin_start(conn.execute(f"SELECT {_local_bin_sql('?', 86400)}".replace("?", str(ts))).fetchone()[0],
                         86400)
        hour = conn.execute(f"SELECT {_local_bin_sql('?', 3600)}".replace("?", str(ts))).fetchone()[0]
        assert datetime.fromtimestamp(day) == local.replace(hour=0, minute=0, second=0), local
        assert datetime.fromtimestamp(hour) == local.replace(minute=0, second=0), local
    assert _local_bin_sql("n.ts", 60) == "CAST(n.ts / 60 AS INTEGER) * 60"   # minutes: unchanged
    conn.close()


def test_network_header_does_not_remeasure_on_every_totals_tick(q_app, monkeypatch):
    """Review item 7: _fit built a throwaway QToolButton and re-laid out the band on every totals
    update. The full-width measurement is cached, and a tick that doesn't change the content's
    width doesn't re-fit at all."""
    from PyQt6.QtWidgets import QApplication, QToolButton, QWidget
    from netspeedtray.views.monitor.network import header as HM
    host = MagicMock()
    host.is_live = True
    box = QWidget()
    hdr = HM.NetworkHeader(I18nStrings("en_US"), parent=box, graph_host=host)
    box.show()
    hdr.resize(3000, constants.layout.MONITOR_HEADER_BAND_HEIGHT)
    hdr.set_totals(1.0e9, 2.0e9, "TIMELINE_24_HOURS")
    QApplication.processEvents()
    made, fits = [], []
    real_init = QToolButton.__init__
    monkeypatch.setattr(HM, "QToolButton", type("Counting", (QToolButton,), {
        "__init__": lambda self, *a, **k: (made.append(1), real_init(self, *a, **k))[1]}))
    real_fit = hdr._fit
    monkeypatch.setattr(hdr, "_fit", lambda w: (fits.append(w), real_fit(w))[1])
    try:
        for i in range(20):                      # same-width ticks: 1.1 GB, 1.2 GB, ...
            hdr.set_totals(1.0e9 + i * 1e8, 2.0e9 + i * 1e8, "TIMELINE_24_HOURS")
        hdr.resize(1200, constants.layout.MONITOR_HEADER_BAND_HEIGHT)   # a resize still fits
        QApplication.processEvents()
        assert made == []
        assert len(fits) <= 3, fits
    finally:
        box.hide()
        box.deleteLater()


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
