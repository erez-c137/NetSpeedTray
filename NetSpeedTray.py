# Standard library imports
import os
import sys
import json
import re
import logging
import traceback
import winreg
from datetime import datetime, timedelta
from typing import Dict
from collections import deque

# Third-party imports
import gc
import matplotlib
import psutil
import signal
import win32api
import win32con
import win32event
import win32gui
import winerror

matplotlib.use('qtagg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import matplotlib.pyplot as plt

# PyQt6 imports
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import QFont, QPainter, QColor, QFontMetrics, QIcon, QContextMenuEvent, QAction
from PyQt6.QtWidgets import (QApplication, QMenu, QDialog, QVBoxLayout, QGridLayout, QGroupBox, QCheckBox, 
                             QSpinBox, QDoubleSpinBox, QPushButton, QLabel, QComboBox, 
                             QWidget, QFileDialog, QMessageBox, QColorDialog)

# Logging handlers
from logging.handlers import RotatingFileHandler

# Constants
APP_NAME = "NetSpeedTray"
APP_VERSION = "1.0.2"
DEFAULT_FONT = "Segoe UI"
DEFAULT_FONT_SIZE = 9
DEFAULT_UPDATE_RATE = 2
MAX_HISTORY_POINTS = 600
LOG_FORMAT = '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
ERROR_LOG_FILE = os.path.join(os.getenv('APPDATA'), 'NetSpeedTray', 'error.log')
MAX_LOG_SIZE = 10 * 1024 * 1024
MAX_LOG_FILES = 3
TIME_INTERVALS = [
    (5, "5 mins"), (10, "10 mins"), (15, "15 mins"), (30, "30 mins"),
    (60, "1 hour"), (120, "2 hours"), (180, "3 hours"), (240, "4 hours"),
    (300, "5 hours"), (360, "6 hours"), (720, "12 hours"), (1440, "24 hours")
]

DEFAULT_CONFIG = {
    'color_coding': False, 'high_speed_threshold': 5.0, 'low_speed_threshold': 1.0,
    'high_speed_color': '#00FF00', 'low_speed_color': '#FFA500', 'default_color': '#FFFFFF',
    'graph_enabled': False, 'history_minutes': 30, 'update_rate': DEFAULT_UPDATE_RATE,
    'position_x': None, 'position_y': None, 'start_with_windows': False,
    'interface_mode': 'all', 'selected_interfaces': []
}

# Utility Functions
def get_app_data_path():
    """Get or create the application data path."""
    app_data = os.path.join(os.getenv('APPDATA'), 'NetSpeedTray')
    if not os.path.exists(app_data):
        os.makedirs(app_data)
    return app_data

def setup_error_logging():
    """Set up error logging with rotation."""
    try:
        handler = RotatingFileHandler(ERROR_LOG_FILE, maxBytes=MAX_LOG_SIZE, backupCount=MAX_LOG_FILES)
        formatter = logging.Formatter(LOG_FORMAT)
        handler.setFormatter(formatter)
        logger = logging.getLogger('NetSpeedTray')
        logger.setLevel(logging.ERROR)
        handler.setLevel(logging.ERROR)
        logger.addHandler(handler)
        return logger
    except Exception as e:
        print(f"Failed to setup logging: {e}")
        return None

# Custom Exceptions
class ConfigError(Exception):
    """Exception for configuration validation or loading errors."""
    pass

# Main Widget Class
class NetworkSpeedWidget(QWidget):
    def __init__(self):
        super().__init__(None)
        # Hide console window and taskbar icon if running with python.exe
        if sys.executable.endswith("python.exe"):
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                # Hide from taskbar
                style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style | win32con.WS_EX_TOOLWINDOW)
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        
        self.logger = setup_error_logging()
        self._init_icon()
        self._init_config()
        self._init_screen_handling()
        self._init_data()
        self._init_ui()
        self.default_color = QColor('#FFFFFF')
        self.high_color = QColor('#00FF00')
        self.low_color = QColor('#FFA500')
        
        self.font = QFont(DEFAULT_FONT, DEFAULT_FONT_SIZE)
        self.setFont(self.font)
        self.metrics = QFontMetrics(self.font)
        self.arrow_width = self.metrics.horizontalAdvance("↑ ")
        
        max_text_width = self.metrics.horizontalAdvance("↑ 999.9 Mb/s") + self.metrics.averageCharWidth()
        taskbar_height = self.get_taskbar_height()
        self.setFixedSize(max_text_width, taskbar_height)
        self.first_update = True
        self.dragging = False
        self.offset = QPoint()
        
        # Delay timers until fully initialized
        QTimer.singleShot(0, self.setupTimers)

    def _init_icon(self):
        """Initialize the application icon."""
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(script_dir, 'NetSpeedTray.ico')
            if os.path.exists(icon_path):
                self.app_icon = QIcon(icon_path)
                self.setWindowIcon(self.app_icon)
                QApplication.setWindowIcon(self.app_icon)
        except Exception as e:
            print(f"Failed to load icon: {e}")
            traceback.print_exc()

    def _init_screen_handling(self):
        """Initialize screen and taskbar handling."""
        self.taskbar_hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        self.tray_hwnd = win32gui.FindWindowEx(self.taskbar_hwnd, None, "TrayNotifyWnd", None)
        if not self.taskbar_hwnd or not self.tray_hwnd:
            print("Error: Could not find taskbar or tray window!")
            sys.exit(1)
        
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.update_position()
        self.show()
        win32gui.SetWindowLong(int(self.winId()), win32con.GWL_EXSTYLE, win32con.WS_EX_TOOLWINDOW)
        win32gui.ShowWindow(int(self.winId()), win32con.SW_SHOWNOACTIVATE)

    def _init_config(self):
        """Initialize configuration."""
        app_data = get_app_data_path()
        self.config_file = os.path.join(app_data, 'netspeedtray.conf')
        self.log_file = os.path.join(app_data, 'netspeedtray.log')
        self.config = self.load_config()

    def _init_ui(self):
        """Initialize UI components."""
        self.setupUI()

    def _init_data(self):
        """Initialize data structures."""
        self.upload_speed = self.download_speed = 0
        self.last_upload = self.last_download = 0
        self.last_time = datetime.now()
        self.speed_history = deque(maxlen=MAX_HISTORY_POINTS)
        self.graph_window = None

    def setupUI(self):
        """Set up the user interface."""
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
        """Set up timers for updates."""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_and_update)
        self.timer.start(100)
        
        self.speed_timer = QTimer(self)
        self.speed_timer.timeout.connect(self.update_stats)
        self.speed_timer.start(max(50, self.config.get('update_rate', DEFAULT_UPDATE_RATE) * 1000))
        
        self.force_show_timer = QTimer(self)
        self.force_show_timer.timeout.connect(self.force_show_if_needed)
        self.force_show_timer.start(200)

    def get_taskbar_height(self):
        """Get the height of the taskbar in logical pixels."""
        if self.taskbar_hwnd:
            rect = win32gui.GetWindowRect(self.taskbar_hwnd)
            height = rect[3] - rect[1]
            logical_height = QApplication.primaryScreen().logicalDotsPerInchY() / 96.0
            return int(height / logical_height)
        return 40

    def update_position(self):
        """Update the widget's position on the taskbar."""
        try:
            if not hasattr(self, 'dragging') or getattr(self, 'dragging', False):
                return
            
            tray_rect = win32gui.GetWindowRect(self.tray_hwnd)
            taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
            screen = self.find_taskbar_screen()
            screen_geometry = screen.availableGeometry()
            
            saved_x = self.config.get('position_x')
            if saved_x is not None and taskbar_rect[0] <= saved_x <= screen_geometry.width() - self.width():
                x = saved_x
            else:
                x = tray_rect[0] - self.width() - 5
            
            y = taskbar_rect[1]
            
            x = max(taskbar_rect[0], min(x, screen_geometry.width() - self.width()))
            y = max(taskbar_rect[1], min(y, taskbar_rect[3] - self.height()))
            
            self.move(x, y)
            
            taskbar_z = win32gui.GetWindow(self.taskbar_hwnd, win32con.GW_HWNDPREV)
            win32gui.SetWindowPos(
                int(self.winId()), taskbar_z, x, y, self.width(), self.height(),
                win32con.SWP_NOACTIVATE
            )
            self.taskbar_y = y
        except Exception as e:
            print(f"Error positioning widget: {e}")
            self.move(100, 100)

    def find_taskbar_screen(self):
        """Find the screen containing the taskbar."""
        taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
        for screen in QApplication.screens():
            if screen.geometry().intersects(QRect(taskbar_rect[0], taskbar_rect[1], 
                                                  taskbar_rect[2] - taskbar_rect[0], 
                                                  taskbar_rect[3] - taskbar_rect[1])):
                return screen
        return QApplication.primaryScreen()

    def check_and_update(self):
        """Check taskbar visibility and update widget state."""
        try:
            is_taskbar_visible = win32gui.IsWindowVisible(self.taskbar_hwnd)
            if is_taskbar_visible:
                if self.is_fullscreen_app_active():
                    if self.isVisible():
                        self.hide()
                else:
                    self.show()
                    if not self.dragging:
                        self.update_position()
            else:
                if self.isVisible():
                    self.hide()
        except Exception as e:
            print(f"Error in check_and_update: {e}")

    def force_show_if_needed(self):
        """Force the widget to show if needed."""
        try:
            is_taskbar_visible = win32gui.IsWindowVisible(self.taskbar_hwnd)
            if is_taskbar_visible and not self.is_fullscreen_app_active():
                self.show()
                if not self.dragging:
                    self.update_position()
        except Exception as e:
            print(f"Error in force_show_if_needed: {e}")

    def is_fullscreen_app_active(self):
        """Check if a full-screen application is active."""
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or hwnd == self.winId():
                return False
            rect = win32gui.GetWindowRect(hwnd)
            screen = self.find_taskbar_screen()
            screen_geometry = screen.geometry()
            desktop_hwnd = win32gui.FindWindow("Progman", None) or win32gui.FindWindow("WorkerW", None)
            if hwnd == desktop_hwnd:
                return False
            return (rect[0] <= screen_geometry.x() and
                    rect[2] >= screen_geometry.x() + screen_geometry.width() and
                    rect[1] <= screen_geometry.y() and
                    rect[3] >= screen_geometry.y() + screen_geometry.height())
        except Exception as e:
            print(f"Error checking full-screen: {e}")
            return False

    def mousePressEvent(self, event):
        """Handle mouse press for dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.offset = event.position().toPoint()
            self.timer.stop()
            self.force_show_timer.stop()
            event.accept()

    def mouseMoveEvent(self, event):
        """Handle mouse movement for dragging."""
        if self.dragging:
            try:
                new_pos = event.globalPosition().toPoint() - self.offset
                screen = self.find_taskbar_screen()
                screen_geometry = screen.availableGeometry()
                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
                
                x = max(taskbar_rect[0], min(new_pos.x(), screen_geometry.width() - self.width()))
                y = self.taskbar_y
                
                self.move(x, y)
                event.accept()
            except Exception as e:
                print(f"Mouse move error: {e}")
                self.dragging = False
                self.timer.start(100)
                self.force_show_timer.start(200)

    def mouseReleaseEvent(self, event):
        """Handle mouse release to end dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.save_position()
            self.timer.start(100)
            self.force_show_timer.start(200)
            event.accept()

    def save_position(self):
        """Save the current widget position to config."""
        self.config['position_x'] = self.x()
        self.save_config()

    def update_stats(self):
        """Update network speed statistics."""
        try:
            current_time = datetime.now()
            total_upload = 0
            total_download = 0
            net_stats = psutil.net_io_counters(pernic=True)
            mode = self.config.get('interface_mode', 'all')
            selected = self.config.get('selected_interfaces', [])
            
            for iface, stats in net_stats.items():
                include_interface = (
                    mode == 'all' or
                    (mode == 'include' and iface in selected) or
                    (mode == 'exclude' and iface not in selected)
                )
                if include_interface:
                    last_up = getattr(self, f'last_upload_{iface}', 0)
                    last_down = getattr(self, f'last_download_{iface}', 0)
                    time_diff = (current_time - self.last_time).total_seconds()
                    if not self.first_update and time_diff > 0:
                        total_upload += (stats.bytes_sent - last_up) / time_diff
                        total_download += (stats.bytes_recv - last_down) / time_diff
                    setattr(self, f'last_upload_{iface}', stats.bytes_sent)
                    setattr(self, f'last_download_{iface}', stats.bytes_recv)
            
            if self.first_update:
                self.upload_speed = 0
                self.download_speed = 0
                self.first_update = False
            else:
                self.upload_speed = total_upload
                self.download_speed = total_download
            
            self.last_time = current_time
            if self.config.get('graph_enabled', False) and not self.first_update:
                self.speed_history.append((total_upload, total_download))
            gc.collect()
            self.update()
        except Exception as e:
            self.logger.error(f"Error updating stats: {e}")

    def format_speed(self, bytes_per_sec):
        """Format speed in bits per second."""
        bits_per_sec = bytes_per_sec * 8
        if bits_per_sec >= 1000000:
            return f"{bits_per_sec / 1000000:5.1f} Mb/s"
        elif bits_per_sec >= 1000:
            return f"{bits_per_sec / 1000:5.1f} Kb/s"
        else:
            return f"{bits_per_sec:5.0f} b/s"

    def get_speed_color(self, speed: float) -> QColor:
        """Get color based on speed threshold."""
        if not self.config.get('color_coding', False):
            return self.default_color
        speed_mbps = speed * 8 / 1000000
        if speed_mbps > self.config.get('high_speed_threshold', 5.0):
            return self.high_color
        elif speed_mbps > self.config.get('low_speed_threshold', 1.0):
            return self.low_color
        return self.default_color

    def paintEvent(self, event):
        """Paint the widget with speed information."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        if self.config.get('graph_enabled', False):
            self._draw_graph(painter)
        self._draw_speed_text(painter)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        if self.config.get('graph_enabled', False):
            self._draw_graph(painter)
        self._draw_speed_text(painter)

    def _draw_graph(self, painter):
        """Draw the mini-graph of speed history."""
        if self.speed_history:
            graph_width = self.width()
            graph_height = self.height()
            GRAPH_POINTS = 30
            history_points = list(self.speed_history)[-GRAPH_POINTS:]
            if history_points:
                max_speed = max(
                    max(point[0] for point in history_points),
                    max(point[1] for point in history_points)
                )
                if max_speed > 0:
                    painter.setPen(QColor(0, 255, 0, 128))
                    for i in range(len(history_points) - 1):
                        x1 = i * graph_width / (len(history_points) - 1)
                        x2 = (i + 1) * graph_width / (len(history_points) - 1)
                        y1 = graph_height * (1 - history_points[i][0] / max_speed)
                        y2 = graph_height * (1 - history_points[i + 1][0] / max_speed)
                        painter.drawLine(int(x1), int(y1), int(x2), int(y2))
                    painter.setPen(QColor(0, 120, 255, 128))
                    for i in range(len(history_points) - 1):
                        x1 = i * graph_width / (len(history_points) - 1)
                        x2 = (i + 1) * graph_width / (len(history_points) - 1)
                        y1 = graph_height * (1 - history_points[i][1] / max_speed)
                        y2 = graph_height * (1 - history_points[i + 1][1] / max_speed)
                        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

    def _draw_speed_text(self, painter):
        """Draw speed text on the widget."""
        painter.setFont(self.font)
        text_height = self.metrics.height()
        widget_height = self.height()
        arrow_width = self.arrow_width
        arrow_x = 5
        text_x = arrow_x + arrow_width
        top_text_y = int(widget_height / 2 - text_height - 2)
        bottom_text_y = int(widget_height / 2 + 2)
        
        up_speed = self.format_speed(self.upload_speed)
        up_color = self.get_speed_color(self.upload_speed)
        painter.setPen(up_color)
        painter.drawText(arrow_x, top_text_y + self.metrics.ascent(), "↑")
        painter.drawText(text_x, top_text_y + self.metrics.ascent(), up_speed)
        
        down_speed = self.format_speed(self.download_speed)
        down_color = self.get_speed_color(self.download_speed)
        painter.setPen(down_color)
        painter.drawText(arrow_x, bottom_text_y + self.metrics.ascent(), "↓")
        painter.drawText(text_x, bottom_text_y + self.metrics.ascent(), down_speed)

    def contextMenuEvent(self, event):
        """Handle context menu events."""
        widget_pos = self.mapToGlobal(QPoint(0, 0))
        widget_width = self.width()
        menu_width = self.context_menu.sizeHint().width()
        menu_height = self.context_menu.sizeHint().height()
        menu_x = widget_pos.x() + (widget_width // 2) - (menu_width // 2)
        menu_y = widget_pos.y() - menu_height
        screen = self.find_taskbar_screen().geometry()
        menu_x = max(0, min(menu_x, screen.width() - menu_width))
        menu_y = max(0, menu_y)
        self.context_menu.exec(QPoint(menu_x, menu_y))
        event.accept()

    def closeEvent(self, event):
        """Handle widget closure."""
        try:
            self.timer.stop()
            self.speed_timer.stop()
            self.force_show_timer.stop()
            self.speed_history.clear()
            self.save_config()
            if self.graph_window is not None:
                self.graph_window.figure.clear()
                self.graph_window.close()
                self.graph_window = None
            plt.close('all')
            win32gui.ShowWindow(int(self.winId()), win32con.SW_HIDE)
            win32gui.SetWindowLong(int(self.winId()), win32con.GWL_EXSTYLE, win32con.WS_EX_TOOLWINDOW)
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}", exc_info=True)
        finally:
            QApplication.quit()

    def load_config(self):
        """Load configuration from file."""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    if 'history_hours' in config:
                        self.logger.info("Migrating history_hours to history_minutes")
                        config['history_minutes'] = int(config.pop('history_hours') * 60)
                    for key in ['position_x', 'position_y']:
                        if key in config and config[key] is not None:
                            config[key] = int(config[key])
                    return self.validate_config({**DEFAULT_CONFIG, **config})
        except Exception as e:
            print(f"Error loading config: {e}")
        return DEFAULT_CONFIG

    def save_config(self):
        """Save configuration to file."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            print(f"Error saving config: {e}")

    def validate_config(self, config: Dict) -> Dict:
        """Validate and normalize configuration."""
        try:
            validated = config.copy()
            for key, (min_val, max_val, default) in {
                'update_rate': (0, 60, 1),
                'high_speed_threshold': (0.1, 1000.0, 1.0),
                'low_speed_threshold': (0.0, 999.9, 0.1),
                'history_minutes': (5, 1440, 30)
            }.items():
                validated[key] = max(min_val, min(max_val, config.get(key, default)))
            for color_key in ['high_speed_color', 'low_speed_color', 'default_color']:
                color = config.get(color_key, '#FFFFFF')
                if not re.match(r'^#(?:[0-9a-fA-F]{3}){1,2}$', color):
                    validated[color_key] = '#FFFFFF'
            return validated
        except Exception as e:
            raise ConfigError(f"Configuration validation failed: {e}")

    def show_settings(self):
        """Show the settings dialog with initial positioning above the widget and taskbar."""
        dialog = SettingsDialog(self, self.config)
        win32gui.SetWindowPos(self.winId(), win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        widget_pos = self.mapToGlobal(QPoint(0, 0))
        widget_width = self.width()
        dialog_width = dialog.sizeHint().width()
        dialog_height = dialog.sizeHint().height()
        dialog_x = widget_pos.x() + (widget_width // 2) - (dialog_width // 2)  # Center horizontally above widget
        taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
        dialog_y = taskbar_rect[1] - dialog_height - 10  # Position just above taskbar
        screen = self.find_taskbar_screen().geometry()
        dialog_x = max(0, min(dialog_x, screen.width() - dialog_width))
        dialog_y = max(0, min(dialog_y, screen.height() - dialog_height))  # Ensure within screen bounds
        dialog.move(dialog_x, dialog_y)
        if dialog.exec():
            old_config = self.config.copy()
            self.config = dialog.get_settings()
            try:
                self.save_config()
                new_rate = self.config['update_rate']
                self.speed_timer.setInterval(50 if new_rate == 0 else new_rate * 1000)
            except Exception as e:
                self.logger.error(f"Failed to apply new config: {e}")
                self.config = old_config
                self.save_config()
        win32gui.SetWindowPos(self.winId(), win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def show_graph(self):
        """Show the graph window."""
        if not self.graph_window:
            self.graph_window = GraphWindow(self)
        if self.speed_history:
            self.graph_window.update_graph(self.speed_history)
            self.graph_window.show()
        else:
            print("No history data available")

    def toggle_startup(self):
        """Toggle startup with Windows."""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "NetSpeedTray"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            try:
                winreg.QueryValueEx(key, app_name)
                winreg.DeleteValue(key, app_name)
                return False
            except WindowsError:
                exe_path = sys.executable if getattr(sys, 'frozen', False) else sys.argv[0]
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe_path}"')
                return True
            finally:
                key.Close()
        except Exception as e:
            self.logger.error(f"Failed to toggle startup: {e}", exc_info=True)
            return False

    def is_startup_enabled(self):
        """Check if startup is enabled."""
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
        self.logger = parent.logger if parent and hasattr(parent, 'logger') else logging.getLogger('NetSpeedTray')
        self.setWindowTitle(f"{APP_NAME} Settings v{APP_VERSION}")
        if hasattr(parent, 'app_icon'):
            self.setWindowIcon(parent.app_icon)
        self.original_pos = None
        self.setupUI()

    def setupUI(self):
        layout = QVBoxLayout()
        
        general_group = QGroupBox("General Settings")
        general_layout = QGridLayout()
        self.update_rate = QSpinBox()
        self.update_rate.setRange(0, 60)
        self.update_rate.setValue(self.config.get('update_rate', 0))
        self.update_rate.setSpecialValueText("Live")
        self.update_rate.setSuffix(" seconds")
        general_layout.addWidget(QLabel("Update Rate:"), 0, 0)
        general_layout.addWidget(self.update_rate, 0, 1)
        self.start_with_windows = QCheckBox("Start with Windows")
        start_with_windows_enabled = False
        if hasattr(self.parent(), 'is_startup_enabled'):
            start_with_windows_enabled = self.parent().is_startup_enabled()
        self.start_with_windows.setChecked(start_with_windows_enabled)
        general_layout.addWidget(self.start_with_windows, 1, 0, 1, 2)
        general_group.setLayout(general_layout)
        layout.addWidget(general_group)

        color_group = QGroupBox("Speed Color Coding")
        color_layout = QGridLayout()
        self.enable_colors = QCheckBox("Enable Color Coding")
        self.enable_colors.setChecked(self.config.get('color_coding', False))
        color_layout.addWidget(self.enable_colors, 0, 0, 1, 2)
        self.color_settings = QWidget()
        color_settings_layout = QGridLayout()
        color_settings_layout.setSpacing(8)
        self.high_speed_threshold = QDoubleSpinBox()
        self.high_speed_threshold.setRange(0.1, 1000.0)
        self.high_speed_threshold.setValue(self.config.get('high_speed_threshold', 1.0))
        self.high_speed_threshold.setSuffix(" Mbps")
        self.low_speed_threshold = QDoubleSpinBox()
        self.low_speed_threshold.setRange(0.0, 999.9)
        self.low_speed_threshold.setValue(self.config.get('low_speed_threshold', 0.1))
        self.low_speed_threshold.setSuffix(" Mbps")
        color_settings_layout.addWidget(QLabel("High Speed Threshold:"), 1, 0)
        color_settings_layout.addWidget(self.high_speed_threshold, 1, 1)
        color_settings_layout.addWidget(QLabel("Low Speed Threshold:"), 2, 0)
        color_settings_layout.addWidget(self.low_speed_threshold, 2, 1)
        self.high_speed_color = QPushButton()
        self.high_speed_color.setStyleSheet(f"background-color: {self.config.get('high_speed_color', '#00FF00')}")
        self.high_speed_color.clicked.connect(lambda: self.choose_color(self.high_speed_color))
        self.low_speed_color = QPushButton()
        self.low_speed_color.setStyleSheet(f"background-color: {self.config.get('low_speed_color', '#FFA500')}")
        self.low_speed_color.clicked.connect(lambda: self.choose_color(self.low_speed_color))
        self.default_color = QPushButton()
        self.default_color.setStyleSheet(f"background-color: {self.config.get('default_color', '#FFFFFF')}")
        self.default_color.clicked.connect(lambda: self.choose_color(self.default_color))
        color_settings_layout.addWidget(QLabel("High Speed Color:"), 3, 0)
        color_settings_layout.addWidget(self.high_speed_color, 3, 1)
        color_settings_layout.addWidget(QLabel("Low Speed Color:"), 4, 0)
        color_settings_layout.addWidget(self.low_speed_color, 4, 1)
        color_settings_layout.addWidget(QLabel("Default Color:"), 5, 0)
        color_settings_layout.addWidget(self.default_color, 5, 1)
        self.color_settings.setLayout(color_settings_layout)
        color_layout.addWidget(self.color_settings, 1, 0, 1, 2)
        color_group.setLayout(color_layout)
        self.color_settings.setVisible(self.enable_colors.isChecked())

        def toggle_color_settings(checked):
            screen = QApplication.primaryScreen().geometry()
            current_pos = self.pos()
            if checked:
                self.original_pos = current_pos
                old_height = self.height()
                self.color_settings.setVisible(True)
                self.adjustSize()
                new_height = self.sizeHint().height()
                new_y = current_pos.y() - (new_height - old_height)
                new_y = max(0, min(new_y, screen.height() - new_height))
                self.move(current_pos.x(), new_y)
            else:
                self.color_settings.setVisible(False)
                self.color_settings.setMaximumHeight(0)
                color_group.adjustSize()
                self.adjustSize()
                if self.parent() and hasattr(self.parent(), 'taskbar_hwnd'):
                    taskbar_rect = win32gui.GetWindowRect(self.parent().taskbar_hwnd)
                    dpr = self.parent().devicePixelRatioF() if hasattr(self.parent(), 'devicePixelRatioF') else 1.0
                    taskbar_top = taskbar_rect[1] / dpr
                    taskbar_bottom = taskbar_rect[3] / dpr
                    new_height = self.height()
                    if taskbar_top < screen.height() / 2:
                        new_y = taskbar_bottom + 10
                    else:
                        new_y = taskbar_top - new_height - 10
                    new_x = current_pos.x() if self.original_pos is None else self.original_pos.x()
                    new_y = max(0, min(new_y, screen.height() - new_height))
                    self.move(int(new_x), int(new_y))
                self.color_settings.setMaximumHeight(16777215)

        self.enable_colors.toggled.connect(toggle_color_settings)
        layout.addWidget(color_group)

        graph_group = QGroupBox("Graph Settings")
        graph_layout = QGridLayout()
        self.enable_graph = QCheckBox("Enable Graph")
        self.enable_graph.setChecked(self.config.get('graph_enabled', False))
        graph_layout.addWidget(self.enable_graph, 0, 0, 1, 2)
        self.history_duration = QComboBox()
        current_minutes = self.config.get('history_minutes', 30)
        for minutes, label in TIME_INTERVALS:
            self.history_duration.addItem(label, minutes)
            if minutes == current_minutes:
                self.history_duration.setCurrentIndex(self.history_duration.count() - 1)
        self.history_duration.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        graph_layout.addWidget(QLabel("History Duration:"), 1, 0)
        graph_layout.addWidget(self.history_duration, 1, 1)
        graph_group.setLayout(graph_layout)
        layout.addWidget(graph_group)

        interface_group = QGroupBox("Network Interfaces")
        interface_layout = QGridLayout()
        self.interfaces = self.get_network_interfaces()
        self.interface_mode = QComboBox()
        self.interface_mode.addItems(['Monitor All', 'Monitor Selected', 'Exclude Selected'])
        current_mode = self.config.get('interface_mode', 'all')
        self.interface_mode.setCurrentText({
            'all': 'Monitor All', 'include': 'Monitor Selected', 'exclude': 'Exclude Selected'
        }.get(current_mode, 'Monitor All'))
        self.interface_mode.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        interface_layout.addWidget(QLabel("Mode:"), 0, 0)
        interface_layout.addWidget(self.interface_mode, 0, 1)
        self.interface_list = QWidget()
        list_layout = QVBoxLayout()
        self.interface_checkboxes = {}
        selected_interfaces = self.config.get('selected_interfaces', [])
        for iface in self.interfaces:
            checkbox = QCheckBox(iface)
            checkbox.setChecked(iface in selected_interfaces)
            self.interface_checkboxes[iface] = checkbox
            list_layout.addWidget(checkbox)
        self.interface_list.setLayout(list_layout)
        interface_layout.addWidget(self.interface_list, 1, 0, 1, 2)
        
        def update_interface_list_state(text):
            enabled = text != 'Monitor All'
            self.interface_list.setEnabled(enabled)

        self.interface_mode.currentTextChanged.connect(update_interface_list_state)
        update_interface_list_state(self.interface_mode.currentText())
        interface_group.setLayout(interface_layout)
        layout.addWidget(interface_group)

        error_group = QGroupBox("Troubleshooting")
        error_layout = QVBoxLayout()
        export_button = QPushButton("Export Error Log")
        export_button.clicked.connect(self.export_error_log)
        error_layout.addWidget(export_button)
        error_group.setLayout(error_layout)
        layout.addWidget(error_group)

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
        """Choose a color for speed thresholds."""
        color = QColorDialog.getColor()
        if color.isValid():
            button.setStyleSheet(f"background-color: {color.name()}")

    def get_settings(self):
        """Get current settings."""
        selected_minutes = self.history_duration.currentData()
        settings = {
            'update_rate': self.update_rate.value(),
            'color_coding': self.enable_colors.isChecked(),
            'high_speed_threshold': self.high_speed_threshold.value(),
            'low_speed_threshold': self.low_speed_threshold.value(),
            'high_speed_color': self.high_speed_color.palette().button().color().name(),
            'low_speed_color': self.low_speed_color.palette().button().color().name(),
            'default_color': self.default_color.palette().button().color().name(),
            'graph_enabled': self.enable_graph.isChecked(),
            'history_minutes': selected_minutes,
            'start_with_windows': self.start_with_windows.isChecked(),
            'interface_mode': {
                'Monitor All': 'all', 'Monitor Selected': 'include', 'Exclude Selected': 'exclude'
            }[self.interface_mode.currentText()],
            'selected_interfaces': [iface for iface, cb in self.interface_checkboxes.items() if cb.isChecked()]
        }
        if self.start_with_windows.isChecked() != self.parent().is_startup_enabled():
            self.parent().toggle_startup()
        return settings

    def get_network_interfaces(self):
        """Get list of network interfaces."""
        interfaces = []
        try:
            stats = psutil.net_if_stats()
            interfaces = [iface for iface, stats in stats.items() if stats.isup]
        except Exception as e:
            print(f"Error getting network interfaces: {e}")
        return sorted(interfaces)

    def export_error_log(self):
        """Export the error log to a file."""
        try:
            if not os.path.exists(ERROR_LOG_FILE):
                QMessageBox.information(self, "Error Log", "No errors have been logged.")
                return
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_name = f"NetSpeedTray_ErrorLog_{timestamp}.log"
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Export Error Log", suggested_name, "Log Files (*.log);;All Files (*.*)"
            )
            if file_path:
                import shutil
                shutil.copy2(ERROR_LOG_FILE, file_path)
                QMessageBox.information(self, "Success", f"Error log exported to:\n{file_path}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to export error log: {e}")


class GraphWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = parent
        self.setWindowTitle("Network Speed History")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        layout = QVBoxLayout()
        self.figure = Figure(figsize=(8, 6))
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas)
        self.setLayout(layout)
        self.resize(800, 600)
        screen = QApplication.primaryScreen().geometry()
        self.move(int((screen.width() - self.width()) / 2), int((screen.height() - self.height()) / 2))
        self.ax = self.figure.add_subplot(111)
        self.upload_line, = self.ax.plot([], [], label='Upload (Mbps)', color='green', linewidth=2)
        self.download_line, = self.ax.plot([], [], label='Download (Mbps)', color='blue', linewidth=2)
        self.ax.set_xlabel('Time')
        self.ax.set_ylabel('Speed (Mbps)')
        self.ax.grid(True, linestyle='--', alpha=0.7)
        self.ax.legend(loc='upper left')

    def update_graph(self, history):
        """Update the graph with speed history."""
        if not history:
            return
        if self._parent and 'history_minutes' in self._parent.config:
            max_points = int(self._parent.config['history_minutes'] * 60)
        else:
            max_points = MAX_HISTORY_POINTS
        history = list(history)[-max_points:]
        upload = [x[0] * 8 / 1000000 for x in history]
        download = [x[1] * 8 / 1000000 for x in history]
        times = [datetime.now() - timedelta(seconds=(len(history) - i - 1)) for i in range(len(history))]
        self.upload_line.set_data(times, upload)
        self.download_line.set_data(times, download)
        self.ax.relim()
        self.ax.autoscale_view()
        self.figure.autofmt_xdate()
        self.canvas.draw()

# Application Monitor Class
class NetworkMonitor:
    def __init__(self):
        mutex_name = "Global\\NetSpeedTray_SingleInstanceMutex"
        self.mutex = win32event.CreateMutex(None, False, mutex_name)
        last_error = win32api.GetLastError()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            print("Another instance of NetSpeedTray is already running.")
            sys.exit(1)
        self.app = QApplication(sys.argv)
        self.widget = NetworkSpeedWidget()
        self.widget.update_position()
        self.widget.show()
        signal.signal(signal.SIGINT, self.signal_handler)
        self.logger = logging.getLogger('NetSpeedTray')

    def signal_handler(self, sig, frame):
        """Handle SIGINT for clean shutdown."""
        self.logger.debug("Received SIGINT, shutting down")
        self.widget.close()
        if hasattr(self, 'mutex'):
            win32api.CloseHandle(self.mutex)
        sys.exit(0)

    def run(self):
        """Run the application event loop."""
        return self.app.exec()

# Main Execution
if __name__ == "__main__":
    monitor = NetworkMonitor()
    sys.exit(monitor.run())
