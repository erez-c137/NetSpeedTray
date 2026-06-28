"""
Unit tests for the window-position memory helpers (utils/window_state.py).

Covers the pure save/restore contract with mocked windows (no Qt event loop):
restore falls back cleanly when there's no valid saved position, clamps an
off-screen position back onto the available geometry, and save persists
{x, y} via the main widget's ConfigManager.
"""
from unittest.mock import MagicMock, patch

from netspeedtray.utils.window_state import (
    restore_window_position, save_window_position,
    restore_window_geometry, save_window_geometry,
)

# Where QApplication is imported in the module under test (for patching screenAt).
_SCREEN_AT = "netspeedtray.utils.window_state.QApplication.screenAt"


def _mock_screen(left=0, top=0, right=1920, bottom=1080):
    screen = MagicMock()
    avail = MagicMock()
    avail.left.return_value = left
    avail.top.return_value = top
    avail.right.return_value = right
    avail.bottom.return_value = bottom
    screen.availableGeometry.return_value = avail
    return screen


def _mock_window(width=400, height=300, screen=None):
    window = MagicMock()
    window.width.return_value = width
    window.height.return_value = height
    window.screen.return_value = screen if screen is not None else _mock_screen()
    return window


# --- restore ----------------------------------------------------------------

def test_restore_returns_false_without_valid_saved_position():
    window = _mock_window()
    assert restore_window_position(window, {}, "k") is False
    assert restore_window_position(window, {"k": None}, "k") is False
    assert restore_window_position(window, {"k": {"x": None, "y": 5}}, "k") is False
    window.move.assert_not_called()


def test_restore_moves_to_saved_in_bounds_position():
    # screenAt -> None: the saved point isn't resolvable to a connected screen, so
    # it falls back to window.screen() (the single mocked primary).
    window = _mock_window()
    with patch(_SCREEN_AT, return_value=None):
        assert restore_window_position(window, {"k": {"x": 100, "y": 200}}, "k") is True
    window.move.assert_called_once_with(100, 200)


def test_restore_clamps_offscreen_position_onto_available_geometry():
    # Window 400x300 on a 1920x1080 work area; saved far off the bottom-right.
    window = _mock_window(width=400, height=300, screen=_mock_screen())
    with patch(_SCREEN_AT, return_value=None):
        assert restore_window_position(window, {"k": {"x": 5000, "y": 2000}}, "k") is True
    # Clamped to (right - width, bottom - height) == (1520, 780).
    window.move.assert_called_once_with(1520, 780)


def test_restore_resolves_screen_from_saved_point_multimonitor():
    # Regression: an unshown top-level window reports the PRIMARY screen, so clamping
    # to window.screen() would yank a secondary-monitor position back to primary.
    # The saved point (3000,200) is on a secondary monitor at x:1920..3840 — restore
    # must resolve and clamp to THAT screen, not the primary.
    secondary = _mock_screen(left=1920, top=0, right=3840, bottom=1080)
    window = _mock_window(width=400, height=300, screen=_mock_screen())  # window.screen() == primary
    with patch(_SCREEN_AT, return_value=secondary):
        assert restore_window_position(window, {"k": {"x": 3000, "y": 200}}, "k") is True
    window.move.assert_called_once_with(3000, 200)  # stays on the secondary monitor


# --- save -------------------------------------------------------------------

def test_save_writes_position_and_persists():
    window = MagicMock()
    pos = MagicMock()
    pos.x.return_value = 111
    pos.y.return_value = 222
    window.pos.return_value = pos

    main = MagicMock()
    main.config = {}

    save_window_position(window, main, "settings_window_pos")

    assert main.config["settings_window_pos"] == {"x": 111, "y": 222}
    main.config_manager.save.assert_called_once_with(main.config)


def test_save_skipped_when_config_missing():
    # config is None -> the guard must return WITHOUT calling config_manager.save
    # (asserting the skip, not merely that it doesn't raise).
    window = MagicMock()
    main = MagicMock()
    main.config = None
    save_window_position(window, main, "k")
    main.config_manager.save.assert_not_called()


def test_save_is_noop_when_main_widget_none():
    window = MagicMock()
    save_window_position(window, None, "k")  # must not raise


# --- geometry (position + size + maximized) ---------------------------------

def test_restore_geometry_applies_size_then_clamps_position():
    window = _mock_window(width=400, height=300)
    with patch(_SCREEN_AT, return_value=None):
        ok = restore_window_geometry(window, {"k": {"x": 100, "y": 200, "w": 940, "h": 680}}, "k")
    assert ok is True
    window.resize.assert_called_once_with(940, 680)
    window.move.assert_called_once_with(100, 200)


def test_restore_geometry_remaximizes_when_flagged():
    window = _mock_window()
    window.windowState.return_value = 0
    with patch(_SCREEN_AT, return_value=None):
        restore_window_geometry(window, {"k": {"x": 10, "y": 10, "w": 800, "h": 600, "maximized": True}}, "k")
    window.setWindowState.assert_called_once()        # re-maximized on open


def test_restore_geometry_upgrades_old_position_only_save():
    # A pre-existing position-only save {x,y} must still restore (no size to apply).
    window = _mock_window()
    with patch(_SCREEN_AT, return_value=None):
        assert restore_window_geometry(window, {"k": {"x": 50, "y": 60}}, "k") is True
    window.resize.assert_not_called()
    window.move.assert_called_once_with(50, 60)


def test_save_geometry_stores_normal_geometry_when_maximized():
    window = MagicMock()
    window.isMaximized.return_value = True
    window.isFullScreen.return_value = False
    normal = MagicMock()
    normal.x.return_value = 30; normal.y.return_value = 40
    normal.width.return_value = 900; normal.height.return_value = 650
    window.normalGeometry.return_value = normal
    main = MagicMock(); main.config = {}
    save_window_geometry(window, main, "monitor_window_pos")
    assert main.config["monitor_window_pos"] == {"x": 30, "y": 40, "w": 900, "h": 650, "maximized": True}
    window.geometry.assert_not_called()               # used normalGeometry, not the maximized size
    main.config_manager.save.assert_called_once()


# --- attach_position_memory (the debounced move-saver, end-to-end) ------------

def test_position_memory_persists_a_user_move(qtbot, monkeypatch):
    """End-to-end: a real move after the arm grace period debounce-saves; a move
    during the arm window (WM frame-adjustment) is ignored. Drives the real
    _PositionMemory event filter + QTimers via qtbot."""
    from PyQt6.QtWidgets import QWidget
    from netspeedtray.utils import window_state

    saved = []
    monkeypatch.setattr(window_state, "save_window_position",
                        lambda window, main, key: saved.append(key))

    main = MagicMock()
    main.config = {}
    main.config_manager = MagicMock()

    w = QWidget()
    qtbot.addWidget(w)
    window_state.attach_position_memory(w, main, "test_window_pos")
    w.show()
    qtbot.waitExposed(w)

    # A move during the arm grace period must NOT be saved (frame-adjustment guard).
    w.move(120, 120)
    qtbot.wait(window_state._MOVE_SAVE_ARM_MS + 60)
    assert saved == [], "a move before arming should not be persisted"

    # A move after arming should debounce-save exactly once.
    w.move(300, 220)
    qtbot.wait(window_state._MOVE_SAVE_DEBOUNCE_MS + 150)
    assert saved == ["test_window_pos"], f"expected one debounced save, got {saved}"
