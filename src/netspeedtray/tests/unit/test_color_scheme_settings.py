"""
Settings for the colour scheme (2.2 rc.2): every themed colour row shows a dark-taskbar and a
light-taskbar swatch - ``[moon][swatch][hex]  [sun][swatch][hex]`` - with no theme selector; Color
coding gains an optional Medium level (#9); the Widget page's Mini graph gains the upload / download
colours (#117). The behaviour #405 / S3 / S5 pinned with the old automatic-colour workarounds is kept
in test_rc_settings_fixes.py / test_windows_mode_theme.py, rewritten for the pairs.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.dialog import SettingsDialog
from netspeedtray.tests.unit.test_rc_settings_fixes import _Main, _flush

D = constants.config.defaults
THEMED_KEYS = [f"{s}_{t}" for s in D.THEMED_COLOR_STEMS for t in ("dark", "light")]
SRC_DIR = Path(__file__).resolve().parents[3]
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


@pytest.fixture(autouse=True)
def _quiet_boxes(monkeypatch):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)


def _make(qtbot, cfg=None, lang="en_US"):
    cfg = dict(D.DEFAULT_CONFIG if cfg is None else cfg)
    main = _Main(cfg)
    dlg = SettingsDialog(main_widget=main, config=cfg, version="2.2.0", i18n=I18nStrings(lang),
                         available_interfaces=["Ethernet", "Wi-Fi"], is_startup_enabled=False)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.settings_changed.connect(lambda c: main.handle_settings_changed(c, save_to_disk=False))
    qtbot.addWidget(dlg)
    return dlg, main


def _owner(dlg, key):
    return next(pg for pg in dlg._color_pages() if key in pg.get_settings())


def _distinct():
    """A different valid colour per themed key."""
    return {k: f"#{(i * 0x0F0B07 + 0x102030) & 0xFFFFFF:06X}" for i, k in enumerate(THEMED_KEYS)}


def test_every_themed_colour_has_a_dark_and_a_light_field(qtbot):
    dlg, _ = _make(qtbot)
    settings = dlg.get_settings()
    for key in THEMED_KEYS:
        assert key in settings, key
        page = _owner(dlg, key)
        assert getattr(page, f"{key}_button").objectName() == key
        assert getattr(page, f"{key}_input").text() == D.DEFAULT_CONFIG[key]
    for legacy in D.LEGACY_COLOR_KEYS:
        assert legacy not in settings, f"the dialog still writes {legacy}"


def test_no_theme_selector_and_the_glyphs_say_which_taskbar(qtbot):
    from PyQt6.QtWidgets import QLabel
    dlg, _ = _make(qtbot)
    i18n = dlg.i18n
    for key in THEMED_KEYS:
        page = _owner(dlg, key)
        glyph = page.findChild(QLabel, f"{key}_glyph")
        theme = i18n.THEME_LIGHT_TASKBAR if key.endswith("_light") else i18n.THEME_DARK_TASKBAR
        assert glyph is not None and glyph.toolTip() == theme and glyph.accessibleName() == theme, key
        assert getattr(page, f"{key}_button").accessibleName().endswith(theme), key
        assert getattr(page, f"{key}_input").accessibleName().endswith(theme), key


def test_load_and_get_round_trip_every_key(qtbot):
    values = _distinct()
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, **values, medium_speed_threshold=4.5))
    s = {}
    for page in dlg._pages():   # what the CONTROLS hold, not the dialog's copy of the config
        s.update(page.get_settings())
    assert {k: s.get(k) for k in THEMED_KEYS} == values
    assert s["medium_speed_threshold"] == 4.5


@pytest.mark.parametrize("key", THEMED_KEYS)
def test_a_picked_colour_lands_in_its_own_field(qtbot, monkeypatch, key):
    """Routing by the section that owns the key; the old key.replace('_color', '') convention could
    not express the _dark / _light suffixes."""
    dlg, main = _make(qtbot)
    seen = {}
    def pick(initial, parent, title):
        seen["initial"] = initial.name().upper()
        return QColor("#12AB34")
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(pick))
    dlg._open_color_dialog(key)
    _flush(dlg)
    assert seen["initial"] == D.DEFAULT_CONFIG[key]          # the picker opens on the field's colour
    s = dlg.get_settings()
    assert s[key] == "#12AB34"
    assert {k: s[k] for k in THEMED_KEYS if k != key} == {k: D.DEFAULT_CONFIG[k] for k in THEMED_KEYS if k != key}
    assert main.config[key] == "#12AB34"                     # reached the live preview


def test_a_cancelled_pick_changes_nothing(qtbot, monkeypatch):
    dlg, _ = _make(qtbot)
    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a: QColor()))   # invalid = cancelled
    dlg._open_color_dialog("arrow_down_color_light")
    assert dlg.appearance_page.arrow_down_color_light_input.text() == D.DEFAULT_CONFIG["arrow_down_color_light"]


def test_reset_all_resets_every_new_key(qtbot):
    values = _distinct()
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, **values, medium_speed_threshold=4.5))
    dlg.advanced_page.reset_all_btn.click()
    s = dlg.get_settings()
    for key in THEMED_KEYS + ["medium_speed_threshold"]:
        assert s[key] == D.DEFAULT_CONFIG[key], key


def test_the_medium_colour_row_shows_only_while_medium_is_on(qtbot):
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, color_coding=True))
    colors = dlg.colors_page
    card = colors.medium_speed_color_card
    assert not card.isVisibleTo(colors), "Medium is Off by default: its colour row is hidden"
    assert colors.medium_speed_threshold.text().strip() == dlg.i18n.CONN_PLAN_OFF
    colors.medium_speed_threshold.setValue(5.0)
    assert card.isVisibleTo(colors)
    assert dlg.get_settings()["medium_speed_threshold"] == 5.0
    colors.medium_speed_threshold.setValue(0.0)
    assert not card.isVisibleTo(colors)
    dlg2, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, color_coding=True, medium_speed_threshold=4.0))
    assert dlg2.colors_page.medium_speed_color_card.isVisibleTo(dlg2.colors_page)


def test_medium_sits_between_high_and_low(qtbot):
    from netspeedtray.utils.components import SettingCard
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, color_coding=True, medium_speed_threshold=5.0))
    titles = [c._title_label.text() for c in dlg.colors_page.findChildren(SettingCard)]
    i18n = dlg.i18n
    order = [i18n.HIGH_SPEED_THRESHOLD_LABEL, i18n.HIGH_SPEED_COLOR_LABEL, i18n.MEDIUM_SPEED_THRESHOLD_LABEL,
             i18n.MEDIUM_SPEED_COLOR_LABEL, i18n.LOW_SPEED_THRESHOLD_LABEL, i18n.LOW_SPEED_COLOR_LABEL]
    assert [titles.index(t) for t in order] == sorted(titles.index(t) for t in order)


def test_the_graph_colour_rows_grey_out_with_the_graph(qtbot):
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, graph_enabled=False))
    mg = dlg.widget_page.mini_graph_section
    assert len(mg._color_cards) == 2 and not any(c.isEnabled() for c in mg._color_cards)
    mg.enable_graph.setChecked(True)
    assert all(c.isEnabled() for c in mg._color_cards)


def test_a_typed_light_colour_previews_and_saves_without_touching_the_dark_one(qtbot):
    from PyQt6.QtTest import QTest
    dlg, main = _make(qtbot)
    field = dlg.appearance_page.default_color_light_input
    field.selectAll()
    QTest.keyClicks(field, "#151618")
    _flush(dlg)
    assert (main.config["default_color_dark"], main.config["default_color_light"]) == ("#FFFFFF", "#151618")
    dlg.save_button.click()
    assert (main.saved[-1]["default_color_dark"], main.saved[-1]["default_color_light"]) == ("#FFFFFF", "#151618")


@pytest.mark.parametrize("locale", sorted(I18nStrings.LANGUAGE_MAP))
def test_the_medium_tooltip_quotes_the_medium_colour_label(locale):
    i18n = I18nStrings(locale)
    assert i18n.MEDIUM_SPEED_COLOR_LABEL in i18n.MEDIUM_SPEED_THRESHOLD_TOOLTIP
    assert i18n.CONN_PLAN_OFF in i18n.MEDIUM_SPEED_THRESHOLD_TOOLTIP   # names the spinbox's own "Off"


# --- the two swatch pairs fit beside their title at the dialog's minimum width (design 4 / 7) -------

_PROBE = textwrap.dedent(r"""
    import json, sys
    from unittest.mock import MagicMock
    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication(sys.argv)
    for n in ("critical", "information", "warning"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: None))
    from netspeedtray import constants
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.dialog import SettingsDialog
    from netspeedtray.views.settings.pages._fluent import ThemedColorPair
    from netspeedtray.utils.components import SettingCard

    def pump():
        for _ in range(5):
            app.processEvents()

    def check(dlg, lang, label, out):
        seen = 0
        for i in range(dlg.stack.count()):
            dlg.navigate_to_page(i)
            pump()
            page = dlg.stack.widget(i).widget()
            vp = dlg.stack.widget(i).viewport()
            for card in page.findChildren(SettingCard):
                if not isinstance(card.control(), ThemedColorPair) or not card.isVisibleTo(page):
                    continue
                seen += 1
                title = card._title_label.text()
                lbl = card._title_label
                if lbl.width() < lbl.minimumSizeHint().width():
                    out.append(f"{lang} {label} {dlg.width()}px: {title!r} title squeezed below a word")
                if card.isStacked() and card.control().geometry().top() < lbl.geometry().bottom():
                    out.append(f"{lang} {label} {dlg.width()}px: {title!r} swatches overlap the title")
                ctl = card.control()
                right = ctl.mapTo(vp, ctl.rect().topRight()).x()
                if right > vp.width() or ctl.width() < ctl.sizeHint().width():
                    out.append(f"{lang} {label} {dlg.width()}px: {title!r} swatches clipped / past the edge")
                for line in ctl.inputs.values():
                    if line.width() < 60:
                        out.append(f"{lang} {label}: {title!r} hex field only {line.width()} px")
        out.append(f"#seen {lang} {label} {seen} {dlg.width()}")

    out = []
    for lang in sys.argv[1].split(","):
        cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
        cfg.update(language=lang, color_coding=True, medium_speed_threshold=5.0, use_custom_arrow_colors=True,
                   graph_enabled=True)
        parent = MagicMock(); parent.config = {}; parent.config_manager = MagicMock()
        dlg = SettingsDialog(main_widget=parent, config=cfg, version="0", i18n=I18nStrings(lang),
                             available_interfaces=["Ethernet", "Wi-Fi"], is_startup_enabled=False)
        dlg.show()
        pump()
        for label, width in (("default", dlg.width()), ("minimum", dlg.minimumWidth())):
            dlg.resize(width, dlg.height())
            pump()
            check(dlg, lang, label, out)
        dlg.close(); dlg.deleteLater()
        pump()
    print("RESULT" + json.dumps(out), flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or not FONT_DIR.is_dir(),
                    reason="needs the Windows font directory for real text metrics")
def test_swatch_pairs_fit_without_a_theme_selector(tmp_path):
    """en + the long ones (de / hu / ru), at the width the dialog opens with and at its minimum (640 px
    and up): every pair is shown whole - no swatch or hex field clipped or past the edge, no title
    squeezed. At those widths the card drops the pair under its title (SettingCard's reflow, as for
    every wide control in Settings); it never needs the design's theme-selector fallback, whose
    trigger was overflow / elided labels. Real Windows font metrics, so a subprocess (see
    test_settings_no_hscroll)."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QPA_FONTDIR=str(FONT_DIR), QT_SCALE_FACTOR="1",
               APPDATA=str(tmp_path), PYTHONPATH=str(SRC_DIR), PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-c", _PROBE, "en_US,de_DE,hu_HU,ru_RU"], env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, f"probe failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    out = json.loads(lines[-1][len("RESULT"):])
    seen = [ln for ln in out if ln.startswith("#seen")]
    assert len(seen) == 8 and all(int(ln.split()[3]) == 8 for ln in seen), seen   # all 8 rows, both widths
    problems = [ln for ln in out if not ln.startswith("#seen")]
    assert problems == [], "\n".join(problems)


# --- the thresholds are shown and entered in the user's unit (PR #441 review item 5) -----------------
# Stored in decimal Mbps (what _speed_band compares in) - the boxes used to show that Mbps number under
# the user's unit label, so "5 MB/s" banded at 5 Mbps = 0.625 MB/s. No migration: the stored values
# always meant Mbps and still do; only what the boxes show changes.

UNITS = [("bits_decimal", 1.0, "Mbps"), ("bytes_decimal", 1 / 8, "MB/s"),
         ("bits_binary", 1e6 / 2 ** 20, "Mibps"), ("bytes_binary", 1e6 / (8 * 2 ** 20), "MiB/s")]


@pytest.mark.parametrize("unit,per_mbps,label", UNITS)
def test_threshold_boxes_show_the_stored_mbps_in_the_users_unit(qtbot, unit, per_mbps, label):
    from netspeedtray.utils.helpers import threshold_units_per_mbps
    assert threshold_units_per_mbps(unit) == pytest.approx(per_mbps)
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, unit_type=unit, color_coding=True,
                               high_speed_threshold=80.0, medium_speed_threshold=40.0, low_speed_threshold=8.0))
    c = dlg.colors_page
    for band, mbps in (("high_speed", 80.0), ("medium_speed", 40.0), ("low_speed", 8.0)):
        spin = getattr(c, f"{band}_threshold")
        assert spin.value() == pytest.approx(mbps * per_mbps, abs=0.001), band
        assert spin.suffix().strip() == label
    # An untouched Save returns the stored values exactly - no unit round-trip drift.
    s = dlg.get_settings()
    assert (s["high_speed_threshold"], s["medium_speed_threshold"], s["low_speed_threshold"]) == (80.0, 40.0, 8.0)


@pytest.mark.parametrize("unit,per_mbps,label", UNITS)
def test_a_threshold_typed_in_the_users_unit_bands_at_that_speed(qtbot, unit, per_mbps, label):
    """'5' in the user's unit means 5 of that unit: just under it is Low, at it is High."""
    from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer
    dlg, main = _make(qtbot, dict(D.DEFAULT_CONFIG, unit_type=unit, color_coding=True))
    dlg.colors_page.high_speed_threshold.setValue(5.0)
    _flush(dlg)
    stored = main.config["high_speed_threshold"]
    assert stored == pytest.approx(5.0 / per_mbps)
    rc = RenderConfig.from_dict(main.config)
    bytes_per_unit = 1e6 / 8 / per_mbps     # bytes/s in one displayed unit
    assert WidgetRenderer._speed_band(4.99 * bytes_per_unit, rc.high_speed_threshold, rc.low_speed_threshold) == "low"
    assert WidgetRenderer._speed_band(5.0 * bytes_per_unit, rc.high_speed_threshold, rc.low_speed_threshold) == "high"


def test_changing_the_unit_reshows_the_thresholds_and_keeps_them(qtbot):
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, unit_type="bits_decimal", color_coding=True,
                               high_speed_threshold=80.0))
    c = dlg.colors_page
    c.set_unit_type("bytes_decimal")
    assert c.high_speed_threshold.value() == pytest.approx(10.0)
    assert c.high_speed_threshold.suffix().strip() == "MB/s"
    assert dlg.get_settings()["high_speed_threshold"] == 80.0
    c.set_unit_type("bits_decimal")
    assert c.high_speed_threshold.value() == pytest.approx(80.0)


def test_the_box_range_never_exceeds_what_validation_keeps(qtbot):
    sliders = constants.ui.sliders
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, unit_type="bytes_binary"))
    c = dlg.colors_page
    per = 1e6 / (8 * 2 ** 20)
    assert c.high_speed_threshold.maximum() == pytest.approx(sliders.SPEED_THRESHOLD_MAX_HIGH * per, abs=0.001)
    assert c.low_speed_threshold.maximum() == pytest.approx(sliders.SPEED_THRESHOLD_MAX_LOW * per, abs=0.001)


# --- a misplaced Medium is flagged in Settings, not silently zeroed (review item 6) ------------------

@pytest.mark.parametrize("medium,shown", [(0.0, False), (5.0, False), (1.0, True), (0.5, True), (10.0, True),
                                          (20.0, True)])
def test_the_medium_range_hint(qtbot, medium, shown):
    dlg, _ = _make(qtbot, dict(D.DEFAULT_CONFIG, color_coding=True))
    c = dlg.colors_page
    c.medium_speed_threshold.setValue(medium)
    assert c.medium_range_hint.isVisibleTo(c) is shown
    assert c.medium_range_hint.text() == dlg.i18n.MEDIUM_SPEED_RANGE_HINT
    c.high_speed_threshold.setValue(50.0)            # moving High can make it valid again
    assert c.medium_range_hint.isVisibleTo(c) is (medium > 0 and not (1.0 < medium < 50.0))


@pytest.mark.parametrize("locale", sorted(I18nStrings.LANGUAGE_MAP))
def test_the_medium_tooltip_states_the_range(locale):
    """Every locale has its own hint and a tooltip that states the range (translated, so not English)."""
    en = I18nStrings("en_US")
    i18n = I18nStrings(locale)
    assert "between the low and high" in en.MEDIUM_SPEED_THRESHOLD_TOOLTIP
    assert i18n.MEDIUM_SPEED_RANGE_HINT.strip()
    if locale != "en_US":
        assert i18n.MEDIUM_SPEED_RANGE_HINT != en.MEDIUM_SPEED_RANGE_HINT
        assert i18n.MEDIUM_SPEED_THRESHOLD_TOOLTIP != en.MEDIUM_SPEED_THRESHOLD_TOOLTIP


# --- the preview strip follows the TASKBAR theme (review item 3) ------------------------------------

def test_the_preview_strip_follows_the_windows_mode_not_the_app_mode(qtbot, monkeypatch):
    from netspeedtray.utils import color_scheme
    from netspeedtray.views.settings import dialog as D_mod
    monkeypatch.setattr(D_mod, "is_dark_mode", lambda: True)          # apps dark...
    monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: 1)   # ...taskbar light
    color_scheme.refresh_shell_theme()
    dlg, _ = _make(qtbot)
    assert constants.color.PREVIEW_STRIP_LIGHT in dlg._preview_strip.styleSheet()
    assert dlg.preview_widget._renderer.config.default_color == "#1B1B1B"
    monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: 0)   # the flip
    color_scheme.refresh_shell_theme()
    dlg.on_taskbar_theme_changed()
    assert constants.color.PREVIEW_STRIP_DARK in dlg._preview_strip.styleSheet()
    assert dlg.preview_widget._renderer.config.default_color == "#FFFFFF"


def test_the_widget_forwards_a_flip_to_an_open_settings_dialog():
    from unittest.mock import MagicMock
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    fake = MagicMock()
    calls = []
    fake.theme_manager.on_theme_changed.side_effect = lambda: calls.append("theme")
    fake.settings_dialog.on_taskbar_theme_changed.side_effect = lambda: calls.append("dialog")
    NetworkSpeedWidget._on_theme_changed(fake)
    assert calls == ["theme", "dialog"]       # the flag is refreshed before the dialog re-resolves
    fake.settings_dialog = None
    NetworkSpeedWidget._on_theme_changed(fake)   # no dialog yet: nothing to forward
