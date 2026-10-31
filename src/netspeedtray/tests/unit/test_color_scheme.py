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


def test_dark_defaults_are_the_pre_scheme_colours_but_the_legibility_ones():
    """The dark-taskbar defaults are the pre-2.2 colours (the #168 rule), except where the legibility
    package changed them on purpose: the High band (#00FF00 -> mint, migrated only from the factory
    value), the unshipped Medium, and the muted upload line (7d)."""
    assert {s: D.THEMED_COLOR_DEFAULTS[s][0] for s in STEMS} == {
        "default_color": "#FFFFFF", "high_speed_color": "#8CF2C3", "medium_speed_color": "#FFFF80",
        "low_speed_color": "#FFA500", "graph_upload_color": "#6F86A6",
        "graph_download_color": constants.graph.DOWNLOAD_LINE_COLOR,
        "arrow_up_color": "#00FF00", "arrow_down_color": "#FFFFFF"}
    # The Monitor window keeps its own (bright) upload colour.
    assert constants.graph.UPLOAD_LINE_COLOR == "#4287F5"


def test_light_defaults_are_the_legibility_ones():
    assert {s: D.THEMED_COLOR_DEFAULTS[s][1] for s in STEMS} == {
        "default_color": "#1B1B1B", "high_speed_color": "#145252", "medium_speed_color": "#764B0A",
        "low_speed_color": "#57190F", "graph_upload_color": "#6A7FA3", "graph_download_color": "#1A7F4E",
        "arrow_up_color": "#007A00", "arrow_down_color": "#1B1B1B"}


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("stem", STEMS)
def test_defaults_read_on_their_taskbar(stem, light):
    """Owner's rule: every default is readable on its taskbar (the white-arrow-on-light bug). The widget's
    text is ~9 pt, so the text colours need the 4.5:1 body-text bar (WCAG 1.4.3), not the 3:1 large-text
    one; the graph lines are non-text graphics, 3:1 (WCAG 1.4.11). Every backdrop of that theme."""
    colour = D.THEMED_COLOR_DEFAULTS[stem][1 if light else 0]
    floor = 3.0 if stem.startswith("graph_") else 4.5
    worst = min(_contrast(colour, bg) for bg in (LIGHT_TASKBARS if light else DARK_TASKBARS))
    assert worst >= floor, f"{stem} {colour}: {worst:.2f}:1 on a {'light' if light else 'dark'} taskbar"


def _ciede2000(h1: str, h2: str) -> float:
    """CIEDE2000 colour difference (sRGB, D65)."""
    import math

    def lab(h):
        c = QColor(h)
        def lin(v):
            v /= 255.0
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = lin(c.red()), lin(c.green()), lin(c.blue())
        x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
        y = 0.2126 * r + 0.7152 * g + 0.0722 * b
        z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
        f = lambda t: t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
        return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))

    (L1, a1, b1), (L2, a2, b2) = lab(h1), lab(h2)
    Cb = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p, h2p = math.degrees(math.atan2(b1, a1p)) % 360, math.degrees(math.atan2(b2, a2p)) % 360
    dL, dC, dh = L2 - L1, C2p - C1p, h2p - h1p
    if C1p * C2p == 0:
        dh = 0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    dH = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lb, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    hb = (h1p + h2p) / 2 if abs(h1p - h2p) <= 180 else (h1p + h2p + 360) / 2
    if C1p * C2p == 0:
        hb = h1p + h2p
    T = (1 - 0.17 * math.cos(math.radians(hb - 30)) + 0.24 * math.cos(math.radians(2 * hb))
         + 0.32 * math.cos(math.radians(3 * hb + 6)) - 0.20 * math.cos(math.radians(4 * hb - 63)))
    dTh = 30 * math.exp(-(((hb - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lb - 50) ** 2 / math.sqrt(20 + (Lb - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dTh)) * Rc
    return math.sqrt((dL / Sl) ** 2 + (dC / Sc) ** 2 + (dH / Sh) ** 2 + Rt * (dC / Sc) * (dH / Sh))


def test_ciede2000_matches_the_reference_pairs():
    """The implementation agrees with the PR #441 review's independent script on two sRGB pairs."""
    assert _ciede2000("#FFBF00", "#FFA500") == pytest.approx(9.3, abs=0.05)
    assert _ciede2000("#FFE14D", "#00FF00") == pytest.approx(29.0, abs=0.05)


# Machado, Oliveira & Fernandes 2009, severity 1.0, applied to linear RGB - the simulation the UI/UX
# review used to pick the legibility package's bands.
_CVD = {
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
                   (-0.003882, -0.048116, 1.051998)),
}


def _simulate(hex_code: str, kind: str) -> str:
    """``hex_code`` as a viewer with ``kind`` colour vision deficiency sees it."""
    c = QColor(hex_code)
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    def enc(v):
        v = max(0.0, min(1.0, v))
        return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    rgb = (lin(c.red()), lin(c.green()), lin(c.blue()))
    out = [enc(sum(m * x for m, x in zip(row, rgb))) for row in _CVD[kind]]
    return "#%02X%02X%02X" % tuple(round(v * 255) for v in out)


_BANDS = ("low_speed_color", "medium_speed_color", "high_speed_color")


@pytest.mark.parametrize("vision", ["normal", "deuteranopia", "protanopia"])
@pytest.mark.parametrize("light", [False, True])
def test_band_palette_stays_apart_for_every_viewer(light, vision):
    """Legibility package "L": the three speed bands are pairwise CIEDE2000 >= 14 apart with normal
    vision AND as deuteranopes / protanopes see them (the old light trio became one olive under
    deuteranopia; dark Medium and High merged for red-green deficiency)."""
    import itertools
    i = 1 if light else 0
    bands = [D.THEMED_COLOR_DEFAULTS[s][i] for s in _BANDS]
    if vision != "normal":
        bands = [_simulate(b, vision) for b in bands]
    for a, b in itertools.combinations(bands, 2):
        assert _ciede2000(a, b) >= 14, (vision, a, b, round(_ciede2000(a, b), 1))


@pytest.mark.parametrize("light", [False, True])
def test_band_palette_reads_on_the_gate_backdrops(light):
    """Every band >= 4.5:1 (body text) on #202020 / #2B2B2B (dark) or #EEEEEE / #E8E8E8 (light)."""
    i = 1 if light else 0
    for stem in _BANDS:
        colour = D.THEMED_COLOR_DEFAULTS[stem][i]
        for bg in (("#EEEEEE", "#E8E8E8") if light else ("#202020", "#2B2B2B")):
            assert _contrast(colour, bg) >= 4.5, (stem, colour, bg, round(_contrast(colour, bg), 2))


def test_band_palette_is_the_decided_one():
    assert [D.THEMED_COLOR_DEFAULTS[s] for s in _BANDS] == [
        ("#FFA500", "#57190F"), ("#FFFF80", "#764B0A"), ("#8CF2C3", "#145252")]


def test_the_cvd_simulation_matches_the_review_script():
    """Spot values from the UI/UX review's palette script (pal.py): it also passed the old light trio,
    which it shows collapsing under deuteranopia - the reason for the change."""
    assert _ciede2000(_simulate("#FFFF80", "deuteranopia"), _simulate("#8CF2C3", "deuteranopia")) \
        == pytest.approx(19.8, abs=0.5)   # pal.py rounds the simulated sRGB differently
    assert _ciede2000(_simulate("#57190F", "deuteranopia"), _simulate("#764B0A", "deuteranopia")) \
        == pytest.approx(14.1, abs=0.5)
    old_light = ("#A84600", "#7A6500", "#007A00")
    assert _ciede2000(_simulate(old_light[1], "deuteranopia"), _simulate(old_light[2], "deuteranopia")) < 14


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
    assert color_scheme.resolve(D.DEFAULT_CONFIG, light=True)["arrow_down_color"] == "#1B1B1B"
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
    assert (rc.default_color, rc.arrow_down_color) == ("#1B1B1B", "#1B1B1B")


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
    assert (out["default_color_dark"], out["default_color_light"]) == ("#FFFFFF", "#1B1B1B")


@pytest.mark.parametrize("flag", [None, "yes", 1])
def test_a_missing_or_non_bool_automatic_flag_counts_as_automatic(flag):
    """Old validation reset a non-bool flag to its default True, so that is what the user had."""
    cfg = _legacy(default_color="#000000")
    if flag is None:
        cfg.pop("color_is_automatic")
    else:
        cfg["color_is_automatic"] = flag
    out = _migrate(cfg)
    assert (out["default_color_dark"], out["default_color_light"]) == ("#FFFFFF", "#1B1B1B")


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
    assert out["arrow_down_color_light"] == "#1B1B1B"


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
    assert out["config_version"] == D.CONFIG_SCHEMA_VERSION == "1.4"


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
    assert on_disk["config_version"] == "1.4" and on_disk["default_color_light"] == "#151618"
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
    assert widget.renderer.config.default_color == "#1B1B1B"
    assert widget.renderer._cached_pens["default"].color().name() == "#1b1b1b"
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


# --- the gap between widget init and the theme detector's baseline (review item 7) -------------------

@pytest.mark.parametrize("cached_light,now", [(False, 1), (True, 0), (False, 0), (True, 1), (False, None)])
def test_start_catches_up_a_flip_made_during_startup(q_app, monkeypatch, cached_light, now):
    """The widget resolved its colours at init; SystemEventHandler.start() takes its baseline later. A
    flip in between was seen by neither, so start() emits theme_changed when the two disagree."""
    from unittest.mock import patch
    from netspeedtray.core.system_events import SystemEventHandler
    monkeypatch.setattr(color_scheme, "_shell_light", cached_light)
    with patch("netspeedtray.core.system_events.WinEventHook"):
        h = SystemEventHandler()
    seen = MagicMock()
    h.theme_changed.connect(seen)
    try:
        with patch("netspeedtray.core.system_events.read_system_uses_light_theme", return_value=now), \
                patch("netspeedtray.core.system_events.get_taskbar_info"):
            h.start()
        assert seen.call_count == (1 if now is not None and (now == 1) != cached_light else 0)
    finally:
        h.stop()


# --- the Detailed fill over an opaque light background (review item 4) ------------------------------

@pytest.mark.parametrize("bg,opacity,mode", [("#000000", 0, "SourceOver"), ("#EEEEEE", 0, "SourceOver"),
                                             ("#202020", 100, "SourceOver"), ("#EEEEEE", 100, "SourceOver"),
                                             ("#FFFFFF", 40, "SourceOver")])
def test_detailed_fill_blend_follows_the_backdrop(q_app, bg, opacity, mode):
    """Legibility package "L" (2a): the Detailed fill blends normally on every backdrop. The additive
    (Plus) glow it had brightened the area behind white text and vanished over a light background."""
    from PyQt6.QtGui import QPainter as QP
    from netspeedtray.tests.unit.color_scheme_render_guard import _metrics
    r = WidgetRenderer(dict(D.DEFAULT_CONFIG, graph_enabled=True, graph_style="detailed",
                            background_color=bg, background_opacity=opacity), I18nStrings("en_US"))
    painter = MagicMock()
    r.draw_mini_graph(painter, 120, 40, r.config, _metrics().net_history)
    modes = [c.args[0] for c in painter.setCompositionMode.call_args_list]
    assert modes[0] == getattr(QP.CompositionMode, f"CompositionMode_{mode}")


# --- an opaque widget background picks its own half (review round 2; pre-existing on main) ---------

@pytest.mark.parametrize("bg,opacity,shell_light,light", [
    ("#EEEEEE", 100, False, True),     # opaque light background on a dark taskbar -> dark text
    ("#202020", 100, True, False),     # opaque dark background on a light taskbar -> light text
    ("#EEEEEE", 0, False, False),      # 0 % opacity: the taskbar's mode, exactly as before
    ("#000000", 0, True, True),
    ("#EEEEEE", 30, False, False),     # mostly the dark taskbar showing through
    ("#EEEEEE", 50, False, True),
    ("#808080", 100, False, True),     # mid grey: black reads better (5.3 vs 3.9:1)
    ("nonsense", 100, True, True),     # unusable colour: the taskbar's mode
])
def test_the_backdrop_decides_the_half(bg, opacity, shell_light, light):
    cfg = dict(D.DEFAULT_CONFIG, background_color=bg, background_opacity=opacity)
    assert color_scheme.backdrop_is_light(cfg, shell_light) is light


def test_the_split_is_where_black_and_white_contrast_equally():
    split = constants.renderer.BACKDROP_LUMINANCE_SPLIT
    assert (1.05 / (split + 0.05)) == pytest.approx((split + 0.05) / 0.05, rel=1e-3)
    # The boundary itself: a grey just above it is light, just below it dark (opaque, either taskbar).
    for shell_light in (False, True):
        assert color_scheme.backdrop_is_light(dict(background_color="#767676", background_opacity=100),
                                              shell_light) is True     # luminance 0.181
        assert color_scheme.backdrop_is_light(dict(background_color="#757575", background_opacity=100),
                                              shell_light) is False    # luminance 0.178


@pytest.mark.parametrize("bg,shell,text", [("#EEEEEE", "dark", "#1B1B1B"), ("#202020", "light", "#FFFFFF")])
def test_text_on_an_opaque_background_reads(shell, bg, text, monkeypatch):
    monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: 1 if shell == "light" else 0)
    color_scheme.refresh_shell_theme()
    rc = RenderConfig.from_dict(dict(D.DEFAULT_CONFIG, background_color=bg, background_opacity=100))
    assert rc.default_color == text
    assert _contrast(rc.default_color, bg) >= 4.5


def test_a_transparent_background_still_follows_the_taskbar(monkeypatch):
    for value, text in ((0, "#FFFFFF"), (1, "#1B1B1B")):
        monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda v=value: v)
        color_scheme.refresh_shell_theme()
        assert RenderConfig.from_dict(dict(D.DEFAULT_CONFIG, background_color="#EEEEEE",
                                           background_opacity=0)).default_color == text
