"""
Regression tests for #72: the widget must stay on the preferred (secondary)
monitor after the initial placement.

Root cause of the "flashes onto monitor 2 then jumps back to primary" bug:
``NetworkSpeedWidget._execute_refresh`` - the authoritative refresh loop that
runs at startup (via ``QTimer.singleShot(0, ...)``), then every second and on
every foreground change - resolved the taskbar with a bare ``get_taskbar_info()``
(always the PRIMARY taskbar) and force-fed it into
``position_manager.update_position(fresh_taskbar_info=...)``. That override beat
the preferred-monitor logic, so the correct initial placement on a secondary
monitor survived only until the first refresh tick (milliseconds later).

The fix makes ``_execute_refresh`` resolve the widget's OWN taskbar, honoring
the ``preferred_monitor`` setting - mirroring what ``_check_for_tray_changes``
already does. These tests call the (unbound) method against a lightweight mock
``self`` so no QApplication or real widget is required.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from netspeedtray.views.widget import main as widget_main


def _make_widget(preferred, *, free_move=False, visible=True):
    """A minimal mock ``self`` exercising the branches in ``_execute_refresh``.

    ``config`` is a real dict on purpose: a MagicMock ``.get`` returns a
    truthy MagicMock, which would silently flip ``free_move``/visibility logic.
    """
    w = MagicMock()
    w.config = {
        "preferred_monitor": preferred,
        "free_move": free_move,
        "keep_visible_fullscreen": False,
    }
    w._is_context_menu_visible = False
    w._dragging = False
    w._taskbar_lost_count = 0
    w.isVisible.return_value = visible
    return w


@pytest.fixture
def patched_taskbar(monkeypatch):
    """Patch the module-level helpers ``_execute_refresh`` calls and record the
    ``preferred_screen_name`` passed to ``get_taskbar_info``."""
    calls = {"preferred": "unset", "returned": None}

    def fake_get_taskbar_info(preferred_screen_name=None):
        calls["preferred"] = preferred_screen_name
        tb = SimpleNamespace(hwnd=4242, screen_name=preferred_screen_name or "PRIMARY")
        calls["returned"] = tb
        return tb

    monkeypatch.setattr(widget_main, "get_taskbar_info", fake_get_taskbar_info)
    monkeypatch.setattr(widget_main, "is_taskbar_visible", lambda tb: True)
    monkeypatch.setattr(widget_main, "is_taskbar_obstructed", lambda tb, hwnd: False)
    monkeypatch.setattr(widget_main.win32gui, "GetForegroundWindow", lambda: 0)
    return calls


def test_refresh_resolves_preferred_monitor_taskbar(patched_taskbar):
    """The refresh path must ask for the preferred monitor's taskbar, not the
    bare primary. This is the specific regression that dragged the widget off a
    secondary monitor within one event-loop tick."""
    w = _make_widget("32UHD144 (2)")

    widget_main.NetworkSpeedWidget._execute_refresh(w)

    assert patched_taskbar["preferred"] == "32UHD144 (2)"


def test_refresh_positions_against_the_preferred_taskbar(patched_taskbar):
    """The taskbar resolved with the preference must be the exact object fed to
    ``update_position`` - proving no second bare (primary) lookup sneaks in and
    re-pins the widget."""
    w = _make_widget("32UHD144 (2)")

    widget_main.NetworkSpeedWidget._execute_refresh(w)

    w.position_manager.update_position.assert_called_once()
    _, kwargs = w.position_manager.update_position.call_args
    assert kwargs.get("fresh_taskbar_info") is patched_taskbar["returned"]
    assert kwargs["fresh_taskbar_info"].screen_name == "32UHD144 (2)"


def test_refresh_without_preference_is_unchanged(patched_taskbar):
    """Single-monitor / no-preference users must be unaffected: passing
    ``preferred_screen_name=None`` is exactly the old bare behavior (primary)."""
    w = _make_widget(None)

    widget_main.NetworkSpeedWidget._execute_refresh(w)

    assert patched_taskbar["preferred"] is None
    w.position_manager.update_position.assert_called_once()


def test_refresh_skips_positioning_in_free_move(patched_taskbar):
    """Free-move users position manually; the refresh must still resolve the
    preferred taskbar (for visibility checks) but must NOT reposition."""
    w = _make_widget("32UHD144 (2)", free_move=True)

    widget_main.NetworkSpeedWidget._execute_refresh(w)

    assert patched_taskbar["preferred"] == "32UHD144 (2)"
    w.position_manager.update_position.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
