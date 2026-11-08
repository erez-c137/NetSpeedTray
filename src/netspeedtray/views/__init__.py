"""
Views submodule for NetSpeedTray.

Contains the UI-related classes: NetworkSpeedWidget (the taskbar widget), SettingsDialog, and the
unified Monitor (views/monitor/, imported lazily by the widget). The standalone Graph and App Activity
windows were retired in 2.0 - the Monitor replaced them; their reusable engine/worker live on under
views/graph/ and views/app_activity/ and are imported directly where needed.
"""

from typing import TYPE_CHECKING

from netspeedtray.views.widget import NetworkSpeedWidget

if TYPE_CHECKING:
    from netspeedtray.views.settings import SettingsDialog

__all__ = ["NetworkSpeedWidget", "SettingsDialog"]


def __getattr__(name: str):
    """`SettingsDialog` loads on first access, not with the package: importing it here eagerly put the
    whole Settings package (six pages, support_bundle -> platform -> _wmi) in every process at startup,
    ~2 MB for a dialog most sessions never open. The widget imports it itself when Settings opens; a
    unit test pins that the startup path leaves `netspeedtray.views.settings` unloaded."""
    if name == "SettingsDialog":
        from netspeedtray.views.settings import SettingsDialog
        return SettingsDialog
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
