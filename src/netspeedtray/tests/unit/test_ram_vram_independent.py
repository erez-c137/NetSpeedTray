"""
RAM / VRAM without CPU / GPU monitoring (2.2.1, item 2).

Before 2.2.1 "RAM rides on CPU, VRAM rides on GPU": the monitor thread already COLLECTED RAM / VRAM on
their own switches (rc.2 G5), but every view still needed CPU / GPU monitoring on - so a RAM-only or
VRAM-only widget painted nothing, the hover card listed nothing, RAM was recorded only with CPU on and
VRAM never. Now each is a readout of its own:

- collection: VRAM alone polls only the PDH adapter-memory counters - no \\GPU Engine wildcard, no
  temperature / power sources and never nvidia-smi (its total comes from the cache or DXGI);
- the widget: a slot (CPU or RAM, GPU or VRAM) whose usage monitor is off paints a memory-only row,
  "RAM 9.2/16.0G", in side by side (in a row or stacked), Auto-Cycle and the side-bar layouts, sized
  from the stable memory reference (never a live value);
- history: RAM and VRAM % are recorded on their own switches (the 'ram' / 'vram' hw_* metrics);
- the metrics endpoint, the cycle fallback and Settings follow the same rule.
The byte-identical guarantee for the configs that could already show memory is
test_ram_vram_render_guard.
"""
import contextlib
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import gpu_adapters
from netspeedtray.core.controller import StatsController
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.monitor_thread import StatsMonitorThread
from netspeedtray.tests.unit.test_ram_vram_render_guard import (BOTTOM, METRICS, RIGHT, TASKBARS, _config,
                                                                 _size)
from netspeedtray.utils import widget_renderer as wr
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import WidgetRenderer

EN = I18nStrings("en_US")
MIB = 1024.0 * 1024.0
GIB = 1024 ** 3

SBS, CYCLE = dict(widget_display_mode="side_by_side"), dict(widget_display_mode="cycle")
RAM = dict(monitor_ram_enabled=True)
VRAM = dict(monitor_vram_enabled=True)
MEM = dict(RAM, **VRAM)

# name: (config, {phase: (texts that must be painted, texts that must not)})
SCENARIOS = {
    "sbs_ram": (dict(SBS, **RAM), {"network_only": ({"RAM", "9.2/16.0G"}, {"VRAM", "3.1/8.0G"})}),
    "sbs_vram": (dict(SBS, **VRAM), {"network_only": ({"VRAM", "3.1/8.0G"}, {"RAM", "9.2/16.0G"})}),
    "sbs_ram_vram": (dict(SBS, **MEM), {"network_only": ({"RAM", "9.2/16.0G", "VRAM", "3.1/8.0G"}, set())}),
    "sbs_ram_vram_in_a_row": (dict(SBS, stack_hardware_stats=False, **MEM),
                              {"network_only": ({"RAM", "9.2/16.0G", "VRAM", "3.1/8.0G"}, set())}),
    "sbs_cpu_ram_vram_in_a_row": (dict(SBS, monitor_cpu_enabled=True, stack_hardware_stats=False, **MEM),
                                  {"network_only": ({"37%", "9.2/16.0G", "VRAM", "3.1/8.0G"}, {"52%"})}),
    "sbs_ram_vram_stacked": (dict(SBS, stack_hardware_stats=True, **MEM),
                             {"network_only": ({"RAM", "9.2/16.0G", "VRAM", "3.1/8.0G"}, set())}),
    "sbs_ram_vram_text_labels": (dict(SBS, hardware_label_style="text", show_memory_labels=True,
                                      show_hardware_temps=True, show_hardware_power=True, **MEM),
                                 {"network_only": ({"RAM", "9.2/16.0G", "VRAM", "3.1/8.0G"},
                                                   {"CPU", "GPU"})}),
    # CPU monitored with its RAM, VRAM on its own: the GPU slot is a memory-only row.
    "sbs_cpu_ram_vram": (dict(SBS, monitor_cpu_enabled=True, **MEM),
                         {"network_only": ({"37%", "9.2/16.0G", "VRAM", "3.1/8.0G"}, {"52%"})}),
    "sbs_cpu_ram_vram_stacked": (dict(SBS, monitor_cpu_enabled=True, stack_hardware_stats=True, **MEM),
                                 {"network_only": ({"37%", "9.2/16.0G", "VRAM", "3.1/8.0G"}, {"52%"})}),
    "cycle_ram_vram": (dict(CYCLE, **MEM), {"cpu_only": ({"RAM", "9.2/16.0G"}, {"VRAM"}),
                                            "gpu_only": ({"VRAM", "3.1/8.0G"}, {"RAM"})}),
}
HORIZONTAL = [t for t in TASKBARS if t[1] == BOTTOM]


def _paint(cfg, w, h, network_width, layout_mode, phase, metrics=METRICS, *, canvas_pad=0):
    """Paint the widget like paintEvent does; returns (strings drawn, alpha array, pad). With
    ``canvas_pad`` the widget sits in a larger canvas with clipping off, so overflow is visible."""
    drawn = []
    real = wr.draw_text

    def spy(painter, x, y, text, *a, **k):
        drawn.append(str(text))
        return real(painter, x, y, text, *a, **k)

    renderer = WidgetRenderer(cfg, EN)
    img = QImage(w + 2 * canvas_pad, h + 2 * canvas_pad, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.translate(canvas_pad, canvas_pad)
    with patch.object(wr, "draw_text", spy), contextlib.ExitStack() as stack:
        if canvas_pad:   # the paint path clips to the widget rect; a clip would hide the overflow
            stack.enter_context(patch.object(QPainter, "setClipRect", lambda *a, **k: None))
        render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, metrics, layout_mode=layout_mode,
                      cycle_mode=phase, network_width=network_width, font=font_from_config(cfg))
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    alpha = np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4))[:, :, 3].copy()
    return drawn, alpha


# --- the widget: memory-only rows ------------------------------------------------------------------

@pytest.mark.parametrize("tb", HORIZONTAL, ids=[t[0] for t in HORIZONTAL])
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_memory_alone_is_painted_on_a_horizontal_bar(q_app, name, tb):
    over, phases = SCENARIOS[name]
    cfg = _config(over)
    _tname, edge, thickness, dpi, small = tb
    w, h, nw = _size(cfg, edge, thickness, dpi, small)
    for phase, (want, never) in phases.items():
        drawn, _alpha = _paint(cfg, w, h, nw, "horizontal", phase)
        assert want <= set(drawn), (name, phase, want - set(drawn), drawn)
        assert not (never & set(drawn)), (name, phase, never & set(drawn))
        # A memory-only row has no percent of its own: only CPU's 37% may appear.
        assert not [t for t in drawn if t.endswith("%") and t != "37%"], (name, phase, drawn)


@pytest.mark.parametrize("tb", HORIZONTAL, ids=[t[0] for t in HORIZONTAL])
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_memory_only_rows_fit_the_width_the_layout_reserved(q_app, name, tb):
    """The layout sizes a memory-only row from the same helper the renderer paints with: no ink is
    painted outside the widget, in any phase (the #106 clip class)."""
    over, phases = SCENARIOS[name]
    cfg = _config(over)
    _tname, edge, thickness, dpi, small = tb
    w, h, nw = _size(cfg, edge, thickness, dpi, small)
    pad = w
    for phase in phases:
        _drawn, a = _paint(cfg, w, h, nw, "horizontal", phase, canvas_pad=pad)
        assert (a[pad:pad + h, pad:pad + w] > 24).any(), "nothing painted"
        a[pad:pad + h, pad:pad + w] = 0
        ys, xs = np.nonzero(a > 24)
        assert not len(xs), (name, phase, "ink outside the widget", int(xs.min()) - pad, int(xs.max()) - pad, w)


@pytest.mark.parametrize("name", ["sbs_ram", "sbs_ram_vram", "cycle_ram_vram"])
def test_memory_only_width_is_sized_from_the_reference_not_live_values(q_app, name):
    """CLAUDE.md layout rule: the widget is sized once from stable reference strings - a cold start
    (no reading yet) and a full machine size the same."""
    over, _phases = SCENARIOS[name]
    cfg = _config(over)
    with patch("psutil.virtual_memory", return_value=types.SimpleNamespace(total=16 * GIB)):
        known = _size(cfg, BOTTOM, 48, 1.0, False)                         # 16 GB RAM, 8 GB VRAM
        cold = _size(cfg, BOTTOM, 48, 1.0, False, totals=(None, None))     # the first resize: no reading
    assert cold[:2] == known[:2]


def test_memory_only_row_reads_na_until_measured(q_app):
    cfg = _config(dict(SBS, **MEM))
    w, h, nw = _size(cfg, BOTTOM, 48, 1.0, False)
    cold = WidgetMetrics(upload_mbps=1.0, download_mbps=2.0, cpu_usage=float("nan"),
                         gpu_usage=float("nan"))
    drawn, _a = _paint(cfg, w, h, nw, "horizontal", "network_only", metrics=cold)
    assert drawn.count(EN.DEFAULT_TEXT) >= 2 and {"RAM", "VRAM"} <= set(drawn), drawn


def _row_cells(cfg, **kw):
    """(x of each string drawn, segment width) for one draw_hardware_stats call."""
    renderer = WidgetRenderer(cfg, EN)
    xs = {}
    real = wr.draw_text

    def spy(painter, x, y, text, *a, **k):
        xs[str(text)] = x
        return real(painter, x, y, text, *a, **k)
    img = QImage(400, 48, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    try:
        with patch.object(wr, "draw_text", spy):
            renderer.draw_hardware_stats(p, None, None, 400, 48, renderer.config, layout_mode="horizontal", **kw)
    finally:
        p.end()
    return xs, renderer.get_last_text_rect().width(), renderer.metrics


@pytest.mark.parametrize("word, kw", [
    ("RAM", dict(ram_info=(9.2, 16.0), ram_only=True)),
    ("VRAM", dict(vram_info=(3.1, 8.0), vram_only=True)),
])
def test_a_lone_memory_row_sizes_its_label_cell_for_its_own_word(q_app, word, kw):
    """RAM alone (or VRAM alone): no wide shared "VRAM " cell between the word and its number - the
    cell is the word's own, from the fixed label string; the layout reserves the same."""
    from netspeedtray.utils.widget_renderer import memory_only_row_width
    xs, seg, fm = _row_cells(_config({}), **kw)
    t = 16.0 if word == "RAM" else 8.0
    num_col = fm.horizontalAdvance(f"{t:.1f}/{t:.1f}G")
    margin = constants.renderer.TEXT_MARGIN
    assert xs[word] == margin
    assert seg == memory_only_row_width(fm, num_col, word) + margin
    assert seg == fm.horizontalAdvance(word + " ") + num_col + margin


def test_stacked_memory_rows_share_the_label_cell_so_they_line_up(q_app):
    from netspeedtray.utils.helpers import memory_label_width
    xs, seg, fm = _row_cells(_config({}), ram_info=(9.2, 16.0), vram_info=(3.1, 8.0),
                             ram_only=True, vram_only=True)
    assert xs["RAM"] == xs["VRAM"]                                    # labels aligned
    num_col = fm.horizontalAdvance("16.0/16.0G")
    assert seg == memory_label_width(fm) + num_col + constants.renderer.TEXT_MARGIN


@pytest.mark.parametrize("thickness", [48, 32])
def test_side_bar_cycle_shows_memory_alone_as_a_compact_percent(q_app, thickness):
    """A side taskbar too narrow for "RAM 9.2/16.0G": the compact row is the word over (or beside)
    the memory's percent used - 9.2 of 16.0 GB is 57% - and the GB values move to the hover card."""
    cfg = _config(dict(CYCLE, **MEM))
    w, h, nw = _size(cfg, RIGHT, thickness, 1.0, False)
    drawn, _a = _paint(cfg, w, h, nw, "vertical", "cpu_only")
    assert "RAM" in drawn and any(t.rstrip("%") == "57" for t in drawn), drawn
    drawn, _a = _paint(cfg, w, h, nw, "vertical", "gpu_only")
    assert "VRAM" in drawn and any(t.rstrip("%") == "38" for t in drawn), drawn   # 3.1 of 8.0


def test_side_bar_side_by_side_moves_memory_alone_to_the_hover_card(q_app):
    from netspeedtray.utils.widget_paint import side_bar_fit, side_hardware_stats
    from netspeedtray.utils.widget_renderer import RenderConfig
    cfg = _config(dict(SBS, **MEM))
    rc = RenderConfig.from_dict(cfg)
    assert side_hardware_stats(rc, "side_by_side") == ["RAM", "VRAM"]
    assert side_hardware_stats(RenderConfig.from_dict(_config(dict(SBS, monitor_cpu_enabled=True, **VRAM))),
                               "side_by_side") == ["CPU", "VRAM"]
    renderer = WidgetRenderer(cfg, EN)
    fit = side_bar_fit(rc, renderer.metrics, renderer.arrow_metrics, EN, 44, mem_totals=(16.0, 8.0))
    assert fit.hides_hardware


def test_stacked_side_by_side_reserves_one_row_per_memory_only_slot():
    from netspeedtray.utils.widget_paint import stacked_segment_height
    # Before 2.2.1 (CPU / GPU on): usage + memory rows, unchanged.
    assert stacked_segment_height("cpu", 10, True, False) == 20
    assert stacked_segment_height("hardware", 10, True, True) == 40
    # A memory-only slot is its one row.
    assert stacked_segment_height("cpu", 10, True, False, cpu_enabled=False) == 10
    assert stacked_segment_height("hardware", 10, True, True, cpu_enabled=False, gpu_enabled=False) == 20
    assert stacked_segment_height("hardware", 10, True, True, cpu_enabled=True, gpu_enabled=False) == 30


def test_auto_cycle_rotates_through_memory_alone():
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    seen = []
    fake = types.SimpleNamespace(config={"widget_display_mode": "cycle", **MEM}, _cycle_index=0,
                                 _current_cycle_mode="network_only", update=MagicMock())
    for _ in range(3):
        NetworkSpeedWidget._step_cycle(fake, 1)
        seen.append(fake._current_cycle_mode)
    assert seen == ["cpu_only", "gpu_only", "network_only"]


def test_cycle_is_kept_with_only_ram_or_vram_on():
    from netspeedtray.core.config_controller import ConfigController
    widget = MagicMock()
    widget.config = {"widget_display_mode": "cycle"}
    cc = ConfigController(widget, MagicMock())
    with patch.object(cc, "apply_all_settings"):
        cc.handle_settings_changed({"widget_display_mode": "cycle", "monitor_ram_enabled": True},
                                   save_to_disk=False)
        assert widget.config["widget_display_mode"] == "cycle"
        cc.handle_settings_changed({"widget_display_mode": "cycle", "monitor_ram_enabled": False},
                                   save_to_disk=False)
        assert widget.config["widget_display_mode"] == "network_only"


# --- collection: VRAM alone is a cheap poll ---------------------------------------------------------

RTX = AdapterInfo("NVIDIA GeForce RTX 4070", 0x10DE, (0, 0xAB01), 12 * GIB, int(8 * GIB), False)
BRD = AdapterInfo("Microsoft Basic Render Driver", 0x1414, (0, 0xDD11), 0, int(8 * GIB), True)
DGPU_I = "luid_0x00000000_0x0000AB01_phys_0"
SMI = "64, 12282, 110.5"


@pytest.fixture
def thread(q_app, monkeypatch):
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: [RTX, BRD])
    t = StatsMonitorThread(interval=0.05)
    t.logger = MagicMock()
    t._nvidia_smi_path = "C:/fake/nvidia-smi.exe"
    t._run_child = MagicMock(return_value=SMI)
    t._NVIDIA_POLL_INTERVAL_SEC = 0.0
    t._wmi_ohm = False
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    t._gpu_adapters = [RTX, BRD]
    return t


def _poll_mem(t, memory_only):
    arrays = ([{DGPU_I: 3000 * MIB}, {DGPU_I: 5 * MIB}] if t._gpu_util_counter is None
              else [{}, {DGPU_I: 3000 * MIB}, {DGPU_I: 5 * MIB}])
    with patch("win32pdh.CollectQueryData"), patch("win32pdh.GetFormattedCounterArray", side_effect=arrays):
        return t._poll_gpu_hybrid(include_temp=False, include_power=False, memory_only=memory_only)


@pytest.mark.parametrize("picked", ["", "NVIDIA GeForce RTX 4070"], ids=["automatic", "picked"])
def test_vram_alone_never_spawns_nvidia_smi_and_keeps_its_total(thread, picked):
    thread.config = {"gpu_adapter": picked}
    if picked:   # the picked card's own line: name,temperature,total,power
        thread._run_child.return_value = f"{picked}, 64, 12282, 110.5"
    thread._gpu_query, thread._gpu_util_counter, thread._gpu_vram_counter, thread._gpu_shared_counter = \
        123, None, 2, 3
    for _ in range(3):
        r = _poll_mem(thread, memory_only=True)
    thread._run_child.assert_not_called()
    assert r.vram_used == pytest.approx(3000.0)
    assert r.vram_total == pytest.approx(12 * 1024)         # DXGI's dedicated size, what Task Manager shows
    # With the GPU monitored nvidia-smi still supplies the total, exactly as before (golden: #236).
    thread._gpu_util_counter = 1
    r = _poll_mem(thread, memory_only=False)
    assert thread._run_child.call_count == 1 and r.vram_total == pytest.approx(12282.0)


def test_vram_alone_uses_a_cached_nvidia_smi_total(thread):
    thread.config = {"gpu_adapter": ""}
    thread._gpu_query, thread._gpu_util_counter, thread._gpu_vram_counter, thread._gpu_shared_counter = \
        123, None, 2, 3
    thread._nvidia_cache_vram_total = 12282.0
    r = _poll_mem(thread, memory_only=True)
    thread._run_child.assert_not_called()
    assert r.vram_total == pytest.approx(12282.0)


@pytest.mark.parametrize("want_util", [False, True])
def test_gpu_query_carries_the_engine_wildcard_only_for_gpu_usage(thread, want_util):
    thread._gpu_util_wanted = want_util
    with patch("win32pdh.OpenQuery", return_value=77), \
            patch("win32pdh.AddCounter", side_effect=lambda q, path: hash(path)) as add, \
            patch("win32pdh.CollectQueryData"):
        assert thread._init_gpu_query()
    paths = [c.args[1] for c in add.call_args_list]
    assert any("Dedicated Usage" in p for p in paths) and any("Shared Usage" in p for p in paths)
    assert any("GPU Engine" in p for p in paths) is want_util
    assert thread._gpu_query_has_util is want_util
    thread._gpu_query = None


class _Pdh:
    """win32pdh stand-in that records every query built and counter added."""

    def __init__(self):
        self.opened, self.added, self.closed = 0, [], 0

    def patches(self):
        def open_query():
            self.opened += 1
            return 1000 + self.opened

        def add_counter(q, path):
            self.added.append((q, path))
            return len(self.added)

        def close_query(q):
            self.closed += 1

        def arrays(counter, fmt):
            path = self.added[counter - 1][1]
            if "GPU Engine" in path:
                return {}
            return {DGPU_I: (3000 if "Dedicated" in path else 5) * MIB}
        return [patch("win32pdh.OpenQuery", open_query), patch("win32pdh.AddCounter", add_counter),
                patch("win32pdh.CloseQuery", close_query), patch("win32pdh.CollectQueryData"),
                patch("win32pdh.GetFormattedCounterArray", arrays)]


def _ticks(thread, config, n=1, between=None):
    """Run the real monitor loop for ``n`` emitted polls; ``between(i)`` runs after poll i."""
    thread.config = config
    thread._is_running = True
    got = []

    def emit(stats):
        got.append(dict(stats))
        if between:
            between(len(got))
        if len(got) >= n:
            thread._is_running = False

    thread.stats_ready = types.SimpleNamespace(emit=emit)
    pdh = _Pdh()
    ps = pdh.patches()
    with patch("netspeedtray.core.monitor_thread.is_rdp_session", return_value=False), \
            patch.object(thread, "_init_com"), patch.object(thread, "_cleanup_com"), \
            patch.object(thread, "_collect_power_stats"), \
            patch.object(thread, "_init_ohm_wmi") as ohm, \
            patch.object(thread, "_poll_cpu_temperature") as cpu_temp, \
            patch.object(thread, "_poll_cpu_power") as cpu_power, \
            patch("netspeedtray.core.monitor_thread.psutil.cpu_percent", return_value=10.0) as cpu_pct, \
            ps[0], ps[1], ps[2], ps[3], ps[4]:
        thread.run()
    return got, pdh, types.SimpleNamespace(ohm=ohm, cpu_temp=cpu_temp, cpu_power=cpu_power, cpu_pct=cpu_pct)


def test_vram_alone_polls_only_the_adapter_memory_counters(thread):
    got, pdh, m = _ticks(thread, {"monitor_vram_enabled": True, "show_hardware_temps": True,
                                  "show_hardware_power": True}, n=2)
    thread._run_child.assert_not_called()                     # no nvidia-smi, not even for the total
    assert pdh.opened == 1 and not any("GPU Engine" in p for _q, p in pdh.added)
    assert got[-1]["vram_used"] == pytest.approx(3000 / 1024) and got[-1]["vram_total"] == pytest.approx(12.0)
    assert "gpu" not in got[-1] and "gpu_temp" not in got[-1] and "gpu_power" not in got[-1]
    thread._lhm_http.get_snapshot.assert_not_called()         # no sensor sources
    m.ohm.assert_not_called()
    assert m.cpu_pct.call_count == 1                          # the loop's one priming call, as always


def test_ram_alone_touches_no_gpu_or_sensor_source(thread):
    got, pdh, m = _ticks(thread, {"monitor_ram_enabled": True, "show_hardware_temps": True}, n=1)
    assert got[0]["ram_used"] > 0 and "cpu" not in got[0] and "vram_used" not in got[0]
    assert pdh.opened == 0 and pdh.added == []
    thread._run_child.assert_not_called()
    m.cpu_temp.assert_not_called()
    m.cpu_power.assert_not_called()
    assert m.cpu_pct.call_count == 1


def test_gpu_query_is_rebuilt_when_the_monitor_window_forces_gpu_usage(thread):
    def force_after_first(i):
        if i == 1:
            thread._force_hardware_collection = True
    got, pdh, _m = _ticks(thread, {"monitor_vram_enabled": True}, n=2, between=force_after_first)
    first = [p for q, p in pdh.added if q == 1001]
    second = [p for q, p in pdh.added if q == 1002]
    assert not any("GPU Engine" in p for p in first)
    assert any("GPU Engine" in p for p in second)             # forced: GPU usage needs the wildcard
    assert "gpu" in got[1]


# --- history, metrics endpoint, Settings ----------------------------------------------------------

def _cfg(**kw):
    c = {"record_hardware_history": True, "monitor_cpu_enabled": False, "monitor_gpu_enabled": False,
         "monitor_ram_enabled": False, "monitor_vram_enabled": False}
    c.update(kw)
    return c


def test_controller_records_ram_on_its_own_switch_and_never_vram(q_app):
    """RAM is recorded without CPU monitoring. VRAM is shown, not recorded: nothing reads a VRAM
    history yet, so a row per second would only cost battery and disk I/O (2.2.1 decision)."""
    ws = MagicMock()
    ctl = StatsController(_cfg(monitor_ram_enabled=True, monitor_vram_enabled=True), ws)
    vram = []
    ctl.vram_info_updated.connect(lambda u, t: vram.append((u, t)))
    ctl.handle_stats({"ram_used": 4.0, "ram_total": 16.0, "vram_used": 2.0, "vram_total": 8.0,
                      "gpu_present": False})
    calls = {c.args[0]: (c.args[1], c.kwargs.get("persist")) for c in ws.add_hardware_stat.call_args_list}
    assert calls == {"ram": (25.0, True)}
    assert vram == [(2.0, 8.0)]                  # ... while the widget still gets every reading


def test_hover_card_lists_memory_without_a_usage_row(q_app):
    from PyQt6.QtWidgets import QLabel
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware={"ram": (9.2, 16.0), "vram": (3.1, 8.0)})
    try:
        texts = [lbl.text() for lbl in card.findChildren(QLabel)]
        assert card.has_hardware and "RAM" in texts and "VRAM" in texts
        assert "CPU" not in texts and "GPU" not in texts
    finally:
        card.hide()
        card.deleteLater()


def test_ram_alone_reaches_the_history_db_and_vram_writes_nothing(q_app, tmp_path, monkeypatch):
    """The real write path: RAM-only polls land in hw_raw as the 'ram' metric (unit percent) and read
    back through the summary the Monitor uses; VRAM-only polls leave no 'vram' metric at all."""
    from datetime import datetime, timedelta
    import sqlite3
    import netspeedtray.core.widget_state as wsm
    from netspeedtray.core.db.schema_v8 import METRICS
    from netspeedtray.core.db.sqlite_uri import sqlite_ro_uri
    from netspeedtray.tests.unit.test_hardware_recording import _live_state

    state = _live_state(tmp_path)
    try:
        ctl = StatsController(_cfg(monitor_ram_enabled=True, monitor_vram_enabled=True), state)
        ctl.set_view(MagicMock())
        base = datetime.now() - timedelta(minutes=1)
        tick = {"n": 0}

        def _fake_now():
            tick["n"] += 1
            return base + timedelta(seconds=3 * tick["n"])
        monkeypatch.setattr(wsm, "datetime", type("_DT", (), {"now": staticmethod(_fake_now)}))
        ctl.handle_stats({"ram_used": 4.0, "ram_total": 16.0, "vram_used": 2.0, "vram_total": 8.0})
        ctl.handle_stats({"ram_used": 8.0, "ram_total": 16.0, "vram_used": 6.0, "vram_total": 8.0})
        state.flush_and_wait(timeout=5.0)
        monkeypatch.undo()
        now = datetime.now()
        ram = state.summarize_hardware("ram", now - timedelta(hours=1), now, poll_interval=1.0)
        vram = state.summarize_hardware("vram", now - timedelta(hours=1), now, poll_interval=1.0)
        assert ram.count == 2 and ram.avg == pytest.approx(37.5)
        assert vram.count == 0
        conn = sqlite3.connect(sqlite_ro_uri(state._db_path), uri=True)
        try:
            units = dict(conn.execute(f"SELECT name, unit FROM {METRICS} WHERE name IN ('ram', 'vram')"))
        finally:
            conn.close()
        assert units == {"ram": "percent"}
    finally:
        state.cleanup()


def test_metrics_endpoint_reports_memory_alone():
    from netspeedtray.core.metrics_snapshot import build_snapshot
    from netspeedtray.tests.unit.test_metrics_endpoint import _fake_widget
    snap = build_snapshot(_fake_widget({"monitor_cpu_enabled": False, "monitor_gpu_enabled": False,
                                        "monitor_ram_enabled": True, "monitor_vram_enabled": True}))
    assert snap.cpu_ratio is None and snap.gpu_ratio is None
    assert snap.ram_used_bytes == pytest.approx(8.0 * GIB) and snap.ram_total_bytes == pytest.approx(16.0 * GIB)
    assert snap.vram_used_bytes == pytest.approx(1.0 * GIB) and snap.vram_total_bytes == pytest.approx(4.0 * GIB)


@pytest.mark.parametrize("key", ["monitor_ram_enabled", "monitor_vram_enabled"])
def test_record_hardware_history_card_is_live_with_memory_alone(q_app, key):
    from netspeedtray.views.settings.pages.advanced import AdvancedPage
    page = AdvancedPage(EN, lambda: None)
    try:
        page.load_settings({})
        assert not page._record_card.isEnabled()
        page.load_settings({key: True})
        assert page._record_card.isEnabled()
    finally:
        page.deleteLater()
