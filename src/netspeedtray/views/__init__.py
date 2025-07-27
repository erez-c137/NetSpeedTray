"""
Views submodule for NetSpeedTray.

Contains UI-related classes like NetworkSpeedWidget, SettingsDialog, and GraphWindow.
"""

from .widget import NetworkSpeedWidget
from .settings import SettingsDialog
from .graph import GraphWindow

__all__ = ["NetworkSpeedWidget", "SettingsDialog", "GraphWindow"]
