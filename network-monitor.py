# Standard library imports
import os
import sys
import json
import re
import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Dict
import winreg

# Third-party imports
import win32gui
import win32con
import psutil
import matplotlib
matplotlib.use('qtagg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

# Qt imports - grouped by functionality
from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QFont, QPainter, QColor, QFontMetrics, QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMenu, QDialog,
    QVBoxLayout, QGridLayout, QGroupBox,
    QCheckBox, QSpinBox, QDoubleSpinBox,
    QLabel, QPushButton, QColorDialog
)

# Constants

APP_NAME = "NetSpeedTray"
APP_VERSION = "1.0.0"
DEFAULT_FONT = "Segoe UI"
DEFAULT_FONT_SIZE = 9
DEFAULT_UPDATE_RATE = 1
MAX_HISTORY_POINTS = 3600  # Maximum points to store in memory
MAX_GRAPH_POINTS = 1000   # Maximum points to display on graph

# Default configuration with documentation
DEFAULT_CONFIG = {
    'color_coding': False,          # Enable/disable speed color coding
    'high_speed_threshold': 1.0,    # Threshold for high speed in Mbps
    'low_speed_threshold': 0.1,     # Threshold for low speed in Mbps
    'high_speed_color': '#00FF00',  # Color for high speed (green)
    'low_speed_color': '#FFA500',   # Color for low speed (orange)
    'default_color': '#FFFFFF',     # Default text color (white)
    'graph_enabled': False,         # Enable/disable speed graph
    'history_hours': 1,             # Hours of history to keep
    'update_rate': DEFAULT_UPDATE_RATE,  # Update interval in seconds
    'position_x': None,             # Last X position on screen
    'position_y': None,             # Last Y position on screen
    'start_with_windows': False     # Start application with Windows
}

def get_app_data_path():
    """Returns path to user's AppData/NetSpeedTray directory, creates if not exists"""
    app_data = os.path.join(os.getenv('APPDATA'), 'NetSpeedTray')
    if not os.path.exists(app_data):
        os.makedirs(app_data)
    return app_data

class NetworkMonitorError(Exception):
    """Base exception for all NetworkMonitor errors."""
    pass

class ConfigError(NetworkMonitorError):
    """Configuration validation or loading errors."""
    pass

class NetworkSpeedWidget(QWidget):
    """
    Main widget that displays network speed in the Windows taskbar.
    
    This widget shows real-time upload and download speeds, with optional
    color coding based on speed thresholds and a graph view of speed history.
    
    Attributes:
        upload_speed (float): Current upload speed in bytes/sec
        download_speed (float): Current download speed in bytes/sec
        speed_history (list): List of historical speed measurements
        config (dict): Widget configuration settings
    """
    
    def __init__(self) -> None:
        super().__init__(None)
        self._init_icon()
        self._init_screen_handling()
        self._init_config()
        self._init_data()
        self._init_ui()
        self._init_logging()

    def _init_icon(self):
        """Initialize application icon from ico file."""
        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'NetSpeedTray.ico')
            if os.path.exists(icon_path):
                self.app_icon = QIcon(icon_path)
                self.setWindowIcon(self.app_icon)
                QApplication.setWindowIcon(self.app_icon)
            else:
                print(f"Icon file not found: {icon_path}")
        except Exception as e:
            print(f"Failed to load icon: {e}")

    def _init_screen_handling(self):
        self.taskbar_hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        if self.taskbar_hwnd:
            self.setParent(QWidget.find(self.taskbar_hwnd))
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.SubWindow
        )
        
        self.dragging = False
        self.offset = QPoint()
        self.settings_file = os.path.join(os.path.expanduser('~'), '.networkmonitor_settings.json')
        
        # Add screen change detection
        QApplication.primaryScreen().geometryChanged.connect(self.handle_screen_change)
        QApplication.primaryScreen().availableGeometryChanged.connect(self.handle_screen_change)

    def _init_config(self):
        app_data = get_app_data_path()
        self.config_file = os.path.join(app_data, 'netspeedtray.conf')
        self.log_file = os.path.join(app_data, 'netspeedtray.log')
        self.config = self.load_config()

    def _init_ui(self):
        self.setAttributes()
        self.setupUI()
        self.setupTimers()

    def _init_data(self):
        self.upload_speed = self.download_speed = 0
        self.last_upload = self.last_download = 0
        self.last_time = datetime.now()
        self.speed_history = []
        self.graph_window = None
    
    def _init_logging(self) -> None:
        try:
            handler = RotatingFileHandler(
                self.log_file,
                maxBytes=self.config.get('max_log_size_mb', 10) * 1024 * 1024,
                backupCount=3
            )
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(logging.ERROR)
            self.logger.addHandler(handler)
        except Exception as e:
            print(f"Failed to setup logging: {e}")

    def setAttributes(self):
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

    def setupUI(self):
        font = QFont(DEFAULT_FONT, DEFAULT_FONT_SIZE)
        metrics = QFontMetrics(font)
        max_text_width = metrics.horizontalAdvance("↑ 999.9 Mb/s") + 10
        taskbar_height = self.get_taskbar_height()
        self.setFixedSize(max_text_width, taskbar_height)
        
        self.context_menu = QMenu(self)
        self.context_menu.addAction("Settings", self.show_settings)
        self.context_menu.addSeparator()
        self.context_menu.addAction("Exit", self.close)

    def setupTimers(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_stats)
        update_rate = self.config.get('update_rate', DEFAULT_UPDATE_RATE)
        # Use max to prevent negative or zero intervals
        self.timer.start(max(50, update_rate * 1000) if update_rate > 0 else 50)
        
        self.keep_alive_timer = QTimer()
        self.keep_alive_timer.timeout.connect(self.ensure_visibility)
        self.keep_alive_timer.start(50)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.offset = event.position().toPoint()
            event.accept()
    
    def mouseMoveEvent(self, event):
        if not self.dragging:
            return
        
        new_pos = event.globalPosition().toPoint() - self.offset
        if self.taskbar_hwnd:
            taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
            new_pos.setY(taskbar_rect[1])
        self.move(new_pos)
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.save_position()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        self._draw_background(painter)
        if self.config.get('graph_enabled', False):
            self._draw_graph(painter)
        self._draw_speed_text(painter)
    
    def contextMenuEvent(self, event):
        """Handle right-click events and show context menu centered above the widget"""
        # Get widget position and dimensions
        widget_pos = self.mapToGlobal(QPoint(0, 0))
        widget_width = self.width()
        menu_width = self.context_menu.sizeHint().width()
        menu_height = self.context_menu.sizeHint().height()
        
        # Calculate menu position (centered horizontally above widget)
        menu_x = widget_pos.x() + (widget_width // 2) - (menu_width // 2)
        menu_y = widget_pos.y() - menu_height
        
        # Ensure menu stays on screen
        screen = QApplication.primaryScreen().geometry()
        menu_x = max(0, min(menu_x, screen.width() - menu_width))
        menu_y = max(0, menu_y)
        
        # Show menu at calculated position
        self.context_menu.exec(QPoint(menu_x, menu_y))
        event.accept()
    
    def closeEvent(self, event) -> None:
        """Handle cleanup when closing the application."""
        try:
            self.save_position()
            self.save_config()
            if self.graph_window:
                self.graph_window.close()
                matplotlib.pyplot.close('all')  # Clean up matplotlib resources
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")  # Keep error logging
        finally:
            QApplication.quit()

    def _draw_background(self, painter):
        # Fill entire widget area with a fully transparent color
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
    
    def _draw_graph(self, painter):
        if self.speed_history:
            graph_width = self.width()
            graph_height = self.height()
            
            # Get recent history points
            history_points = self.speed_history[-60:]  # Last 60 points
            if history_points:
                max_speed = max(
                    max(point['upload'] for point in history_points),
                    max(point['download'] for point in history_points)
                )
                if max_speed > 0:
                    # Draw upload line
                    painter.setPen(QColor(0, 255, 0, 128))
                    for i in range(len(history_points) - 1):
                        x1 = i * graph_width / (len(history_points) - 1)
                        x2 = (i + 1) * graph_width / (len(history_points) - 1)
                        y1 = graph_height * (1 - history_points[i]['upload'] / max_speed)
                        y2 = graph_height * (1 - history_points[i + 1]['upload'] / max_speed)
                        painter.drawLine(int(x1), int(y1), int(x2), int(y2))
                    
                    # Draw download line
                    painter.setPen(QColor(0, 120, 255, 128))
                    for i in range(len(history_points) - 1):
                        x1 = i * graph_width / (len(history_points) - 1)
                        x2 = (i + 1) * graph_width / (len(history_points) - 1)
                        y1 = graph_height * (1 - history_points[i]['download'] / max_speed)
                        y2 = graph_height * (1 - history_points[i + 1]['download'] / max_speed)
                        painter.drawLine(int(x1), int(y1), int(x2), int(y2))
    
    def _draw_speed_text(self, painter):
        font = QFont(DEFAULT_FONT, DEFAULT_FONT_SIZE)
        painter.setFont(font)
        
        metrics = painter.fontMetrics()
        text_height = metrics.height()
        widget_height = self.height()
        
        arrow_width = metrics.horizontalAdvance("↑ ")
        content_width = self.width() - 10
        
        top_text_y = int(widget_height/2 - text_height - 2)
        bottom_text_y = int(widget_height/2 + 2)
        
        up_speed = self.format_speed(self.upload_speed)
        up_color = self.get_speed_color(self.upload_speed)
        painter.setPen(up_color)
        painter.drawText(5, top_text_y + metrics.ascent(), "↑")
        painter.drawText(5 + arrow_width, top_text_y + metrics.ascent(), up_speed)
        
        down_speed = self.format_speed(self.download_speed)
        down_color = self.get_speed_color(self.download_speed)
        painter.setPen(down_color)
        painter.drawText(5, bottom_text_y + metrics.ascent(), "↓")
        painter.drawText(5 + arrow_width, bottom_text_y + metrics.ascent(), down_speed)
    
    def save_position(self):
        self.config['position_x'] = self.x()
        self.config['position_y'] = self.y()
        self.save_config()

    def load_position(self):
        x = self.config.get('position_x')
        y = self.config.get('position_y')
        if x is not None and y is not None:
            self.move(x, y)
        else:
            self.set_default_position()
    
    def set_default_position(self):
        """Position widget on taskbar to the left of system tray"""
        if self.taskbar_hwnd:
            try:
                # Get screen dimensions
                screen = QApplication.primaryScreen().geometry()
                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
                
                tray = win32gui.FindWindow("TrayNotifyWnd", None)
                chevron = win32gui.FindWindowEx(tray, 0, "Button", None)
                
                if tray and chevron:
                    chevron_rect = win32gui.GetWindowRect(chevron)
                    x = chevron_rect[0] - self.width() - 5  # 5px padding
                else:
                    # Fallback: position near right edge
                    x = screen.width() - self.width() - 200  # 200px from right edge
                
                # Set Y position to taskbar height
                y = taskbar_rect[1]
                
                # Ensure position is valid
                x = max(0, min(x, screen.width() - self.width()))
                
                self.move(x, y)
                
            except Exception as e:
                # Final fallback
                screen = QApplication.primaryScreen().geometry()
                x = screen.width() - self.width() - 100
                y = taskbar_rect[1] if self.taskbar_hwnd else screen.height() - self.height()
                self.move(x, y)

    def ensure_on_screen(self):
        """Ensure widget stays within screen boundaries"""
        screen = QApplication.primaryScreen().geometry()
        current_pos = self.pos()
        new_x = max(0, min(current_pos.x(), screen.width() - self.width()))
        new_y = current_pos.y()
        
        if new_x != current_pos.x():
            self.move(new_x, new_y)
            self.save_position()

    def recover_position(self):
        """Recover widget position after screen changes"""
        try:
            if self.taskbar_hwnd:
                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
                self.move(self.config.get('position_x'), taskbar_rect[1])
        except Exception:
            self.set_default_position()
    
    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    return self.validate_config({**DEFAULT_CONFIG, **json.load(f)})
        except Exception as e:
            print(f"Error loading config: {e}")
        return DEFAULT_CONFIG

    def save_config(self):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    def validate_config(self, config: Dict) -> Dict:
        """
        Validate and sanitize configuration values.
        
        Args:
            config: Dictionary containing configuration values
            
        Returns:
            Dictionary with validated configuration values
            
        Raises:
            ConfigError: If configuration validation fails
        """
        try:
            validated = config.copy()
            
            # Validate numerical values
            for key, (min_val, max_val, default) in {
                'update_rate': (0, 60, 1),
                'high_speed_threshold': (0.1, 1000.0, 1.0),
                'low_speed_threshold': (0.0, 999.9, 0.1),
                'history_hours': (1, 24, 1)
            }.items():
                validated[key] = max(min_val, min(max_val, config.get(key, default)))
            
            # Validate colors
            for color_key in ['high_speed_color', 'low_speed_color', 'default_color']:
                color = config.get(color_key, '#FFFFFF')
                if not re.match(r'^#(?:[0-9a-fA-F]{3}){1,2}$', color):
                    validated[color_key] = '#FFFFFF'
            
            return validated
        except Exception as e:
            raise ConfigError(f"Configuration validation failed: {e}")
    
    def update_stats(self):
        """
    Update network speed statistics.
    
    Calculates current upload and download speeds based on bytes
    transferred since last update. If graph is enabled, maintains
    a history of speed measurements for graphing.
    
    Handles:
    - Speed calculation from bytes transferred
    - History maintenance for graphing
    - Memory optimization by limiting stored points
        """
        try:
            net_stats = psutil.net_io_counters()
            current_time = datetime.now()
            
            # Calculate speeds
            time_diff = (current_time - self.last_time).total_seconds()
            if time_diff > 0:
                self.upload_speed = (net_stats.bytes_sent - self.last_upload) / time_diff
                self.download_speed = (net_stats.bytes_recv - self.last_download) / time_diff
                
                # Update history if graph is enabled
                if self.config.get('graph_enabled', False):
                    self.speed_history.append({
                        'timestamp': current_time,
                        'upload': self.upload_speed,
                        'download': self.download_speed
                    })
                    
                    # Cleanup old history
                    cutoff = current_time - timedelta(hours=self.config.get('history_hours', 1))
                    self.speed_history = [x for x in self.speed_history if x['timestamp'] > cutoff]
                    
                    # Optimize memory usage by limiting history points
                    if len(self.speed_history) > MAX_HISTORY_POINTS:
                        self.speed_history = self.speed_history[-MAX_HISTORY_POINTS:]
                    
                    if len(self.speed_history) > MAX_GRAPH_POINTS:
                        # Keep one point per (points/max_points) points
                        step = len(self.speed_history) // MAX_GRAPH_POINTS
                        self.speed_history = self.speed_history[::step]
            
            self.last_upload = net_stats.bytes_sent
            self.last_download = net_stats.bytes_recv
            self.last_time = current_time
            
            self.update()
        except Exception as e:
            self.logger.error(f"Error updating stats: {e}")
    
    def format_speed(self, bytes_per_sec):
        bits_per_sec = bytes_per_sec * 8
        if bits_per_sec >= 1000000:
            return f"{bits_per_sec / 1000000:5.1f} Mb/s"
        elif bits_per_sec >= 1000:
            return f"{bits_per_sec / 1000:5.1f} Kb/s"
        else:
            return f"{bits_per_sec:5.0f} b/s"
    
    def get_speed_color(self, speed: float) -> QColor:
        """
        Determine the color for the given speed based on thresholds.
        
        Args:
            speed: Network speed in bytes per second
            
        Returns:
            QColor object representing the appropriate color
        """
        if not self.config.get('color_coding', False):
            return QColor(self.config.get('default_color', '#FFFFFF'))
        
        speed_mbps = speed * 8 / 1000000
        thresholds = [
            (self.config.get('high_speed_threshold', 1), self.config.get('high_speed_color', '#00FF00')),
            (self.config.get('low_speed_threshold', 0.1), self.config.get('low_speed_color', '#FFA500'))
        ]
        
        for threshold, color in thresholds:
            if speed_mbps > threshold:
                return QColor(color)
        return QColor(self.config.get('default_color', '#FFFFFF'))

    def show_settings(self):
        dialog = SettingsDialog(self, self.config)
        
        # Center dialog above widget horizontally and position above taskbar
        widget_pos = self.mapToGlobal(QPoint(0, 0))
        widget_width = self.width()
        dialog_width = dialog.sizeHint().width()
        dialog_height = dialog.sizeHint().height()
        
        dialog_x = widget_pos.x() + (widget_width // 2) - (dialog_width // 2)
        
        taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
        dialog_y = taskbar_rect[1] - dialog_height - 5  # Position just above taskbar with 5px padding
        
        screen = QApplication.primaryScreen().geometry()
        dialog_x = max(0, min(dialog_x, screen.width() - dialog_width))
        dialog_y = max(0, min(dialog_y, screen.height() - dialog_height))
        
        dialog.move(dialog_x, dialog_y)
        
        if dialog.exec():
            old_config = self.config.copy()
            self.config = dialog.get_settings()
            self.save_config()
            
            # Update timer interval if changed
            new_rate = self.config['update_rate']
            self.timer.setInterval(50 if new_rate == 0 else new_rate * 1000)

    def setup_logging(self):
        """Enhanced logging setup"""
        try:
            handler = logging.RotatingFileHandler(
                self.log_file,
                maxBytes=self.config.get('max_log_size_mb', 10) * 1024 * 1024,
                backupCount=3
            )
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
            )
            handler.setFormatter(formatter)
            logger = logging.getLogger()
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
        except Exception as e:
            print(f"Failed to setup logging: {e}")

    def show_graph(self):
        """Opens a window displaying network speed history graph if data is available"""
        if not self.graph_window:
            self.graph_window = GraphWindow()
        
        if self.speed_history:
            self.graph_window.update_graph(self.speed_history)
            self.graph_window.show()
        else:
            print("No history data available")

    def handle_screen_change(self, geometry):
        """Adjusts widget position when screen resolution or configuration changes"""
        try:
            # Wait briefly for display to stabilize (explains the delay)
            QTimer.singleShot(500, self.recover_position)
        except Exception as e:
            print(f"Screen change error: {e}")
            self.set_default_position()

    def ensure_visibility(self):
        if self.isVisible():
            try:
                current_pos = self.pos()
                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
                
                if (current_pos.y() != taskbar_rect[1]):
                    x = self.config.get('position_x')
                    y = self.config.get('position_y')
                    if x is not None and y is not None:
                        self.move(x, y)
                    else:
                        self.set_default_position()
                
                win32gui.SetWindowPos(
                    int(self.winId()),
                    win32con.HWND_TOPMOST,
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
                )
            except Exception as e:
                self.load_position()
    
    def get_taskbar_height(self):
        if self.taskbar_hwnd:
            rect = win32gui.GetWindowRect(self.taskbar_hwnd)
            return rect[3] - rect[1]
        return 40

    def toggle_startup(self):
        """
        Toggle application startup with Windows.
        
        Returns:
            bool: True if startup is enabled, False if disabled or on error
        
        Raises:
            WindowsError: If registry access fails
        """
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "NetSpeedTray"
        
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            try:
                winreg.QueryValueEx(key, app_name)
                # Key exists, remove it
                winreg.DeleteValue(key, app_name)
                return False
            except WindowsError:
                # Key doesn't exist, add it
                exe_path = sys.executable if getattr(sys, 'frozen', False) else sys.argv[0]
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe_path}"')
                return True
            finally:
                key.Close()
        except Exception as e:
            self.logger.error(f"Failed to toggle startup: {str(e)}")
            return False

    def is_startup_enabled(self):
        """Check if application is set to start with Windows"""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "NetSpeedTray"
        
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, app_name)
                return True
            except WindowsError:
                return False
            finally:
                key.Close()
        except Exception:
            return False

class SettingsDialog(QDialog):
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.config = config or {}
        self.setWindowTitle(f"{APP_NAME} Settings v{APP_VERSION}")
        # Set the icon for the settings dialog
        if hasattr(parent, 'app_icon'):
            self.setWindowIcon(parent.app_icon)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()
        
        # General settings group
        general_group = QGroupBox("General Settings")
        general_layout = QGridLayout()
        
        # Update rate settings
        self.update_rate = QSpinBox()
        self.update_rate.setRange(0, 60)
        self.update_rate.setValue(self.config.get('update_rate', 0))
        self.update_rate.setSpecialValueText("Live")
        self.update_rate.setSuffix(" seconds")
        
        general_layout.addWidget(QLabel("Update Rate:"), 0, 0)
        general_layout.addWidget(self.update_rate, 0, 1)

        # Start with Windows checkbox
        self.start_with_windows = QCheckBox("Start with Windows")
        start_with_windows_enabled = False
        if hasattr(self.parent(), 'is_startup_enabled'):
            start_with_windows_enabled = self.parent().is_startup_enabled()
        self.start_with_windows.setChecked(start_with_windows_enabled)
        general_layout.addWidget(self.start_with_windows, 1, 0, 1, 2)

        general_group.setLayout(general_layout)
        layout.addWidget(general_group)

        # Color coding settings group
        color_group = QGroupBox("Speed Color Coding")
        color_layout = QGridLayout()
        
        self.enable_colors = QCheckBox("Enable Color Coding")
        self.enable_colors.setChecked(self.config.get('color_coding', False))
        color_layout.addWidget(self.enable_colors, 0, 0, 1, 2)
        
        self.high_speed_threshold = QDoubleSpinBox()
        self.high_speed_threshold.setRange(0.1, 1000.0)
        self.high_speed_threshold.setValue(self.config.get('high_speed_threshold', 1.0))
        self.high_speed_threshold.setSuffix(" Mbps")
        
        self.low_speed_threshold = QDoubleSpinBox()
        self.low_speed_threshold.setRange(0.0, 999.9)
        self.low_speed_threshold.setValue(self.config.get('low_speed_threshold', 0.1))
        self.low_speed_threshold.setSuffix(" Mbps")
        
        color_layout.addWidget(QLabel("High Speed Threshold:"), 1, 0)
        color_layout.addWidget(self.high_speed_threshold, 1, 1)
        
        color_layout.addWidget(QLabel("Low Speed Threshold:"), 2, 0)
        color_layout.addWidget(self.low_speed_threshold, 2, 1)
        
        self.high_speed_color = QPushButton()
        self.high_speed_color.setStyleSheet(f"background-color: {self.config.get('high_speed_color', '#00FF00')}")
        self.high_speed_color.clicked.connect(lambda: self.choose_color(self.high_speed_color))
        
        self.low_speed_color = QPushButton()
        self.low_speed_color.setStyleSheet(f"background-color: {self.config.get('low_speed_color', '#FFA500')}")
        self.low_speed_color.clicked.connect(lambda: self.choose_color(self.low_speed_color))
        
        self.default_color = QPushButton()
        self.default_color.setStyleSheet(f"background-color: {self.config.get('default_color', '#FFFFFF')}")
        self.default_color.clicked.connect(lambda: self.choose_color(self.default_color))
        
        color_layout.addWidget(QLabel("High Speed Color:"), 3, 0)
        color_layout.addWidget(self.high_speed_color, 3, 1)
        
        color_layout.addWidget(QLabel("Low Speed Color:"), 4, 0)
        color_layout.addWidget(self.low_speed_color, 4, 1)
        
        color_layout.addWidget(QLabel("Default Color:"), 5, 0)
        color_layout.addWidget(self.default_color, 5, 1)
        
        color_group.setLayout(color_layout)
        layout.addWidget(color_group)
        
        # Graph settings group
        graph_group = QGroupBox("Graph Settings")
        graph_layout = QGridLayout()
        
        self.enable_graph = QCheckBox("Enable Graph")
        self.enable_graph.setChecked(self.config.get('graph_enabled', False))
        graph_layout.addWidget(self.enable_graph, 0, 0, 1, 2)
        
        self.history_hours = QSpinBox()
        self.history_hours.setRange(1, 24)
        self.history_hours.setValue(self.config.get('history_hours', 1))
        self.history_hours.setSuffix(" hours")
        
        graph_layout.addWidget(QLabel("History Duration:"), 1, 0)
        graph_layout.addWidget(self.history_hours, 1, 1)
        
        graph_group.setLayout(graph_layout)
        layout.addWidget(graph_group)
        
        # Buttons
        buttons = QVBoxLayout()
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.accept)
        buttons.addWidget(save_button)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        
        layout.addLayout(buttons)
        
        self.setLayout(layout)

    def choose_color(self, button):
        color = QColorDialog.getColor()
        if (color.isValid()):
            button.setStyleSheet(f"background-color: {color.name()}")

    def get_settings(self):
        settings = {
            'update_rate': self.update_rate.value(),
            'color_coding': self.enable_colors.isChecked(),
            'high_speed_threshold': self.high_speed_threshold.value(),
            'low_speed_threshold': self.low_speed_threshold.value(),
            'high_speed_color': self.high_speed_color.palette().button().color().name(),
            'low_speed_color': self.low_speed_color.palette().button().color().name(),
            'default_color': self.default_color.palette().button().color().name(),
            'graph_enabled': self.enable_graph.isChecked(),
            'history_hours': self.history_hours.value(),
            'logging_enabled': False,
            'max_log_size_mb': 10,
            'start_with_windows': self.start_with_windows.isChecked()
        }
        
        # Handle startup setting
        if self.start_with_windows.isChecked() != self.parent().is_startup_enabled():
            self.parent().toggle_startup()
        
        return settings

class GraphWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Network Speed History")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        
        layout = QVBoxLayout()
        
        self.figure = Figure(figsize=(8, 6))
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)
        
        self.setLayout(layout)
        self.resize(800, 600)
        
        screen = QApplication.primaryScreen().geometry()
        self.move(
            int((screen.width() - self.width()) / 2),
            int((screen.height() - self.height()) / 2)
        )

    def update_graph(self, history):
        if not history:
            return
            
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        
        times = [x['timestamp'] for x in history]
        upload = [x['upload']*8/1000000 for x in history]  # Convert to Mbps (explains the math)
        download = [x['download']*8/1000000 for x in history]  # Convert to Mbps
        
        ax.plot(times, upload, label='Upload (Mbps)', color='green', linewidth=2)
        ax.plot(times, download, label='Download (Mbps)', color='blue', linewidth=2)
        
        ax.set_xlabel('Time')
        ax.set_ylabel('Speed (Mbps)')
        ax.grid(True, linestyle='--', alpha=0)
        ax.legend(loc='upper left')
        
        self.figure.autofmt_xdate()
        
        self.figure.tight_layout()
        
        self.canvas.draw()

class NetworkMonitor:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.widget = NetworkSpeedWidget()
        self.widget.load_position()
        self.widget.show()
    
    def run(self):
        return self.app.exec()

if __name__ == "__main__":
    monitor = NetworkMonitor()
    sys.exit(monitor.run())

# TODO: Add network interface selection
# TODO: Implement data export functionality
# TODO: Add bandwidth alerts
# TODO: Implement automatic updates