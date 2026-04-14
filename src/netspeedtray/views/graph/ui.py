"""
UI layout and component initialization for the Graph Window.
Separates visual construction from the main window controller.
"""

from PyQt6.QtCore import Qt, QPoint, QSize, QTimer
from PyQt6.QtWidgets import (
    QVBoxLayout, QWidget, QTabWidget, QHBoxLayout, 
    QLabel, QPushButton, QSizePolicy, QFrame, QGridLayout
)
from PyQt6.QtGui import QIcon, QPainter, QColor, QBrush, QPen
from typing import Tuple, Optional

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
        "LIVE": {"color": "#4caf50", "i18n_key": "GRAPH_STATUS_LIVE", "fallback": "LIVE", "pulse": True},  # Green
        "COLLECTING": {"color": "#ff9800", "i18n_key": "GRAPH_STATUS_LOAD", "fallback": "LOAD", "pulse": True},  # Orange
        "NO_DATA": {"color": "#d32f2f", "i18n_key": "GRAPH_STATUS_NO_DATA", "fallback": "NO DATA", "pulse": False},  # Red
    }

    def __init__(self, parent=None, i18n=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._i18n = i18n
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
        self._text_lbl = QLabel(self._get_state_text("COLLECTING"))
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

    def _get_state_text(self, state_name: str) -> str:
        cfg = self.STATES.get(state_name, {})
        key = cfg.get("i18n_key")
        fallback = cfg.get("fallback", state_name)
        if not key or self._i18n is None:
            return fallback
        try:
            return getattr(self._i18n, key)
        except Exception:
            return fallback

    def setStatus(self, state_name):
        """Sets the indicator state (LIVE, COLLECTING, NO_DATA)."""
        if state_name not in self.STATES:
            return
        
        self._current_state = state_name
        cfg = self.STATES[state_name]
        
        self._dot_color = QColor(cfg["color"])
        self._text_lbl.setText(self._get_state_text(state_name))
        
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

class OverviewMetricCard(QFrame):
    """
    A compact dashboard card used by the Overview tab.

    Includes a subtle left accent bar, a title, a primary value, and a secondary line.
    """
    def __init__(self, title: str, accent_color: str = "#4caf50", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("overviewMetricCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        # Dark-mode friendly by default (GraphWindow is primarily dark themed today).
        self.setStyleSheet(f"""
            QFrame#overviewMetricCard {{
                background: rgba(32, 32, 32, 0.92);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # Accent bar (separate widget to keep Qt styles simple / reliable).
        self._accent = QWidget(self)
        self._accent.setFixedWidth(4)
        self._accent.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._accent.setStyleSheet(f"background: {accent_color}; border-radius: 2px;")

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)
        header_row.addWidget(self._accent, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("""
            QLabel {
                color: rgba(220, 220, 220, 0.70);
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.6px;
                font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
            }
        """)

        self.value_lbl = QLabel("--")
        self.value_lbl.setStyleSheet("""
            QLabel {
                color: rgba(255, 255, 255, 0.95);
                font-size: 22px;
                font-weight: 700;
                font-family: 'Cascadia Mono', 'Consolas', 'SF Mono', monospace;
            }
        """)

        self.sub_lbl = QLabel("")
        self.sub_lbl.setStyleSheet("""
            QLabel {
                color: rgba(220, 220, 220, 0.60);
                font-size: 11px;
                font-weight: 500;
                font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
            }
        """)

        text_col.addWidget(self.title_lbl)
        text_col.addWidget(self.value_lbl)
        text_col.addWidget(self.sub_lbl)
        header_row.addLayout(text_col, 1)

        layout.addLayout(header_row)


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
        self.overview_meta_label = None

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

        # Sub-widgets for tabs
        self.overview_widget = QWidget()
        self.overview_layout = QVBoxLayout(self.overview_widget)
        self.overview_layout.setContentsMargins(15, 15, 15, 15)
        self.overview_layout.setSpacing(12)

        # Context line (range/interface/updated) - filled in by GraphWindow on updates.
        self.overview_meta_label = QLabel("")
        self.overview_meta_label.setObjectName("overviewMetaLabel")
        self.overview_meta_label.setStyleSheet("""
            QLabel#overviewMetaLabel {
                color: rgba(220, 220, 220, 0.55);
                font-size: 11px;
                font-weight: 500;
                font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
                padding: 2px 2px 0px 2px;
            }
        """)
        self.overview_layout.addWidget(self.overview_meta_label)
        
        self.dashboard_grid = QGridLayout()
        self.dashboard_grid.setSpacing(12)
        self.dashboard_grid.setColumnStretch(0, 1)
        self.dashboard_grid.setColumnStretch(1, 1)
        self.overview_layout.addLayout(self.dashboard_grid)

        self.card_net_down = OverviewMetricCard(self.i18n.DOWNLOAD_LABEL, accent_color=constants.graph.DOWNLOAD_LINE_COLOR)
        self.card_net_up = OverviewMetricCard(self.i18n.UPLOAD_LABEL, accent_color=constants.graph.UPLOAD_LINE_COLOR)
        self.card_cpu_frame = OverviewMetricCard(self.i18n.ORDER_TYPE_CPU, accent_color=constants.graph.CPU_LINE_COLOR)
        self.card_gpu_frame = OverviewMetricCard(self.i18n.ORDER_TYPE_GPU, accent_color=constants.graph.GPU_LINE_COLOR)

        # For backward compatibility with existing update code in GraphWindow
        self.card_net_down_val = self.card_net_down.value_lbl
        self.card_net_up_val = self.card_net_up.value_lbl
        self.card_cpu_val = self.card_cpu_frame.value_lbl
        self.card_gpu_val = self.card_gpu_frame.value_lbl
        self.card_net_down_sub = self.card_net_down.sub_lbl
        self.card_net_up_sub = self.card_net_up.sub_lbl
        self.card_cpu_sub = self.card_cpu_frame.sub_lbl
        self.card_gpu_sub = self.card_gpu_frame.sub_lbl

        self.dashboard_grid.addWidget(self.card_net_down, 0, 0)
        self.dashboard_grid.addWidget(self.card_net_up, 0, 1)
        self.dashboard_grid.addWidget(self.card_cpu_frame, 1, 0)
        self.dashboard_grid.addWidget(self.card_gpu_frame, 1, 1)
        
        # Plot area: the shared Matplotlib canvas is reparented here when Overview is active.
        self.overview_plot_container = QWidget()
        self.overview_plot_container.setObjectName("overviewPlotContainer")
        self.overview_plot_container.setStyleSheet("""
            QWidget#overviewPlotContainer {
                background: rgba(20, 20, 20, 0.35);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 10px;
            }
        """)
        self.overview_plot_layout = QVBoxLayout(self.overview_plot_container)
        self.overview_plot_layout.setContentsMargins(10, 8, 10, 8)
        self.overview_plot_layout.setSpacing(0)
        self.overview_layout.addWidget(self.overview_plot_container, 1)

        self.tab_widget.addTab(self.overview_widget, getattr(self.i18n, "OVERVIEW_TAB_LABEL", "Overview"))

        self.graph_widget = QWidget()
        self.graph_layout = QVBoxLayout(self.graph_widget)
        self.graph_layout.setContentsMargins(15, 15, 15, 15)
        self.graph_layout.setSpacing(12)

        self.graph_plot_container = QWidget()
        self.graph_plot_container.setObjectName("graphPlotContainer")
        self.graph_plot_container.setStyleSheet(self.overview_plot_container.styleSheet())
        self.graph_plot_layout = QVBoxLayout(self.graph_plot_container)
        self.graph_plot_layout.setContentsMargins(10, 8, 10, 8)
        self.graph_plot_layout.setSpacing(0)
        self.graph_layout.addWidget(self.graph_plot_container, 1)

        self.tab_widget.addTab(self.graph_widget, self.i18n.SPEED_GRAPH_TAB_LABEL)
        
        # CPU tab
        self.cpu_widget = QWidget()
        self.cpu_layout = QVBoxLayout(self.cpu_widget)
        self.cpu_layout.setContentsMargins(15, 15, 15, 15)
        self.cpu_layout.setSpacing(12)

        self.cpu_plot_container = QWidget()
        self.cpu_plot_container.setObjectName("cpuPlotContainer")
        self.cpu_plot_container.setStyleSheet(self.overview_plot_container.styleSheet())
        self.cpu_plot_layout = QVBoxLayout(self.cpu_plot_container)
        self.cpu_plot_layout.setContentsMargins(10, 8, 10, 8)
        self.cpu_plot_layout.setSpacing(0)
        self.cpu_layout.addWidget(self.cpu_plot_container, 1)

        self.tab_widget.addTab(self.cpu_widget, self.i18n.ORDER_TYPE_CPU)
        
        # GPU tab
        self.gpu_widget = QWidget()
        self.gpu_layout = QVBoxLayout(self.gpu_widget)
        self.gpu_layout.setContentsMargins(15, 15, 15, 15)
        self.gpu_layout.setSpacing(12)

        self.gpu_plot_container = QWidget()
        self.gpu_plot_container.setObjectName("gpuPlotContainer")
        self.gpu_plot_container.setStyleSheet(self.overview_plot_container.styleSheet())
        self.gpu_plot_layout = QVBoxLayout(self.gpu_plot_container)
        self.gpu_plot_layout.setContentsMargins(10, 8, 10, 8)
        self.gpu_plot_layout.setSpacing(0)
        self.gpu_layout.addWidget(self.gpu_plot_container, 1)

        self.tab_widget.addTab(self.gpu_widget, self.i18n.ORDER_TYPE_GPU)
        
        # Ensure tab bar is visible
        self.tab_widget.tabBar().setVisible(True)

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

            self.max_stat_title, self.max_stat_val = self._create_stat_card(stats_layout, self.i18n.STAT_MAX_SPEED)
            self.avg_stat_title, self.avg_stat_val = self._create_stat_card(stats_layout, self.i18n.STAT_AVG_SPEED)
            self.total_stat_title, self.total_stat_val = self._create_stat_card(stats_layout, self.i18n.STAT_TOTAL_DATA)
            
            # 2. Loading Indicator (Pulse Widget) - NOW INSIDE STATS BAR
            stats_layout.addStretch() 
            self.loading_indicator = StatusIndicatorWidget(self.stats_bar, i18n=self.i18n)
            stats_layout.addWidget(self.loading_indicator) 
            
            header_layout.addWidget(self.stats_bar, 1)

            # 3. Reset View Button
            self.reset_zoom_btn = QPushButton(f"⟲ {self.i18n.BUTTON_RESET_VIEW}", self.header_widget)
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
            self.zoom_hint_label = QLabel(self.i18n.ZOOM_HINT, self.graph_widget)
            self.zoom_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            try:
                self.zoom_hint_label.setStyleSheet(style_utils.zoom_hint_style())
            except AttributeError:
                self.zoom_hint_label.setStyleSheet("QLabel { background: rgba(0,0,0,0.6); color: white; border-radius: 12px; padding: 4px 12px; }")
            self.zoom_hint_label.hide()

        except Exception as e:
            self.logger.error(f"Error initializing overlay elements: {e}", exc_info=True)

    def _create_stat_card(self, parent_layout: QHBoxLayout, title_text: str) -> Tuple[QLabel, QLabel]:
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
        return title_lbl, value_lbl

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
        """Displays a message overlay (errors) or updates the compact status indicator (non-errors)."""
        if not is_error:
            self.set_status(message)
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

    def set_status(self, state_or_message: str) -> None:
        """
        Sets the compact status indicator state.

        Callers should prefer passing explicit state names: LIVE, COLLECTING, NO_DATA.
        For backwards-compatibility, we also map known localized messages.
        """
        if not hasattr(self, "loading_indicator") or self.loading_indicator is None:
            return

        normalized = (state_or_message or "").strip()
        state = None

        if normalized in StatusIndicatorWidget.STATES:
            state = normalized
        else:
            # Map localized messages (do not rely on English substring parsing).
            try:
                if normalized == self.i18n.NO_DATA_MESSAGE:
                    state = "NO_DATA"
                elif normalized == self.i18n.COLLECTING_DATA_MESSAGE:
                    state = "COLLECTING"
            except Exception:
                state = None

        if state is None:
            state = "LIVE"

        self.loading_indicator.setStatus(state)
        self.loading_indicator.show()

    def hide_graph_message(self):
        """Hides the large message overlay (status indicator remains available)."""
        self._graph_message_label.hide()

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
