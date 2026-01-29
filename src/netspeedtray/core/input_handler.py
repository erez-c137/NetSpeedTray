"""
Input Handler for NetworkSpeedTray.

This module encapsulates all mouse and keyboard interaction logic, separating it
from the main widget processing. It handles:
1. Dragging operations (start, move, end).
2. Context menu triggers.
3. interactions (e.g., Double-Click to open Graph).
"""

import logging
from typing import Optional, TYPE_CHECKING
from PyQt6.QtCore import QObject, QPoint, Qt
from PyQt6.QtGui import QMouseEvent

from netspeedtray import constants

if TYPE_CHECKING:
    from netspeedtray.views.widget import NetworkSpeedWidget
    from netspeedtray.core.position_manager import PositionManager
    from netspeedtray.core.tray_manager import TrayIconManager

class InputHandler(QObject):
    """
    Handles mouse and keyboard input for the NetworkSpeedWidget.
    """
    def __init__(self, 
                 widget: 'NetworkSpeedWidget', 
                 position_manager: 'PositionManager',
                 tray_manager: 'TrayIconManager') -> None:
        super().__init__(widget)
        self.widget = widget
        self.position_manager = position_manager
        self.tray_manager = tray_manager
        self.logger = logging.getLogger("NetSpeedTray.Core.InputHandler")
        
        # State
        self._drag_start_pos: Optional[QPoint] = None
        self._is_dragging: bool = False

    def handle_mouse_press(self, event: QMouseEvent) -> None:
        """Handles mouse press start."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint() - self.widget.pos()
            self._is_dragging = False # Waiting for move to confirm drag
            event.accept()

    def handle_mouse_move(self, event: QMouseEvent) -> None:
        """Handles dragging logic."""
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self._drag_start_pos:
            return

        # Start dragging if we haven't already
        self._is_dragging = True
        self.widget._dragging = True # Notify widget to stop position checks
        
        # Calculate desired position
        desired_global_pos = event.globalPosition().toPoint() - self._drag_start_pos
        
        # Constrain the position using PositionManager
        final_pos = self.position_manager.constrain_drag(desired_global_pos)
        
        # Apply move
        self.widget.move(final_pos)
        event.accept()

    def handle_mouse_release(self, event: QMouseEvent) -> None:
        """Handles drag end and config saving."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_dragging:
                # Drag completed
                self._is_dragging = False
                self.widget._dragging = False
                self._save_dragged_position()
                self.logger.debug("Drag ended. Position saved: %s", self.widget.pos())
            
            self._drag_start_pos = None
            event.accept()

    def handle_double_click(self, event: QMouseEvent) -> None:
        """Handles double-click (Open Graph)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.logger.debug("Double-click detected. Opening Graph Window.")
            if hasattr(self.widget, 'open_graph_window'):
                self.widget.open_graph_window()
            event.accept()

    def _save_dragged_position(self) -> None:
        """Saves final position and sets free_move=True."""
        try:
            updates = {
                "free_move": True,
                "position_x": self.widget.x(),
                "position_y": self.widget.y()
            }
            if hasattr(self.widget, 'update_config'):
                self.widget.update_config(updates)
        except Exception as e:
            self.logger.error("Failed to save dragged position: %s", e, exc_info=True)
