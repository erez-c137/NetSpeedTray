"""
Byte-identical render guard for the 2.2 rc.2 colour scheme (design-color-scheme.md section 8.5).

Renders the widget - horizontal 48 px, small taskbar 32 px, a 48 px side bar; graph off / Detailed /
Trend / split; color coding off / on - on a dark and a light taskbar, for three configs: the factory
defaults, an rc.1 "automatic" config and an rc.1 config with custom colours (the #332 case, plus custom
High / Low / arrow colours). On the new code the rc.1 configs go through the real 1.2 -> 1.3 migration.

The SAME scenarios run on the code before the scheme (main @ 02a86ff0), which wrote the golden file
``color_scheme_render_golden.json`` next to this module:

    python <this file> --src <old tree>/src --write <json> [--png-dir DIR]

For each scenario the golden holds two hashes of the painted RGBA bytes:
- ``literal``: the old code as it was (on a light taskbar the old theme manager had set an automatic
  text colour to black - that is reproduced).
- ``expected``: the old code given the new LIGHT-taskbar defaults where the old value was the factory
  one (High / Low / both arrows / the two graph colours; TUNED_LIGHT below). These are the only colours
  the scheme changes on purpose; everything else must paint exactly as before.

test_color_scheme_render_guard.py checks the new code against ``expected`` for every scenario, and that
``literal == expected`` for every dark-taskbar scenario and every custom-colour one - so the dark
widget and every user-chosen colour are literally unchanged. Font rendering differs between machines,
so the golden also carries a calibration hash; the test skips (it does not fail) when it differs.

Not a test module itself (no ``test_`` prefix): it must import on the old tree too.
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter

# The light-taskbar defaults the scheme introduces where the old (single) value was the factory one.
# Hard-coded because the old tree has no such constants; the test asserts they match the new ones.
TUNED_LIGHT = {
    "high_speed_color": "#007A00",
    "low_speed_color": "#B04A00",
    "arrow_up_color": "#007A00",
    "arrow_down_color": "#000000",
    "graph_upload_color": "#2F6FDB",
    "graph_download_color": "#1A7F4E",
}
OLD_FACTORY = {"high_speed_color": "#00FF00", "low_speed_color": "#FFA500", "arrow_up_color": "#00FF00",
               "arrow_down_color": "#FFFFFF", "graph_upload_color": "#4287F5",
               "graph_download_color": "#42B883"}

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


def has_scheme() -> bool:
    return importlib.util.find_spec("netspeedtray.utils.color_scheme") is not None


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
    """A 1.2-schema (rc.1) config with factory values."""
    from netspeedtray import constants
    d = constants.config.defaults
    cfg = dict(d.DEFAULT_CONFIG)
    if has_scheme():   # build the 1.2 shape from the 1.3 defaults: drop the pairs, add the old keys
        for stem in d.THEMED_COLOR_STEMS:
            cfg.pop(f"{stem}_dark", None)
            cfg.pop(f"{stem}_light", None)
        cfg.pop("medium_speed_threshold", None)
        cfg.update({"default_color": "#FFFFFF", "color_is_automatic": True, "high_speed_color": "#00FF00",
                    "low_speed_color": "#FFA500", "arrow_up_color": "#00FF00", "arrow_down_color": "#FFFFFF"})
    cfg["config_version"] = "1.2"
    return cfg


def build_config(kind: str, theme: str, overrides: dict, substitute: bool):
    """(config dict for the renderer, stems substituted). On the old tree it is the config the old app
    paints from on that taskbar; on the new tree, the factory 1.3 defaults or the migrated rc.1 file."""
    from netspeedtray import constants
    light = theme == "light"
    if has_scheme():
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
        return cfg, []
    # --- the old tree ---
    cfg = _legacy_base()
    if kind == "rc1_custom":
        cfg.update(RC1_CUSTOM)
    if kind == "rc1_auto":
        cfg["use_custom_arrow_colors"] = True
    if light and cfg.get("color_is_automatic", True):
        cfg["default_color"] = "#000000"   # the old theme manager's automatic text colour
    cfg.update(overrides)
    subst = []
    if light and substitute:
        for stem, tuned in TUNED_LIGHT.items():
            if stem.startswith("graph_"):
                subst.append(stem)   # applied to the constants in render()
            elif str(cfg.get(stem, "")).upper() == OLD_FACTORY[stem]:
                cfg[stem] = tuned
                subst.append(stem)
    return cfg, subst


def render(name: str, substitute: bool = True, png_dir=None) -> str:
    from netspeedtray import constants
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.utils.widget_paint import font_from_config, render_widget
    from netspeedtray.utils.widget_renderer import WidgetRenderer
    lay, graph, coding, kind, theme = name.split("/")
    overrides = dict(GRAPHS[graph], **CODING[coding], widget_display_mode="network_only",
                     background_opacity=0, high_speed_threshold=10.0, low_speed_threshold=1.0)
    cfg, subst = build_config(kind, theme, overrides, substitute)
    saved = {}
    if has_scheme():
        from netspeedtray.utils import color_scheme
        saved["light"] = color_scheme._shell_light
        color_scheme._shell_light = theme == "light"
    elif "graph_upload_color" in subst:
        saved["graph"] = (constants.graph.UPLOAD_LINE_COLOR, constants.graph.DOWNLOAD_LINE_COLOR)
        constants.graph.UPLOAD_LINE_COLOR = TUNED_LIGHT["graph_upload_color"]
        constants.graph.DOWNLOAD_LINE_COLOR = TUNED_LIGHT["graph_download_color"]
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
        if "light" in saved:
            color_scheme._shell_light = saved["light"]
        if "graph" in saved:
            constants.graph.UPLOAD_LINE_COLOR, constants.graph.DOWNLOAD_LINE_COLOR = saved["graph"]
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    if png_dir:
        out = Path(png_dir) / (name.replace("/", "__") + ".png")
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out))
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return hashlib.sha256(f"{w}x{h}".encode() + bytes(ptr)).hexdigest()


def calibration() -> str:
    """A plain text render that depends only on the platform's fonts - the guard's environment check."""
    img = QImage(200, 40, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(QFont("Segoe UI", 9))
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRect(0, 0, 200, 40), Qt.AlignmentFlag.AlignCenter, "↑ 888.8 Mbps ↓ 12.4")
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return hashlib.sha256(bytes(ptr)).hexdigest()


def main(argv=None) -> int:
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", help="write the golden JSON here (run on the OLD tree)")
    ap.add_argument("--png-dir")
    ap.add_argument("--src", help="the tree's src directory to import netspeedtray from")
    args = ap.parse_args(argv)
    if args.src:
        sys.path.insert(0, str(Path(args.src).resolve()))
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    out = {"calibration": calibration(), "has_scheme": has_scheme(), "scenarios": {}}
    for name in scenario_names():
        out["scenarios"][name] = {"expected": render(name, True, args.png_dir),
                                  "literal": render(name, False)}
    if args.write:
        Path(args.write).write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print(f"{len(out['scenarios'])} scenarios; calibration {out['calibration'][:12]}")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
