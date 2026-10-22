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
import types

import pytest
from PyQt6.QtCore import QRect, QSize

from netspeedtray.constants import TaskbarEdge
import netspeedtray.views.usage_flyout as _uf
from netspeedtray.views.usage_flyout import UsageFlyout

SHADOW = getattr(_uf, "_SHADOW_MARGIN", 12)   # the card window's transparent shadow margin

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


def test_no_room_anywhere_keeps_the_card_on_its_screen():
    """No side of the widget has room (a 640 x 360 screen, widget mid-screen): the card used to keep
    an unclamped position beside the widget - off the monitor (y -102..-215) or onto a neighbour.
    Now its body stays on the screen; it covers as little of the widget as it can."""
    screen = QRect(0, 0, 640, 360)
    avail = screen.adjusted(0, 0, 0, -48)
    widget = QRect(260, 160, 120, 40)
    card = UsageFlyout.placement(widget, avail, QSize(294, 249), None, bounds=screen)
    body = card.adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
    assert screen.contains(body), (card, body)


def _review_placements():
    """The independent reviewer's sweep (h_place.py): 6 monitors incl. tiny and negative-coordinate,
    every edge + free-float, docked and auto-hide, 40 widget positions each."""
    import random
    mons = [("1080p", QRect(0, 0, 1920, 1080)), ("small-right", QRect(1920, 200, 1024, 600)),
            ("portrait", QRect(-1080, -400, 1080, 1920)), ("above", QRect(0, -1080, 1920, 1080)),
            ("tiny", QRect(1920, 0, 800, 480)), ("tiny2", QRect(0, 0, 640, 360))]
    th = 48
    for (name, scr), edge, ah, pos in itertools.product(mons, [B, T, L, R, None], [False, True], range(40)):
        if edge == B:
            avail = scr.adjusted(0, 0, 0, 0 if ah else -th)
        elif edge == T:
            avail = scr.adjusted(0, 0 if ah else th, 0, 0)
        elif edge == L:
            avail = scr.adjusted(0 if ah else th, 0, 0, 0)
        elif edge == R:
            avail = scr.adjusted(0, 0, 0 if ah else -th, 0)
        else:
            avail = scr.adjusted(0, 0, 0, -th)
        rnd = random.Random(pos)
        if edge in (B, T):
            ww, wh = 120, th
            x = scr.left() + [0, scr.width() - ww, rnd.randrange(scr.width() - ww)][pos % 3]
            y = scr.bottom() + 1 - th if edge == B else scr.top()
        elif edge in (L, R):
            ww, wh = th, 60
            y = scr.top() + [0, scr.height() - wh, rnd.randrange(scr.height() - wh)][pos % 3]
            x = scr.left() if edge == L else scr.right() + 1 - th
        else:
            ww, wh = 120, 40
            x = scr.left() + rnd.randrange(scr.width() - ww)
            y = scr.top() + rnd.randrange(scr.height() - wh)
        taskbar = None
        if edge is not None and not ah:
            taskbar = {B: QRect(scr.left(), scr.bottom() + 1 - th, scr.width(), th),
                       T: QRect(scr.left(), scr.top(), scr.width(), th),
                       L: QRect(scr.left(), scr.top(), th, scr.height()),
                       R: QRect(scr.right() + 1 - th, scr.top(), th, scr.height())}[edge]
        yield name, scr, avail, edge, QRect(x, y, ww, wh), taskbar


@pytest.mark.parametrize("sz", [QSize(278, 249), QSize(294, 249)], ids=["en", "ru"])
def test_reviewer_placement_sweep(sz):
    for name, scr, avail, edge, widget, taskbar in _review_placements():
        card = UsageFlyout.placement(widget, avail, sz, edge, bounds=scr)
        body = card.adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)
        ctx = (name, edge, widget, card)
        assert scr.contains(body), ctx
        assert avail.contains(body), ctx
        assert taskbar is None or not body.intersects(taskbar), ctx
        # Over the widget only when no side has room for it.
        lo_x, hi_x = avail.left() + 8, avail.right() + 1 - 8        # work area minus _EDGE_MARGIN
        lo_y, hi_y = avail.top() + 8, avail.bottom() + 1 - 8
        room = (widget.left() - 8 - lo_x >= sz.width() or hi_x - (widget.right() + 1 + 8) >= sz.width()
                or widget.top() - 8 - lo_y >= sz.height() or hi_y - (widget.bottom() + 1 + 8) >= sz.height())
        if room:
            assert not card.intersects(widget), ctx


def test_the_card_is_transparent_for_input(q_app):
    """Even a card forced over its widget cannot steal the hover (and start the flicker loop): an
    unpinned card is made click-through - natively, before it is first shown. Never with Qt's
    WindowTransparentForInput, which leaves a later-pinned card deaf to clicks (B5; the real-window
    check is test_hover_card_pin_native.py)."""
    from unittest.mock import patch
    from PyQt6.QtCore import Qt, QRect
    card = UsageFlyout(types.SimpleNamespace(**{k: k for k in (
        "BYTES_UNIT", "KB_UNIT", "MB_UNIT", "GB_UNIT", "TB_UNIT", "PB_UNIT", "USAGE_TODAY_LABEL",
        "USAGE_THIS_MONTH_LABEL")}), (1, 1), (1, 1))
    order = []
    try:
        assert not card.windowFlags() & Qt.WindowType.WindowTransparentForInput
        with patch("netspeedtray.views.usage_flyout._set_click_through",
                   side_effect=lambda w, through: order.append(("through", through))), \
                patch.object(card, "show", side_effect=lambda: order.append("show")):
            card.show_for(QRect(1600, 1032, 85, 48), QRect(0, 0, 1920, 1032))
        assert order == [("through", True), "show"]
    finally:
        card.deleteLater()
