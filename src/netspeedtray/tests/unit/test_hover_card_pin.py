"""Click-to-pin the hover card (side-taskbar design §2, Phase B) - on every taskbar edge.

A single left click on the widget used to do nothing. Now:
- card up from hover -> the click pins it (it survives the pointer leaving);
- no card up (touch has no hover, or the click beat the 350 ms hover delay) -> wait
  doubleClickInterval(), then open it pinned - so a double-click still runs its action cleanly;
- a pinned card closes on: a 2nd click, another app coming to the front, the context menu, the
  double-click action, a drag start, a fullscreen app, a dock change;
- the card never takes focus: it keeps WindowDoesNotAcceptFocus + WA_ShowWithoutActivating, and
  pinning only moves/resizes it. (The widget is a different matter: a click on it makes it the
  foreground window, as it always has - only its show is non-activating.);
- a card opened by hover carries a "Click to keep this open" line while the gesture hint is still
  teaching; pinning drops it.

Driven through the REAL InputHandler and the real NetworkSpeedWidget methods (bound to a light
widget), never a launched app.
"""
import os
import types
from unittest.mock import ANY, MagicMock, call, patch

import pytest
from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.input_handler import InputHandler
from netspeedtray.views.widget.main import NetworkSpeedWidget as NSW

EN = I18nStrings("en_US")


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


_BOUND = ("hover_card_state", "pin_hover_card", "dismiss_hover_card", "_hide_usage_hover_card",
          "_on_foreground_for_hover_card", "_on_pointer_left", "_on_immediate_hide_requested")


class _Widget(QWidget):
    """Just enough of NetworkSpeedWidget for the pin state machine: its real methods, a fake card."""

    def __init__(self):
        super().__init__()
        self.config = {}
        self._dragging = False
        self.is_paused = False
        self.logger = MagicMock()
        self._hover_card = None
        self._hover_card_pinned = False
        self._hover_card_timer = MagicMock()
        self.position_manager = MagicMock()
        self.position_manager.is_free_float_active.return_value = False
        self.open_monitor_window = MagicMock()
        self.shown = []
        self.cards = []
        for name in _BOUND:
            setattr(self, name, types.MethodType(getattr(NSW, name), self))

    def _show_usage_hover_card(self, pinned=False):   # what the real one leaves behind
        card = MagicMock()
        self._hover_card, self._hover_card_pinned = card, bool(pinned)
        self.shown.append(pinned)
        self.cards.append(card)

    def setVisible(self, visible):                   # the fullscreen hide - not under test here
        self.visible_set = visible


@pytest.fixture
def w():
    widget = _Widget()
    handler = InputHandler(widget, MagicMock(), MagicMock())
    widget.handler = widget.input_handler = handler
    yield widget
    handler._click_timer.stop()


def _ev(kind="press", button=Qt.MouseButton.LeftButton):
    e = MagicMock(spec=QMouseEvent)
    e.button.return_value = button
    e.buttons.return_value = button
    e.globalPosition.return_value = QPointF(10.0, 10.0)
    return e


def _click(w):
    w.handler.handle_mouse_press(_ev())
    w.handler.handle_mouse_release(_ev())


def _double_click(w):
    """Qt's sequence: press, release, DOUBLE-CLICK (instead of the 2nd press), release."""
    _click(w)
    w.handler.handle_double_click(_ev())
    w.handler.handle_mouse_release(_ev())


def _hover(w):
    w._show_usage_hover_card(pinned=False)


def _settle(w):
    """Let the double-click window run out."""
    w.handler._click_timer.stop()
    w.handler._on_single_click_settled()


# --- the state machine ----------------------------------------------------------------------------

def test_hover_then_click_pins_at_once(w):
    _hover(w)
    _click(w)
    assert w.hover_card_state() == "pinned"
    w.cards[0].set_pinned.assert_called_once_with(True)
    assert not w.handler._click_timer.isActive()            # no wait when the card is already up


def test_pinned_card_survives_the_pointer_leaving_a_hover_card_does_not(w):
    _hover(w)
    w._on_pointer_left()
    assert w.hover_card_state() is None
    _hover(w)
    _click(w)
    w._on_pointer_left()
    assert w.hover_card_state() == "pinned"


def test_second_click_closes_the_pinned_card(w):
    _hover(w)
    _click(w)
    _click(w)
    assert w.hover_card_state() is None
    w.cards[0].hide.assert_called()


def test_touch_tap_waits_for_the_double_click_window_then_opens_pinned(w):
    _click(w)                                                # no hover first: a touch tap
    assert w.shown == []                                     # nothing flashes yet
    assert w.handler._click_timer.isActive()
    assert w.handler._click_timer.interval() == QApplication.doubleClickInterval()
    _settle(w)
    assert w.shown == [True] and w.hover_card_state() == "pinned"


def test_double_click_still_opens_the_monitor_cleanly(w):
    _double_click(w)
    w.open_monitor_window.assert_called_once()
    assert not w.handler._click_timer.isActive()             # the pending pin was cancelled
    assert w.shown == [] and w.hover_card_state() is None    # no card flashed
    _click(w)                                                # the next click is a fresh click
    assert w.handler._click_timer.isActive()


def test_double_click_over_a_hover_card_runs_the_action_and_closes_the_card(w):
    _hover(w)
    _double_click(w)
    w.open_monitor_window.assert_called_once()
    assert w.hover_card_state() is None


def test_drag_start_closes_a_pinned_card(w):
    _hover(w)
    _click(w)
    w.handler._drag_start_pos = QPoint(5, 5)
    w.handler.position_manager.constrain_drag.side_effect = lambda p: p
    move = _ev()
    move.globalPosition.return_value = QPointF(200.0, 200.0)
    w.handler.handle_mouse_move(move)
    assert w.hover_card_state() is None
    w.handler._is_dragging = False


def test_a_release_after_a_drag_is_not_a_click(w):
    w.handler._is_dragging = True
    w.handler._drag_start_pos = w.pos()
    w.handler.handle_mouse_release(_ev())
    assert not w.handler._click_timer.isActive() and w.shown == []


def test_middle_and_right_clicks_leave_the_card_alone(w):
    _hover(w)
    w.handler.handle_mouse_press(_ev(button=Qt.MouseButton.MiddleButton))
    w.handler.handle_mouse_release(_ev(button=Qt.MouseButton.MiddleButton))
    w.handler.handle_mouse_press(_ev(button=Qt.MouseButton.RightButton))
    assert w.hover_card_state() == "hover"


def test_another_app_coming_to_the_front_closes_it_our_own_windows_do_not(w):
    _hover(w)
    _click(w)
    with patch("win32process.GetWindowThreadProcessId", return_value=(1, os.getpid())):
        w._on_foreground_for_hover_card(1234)                 # the widget itself got activated
    assert w.hover_card_state() == "pinned"
    with patch("win32process.GetWindowThreadProcessId", return_value=(1, os.getpid() + 1)):
        w._on_foreground_for_hover_card(5678)
    assert w.hover_card_state() is None


def test_foreground_changes_do_not_touch_an_unpinned_card(w):
    _hover(w)
    with patch("win32process.GetWindowThreadProcessId", return_value=(1, os.getpid() + 1)):
        w._on_foreground_for_hover_card(5678)
    assert w.hover_card_state() == "hover"                   # leaving the widget handles that one


@pytest.mark.parametrize("keep_visible", [False, True])
def test_a_fullscreen_app_closes_it_even_when_the_widget_stays(w, keep_visible):
    w.config["keep_visible_fullscreen"] = keep_visible
    _hover(w)
    _click(w)
    w._on_immediate_hide_requested()
    assert w.hover_card_state() is None


def test_the_context_menu_closes_it(w):
    from netspeedtray.core.tray_manager import TrayIconManager
    _hover(w)
    _click(w)
    w._is_context_menu_visible = False
    m = TrayIconManager(w, EN)
    m._load_and_set_icon = lambda: None
    m.initialize()
    with patch.object(m.context_menu, "exec") as exec_, patch.object(m, "_calculate_menu_position"):
        w._execute_refresh = MagicMock()
        m.show_context_menu()
    exec_.assert_called_once()
    assert w.hover_card_state() is None


def test_a_dock_change_closes_it():
    """A dock change re-sizes the widget; the re-size closes any card (pinned too) first."""
    from netspeedtray.views.widget.layout import WidgetLayoutManager
    widget = MagicMock()
    widget._hover_card = MagicMock()
    lm = WidgetLayoutManager(widget)
    lm.metrics = MagicMock()
    with patch("netspeedtray.utils.taskbar_utils.get_free_float_screen", side_effect=RuntimeError):
        lm.resize_widget_for_font()
    widget._hide_usage_hover_card.assert_called_once()
    fake = types.SimpleNamespace(_hover_card=MagicMock(), _hover_card_pinned=True)
    NSW._hide_usage_hover_card(fake)
    assert fake._hover_card is None and fake._hover_card_pinned is False


# --- no focus stealing ----------------------------------------------------------------------------

def test_card_never_takes_focus_and_pinning_only_moves_it(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, (1e6, 2e6), (3e6, 4e6), hint="Right-click for the menu.",
                       pin_hint=EN.HOVER_CARD_PIN_HINT)
    try:
        assert card.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert card.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        with patch.object(card, "activateWindow") as act, patch.object(card, "raise_") as raise_, \
                patch.object(card, "setFocus") as focus:
            card.show_for(QRect(1600, 1032, 85, 48), QRect(0, 0, 1920, 1032))
            assert card.pin_hint_visible
            before = card.sizeHint().height()
            card.set_pinned(True)
            assert not card.pin_hint_visible
            assert card.sizeHint().height() < before            # the line is gone, card re-placed
            assert card.geometry().bottom() < 1032                # still above a bottom bar
        act.assert_not_called(); raise_.assert_not_called(); focus.assert_not_called()
    finally:
        card.hide()
        card.deleteLater()


def test_pinning_from_the_handler_never_activates(w):
    _hover(w)
    _click(w)
    card = w.cards[0]
    card.activateWindow.assert_not_called()
    card.raise_.assert_not_called()
    card.setFocus.assert_not_called()


def test_the_widget_window_itself_still_shows_without_activating(q_app):
    from netspeedtray.views.widget.layout import WidgetLayoutManager
    widget = QWidget()
    lm =WidgetLayoutManager.__new__(WidgetLayoutManager)
    lm.widget, lm.logger = widget, MagicMock()
    lm.setup_window_properties()
    assert widget.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


# --- the "Click to keep this open" footer ---------------------------------------------------------

def _show_fake(**config):
    cfg = {"show_usage_on_hover": False, "show_hover_tips": True, **config}
    fake = types.SimpleNamespace(
        config=cfg, i18n=EN, logger=MagicMock(), _is_context_menu_visible=False, _dragging=False,
        system_event_handler=None, _hover_card=None, _hover_card_pinned=False,
        isVisible=lambda: True, screen=lambda: None, frameGeometry=lambda: QRect(1600, 1032, 85, 48),
        _hover_hint_text=lambda: "hint", _hover_hardware_wanted=lambda: False,
        _hover_card_edge=lambda: None, _hide_usage_hover_card=MagicMock(),
        dismiss_hover_card=MagicMock(), _PIN_HINT_MAX_SHOWS=NSW._PIN_HINT_MAX_SHOWS)
    fake.config_controller = types.SimpleNamespace(
        update_config=lambda updates, apply_and_repaint=True: cfg.update(updates))
    for name in ("_pin_hint_text", "_retire_pin_hint"):
        setattr(fake, name, types.MethodType(getattr(NSW, name), fake))
    return fake


def _show(fake, **kw):
    with patch("netspeedtray.views.usage_flyout.UsageFlyout") as card_cls, \
            patch("netspeedtray.views.widget.main.QApplication.primaryScreen", return_value=None):
        NSW._show_usage_hover_card(fake, **kw)
    return card_cls


@pytest.mark.parametrize("pinned,expect", [(False, EN.HOVER_CARD_PIN_HINT), (True, None)])
def test_hover_card_gets_the_pin_footer_only_when_opened_by_hover(pinned, expect):
    fake = _show_fake()
    card_cls = _show(fake, pinned=pinned)
    assert card_cls.call_args[1]["pin_hint"] == expect
    assert fake._hover_card_pinned is pinned


def test_pin_footer_shows_on_the_first_few_cards_even_after_the_gesture_hint_graduated():
    fake = _show_fake()
    fake._hover_hint_text = lambda: None                   # an upgrader: the gesture hint is long gone
    fake._hover_hardware_wanted = lambda: True
    fake._hover_hardware_snapshot = lambda: {"cpu": {"usage": 12.0}}
    hints = [_show(fake).call_args[1]["pin_hint"] for _ in range(NSW._PIN_HINT_MAX_SHOWS + 2)]
    assert hints == [EN.HOVER_CARD_PIN_HINT] * NSW._PIN_HINT_MAX_SHOWS + [None, None]
    assert fake.config["pin_hint_shown_count"] == NSW._PIN_HINT_MAX_SHOWS


def test_pinning_once_retires_the_footer():
    fake = _show_fake()
    _show(fake)
    fake._hover_card = MagicMock()
    fake._hover_card_timer = MagicMock()
    NSW.pin_hover_card(fake)
    assert fake.config["pin_hint_shown_count"] == NSW._PIN_HINT_MAX_SHOWS
    assert _show(fake).call_args[1]["pin_hint"] is None


def test_card_opened_pinned_takes_clicks_and_a_click_on_it_closes_it():
    fake = _show_fake()
    card_cls = _show(fake, pinned=True)
    card = card_cls.return_value
    card.dismiss_requested.connect.assert_called_once_with(fake.dismiss_hover_card)
    card.set_pinned.assert_called_once_with(True)            # after show_for: native style exists
    assert card.method_calls.index(call.show_for(ANY, ANY, edge=None, screen=None)) < \
        card.method_calls.index(call.set_pinned(True))


# --- the pinned card takes clicks (it is click-through otherwise) ---------------------------------

def _card(**kw):
    from netspeedtray.views.usage_flyout import UsageFlyout
    return UsageFlyout(EN, (1e6, 2e6), (3e6, 4e6), hint="Right-click for the menu.",
                       pin_hint=EN.HOVER_CARD_PIN_HINT, **kw)


def test_pinning_clears_only_ws_ex_transparent_and_unpinning_restores_it(q_app):
    import ctypes
    from netspeedtray.views import usage_flyout as uf
    noactivate, layered = 0x08000000, 0x00080000
    style = {"ex": noactivate | layered | uf._WS_EX_TRANSPARENT}
    user32 = ctypes.windll.user32
    card = _card()
    try:
        with patch.object(user32, "GetWindowLongW", side_effect=lambda h, i: style["ex"]), \
                patch.object(user32, "SetWindowLongW",
                             side_effect=lambda h, i, v: style.update(ex=v)) as set_long, \
                patch.object(card, "setWindowFlags") as set_flags:
            card.show_for(QRect(1600, 1032, 85, 48), QRect(0, 0, 1920, 1032))
            card.set_pinned(True)
            assert style["ex"] == noactivate | layered            # clickable, still never activates
            card.set_pinned(False)
            assert style["ex"] == noactivate | layered | uf._WS_EX_TRANSPARENT
        assert set_long.call_count == 2 and all(c.args[1] == uf._GWL_EXSTYLE for c in set_long.call_args_list)
        set_flags.assert_not_called()                             # would re-create / re-show the window
        assert card.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    finally:
        card.hide()
        card.deleteLater()


def test_a_press_on_the_pinned_card_is_consumed_and_closes_it(q_app):
    card = _card()
    seen = []
    card.dismiss_requested.connect(lambda: seen.append(1))
    try:
        event = MagicMock()
        card.mousePressEvent(event)                               # not pinned: nothing (and never sent)
        assert seen == []
        with patch("netspeedtray.views.usage_flyout._set_click_through"):
            card.set_pinned(True)
        event = MagicMock()
        card.mousePressEvent(event)
        event.accept.assert_called_once()                         # consumed, not passed through
        assert seen == [1]
    finally:
        card.deleteLater()


def test_re_placing_after_pinning_keeps_the_screen_bounds(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    screen = MagicMock()
    screen.geometry.return_value = QRect(0, 0, 640, 360)
    card = _card()
    try:
        with patch.object(UsageFlyout, "placement", wraps=UsageFlyout.placement) as place, \
                patch("netspeedtray.views.usage_flyout._set_click_through"), \
                patch.object(card, "windowHandle", return_value=None):
            card.show_for(QRect(300, 300, 48, 56), QRect(0, 0, 592, 360), screen=screen)
            card.set_pinned(True)
            card.reanchor(QRect(300, 200, 48, 56))
        assert [c.kwargs.get("bounds") for c in place.call_args_list] == [QRect(0, 0, 640, 360)] * 3
    finally:
        card.hide()
        card.deleteLater()


def test_pinned_card_usage_rows_update_in_place(q_app):
    card = _card(cap=(10.0, 100.0, 10.0))
    try:
        before = card._usage_labels[0][0].text()
        card.update_usage((5e9, 6e9), (7e9, 8e9), (55.0, 100.0, 55.0))
        assert card._usage_labels[0][0].text() != before
        assert "6" in card._usage_labels[0][0].text()             # today ↓ 6.0 GB
        assert card._cap_labels[2].value() == 55
    finally:
        card.deleteLater()


# --- the review's later items ---------------------------------------------------------------------

def test_a_double_click_whose_release_was_lost_cannot_eat_the_next_click(w):
    w.handler.handle_mouse_press(_ev())
    w.handler.handle_mouse_release(_ev())
    w.handler.handle_double_click(_ev())                          # ... and its release never arrives
    _click(w)
    assert w.handler._click_timer.isActive()                      # the next click counts


def test_closing_the_card_cancels_a_pending_click_to_pin(w):
    _click(w)
    assert w.handler._click_timer.isActive()
    w.dismiss_hover_card()                                        # e.g. the menu opened meanwhile
    assert not w.handler._click_timer.isActive()


def test_a_theme_change_closes_the_card():
    fake = types.SimpleNamespace(dismiss_hover_card=MagicMock(), theme_manager=MagicMock())
    NSW._on_theme_changed(fake)
    fake.dismiss_hover_card.assert_called_once()
    fake.theme_manager.on_theme_changed.assert_called_once()


@pytest.mark.parametrize("pinned", [True, False])
def test_a_pinned_card_follows_the_widget_when_it_moves(pinned):
    card = MagicMock()
    fake = types.SimpleNamespace(_hover_card=card, _hover_card_pinned=pinned, logger=MagicMock(),
                                 frameGeometry=lambda: QRect(1500, 1032, 85, 48))
    NSW._reanchor_hover_card(fake)
    if pinned:
        card.reanchor.assert_called_once_with(QRect(1500, 1032, 85, 48))
    else:
        card.reanchor.assert_not_called()


def test_a_pinned_card_refreshes_its_usage_at_the_hardware_cadence():
    card = MagicMock()
    fake = types.SimpleNamespace(
        _hover_card=card, _hover_card_pinned=True, config={}, logger=MagicMock(),
        _HOVER_HW_REFRESH_SEC=NSW._HOVER_HW_REFRESH_SEC, _hover_usage_refreshed=0.0,
        _hover_usage_totals=lambda: ((1.0, 2.0), (3.0, 4.0)), _hover_cap_info=lambda: None)
    NSW._refresh_pinned_usage(fake)
    NSW._refresh_pinned_usage(fake)                              # same tick: throttled
    card.update_usage.assert_called_once_with((1.0, 2.0), (3.0, 4.0), None)
    fake._hover_card_pinned = False
    fake._hover_usage_refreshed = 0.0
    NSW._refresh_pinned_usage(fake)
    assert card.update_usage.call_count == 1                     # a hover card is not refreshed


def test_every_way_the_menu_opens_closes_the_card():
    from netspeedtray.core.tray_manager import TrayIconManager

    class W(QWidget):
        def __init__(self):
            super().__init__()
            self.config = {}
            self.dismiss_hover_card = MagicMock()

    w = W()
    m = TrayIconManager(w, EN)
    m._load_and_set_icon = lambda: None
    m.initialize()
    m.context_menu.aboutToShow.emit()                             # e.g. opened without show_context_menu
    w.dismiss_hover_card.assert_called_once()


# --- a failed style call is not silent ------------------------------------------------------------

@pytest.mark.parametrize("failure", ["raises", "ignored"])
def test_a_failed_click_through_change_warns_once_per_direction(q_app, caplog, failure):
    """_set_click_through used to swallow every failure: a pinned card that stayed click-through
    (or a hover card that caught the pointer) left nothing in the log. Now it warns - once per
    direction, since the card is rebuilt on every hover and a failing call fails every time."""
    import ctypes
    import logging
    from netspeedtray.views import usage_flyout as uf
    user32 = ctypes.windll.user32
    card = _card()
    style = {"ex": 0x08000000 | 0x00080000}
    if failure == "raises":
        get = patch.object(user32, "GetWindowLongW", side_effect=OSError("access denied"))
    else:                                                          # the call "works", nothing changes
        get = patch.object(user32, "GetWindowLongW", side_effect=lambda h, i: style["ex"])
    try:
        with get, patch.object(user32, "SetWindowLongW", return_value=0), \
                patch.object(uf, "_click_through_warned", set()), \
                caplog.at_level(logging.WARNING, logger=uf.__name__):
            assert uf._set_click_through(card, True) is False
            assert uf._set_click_through(card, True) is False
            style["ex"] |= uf._WS_EX_TRANSPARENT
            assert uf._set_click_through(card, False) is False
            assert uf._set_click_through(card, False) is False
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2, [r.getMessage() for r in warnings]
        assert "could not set" in warnings[0].getMessage()
        assert "could not clear" in warnings[1].getMessage()
    finally:
        card.deleteLater()


def test_a_working_click_through_change_does_not_warn(q_app, caplog):
    import ctypes
    import logging
    from netspeedtray.views import usage_flyout as uf
    user32 = ctypes.windll.user32
    style = {"ex": 0x08000000 | 0x00080000}
    card = _card()
    try:
        with patch.object(user32, "GetWindowLongW", side_effect=lambda h, i: style["ex"]), \
                patch.object(user32, "SetWindowLongW", side_effect=lambda h, i, v: style.update(ex=v)), \
                patch.object(uf, "_click_through_warned", set()), \
                caplog.at_level(logging.WARNING, logger=uf.__name__):
            assert uf._set_click_through(card, True) is True
            assert uf._set_click_through(card, False) is True
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    finally:
        card.deleteLater()
