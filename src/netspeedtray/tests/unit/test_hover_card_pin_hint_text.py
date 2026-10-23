"""The hover card's pin footer must point at the WIDGET (rc.2 action plan A1).

An unpinned card is click-through (WS_EX_TRANSPARENT): a click on it lands in the app behind it.
The click that pins the card goes on the widget (core/input_handler.py ``_on_single_click``). The
footer once read "Click to keep this open" - shown ON the card, it told people to click the one
thing that does not pin. These tests keep every locale naming the widget, and keep the longest
translation readable on the narrowest card (the footer word-wraps at 300 px; it must never clip).
"""
import json
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from netspeedtray.constants.i18n import I18nStrings

LOCALES_DIR = Path(__file__).resolve().parents[2] / "constants" / "locales"

# Each locale's own word for the widget, as its other widget strings already use it
# (HOVER_USAGE_CARD_LABEL, POWER_HOVER_CARD_LABEL, WIDGET_SETTINGS_GROUP ...). Lower-cased match.
WIDGET_TERM = {
    "en_US": "widget", "de_DE": "widget", "es_ES": "widget", "fr_FR": "widget",
    "it_IT": "widget", "nl_NL": "widget", "hu_HU": "widget", "tr_TR": "widget",
    "pl_PL": "widż", "ru_RU": "виджет", "sl_SI": "gradnik", "he_IL": "יישומון",
    "ja_JP": "ウィジェット", "ko_KR": "위젯", "zh_CN": "小部件", "zh_TW": "小工具",
}

LOCALES = sorted(p.stem for p in LOCALES_DIR.glob("*.json"))


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


def _hint(locale: str) -> str:
    return json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))["HOVER_CARD_PIN_HINT"]


def test_every_shipped_locale_has_a_widget_term():
    assert set(LOCALES) == set(WIDGET_TERM)


def test_english_pin_hint_points_at_the_widget():
    text = _hint("en_US").lower()
    assert "widget" in text
    assert "click the widget" in text


@pytest.mark.parametrize("locale", LOCALES)
def test_pin_hint_names_the_widget_in_every_locale(locale):
    assert WIDGET_TERM[locale] in _hint(locale).lower()


@pytest.mark.parametrize("locale", LOCALES)
def test_pin_hint_wraps_and_never_clips_on_the_narrowest_card(locale):
    """The narrowest card a user can get: no usage rows, no tips - just a short hardware line plus
    the footer. The footer must wrap inside the card, with room for every wrapped line."""
    from netspeedtray.views.usage_flyout import UsageFlyout

    i18n = I18nStrings(locale)
    app = QApplication.instance()
    old_dir = app.layoutDirection()
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl
                           else Qt.LayoutDirection.LeftToRight)
    try:
        card = UsageFlyout(i18n, None, None, hardware={"cpu": {"usage": 7.0}},
                           pin_hint=i18n.HOVER_CARD_PIN_HINT)
        card.adjustSize()
        card.show()                    # offscreen platform (conftest): lays out the children
        app.processEvents()
        lbl = card._pin_hint_lbl
        assert lbl is not None
        assert lbl.wordWrap()                      # wraps; a QLabel never elides
        assert lbl.maximumWidth() <= 300
        assert lbl.width() <= 300
        # Tall enough for every wrapped line at the width it got ...
        assert lbl.height() >= lbl.heightForWidth(lbl.width())
        # ... and wholly inside the card.
        top_left = lbl.mapTo(card, QPoint(0, 0))
        assert top_left.x() >= 0 and top_left.y() >= 0
        assert top_left.x() + lbl.width() <= card.width()
        assert top_left.y() + lbl.height() <= card.height()
        card.hide()
        card.deleteLater()
    finally:
        app.setLayoutDirection(old_dir)
