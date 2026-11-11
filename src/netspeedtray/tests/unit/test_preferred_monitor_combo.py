"""Settings > Widget > Preferred monitor (relu616, rc.4 on #373).

The entries listed Qt's LOGICAL size (a 1920 x 1080 screen at 150 % read "1280 x 720") and the
220 px box cut "Monitor 1: 1280 x 720 (Hauptbildschirm)" off. Now: the native mode and the scale,
a box as wide as its longest entry (capped; the open list never cut), and the stored value is still
the screen name, so existing saved choices keep matching.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.dialog import SettingsDialog
from netspeedtray.views.settings.pages import widget as page_mod

DEFAULTS = constants.config.defaults.DEFAULT_CONFIG


class _FakeScreen:
    def __init__(self, name, w, h, dpr):
        self._name, self._geo, self._dpr = name, QRect(0, 0, w, h), dpr

    def name(self):
        return self._name

    def geometry(self):
        return self._geo

    def devicePixelRatio(self):
        return self._dpr


@pytest.fixture
def two_screens(monkeypatch):
    screens = [_FakeScreen(r"\\.\DISPLAY1", 1280, 720, 1.5), _FakeScreen(r"\\.\DISPLAY2", 2560, 1440, 1.0)]
    app = MagicMock()
    app.screens.return_value = screens
    app.primaryScreen.return_value = screens[0]
    fake_cls = MagicMock()
    fake_cls.instance.return_value = app
    fake_cls.layoutDirection.side_effect = QGuiApplication.layoutDirection
    monkeypatch.setattr(page_mod, "QGuiApplication", fake_cls)
    return screens


def _dialog(qtbot, monkeypatch, lang="en_US", cfg=None):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    m = MagicMock(); m.config = {}; m.config_manager = MagicMock()
    d = SettingsDialog(main_widget=m, config=dict(DEFAULTS, **(cfg or {})), version="2.2.0",
                       i18n=I18nStrings(lang), available_interfaces=["Ethernet"], is_startup_enabled=False)
    qtbot.addWidget(d)
    return d


def test_monitor_list_shows_the_native_resolution_and_scale(qtbot, monkeypatch, two_screens):
    d = _dialog(qtbot, monkeypatch)
    combo = d.widget_page.preferred_monitor_combo
    first, second = combo.itemText(1), combo.itemText(2)
    assert "1920 x 1080, 150%" in first and "1280" not in first      # not Qt's logical 1280 x 720
    assert d.i18n.PREFERRED_MONITOR_PRIMARY_TAG in first
    assert "2560 x 1440, 100%" in second
    # The stored value is still the screen name, so a saved choice keeps matching.
    assert combo.itemData(1) == r"\\.\DISPLAY1" and combo.itemData(2) == r"\\.\DISPLAY2"


def test_a_saved_monitor_still_selects_its_entry(qtbot, monkeypatch, two_screens):
    d = _dialog(qtbot, monkeypatch, cfg={"preferred_monitor": r"\\.\DISPLAY2"})
    combo = d.widget_page.preferred_monitor_combo
    assert combo.currentData() == r"\\.\DISPLAY2"
    assert d.i18n.SETTINGS_NOT_CONNECTED_TAG not in combo.currentText()
    assert d.get_settings()["preferred_monitor"] == r"\\.\DISPLAY2"


@pytest.mark.parametrize("lang", ["de_DE", "en_US", "ru_RU", "hu_HU"])
def test_monitor_combo_is_wide_enough_for_its_longest_entry(qtbot, monkeypatch, two_screens, lang):
    d = _dialog(qtbot, monkeypatch, lang=lang)
    combo = d.widget_page.preferred_monitor_combo
    fm = combo.fontMetrics()
    longest = max(fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count()))
    dims = constants.ui.dialogs
    assert combo.minimumWidth() >= min(longest + dims.MONITOR_COMBO_CHROME_PX, dims.MONITOR_COMBO_MAX_WIDTH)
    assert combo.minimumWidth() <= dims.MONITOR_COMBO_MAX_WIDTH
    assert combo.view().minimumWidth() >= longest            # the open list never cuts an entry


@pytest.mark.parametrize("lang", sorted(I18nStrings.LANGUAGE_MAP))
def test_every_locale_formats_the_entry(lang):
    text = I18nStrings(lang).PREFERRED_MONITOR_ITEM.format(index=1, width=1920, height=1080, scale=150)
    assert "1920" in text and "1080" in text and "150" in text


def test_bengali_monitor_number_in_bengali_digits(qtbot, monkeypatch, two_screens):
    """"Monitor 1" is a fixed label: its number in Bengali digits (the translator's rule on #477); the
    resolution and scale are technical values and stay Western. English is unchanged."""
    d = _dialog(qtbot, monkeypatch, lang="bn_BD")
    combo = d.widget_page.preferred_monitor_combo
    first, second = combo.itemText(1), combo.itemText(2)
    assert first.startswith("মনিটর ১:") and "1920 x 1080, 150%" in first, first
    assert second.startswith("মনিটর ২:") and "2560 x 1440, 100%" in second, second
    en = _dialog(qtbot, monkeypatch).widget_page.preferred_monitor_combo
    assert en.itemText(1).startswith("Monitor 1:"), en.itemText(1)
