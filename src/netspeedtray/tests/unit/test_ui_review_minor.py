"""
2.2 UI review, minor items: the hover card's numbers use the locale decimal separator, the plan-speed
headroom choices use the locale's percent form, the metrics URL field looks like the port spin box,
and the 2.2 graph-scale titles follow the sentence case of the cards around them.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QLabel

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.helpers import format_percent


def _texts(w):
    return " ".join(lbl.text() for lbl in w.findChildren(QLabel))


def test_hover_card_numbers_use_the_locale_decimal_separator(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(I18nStrings("de_DE"), (1.2e9, 8.4e9), (31.5e9, 210.8e9), cap=(242.3, 500.0, 48.5))
    text = _texts(card)
    assert "8,4 GB" in text and "210,8 GB" in text and "242,3 / 500" in text, text
    assert "8.4" not in text and "242.3" not in text, text


def test_hover_card_fractional_cap_uses_the_separator(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(I18nStrings("fr_FR"), (0, 0), (0, 0), cap=(1.0, 2.5, 40.0))
    assert "/ 2,5" in _texts(card)


@pytest.mark.parametrize("lang,expected", [
    ("en_US", "10%"), ("tr_TR", "%10"), ("fr_FR", "10 %"), ("de_DE", "10 %"), ("ja_JP", "10%"),
])
def test_percent_follows_the_locale(lang, expected):
    assert format_percent(10, I18nStrings(lang)) == expected


def test_percent_falls_back_without_a_template():
    assert format_percent(25, None) == "25%"


@pytest.mark.parametrize("lang,expected", [("tr_TR", "%10"), ("fr_FR", "10 %"), ("en_US", "10%")])
def test_headroom_choices_use_the_locale_percent(q_app, lang, expected):
    from netspeedtray.views.settings.pages.appearance import AppearancePage
    page = AppearancePage(I18nStrings(lang), lambda: None, MagicMock(), MagicMock())
    combo = page.graph_scale_headroom
    labels = [combo.itemText(i) for i in range(combo.count())]
    i = combo.findData(10)
    assert i >= 0 and labels[i] == expected, labels


def test_metrics_url_field_is_styled_like_the_spin_box(q_app):
    from netspeedtray.utils import styles as su
    from netspeedtray.views.settings.pages.advanced import AdvancedPage
    page = AdvancedPage(I18nStrings("en_US"), lambda: None)
    assert page.metrics_url.property("fieldStyle") == "input"
    css = su.dialog_style()
    assert 'QSpinBox, QDoubleSpinBox, QLineEdit[fieldStyle="input"]' in css


def test_new_graph_scale_titles_are_sentence_case():
    i18n = I18nStrings("en_US")
    for key in ("GRAPH_FIXED_SCALE_LABEL", "GRAPH_SCALE_HEADROOM_LABEL", "GRAPH_SHOW_CEILING_LABEL"):
        words = getattr(i18n, key).split()
        assert all(w[0].islower() for w in words[1:]), (key, getattr(i18n, key))
