"""Settings > Widget > "Dock at the left end of the taskbar" (`pin_left_edge`).

The reworked pin (maintainer spec on PR #554; second- and third-round reviews on #604): anchor the
widget to the FIRST shell thing on the LEFT of a top/bottom taskbar - the Widgets/weather button's
right edge when one is there, else the bar's left edge - and keep LEFT_ANCHOR_GAP_PX off it (never
abut a shell button; its own constant, so tuning the tray side doesn't move this one). When the
bar's left end is not free - a Win10-style bar with its task list there, a left-aligned Win11 bar,
or a mirrored (RTL) bar with the clock/tray at the left end - the pin falls back to the normal
tray spot; on a side bar it does not apply at all (the setting is greyed out there, with a reason
said on a visible line under the card as well, as is Free Move while it is on: the pin steps
aside while the widget floats).

Enforced in PositionManager._apply_geometry - the single funnel every placement path ends in
(saved free-move spot, free-float, calculated, substitute-tray) - so it works at any resolution
(the anchor is re-derived from the CURRENT bar layout on each placement) and outranks a dragged
spot while it applies. A drag while pinned adjusts `left_anchor_drag_dx` (from the anchor, not
tray_offset_x), clamped to the TRAY edge (the drag's own right limit) on save and on use; Reset
position clears it. While the Widgets button is enabled but its span is unknown (probe pending),
the bar's screen's LAST GOOD anchor stands instead of the bar edge - keyed by SCREEN, not HWND,
so an Explorer restart keeps it, and dropped on a DPI / geometry change (PR #604 third-round
review). Burial recovery has NO pinned special case: the plain #327 seat serves a pinned widget
too (a bare HWND_TOPMOST re-assert is the #200 bug), and the regular placement pass re-places
the widget at its anchor. Event-driven only - no timers (#200).
"""
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import win32con

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QWidget

from netspeedtray import constants
from netspeedtray.core import position_manager as pm_mod
from netspeedtray.core.app_overlap_yield import BUSY, NO_TASKBAR, OverlapContext
from netspeedtray.core.input_handler import InputHandler
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.utils import taskbar_utils
from netspeedtray.views.settings.pages.widget import WidgetPage

GAP = constants.layout.LEFT_ANCHOR_GAP_PX
TGAP = constants.layout.TRAY_EDGE_MIN_GAP_PX
BOTTOM = constants.taskbar.edge.BOTTOM
LEFT = constants.taskbar.edge.LEFT
TB = 0xABC          # the taskbar (Shell_TrayWnd)
W = 0x1000          # our widget
MENU = 0x2000       # an open taskbar context menu (above the taskbar)
TIP = 0x2001        # a hidden taskbar tooltip
APP = 0x3000        # an ordinary application window
EXPECTED_ABOVE_FLAGS = (win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                        | 0x0200)  # SWP_NOOWNERZORDER - the #327 seat above the taskbar


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


def _screen(left: int, width: int, height: int = 1080) -> MagicMock:
    """A QScreen stand-in with a real geometry so validate/screenAt-style logic can run."""
    scr = MagicMock()
    scr.geometry.return_value = QRect(left, 0, width, height)
    scr.availableGeometry.return_value = QRect(left, 0, width, height - 40)
    scr.name.return_value = f"\\\\_DISPLAY_{left}"
    return scr


def _taskbar(screen: MagicMock = None, *, edge= BOTTOM, hwnd: int = 5151, dpi: float = 1.0,
             tasklist=None, widgets_extent=None, tray=(1790, 1040, 1920, 1080)) -> MagicMock:
    """A TaskbarInfo stand-in with the fields the pin / yield / placement logic reads."""
    info = MagicMock()
    info.hwnd = hwnd
    info.dpi_scale = dpi
    info.is_primary = True
    info.get_edge_position.return_value = edge
    info.get_screen.return_value = screen
    info.get_tray_rect.return_value = tray
    info.tasklist_rect = tasklist
    info.widgets_extent = widgets_extent
    info.widgets_rect = None
    info.rect = (0, 1032, 1920, 1080)
    info.screen_name = screen.name() if screen is not None else "\\\\_DISPLAY_0"
    info.screen_geometry = (0, 0, 1920, 1080)
    info.toolbars_rect = None
    return info


def _manager(config: dict, screen: MagicMock, taskbar: MagicMock = None) -> tuple:
    """A PositionManager over a MagicMock widget. The taskbar stand-in is the state's TaskbarInfo;
    QApplication.screenAt is patched per-test to the given screen (the pin resolves the target
    screen from the incoming position)."""
    widget = MagicMock()
    widget.width.return_value = 85
    widget.height.return_value = 48
    widget.pos.return_value = QPoint(0, 0)
    widget.screen.return_value = screen
    widget.winId.return_value = W
    widget.isVisible.return_value = True
    info = taskbar if taskbar is not None else _taskbar(screen)
    pm = PositionManager(WindowState(config=dict(config), widget=widget, taskbar_info=info))
    pm._last_applied_geometry = None
    return pm, widget


def _pin_alignment(monkeypatch, align):
    """Pin the registry TaskbarAl read (1 = centred Win11 default, 0 = left, None = unreadable)."""
    monkeypatch.setattr(PositionManager, "_read_taskbar_alignment", staticmethod(lambda: align))


# --- the pin in _apply_geometry: the anchor ---------------------------------------------------

def test_pin_anchors_to_the_widgets_button_right_edge(monkeypatch):
    """The first shell thing on the left IS the Widgets/weather button (a centred Win11 bar keeps
    it at the far left): the widget sits LEFT_ANCHOR_GAP_PX off its RIGHT edge."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, widgets_extent=(6, 1032, 158, 1080))
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(158 + GAP, 600)


def test_pin_anchors_to_the_bar_left_edge_without_a_widgets_button(monkeypatch):
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, widget = _manager({"pin_left_edge": True}, scr)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(scr.geometry().left() + GAP, 600)


def test_pin_ignores_a_widgets_button_at_the_tray_end(monkeypatch):
    """The button beside the tray (a left-aligned bar puts it there, or the probe disagrees with
    the alignment) belongs to the tray end - the left end is not free, so the pin does not apply
    and the placement path's own spot (the normal tray spot) stands."""
    _pin_alignment(monkeypatch, None)   # registry unreadable: the button's side decides
    scr = _screen(0, 1920)
    tb = _taskbar(scr, widgets_extent=(1530, 1032, 1690, 1080))
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)


def test_pin_falls_back_to_the_tray_spot_when_the_bar_is_left_aligned(monkeypatch):
    """A left-aligned Win11 bar has Start + apps right at the left end: the pin is ignored."""
    _pin_alignment(monkeypatch, 0)
    scr = _screen(0, 1920)
    pm, widget = _manager({"pin_left_edge": True}, scr)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)


def test_pin_falls_back_on_a_win10_style_bar(monkeypatch):
    """A real task list at the left end (Win10 / ExplorerPatcher): the left end is not free."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, tasklist=(40, 1032, 900, 1080))
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)


def test_pin_is_ignored_on_a_side_bar(monkeypatch):
    """Side bars stay out of scope (the setting is greyed out there): no anchor, no override."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, edge=LEFT)
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)


# --- the last good anchor while the Widgets probe is pending (2.2.2 review, should-fix 4) -------

def test_probe_pending_keeps_the_last_good_anchor(monkeypatch):
    """Widgets on but the button's span unknown (startup, an Explorer restart: the probe is
    pending) - the widget keeps the LAST GOOD anchor for this bar instead of flashing onto the
    weather button from the bar edge until the probe lands."""
    _pin_alignment(monkeypatch, 1)
    monkeypatch.setattr(taskbar_utils, "widgets_button_enabled", lambda: True)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, widgets_extent=(6, 1032, 158, 1080))
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(158 + GAP, 600)
    tb.widgets_extent = None   # the bar re-laid: no button box until the probe answers
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_with(158 + GAP, 600)   # still at the anchor - NOT on the button


def test_probe_pending_without_a_last_good_anchor_uses_the_bar_edge(monkeypatch):
    """Nothing seen yet (first placement, probe still pending): the bar edge stands - there is no
    better spot to keep."""
    _pin_alignment(monkeypatch, 1)
    monkeypatch.setattr(taskbar_utils, "widgets_button_enabled", lambda: True)
    scr = _screen(0, 1920)
    pm, widget = _manager({"pin_left_edge": True}, scr)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(scr.geometry().left() + GAP, 600)


def test_probe_pending_keeps_the_anchor_across_an_explorer_restart(monkeypatch):
    """The last good anchor is keyed by the bar's SCREEN, not the taskbar HWND (PR #604
    third-round review): an Explorer restart's fresh HWND on the same screen still hits - that
    restart is exactly when the anchor is needed, and the old HWND key lost it (the widget
    flashed onto the weather button from the bar edge until the fresh bar's probe answered)."""
    _pin_alignment(monkeypatch, 1)
    monkeypatch.setattr(taskbar_utils, "widgets_button_enabled", lambda: True)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, hwnd=111, widgets_extent=(6, 1032, 158, 1080))
    pm, _widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    assert pm.left_anchor_spot_x(tb) == 158 + GAP    # caches (screen, 158)
    tb2 = _taskbar(scr, hwnd=222)                    # the restart's fresh bar, no button known
    assert pm.left_anchor_spot_x(tb2) == 158 + GAP   # the screen's last good anchor stands


def test_probe_pending_does_not_cross_screens(monkeypatch):
    """A bar on ANOTHER screen must not inherit the first screen's anchor: the key is the
    screen's device name."""
    _pin_alignment(monkeypatch, 1)
    monkeypatch.setattr(taskbar_utils, "widgets_button_enabled", lambda: True)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, hwnd=111, widgets_extent=(6, 1032, 158, 1080))
    pm, _widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    assert pm.left_anchor_spot_x(tb) == 158 + GAP    # caches (screen, 158)
    other = _screen(1920, 2560)                      # a different screen -> a different key
    tb2 = _taskbar(other, hwnd=222, tray=(4350, 1040, 4480, 1080))   # its tray at its right end
    assert pm.left_anchor_spot_x(tb2, screen=other) == 1920 + GAP


def test_last_good_anchor_drops_on_a_dpi_change(monkeypatch):
    """A display-scale change invalidates the cached LOGICAL x: the anchor is dropped and the
    bar edge stands until the re-scaled bar's probe answers again (PR #604 third-round review)."""
    _pin_alignment(monkeypatch, 1)
    monkeypatch.setattr(taskbar_utils, "widgets_button_enabled", lambda: True)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, hwnd=111, widgets_extent=(6, 1032, 158, 1080))
    pm, _widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    assert pm.left_anchor_spot_x(tb) == 158 + GAP
    tb2 = _taskbar(scr, hwnd=111, dpi=1.5)           # the same bar after a scale change
    assert pm.left_anchor_spot_x(tb2) == scr.geometry().left() + GAP


def test_last_good_anchor_drops_on_a_geometry_change(monkeypatch):
    """A resolution / arrangement change invalidates the cached anchor the same way."""
    _pin_alignment(monkeypatch, 1)
    monkeypatch.setattr(taskbar_utils, "widgets_button_enabled", lambda: True)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, hwnd=111, widgets_extent=(6, 1032, 158, 1080))
    pm, _widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    assert pm.left_anchor_spot_x(tb) == 158 + GAP
    scr.geometry.return_value = QRect(0, 0, 2560, 1080)   # the resolution changed
    tb2 = _taskbar(scr, hwnd=111)
    assert pm.left_anchor_spot_x(tb2) == GAP


def test_pin_adds_the_drag_offset_from_the_anchor(monkeypatch):
    """A pinned drag stores `left_anchor_drag_dx` (from the anchor, not tray_offset_x); the
    placement adds it to the anchored spot."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, widgets_extent=(6, 1032, 158, 1080))
    pm, widget = _manager({"pin_left_edge": True, "left_anchor_drag_dx": 40}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(158 + GAP + 40, 600)


def test_drag_offset_is_clamped_to_the_tray_limit(monkeypatch):
    """A big offset (saved on a 2560 px screen) cannot push the widget past the TRAY: the right
    limit is the same one the drag uses - the tray's left edge minus the widget width and
    TRAY_EDGE_MIN_GAP_PX - not the screen edge, so an offset saved near the tray cannot land on
    the clock after a monitor change or when the tray grows (a new tray icon, an IME indicator)
    (PR #604 third-round review). Off-screen the recovery could not clear it either: the offset
    is measured FROM the anchor."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, widgets_extent=(6, 1032, 158, 1080))   # the tray at (1790..1920)
    pm, widget = _manager({"pin_left_edge": True, "left_anchor_drag_dx": 4000}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1790 - 85 - TGAP, 600)   # 8 px clear of the tray's edge


def test_drag_offset_clamp_falls_back_to_the_screen_edge_without_a_tray(monkeypatch):
    """A mirrored SECONDARY bar has no tray to check (a known limit, per the review): the bar's
    screen edge stands as the right limit there."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, widgets_extent=(6, 1032, 158, 1080), tray=None)
    pm, widget = _manager({"pin_left_edge": True, "left_anchor_drag_dx": 4000}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1920 - 85, 600)   # right edge flush with the screen end


def test_pin_follows_the_widgets_button_width(monkeypatch):
    """The weather text grows: the anchor is re-derived from the CURRENT button rect on every
    placement pass, so the widget follows the button's right edge."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, widgets_extent=(6, 1032, 158, 1080))
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(158 + GAP, 600)
    tb.widgets_extent = (6, 1032, 208, 1080)   # "Mostly cloudy" -> longer text
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_with(208 + GAP, 600)


def test_pin_repins_when_the_resolution_changes(monkeypatch):
    """Same toggle, different resolution / a secondary screen: the anchor is re-derived from the
    CURRENT screen geometry, never from a stored pixel value."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(1920, 2560, 1440)
    tb = _taskbar(scr, tray=(4350, 1392, 4480, 1440))   # a tray rect on THIS screen's right end
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(4000, 300)
    widget.move.assert_called_once_with(1920 + GAP, 300)


def test_pin_resolves_the_screen_from_the_unclamped_probe(monkeypatch):
    """A monitor arranged fully ABOVE the primary has a negative y-range; the probe must not
    clamp y to 0 (it would land in the primary and re-pin the widget to the wrong screen's
    bar, flapping invisible every refresh tick)."""
    _pin_alignment(monkeypatch, 1)
    above = _screen(960, 3840, 2160)
    above.geometry.return_value = QRect(960, -2160, 3840, 2160)
    above.availableGeometry.return_value = QRect(960, -2160, 3840, 2120)
    primary = _screen(0, 1920)
    screens = [above, primary]

    def fake_screen_at(point):
        for scr in screens:
            g = scr.geometry.return_value
            if g.left() <= point.x() < g.right() and g.top() <= point.y() < g.bottom():
                return scr
        return None

    tb = _taskbar(above, tray=(4200, -2112, 4700, -2080))   # this screen's tray is at its right end
    pm, widget = _manager({"pin_left_edge": True}, above, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt",
               side_effect=fake_screen_at):
        pm._apply_geometry(1000, -2100)   # the widget sits on the upper display
    widget.move.assert_called_once_with(960 + GAP, -2100)


def test_pin_steps_aside_while_free_move_is_on(monkeypatch):
    """Free Move on: the pin does NOT apply - the saved free-move spot stands (x AND y). The old
    behaviour let the pin win over x while the saved y stayed, leaving the widget floating
    mid-screen at the anchor's x (2.2.2 review)."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    cfg = {"pin_left_edge": True, "free_move": True, "position_x": 1700, "position_y": 990}
    pm, widget = _manager(cfg, scr)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        assert pm._apply_saved_position() is True
    widget.move.assert_called_once_with(1700, 990)


def test_left_anchor_spot_x_is_none_while_floating(monkeypatch):
    """The pin's spot is None whenever the widget floats - Free Move or a free-float (#188): the
    drag path, the yield and the probe refresh all read that and stand aside with it. Turn Free
    Move off and the pin applies again."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True, "free_move": True}, scr)
    assert pm.left_anchor_spot_x() is None
    pm._state.config["free_move"] = False
    assert pm.left_anchor_spot_x() is not None
    pm._free_float_active = True   # the #188 runtime float floats the same way
    assert pm.left_anchor_spot_x() is None


# --- mirrored (RTL) taskbars: the tray in the LEFT half owns the left end ------------------------

def test_pin_falls_back_when_the_tray_sits_in_the_left_half(monkeypatch):
    """A mirrored (right-to-left Windows) taskbar keeps the clock at the LEFT end - with Widgets
    off, the tray rect's centre in the bar's left half is the only tell that the left end is the
    tray end: the pin does not apply (it would land the widget on the tray)."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, tray=(60, 1040, 190, 1080), widgets_extent=None)
    assert pm_mod.left_end_is_free(tb, scr, 1) == (False, None)
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)


def test_left_end_is_free_with_a_tray_on_the_right_half():
    """The normal (LTR) bar: the clock sits in the RIGHT half, so the left end stays free - the
    tray test mirrors the Widgets-button one instead of replacing it."""
    scr = _screen(0, 1920)
    tb = _taskbar(scr)   # the default stand-in's tray is at (1790..1920), the right end
    assert pm_mod.left_end_is_free(tb, scr, 1) == (True, None)


def test_pin_off_leaves_every_path_untouched():
    pm, widget = _manager({}, _screen(0, 1920))
    with patch("netspeedtray.core.position_manager.QApplication.screenAt",
               return_value=_screen(0, 1920)) as screen_at:
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)
    screen_at.assert_not_called()


def test_no_real_taskbar_leaves_the_pin_inert(monkeypatch):
    """The fallback TaskbarInfo (hwnd 0, Explorer restarting) has no bar to anchor to: the pin
    must not invent one from the screen alone."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, hwnd=0)
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)


# --- the pin's probe refresh (a placement pass re-derives the anchor from the new rect) --------

def _stub_probe_plumbing(monkeypatch, snapshot, background, slot, waits):
    monkeypatch.setattr(taskbar_utils, "request_background_layout_probe",
                        lambda h: background.append(h))
    monkeypatch.setattr(taskbar_utils, "request_taskbar_layout_probe",
                        lambda h, keep_last=False: slot.append(h))
    monkeypatch.setattr(taskbar_utils, "wait_for_taskbar_layout",
                        lambda h, s: waits.append((h, s)))
    monkeypatch.setattr(taskbar_utils, "widgets_toggle_generation", lambda: (0, None))
    monkeypatch.setattr(taskbar_utils, "dock_signature", lambda info: ("b", 48, 1.0, False))
    monkeypatch.setattr(taskbar_utils, "taskbar_layout_snapshot", lambda h: snapshot)


def test_left_anchor_refresh_first_sight_requests_a_slot_probe(monkeypatch):
    """First sight of the bar (startup, a fresh HWND after TaskbarCreated): a one-shot slot probe
    with a short bounded wait, so the FIRST pinned placement anchors off the real button."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True}, scr)
    background, slot, waits = [], [], []
    _stub_probe_plumbing(monkeypatch, (None, None, False), background, slot, waits)
    hwnd = pm._state.taskbar_info.hwnd
    pm._refresh_left_anchor_layout(pm._state.taskbar_info)
    assert slot == [hwnd] and waits == [(hwnd, 0.25)] and background == []


def test_left_anchor_refresh_known_layout_uses_the_background_queue(monkeypatch):
    """With a layout already known, the refresh goes through the background queue (never the
    one-taskbar slot - the yield and the side-bar placement own it), rate-limited to one request
    per window."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True}, scr)
    background, slot, waits = [], [], []
    _stub_probe_plumbing(monkeypatch, ((0, 1032, 900, 1080), (6, 1032, 158, 1080), False),
                         background, slot, waits)
    hwnd = pm._state.taskbar_info.hwnd
    pm._refresh_left_anchor_layout(pm._state.taskbar_info)
    assert background == [hwnd] and slot == [] and waits == []
    pm._refresh_left_anchor_layout(pm._state.taskbar_info)
    assert background == [hwnd]   # rate-limited: one request per window


def test_left_anchor_refresh_skips_while_a_probe_is_in_flight(monkeypatch):
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True}, scr)
    background, slot, waits = [], [], []
    _stub_probe_plumbing(monkeypatch, (None, None, True), background, slot, waits)
    pm._refresh_left_anchor_layout(pm._state.taskbar_info)
    assert background == [] and slot == [] and waits == []


def test_left_anchor_refresh_is_off_with_the_pin(monkeypatch):
    pm, _widget = _manager({}, _screen(0, 1920))
    background, slot, waits = [], [], []
    _stub_probe_plumbing(monkeypatch, (None, None, False), background, slot, waits)
    pm._refresh_left_anchor_layout(pm._state.taskbar_info)
    assert background == [] and slot == [] and waits == []


# --- #24 on that side: the yield judges the anchored spot --------------------------------------

def _overlap_info(scr, widgets_extent=(6, 1032, 158, 1080)):
    return _taskbar(scr, widgets_extent=widgets_extent)


def test_pinned_context_judges_the_anchored_spot(monkeypatch):
    """With the pin on, the yield's "widget spot" is the ANCHORED left spot: when the app buttons
    reach it the widget hides, and it comes back when room returns - like the tray side. There is
    no second spot (no stepped / tray_spot pair): the pin has one place to be."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True, "hide_when_apps_cover": True}, scr,
                           taskbar=_overlap_info(scr))
    pm._app_overlap_info = pm._state.taskbar_info
    ctx = pm._app_overlap_context()
    assert isinstance(ctx, OverlapContext)
    assert ctx.widget == (158 + GAP, 158 + GAP + 85)
    assert ctx.stepped is None and ctx.tray_spot is None
    assert ctx.may_hide is True
    assert ctx.drop_key is None and ctx.saved_drop is None


def test_pinned_context_falls_back_when_the_pin_does_not_apply(monkeypatch):
    """Left-aligned bar (the pin falls back to the tray spot): the yield judges the widget where
    the placement put it - not the anchored spot."""
    _pin_alignment(monkeypatch, 0)
    scr = _screen(0, 1920)
    pm, widget = _manager({"pin_left_edge": True, "hide_when_apps_cover": True}, scr)
    widget.pos.return_value = QPoint(1500, 600)
    pm._app_overlap_info = pm._state.taskbar_info
    assert pm._pinned_overlap_context(pm._state.taskbar_info, True, OverlapContext, BUSY) is None
    ctx = pm._app_overlap_context()
    assert isinstance(ctx, OverlapContext)
    assert ctx.widget[0] != 158 + GAP


def test_pinned_widget_yields_and_returns(monkeypatch):
    """Duck + return on the anchored spot, through the REAL yield and the REAL context: the app
    buttons grow over the left end -> the widget hides; they clear again -> two readings later it
    shows."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True, "hide_when_apps_cover": True}, scr,
                           taskbar=_overlap_info(scr))
    pm._app_overlap_info = pm._state.taskbar_info
    pm._app_yield._state.confirm_ms = 0   # the confirm wait is the hysteresis's own tests' business
    changes = []
    pm.on_app_yield_changed = lambda: changes.append(1)

    ctl = types.SimpleNamespace(result=None, workers=[], bar=(0, 1032, 1920, 1080))

    def start():
        ctl.workers.append(taskbar_utils._layout_worker)

    fresh = {"hwnd": 0, "apps": None, "widgets": None, "group": None, "overflow": None,
             "pending": False, "done": False, "gen": 0, "running": False, "started": 0.0,
             "landed": 0}
    taskbar_utils._layout_state.update(fresh)
    try:
        with patch.object(taskbar_utils, "_start_layout_worker", side_effect=start), \
                patch.object(taskbar_utils, "_query_taskbar_layout_blocking",
                             side_effect=lambda _h: ctl.result), \
                patch.object(taskbar_utils, "_taskbar_window_rect",
                             side_effect=lambda _h: ctl.bar):
            def probe(group):
                ctl.result = (group, (6, 1032, 158, 1080), group, None, None, None)
                pm._app_yield._probe_timer.stop()
                pm._app_yield._fire_probe()
                while ctl.workers:
                    ctl.workers.pop(0)()
                pm._app_yield._poll_probe()

            pm._app_yield.sync()     # attach to the pinned context, schedule the first probe
            assert pm._app_yield.attached_hwnd == pm._state.taskbar_info.hwnd
            probe((674, 1032, 1247, 1080))      # a centred group, well right of the anchor
            assert pm._app_yield.yielded is False
            probe((250, 1032, 1500, 1080))      # many apps: the group reaches the left end
            assert pm._app_yield.yielded is True
            assert changes
            probe((674, 1032, 1247, 1080))      # clear once ...
            probe((674, 1032, 1247, 1080))      # ... and a second reading: back
            assert pm._app_yield.yielded is False
    finally:
        pm._app_yield.detach()


def test_pinned_widget_with_hiding_off_never_probes(monkeypatch):
    """'Hide while app buttons cover the widget' off, and the pin APPLIES: the pinned context is
    NOT built - the yield stays detached (and probe-free). It has no stepped / tray-spot pair for
    the full-bar fallback to weigh, so a context would only have made the yield run its probes
    for nothing (2.2.2 review)."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True, "hide_when_apps_cover": False}, scr,
                           taskbar=_overlap_info(scr))
    pm._app_overlap_info = pm._state.taskbar_info
    ctx = pm._app_overlap_context()
    assert not isinstance(ctx, OverlapContext)
    assert "setting is off" in str(ctx)


def test_pinned_fallback_with_hiding_off_still_reaches_the_tray_side_watch(monkeypatch):
    """Pin on but NOT applying (a left-aligned bar here) with 'Hide while app buttons cover' off:
    the old early 'setting is off' return skipped the tray side's watch probe - the not-applying
    fallback now behaves exactly like the normal tray spot (PR #604 third-round review): a
    context (may_hide=False) is built, so the yield stays attached and watches the bar."""
    _pin_alignment(monkeypatch, 0)
    monkeypatch.setattr(taskbar_utils, "widgets_button_enabled", lambda: True)
    scr = _screen(0, 1920)
    pm, widget = _manager({"pin_left_edge": True, "hide_when_apps_cover": False}, scr)
    widget.pos.return_value = QPoint(1500, 600)
    pm._app_overlap_info = pm._state.taskbar_info
    ctx = pm._app_overlap_context()
    assert isinstance(ctx, OverlapContext)
    assert ctx.may_hide is False


# --- dragging while pinned ---------------------------------------------------------------------

class _DragWidget(QWidget):
    def __init__(self, config):
        super().__init__()
        self._dragging = False
        self.config = config
        self.is_paused = False

    def move(self, *args):        # replaced with a MagicMock per test
        pass


def _drag_setup(config: dict) -> tuple:
    w = _DragWidget(dict(config))
    w.move = MagicMock()
    w.setGeometry(100, 100, 85, 48)
    handler = InputHandler(widget=w, position_manager=MagicMock(), tray_manager=MagicMock())
    press = MagicMock(spec=QMouseEvent)
    press.button.return_value = Qt.MouseButton.LeftButton
    press.globalPosition.return_value = QPointF(120.0, 130.0)
    handler.handle_mouse_press(press)
    return w, handler


def _move_event(global_x: float, global_y: float) -> MagicMock:
    event = MagicMock(spec=QMouseEvent)
    event.buttons.return_value = Qt.MouseButton.LeftButton
    event.button.return_value = Qt.MouseButton.LeftButton
    event.globalPosition.return_value = QPointF(global_x, global_y)
    return event


def test_dragging_works_while_pinned():
    """The pin no longer blocks dragging: the drag slides the widget along the bar FROM the
    anchor (PositionManager.constrain_drag keeps it right of anchor + gap)."""
    w, handler = _drag_setup({"pin_left_edge": True})
    handler.position_manager.constrain_drag.side_effect = lambda p: p
    handler.handle_mouse_move(_move_event(400, 300))
    assert handler._is_dragging is True
    w.move.assert_called()


def test_dragging_works_when_the_pin_is_off():
    w, handler = _drag_setup({})
    handler.position_manager.constrain_drag.side_effect = lambda p: p
    handler.handle_mouse_move(_move_event(400, 300))
    assert handler._is_dragging is True
    w.move.assert_called()


def test_pinned_drag_saves_left_anchor_drag_dx():
    """The drop measures FROM the anchor (not tray_offset_x): dx = drop_x - anchor spot."""
    w = _DragWidget({"pin_left_edge": True})
    w.pos = MagicMock(return_value=QPoint(160, 600))
    w.update_config = MagicMock()
    pm = MagicMock()
    pm.is_floating.return_value = False
    pm.left_anchor_spot_x.return_value = 100   # anchor 92 + gap 8
    handler = InputHandler(widget=w, position_manager=pm, tray_manager=MagicMock())
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info",
               return_value=_taskbar(_screen(0, 1920))):
        handler._save_dragged_position()
    w.update_config.assert_called_once_with({"left_anchor_drag_dx": 60})


def test_pinned_drag_clamps_the_offset_to_zero():
    """A drop left of the anchored spot (a lost drag) snaps the offset to 0: the widget never
    ends up on the shell button it is anchored off."""
    w = _DragWidget({"pin_left_edge": True})
    w.pos = MagicMock(return_value=QPoint(50, 600))
    w.update_config = MagicMock()
    pm = MagicMock()
    pm.is_floating.return_value = False
    pm.left_anchor_spot_x.return_value = 100
    handler = InputHandler(widget=w, position_manager=pm, tray_manager=MagicMock())
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info",
               return_value=_taskbar(_screen(0, 1920))):
        handler._save_dragged_position()
    w.update_config.assert_called_once_with({"left_anchor_drag_dx": 0})


def test_pinned_drag_save_clamps_the_offset_to_the_bar():
    """A drop past the bar's end (a lost drag, or a race with a resolution change) saves a CLAMPED
    offset - to the TRAY limit, the same right limit the drag uses: tray left - width -
    TRAY_EDGE_MIN_GAP_PX - so an offset saved near the tray cannot land on the clock after a
    monitor change or when the tray grows (PR #604 third-round review)."""
    w = _DragWidget({"pin_left_edge": True})
    w.pos = MagicMock(return_value=QPoint(5000, 600))
    w.width = MagicMock(return_value=85)
    w.update_config = MagicMock()
    pm = MagicMock()
    pm.is_floating.return_value = False
    pm.left_anchor_spot_x.return_value = 100
    handler = InputHandler(widget=w, position_manager=pm, tray_manager=MagicMock())
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info",
               return_value=_taskbar(_screen(0, 1920))):
        handler._save_dragged_position()
    # anchor 100, widget 85 wide: dx may reach 1790 (tray left) - 85 - 8 (tray gap) - 100, not 4900.
    w.update_config.assert_called_once_with({"left_anchor_drag_dx": 1790 - 85 - TGAP - 100})


def test_pinned_fallback_drag_still_saves_tray_offset_x():
    """Pin on but not applying (left-aligned / Win10 fallback, side bars): the widget sits at the
    normal tray spot, so the drop saves tray_offset_x as before."""
    w = _DragWidget({"pin_left_edge": True})
    w.pos = MagicMock(return_value=QPoint(1400, 600))
    w.update_config = MagicMock()
    pm = MagicMock()
    pm.is_floating.return_value = False
    pm.left_anchor_spot_x.return_value = None   # the pin does not apply to this bar
    handler = InputHandler(widget=w, position_manager=pm, tray_manager=MagicMock())
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info",
               return_value=_taskbar(_screen(0, 1920))):
        handler._save_dragged_position()
    updates = w.update_config.call_args[0][0]
    assert "tray_offset_x" in updates and "left_anchor_drag_dx" not in updates


def test_unpinned_drag_still_saves_tray_offset_x():
    w = _DragWidget({})
    w.pos = MagicMock(return_value=QPoint(1400, 600))
    w.width = MagicMock(return_value=85)
    w.update_config = MagicMock()
    pm = MagicMock()
    pm.is_floating.return_value = False
    handler = InputHandler(widget=w, position_manager=pm, tray_manager=MagicMock())
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info",
               return_value=_taskbar(_screen(0, 1920))):
        handler._save_dragged_position()
    updates = w.update_config.call_args[0][0]
    assert updates["tray_offset_x"] == 1790 - 1400 - 85


def test_constrain_drag_keeps_the_pinned_widget_right_of_the_anchor(monkeypatch):
    """The drag floor is anchor + LEFT_ANCHOR_GAP_PX: the offset is clamped >= 0 live."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, widget = _manager({"pin_left_edge": True}, scr)
    with patch.object(pm._calculator, "constrain_drag_position",
                      side_effect=lambda pos, info, size: pos):
        got = pm.constrain_drag(QPoint(5, 600))
    assert got.x() == GAP            # 5 -> the anchored spot (screen left 0 + gap 8)
    assert got.y() == 600
    with patch.object(pm._calculator, "constrain_drag_position",
                      side_effect=lambda pos, info, size: pos):
        got = pm.constrain_drag(QPoint(500, 600))
    assert got.x() == 500            # right of the anchor: unchanged


# --- Reset clears the pinned drag offset -------------------------------------------------------

def test_reset_clears_left_anchor_drag_dx(monkeypatch):
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True, "left_anchor_drag_dx": 40}, scr)
    monkeypatch.setattr(pm_mod, "get_taskbar_info", lambda *a, **k: _taskbar(scr))
    pm.reset_to_default()
    assert pm._state.config["left_anchor_drag_dx"] == constants.config.defaults.DEFAULT_LEFT_ANCHOR_DRAG_DX
    assert pm._state.config["tray_offset_x"] == constants.config.defaults.DEFAULT_TRAY_OFFSET_X


def test_has_custom_position_counts_a_pinned_drag():
    """Reset position must be OFFERED after a pinned drag (the tray menu reads this) - but only
    while the pin is ON: with the pin off the offset is inert and must not keep offering Reset
    position for a widget that sits where the app put it (2.2.2 review)."""
    defaults = constants.config.defaults
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    widget = NetworkSpeedWidget.__new__(NetworkSpeedWidget)   # only the pure helper is under test
    widget.config = {"pin_left_edge": True, "left_anchor_drag_dx": 40}
    assert widget.has_custom_position() is True
    widget.config = {"pin_left_edge": True, "left_anchor_drag_dx": 0}
    assert widget.has_custom_position() is False
    widget.config = {"pin_left_edge": False, "left_anchor_drag_dx": 40}
    assert widget.has_custom_position() is False


# --- burial recovery (no pinned special case any more, per the 2.2.2 review) ---------------------
#
# The pin-mode branch is GONE: a bare SetWindowPos(HWND_TOPMOST) on a taskbar-owned window is the
# #200 bug (it pulls the owner cluster above open menus / jump lists), and Z-order does not depend
# on x - the #327 seat below is already right for a pinned widget, and the regular placement pass
# re-places it at the anchor. The two pin-mode tests were removed with it: the _ZOrder fake never
# actually reorders, so it could not catch the real bug anyway.

class _ZOrder:
    """A fake top-level Z-order (index 0 is the top); SetWindowPos really reorders it."""
    CLASSES = {TB: "Shell_TrayWnd", MENU: "#32768", TIP: "tooltips_class32",
               W: "Qt6QWindow", APP: "Notepad"}

    def __init__(self, order):
        self.order = list(order)
        self.set_calls = []

    def GetForegroundWindow(self):
        return APP

    def GetWindow(self, hwnd, cmd):
        i = self.order.index(hwnd)
        return self.order[i - 1] if i > 0 else 0

    def GetWindowLong(self, hwnd, index):
        return win32con.WS_EX_TOPMOST

    def GetClassName(self, hwnd):
        return self.CLASSES.get(hwnd, "Other")

    def IsWindow(self, hwnd):
        return hwnd in self.order

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.set_calls.append((hwnd, after, flags))


class _Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def monotonic(self):
        return self.t


@contextmanager
def _burial_env(config):
    """A buried widget (below its taskbar in Z) with the win32 surface patched out."""
    scr = _screen(0, 1920)
    z = _ZOrder([MENU, TIP, TB, W, APP])
    widget = MagicMock()
    widget.winId.return_value = W
    widget.isVisible.return_value = True
    widget.width.return_value = 85
    widget.height.return_value = 48
    widget.screen.return_value = scr
    widget.pos.return_value = QPoint(166, 600)
    pm = PositionManager(WindowState(config=dict(config), widget=widget, taskbar_info=_taskbar(scr)))
    win32 = MagicMock()
    for name in ("GetWindow", "GetWindowLong", "GetClassName", "IsWindow", "SetWindowPos",
                 "GetForegroundWindow"):
        setattr(win32, name, MagicMock(side_effect=getattr(z, name)))
    with patch.object(pm_mod, "win32gui", win32), \
            patch.object(pm_mod, "_GetWindowLongPtr", return_value=TB), \
            patch.object(pm_mod, "_SetWindowLongPtr"), \
            patch.object(pm_mod, "_window_band", side_effect=lambda h: None), \
            patch.object(pm_mod.time, "monotonic", side_effect=_Clock().monotonic), \
            patch.object(PositionManager, "_read_taskbar_alignment", staticmethod(lambda: 1)):
        yield pm, z


def test_burial_recovery_with_the_pin_still_seats_above_the_taskbar():
    """Sanity: the plain #327 recovery serves a PINNED widget too now (2.2.2 review) - inserted
    directly above the taskbar, no bare HWND_TOPMOST re-assert; the regular placement pass puts
    it back at its anchor afterwards."""
    with _burial_env({"pin_left_edge": True}) as (pm, z):
        pm.update_position = MagicMock()
        assert pm.reseat_above_taskbar() is True
        assert z.set_calls == [(W, TIP, EXPECTED_ABOVE_FLAGS)]
        assert pm.update_position.called is False


def test_redock_seats_the_pinned_widget_at_the_anchor():
    """The one-time topmost seat at a (re)dock (owner re-established) lands AT the anchor too:
    a placement pass runs when the pin is on. Only on the (re)dock itself - never periodic."""
    scr = _screen(0, 1920)
    pm, _widget = _manager({"pin_left_edge": True}, scr)
    pm.update_position = MagicMock()
    with patch.object(pm_mod, "win32gui") as w32, \
            patch.object(PositionManager, "_ensure_taskbar_owner", return_value=True):
        w32.IsWindow.return_value = True
        pm.ensure_topmost()
    pm.update_position.assert_called_once()


def test_redock_without_the_pin_does_not_re_place():
    scr = _screen(0, 1920)
    pm, _widget = _manager({}, scr)
    pm.update_position = MagicMock()
    with patch.object(pm_mod, "win32gui") as w32, \
            patch.object(PositionManager, "_ensure_taskbar_owner", return_value=True):
        w32.IsWindow.return_value = True
        pm.ensure_topmost()
    pm.update_position.assert_not_called()


# --- Settings: the card lives on the Widget page, greyed out on side bars -----------------------

def _page():
    from netspeedtray.constants.i18n import I18nStrings
    return WidgetPage(I18nStrings("en_US"), on_change=lambda: None)


def test_pin_card_loads_and_saves_the_same_key():
    page = _page()
    page.load_settings({"pin_left_edge": True})
    assert page.pin_left_edge.isChecked() is True
    assert page.get_settings()["pin_left_edge"] is True
    page.load_settings({})
    assert page.pin_left_edge.isChecked() is False


def test_pin_card_shows_the_new_copy():
    """2.2.2 review (copy): 'Dock at the left end of the taskbar' with a one-line description."""
    page = _page()
    assert page._pin_card._title_label.text() == page.i18n.PIN_LEFT_EDGE_LABEL
    assert page.i18n.PIN_LEFT_EDGE_LABEL == "Dock at the left end of the taskbar"
    assert page._pin_card._desc_label.text() == page.i18n.PIN_LEFT_EDGE_DESC


def test_pin_card_greys_out_on_a_side_bar():
    page = _page()
    tb = MagicMock()
    tb.get_edge_position.return_value = LEFT
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=tb):
        page._sync_pin_card()
        assert page._pin_card.isEnabled() is False
        assert page._pin_card.toolTip() == page.i18n.PIN_LEFT_EDGE_GREY_SIDE
        # The saved choice stays underneath the greyed-out card (load re-syncs with the same bar).
        page.load_settings({"pin_left_edge": True})
        assert page.pin_left_edge.isChecked() is True
        assert page._pin_card.isEnabled() is False


def test_pin_card_greys_out_while_free_move_is_on():
    """Free Move on: the pin steps aside while the widget floats, so the switch does nothing -
    the card greys out and says so (2.2.2 review). Turning Free Move off re-enables it, and the
    grey-out follows the flip live."""
    page = _page()
    tb = MagicMock()
    tb.get_edge_position.return_value = BOTTOM
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=1):
        page.free_move.setChecked(True)   # fires the toggled hook that re-syncs the card
        assert page._pin_card.isEnabled() is False
        assert page._pin_card.toolTip() == page.i18n.PIN_LEFT_EDGE_GREY_FREE_MOVE
        page.free_move.setChecked(False)
        assert page._pin_card.isEnabled() is True
        assert page._pin_card.toolTip() == ""


def test_pin_card_greys_out_on_a_left_aligned_bar():
    """A left-aligned Win11 bar (TaskbarAl = 0) or a Win10-style task list: the pin falls back to
    the tray spot, so the toggle silently did nothing - grey it out and say why (2.2.2 review)."""
    page = _page()
    tb = MagicMock()
    tb.get_edge_position.return_value = BOTTOM
    tb.tasklist_rect = None
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=0):
        page._sync_pin_card()
        assert page._pin_card.isEnabled() is False
        assert page._pin_card.toolTip() == page.i18n.PIN_LEFT_EDGE_GREY_LEFT_ALIGNED
    # The Win10-style variant (a real task list at the left end) reads the same way.
    tb.tasklist_rect = (40, 1032, 900, 1080)
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=None):
        page._sync_pin_card()
        assert page._pin_card.isEnabled() is False
        assert page._pin_card.toolTip() == page.i18n.PIN_LEFT_EDGE_GREY_LEFT_ALIGNED


def test_pin_card_enabled_on_a_bottom_bar():
    page = _page()
    tb = MagicMock()
    tb.get_edge_position.return_value = BOTTOM
    tb.tasklist_rect = None
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=1):
        page._sync_pin_card()
    assert page._pin_card.isEnabled() is True
    assert page._pin_card.toolTip() == ""


def test_pin_card_grey_reason_is_also_a_visible_note():
    """The grey-out reason is said OUTSIDE the card too - a small secondary-text line under it,
    the monitor_note pattern (a disabled card can't be reached by Tab or touch, and Narrator only
    reads a greyed switch, while the cause here is a Windows setting the user can't see) - with
    the tooltip kept as a bonus (PR #604 third-round review). An enabled card hides the line."""
    page = _page()
    assert page.pin_note.isVisibleTo(page) is False
    tb = MagicMock()
    tb.get_edge_position.return_value = LEFT
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=tb):
        page._sync_pin_card()
        assert page.pin_note.isVisibleTo(page) is True
        assert page.pin_note.text() == page.i18n.PIN_LEFT_EDGE_GREY_SIDE
        assert page._pin_card.toolTip() == page.i18n.PIN_LEFT_EDGE_GREY_SIDE   # the tooltip stays
    tb.get_edge_position.return_value = BOTTOM
    tb.tasklist_rect = None
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=1):
        page._sync_pin_card()
    assert page.pin_note.isVisibleTo(page) is False
    assert page._pin_card.isEnabled() is True


def test_settings_and_placement_agree_on_a_mirrored_bar(monkeypatch):
    """A mirrored (RTL) bar keeps the clock at the LEFT end: the placement's left_end_is_free
    falls back to the tray spot there - and Settings now asks that SAME function instead of
    repeating its own checks, so the card greys out with the left-busy reason too. The old local
    checks missed the tray half: the card stayed enabled while the widget sat by the clock
    (PR #604 third-round review)."""
    _pin_alignment(monkeypatch, 1)
    scr = _screen(0, 1920)
    tb = _taskbar(scr, tray=(60, 1040, 190, 1080), widgets_extent=None)   # the clock at the left
    # The placement side: the pin does not apply - the widget stays at the tray spot.
    pm, widget = _manager({"pin_left_edge": True}, scr, taskbar=tb)
    with patch("netspeedtray.core.position_manager.QApplication.screenAt", return_value=scr):
        pm._apply_geometry(1500, 600)
    widget.move.assert_called_once_with(1500, 600)
    # The Settings side: the same verdict, from the same test (PositionManager.left_end_is_free).
    page = _page()
    with patch("netspeedtray.utils.taskbar_utils.get_taskbar_info", return_value=tb), \
            patch("netspeedtray.core.system_events.read_taskbar_alignment", return_value=1):
        page._sync_pin_card()
    assert page._pin_card.isEnabled() is False
    assert page._pin_card.toolTip() == page.i18n.PIN_LEFT_EDGE_GREY_LEFT_ALIGNED
    assert page.pin_note.text() == page.i18n.PIN_LEFT_EDGE_GREY_LEFT_ALIGNED


def test_pin_card_uses_the_review_copy():
    """2.2.2 third-round review (copy): no '8 px in', no 'tray spot' - and no 'falls back' claim
    (a centred bar that fills up hides the widget, it does not fall back) - the maintainer's
    exact suggested sentence instead."""
    page = _page()
    assert page.i18n.PIN_LEFT_EDGE_DESC == (
        "Sits beside the Widgets button, or at the far left when it's off. "
        "Drag it along the bar to move it; Reset position brings it back.")
    assert page._pin_card._desc_label.text() == page.i18n.PIN_LEFT_EDGE_DESC


def test_pin_description_copy_mirrored_to_every_locale():
    """The review's description sentence is mirrored to all 17 locales (they go to the
    translators with 2.2.2). It carries no digits, so the fixed-text digits scan (bn_BD's native
    numerals) has nothing to convert."""
    import json
    expected = ("Sits beside the Widgets button, or at the far left when it's off. "
                "Drag it along the bar to move it; Reset position brings it back.")
    locales = Path(__file__).parents[3] / "netspeedtray" / "constants" / "locales"
    checked = 0
    for p in sorted(locales.glob("*.json")):
        data = json.loads(p.read_bytes().decode("utf-8"))
        assert data["PIN_LEFT_EDGE_DESC"] == expected, p.name
        checked += 1
    assert checked == 17


def test_pin_lives_on_the_widget_page_only():
    """2.2.1 IA: the pin is a positioning control - it moved from General to the Widget page."""
    page = _page()
    assert hasattr(page, "pin_left_edge")
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.pages.general import GeneralPage
    general = GeneralPage(I18nStrings("en_US"), on_change=lambda: None)
    assert not hasattr(general, "pin_left_edge")
