"""
Tests for UsageFlyout — the Win11 hover card that replaced the clipped Qt tooltip and the
tray usage rows. Covers content (Today/This-month, conditional hint + cap) and the key fix:
the card is clamped fully inside the screen work area so the taskbar can never clip it.
"""
import types

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication, QLabel

from netspeedtray.views.usage_flyout import UsageFlyout


@pytest.fixture(scope="session")
def q_app():
    return QApplication.instance() or QApplication([])


def _i18n():
    ns = types.SimpleNamespace()
    for k, v in {"BYTES_UNIT": "B", "KB_UNIT": "KB", "MB_UNIT": "MB",
                 "GB_UNIT": "GB", "TB_UNIT": "TB", "PB_UNIT": "PB"}.items():
        setattr(ns, k, v)
    ns.USAGE_TODAY_LABEL = "Today"
    ns.USAGE_THIS_MONTH_LABEL = "This month"
    ns.TRAY_DATA_CAP_MENU_ITEM = "Data cap…"
    ns.WIDGET_HOVER_TOOLTIP = "Right-click for graphs, app activity, and CPU/GPU stats"
    return ns


def _texts(card):
    return [w.text() for w in card.findChildren(QLabel)]


def test_card_renders_today_and_month_rows(q_app):
    card = UsageFlyout(_i18n(), today=(4.4e6, 320.3e3), month=(4.4e6, 320.3e3))
    texts = _texts(card)
    assert "Today" in texts and "This month" in texts
    joined = " ".join(texts)
    assert "↓" in joined and "↑" in joined           # both directions shown
    assert "MB" in joined and "KB" in joined          # formatted with decimal units


def test_hint_line_present_only_when_given(q_app):
    with_hint = UsageFlyout(_i18n(), (0, 0), (0, 0), hint="Right-click for graphs")
    assert any("Right-click" in t for t in _texts(with_hint))
    without = UsageFlyout(_i18n(), (0, 0), (0, 0))
    assert not any("Right-click" in t for t in _texts(without))


def test_card_can_be_hint_only(q_app):
    """Tips toggle on, data toggle off -> a card with only the gesture hint, no usage rows."""
    card = UsageFlyout(_i18n(), hint="Right-click for graphs")
    texts = _texts(card)
    assert any("Right-click" in t for t in texts)
    assert "Today" not in texts and "This month" not in texts


def test_card_can_be_data_only(q_app):
    """Data toggle on, tips toggle off -> usage rows with no hint."""
    card = UsageFlyout(_i18n(), today=(1e6, 1e6), month=(1e6, 1e6))
    texts = _texts(card)
    assert "Today" in texts and "This month" in texts
    assert not any("Right-click" in t for t in texts)


def test_cap_line_only_when_cap_set(q_app):
    no_cap = UsageFlyout(_i18n(), (0, 0), (0, 0))
    assert not any("Data cap" in t for t in _texts(no_cap))

    capped = UsageFlyout(_i18n(), (0, 0), (0, 0), cap=(4.8, 50.0, 9.6))
    joined = " ".join(_texts(capped))
    assert "Data cap" in joined and "50" in joined and "%" in joined


def test_show_for_clamps_fully_into_work_area(q_app):
    """The widget lives *in* the taskbar band; the card must land fully inside the work area
    (which excludes the taskbar) — this is the fix for the clipped tooltip."""
    avail = QRect(0, 0, 1920, 1040)           # screen 1920x1080 minus a 40px bottom taskbar
    widget_geo = QRect(1700, 1045, 120, 30)   # widget sits inside the taskbar band
    card = UsageFlyout(_i18n(), (1e6, 1e6), (1e6, 1e6))
    card.show_for(widget_geo, avail)
    g = card.geometry()
    assert g.left() >= avail.left()
    assert g.top() >= avail.top()
    assert g.right() <= avail.right()
    assert g.bottom() <= avail.bottom()       # never under the taskbar
