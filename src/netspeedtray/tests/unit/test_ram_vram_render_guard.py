"""
Render guard for "RAM / VRAM without CPU / GPU" (rc.2 owner decision): every config that could
already show memory - RAM with CPU on, VRAM with GPU on - must size and paint exactly as before.

origin/main @ a72203ea (the commit before the change) sized every scenario below with the real
WidgetLayoutManager and painted it with the real render_widget; ram_vram_render_guard_golden.json
holds the (width, height, sha256 of the painted pixels) it produced. This branch must reproduce every
entry byte for byte: bottom 48 px at 100/125/150%, a Small 32 px bottom bar, and 48/32 px side bars,
for the default config and the common CPU+RAM / GPU+VRAM / both layouts (side by side, stacked,
Auto-Cycle phases, temps + power, memory labels, text labels, the hover-card switch).

The pixels depend on the font the offscreen platform resolves, so the golden also stores a font
fingerprint; on a machine whose fallback font renders differently the test skips (it is a regression
guard for this repo's machines and CI, not a font test).

Regenerate ONLY for a deliberate, reviewed change to these layouts:  python -m
netspeedtray.tests.unit.test_ram_vram_render_guard --write   (from src/, QT_QPA_PLATFORM=offscreen).
"""
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QFontMetrics, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.taskbar_utils import TaskbarInfo
from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.views.widget.layout import WidgetLayoutManager

GOLDEN = Path(__file__).with_name("ram_vram_render_guard_golden.json")
EN = I18nStrings("en_US")
BOTTOM, RIGHT = constants.TaskbarEdge.BOTTOM, constants.TaskbarEdge.RIGHT

METRICS = WidgetMetrics(upload_mbps=12.4, download_mbps=88.6, cpu_usage=37.0, gpu_usage=52.0,
                        cpu_temp=58.0, gpu_temp=64.0, cpu_power=35.0, gpu_power=120.0,
                        ram_used=9.2, ram_total=16.0, vram_used=3.1, vram_total=8.0)

CPU_RAM = dict(monitor_cpu_enabled=True, monitor_ram_enabled=True)
GPU_VRAM = dict(monitor_gpu_enabled=True, monitor_vram_enabled=True)
ALL = dict(CPU_RAM, **GPU_VRAM)
SBS, CYCLE = dict(widget_display_mode="side_by_side"), dict(widget_display_mode="cycle")
CYCLE_PHASES = ["network_only", "cpu_only", "gpu_only"]

SCENARIOS = {
    "default": {},
    "graph": dict(graph_enabled=True),
    "sbs_cpu": dict(SBS, monitor_cpu_enabled=True),
    "sbs_cpu_ram": dict(SBS, **CPU_RAM),
    "sbs_gpu_vram": dict(SBS, **GPU_VRAM),
    "sbs_all": dict(SBS, **ALL),
    "sbs_all_graph": dict(SBS, graph_enabled=True, **ALL),
    "sbs_all_stacked": dict(SBS, stack_hardware_stats=True, **ALL),
    "sbs_all_temps_power_labels": dict(SBS, show_hardware_temps=True, show_hardware_power=True,
                                       show_memory_labels=True, **ALL),
    "sbs_cpu_ram_text": dict(SBS, hardware_label_style="text", **CPU_RAM),
    "sbs_cpu_ram_gpu": dict(SBS, monitor_gpu_enabled=True, **CPU_RAM),
    "cycle_cpu_ram": dict(CYCLE, **CPU_RAM),
    "cycle_all_labels": dict(CYCLE, show_memory_labels=True, **ALL),
    "hover_card_all": dict(SBS, hardware_in_hover_card=True, **ALL),
}

# (name, edge, thickness, dpi, small)
TASKBARS = [
    ("bottom48_100", BOTTOM, 48, 1.0, False),
    ("bottom48_125", BOTTOM, 48, 1.25, False),
    ("bottom48_150", BOTTOM, 48, 1.5, False),
    ("bottom32_small", BOTTOM, 32, 1.0, True),
    ("right48", RIGHT, 48, 1.0, False),
    ("right32", RIGHT, 32, 1.0, False),
]


def _config(over):
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"free_float": False, "background_opacity": 0, "graph_enabled": False,
                "hardware_label_style": "icons"})
    cfg.update(over)
    return cfg


def _taskbar(edge, thickness, dpi):
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 1920, 1080)
    tb = MagicMock(spec=TaskbarInfo)
    phys = round(thickness * dpi)
    if edge == BOTTOM:
        screen.availableGeometry.return_value = QRect(0, 0, 1920, 1080 - thickness)
        tb.rect = (0, round(1080 * dpi) - phys, round(1920 * dpi), round(1080 * dpi))
        tb.height = thickness
        tb.get_tray_rect.return_value = (round(1700 * dpi), tb.rect[1], round(1900 * dpi), tb.rect[3])
    else:
        screen.availableGeometry.return_value = QRect(0, 0, 1920 - thickness, 1080)
        tb.rect = (round(1920 * dpi) - phys, 0, round(1920 * dpi), round(1080 * dpi))
        tb.height = phys
        tb.get_tray_rect.return_value = (tb.rect[0], round(908 * dpi), tb.rect[2], tb.rect[3])
    tb.hwnd, tb.dpi_scale = 12345, dpi
    tb.tasklist_rect = tb.widgets_rect = None
    tb.get_edge_position.return_value = edge
    tb.get_screen.return_value = screen
    return tb


def _size(cfg, edge, thickness, dpi, small):
    widget = MagicMock()
    widget.config = cfg
    widget.i18n = EN
    widget._identity_reserve_px.return_value = 0
    widget._hover_card = None
    widget.ram_total, widget.vram_total = 16.0, 8.0
    lm = WidgetLayoutManager(widget)
    lm.metrics = QFontMetrics(font_from_config(cfg))
    lm.arrow_metrics = lm.metrics
    with patch("netspeedtray.views.widget.layout.get_taskbar_info", return_value=_taskbar(edge, thickness, dpi)), \
            patch("netspeedtray.views.widget.layout.is_small_taskbar", return_value=small), \
            patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", return_value=None):
        lm.resize_widget_for_font()
    (w, h), _ = widget.setFixedSize.call_args
    return int(w), int(h), lm._network_width


def _paint_hash(cfg, w, h, network_width, layout_mode, phase):
    renderer = WidgetRenderer(cfg, EN)
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, METRICS, layout_mode=layout_mode,
                  cycle_mode=phase, network_width=network_width, font=font_from_config(cfg))
    p.end()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return hashlib.sha256(bytes(ptr)).hexdigest()[:16]


def font_fingerprint():
    """Hash of a reference string painted in the default widget font: a different fallback font (a
    machine without the repo's usual fonts) changes it, and the golden pixels can't apply there."""
    cfg = _config({})
    font = font_from_config(cfg)
    img = QImage(160, 24, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setFont(font)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(2, 18, "CPU 37% 9.2/16.0G 88.8 Mbps")
    p.end()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return hashlib.sha256(bytes(ptr)).hexdigest()[:16] + f":{QFontMetrics(font).horizontalAdvance('88.8 Mbps')}"


def run_all():
    out = {}
    for sname, over in SCENARIOS.items():
        cfg = _config(over)
        phases = CYCLE_PHASES if over.get("widget_display_mode") == "cycle" else ["network_only"]
        for tname, edge, thickness, dpi, small in TASKBARS:
            w, h, nw = _size(cfg, edge, thickness, dpi, small)
            layout_mode = "horizontal" if edge == BOTTOM else "vertical"
            for phase in phases:
                out[f"{sname}|{tname}|{phase}"] = [w, h, _paint_hash(cfg, w, h, nw, layout_mode, phase)]
    return out


def _load():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_golden_covers_every_scenario():
    data = _load()
    assert len(data["entries"]) == sum(
        len(TASKBARS) * (3 if o.get("widget_display_mode") == "cycle" else 1) for o in SCENARIOS.values())


def test_existing_memory_layouts_are_unchanged(q_app):
    data = _load()
    if data["font"] != font_fingerprint():
        pytest.skip("this machine's offscreen fallback font differs from the golden's")
    got = run_all()
    diff = {k: (data["entries"][k], v) for k, v in got.items() if data["entries"].get(k) != v}
    assert not diff, f"{len(diff)} layouts changed (golden, now): {json.dumps(diff, indent=1)[:4000]}"


if __name__ == "__main__" and "--write" in sys.argv:
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    GOLDEN.write_text(json.dumps({"font": font_fingerprint(), "entries": run_all()}, indent=0,
                                 sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", GOLDEN)
