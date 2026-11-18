"""
2.2.2 combined review, UX round: one keyboard focus-ring colour on every new surface, one High Contrast
primary-button treatment, one secondary-button style, and the copy / glyph decisions that are code
(the first-run tip's button and double-click line, the "opens a browser" glyph, the Overview's Check
connection icon). The strings themselves are pinned in the locale tests.
"""
import types

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import styles as su
from netspeedtray.utils import taskbar_appearance as ta

EN = I18nStrings("en_US")
HC = {"text": "#FFFFFF", "background": "#000000", "link": "#FFFF00", "focus": "#1AEBFF"}
HC_UI = {"text": "#FFFFFF", "background": "#000000", "highlight": "#1AEBFF", "highlight_text": "#000000"}


@pytest.fixture
def light(monkeypatch):
    monkeypatch.setattr(su, "is_dark_mode", lambda: False)
    monkeypatch.setattr(ta, "read_high_contrast_link_colors", lambda: None)
    monkeypatch.setattr(ta, "read_high_contrast_ui_colors", lambda: None)


@pytest.fixture
def contrast(monkeypatch):
    monkeypatch.setattr(ta, "read_high_contrast_link_colors", lambda: dict(HC))
    monkeypatch.setattr(ta, "read_high_contrast_ui_colors", lambda: dict(HC_UI))


# --- U3: one focus-ring colour -----------------------------------------------------------------------

def test_the_focus_ring_is_the_text_colour_outside_high_contrast(light):
    from netspeedtray.utils.components import notice_link_colors
    from netspeedtray.views.flyout import _card_colors
    from netspeedtray.views.monitor.internet_offer import confirm_colors
    text = su.semantic_colors()["text_primary"]
    assert notice_link_colors(su.semantic_colors()["card_bg"])["focus"] == text
    assert confirm_colors()["focus"] == text
    assert _card_colors()["focus"] == text


def test_the_focus_ring_is_highlight_in_high_contrast(contrast):
    from netspeedtray.utils.components import notice_link_colors
    from netspeedtray.views.flyout import _card_colors
    from netspeedtray.views.monitor.internet_offer import confirm_colors
    assert notice_link_colors()["focus"] == HC["focus"]
    assert confirm_colors()["focus"] == HC["focus"]
    assert _card_colors()["focus"] == HC_UI["highlight"]


# --- U7: one High Contrast primary button, one secondary style ---------------------------------------

def test_the_tip_and_the_internet_confirm_fill_the_primary_with_highlight_in_high_contrast(qtbot, contrast):
    from netspeedtray.views.flyout import Flyout
    from netspeedtray.views.monitor.internet_offer import InternetCheckConfirm
    tip = Flyout("t", "b", action_text="Open Monitor", secondary_text="Got it", auto_dismiss_ms=0,
                 focusable=True)
    qtbot.addWidget(tip)
    assert f"background-color: {HC_UI['highlight']}" in tip._action_btn.styleSheet()
    assert HC_UI["highlight"] not in tip._secondary_btn.styleSheet().split("kbfocus")[0]
    confirm = InternetCheckConfirm(EN, framed=True)
    qtbot.addWidget(confirm)
    sheet = confirm.styleSheet()
    assert f"QPushButton#internetConfirmOn {{ background: {HC['focus']}" in sheet


def test_the_secondary_buttons_share_one_style(qtbot, light):
    """The tip's "Got it" and the internet confirm's "Not now": the Fluent standard button (subtle
    fill, 1 px card stroke) - not the older grey button_style."""
    from netspeedtray.views.flyout import Flyout
    from netspeedtray.views.monitor.internet_offer import InternetCheckConfirm
    c = su.semantic_colors()
    tip = Flyout("t", "b", action_text="Open Monitor", secondary_text="Got it", auto_dismiss_ms=0,
                 focusable=True)
    qtbot.addWidget(tip)
    confirm = InternetCheckConfirm(EN, framed=True)
    qtbot.addWidget(confirm)
    for sheet in (tip._secondary_btn.styleSheet(), confirm.styleSheet().split("internetConfirmNotNow", 1)[1]):
        assert c["subtle_fill"].lower() in sheet.lower() and c["card_stroke"].lower() in sheet.lower()


# --- U2 / U7: glyphs ---------------------------------------------------------------------------------

def test_the_overviews_check_connection_button_is_the_globe(qtbot, light, monkeypatch):
    """The same glyph as the right-click menu's and the running panel's Check connection (E774)."""
    from netspeedtray.views.monitor import connection_check as cc
    seen = []
    real = su.fluent_icon
    monkeypatch.setattr(su, "fluent_icon", lambda cp, *a, **k: (seen.append(cp), real(cp, *a, **k))[1])
    btn = cc.make_check_button(EN)
    qtbot.addWidget(btn)
    assert seen == [0xE774]


def test_a_link_that_opens_the_browser_shows_open_in_new(qtbot, light):
    from netspeedtray.utils.components import NoticeLink
    from netspeedtray.views.temp_onboarding_dialog import TempOnboardingDialog
    dlg = TempOnboardingDialog(EN, None)
    qtbot.addWidget(dlg)
    assert dlg.get_lhm_link.chevron_codepoint() == 0xE8A7          # Get LibreHardwareMonitor: a web page
    inside = NoticeLink("", "Open Settings", lambda: None)
    qtbot.addWidget(inside)
    assert inside.chevron_codepoint() == 0xE76C                     # an in-app link keeps the chevron


def test_whats_new_shows_open_in_new_on_its_button(qtbot, light, monkeypatch):
    from netspeedtray.core import startup_notices as logic
    from netspeedtray.views.widget import startup_notices as ui
    w = types.SimpleNamespace(i18n=EN, config={})
    card = ui.StartupNotices._build(types.SimpleNamespace(_w=w, _version="2.2.2",
                                                          _open_release_page=lambda: None), logic.NOTICE_WHATS_NEW)
    qtbot.addWidget(card)
    assert not card._action_btn.icon().isNull()


def test_all_apps_has_no_arrow_character_and_a_chevron_glyph(qtbot, light):
    from netspeedtray.views.monitor.overview.busiest_apps import BusiestAppsCard
    card = BusiestAppsCard(EN)
    qtbot.addWidget(card)
    assert "→" not in EN.MONITOR_VIEW_ALL_APPS
    assert card.all_apps_chevron.pixmap() is not None and not card.all_apps_chevron.pixmap().isNull()


# --- U7: the first-run tip's button and its double-click line ----------------------------------------

def _tip(config):
    from netspeedtray.core import startup_notices as logic
    from netspeedtray.views.widget import startup_notices as ui
    w = types.SimpleNamespace(i18n=EN, config=config, open_monitor_window=lambda: None)
    return ui.StartupNotices._build(types.SimpleNamespace(_w=w, _version="2.2.2", _open_monitor=lambda: None),
                                    logic.NOTICE_TIP)


def _body(card):
    from PyQt6.QtWidgets import QLabel
    return "\n".join(lbl.text() for lbl in card.findChildren(QLabel))


@pytest.mark.parametrize("action,line", [
    ("open_monitor", "Double-click to open the Monitor."),
    ("settings", "Double-click: Open Settings"),
    ("pause_resume", "Double-click: Pause / Resume"),
    ("none", None),
])
def test_the_tips_double_click_line_follows_the_setting(qtbot, light, action, line):
    card = _tip({"double_click_action": action})
    qtbot.addWidget(card)
    assert card._action_btn.text() == EN.CLICK_ACTION_OPEN_MONITOR_LABEL == "Open Monitor"
    body = _body(card)
    assert "Point to the speeds for details." in body and "Right-click for the menu and Settings." in body
    if line is None:
        assert "Double-click" not in body
    else:
        assert line in body
        if action != "open_monitor":
            assert "open the Monitor" not in body
