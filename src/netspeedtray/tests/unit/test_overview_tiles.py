"""
OverviewTab tiles - the matplotlib-free glance. Verifies the sparkline stat tiles and the usage
tile read live values defensively, that the refresh timer only runs while visible, and that a
missing data source degrades to a dash instead of crashing.
"""
from datetime import datetime

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor.overview.tab import OverviewTab
from netspeedtray.views.monitor.overview.tiles import Sparkline, UsageTile


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Snap:
    def __init__(self, value):
        self.value = value
        self.timestamp = datetime.now()


class _Agg:
    def __init__(self, up, down):
        self.upload = up
        self.download = down
        self.timestamp = datetime.now()


class _WS:
    def get_aggregated_speed_history(self):
        return [_Agg(1.0e6, 2.0e6), _Agg(1.5e6, 3.0e6)]

    def get_cpu_history(self):
        return [_Snap(10.0), _Snap(42.0)]

    def get_gpu_history(self):
        return []

    def get_ram_history(self):
        return [_Snap(50.0)]

    def get_speed_history(self, start, end, iface, resolution='auto'):
        return [(datetime.now(), 1.0e6, 2.0e6), (datetime.now(), 1.5e6, 3.0e6)]   # (ts, up, dn)

    def get_hardware_history(self, stat, start, end):
        return [(datetime.now(), 42.0)]

    def summarize_network(self, direction, start, end, iface, poll):
        from netspeedtray.utils.summaries import summarize_raw
        return summarize_raw([2.0e6, 3.0e6] if direction == "download" else [1.0e6, 1.5e6])

    def summarize_hardware(self, stat, start, end, poll):
        from netspeedtray.utils.summaries import summarize_raw
        return summarize_raw([10.0, 42.0])


class _MW:
    """A minimal stand-in for the main widget."""
    cpu_usage = 42.0
    gpu_usage = 0.0
    gpu_present = True
    # download_speed/upload_speed are in MBPS (the controller emits Mbps; main.py stores it verbatim).
    # The old fixture used bytes/sec values here, which masked the Overview's missing Mbps→bytes/sec
    # conversion (the "Overview shows 0.0" bug) - the raw-pass happened to format ~16 Mbps.
    download_speed = 80.0   # Mbps
    upload_speed = 40.0     # Mbps
    ram_used = 8.0e9
    ram_total = 16.0e9
    vram_used = None
    vram_total = None
    session_start_time = None
    widget_state = _WS()

    def _hover_usage_totals(self):
        return ((1.0e6, 2.0e6), (3.0e6, 4.0e6))  # (today, month) as (up, down)

    def _hover_cap_info(self):
        return (5.0, 10.0, 50.0)  # used_gb, cap_gb, pct


def _cfg(**over):
    c = {"monitor_cpu_enabled": True, "monitor_gpu_enabled": True, "monitor_ram_enabled": True,
         "unit_type": "bits_decimal", "decimal_places": 1}
    c.update(over)
    return c


def test_hardware_tiles_always_built(q_app):
    # The Monitor forces hardware collection while open, so the four hardware tiles always exist
    # regardless of the widget's config flags. Network is now the hero, not a tile.
    ov = OverviewTab(_MW(), _cfg(monitor_gpu_enabled=False, monitor_ram_enabled=False),
                     I18nStrings("en_US"))
    assert set(ov._tiles.keys()) == {"cpu", "gpu", "ram", "vram"}


def test_latency_pill_classification(q_app):
    from types import SimpleNamespace
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    L = lambda gw, an, ls: ov._latency_html(SimpleNamespace(latency_gw=gw, latency_anchor=an, latency_loss=ls))
    assert "Good" in L(12.0, None, 0.0) and "12 ms" in L(12.0, None, 0.0)
    assert "OK" in L(80.0, None, 0.0)
    assert "Slow" in L(None, None, 10.0) and "loss" in L(None, None, 10.0)   # all timeouts
    assert "Slow" in L(220.0, None, 0.0)
    assert "45 ms" in L(2.0, 45.0, 0.0)                                        # internet anchor wins
    assert L(None, None, 0.0) == ""                                           # no data -> empty


def test_reload_window_passes_the_configured_poll(q_app):
    """2.1.5 item 9: the configured update_rate must reach summarize_* through the shared resolver
    (a local `or 1.0` once leaked a sentinel and made every Overview summary report coverage 0%)."""
    captured = []

    class _CapWS(_WS):
        def get_speed_history(self, start, end, iface, resolution='auto', **kwargs):
            return super().get_speed_history(start, end, iface, resolution=resolution)

        def summarize_network(self, direction, start, end, iface, poll):
            captured.append(poll)
            return super().summarize_network(direction, start, end, iface, poll)

        def summarize_hardware(self, stat, start, end, poll):
            captured.append(poll)
            return super().summarize_hardware(stat, start, end, poll)

    class _MW2(_MW):
        widget_state = _CapWS()

    ov = OverviewTab(_MW2(), _cfg(update_rate=5.0), I18nStrings("en_US"))
    ov.isVisible = lambda: True          # bypass the visibility guard without show() (no feed thread)
    try:
        ov._reload_window()
        _spin(lambda: ov._win_summ is not None)   # the reads run on the tab's reader thread
        assert captured and all(p == 5.0 for p in captured)
    finally:
        ov.teardown()


def _spin(cond, timeout: float = 5.0) -> None:
    """Spin the GUI event loop until ``cond()`` holds (the reader thread's result is a queued signal)."""
    import time
    from PyQt6.QtWidgets import QApplication
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    assert cond(), "timed out waiting for the Overview's reader thread"


class _HeldWS(_WS):
    """Records the thread of every history read. The i-th get_speed_history call (one per window
    load) waits on ``holds[i]`` when there is one; the download it returns is the window's span in
    seconds, so a test can tell which range a result was read for."""
    def __init__(self, holds=()):
        import threading
        self.holds = list(holds)
        self.threads = []
        self.speed_calls = 0
        self.interrupted = []
        self._lock = threading.Lock()

    def _note(self):
        import threading
        with self._lock:
            self.threads.append(threading.get_ident())

    def get_speed_history(self, start, end, iface, resolution='auto', **kwargs):
        self._note()
        with self._lock:
            i = self.speed_calls
            self.speed_calls += 1
        if i < len(self.holds):
            self.holds[i].wait(3.0)
        span = (end - start).total_seconds()
        return [(end, 1.0, span), (end, 1.0, span)]

    def get_hardware_history(self, stat, start, end):
        self._note()
        return super().get_hardware_history(stat, start, end)

    def summarize_network(self, *a):
        self._note()
        return super().summarize_network(*a)

    def summarize_hardware(self, *a):
        self._note()
        return super().summarize_hardware(*a)

    def get_earliest_data_timestamp(self):
        self._note()
        return datetime(2026, 1, 1)

    def get_history_volume_for_period(self, start, end, iface=None):
        from netspeedtray.core.db.history_reader import VolumeResult
        self._note()
        self.volume_calls = getattr(self, "volume_calls", 0) + 1
        return VolumeResult(1e9, 2e9, 0.0, 0.0, False)

    def interrupt_reads(self, thread_id=None):
        self.interrupted.append(thread_id)
        for h in self.holds:
            h.set()


def _overview_on(ws, period=2, session_start=None, **cfg):
    class _MWh(_MW):
        widget_state = ws
        session_start_time = session_start
    ov = OverviewTab(_MWh(), _cfg(history_period_slider_value=period, **cfg), I18nStrings("en_US"))
    ov.isVisible = lambda: True          # bypass the visibility guard without show()
    return ov


def test_window_reads_run_off_the_gui_thread(q_app):
    """Beta checkup follow-up: _reload_window ran ~10 history reads on the GUI thread every 6 s (up to
    ~1.3 s per reload on a 26 MB v8 DB). They must run on the reader thread; until the result lands
    the hero says it is reading instead of showing an empty avg/peak, and the strip claims nothing."""
    import threading
    gui = threading.get_ident()
    ws = _HeldWS()
    ov = _overview_on(ws)
    try:
        ov._reload_window()
        assert gui not in ws.threads, "a history read ran on the GUI thread"
        ov._render()
        assert ov._hero._sub.text() == "Reading history…"
        assert ov._connection_health_html(_MW()) == ""
        _spin(lambda: ov._win_summ is not None)
        assert ws.threads and gui not in ws.threads, "a history read ran on the GUI thread"
        # ...and the tiles get the same figures the synchronous reload produced.
        assert ov._series["down"] == [86400.0, 86400.0] and ov._series["cpu"] == [42.0]
        assert ov._win_summ["cpu"].max == 42.0 and ov._latency_events is not None
        assert "avg" in ov._hero._sub.text() and "Reading" not in ov._hero._sub.text()
    finally:
        ov.teardown()
    assert ov._reader_thread is None


def test_reloads_coalesce_while_one_is_in_flight(q_app):
    """The 6 s timer, a show and a range change can all ask for a reload while a slow one runs: they
    must not pile up behind it - one in flight, at most one rerun after it."""
    import threading
    hold = threading.Event()
    ws = _HeldWS(holds=[hold])
    ov = _overview_on(ws)
    try:
        ov._reload_window()
        _spin(lambda: ws.speed_calls == 1)
        for _ in range(3):
            ov._reload_window()
        assert ws.speed_calls == 1, "a reload started while another was in flight"
        hold.set()
        _spin(lambda: ws.speed_calls == 2)        # exactly one rerun for the three requests...
        for _ in range(50):
            q_app.processEvents()
        assert ws.speed_calls == 2                # ...and nothing more queued behind it
    finally:
        ov.teardown()


def test_a_result_for_the_old_range_is_dropped(q_app):
    """The range changes while the old range's read is running: its late result must never be
    painted under the new range's label - the placeholder stays until the new range's read lands."""
    import threading
    holds = [threading.Event(), threading.Event()]
    ws = _HeldWS(holds=holds)
    ov = _overview_on(ws)
    try:
        ov._reload_window()                       # 24 hours, held
        _spin(lambda: ws.speed_calls == 1)
        ov._on_period_changed(7)                  # -> 1 hour
        assert ov._win_summ is None and ov._series == {}
        holds[0].set()                            # the 24 h result lands now...
        _spin(lambda: ws.speed_calls == 2)        # ...and the coalesced 1 h reload has started
        q_app.processEvents()
        assert ov._win_summ is None and ov._series == {}, "the old range's result was applied"
        holds[1].set()
        _spin(lambda: ov._win_summ is not None)
        assert ov._series["down"] == [3600.0, 3600.0]
    finally:
        ov.teardown()


def test_a_result_landing_after_hide_is_dropped(q_app):
    import threading
    from PyQt6.QtGui import QHideEvent
    hold = threading.Event()
    ws = _HeldWS(holds=[hold])
    ov = _overview_on(ws)
    try:
        ov._reload_window()
        _spin(lambda: ws.speed_calls == 1)
        ov.hideEvent(QHideEvent())
        hold.set()
        for _ in range(50):
            q_app.processEvents()
        assert ov._win_summ is None and ov._series == {}
        assert ws.speed_calls == 1                # and no rerun while hidden
    finally:
        ov.teardown()


def test_closing_mid_load_drops_the_result_and_interrupts_at_once(q_app, monkeypatch):
    """The Monitor closes while a reload runs: the query is interrupted straight away (no
    GRAPH_THREAD_QUIT_WAIT_MS of blocked UI - stretched to 5 s here so the bound is timing-proof),
    the reader thread is stopped, and the late result never reaches the tab."""
    import threading
    import time
    from netspeedtray import constants
    monkeypatch.setattr(constants.timeouts, "GRAPH_THREAD_QUIT_WAIT_MS", 5000)
    hold = threading.Event()
    ws = _HeldWS(holds=[hold])
    ov = _overview_on(ws)
    ov._reload_window()
    _spin(lambda: ws.speed_calls == 1 and getattr(ov, "_reader_ident", None) is not None)
    reader, stale_seq = ov._reader_ident, ov._load_seq
    t0 = time.perf_counter()
    ov.teardown()
    assert time.perf_counter() - t0 < 2.5, "close waited for a result it discards"
    assert ws.interrupted == [reader]
    assert ov._reader_thread is None
    for _ in range(20):
        q_app.processEvents()
    assert ov._win_summ is None and ov._series == {}
    # A result carrying the pre-close sequence (queued before the disconnect) is ignored too.
    ov._on_window_loaded(stale_seq, ({"down": [1.0]}, {}, {}, None))
    assert ov._win_summ is None and ov._series == {}



def test_a_same_range_reload_keeps_the_figures_on_screen(q_app):
    """Review: the periodic 6 s reload (same range) must not flash the placeholder - the last
    figures stay up while the next read runs, and are replaced only when it lands."""
    import threading
    holds = [threading.Event(), threading.Event()]
    holds[0].set()                                # the first load goes straight through
    ws = _HeldWS(holds=holds)
    ov = _overview_on(ws)
    try:
        ov._reload_window()
        _spin(lambda: ov._win_summ is not None)
        ov._reload_window()                       # the timer's reload, held
        _spin(lambda: ws.speed_calls == 2)
        ov._render()                              # the 1 Hz tick meanwhile
        assert ov._win_summ is not None and ov._series["down"] == [86400.0, 86400.0]
        assert "Reading" not in ov._hero._sub.text() and "avg" in ov._hero._sub.text()
        holds[1].set()
        _spin(lambda: not ov._load_busy)
        assert ov._win_summ is not None
    finally:
        ov.teardown()


def test_hide_then_show_mid_read_ends_with_data(q_app):
    """Hidden while a read runs (its result is dropped), shown again before it lands: the tab must
    end up with the figures, not stuck on the placeholder."""
    import threading
    from PyQt6.QtGui import QHideEvent, QShowEvent
    hold = threading.Event()
    ws = _HeldWS(holds=[hold])
    ov = _overview_on(ws)
    ov._busiest.start = lambda: None             # no per-app sampler in a unit test
    try:
        ov._reload_window()
        _spin(lambda: ws.speed_calls == 1)
        ov.hideEvent(QHideEvent())
        ov.showEvent(QShowEvent())
        hold.set()
        _spin(lambda: ov._win_summ is not None)
        assert ws.speed_calls == 2 and ov._series["down"] == [86400.0, 86400.0]
        assert "Reading" not in ov._hero._sub.text()
    finally:
        ov.teardown()


def test_session_volume_and_earliest_sample_are_read_off_the_gui_thread(q_app):
    """Review: the strip's session volume was read on every 1 Hz render, and All / System uptime read
    the earliest stored sample (plus a batch flush) on the GUI thread on every reload. Both now come
    from the reader thread, and the render reads nothing."""
    import threading
    from datetime import timedelta
    gui = threading.get_ident()
    ws = _HeldWS()
    ov = _overview_on(ws, period=5, session_start=datetime.now() - timedelta(minutes=5))   # All
    try:
        ov._reload_window()
        _spin(lambda: ov._win_summ is not None)
        assert gui not in ws.threads, "a history read ran on the GUI thread"
        reads = len(ws.threads)
        for _ in range(3):
            ov._render()                          # three 1 Hz ticks
        assert len(ws.threads) == reads, "the 1 Hz render read the DB"
        assert "2.0 GB" in ov._session_lbl.text(), ov._session_lbl.text()
        # The range really starts at the earliest stored sample (span = now - 2026-01-01).
        assert ov._series["down"][0] > 86400.0 * 30
    finally:
        ov.teardown()


def test_a_failed_session_copy_does_not_leave_the_placeholder_up(q_app):
    class _BadSessionWS(_HeldWS):
        def get_aggregated_speed_history(self):
            raise RuntimeError("deque mutated during iteration")
    ov = _overview_on(_BadSessionWS())
    ov._period_index = 0                           # Session (a saved 0 reads back as the 24 h default)
    try:
        ov._reload_window()
        assert ov._win_summ is not None
        assert "Reading" not in ov._hero._sub.text()
    finally:
        ov.teardown()


def test_context_right_prefers_true_system_power(q_app):
    from types import SimpleNamespace
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    # The strip's right slot shows TRUE system power (PSYS/battery) when the platform exposes it -
    # CPU/GPU power is on the cards now, so it's no longer repeated here.
    mw = SimpleNamespace(system_power=95.0, latency_gw=10.0, latency_anchor=None)
    assert ov._context_right_text(mw) == "System 95 W"


def test_context_right_connection_health_when_no_syspower(q_app):
    from datetime import datetime
    from types import SimpleNamespace
    mw = SimpleNamespace(system_power=None, latency_gw=12.0, latency_anchor=None)
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    # A clean window (with latency data present) reads "steady" in green.
    ov._latency_events = {"count": 0, "last_start": None}
    assert "steady" in ov._context_right_text(mw)
    # Drops over the window surface the count + last drop time, so a missed-live blip is still visible.
    ov._latency_events = {"count": 2, "last_start": datetime(2026, 6, 28, 8, 40)}
    txt = ov._context_right_text(mw)
    assert "2 drops" in txt and "08:40" in txt
    # Latency monitoring off -> the slot stays empty.
    ov_off = OverviewTab(_MW(), _cfg(latency_enabled=False), I18nStrings("en_US"))
    assert ov_off._context_right_text(mw) == ""


def test_hw_sub_omits_power_that_rounds_to_zero(q_app):
    # Regression for the GPU-tile flicker: a flaky iGPU power of 0.3 W rounds to "0 W" and would make
    # the sub-line appear/vanish; only show power that displays as >= 1 W. (temp >= 1 likewise.)
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    assert ov._hw_sub(None, 0.3) == ""
    assert ov._hw_sub(None, 0.0) == ""
    assert ov._hw_sub(0.0, None) == ""
    assert ov._hw_sub(55.0, 65.0) == "55°C  ·  65 W"
    assert ov._hw_sub(62.0, None) == "62°C"


def test_stat_tile_reserves_sub_line(q_app):
    # The sub-line keeps its height even when empty, so a coming-and-going reading never reflows the
    # tile (the cause of the title/% jumping up and down).
    from netspeedtray.views.monitor.overview.tiles import StatTile
    t = StatTile("iGPU", "#FF9800")
    assert t._sub.minimumHeight() > 0
    t.set("23%", [1.0, 2.0], vmax=100.0, sub_text="")
    assert t._sub.text() == " "                 # blank line reserved, not hidden
    t.set("23%", [1.0, 2.0], vmax=100.0, sub_text="55°C")
    assert t._sub.text() == "55°C"


def test_gpu_tile_hides_on_no_gpu(q_app):
    # Review fix: a confirmed no-GPU box (gpu_present False) hides the GPU tile instead of 0%.
    mw = _MW()
    mw.gpu_present = False
    ov = OverviewTab(mw, _cfg(), I18nStrings("en_US"))
    ov.show()
    q_app.processEvents()
    assert not ov._tiles["gpu"].isVisibleTo(ov)
    ov.hide()
    ov.teardown()   # showing starts the most-connections feed thread; tear it down so it can't leak


def test_tiles_show_live_values(q_app):
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    ov.show()                       # showEvent ticks immediately
    q_app.processEvents()
    assert ov._tiles["cpu"]._value.text() == "42%"
    assert ov._tiles["ram"]._value.text() == "50%"          # 8/16
    # Network hero shows the live speed in the right MAGNITUDE (the Mbps→bytes/sec conversion must be
    # applied - passing raw Mbps to format_speed collapsed it to ~0.0). 80 Mbps must read ~80, not 0 or
    # thousands of Gbps.
    down_text = ov._hero._down_v.text()
    assert "Mbps" in down_text and "Mbps" in ov._hero._up_v.text()
    assert float(down_text.replace(",", ".").split()[0]) == pytest.approx(80.0, abs=0.5)
    # VRAM has no reading (vram_used None) -> its tile hides itself
    assert not ov._tiles["vram"].isVisibleTo(ov)
    # usage tile: cap is set -> progress shows 50%
    assert ov._usage._cap_bar.isVisible()
    assert ov._usage._cap_bar.value() == 50
    ov.hide()
    ov.teardown()


def test_responsive_reflow_grid_positions(q_app):
    """The hardware tiles + bottom cards reflow between a wide (1×4 / side-by-side) and a compact
    (2×2 / stacked) arrangement - never overlapping, so a narrow window stays usable."""
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    # Compact: 2 columns -> RAM drops to the second row; bottom cards stack.
    ov._narrow = True
    ov._set_visible_tiles(["cpu", "gpu", "ram", "vram"])
    ov._place_bottom(stacked=True)
    r, c, _, _ = ov._hw_grid.getItemPosition(ov._hw_grid.indexOf(ov._tiles["ram"]))
    assert (r, c) == (1, 0)
    bb = ov._bottom_grid.getItemPosition(ov._bottom_grid.indexOf(ov._busiest))
    assert (bb[0], bb[1]) == (1, 0)        # busiest stacked under usage
    # Wide: one row of 4; bottom side by side.
    ov._narrow = False
    ov._set_visible_tiles(["cpu", "gpu", "ram", "vram"])
    ov._place_bottom(stacked=False)
    r, c, _, _ = ov._hw_grid.getItemPosition(ov._hw_grid.indexOf(ov._tiles["vram"]))
    assert (r, c) == (0, 3)
    bb = ov._bottom_grid.getItemPosition(ov._bottom_grid.indexOf(ov._busiest))
    assert (bb[0], bb[1]) == (0, 1)        # busiest beside usage
    ov.teardown()


def test_three_tiles_fill_the_width(q_app):
    """With only 3 tiles (no dedicated VRAM on an iGPU) they reflow to 3 columns and FILL the width -
    no empty hole where the 4th tile would be (regression for the owner's iGPU screenshot)."""
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    ov._narrow = False
    ov._set_visible_tiles(["cpu", "gpu", "ram"])
    # Exactly 3 stretched columns; the 4th has no stretch (no reserved hole).
    assert [ov._hw_grid.columnStretch(i) for i in range(4)] == [1, 1, 1, 0]
    r, c, _, _ = ov._hw_grid.getItemPosition(ov._hw_grid.indexOf(ov._tiles["ram"]))
    assert (r, c) == (0, 2)                # RAM is the third (last) column, no gap after it
    assert not ov._tiles["vram"].isVisibleTo(ov)   # the absent tile isn't placed or shown
    ov.teardown()


def test_resize_crosses_breakpoint(q_app):
    """Resizing across the breakpoint flips the layout (and the content lives in a scroll area, so a
    short window scrolls instead of overlapping)."""
    from PyQt6.QtWidgets import QScrollArea
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    assert isinstance(ov._scroll, QScrollArea)
    ov.resize(900, 600)
    ov.show()
    q_app.processEvents()
    assert ov._narrow is False              # wide -> 1×4
    ov.resize(680, 600)
    q_app.processEvents()
    assert ov._narrow is True               # narrow -> 2×2
    ov.hide()
    ov.teardown()


def test_timer_idles_when_hidden(q_app):
    ov = OverviewTab(_MW(), _cfg(), I18nStrings("en_US"))
    ov.show()
    q_app.processEvents()
    assert ov._timer.isActive()
    ov.hide()
    q_app.processEvents()
    assert not ov._timer.isActive()
    ov.teardown()
    assert not ov._timer.isActive()


def test_missing_data_source_degrades(q_app):
    """A main widget with no widget_state must not crash the tick (teardown race / early boot)."""
    class _Empty:
        cpu_usage = 0.0
        widget_state = None
    ov = OverviewTab(_Empty(), _cfg(monitor_gpu_enabled=False), I18nStrings("en_US"))
    ov.show()
    q_app.processEvents()
    ov._tick()  # must not raise
    ov.hide()
    ov.teardown()


def test_dynamic_range_zooms_to_active_band(q_app):
    """The sparkline scale fits the data's band so low-but-varying activity reads in detail, with a
    minimum span so a steady metric isn't blown up into dramatic noise."""
    from netspeedtray.views.monitor.overview.tiles import dynamic_range
    # CPU mostly 5-20%: zoom near the data, NOT 0-100, but at least the minimum span wide.
    lo, hi = dynamic_range([5, 8, 12, 20, 15, 10])
    assert 0 <= lo and hi <= 100
    assert hi - lo >= 15 - 1e-6        # minimum span enforced
    assert hi <= 35                    # zoomed near the band, not pinned at 100
    # A near-steady metric still gets the minimum span (not a hair-thin amplified band).
    lo2, hi2 = dynamic_range([46.0, 46.5, 45.8, 46.2])
    assert hi2 - lo2 >= 15 - 1e-6
    # Clamps to 0..100 for percentages, and empty input is safe.
    lo3, hi3 = dynamic_range([2, 1, 3])
    assert lo3 >= 0
    lo4, hi4 = dynamic_range([])
    assert hi4 > lo4


def test_sparkline_respects_vmin(q_app):
    """A non-zero vmin 'zooms' the trace: a value at vmin sits on the floor, at vmax on the ceiling."""
    s = Sparkline("#00BCD4")
    s.resize(80, 40)
    s.set_series([10.0, 20.0, 30.0], vmax=30.0, vmin=10.0)
    assert s._vmin == 10.0 and s._vmax == 30.0
    s.repaint()   # must not raise; 10 maps to baseline, 30 to top


def test_sparkline_handles_short_series(q_app):
    s = Sparkline("#00BCD4")
    s.set_series([])          # empty
    s.set_series([5.0])       # single point (n < 2)
    s.resize(80, 36)
    s.set_series([1.0, 2.0, 3.0, 100.0], vmax=100.0)  # normal, with fixed scale
    # No exception == pass; paintEvent is exercised on the next event loop spin.


def test_usage_tile_hides_cap_when_unset(q_app):
    u = UsageTile(I18nStrings("en_US"))
    u.set((1.0e6, 2.0e6), (3.0e6, 4.0e6), cap=None)
    assert not u._cap_bar.isVisibleTo(u)
    assert not u._cap_text.isVisibleTo(u)
    # With no cap, the discoverable "Set a monthly limit" hint shows instead.
    assert u._cap_hint.isVisibleTo(u)


def test_usage_tile_avg_and_projected(q_app):
    """The card derives Avg/day + Projected from the month total, and the hint flips to the cap bar
    once a cap is configured."""
    from datetime import datetime
    import calendar
    u = UsageTile(I18nStrings("en_US"))
    month = (3.0e9, 1.2e9)   # (up, down) bytes this month
    u.set((1e8, 2e8), month, cap=None)
    day = max(1, datetime.now().day)
    dim = calendar.monthrange(datetime.now().year, datetime.now().month)[1]
    # Avg/day text reflects month/day; projected reflects the month-end estimate (both non-empty).
    assert u._avgday[1].text() not in ("", "-")
    assert u._proj[1].text() not in ("", "-")
    # A cap hides the hint and shows the bar.
    u.set((1e8, 2e8), month, cap=(4.0, 10.0, 40.0))
    assert not u._cap_hint.isVisibleTo(u)
    assert u._cap_bar.isVisibleTo(u)


def test_usage_tile_hint_requests_cap(q_app):
    u = UsageTile(I18nStrings("en_US"))
    fired = []
    u.set_cap_requested.connect(lambda: fired.append(True))
    u._cap_hint.linkActivated.emit("#")   # simulate clicking the link
    assert fired == [True]


def test_cap_bar_warns_by_threshold(q_app):
    """The cap bar stays calm under 80%, goes amber 80-99%, red at/over 100% (and clamps to 100)."""
    u = UsageTile(I18nStrings("en_US"))
    u.set((1e6, 2e6), (3e6, 4e6), cap=(4.0, 10.0, 40.0))
    ss = u._cap_bar.styleSheet()
    assert "#FFB900" not in ss and "#E81123" not in ss      # accent
    u.set((1e6, 2e6), (3e6, 4e6), cap=(8.5, 10.0, 85.0))
    assert "#FFB900" in u._cap_bar.styleSheet()             # amber
    u.set((1e6, 2e6), (3e6, 4e6), cap=(11.0, 10.0, 110.0))
    assert "#E81123" in u._cap_bar.styleSheet()             # red
    assert u._cap_bar.value() == 100                        # clamped


def test_network_hero_dual_sparkline(q_app):
    """The hero feeds BOTH download and upload series into one dual-trace sparkline."""
    from netspeedtray.views.monitor.overview.tiles import NetworkHero
    h = NetworkHero(I18nStrings("en_US"), "#4DA3FF", "#F0883E")
    h.set("12.4 Mbps", "0.5 Mbps", [1.0, 2.0, 3.0], [0.2, 0.5, 0.3], sub_text="Peak ↓ 3 ↑ 0.5")
    assert h._down_v.text() == "12.4 Mbps"
    assert h._up_v.text() == "0.5 Mbps"
    assert h._spark._series == [1.0, 2.0, 3.0]
    assert h._spark._series2 == [0.2, 0.5, 0.3]   # upload trace present
    assert h._sub.text() == "Peak ↓ 3 ↑ 0.5"


def test_sparkline_paints_baseline_when_sparse(q_app):
    """With <2 points the sparkline draws a faint baseline (keeps tile weight) instead of returning."""
    s = Sparkline("#4CAF50")
    s.resize(80, 36)
    s.set_series([])     # zero points -> baseline path, must not raise
    s.set_series([42.0], vmax=100.0)  # one point -> flat line at height, must not raise
    s.repaint()
