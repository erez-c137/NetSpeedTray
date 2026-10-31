"""
Render guard for the widget's paint (design-color-scheme.md section 8.5; re-based for the legibility
package "L", 2.2 rc.2).

Renders the widget - horizontal 48 px, small taskbar 32 px, a 48 px side bar; graph off / Detailed /
Trend / split; color coding off / on - on a dark and a light taskbar, for three configs: the factory
defaults, an rc.1 "automatic" config and an rc.1 config with custom colours (the #332 case, plus custom
High / Low / arrow colours). The rc.1 configs go through the real 1.2 -> 1.4 migration, so a change in
what an upgraded user sees shows up here too.

History: for PR #441 the golden came from the tree BEFORE the colour scheme and proved the scheme
painted byte-identically except where it meant to. The legibility package changes every frame on
purpose (the erasing halo, the bands, the font, the normal blend, the graph's scale floor), so the
golden ``color_scheme_render_golden.json`` was re-written from the L branch after its visual review
(the 170-scenario contrast renders and their contact sheets): from here on it pins THAT paint, and any
later change to it has to be deliberate (re-write it, and say why, in the PR).

    python <this file> --write <json> [--png-dir DIR]

Each scenario holds the hash of its painted RGBA bytes; every scenario with a graph also holds the frame
as a PNG, compared per pixel within TOLERANCE levels when the hash differs - the gradient fills and the
halo's smooth-scaled stamps round differently on some raster paths (on GitHub's runner the old additive
fills already did). Font rendering differs between machines, so the golden also carries a calibration
hash; the test skips (it does not fail) when it differs.

Not a test module itself (no ``test_`` prefix).
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

BASELINE = "legibility-L"

LAYOUTS = ("bar48", "bar32", "side48")
GRAPHS = {
    "graph_off": dict(graph_enabled=False),
    "detailed": dict(graph_enabled=True, graph_style="detailed"),
    "trend": dict(graph_enabled=True, graph_style="trend"),
    "split": dict(graph_enabled=True, graph_style="trend", graph_split_directions=True),
}
CODING = {"coding_off": dict(color_coding=False), "coding_on": dict(color_coding=True)}
CONFIGS = ("factory", "rc1_auto", "rc1_custom")
THEMES = ("dark", "light")
RC1_CUSTOM = {"color_is_automatic": False, "default_color": "#151618", "high_speed_color": "#FF3366",
              "low_speed_color": "#33CCFF", "use_custom_arrow_colors": True,
              "arrow_up_color": "#FFAA00", "arrow_down_color": "#00AAFF"}

# Per-channel tolerance (0-255) for the frames with a graph - far below any real change (a 0.25 px
# thicker stroke or a different colour moves pixels by tens of levels).
TOLERANCE = 4


def scenario_names():
    return [f"{lay}/{g}/{c}/{cfg}/{t}" for lay in LAYOUTS for g in GRAPHS for c in CODING
            for cfg in CONFIGS for t in THEMES]


def _metrics():
    from netspeedtray.core.widget_state import AggregatedSpeedData
    from netspeedtray.utils.widget_paint import WidgetMetrics
    mbps = 125_000.0
    t0 = datetime(2026, 1, 1)
    hist = [AggregatedSpeedData(upload=(3.0 + 1.5 * ((i % 20) / 10 - 1)) * mbps,
                                download=(48.0 if i >= 30 else 1.0 + (i % 7)) * mbps,
                                timestamp=t0 + timedelta(seconds=i)) for i in range(90)]
    # Upload 4.2 Mbps sits in the Low band, download 48.7 in High (thresholds 1 / 10).
    return WidgetMetrics(upload_mbps=4.2, download_mbps=48.7, net_history=hist)


def _legacy_base():
    """A 1.2-schema (rc.1 / 2.1.7) file with factory values, as that build wrote it."""
    from netspeedtray import constants
    d = constants.config.defaults
    cfg = dict(d.DEFAULT_CONFIG)
    for stem in d.THEMED_COLOR_STEMS:
        cfg.pop(f"{stem}_dark", None)
        cfg.pop(f"{stem}_light", None)
    cfg.pop("medium_speed_threshold", None)
    cfg.update({"default_color": "#FFFFFF", "color_is_automatic": True, "high_speed_color": "#00FF00",
                "low_speed_color": "#FFA500", "arrow_up_color": "#00FF00", "arrow_down_color": "#FFFFFF",
                "font_family": "Segoe UI", "arrow_font_family": "Segoe UI", "use_separate_arrow_font": True,
                "arrow_font_size": 10, "arrow_font_weight": 600})
    cfg["config_version"] = "1.2"
    return cfg


def build_config(kind: str, theme: str, overrides: dict):
    """The config the renderer paints from: the factory defaults, or the migrated rc.1 file."""
    from netspeedtray import constants
    light = theme == "light"
    if kind == "factory":
        cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    else:
        from netspeedtray.utils.config import ConfigManager
        legacy = _legacy_base()
        if kind == "rc1_custom":
            legacy.update(RC1_CUSTOM)
        elif light:
            legacy["default_color"] = "#000000"   # what the old startup sync persisted on a light bar
        if kind == "rc1_auto":
            legacy["use_custom_arrow_colors"] = True
        cm = ConfigManager(config_path=Path("unused-config.json"))
        cfg = cm._validate_config(cm._migrate_config(legacy))
    cfg.update(overrides)
    return cfg


def render_image(name: str) -> QImage:
    """The scenario's frame (RGBA8888, transparent background - what the widget's layered window holds)."""
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.utils import color_scheme
    from netspeedtray.utils.taskbar_appearance import PLAIN
    from netspeedtray.utils.widget_paint import font_from_config, render_widget
    from netspeedtray.utils.widget_renderer import WidgetRenderer
    lay, graph, coding, kind, theme = name.split("/")
    overrides = dict(GRAPHS[graph], **CODING[coding], widget_display_mode="network_only",
                     background_opacity=0, high_speed_threshold=10.0, low_speed_threshold=1.0)
    cfg = build_config(kind, theme, overrides)
    saved = (color_scheme._shell_light, color_scheme._appearance)
    color_scheme._shell_light = theme == "light"
    color_scheme._appearance = PLAIN          # no High Contrast / accent bar, whatever this machine has
    try:
        if lay == "side48":
            from netspeedtray.tests.unit import test_side_taskbar_compact as side
            w, h, _fit = side._size(cfg, side._taskbar(side.LEFT, 48))
            mode = "vertical"
        else:
            w, h, mode = 260, (48 if lay == "bar48" else 32), "horizontal"
        renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        try:
            render_widget(p, QRect(0, 0, w, h), renderer, renderer.config, _metrics(), layout_mode=mode,
                          cycle_mode="network_only", font=font_from_config(cfg))
        finally:
            p.end()
    finally:
        color_scheme._shell_light, color_scheme._appearance = saved
    return img.convertToFormat(QImage.Format.Format_RGBA8888)


def _hash(img: QImage) -> str:
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return hashlib.sha256(f"{img.width()}x{img.height()}".encode() + bytes(ptr)).hexdigest()


def render(name: str, png_dir=None) -> str:
    img = render_image(name)
    if png_dir:
        out = Path(png_dir) / (name.replace("/", "__") + ".png")
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out))
    return _hash(img)


def uses_tolerance(name: str) -> bool:
    return name.split("/")[1] != "graph_off"


def png_b64(img: QImage) -> str:
    import base64
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return base64.b64encode(bytes(data)).decode("ascii")


def max_pixel_difference(img: QImage, expected_b64: str) -> int:
    """Largest per-channel difference (0-255) between ``img`` and the golden PNG; 255 if the sizes differ."""
    import base64
    import numpy as np
    ref = QImage.fromData(base64.b64decode(expected_b64), "PNG").convertToFormat(QImage.Format.Format_RGBA8888)
    if (ref.width(), ref.height()) != (img.width(), img.height()):
        return 255

    def arr(i):
        ptr = i.constBits()
        ptr.setsize(i.sizeInBytes())
        return np.frombuffer(bytes(ptr), dtype=np.uint8).astype(int)
    return int(np.abs(arr(img) - arr(ref)).max())


def calibration() -> str:
    """A plain text render that depends only on the platform's fonts - the guard's environment check.
    In the widget's default face (Segoe UI Variable at its Text size, or Segoe UI) and in Segoe UI."""
    from netspeedtray.utils.widget_renderer import make_widget_font
    img = QImage(200, 40, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("#FFFFFF"))
    for i, font in enumerate((make_widget_font("Segoe UI Variable", 9, 400),
                              make_widget_font("Segoe UI", 9, 400))):
        p.setFont(font)
        p.drawText(QRect(0, i * 20, 200, 20), Qt.AlignmentFlag.AlignCenter, "↑ 888.8 Mbps ↓ 12.4")
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return hashlib.sha256(bytes(ptr)).hexdigest()


def main(argv=None) -> int:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", help="write the golden JSON here")
    ap.add_argument("--png-dir")
    args = ap.parse_args(argv)
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    out = {"calibration": calibration(), "baseline": BASELINE, "scenarios": {}}
    for name in scenario_names():
        entry = {"expected": render(name, args.png_dir)}
        if uses_tolerance(name):
            entry["expected_png"] = png_b64(render_image(name))
        out["scenarios"][name] = entry
    if args.write:
        Path(args.write).write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print(f"{len(out['scenarios'])} scenarios; calibration {out['calibration'][:12]}")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
