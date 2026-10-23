"""
The colour scheme (2.2 rc.2; #117 graph colours, #332 light/dark colours, #9 a Medium level).

Every foreground colour the widget paints has a dark-taskbar and a light-taskbar value
(``<stem>_dark`` / ``<stem>_light``); RenderConfig.from_dict picks the half for the current Windows
mode, and the theme never writes the config. Before, one colour per setting had to serve both
taskbars: a #151618 text chosen for a light taskbar was invisible on a dark one (#332), the white
down arrow was invisible on a light one, the graph colours were not configurable at all (#117), and
there were only three speed levels (#9).

Design: Docs/releases/v2.2/design-color-scheme.md (sections 2-8).
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import color_scheme
from netspeedtray.utils.config import ConfigManager
from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer

D = constants.config.defaults
STEMS = D.THEMED_COLOR_STEMS
SRC = Path(__file__).resolve().parents[2]          # .../src/netspeedtray
MBPS = 125_000.0

# Windows 11 taskbar backdrops (as in test_hardware_label_colors: solid taskbars and the Settings
# preview strip's #2B2B2B / #E8E8E8), plus the extremes.
DARK_TASKBARS = ("#202020", "#2B2B2B", "#1C1C1C", "#000000")
LIGHT_TASKBARS = ("#EEEEEE", "#E8E8E8", "#F3F3F3", "#FFFFFF")


def _contrast(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio."""
    def lum(h):
        c = QColor(h)
        def ch(v):
            v /= 255.0
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture
def shell(monkeypatch):
    """Set the Windows mode the scheme reads: shell("light") / shell("dark") / shell(None) (unreadable)."""
    def _set(mode):
        value = {"light": 1, "dark": 0, None: None}[mode]
        monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: value)
        color_scheme.refresh_shell_theme()
    return _set


def _cm(tmp_path=None):
    return ConfigManager(config_path=(tmp_path or Path(".")) / "NetSpeedTray_Config.json")


def _migrate(cfg):
    cm = _cm()
    return cm._validate_config(cm._migrate_config(dict(cfg)))


def _legacy(**over):
    """An rc.1 / 2.1.7 (schema 1.2) config: the factory values with the pre-1.3 colour keys."""
    cfg = {k: v for k, v in D.DEFAULT_CONFIG.items()
           if not k.endswith(("_dark", "_light")) and k != "medium_speed_threshold"}
    cfg.update(config_version="1.2", default_color="#FFFFFF", color_is_automatic=True,
               high_speed_color="#00FF00", low_speed_color="#FFA500",
               arrow_up_color="#00FF00", arrow_down_color="#FFFFFF")
    cfg.update(over)
    return cfg


# --------------------------------------------------------------------------- keys + defaults

def test_every_themed_colour_has_a_dark_and_a_light_key():
    assert set(STEMS) == {"default_color", "high_speed_color", "medium_speed_color", "low_speed_color",
                          "graph_upload_color", "graph_download_color", "arrow_up_color", "arrow_down_color"}
    for stem in STEMS:
        for suffix in ("dark", "light"):
            key = f"{stem}_{suffix}"
            assert key in D.DEFAULT_CONFIG and key in D.VALIDATION_SCHEMA, key
            assert re.fullmatch(D.VALIDATION_SCHEMA[key]["regex"], D.DEFAULT_CONFIG[key]), key
    assert D.DEFAULT_CONFIG["medium_speed_threshold"] == 0.0    # Off by default (#9)
    for legacy in D.LEGACY_COLOR_KEYS:
        assert legacy not in D.DEFAULT_CONFIG, f"{legacy} is inert since 1.3"


def test_dark_defaults_are_the_pre_scheme_colours():
    """Nobody's dark-taskbar widget changes on upgrade (the #168 rule)."""
    assert {s: D.THEMED_COLOR_DEFAULTS[s][0] for s in STEMS} == {
        "default_color": "#FFFFFF", "high_speed_color": "#00FF00", "medium_speed_color": "#FFBF00",
        "low_speed_color": "#FFA500", "graph_upload_color": constants.graph.UPLOAD_LINE_COLOR,
        "graph_download_color": constants.graph.DOWNLOAD_LINE_COLOR,
        "arrow_up_color": "#00FF00", "arrow_down_color": "#FFFFFF"}


@pytest.mark.parametrize("stem", STEMS)
def test_light_defaults_read_on_a_light_taskbar(stem):
    """Owner's rule: every default is readable on a light taskbar (the white-arrow-on-light bug). At
    least 3:1 (WCAG non-text / large text) on every light backdrop; the text colours 4.1:1 or more."""
    light = D.THEMED_COLOR_DEFAULTS[stem][1]
    floor = 3.0 if stem.startswith("graph_") else 4.0
    worst = min(_contrast(light, bg) for bg in LIGHT_TASKBARS)
    assert worst >= floor, f"{stem} {light}: {worst:.2f}:1 on a light taskbar"


def test_the_medium_amber_reads_on_both_taskbars():
    dark, light = D.THEMED_COLOR_DEFAULTS["medium_speed_color"]
    assert min(_contrast(dark, bg) for bg in DARK_TASKBARS) >= 3.0
    assert min(_contrast(light, bg) for bg in LIGHT_TASKBARS) >= 3.0
    # Amber: a hue between the Low orange and the High green, on both taskbars.
    for i in (0, 1):
        hues = [QColor(D.THEMED_COLOR_DEFAULTS[s][i]).hsvHue()
                for s in ("low_speed_color", "medium_speed_color", "high_speed_color")]
        assert hues[0] < hues[1] < hues[2], hues
        assert 40 <= hues[1] <= 50


def test_the_render_guard_knows_the_new_light_defaults():
    from netspeedtray.tests.unit.color_scheme_render_guard import TUNED_LIGHT, OLD_FACTORY
    for stem, tuned in TUNED_LIGHT.items():
        assert D.THEMED_COLOR_DEFAULTS[stem] == (OLD_FACTORY[stem], tuned), stem


# --------------------------------------------------------------------------- resolver

def test_resolve_picks_the_current_windows_mode(shell):
    cfg = dict(D.DEFAULT_CONFIG, default_color_dark="#111111", default_color_light="#222222")
    shell("dark")
    assert color_scheme.resolve(cfg)["default_color"] == "#111111"
    assert RenderConfig.from_dict(cfg).default_color == "#111111"
    shell("light")
    assert color_scheme.resolve(cfg)["default_color"] == "#222222"
    assert RenderConfig.from_dict(cfg).default_color == "#222222"


def test_an_unreadable_theme_is_dark(shell):
    shell(None)
    assert color_scheme.shell_is_light() is False
    assert color_scheme.resolve(D.DEFAULT_CONFIG)["default_color"] == "#FFFFFF"


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("bad", [None, "", "#FFF", "red", "#GGGGGG", 123])
def test_a_missing_or_invalid_value_falls_back_to_that_themes_default(light, bad):
    cfg = dict(D.DEFAULT_CONFIG)
    for stem in STEMS:
        cfg.pop(f"{stem}_{'light' if light else 'dark'}")
        if bad is not None:
            cfg[f"{stem}_{'light' if light else 'dark'}"] = bad
    # ...and NEVER the other theme's value, nor the inert pre-1.3 key of the same name.
    cfg.update({f"{stem}_{'dark' if light else 'light'}": "#ABCDEF" for stem in STEMS})
    cfg.update({stem: "#FEDCBA" for stem in STEMS})
    got = color_scheme.resolve(cfg, light=light)
    assert got == {s: D.THEMED_COLOR_DEFAULTS[s][1 if light else 0] for s in STEMS}


def test_the_light_override_ignores_the_cached_flag(shell):
    shell("dark")
    assert color_scheme.resolve(D.DEFAULT_CONFIG, light=True)["arrow_down_color"] == "#000000"
    assert color_scheme.resolve(D.DEFAULT_CONFIG, light=False)["arrow_down_color"] == "#FFFFFF"


def test_the_flag_is_read_lazily_once_and_refreshed_on_demand(monkeypatch):
    calls = []
    monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: calls.append(1) or 1)
    monkeypatch.setattr(color_scheme, "_shell_light", None)
    assert color_scheme.shell_is_light() is True
    for _ in range(5):
        RenderConfig.from_dict(D.DEFAULT_CONFIG)
    assert len(calls) == 1, "the registry was read on every RenderConfig"
    monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: 0)
    assert color_scheme.refresh_shell_theme() is False and color_scheme.shell_is_light() is False


def test_the_first_render_config_on_a_light_taskbar_is_light(monkeypatch):
    """No dark-text flash on a light bar at startup: nothing has read the flag yet."""
    monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: 1)
    monkeypatch.setattr(color_scheme, "_shell_light", None)
    rc = RenderConfig.from_dict(D.DEFAULT_CONFIG)
    assert (rc.default_color, rc.arrow_down_color) == ("#000000", "#000000")


def test_the_widget_reads_the_windows_mode_before_it_builds_a_renderer():
    """main.py refreshes the flag before _init_managers / the renderer (risk table: stale flag)."""
    src = (SRC / "views/widget/main.py").read_text(encoding="utf-8")
    refresh = src.index("color_scheme.refresh_shell_theme()")
    assert refresh < src.index("self._init_managers() # Initialize managers first")
    assert "apply_theme_aware_defaults" not in src


# --------------------------------------------------------------------------- migration 1.2 -> 1.3

@pytest.mark.parametrize("stored", ["#FFFFFF", "#000000"])
def test_an_automatic_text_colour_becomes_white_and_black(stored):
    """The flag decides, not the stored hex: an automatic user on a light taskbar has #000000 on disk
    (the old startup sync wrote it); copying it would make the dark-taskbar text black."""
    out = _migrate(_legacy(default_color=stored, color_is_automatic=True))
    assert (out["default_color_dark"], out["default_color_light"]) == ("#FFFFFF", "#000000")


@pytest.mark.parametrize("flag", [None, "yes", 1])
def test_a_missing_or_non_bool_automatic_flag_counts_as_automatic(flag):
    """Old validation reset a non-bool flag to its default True, so that is what the user had."""
    cfg = _legacy(default_color="#000000")
    if flag is None:
        cfg.pop("color_is_automatic")
    else:
        cfg["color_is_automatic"] = flag
    out = _migrate(cfg)
    assert (out["default_color_dark"], out["default_color_light"]) == ("#FFFFFF", "#000000")


def test_a_fixed_text_colour_is_kept_on_both_taskbars():
    """#332: a fixed #151618 looks exactly as before on both taskbars, until the user changes one."""
    out = _migrate(_legacy(default_color="#151618", color_is_automatic=False))
    assert (out["default_color_dark"], out["default_color_light"]) == ("#151618", "#151618")


def test_custom_band_and_arrow_colours_are_copied_to_both_taskbars():
    out = _migrate(_legacy(high_speed_color="#FF3366", low_speed_color="#33CCFF",
                           arrow_up_color="#FFAA00", arrow_down_color="#00AAFF"))
    for stem, value in (("high_speed_color", "#FF3366"), ("low_speed_color", "#33CCFF"),
                        ("arrow_up_color", "#FFAA00"), ("arrow_down_color", "#00AAFF")):
        assert (out[f"{stem}_dark"], out[f"{stem}_light"]) == (value, value), stem


def test_factory_colours_take_the_readable_light_defaults():
    """Nobody chose the factory value, so it becomes the theme defaults: the same on a dark taskbar,
    readable on a light one - the white down arrow was invisible there (design 3, 'optional
    refinement', applied to every factory colour per the owner's light-default rule)."""
    out = _migrate(_legacy())
    for stem in ("high_speed_color", "low_speed_color", "arrow_up_color", "arrow_down_color"):
        assert (out[f"{stem}_dark"], out[f"{stem}_light"]) == D.THEMED_COLOR_DEFAULTS[stem], stem
    assert out["arrow_down_color_light"] == "#000000"


def test_new_colours_and_medium_get_their_defaults():
    out = _migrate(_legacy())
    for stem in ("medium_speed_color", "graph_upload_color", "graph_download_color"):
        assert (out[f"{stem}_dark"], out[f"{stem}_light"]) == D.THEMED_COLOR_DEFAULTS[stem], stem
    assert out["medium_speed_threshold"] == 0.0


def test_legacy_keys_stay_in_the_file_untouched_for_a_rollback():
    """rc.1 / 2.1.7 read the pre-1.3 keys: they ride through as unknown keys, exactly as they were."""
    before = _legacy(default_color="#151618", color_is_automatic=False, high_speed_color="#FF3366")
    out = _migrate(before)
    for key in D.LEGACY_COLOR_KEYS:
        assert out[key] == before[key], key
    assert out["config_version"] == "1.3"


def test_the_migration_is_idempotent_and_keeps_newer_pairs():
    """Run twice = run once; and rc.2 -> rollback -> rc.2 keeps the pair this build wrote, even though
    the older build rewrote the legacy key (gated on the pair's presence, not on the version)."""
    once = _migrate(_legacy(default_color="#151618", color_is_automatic=False))
    assert _migrate(once) == once
    rolled_back = dict(once, config_version="1.3", default_color="#000000", color_is_automatic=True,
                       default_color_light="#123456")
    again = _migrate(rolled_back)
    assert (again["default_color_dark"], again["default_color_light"]) == ("#151618", "#123456")


def test_load_backs_up_the_1_2_file_once_and_persists_the_migration(tmp_path):
    path = tmp_path / "NetSpeedTray_Config.json"
    original = _legacy(default_color="#151618", color_is_automatic=False)
    path.write_text(json.dumps(original), encoding="utf-8")
    loaded = ConfigManager(config_path=path).load()
    bak = tmp_path / "NetSpeedTray_Config.json.bak.v1.2"
    assert bak.exists() and json.loads(bak.read_text(encoding="utf-8")) == original
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["config_version"] == "1.3" and on_disk["default_color_light"] == "#151618"
    assert on_disk["default_color"] == "#151618" and on_disk["color_is_automatic"] is False
    assert loaded["default_color_dark"] == "#151618"
    bak.write_text("pristine", encoding="utf-8")
    ConfigManager(config_path=path).load()             # second launch: nothing to migrate
    assert bak.read_text(encoding="utf-8") == "pristine"
    assert not list(tmp_path.glob("*.fields*"))


def test_no_warning_about_the_inert_legacy_keys(caplog):
    with caplog.at_level("WARNING", logger="NetSpeedTray.Config"):
        _migrate(_legacy())
    assert "Preserving unknown" not in caplog.text


# --------------------------------------------------------------------------- Medium validation (#9)

@pytest.mark.parametrize("medium,kept", [(0.0, 0.0), (5.0, 5.0), (1.5, 1.5), (9.99, 9.99),
                                         (1.0, 0.0), (0.5, 0.0), (10.0, 0.0), (50.0, 0.0)])
def test_a_misplaced_medium_is_turned_off_alone(medium, kept):
    out = _cm()._validate_config(dict(D.DEFAULT_CONFIG, high_speed_threshold=10.0, low_speed_threshold=1.0,
                                      medium_speed_threshold=medium))
    assert out["medium_speed_threshold"] == kept
    assert (out["high_speed_threshold"], out["low_speed_threshold"]) == (10.0, 1.0)


def test_medium_is_judged_against_the_settled_high_low_pair():
    """A swapped High/Low pair resets to 10/1 first; a Medium of 5 is then valid."""
    out = _cm()._validate_config(dict(D.DEFAULT_CONFIG, high_speed_threshold=1.0, low_speed_threshold=10.0,
                                      medium_speed_threshold=5.0))
    assert (out["high_speed_threshold"], out["medium_speed_threshold"], out["low_speed_threshold"]) == \
        (10.0, 5.0, 1.0)


# --------------------------------------------------------------------------- bands (#9)

THREE_BANDS = [(0.0, "default"), (0.5, "default"), (0.999, "default"), (1.0, "low"), (5.0, "low"),
               (9.99, "low"), (10.0, "high"), (500.0, "high")]


@pytest.mark.parametrize("mbps,band", THREE_BANDS)
def test_medium_off_bands_exactly_as_before(mbps, band):
    assert WidgetRenderer._speed_band(mbps * MBPS, 10.0, 1.0) == band
    assert WidgetRenderer._speed_band(mbps * MBPS, 10.0, 1.0, 0.0) == band


@pytest.mark.parametrize("mbps,band", [(0.5, "default"), (1.0, "low"), (4.99, "low"), (5.0, "medium"),
                                       (9.99, "medium"), (10.0, "high"), (500.0, "high")])
def test_four_bands_with_medium_on(mbps, band):
    assert WidgetRenderer._speed_band(mbps * MBPS, 10.0, 1.0, 5.0) == band


@pytest.mark.parametrize("medium", [1.0, 0.5, 10.0, 20.0, -3.0])
@pytest.mark.parametrize("mbps,band", THREE_BANDS)
def test_a_misplaced_medium_is_ignored_until_validation_fixes_it(medium, mbps, band):
    """The Settings spinboxes don't enforce the order, so a live preview can pass one."""
    assert WidgetRenderer._speed_band(mbps * MBPS, 10.0, 1.0, medium) == band


def _paint_speeds(cfg, up_mbps, down_mbps, w=300, h=48):
    r = WidgetRenderer(cfg, I18nStrings("en_US"))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    r.draw_network_speeds(p, up_mbps * MBPS, down_mbps * MBPS, w, h, r.config)
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
    return {tuple(px) for px in arr[arr[:, :, 3] >= 250][:, :3].tolist()}


def test_the_medium_band_paints_the_medium_colour(q_app):
    from netspeedtray.tests.color_helpers import themed
    cfg = themed(dict(D.DEFAULT_CONFIG, color_coding=True, medium_speed_threshold=5.0,
                      high_speed_color="#FF0000", medium_speed_color="#0000FF", low_speed_color="#00FF00",
                      default_color="#FFFFFF"))
    assert (0, 0, 255) in _paint_speeds(cfg, 7.0, 7.0)
    assert (0, 0, 255) not in _paint_speeds(dict(cfg, medium_speed_threshold=0.0), 7.0, 7.0)
    assert (0, 255, 0) in _paint_speeds(dict(cfg, medium_speed_threshold=0.0), 7.0, 7.0)


# --------------------------------------------------------------------------- graph colours (#117)

def _graph_pixels(cfg, w=160, h=40):
    from netspeedtray.tests.unit.color_scheme_render_guard import _metrics
    from netspeedtray.utils.widget_paint import font_from_config, render_widget
    r = WidgetRenderer(cfg, I18nStrings("en_US"))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    render_widget(p, QRect(0, 0, w, h), r, r.config, _metrics(), layout_mode="horizontal",
                  cycle_mode="network_only", font=font_from_config(cfg))
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
    return {tuple(px) for px in arr[arr[:, :, 3] >= 250][:, :3].tolist()}


@pytest.mark.parametrize("style,split", [("detailed", False), ("trend", False), ("trend", True),
                                         ("detailed", True)])
@pytest.mark.parametrize("light", [False, True])
def test_custom_graph_colours_reach_every_graph_style(q_app, monkeypatch, style, split, light):
    monkeypatch.setattr(color_scheme, "_shell_light", light)
    half = "light" if light else "dark"
    cfg = dict(D.DEFAULT_CONFIG, graph_enabled=True, graph_opacity=100, graph_style=style,
               graph_split_directions=split, widget_display_mode="network_only", hide_arrows=True,
               **{f"graph_upload_color_{half}": "#FF00FF", f"graph_download_color_{half}": "#00FFFF"})
    px = _graph_pixels(cfg)
    assert (255, 0, 255) in px, "upload colour not painted"
    assert (0, 255, 255) in px, "download colour not painted"
    for stock in (constants.graph.UPLOAD_LINE_COLOR, constants.graph.DOWNLOAD_LINE_COLOR):
        c = QColor(stock)
        assert (c.red(), c.green(), c.blue()) not in px, f"the old constant {stock} is still painted"


def test_a_graph_colour_change_repaints_the_cached_graph(q_app):
    """update_config clears the polyline cache and the colours are read at draw time."""
    from netspeedtray.tests.unit.color_scheme_render_guard import _metrics
    from netspeedtray.utils.widget_paint import font_from_config, render_widget
    from netspeedtray.tests.color_helpers import themed
    cfg = themed(dict(D.DEFAULT_CONFIG, graph_enabled=True, graph_opacity=100, hide_arrows=True,
                      graph_upload_color="#FF00FF", graph_download_color="#00FFFF"))
    r = WidgetRenderer(cfg, I18nStrings("en_US"))

    def colours():
        img = QImage(160, 40, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        render_widget(p, QRect(0, 0, 160, 40), r, r.config, _metrics(), font=font_from_config(cfg))
        p.end()
        return {img.pixelColor(x, y).name() for x in range(160) for y in range(40)
                if img.pixelColor(x, y).alpha() >= 250}
    assert "#ff00ff" in colours()                     # fills the polyline cache
    r.update_config(themed(dict(cfg, graph_upload_color="#FFFF00")))
    after = colours()
    assert "#ffff00" in after and "#ff00ff" not in after


# --------------------------------------------------------------------------- theme flip

def test_a_windows_mode_flip_repaints_without_touching_the_config(q_app, shell, monkeypatch):
    """Replaces the #405 'recolours from the Windows mode' test: the painted text follows the flip, and
    the config - in memory and on disk - is not touched (it used to be rewritten on every flip)."""
    from netspeedtray.views.widget.theme import WidgetThemeManager
    shell("dark")
    config = dict(D.DEFAULT_CONFIG)
    snapshot = dict(config)
    widget = MagicMock()
    widget.config = config
    widget.renderer = WidgetRenderer(config, I18nStrings("en_US"))
    assert widget.renderer.config.default_color == "#FFFFFF"
    monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: 1)   # the flip
    WidgetThemeManager(widget).on_theme_changed()
    assert widget.renderer.config.default_color == "#000000"
    assert widget.renderer._cached_pens["default"].color().name() == "#000000"
    assert config == snapshot, "the theme wrote into the config"
    widget.config_manager.save.assert_not_called()
    widget.update.assert_called()


def test_the_theme_module_no_longer_writes_colours():
    """The startup disk write (apply_theme_aware_defaults) and the in-memory rewrite are gone."""
    src = (SRC / "views/widget/theme.py").read_text(encoding="utf-8")
    for gone in ("winreg", "default_color", "color_is_automatic", "config[", "config.update",
                 "update_config(updates", "apply_theme_aware_defaults", "update_color_for_live_theme"):
        assert gone not in src, gone


# --------------------------------------------------------------------------- no legacy readers

_LEGACY_READ = re.compile(
    r"""(?:\.get\(\s*|\[\s*)['"](%s)['"]""" % "|".join(D.LEGACY_COLOR_KEYS))


def test_no_module_reads_a_legacy_colour_key():
    """A missed reader would paint the stale pre-1.3 value the file keeps for a rollback. Only the 1.3
    migration may read them; ``colors[...]`` is the resolved {stem: hex} dict, not the config."""
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith("tests/") or rel == "utils/config.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in _LEGACY_READ.finditer(line):
                if not line[:m.start()].rstrip().endswith("colors"):
                    offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_legacy_values_never_reach_the_paint(q_app, monkeypatch):
    """Behavioural twin of the grep: legacy keys holding a sentinel never show up in the RenderConfig
    or in the painted pixels, on either taskbar."""
    sentinel = "#FF00FF"
    cfg = dict(D.DEFAULT_CONFIG, color_coding=True, use_custom_arrow_colors=True, graph_enabled=True,
               graph_opacity=100, medium_speed_threshold=5.0,
               **{k: sentinel for k in D.LEGACY_COLOR_KEYS if k != "color_is_automatic"},
               color_is_automatic=False)
    for light in (False, True):
        monkeypatch.setattr(color_scheme, "_shell_light", light)
        rc = RenderConfig.from_dict(cfg)
        fields = {f: getattr(rc, f) for f in STEMS}
        assert sentinel not in fields.values(), fields
        for up, down in ((0.5, 0.5), (3.0, 7.0), (50.0, 50.0)):
            assert (255, 0, 255) not in _paint_speeds(cfg, up, down)
