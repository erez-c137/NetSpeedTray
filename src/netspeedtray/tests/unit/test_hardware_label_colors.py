"""CPU/GPU label colour on the taskbar widget: Brand / Match text / Custom (2.2).

The pre-2.2 "Indicator Style" mixed shape and colour in one three-way setting, painted coloured
labels in a fixed blue/red that meant nothing, and painted "monochrome" labels in hard-coded white -
invisible on a light taskbar. Shape (icons/text) and colour (brand/match_text/custom) are now
separate settings; the config migration splits the old value.
"""
import json

import numpy as np
import pytest
from PyQt6.QtGui import QColor, QImage, QPainter

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import hardware_vendors as hv
from netspeedtray.utils.config import ConfigManager
from netspeedtray.utils.widget_renderer import WidgetRenderer
from netspeedtray.tests.color_helpers import themed

D = constants.config.defaults

# Windows 11 taskbar backdrops the label colours must read on (solid dark / light taskbar, and the
# Settings preview strip's own backdrops #2B2B2B / #E8E8E8).
DARK_TASKBARS = ("#202020", "#2B2B2B", "#1C1C1C")
LIGHT_TASKBARS = ("#EEEEEE", "#E8E8E8", "#F3F3F3")


def _contrast(a: str, b: str) -> float:
    def lum(h):
        c = QColor(h)
        def ch(v):
            v /= 255.0
            return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _hue(h: str) -> int:
    return QColor(h).hsvHue()


@pytest.fixture
def vendors(monkeypatch):
    """Pin the detected vendors (the real ones are cached registry reads)."""
    def _set(cpu, gpu):
        monkeypatch.setattr(hv, "cpu_vendor", lambda: cpu)
        monkeypatch.setattr(hv, "gpu_vendor", lambda: gpu)
    return _set


# --------------------------------------------------------------------------- vendor -> colour

@pytest.mark.parametrize("light", [False, True])
def test_gpu_wears_its_own_brand_hue(vendors, light):
    """The owner's ask: AMD red, NVIDIA green, Intel blue (on a box where the CPU is another maker)."""
    vendors("intel", "nvidia")
    assert 75 <= _hue(hv.widget_label_color("gpu", light)) <= 150, "NVIDIA is not green"
    vendors("intel", "amd")
    assert _hue(hv.widget_label_color("gpu", light)) in range(0, 12), "AMD GPU is not red"
    vendors("amd", "intel")
    assert 190 <= _hue(hv.widget_label_color("gpu", light)) <= 230, "Intel GPU is not blue"


@pytest.mark.parametrize("light", [False, True])
def test_cpu_brand_hues(vendors, light):
    vendors("intel", "nvidia")
    assert 190 <= _hue(hv.widget_label_color("cpu", light)) <= 230, "Intel CPU is not blue"
    vendors("amd", "nvidia")
    assert _hue(hv.widget_label_color("cpu", light)) in range(0, 12), "AMD CPU is not red"


@pytest.mark.parametrize("vendor", ["intel", "amd"])
@pytest.mark.parametrize("light", [False, True])
def test_same_vendor_cpu_and_gpu_stay_apart(vendors, vendor, light):
    """Intel CPU + Intel iGPU (very common) and an all-AMD box must not paint both labels alike."""
    vendors(vendor, vendor)
    cpu, gpu = hv.widget_label_color("cpu", light), hv.widget_label_color("gpu", light)
    assert cpu != gpu
    assert abs(_hue(cpu) - _hue(gpu)) >= 15, (cpu, gpu)


@pytest.mark.parametrize("light", [False, True])
def test_unknown_vendors_keep_the_neutral_colours(vendors, light):
    vendors("unknown", "unknown")
    cpu, gpu = hv.widget_label_color("cpu", light), hv.widget_label_color("gpu", light)
    if not light:   # exactly the pre-2.2 colours on the dark taskbar - nothing changes there
        assert cpu.upper() == constants.renderer.CPU_LINE_COLOR.upper()
        assert gpu.upper() == constants.renderer.GPU_LINE_COLOR.upper()
    assert 190 <= _hue(cpu) <= 215 and _hue(gpu) in range(0, 12)


def test_hybrid_laptop_gpu_falls_back_to_neutral(monkeypatch):
    """Intel iGPU + discrete NVIDIA: the GPU % is max-across-adapters, so no single brand is honest."""
    hv.gpu_vendor.cache_clear()
    monkeypatch.setattr(hv, "_enumerate_gpu_adapters", lambda: ["intel", "nvidia"])
    monkeypatch.setattr(hv, "cpu_vendor", lambda: "intel")
    try:
        assert hv.gpu_vendor() == "unknown"
        assert hv.widget_label_color("gpu", False).upper() == constants.renderer.GPU_LINE_COLOR.upper()
    finally:
        hv.gpu_vendor.cache_clear()


@pytest.mark.parametrize("adapters", [
    ["amd", "nvidia"],             # Ryzen iGPU ("AMD Radeon(TM) Graphics") + discrete NVIDIA
    ["nvidia", "amd"],
    ["intel", "amd"],
    ["intel", "nvidia", "nvidia"],
])
def test_any_mix_of_gpu_vendors_is_hybrid(monkeypatch, adapters):
    """The Ryzen iGPU's name reads like a discrete Radeon, so a Ryzen + NVIDIA laptop used to come out
    as 'amd' (an orange/red GPU label for a GPU % that is max-across-adapters). Any vendor mix is
    'unknown', which is what the Brand tooltip promises for hybrids."""
    hv.gpu_vendor.cache_clear()
    monkeypatch.setattr(hv, "_enumerate_gpu_adapters", lambda: list(adapters))
    try:
        assert hv.gpu_vendor() == "unknown"
    finally:
        hv.gpu_vendor.cache_clear()


@pytest.mark.parametrize("adapters,expected", [
    (["nvidia"], "nvidia"), (["amd"], "amd"), (["intel"], "intel"),
    (["nvidia", "nvidia"], "nvidia"), ([], "unknown"),
])
def test_a_single_gpu_vendor_is_reported(monkeypatch, adapters, expected):
    hv.gpu_vendor.cache_clear()
    monkeypatch.setattr(hv, "_enumerate_gpu_adapters", lambda: list(adapters))
    try:
        assert hv.gpu_vendor() == expected
    finally:
        hv.gpu_vendor.cache_clear()


def test_ryzen_igpu_name_classifies_as_amd():
    """Pins the premise of the hybrid rule above: by name alone the iGPU is just 'amd'."""
    assert hv._classify_gpu("AMD Radeon(TM) Graphics") == "amd"


VENDORS_CPU = ["intel", "amd", "unknown"]
VENDORS_GPU = ["intel", "amd", "nvidia", "unknown"]


@pytest.mark.parametrize("cpu", VENDORS_CPU)
@pytest.mark.parametrize("gpu", VENDORS_GPU)
@pytest.mark.parametrize("light", [False, True])
def test_cpu_and_gpu_never_share_a_hue(vendors, cpu, gpu, light):
    """Every vendor pair, incl. the neutral fallbacks: a Ryzen + NVIDIA hybrid painted AMD-red CPU
    next to neutral-red GPU, and an unknown CPU + Intel GPU blue beside blue."""
    vendors(cpu, gpu)
    c, g = hv.widget_label_color("cpu", light), hv.widget_label_color("gpu", light)
    gap = abs(_hue(c) - _hue(g)) % 360
    assert min(gap, 360 - gap) >= 15, (cpu, gpu, c, g)


def test_amd_cpu_with_a_hybrid_gpu_gets_the_purple_neutral(vendors):
    vendors("amd", "unknown")
    for light in (False, True):
        assert 250 <= _hue(hv.widget_label_color("gpu", light)) <= 290, "not purple"


def _palette(light: bool):
    i = 1 if light else 0
    return ([v[i] for v in hv._LABEL_BRAND.values()] + [v[i] for v in hv._LABEL_GPU_SIBLING.values()]
            + [hv._LABEL_NEUTRAL["cpu"][i], hv._LABEL_NEUTRAL["gpu"][i], hv._LABEL_NEUTRAL_GPU_ALT[i]])


@pytest.mark.parametrize("light,backdrops", [(False, DARK_TASKBARS), (True, LIGHT_TASKBARS)])
def test_brand_shades_are_legible_text_on_their_taskbar(light, backdrops):
    """WCAG text contrast 4.5:1 on every reference backdrop. The pre-2.2 dark-taskbar neutrals are
    exempt (unchanged on purpose); everything new must clear it."""
    legacy = {constants.renderer.CPU_LINE_COLOR.upper(), constants.renderer.GPU_LINE_COLOR.upper()}
    for fg in _palette(light):
        if not light and fg.upper() in legacy:
            continue
        for bg in backdrops:
            assert _contrast(fg, bg) >= 4.5, f"{fg} on {bg}: {_contrast(fg, bg):.2f}:1"


# --------------------------------------------------------------------------- the renderer

def _renderer(**cfg_overrides):
    cfg = dict(D.DEFAULT_CONFIG)
    cfg.update({"monitor_cpu_enabled": True, "monitor_gpu_enabled": True, "show_hardware_temps": False,
                "hardware_label_style": "text"})
    cfg.update(cfg_overrides)
    return WidgetRenderer(themed(cfg), I18nStrings("en_US"))


def _paint_hw(r) -> np.ndarray:
    img = QImage(300, 56, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    r.draw_hardware_stats(p, 50.0, 70.0, 300, 56, r.config)
    p.end()
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    return np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4)).copy()


def _count(arr, hex_code, tol=8) -> int:
    rgb = np.array(QColor(hex_code).getRgb()[:3])
    solid = arr[:, :, 3] >= 250
    near = np.all(np.abs(arr[:, :, :3].astype(int) - rgb) <= tol, axis=2)
    return int((solid & near).sum())


@pytest.mark.parametrize("style", ["icons", "text"])
def test_match_text_on_a_light_taskbar_is_not_white(q_app, style):
    """The old monochrome style painted #FFFFFF regardless of taskbar - invisible on a light one.
    Match text follows the widget's text colour, which the theme manager makes black there."""
    r = _renderer(hardware_label_style=style, hardware_label_color="match_text", default_color="#000000")
    assert r.hardware_label_colors(r.config) == ("#000000", "#000000")
    arr = _paint_hw(r)
    # tol 10: the CPU icon's die dot is #F0F0F0 inside a black package (on purpose, see the next test).
    assert _count(arr, "#FFFFFF", tol=10) == 0, "white label pixels on a light taskbar"
    assert _count(arr, "#000000") > 20


def test_match_text_follows_a_custom_text_colour(q_app):
    r = _renderer(hardware_label_color="match_text", default_color="#FFD700")
    assert r.hardware_label_colors(r.config) == ("#FFD700", "#FFD700")


@pytest.mark.parametrize("style", ["icons", "text"])
def test_custom_colours_are_painted(q_app, style):
    r = _renderer(hardware_label_style=style, hardware_label_color="custom",
                  cpu_label_color="#FF00FF", gpu_label_color="#00FFFF")
    arr = _paint_hw(r)
    assert _count(arr, "#FF00FF") > 5, "custom CPU colour not painted"
    assert _count(arr, "#00FFFF") > 5, "custom GPU colour not painted"


def test_custom_with_a_broken_hex_falls_back_to_the_text_colour(q_app):
    r = _renderer(hardware_label_color="custom", cpu_label_color="#12", default_color="#FFFFFF")
    cpu, gpu = r.hardware_label_colors(r.config)
    assert cpu == "#FFFFFF" and gpu.upper() == D.DEFAULT_GPU_LABEL_COLOR


@pytest.mark.parametrize("text,light", [("#FFFFFF", False), ("#000000", True)])
def test_brand_picks_the_taskbar_shade_from_the_text_colour(q_app, vendors, text, light):
    vendors("intel", "nvidia")
    r = _renderer(hardware_label_color="brand", default_color=text)
    expected = (hv.widget_label_color("cpu", light), hv.widget_label_color("gpu", light))
    assert r.hardware_label_colors(r.config) == expected
    arr = _paint_hw(r)
    assert _count(arr, expected[0]) > 5 and _count(arr, expected[1]) > 5


def test_icon_die_stays_visible_in_a_black_cpu_icon(q_app):
    """A black (match-text, light taskbar) CPU icon used to fill its die #121212 - black on black."""
    r = _renderer(hardware_label_style="icons", hardware_label_color="match_text", default_color="#000000",
                  monitor_gpu_enabled=False)
    img = QImage(300, 56, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    r.draw_hardware_stats(p, 50.0, None, 300, 56, r.config)
    p.end()
    assert _count(_paint_rgba(img), "#F0F0F0") >= 1


def _paint_rgba(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.height() * img.width() * 4)
    return np.frombuffer(ptr, dtype=np.uint8).reshape((img.height(), img.width(), 4)).copy()


# --------------------------------------------------------------------------- migration + validation

@pytest.fixture
def manager(tmp_path):
    return ConfigManager(tmp_path / "netspeedtray_test.conf")


@pytest.mark.parametrize("old,style,color", [
    ("icons_colored", "icons", "brand"),
    ("icons_monochrome", "icons", "match_text"),
    ("text", "text", "brand"),
])
def test_old_styles_migrate(manager, old, style, color):
    out = manager._migrate_config({"config_version": "1.1", "hardware_label_style": old})
    assert (out["hardware_label_style"], out["hardware_label_color"]) == (style, color)
    again = manager._migrate_config(out)
    assert (again["hardware_label_style"], again["hardware_label_color"]) == (style, color), "not idempotent"


@pytest.mark.parametrize("old,style,color", [
    ("icons_colored", "icons", "brand"),
    ("icons_monochrome", "icons", "match_text"),
    ("text", "text", "brand"),
])
def test_old_styles_survive_a_full_load(tmp_path, old, style, color):
    """End to end through load(): the migration must run before validation resets the old value."""
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"config_version": "1.1", "hardware_label_style": old}), encoding="utf-8")
    loaded = ConfigManager(path).load()
    assert (loaded["hardware_label_style"], loaded["hardware_label_color"]) == (style, color)


def test_a_2_1_config_is_backed_up_before_the_label_style_migration(tmp_path):
    """RC C2: the label-style split rewrites a 2.1.x value, so the schema version moved past 1.1 -
    otherwise the pre-migration backup (`.bak.v<old>`) never triggers and a rollback has no copy."""
    path = tmp_path / "config.json"
    original = {"config_version": "1.1", "hardware_label_style": "icons_colored"}
    path.write_text(json.dumps(original), encoding="utf-8")
    loaded = ConfigManager(path).load()
    backup = path.with_name(f"{path.name}.bak.v1.1")
    assert backup.exists()
    assert json.loads(backup.read_text(encoding="utf-8")) == original
    assert loaded["config_version"] == "1.4"


def test_runtime_taskbar_height_is_neither_written_nor_preserved(tmp_path, caplog):
    """RC C3: the runtime taskbar height was injected into the config, saved to disk, and warned
    about on every launch as an unknown key 'written by a newer version?'."""
    import logging
    from unittest.mock import MagicMock
    from netspeedtray.core.config_controller import ConfigController
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"config_version": "1.4", "taskbar_height": 48, "font_size": 10}),
                    encoding="utf-8")
    cm = ConfigManager(path)
    with caplog.at_level(logging.WARNING):
        cfg = ConfigController(MagicMock(), cm).load_initial_config(48)
    assert "taskbar_height" not in cfg
    assert cfg["font_size"] == 10
    assert not [r for r in caplog.records if "taskbar_height" in r.getMessage()]


@pytest.mark.parametrize("color", ["brand", "custom", "match_text"])
def test_a_colour_already_on_file_wins_over_the_implied_one(manager, color):
    """Rollback round trip: 2.1.x / 2.2 beta.2 reset the unknown style "icons" to THEIR default,
    icons_monochrome, but keep the unknown hardware_label_color. Back on this build the colour on
    file is the user's real choice; monochrome must not turn Brand/Custom into Match text."""
    out = manager._migrate_config({"hardware_label_style": "icons_monochrome",
                                   "hardware_label_color": color, "cpu_label_color": "#00FF00"})
    assert (out["hardware_label_style"], out["hardware_label_color"]) == ("icons", color)
    assert out["cpu_label_color"] == "#00FF00"


def test_monochrome_without_a_colour_means_match_text(manager):
    """A genuine pre-2.2 monochrome config has no colour key: it keeps its look as Match text."""
    out = manager._migrate_config({"hardware_label_style": "icons_monochrome"})
    assert out["hardware_label_color"] == "match_text"


def test_new_text_style_is_left_alone(manager):
    out = manager._migrate_config({"hardware_label_style": "text", "hardware_label_color": "custom"})
    assert (out["hardware_label_style"], out["hardware_label_color"]) == ("text", "custom")


def test_fresh_defaults():
    assert D.DEFAULT_CONFIG["hardware_label_style"] == "icons"
    assert D.DEFAULT_CONFIG["hardware_label_color"] == "brand"   # owner call: new installs get Brand
    # Custom starts at the pre-2.2 neutral colours.
    assert D.DEFAULT_CPU_LABEL_COLOR.upper() == constants.renderer.CPU_LINE_COLOR.upper()
    assert D.DEFAULT_GPU_LABEL_COLOR.upper() == constants.renderer.GPU_LINE_COLOR.upper()


@pytest.mark.parametrize("key,bad", [
    ("hardware_label_style", "icons_colored"),   # unmigrated old values are not valid any more
    ("hardware_label_style", "sparkles"),
    ("hardware_label_color", "rainbow"),
    ("cpu_label_color", "red"),
    ("gpu_label_color", "#12345"),
])
def test_validation_rejects_bad_values(manager, key, bad):
    out = manager._validate_config({key: bad})
    assert out[key] == D.VALIDATION_SCHEMA[key]["default"]


def test_validation_keeps_good_values(manager):
    good = {"hardware_label_style": "text", "hardware_label_color": "custom",
            "cpu_label_color": "#00ff00", "gpu_label_color": "#ABCDEF"}
    out = manager._validate_config(good)
    assert {k: out[k] for k in good} == good


# --------------------------------------------------------------------------- the Settings page

@pytest.fixture
def page(qt_owned):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    return qt_owned(HardwarePage(I18nStrings("en_US"), lambda: None))


def test_page_round_trips_the_colour_settings(page):
    page.load_settings({"hardware_label_style": "text", "hardware_label_color": "custom",
                        "cpu_label_color": "#112233", "gpu_label_color": "#445566"})
    g = page.get_settings()
    assert (g["hardware_label_style"], g["hardware_label_color"]) == ("text", "custom")
    assert (g["cpu_label_color"], g["gpu_label_color"]) == ("#112233", "#445566")
    assert not page.label_colors_container.isHidden(), "custom pickers hidden while Custom is selected"


def test_page_hides_the_pickers_unless_custom(page):
    page.load_settings({"hardware_label_color": "brand"})
    assert page.label_colors_container.isHidden()
    page.label_color.setCurrentIndex(page.label_color.findData("custom"))
    assert not page.label_colors_container.isHidden()


def test_page_colour_dialog_result_lands_in_the_field(page):
    page.load_settings({"hardware_label_color": "custom"})
    page.set_color_input("gpu_label_color", "#abcdef")
    assert page.get_settings()["gpu_label_color"] == "#ABCDEF"


def test_page_half_typed_hex_keeps_the_last_valid_colour(q_app):
    """Saving (or the live preview firing) mid-edit must keep the colour the user had, not reset it
    to the default - and partial keystrokes must not push a settings update at all."""
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    calls = []
    page = HardwarePage(I18nStrings("en_US"), lambda: calls.append(1))
    page.load_settings({"hardware_label_color": "custom", "cpu_label_color": "#112233"})
    calls.clear()
    page.cpu_label_color_input.setText("#12")
    assert page.get_settings()["cpu_label_color"] == "#112233"
    assert not calls, "a half-typed hex pushed a settings update"
    page.cpu_label_color_input.setText("#123456")
    assert page.get_settings()["cpu_label_color"] == "#123456"
    assert calls, "a complete hex did not update the settings"
    page.cpu_label_color_input.setText("#1234")
    assert page.get_settings()["cpu_label_color"] == "#123456"


def test_preview_mirrors_the_label_colour(q_app):
    """The Settings preview is a PreviewWidget fed the dialog's settings: it must paint the choice."""
    from netspeedtray.views.widget.preview import PreviewWidget
    cfg = dict(D.DEFAULT_CONFIG)
    cfg.update({"monitor_cpu_enabled": True, "monitor_gpu_enabled": True, "widget_display_mode": "side_by_side",
                "hardware_label_style": "text", "hardware_label_color": "custom",
                "cpu_label_color": "#FF00FF", "gpu_label_color": "#00FFFF", "graph_enabled": False})
    pw = PreviewWidget(cfg, I18nStrings("en_US"), width=300, height=40)
    img = QImage(pw.size(), QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    pw.render(img)
    arr = _paint_rgba(img)
    assert _count(arr, "#FF00FF") > 5 and _count(arr, "#00FFFF") > 5
