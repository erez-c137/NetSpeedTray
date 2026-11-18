"""
The Monitor's Hardware graph with nothing recorded. Since 2.2, hardware history is written only for
the CPU/GPU stats the user monitors, and both are off by default - so at 24h/7d the graph used to say
"Collecting data for current session..." forever. With monitoring off, a ranged view now says where to
turn recording on; the live SESSION view (fed by the Monitor's forced collection) keeps "Collecting".
Since 2.2.2 (D4) that note is a NoticeLink with a Settings button the Monitor puts over the canvas
(graph_host.HistoryOffNotice, tested in test_notice_link.py): the renderer draws nothing for it and
reports which note applies in ``history_off_notice`` = (reason, role).

Goes through the real GraphRenderer code paths onto a bare matplotlib Figure (no Qt canvas).
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from matplotlib.figure import Figure

from netspeedtray.constants import styles as style_constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.graph.renderer import GraphRenderer

I18N = I18nStrings("en_US")
COLLECTING = I18N.COLLECTING_DATA_MESSAGE


def _bare_init(self):
    self.figure = Figure(figsize=(8, 6), dpi=100)
    self.canvas = MagicMock()
    self.ax_download = self.figure.add_subplot(2, 1, 1)
    self.ax_upload = self.figure.add_subplot(2, 1, 2)
    self.axes = [self.ax_download, self.ax_upload]
    self._is_dark_mode = False
    self._current_text_color = style_constants.LIGHT_MODE_TEXT_COLOR
    self._current_grid_color = style_constants.GRID_COLOR_LIGHT


@pytest.fixture
def renderer(monkeypatch):
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _bare_init)
    return GraphRenderer(MagicMock(), I18N)


def _texts(fig):
    return [t.get_text() for ax in fig.axes for t in ax.texts]


MON_OFF = {"cpu": "monitor", "gpu": "monitor"}    # 2.2 defaults
REC_OFF = {"cpu": "record", "gpu": "record"}      # monitoring on, Record hardware history off
RECORDING = {"cpu": None, "gpu": None}


def _render(r, stat, period, history_off):
    end = datetime.now()
    data = {"cpu": [], "gpu": [], "ram": []} if stat.startswith("hw") else []
    r.render(data, end - timedelta(hours=24), end, period, stat_type=stat,
             hw_styles={"history_off": history_off})
    return r.history_off_notice


def _role(stat):
    """The role a single-stat view reports (its own stat); None for the combined views."""
    return stat if stat in ("cpu", "gpu") else None


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate", "cpu", "gpu"])
def test_ranged_view_with_monitoring_off_says_how_to_turn_it_on(renderer, stat):
    assert _render(renderer, stat, "TIMELINE_24_HOURS", history_off=MON_OFF) == ("monitor", _role(stat))
    assert _texts(renderer.figure) == []          # the notice is the host's, not chart text


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate", "cpu", "gpu"])
def test_record_switch_off_points_to_advanced(renderer, stat):
    assert _render(renderer, stat, "TIMELINE_24_HOURS", history_off=REC_OFF) == ("record", _role(stat))
    assert COLLECTING not in _texts(renderer.figure)


def test_toggle_layout_gpu_view_with_only_cpu_recorded(renderer):
    """CPU on, GPU off: the GPU-only toggle view will never fill; the CPU one is collecting."""
    mixed = {"cpu": None, "gpu": "monitor"}
    assert _render(renderer, "gpu", "TIMELINE_24_HOURS", history_off=mixed) == ("monitor", "gpu")
    assert _render(renderer, "cpu", "TIMELINE_24_HOURS", history_off=mixed) is None
    assert COLLECTING in _texts(renderer.figure)
    # CPU will fill it
    assert _render(renderer, "hwcombined", "TIMELINE_24_HOURS", history_off=mixed) is None
    assert COLLECTING in _texts(renderer.figure)


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate"])
def test_ram_only_recording_is_collecting_not_off(renderer, stat):
    """2.2.1: RAM is recorded without CPU/GPU monitoring - its graph will fill, so an empty range is
    "collecting", not "history is off"; with RAM off as well it is off."""
    assert _render(renderer, stat, "TIMELINE_24_HOURS", history_off={**MON_OFF, "ram": None}) is None
    assert COLLECTING in _texts(renderer.figure)
    assert _render(renderer, stat, "TIMELINE_24_HOURS",
                   history_off={**MON_OFF, "ram": "monitor"}) == ("monitor", None)


NO_HISTORY = I18N.NET_GRAPH_NO_HISTORY_YET
NO_TRAFFIC = f"{I18N.NET_GRAPH_NO_TRAFFIC_TITLE}\n{I18N.NET_GRAPH_NO_TRAFFIC_REASON}"


def test_empty_network_range_says_so_instead_of_a_flat_zero_line(renderer):
    """RC later-list: with nothing stored for the range the reader returns only zero padding, and the
    Network graph drew a flat 0 line - read as "no traffic" - with no note."""
    end = datetime.now()
    start = end - timedelta(hours=24)
    pad = [(start + timedelta(hours=h), 0.0, 0.0) for h in range(0, 25, 3)]
    nothing = {"earliest": None}
    renderer.render(pad, start, end, "TIMELINE_24_HOURS", stat_type="network", hw_styles=nothing)
    assert NO_HISTORY in _texts(renderer.figure)
    renderer.render([], start, end, "TIMELINE_24_HOURS", stat_type="network", hw_styles=nothing)
    assert NO_HISTORY in _texts(renderer.figure)
    real = [(start + timedelta(hours=h), 1e5, 2e6) for h in range(0, 25, 3)]
    renderer.render(real, start, end, "TIMELINE_24_HOURS", stat_type="network", hw_styles=nothing)
    assert not [t for t in _texts(renderer.figure) if t in (NO_HISTORY, NO_TRAFFIC)]   # taken down


def test_empty_network_range_says_why(renderer):
    """Review item 6: "Not enough history" was wrong for a past window where the PC was simply off or
    idle (the history keeps no all-zero rows): history exists before it, so say no traffic was
    recorded; a range that ends before the first record says when history starts."""
    end = datetime(2026, 9, 10, 12)
    start = end - timedelta(days=7)
    pad = [(start, 0.0, 0.0), (end, 0.0, 0.0)]
    renderer.render(pad, start, end, "TIMELINE_WEEK", stat_type="network",
                    hw_styles={"earliest": start - timedelta(days=30)})
    assert NO_TRAFFIC in _texts(renderer.figure)
    renderer.render(pad, start, end, "TIMELINE_WEEK", stat_type="network",
                    hw_styles={"earliest": datetime(2026, 10, 1, 8)})
    texts = _texts(renderer.figure)
    assert any(t.startswith("History starts") and "Oct" in t and "01" in t for t in texts), texts
    assert NO_TRAFFIC not in texts


@pytest.mark.parametrize("lang", ["en_US", "de_DE", "zh_CN", "hu_HU"])
def test_no_traffic_note_never_blames_the_apps_pause(monkeypatch, lang):
    """#525 (RainThings): the note said the PC was "off, idle or paused", mixing PC states with the
    app's own Pause. That Pause only freezes the widget's readout - history keeps recording - so it is
    never why a period is empty: the note is the title line plus a PC-state / app-not-running line."""
    i18n = I18nStrings(lang)
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _bare_init)
    r = GraphRenderer(MagicMock(), i18n)
    r._draw_net_empty_message("TIMELINE_WEEK", datetime(2026, 9, 3), datetime(2026, 9, 10),
                              {"earliest": datetime(2026, 8, 1)})
    text = r._net_empty_artist.get_text()
    assert text == r._lbl(f"{i18n.NET_GRAPH_NO_TRAFFIC_TITLE}\n{i18n.NET_GRAPH_NO_TRAFFIC_REASON}")
    assert "NET_GRAPH_NO_TRAFFIC" not in i18n._strings         # the mixed sentence is gone everywhere
    en = I18nStrings("en_US").NET_GRAPH_NO_TRAFFIC_REASON
    assert "paused" not in en.lower() and "asleep" in en and "NetSpeedTray" in en


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate"])
def test_cpu_monitored_but_not_recorded_points_to_the_record_switch(renderer, stat):
    """RC M6: CPU on + Record off (GPU off) said "turn on CPU or GPU monitoring" - but CPU IS on, and
    turning GPU on would record nothing either. The record switch is the reason."""
    assert _render(renderer, stat, "TIMELINE_24_HOURS",
                   history_off={"cpu": "record", "gpu": "monitor"}) == ("record", None)


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate"])
def test_monitoring_on_or_session_view_still_says_collecting(renderer, stat):
    assert _render(renderer, stat, "TIMELINE_24_HOURS", history_off=RECORDING) is None
    assert COLLECTING in _texts(renderer.figure)
    assert _render(renderer, stat, "TIMELINE_SESSION", history_off=MON_OFF) is None
    assert COLLECTING in _texts(renderer.figure)


def test_hebrew_messages_are_shaped_for_matplotlib(monkeypatch):
    """matplotlib has no bidi: Hebrew must go through shape_rtl like every other graph label."""
    from netspeedtray.utils.mpl_fonts import matplotlib_does_bidi, shape_rtl
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _bare_init)
    he = I18nStrings("he_IL")
    r = GraphRenderer(MagicMock(), he)
    for history_off, key in ((RECORDING, "COLLECTING_DATA_MESSAGE"),):
        _render(r, "hwcombined", "TIMELINE_24_HOURS", history_off)
        shaped = shape_rtl(getattr(he, key), "he_IL")
        # reshaped on matplotlib < 3.11; 3.11+ does its own bidi and gets the logical text
        assert (shaped != getattr(he, key)) is not matplotlib_does_bidi() and shaped in _texts(r.figure)
    ax = r.figure.add_subplot(1, 1, 1)
    end = datetime.now()
    ax.set_xlim(end - timedelta(hours=2), end)
    r._add_event_markers(ax, end - timedelta(hours=2), end, boot_time=end - timedelta(hours=1))
    assert [t.get_text() for t in ax.texts] == [f" ↑ {shape_rtl(he.GRAPH_BOOT_MARKER, 'he_IL')}"]


@pytest.mark.parametrize("cfg, off", [
    ({}, {**MON_OFF, "ram": "monitor"}),                                  # 2.2 defaults
    ({"monitor_cpu_enabled": True}, {"cpu": None, "gpu": "monitor", "ram": "monitor"}),
    ({"monitor_gpu_enabled": True}, {"cpu": "monitor", "gpu": None, "ram": "monitor"}),
    ({"monitor_cpu_enabled": True, "monitor_gpu_enabled": True, "record_hardware_history": False},
     {**REC_OFF, "ram": "monitor"}),
    # 2.2.1: RAM is recorded on its own switch, CPU monitoring off or not.
    ({"monitor_ram_enabled": True}, {**MON_OFF, "ram": None}),
    ({"monitor_ram_enabled": True, "record_hardware_history": False}, {**MON_OFF, "ram": "record"}),
])
def test_graph_host_derives_reasons_from_the_writer_rule(q_app, cfg, off):
    from netspeedtray.views.monitor.graph_host import GraphHost
    host = GraphHost(MagicMock(), cfg, I18N)
    assert host._hw_styles()["history_off"] == off
