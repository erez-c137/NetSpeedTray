"""
Hover-card placement never covers its own widget.

A card over the widget sits under the resting cursor: the widget gets a leave event, hides the card,
gets an enter event and shows it again 350 ms later - a flicker loop. Two ways it happened (rc check):
an AUTO-HIDE side bar does not shrink the work area, so "flush with the work area's inner edge" put
the card on the widget (27-28 x 58 px); and a free-float / free-move widget near a screen's top
always opened the card above, where the clamp pushed it back down onto the widget (85 x 40 px).

Pure geometry through ``UsageFlyout.placement`` (what ``show_for`` moves the window to); no window is
shown.
"""
import itertools

import pytest
from PyQt6.QtCore import QRect, QSize

from netspeedtray.constants import TaskbarEdge
from netspeedtray.views.usage_flyout import UsageFlyout

T, B, L, R = TaskbarEdge.TOP, TaskbarEdge.BOTTOM, TaskbarEdge.LEFT, TaskbarEdge.RIGHT

SCREENS = {
    "primary": QRect(0, 0, 1920, 1080),
    "left_negative_x": QRect(-1920, 0, 1920, 1080),
    "above_negative_y": QRect(0, -1080, 1920, 1080),
    "right_offset": QRect(1920, -200, 2560, 1440),
}
CARDS = [QSize(278, 222), QSize(320, 360)]
BAR = 48


def _bar_case(screen: QRect, edge, along: float, autohide: bool):
    """(widget rect, work area) for a widget docked on ``edge`` at fraction ``along`` of the bar."""
    s = screen
    if edge in (L, R):
        wh = 58
        y = s.top() + round(along * (s.height() - wh))
        x = s.right() + 1 - BAR if edge == R else s.left()
        widget = QRect(x, y, BAR, wh)
        if autohide:
            avail = QRect(s)
        elif edge == R:
            avail = QRect(s.left(), s.top(), s.width() - BAR, s.height())
        else:
            avail = QRect(s.left() + BAR, s.top(), s.width() - BAR, s.height())
    else:
        ww = 85
        x = s.left() + round(along * (s.width() - ww))
        y = s.bottom() + 1 - BAR if edge == B else s.top()
        widget = QRect(x, y, ww, BAR)
        if autohide:
            avail = QRect(s)
        elif edge == B:
            avail = QRect(s.left(), s.top(), s.width(), s.height() - BAR)
        else:
            avail = QRect(s.left(), s.top() + BAR, s.width(), s.height() - BAR)
    return widget, avail


@pytest.mark.parametrize("screen_name", SCREENS)
@pytest.mark.parametrize("edge", [T, B, L, R])
@pytest.mark.parametrize("autohide", [False, True], ids=["docked", "autohide"])
def test_docked_card_never_covers_the_widget(screen_name, edge, autohide):
    screen = SCREENS[screen_name]
    for along, sz in itertools.product([0.0, 0.02, 0.5, 0.98, 1.0], CARDS):
        widget, avail = _bar_case(screen, edge, along, autohide)
        card = UsageFlyout.placement(widget, avail, sz, edge)
        ctx = (screen_name, edge, autohide, along, sz, widget, avail, card)
        assert not card.intersects(widget), ctx
        assert avail.contains(card), ctx
        # ... and it opens away from the bar, toward the desktop.
        if edge == R:
            assert card.right() < widget.left(), ctx
        elif edge == L:
            assert card.left() > widget.right(), ctx
        elif edge == T:
            assert card.top() > widget.bottom(), ctx
        else:
            assert card.bottom() < widget.top(), ctx


@pytest.mark.parametrize("screen_name", SCREENS)
@pytest.mark.parametrize("edge", [None, B, T], ids=["free_float", "free_move_bottom", "free_move_top"])
def test_free_floating_or_dragged_card_never_covers_the_widget(screen_name, edge):
    """Anywhere on the screen - corners included - the card flips to whichever side has room."""
    s = SCREENS[screen_name]
    for fx, fy, sz in itertools.product([0.0, 0.01, 0.5, 0.99, 1.0], [0.0, 0.01, 0.3, 0.5, 0.99, 1.0], CARDS):
        widget = QRect(0, 0, 85, 40)
        widget.moveTo(s.left() + round(fx * (s.width() - 85)), s.top() + round(fy * (s.height() - 40)))
        card = UsageFlyout.placement(widget, s, sz, edge)
        ctx = (screen_name, edge, fx, fy, sz, widget, card)
        assert not card.intersects(widget), ctx
        assert s.contains(card), ctx


def test_free_float_near_the_top_opens_below():
    s = SCREENS["primary"]
    widget = QRect(1700, 20, 85, 40)
    card = UsageFlyout.placement(widget, s, CARDS[0], None)
    assert card.top() > widget.bottom()


def test_bottom_bar_still_opens_above_and_centred():
    widget, avail = _bar_case(SCREENS["primary"], B, 0.5, autohide=True)
    card = UsageFlyout.placement(widget, avail, CARDS[0], B)
    assert card.bottom() < widget.top()
    assert abs(card.center().x() - widget.center().x()) <= 1


def test_card_too_big_for_the_work_area_still_stays_off_the_widget():
    """No clamped position clears the widget -> keep one beside it, even past the work area."""
    avail = QRect(0, 0, 300, 200)
    widget = QRect(100, 80, 85, 40)
    card = UsageFlyout.placement(widget, avail, QSize(280, 190), B)
    assert not card.intersects(widget)
