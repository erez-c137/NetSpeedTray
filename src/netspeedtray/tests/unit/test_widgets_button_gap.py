"""The default spot on a top/bottom bar keeps clear of a Widgets/weather button beside the tray (rc.3).

Found in the VM (NST-Win11-MultiMon, Win11 26H2, LEFT-aligned bar, 100 %, 2026-10-29): Win11 puts the
Widgets button right beside the tray, and the default spot (tray left - #161 gap - width) landed on
its right half - arrows touching the weather text. Live UIA numbers, physical px: WidgetsButton
1606..1758, TrayNotifyWnd from 1770, the 85 px widget at 1677..1762. The default spot now sits left of
the button, WIDGETS_BUTTON_MIN_GAP_PX clear; a centred bar (button at the far left), a user's drag,
a secondary bar and the drag limits are unchanged.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.core.position_manager import PositionCalculator, PositionManager, WindowState

E = constants.taskbar.edge
GAP = constants.layout.WIDGETS_BUTTON_MIN_GAP_PX
TRAY_GAP = constants.layout.TRAY_EDGE_MIN_GAP_PX
W, H = 85, 48                      # the VM widget's size (logical px)
VM_WIDGETS = (1606, 1758)          # WidgetsButton box, physical px at 100 %
VM_TRAY_LEFT = 1770
CENTRED_WIDGETS = (6, 158)         # a centred bar keeps the button at the far left


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


def _bar(edge=E.BOTTOM, dpi=1.0, widgets_extent=VM_WIDGETS, widgets_rect=None, tray=True, thick=48):
    """A Win11 top/bottom bar (no classic task list). Rect spans are LOGICAL px at 100 %, scaled to
    physical by ``dpi`` the way Windows reports them."""
    def p(v):
        return int(round(v * dpi))

    top = 1080 - thick if edge == E.BOTTOM else 0
    tb = MagicMock()
    tb.hwnd, tb.dpi_scale, tb.height = 4242, dpi, p(thick)
    tb.rect = (0, p(top), p(1920), p(top + thick))
    tb.tasklist_rect = None
    tb.toolbars_rect = None
    tb.apps_extent = None
    tb.widgets_extent = (p(widgets_extent[0]), p(top), p(widgets_extent[1]), p(top + thick)) if widgets_extent else None
    tb.widgets_rect = (p(widgets_rect[0]), p(top + 4), p(widgets_rect[1]), p(top + thick - 4)) if widgets_rect else None
    tb.get_edge_position.return_value = edge
    s = MagicMock()
    s.geometry.return_value = QRect(0, 0, 1920, 1080)
    s.availableGeometry.return_value = QRect(0, thick if edge == E.TOP else 0, 1920, 1080 - thick)
    s.name.return_value = "S"
    tb.get_screen.return_value = s
    tb.get_tray_rect.return_value = (p(VM_TRAY_LEFT), p(top), p(1920), p(top + thick)) if tray else None
    return tb


def _x(tb, config=None, width=W):
    cfg = {"tray_offset_x": 0, "tray_offset_y": 3} if config is None else config
    return PositionCalculator().calculate_position(tb, (width, H), cfg).x


@pytest.mark.parametrize("edge", [E.BOTTOM, E.TOP])
def test_left_aligned_bar_default_spot_keeps_clear_of_the_widgets_button(edge):
    x = _x(_bar(edge=edge))
    # Before the fix: 1770 - 8 - 85 = 1677, i.e. on the button (1606..1758), the VM's exact spot.
    assert x + W + GAP <= VM_WIDGETS[0]
    assert x == VM_WIDGETS[0] - GAP - W


@pytest.mark.parametrize("dpi", [1.25, 1.5, 2.0])
def test_gap_is_logical_px_at_every_scale(dpi):
    tb = _bar(dpi=dpi)
    x = _x(tb)
    button_left = tb.widgets_extent[0] / dpi
    assert x + W + GAP <= button_left
    assert button_left - (x + W) < GAP + 1       # snug: no more than the gap (plus rounding)


def test_centred_bar_is_unchanged():
    # The button at the far left is not between the widget and the tray: the tray spot, as before.
    assert _x(_bar(widgets_extent=CENTRED_WIDGETS)) == VM_TRAY_LEFT - TRAY_GAP - W


def test_no_widgets_button_is_unchanged():
    assert _x(_bar(widgets_extent=None)) == VM_TRAY_LEFT - TRAY_GAP - W


def test_falls_back_to_the_200_probe_content_rect():
    # Layout probe not landed yet / disabled: the #200 probe's content rect (VM: 1614..1750) is used.
    x = _x(_bar(widgets_extent=None, widgets_rect=(1614, 1750)))
    assert x == 1614 - GAP - W


def test_the_full_button_box_wins_over_the_content_rect():
    x = _x(_bar(widgets_extent=VM_WIDGETS, widgets_rect=(1614, 1750)))
    assert x == VM_WIDGETS[0] - GAP - W


def test_a_user_drag_offset_is_never_overridden():
    # A drop saves tray_offset_x from the tray; the widget stays exactly there, button or not (#200).
    for off in (50, 120, TRAY_GAP):
        assert _x(_bar(), {"tray_offset_x": off}) == VM_TRAY_LEFT - W - off


@pytest.mark.parametrize("legacy", sorted(constants.config.defaults.LEGACY_DEFAULT_TRAY_OFFSETS_X))
def test_legacy_default_offsets_are_defaults_too(legacy):
    from netspeedtray.core.position_manager import user_placed_horizontal_offset
    assert not user_placed_horizontal_offset({"tray_offset_x": legacy})
    assert _x(_bar(), {"tray_offset_x": legacy}) == VM_WIDGETS[0] - GAP - W


def test_user_placed_horizontal_offset():
    from netspeedtray.core.position_manager import user_placed_horizontal_offset
    assert not user_placed_horizontal_offset({})
    assert not user_placed_horizontal_offset({"tray_offset_x": 0})
    assert not user_placed_horizontal_offset({"tray_offset_x": True})
    assert user_placed_horizontal_offset({"tray_offset_x": 8})
    assert user_placed_horizontal_offset({"tray_offset_x": -20})


def test_secondary_bar_without_tray_is_unchanged():
    # A secondary taskbar has no TrayNotifyWnd (and no Widgets button): the clock reserve rule, as before.
    tb = _bar(tray=False)
    reserve = constants.config.defaults.DEFAULT_SECONDARY_CLOCK_RESERVE_PX
    assert _x(tb) == 1920 - reserve - W


def test_widget_already_clear_of_the_button_is_unchanged():
    # A button that ends well before the default spot (narrow weather, wide gap) changes nothing.
    assert _x(_bar(widgets_extent=(1500, 1600))) == VM_TRAY_LEFT - TRAY_GAP - W


def test_no_room_left_of_the_button_keeps_the_tray_spot():
    # A button reaching the screen's left edge can't be stepped past: the old spot, never off-screen.
    assert _x(_bar(widgets_extent=(40, 1758))) == VM_TRAY_LEFT - TRAY_GAP - W


def test_drag_limits_are_unchanged():
    # The user may still drag right up to the tray gap (onto the button: their call, #200).
    tb = _bar()
    d = PositionCalculator().constrain_drag_position(QPoint(1900, 0), tb, QSize(W, H))
    assert d.x() == VM_TRAY_LEFT - W - TRAY_GAP


def test_the_app_yield_judges_the_stepped_spot():
    # #24 (#438/#460): a hidden widget is judged where it WOULD go - the spot left of the button.
    tb = _bar()
    widget = MagicMock()
    widget.width.return_value, widget.height.return_value = W, H
    pm = PositionManager(WindowState(config={"tray_offset_x": 0, "tray_offset_y": 3}, widget=widget,
                                     taskbar_info=tb))
    assert pm._would_be_x(tb, -1.0) == VM_WIDGETS[0] - GAP - W


@pytest.mark.parametrize("offset,fallback,expect", [
    (0, False, False),     # the default spot: clear by construction - a probe mismatch never nudges
    (10, False, False),    # a pre-1.3.0 default is the default too
    (0, True, True),       # the full-bar fallback sits on the button's padding (rc.2's spot): nudge
    (50, False, True),     # a user's drag onto the button: nudge, as #200 always did
])
def test_the_200_nudge_on_a_top_bottom_bar(offset, fallback, expect):
    """rc.3 VM: a tray icon appearing slid the Widgets button under the widget for one #200 probe while
    the layout probe still held the old box - a false, never-again notice for a default spot."""
    import types
    from unittest.mock import patch
    from netspeedtray.utils import taskbar_utils
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    pm = MagicMock()
    pm.is_widgets_fallback_active.return_value = fallback
    fake = types.SimpleNamespace(
        _widgets_nudge_shown=False, logger=MagicMock(), config={"tray_offset_x": offset},
        update_config=MagicMock(), position_manager=pm, i18n=MagicMock(),
        x=lambda: 1481, y=lambda: 1032, width=lambda: 85, height=lambda: 48)
    tb = types.SimpleNamespace(widgets_rect=(1538, 1036, 1674, 1076), dpi_scale=1.0,
                               get_edge_position=lambda: E.BOTTOM)
    with patch("netspeedtray.views.widget.main.QTimer.singleShot"), \
            patch.object(taskbar_utils, "set_widgets_probe_enabled"):
        NetworkSpeedWidget._maybe_nudge_widgets_overlap(fake, tb)
    assert fake._widgets_nudge_shown is expect


def test_the_200_cache_never_answers_for_another_taskbar(monkeypatch):
    # An Explorer restart brings a new taskbar HWND, often with a new layout (VM: left -> centred). The
    # old bar's cached rect must not place the widget on the new bar for the cache's 30 s.
    import time
    from netspeedtray.utils import taskbar_utils as tu
    monkeypatch.setattr(tu, "_widgets_state", {"rect": None, "ts": 0.0, "querying": False, "hwnd": 0})
    monkeypatch.setattr(tu, "_widgets_probe_enabled", True, raising=False)
    monkeypatch.setattr(tu, "_widgets_button_enabled", lambda: True, raising=False)
    rects = {0x1: (1574, 1032, 1726, 1080), 0x2: (6, 1032, 158, 1080)}
    monkeypatch.setattr("netspeedtray.utils.uia_widgets.query_widgets_rect", lambda hwnd: rects[hwnd])

    def settle():
        end = time.monotonic() + 3.0
        while time.monotonic() < end:
            with tu._widgets_lock:
                if not tu._widgets_state["querying"] and tu._widgets_state["ts"] > 0:
                    return
            time.sleep(0.01)
        raise AssertionError("refresh never completed")

    tu.get_widgets_rect(0x1)
    settle()
    assert tu.get_widgets_rect(0x1) == rects[0x1]
    assert tu.get_widgets_rect(0x2) is None        # not the old bar's rect, even within the TTL
    settle()
    assert tu.get_widgets_rect(0x2) == rects[0x2]


@pytest.mark.parametrize("dropped_at", [10, 0])
def test_a_drop_on_a_default_offset_still_sticks(dropped_at):
    """rc.3 review: a drag dropped 2 px left of the drag limit saves tray_offset_x 10 - a pre-1.3.0
    default, which user_placed_horizontal_offset reads as "not placed" - and the next reposition then
    stepped the widget ~160 px left of the Widgets button, away from where the user let go. The drop
    saves the next offset instead (1 px, invisible), so the drag sticks."""
    import logging
    from unittest.mock import patch
    from netspeedtray.core.input_handler import InputHandler
    from netspeedtray.core.position_manager import user_placed_horizontal_offset
    widget = MagicMock()
    widget.config = {"tray_offset_x": 0}
    widget.pos.return_value = QPoint(VM_TRAY_LEFT - dropped_at - W, 1032)
    widget.width.return_value, widget.height.return_value = W, H
    pm = MagicMock()
    pm.is_floating.return_value = False
    handler = InputHandler.__new__(InputHandler)
    handler.widget, handler.position_manager = widget, pm
    handler.logger = logging.getLogger("test")
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=_bar()):
        handler._save_dragged_position()
    saved = widget.update_config.call_args[0][0]["tray_offset_x"]
    assert abs(saved - dropped_at) <= 2
    assert user_placed_horizontal_offset({"tray_offset_x": saved})
    x = _x(_bar(), {"tray_offset_x": saved, "tray_offset_y": 3})
    assert abs(x - (VM_TRAY_LEFT - dropped_at - W)) <= TRAY_GAP   # where it was let go (#161 floor aside)
