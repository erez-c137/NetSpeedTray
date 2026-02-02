"""
UI layout and component initialization for the Graph Window.
Separates visual construction from the main window controller.
"""

from PyQt6.QtCore import Qt, QPoint, QSize, QTimer
from PyQt6.QtWidgets import (
    QVBoxLayout, QWidget, QTabWidget, QHBoxLayout, 
    QLabel, QPushButton, QSizePolicy
)
from PyQt6.QtGui import QIcon, QPainter, QColor, QBrush, QPen
from typing import Tuple

from netspeedtray import constants
from netspeedtray.constants import styles as style_constants
from netspeedtray.utils import helpers, styles as style_utils


class StatusIndicatorWidget(QWidget):
    """
    A subtle, professional status indicator with a pulsing dot and small text.
    Uses paintEvent for the dot to avoid layout thrashing.
    """
    # State Definitions
    STATES = {
        "LIVE": {"color": "#4caf50", "text": "LIVE", "pulse": True},       # Green
        "COLLECTING": {"color": "#ff9800", "text": "LOAD", "pulse": True},  # Orange
        "NO_DATA": {"color": "#d32f2f", "text": "NO DATA", "pulse": False}  # Red
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._current_state = "COLLECTING"
        self._dot_color = QColor(self.STATES["COLLECTING"]["color"])
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._update_animation)
        self._opacity = 1.0
        self._fading_out = True
        
        # Optimize size
        self.setFixedHeight(16)
        
        # Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        
        # Spacer for the dot
        self._dot_spacer = QWidget()
        self._dot_spacer.setFixedSize(10, 16) 
        layout.addWidget(self._dot_spacer)
        
        # Text
        self._text_lbl = QLabel(self.STATES["COLLECTING"]["text"])
        self._text_lbl.setStyleSheet("""
            QLabel {
                color: #aaa;
                font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.5px;
                background: transparent;
            }
        """)
        layout.addWidget(self._text_lbl)
        
        self.setStyleSheet("background: transparent;")

    def setStatus(self, state_name):
        """Sets the indicator state (LIVE, COLLECTING, NO_DATA)."""
        if state_name not in self.STATES:
            return
        
        self._current_state = state_name
        cfg = self.STATES[state_name]
        
        self._dot_color = QColor(cfg["color"])
        self._text_lbl.setText(cfg["text"])
        
        if cfg["pulse"]:
            if not self._anim_timer.isActive():
                self._anim_timer.start(80)
        else:
            self._anim_timer.stop()
            self._opacity = 1.0
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        dot_x = 1
        dot_y = 4 
        
        c = QColor(self._dot_color)
        if self.STATES[self._current_state]["pulse"]:
            c.setAlphaF(self._opacity)
        
        painter.setBrush(QBrush(c))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(dot_x, dot_y, 8, 8)

    def _update_animation(self):
        if self._fading_out:
            self._opacity -= 0.08
            if self._opacity <= 0.2:
                self._opacity = 0.2
                self._fading_out = False
        else:
            self._opacity += 0.08
            if self._opacity >= 1.0:
                self._opacity = 1.0
                self._fading_out = True
        self.update()

    def show(self):
        super().show()
        if self.STATES[self._current_state]["pulse"] and not self._anim_timer.isActive():
            self._anim_timer.start(80)
    
    def hide(self):
        super().hide()
        self._anim_timer.stop()


class GraphWindowUI:
    """ Handles all UI layout and component initialization for GraphWindow. """
    
    def __init__(self, window: QWidget):
        self.window = window
        self.window.setMinimumSize(constants.graph.GRAPH_WIDGET_WIDTH, constants.graph.GRAPH_WIDGET_HEIGHT)
        self.logger = window.logger
        self.i18n = window.i18n
        
        # UI Elements
        self.main_layout = None
        self.tab_widget = None
        self.graph_widget = None
        self.graph_layout = None
        self.stats_bar = None
        self.hamburger_icon = None
        self.reset_zoom_btn = None
        self.zoom_hint_label = None
        self._graph_message_label = None
        
        # Stat Labels
        self.max_stat_val = None
        self.avg_stat_val = None
        self.total_stat_val = None

    def setupUi(self):
        """Constructs the main layout and widgets."""
        # Root layout is now Horizontal to allow side-by-side panels
        self.main_layout = QHBoxLayout(self.window)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Content Container (Holds the Graph/Tabs)
        self.content_container = QWidget()
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(self.content_container, 1)

        # Tab widget (App Usage feature temporarily disabled)
        self.tab_widget = QTabWidget(self.content_container)
        self.content_layout.addWidget(self.tab_widget)

        # Graph tab
        self.graph_widget = QWidget()
        self.graph_layout = QVBoxLayout(self.graph_widget)
        self.tab_widget.addTab(self.graph_widget, self.i18n.SPEED_GRAPH_TAB_LABEL)
        
        # Hide the tab bar as it's not needed for a single tab
        self.tab_widget.tabBar().setVisible(False)

    def add_settings_panel(self, settings_widget: QWidget):
        """Adds the settings widget to the side of the main content."""
        self.main_layout.addWidget(settings_widget, 0)

    def init_overlay_elements(self):
        """ Initialize info header (Stats + Controls) using a proper Layout. """
        try:
            # Container for the top bar
            self.header_widget = QWidget()
            self.header_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.header_widget.setStyleSheet("background: transparent;")
            
            # Use HBox layout to manage positioning automatically
            header_layout = QHBoxLayout(self.header_widget)
            header_layout.setContentsMargins(0, 0, 10, 0)
            header_layout.setSpacing(12)
            
            # 1. Stats Bar
            self.stats_bar = QWidget()
            self.stats_bar.setObjectName("statsBar")
            self.stats_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.stats_bar.setStyleSheet(style_utils.graph_stats_bar_style())
            
            stats_layout = QHBoxLayout(self.stats_bar)
            stats_layout.setContentsMargins(12, 6, 12, 6)
            stats_layout.setSpacing(24)

            self.max_stat_val = self._create_stat_card(stats_layout, "MAX SPEED")
            self.avg_stat_val = self._create_stat_card(stats_layout, "AVG SPEED")
            self.total_stat_val = self._create_stat_card(stats_layout, "TOTAL DATA")
            
            # 2. Loading Indicator (Pulse Widget) - NOW INSIDE STATS BAR
            stats_layout.addStretch() 
            self.loading_indicator = StatusIndicatorWidget(self.stats_bar)
            stats_layout.addWidget(self.loading_indicator) 
            
            header_layout.addWidget(self.stats_bar, 1)

            # 3. Reset View Button
            self.reset_zoom_btn = QPushButton("⟲ Reset View", self.header_widget)
            self.reset_zoom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.reset_zoom_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(0, 120, 212, 0.9);
                    color: white;
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-size: 11px;
                    font-weight: 500;
                    border: none;
                }
                QPushButton:hover { background: rgba(0, 120, 212, 1.0); }
                QPushButton:pressed { background: rgba(0, 100, 180, 1.0); }
            """)
            self.reset_zoom_btn.hide()
            header_layout.addWidget(self.reset_zoom_btn, 0, Qt.AlignmentFlag.AlignVCenter)

            # 4. Hamburger Menu
            hamburger_size = getattr(constants.graph, 'HAMBURGER_ICON_SIZE', 24)
            self.hamburger_icon = QPushButton(self.header_widget)
            self.hamburger_icon.setFixedSize(hamburger_size, hamburger_size)
            self.hamburger_icon.setCursor(Qt.CursorShape.PointingHandCursor)
            self.hamburger_icon.setText("☰")
            font = self.hamburger_icon.font()
            font.setPointSize(14)
            self.hamburger_icon.setFont(font)
            self.hamburger_icon.setStyleSheet(style_utils.graph_overlay_style())
            
            header_layout.addWidget(self.hamburger_icon, 0, Qt.AlignmentFlag.AlignVCenter)

            # Add header to the main VBox layout
            self.graph_layout.addWidget(self.header_widget)
            
            # 5. Message Overlay (Legacy Large Overlay)
            self._graph_message_label = QLabel(self.graph_widget)
            self._graph_message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._graph_message_label.setStyleSheet("""
                QLabel {
                    color: #aaa;
                    background: rgba(45, 45, 45, 180);
                    border-radius: 8px;
                    padding: 20px 40px;
                    font-size: 15px;
                    font-weight: 500;
                }
            """)
            self._graph_message_label.hide()
            
            # 6. Zoom Hint Label
            self.zoom_hint_label = QLabel("Double-click to Reset", self.graph_widget)
            self.zoom_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            try:
                self.zoom_hint_label.setStyleSheet(style_utils.zoom_hint_style())
            except AttributeError:
                self.zoom_hint_label.setStyleSheet("QLabel { background: rgba(0,0,0,0.6); color: white; border-radius: 12px; padding: 4px 12px; }")
            self.zoom_hint_label.hide()

        except Exception as e:
            self.logger.error(f"Error initializing overlay elements: {e}", exc_info=True)

    def _create_stat_card(self, parent_layout: QHBoxLayout, title_text: str) -> QLabel:
        """ Internal helper to create a stat card. """
        card = QWidget()
        card.setStyleSheet(style_utils.graph_stats_card_style())
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(1)

        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet(style_utils.graph_stats_title_style())
        
        value_lbl = QLabel("--")
        value_lbl.setStyleSheet(style_utils.graph_stats_value_style())
        
        card_layout.addWidget(title_lbl)
        card_layout.addWidget(value_lbl)
        parent_layout.addWidget(card)
        return value_lbl

    def reposition_overlay_elements(self):
        """Reposition overlays based on window/widget size."""
        if not all([self.tab_widget, self.hamburger_icon, self.stats_bar]):
            return

        try:
            if self.tab_widget.currentWidget() == self.graph_widget:
                # Header items are layout managed now. 
                # Manage absolute overlays only.

                # Message Overlay (Center of graph)
                if self._graph_message_label.isVisible():
                    # Center in the remaining space (approx graph widget size)
                    msg_x = (self.graph_widget.width() - self._graph_message_label.width()) // 2
                    msg_y = (self.graph_widget.height() - self._graph_message_label.height()) // 2
                    self._graph_message_label.move(msg_x, msg_y)
                    self._graph_message_label.raise_()
                
                # Zoom Hint (Bottom Center typically, or top center)
                if self.zoom_hint_label.isVisible():
                     hint_x = (self.graph_widget.width() - self.zoom_hint_label.width()) // 2
                     hint_y = 60 # Below header
                     self.zoom_hint_label.move(hint_x, hint_y)
                     self.zoom_hint_label.raise_()
                    
        except Exception as e:
            self.logger.error(f"Error repositioning overlay elements: {e}", exc_info=True)

    def show_graph_message(self, message: str, is_error: bool = True):
        """Displays a message overlay or updates the status indicator."""
        if not is_error:
            # Route to normalized status indicator
            if "collecting" in message.lower() or "loading" in message.lower():
                self.loading_indicator.setStatus("COLLECTING")
            elif "no data" in message.lower():
                self.loading_indicator.setStatus("NO_DATA")
            else:
                self.loading_indicator.setStatus("LIVE")
            
            self.loading_indicator.show()
            
            if self._graph_message_label.isVisible():
                self._graph_message_label.hide()
            return

        # Legacy/Error behavior (Large Overlay)
        self._graph_message_label.setText(message)
        self.loading_indicator.hide() 
        
        # Style adjustment for error vs info
        color = "#ff4d4d" if is_error else "#aaa"
        bg = "rgba(60, 30, 30, 200)" if is_error else "rgba(45, 45, 45, 180)"
        
        self._graph_message_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                background: {bg};
                border-radius: 8px;
                padding: 20px 40px;
                font-size: 15px;
                font-weight: 500;
            }}
        """)
        
        self._graph_message_label.adjustSize()
        self.reposition_overlay_elements()
        self._graph_message_label.show()
        self._graph_message_label.raise_()

    def hide_graph_message(self):
        """Hides the message overlay."""
        self._graph_message_label.hide()
        self.loading_indicator.hide()

    def show_graph_error(self, message: str):
        """A convenience wrapper to display an error message on the graph."""
        self.show_graph_message(message, is_error=True)

    def show_zoom_hint(self):
        """Shows a temporary hint label when zooming."""
        self.zoom_hint_label.show()
        self.zoom_hint_label.raise_()
        # Auto-hide after 3 seconds
        QTimer.singleShot(3000, self.zoom_hint_label.hide)

    def get_settings_panel_geometry(self, panel_width: int = 320) -> Tuple[QPoint, QSize, int]:
        """
        Calculates position for settings panel.
        Now purely anchors to the right side of the window, regardless of content.
        Does NOT try to push the window wider.
        """
        # Always anchor to the right edge of the graph layout
        # Overlap the content (the user wanted it to behave like a drawer/overlay)
        
        # Calculate available height below header
        header_height = self.header_widget.height() if hasattr(self, 'header_widget') else 60
        panel_y = header_height
        panel_height = self.graph_widget.height() - panel_y
        
        # Align to right edge
        win_width = self.window.width()
        panel_x = win_width - panel_width - 10 # 10px padding from right edge
        
        # Ensure it doesn't go off-screen to the left if window is tiny
        if panel_x < 0:
            panel_x = 0
            panel_width = win_width # Full width if window is smaller than panel

        # Returning (pos, size, req_width)
        # req_width is just current width now, we never want to expand.
        return QPoint(panel_x, panel_y), QSize(panel_width, panel_height), self.window.width()
