"""
Views submodule for NetSpeedTray.

Contains UI-related classes like NetworkSpeedWidget, SettingsDialog, and GraphWindow.

GraphWindow is intentionally NOT eagerly imported here — it pulls in
matplotlib + numpy (~50 MB RSS) and is only needed when the user opens
the graph window. Access it via `from netspeedtray.views.graph import
GraphWindow` instead. A module-level `__getattr__` makes
`from netspeedtray.views import GraphWindow` still work for backward
compatibility, with the heavy import deferred until first access.
"""

from netspeedtray.views.widget import NetworkSpeedWidget
from netspeedtray.views.settings import SettingsDialog

__all__ = ["NetworkSpeedWidget", "SettingsDialog", "GraphWindow"]


def __getattr__(name):
    """PEP 562 lazy attribute access — defers matplotlib import to graph open."""
    if name == "GraphWindow":
        from netspeedtray.views.graph import GraphWindow as _GraphWindow
        return _GraphWindow
    raise AttributeError(f"module 'netspeedtray.views' has no attribute {name!r}")
