"""
Views submodule for NetSpeedTray.

Contains the UI-related classes: NetworkSpeedWidget (the taskbar widget), SettingsDialog, and the
unified Monitor (views/monitor/, imported lazily by the widget). The standalone Graph and App Activity
windows were retired in 2.0 — the Monitor replaced them; their reusable engine/worker live on under
views/graph/ and views/app_activity/ and are imported directly where needed.
"""

from netspeedtray.views.widget import NetworkSpeedWidget
from netspeedtray.views.settings import SettingsDialog

__all__ = ["NetworkSpeedWidget", "SettingsDialog"]
