"""
Hardware vendor detection + the Monitor's combined CPU/GPU graph plumbing: the vendor palette
(brand hues, the collision rule, override), GraphHost._hw_styles, and the worker's hwcombined stat.
"""
from datetime import datetime, timedelta

import pytest

from netspeedtray.utils import hardware_vendors as hv


def test_vendors_return_known_values():
    assert hv.cpu_vendor() in {"intel", "amd", "unknown"}
    assert hv.gpu_vendor() in {"nvidia", "amd", "intel", "unknown"}


def test_cpu_solid_gpu_dashed():
    cc, cs = hv.graph_line_style("cpu")
    gc, gs = hv.graph_line_style("gpu")
    assert cs == "solid"
    assert gs != "solid"                 # GPU is dashed
    assert cc.startswith("#") and gc.startswith("#")


def test_override_color_wins():
    c, s = hv.graph_line_style("cpu", "#ABCDEF")
    assert c == "#ABCDEF" and s == "solid"


def test_classify_gpu():
    assert hv._classify_gpu("NVIDIA GeForce RTX 4080") == "nvidia"
    assert hv._classify_gpu("AMD Radeon RX 7900 XT") == "amd"
    assert hv._classify_gpu("Intel(R) Arc(TM) A770 Graphics") == "intel"
    assert hv._classify_gpu("Intel(R) UHD Graphics 770") == "intel"
    assert hv._classify_gpu("Microsoft Basic Display Adapter") == "unknown"


def test_cpu_and_gpu_colors_always_distinct():
    """The whole point of the collision rule: CPU and GPU defaults must differ even on a same-vendor
    box (AMD red Ryzen + AMD red Radeon; Intel blue Core + Intel blue iGPU)."""
    assert hv.default_color("cpu") != hv.default_color("gpu")


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_graphhost_hw_styles_override_and_default(q_app):
    from unittest.mock import MagicMock
    from netspeedtray.views.monitor.graph_host import GraphHost
    from netspeedtray.constants.i18n import I18nStrings

    host = GraphHost(MagicMock(), {"monitor_cpu_graph_color": "#111111",
                                   "monitor_gpu_graph_color": "#222222"}, I18nStrings("en_US"))
    s = host._hw_styles()
    assert s["cpu"][0] == "#111111" and s["cpu"][1] == "solid"
    assert s["gpu"][0] == "#222222" and s["gpu"][1] != "solid"

    host2 = GraphHost(MagicMock(), {}, I18nStrings("en_US"))   # no override -> vendor default
    s2 = host2._hw_styles()
    assert s2["cpu"][0].startswith("#") and s2["gpu"][0].startswith("#")


def test_worker_hwcombined_emits_cpu_gpu_dict(q_app):
    """stat_type='hwcombined' returns a {cpu, gpu} dict (one axis), not a single-stat list."""
    from unittest.mock import MagicMock
    from netspeedtray.views.graph.worker import GraphDataWorker
    from netspeedtray.views.graph.request import DataRequest

    class _Snap:
        def __init__(self, v, t):
            self.value, self.timestamp = v, t

    now = datetime.now()
    ws = MagicMock()
    ws.cpu_history = [_Snap(40.0, now), _Snap(55.0, now)]
    ws.gpu_history = [_Snap(20.0, now)]
    worker = GraphDataWorker(ws)
    got = []
    worker.data_ready.connect(lambda *a: got.append(a))
    req = DataRequest(start_time=now - timedelta(minutes=1), end_time=now + timedelta(minutes=1),
                      interface_name=None, is_session_view=True, sequence_id=1, stat_type="hwcombined")
    worker.process_data(req)
    data = got[-1][0]
    assert isinstance(data, dict) and set(data) == {"cpu", "gpu"}
    assert len(data["cpu"]) == 2 and len(data["gpu"]) == 1
