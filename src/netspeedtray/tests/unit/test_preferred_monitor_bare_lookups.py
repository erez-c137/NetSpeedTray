"""
rc.2 W12: a bare ``get_taskbar_info()`` resolves the PRIMARY taskbar. Every lookup that stands for
"the widget's own taskbar" must pass the preferred monitor (#72), or a widget on a secondary
monitor is seeded / docked against the primary bar (memory: secondary-taskbar geometry).

Covered here: the PositionManager seed in ``_init_managers`` and the owner fallback in
``_ensure_taskbar_owner``. The layout-mode edge (``_refresh_cached_layout_mode``) is fixed on #434's
branch with its own test (test_layout_mode_preferred_monitor.py). The remaining bare calls are
deliberate: ``SystemEventHandler``'s Explorer-restart probe and MOVESIZE hook watch the primary bar,
which dies with Explorer like every other one, and ``get_taskbar_height`` is the legacy startup
argument nothing reads any more.
"""
import types
from unittest.mock import MagicMock, patch

from netspeedtray.core import position_manager as pm_mod
from netspeedtray.core.position_manager import PositionManager, WindowState
from netspeedtray.views.widget import main as widget_main

PREFERRED = r"\\.\DISPLAY2"


def test_init_managers_seeds_the_preferred_monitors_taskbar():
    seen = []

    def fake_get_taskbar_info(preferred_screen_name=None):
        seen.append(preferred_screen_name)
        return MagicMock(hwnd=0x2222)

    fake = types.SimpleNamespace(logger=MagicMock(), config={"preferred_monitor": PREFERRED},
                                 current_metrics=object(), _maybe_show_side_no_room_notice=MagicMock())
    with patch.object(widget_main, "get_taskbar_info", fake_get_taskbar_info), \
            patch.object(widget_main, "WidgetLayoutManager"), \
            patch.object(widget_main, "WidgetThemeManager"), \
            patch.object(widget_main, "StartupManager"), \
            patch.object(widget_main, "PositionManager") as pm_cls, \
            patch.object(widget_main, "WindowState") as ws_cls:
        widget_main.NetworkSpeedWidget._init_managers(fake)
    assert seen == [PREFERRED]
    assert ws_cls.call_args.kwargs["taskbar_info"].hwnd == 0x2222
    pm_cls.assert_called_once()


def test_owner_fallback_resolves_the_preferred_monitors_taskbar():
    """No taskbar recorded yet: the owner fallback must look up the widget's own bar, not the primary."""
    seen = []

    def fake_get_taskbar_info(preferred_screen_name=None):
        seen.append(preferred_screen_name)
        return types.SimpleNamespace(hwnd=0x2222)

    widget = MagicMock()
    pm = PositionManager(WindowState(config={"preferred_monitor": PREFERRED}, widget=widget,
                                     taskbar_info=None))
    win32 = MagicMock()
    win32.IsWindow.return_value = True
    with patch.object(pm_mod, "get_taskbar_info", fake_get_taskbar_info), \
            patch.object(pm_mod, "win32gui", win32), \
            patch.object(pm_mod, "_GetWindowLongPtr", return_value=0), \
            patch.object(pm_mod, "_SetWindowLongPtr") as set_owner:
        assert pm._ensure_taskbar_owner(0x1000) is True
    assert seen == [PREFERRED]
    set_owner.assert_called_once_with(0x1000, pm_mod._GWLP_HWNDPARENT, 0x2222)
