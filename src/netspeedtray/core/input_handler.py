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
from PyQt6.QtWidgets import QApplication

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

        # Check for minimum drag distance to prevent accidental moves (Fix for sticky Free Move)
        if (event.globalPosition().toPoint() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
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
        """
        Saves the final position based on the current mode:
        - Free Move ON: Saves absolute X/Y coordinates.
        - Free Move OFF: Calculates and saves the offset relative to the tray/edge.
        """
        try:
            config = self.widget.config
            is_free_move = config.get("free_move", False)
            updates = {}

            if is_free_move:
                # Save absolute coordinates
                updates["position_x"] = self.widget.x()
                updates["position_y"] = self.widget.y()
            else:
                # Calculate constrained offset
                # We need to know WHICH offset to update (x or y) based on taskbar orientation
                from netspeedtray.utils.taskbar_utils import get_taskbar_info
                from netspeedtray import constants
                
                tb_info = get_taskbar_info()
                edge = tb_info.get_edge_position()
                dpi_scale = tb_info.dpi_scale if tb_info.dpi_scale > 0 else 1.0
                
                if edge in (constants.taskbar.edge.BOTTOM, constants.taskbar.edge.TOP):
                    # Horizontal Taskbar: Variable is X offset from RIGHT side
                    # Offset = RightBoundary - WidgetRight
                    # Logic matches PositionCalculator: x = right_boundary - widget_width - offset
                    # So: offset = right_boundary - x - widget_width
                    
                    # Calculate Right Boundary ( Tray Left or Screen Right )
                    tray_rect = tb_info.get_tray_rect()
                    right_boundary = (tray_rect[0] / dpi_scale) if tray_rect else (tb_info.rect[2] / dpi_scale)
                    
                    # Current Widget X
                    current_x = self.widget.x()
                    current_x_log = self.widget.pos().x() # Use logical pos from pos()
                    widget_width = self.widget.width()
                    
                    new_offset = int(right_boundary - current_x_log - widget_width)
                    updates["tray_offset_x"] = new_offset
                    self.logger.debug(f"Saved Horizontal Offset: {new_offset} (RightBound={right_boundary}, X={current_x_log}, W={widget_width})")
                    
                elif edge in (constants.taskbar.edge.LEFT, constants.taskbar.edge.RIGHT):
                    # Vertical Taskbar: Variable is Y offset from BOTTOM
                    # Logic: y = bottom_boundary - widget_height - offset_y
                    # So: offset_y = bottom_boundary - y - widget_height
                    
                    tray_rect = tb_info.get_tray_rect()
                    bottom_boundary = (tray_rect[1] / dpi_scale) if tray_rect else (tb_info.rect[3] / dpi_scale)
                    
                    current_y = self.widget.y()
                    current_y_log = self.widget.pos().y()
                    widget_height = self.widget.height()
                    
                    new_offset = int(bottom_boundary - current_y_log - widget_height)
                    updates["tray_offset_y"] = new_offset
                    self.logger.debug(f"Saved Vertical Offset: {new_offset} (BottomBound={bottom_boundary}, Y={current_y_log}, H={widget_height})")

            if hasattr(self.widget, 'update_config') and updates:
                self.widget.update_config(updates)
                self.logger.debug(f"Drag ended. Updated config: {updates}")
                
        except Exception as e:
            self.logger.error("Failed to save dragged position: %s", e, exc_info=True)
