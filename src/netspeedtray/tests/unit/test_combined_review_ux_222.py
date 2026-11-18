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
