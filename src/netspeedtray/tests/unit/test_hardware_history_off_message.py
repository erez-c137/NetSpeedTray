"""
The Monitor's Hardware graph with nothing recorded. Since 2.2, hardware history is written only for
the CPU/GPU stats the user monitors, and both are off by default - so at 24h/7d the graph used to say
"Collecting data for current session..." forever. With monitoring off, a ranged view now says where to
turn recording on; the live SESSION view (fed by the Monitor's forced collection) keeps "Collecting".

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
OFF = I18N.HARDWARE_HISTORY_OFF_MESSAGE
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
RECORD_OFF = I18N.HARDWARE_HISTORY_RECORD_OFF_MESSAGE


def _render(r, stat, period, history_off):
    end = datetime.now()
    data = {"cpu": [], "gpu": [], "ram": []} if stat.startswith("hw") else []
    r.render(data, end - timedelta(hours=24), end, period, stat_type=stat,
             hw_styles={"history_off": history_off})


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate", "cpu", "gpu"])
def test_ranged_view_with_monitoring_off_says_how_to_turn_it_on(renderer, stat):
    _render(renderer, stat, "TIMELINE_24_HOURS", history_off=MON_OFF)
    texts = _texts(renderer.figure)
    assert OFF in texts
    assert COLLECTING not in texts


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate", "cpu", "gpu"])
def test_record_switch_off_points_to_advanced(renderer, stat):
    _render(renderer, stat, "TIMELINE_24_HOURS", history_off=REC_OFF)
    texts = _texts(renderer.figure)
    assert RECORD_OFF in texts and OFF not in texts and COLLECTING not in texts


def test_toggle_layout_gpu_view_with_only_cpu_recorded(renderer):
    """CPU on, GPU off: the GPU-only toggle view will never fill; the CPU one is collecting."""
    mixed = {"cpu": None, "gpu": "monitor"}
    _render(renderer, "gpu", "TIMELINE_24_HOURS", history_off=mixed)
    assert OFF in _texts(renderer.figure)
    _render(renderer, "cpu", "TIMELINE_24_HOURS", history_off=mixed)
    assert COLLECTING in _texts(renderer.figure)
    _render(renderer, "hwcombined", "TIMELINE_24_HOURS", history_off=mixed)   # CPU will fill it
    assert COLLECTING in _texts(renderer.figure)


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate"])
def test_monitoring_on_or_session_view_still_says_collecting(renderer, stat):
    _render(renderer, stat, "TIMELINE_24_HOURS", history_off=RECORDING)
    assert COLLECTING in _texts(renderer.figure)
    _render(renderer, stat, "TIMELINE_SESSION", history_off=MON_OFF)
    texts = _texts(renderer.figure)
    assert COLLECTING in texts and OFF not in texts


def test_hebrew_messages_are_shaped_for_matplotlib(monkeypatch):
    """matplotlib has no bidi: Hebrew must go through shape_rtl like every other graph label."""
    from netspeedtray.utils.mpl_fonts import shape_rtl
    monkeypatch.setattr(GraphRenderer, "_init_matplotlib", _bare_init)
    he = I18nStrings("he_IL")
    r = GraphRenderer(MagicMock(), he)
    for history_off, key in ((MON_OFF, "HARDWARE_HISTORY_OFF_MESSAGE"),
                             (REC_OFF, "HARDWARE_HISTORY_RECORD_OFF_MESSAGE"),
                             (RECORDING, "COLLECTING_DATA_MESSAGE")):
        _render(r, "hwcombined", "TIMELINE_24_HOURS", history_off)
        shaped = shape_rtl(getattr(he, key), "he_IL")
        assert shaped != getattr(he, key) and shaped in _texts(r.figure)
    ax = r.figure.add_subplot(1, 1, 1)
    end = datetime.now()
    ax.set_xlim(end - timedelta(hours=2), end)
    r._add_event_markers(ax, end - timedelta(hours=2), end, boot_time=end - timedelta(hours=1))
    assert [t.get_text() for t in ax.texts] == [f" ↑ {shape_rtl(he.GRAPH_BOOT_MARKER, 'he_IL')}"]


@pytest.mark.parametrize("cfg, off", [
    ({}, MON_OFF),                                                        # 2.2 defaults
    ({"monitor_cpu_enabled": True}, {"cpu": None, "gpu": "monitor"}),
    ({"monitor_gpu_enabled": True}, {"cpu": "monitor", "gpu": None}),
    ({"monitor_cpu_enabled": True, "monitor_gpu_enabled": True, "record_hardware_history": False}, REC_OFF),
])
def test_graph_host_derives_reasons_from_the_writer_rule(q_app, cfg, off):
    from netspeedtray.views.monitor.graph_host import GraphHost
    host = GraphHost(MagicMock(), cfg, I18N)
    assert host._hw_styles()["history_off"] == off
