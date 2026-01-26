"""
Views submodule for NetSpeedTray.

Contains UI-related classes like NetworkSpeedWidget, SettingsDialog, and GraphWindow.
"""

from netspeedtray.views.widget import NetworkSpeedWidget
from netspeedtray.views.settings import SettingsDialog
from netspeedtray.views.graph import GraphWindow

__all__ = ["NetworkSpeedWidget", "SettingsDialog", "GraphWindow"]
