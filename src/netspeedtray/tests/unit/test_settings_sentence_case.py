"""
rc.2 S6 - the Settings window speaks one casing: Win11 sentence case, with no trailing colons on card
titles. The 2.2 redesign wrote its new strings that way, but the 2.0-era titles beside them stayed in
Title Case ("Show Mini Graph", "Graph Timespan:"), and fr_FR / tr_TR had copied the English casing.

Every locale: no card / expander / section title ends with a colon. en_US: no title capitalizes a word
after the first, except acronyms, units and the app's own surface names.
"""
import re
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QLabel, QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.components import SettingCard, SettingExpander
from netspeedtray.views.settings.dialog import SettingsDialog

LOCALES = sorted(I18nStrings.LANGUAGE_MAP) if hasattr(I18nStrings, "LANGUAGE_MAP") else ["en_US"]

# Capitalized mid-title words that are names, not Title Case.
_NAMES = {"Windows", "Intel", "NVIDIA", "AMD", "LibreHardwareMonitor", "NetSpeedTray", "Mb", "Mbps", "Kbps",
          "Wi-Fi", "Ethernet"}
# Titles that name the app's own surfaces / menu commands ("the Settings and Monitor windows").
_SURFACE_TITLES = {"Keep Settings and Monitor windows on top", "Add Pause/Resume to the right-click menu"}


def _dialog(qtbot, monkeypatch, locale):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update(interface_mode="selected")
    m = MagicMock()
    m.config = {}
    m.config_manager = MagicMock()
    dlg = SettingsDialog(main_widget=m, config=cfg, version="0", i18n=I18nStrings(locale),
                         available_interfaces=["Ethernet", "Wi-Fi"], is_startup_enabled=False,
                         primary_interface="Wi-Fi")
    qtbot.addWidget(dlg)
    return dlg


def _titles(dlg):
    out = []
    for i in range(dlg.stack.count()):
        page = dlg.stack.widget(i).widget()
        for card in page.findChildren(SettingCard):
            out.append(card._title_label.text())
        for exp in page.findChildren(SettingExpander):
            out.append(exp._title_label.text())
        out += [lbl.text() for lbl in page.findChildren(QLabel, "settingsSectionHeader")]
    out += [dlg.sidebar.item(i).text() for i in range(dlg.sidebar.count())]
    return [t for t in (s.strip() for s in out) if t]


def _title_case_words(title: str):
    words = re.findall(r"[^\W\d_][\w'\-]*", title)
    return [w for w in words[1:]
            if w[0].isupper() and w not in _NAMES and sum(ch.isupper() for ch in w) < 2]


@pytest.mark.parametrize("locale", LOCALES)
def test_no_settings_title_ends_with_a_colon(qtbot, monkeypatch, locale):
    titles = _titles(_dialog(qtbot, monkeypatch, locale))
    assert titles, "found no Settings titles - the collector is broken"
    colons = [t for t in titles if t.endswith((":", "："))]
    assert not colons, f"{locale}: titles with a trailing colon: {colons}"


def test_english_settings_titles_are_sentence_case(qtbot, monkeypatch):
    titles = _titles(_dialog(qtbot, monkeypatch, "en_US"))
    offenders = {t: _title_case_words(t) for t in titles if t not in _SURFACE_TITLES}
    offenders = {t: w for t, w in offenders.items() if w}
    assert not offenders, "Title Case left in Settings: " + "; ".join(f"{t!r} {w}" for t, w in offenders.items())
