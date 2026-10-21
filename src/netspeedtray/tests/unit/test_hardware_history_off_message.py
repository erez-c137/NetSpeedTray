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


def _render(r, stat, period, history_off):
    end = datetime.now()
    data = {"cpu": [], "gpu": [], "ram": []} if stat.startswith("hw") else []
    r.render(data, end - timedelta(hours=24), end, period, stat_type=stat,
             hw_styles={"history_off": history_off})


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate", "cpu"])
def test_ranged_view_with_monitoring_off_says_how_to_turn_it_on(renderer, stat):
    _render(renderer, stat, "TIMELINE_24_HOURS", history_off=True)
    texts = _texts(renderer.figure)
    assert OFF in texts
    assert COLLECTING not in texts


@pytest.mark.parametrize("stat", ["hwcombined", "hwseparate"])
def test_monitoring_on_or_session_view_still_says_collecting(renderer, stat):
    _render(renderer, stat, "TIMELINE_24_HOURS", history_off=False)
    assert COLLECTING in _texts(renderer.figure)
    _render(renderer, stat, "TIMELINE_SESSION", history_off=True)
    texts = _texts(renderer.figure)
    assert COLLECTING in texts and OFF not in texts


@pytest.mark.parametrize("cfg, off", [
    ({}, True),                                   # 2.2 defaults: CPU and GPU monitoring off
    ({"monitor_cpu_enabled": True}, False),
    ({"monitor_gpu_enabled": True}, False),
])
def test_graph_host_flags_history_off_from_config(q_app, cfg, off):
    from netspeedtray.views.monitor.graph_host import GraphHost
    host = GraphHost(MagicMock(), cfg, I18N)
    assert host._hw_styles()["history_off"] is off
