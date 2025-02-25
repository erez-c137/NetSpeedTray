# Standard library imports
import os, sys, json, re, logging, winreg, traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Dict
from collections import deque
import gc

# Third-party imports
import win32gui, win32con, win32event, win32api, winerror, psutil, signal
import matplotlib
matplotlib.use('qtagg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import matplotlib.pyplot as plt

# Qt imports - grouped by functionality
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect
from PyQt6.QtGui import QFont, QPainter, QColor, QFontMetrics, QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMenu, QDialog, QVBoxLayout, QGridLayout, 
    QGroupBox, QCheckBox, QSpinBox, QDoubleSpinBox, QLabel, QPushButton, 
    QColorDialog, QComboBox, QMessageBox, QFileDialog
)

def get_app_data_path():

    """
    Returns the application data directory path, creating it if it doesn't exist.
    Returns:
        str: Path to NetSpeedTray application data directory
    """

    app_data = os.path.join(os.getenv('APPDATA'), 'NetSpeedTray')
    if not os.path.exists(app_data):
        os.makedirs(app_data)
    return app_data


APP_NAME = "NetSpeedTray"
APP_VERSION = "1.0.2-b.5"
DEFAULT_FONT = "Segoe UI"
DEFAULT_FONT_SIZE = 9
DEFAULT_UPDATE_RATE = 2
MAX_HISTORY_POINTS = 600  # 10 minutes at 1-second intervals
LOG_FORMAT = '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
ERROR_LOG_FILE = os.path.join(get_app_data_path(), 'error.log')
MAX_LOG_SIZE = 10 * 1024 * 1024
MAX_LOG_FILES = 3
TIME_INTERVALS = [
    (5, "5 mins"),
    (10, "10 mins"),
    (15, "15 mins"),
    (30, "30 mins"),
    (60, "1 hour"),
    (120, "2 hours"),
    (180, "3 hours"),
    (240, "4 hours"),
    (300, "5 hours"),
    (360, "6 hours"),
    (720, "12 hours"),
    (1440, "24 hours")
]

DEFAULT_CONFIG = {
    'color_coding': False,
    'high_speed_threshold': 5.0,
    'low_speed_threshold': 1.0,
    'high_speed_color': '#00FF00',
    'low_speed_color': '#FFA500',
    'default_color': '#FFFFFF',
    'graph_enabled': False,
    'history_minutes': 30,
    'update_rate': DEFAULT_UPDATE_RATE,
    'position_x': None,
    'position_y': None,
    'start_with_windows': False,
    'interface_mode': 'all',
    'selected_interfaces': []
}

def setup_error_logging():

    """
    Configures application-wide error logging with rotation.
    Returns:
        logging.Logger: Configured logger instance or None if setup fails
    """

    try:
        handler = RotatingFileHandler(
            ERROR_LOG_FILE,
            maxBytes=MAX_LOG_SIZE,
            backupCount=MAX_LOG_FILES
        )
        formatter = logging.Formatter(LOG_FORMAT)
        handler.setFormatter(formatter)
        
        logger = logging.getLogger('NetSpeedTray')
        logger.setLevel(logging.ERROR)  # Using logging.ERROR instead of just ERROR
        handler.setLevel(logging.ERROR)  # Also set handler level
        logger.addHandler(handler)
        return logger
    except Exception as e:
        print(f"Failed to setup logging: {e}")
        return None


class NetworkMonitorError(Exception):

    """Base exception for all NetworkMonitor errors."""

    pass

class ConfigError(NetworkMonitorError):

    """Configuration validation or loading errors."""

    pass

class NetworkSpeedWidget(QWidget):

    """
    Main widget that displays network speed in the Windows taskbar.
    Features:
    - Real-time upload/download speed monitoring
    - Color-coded speed indicators
    - Interface selection
    - Speed history graphing
    - Error logging and diagnostics  

    Attributes:
        upload_speed (float): Current upload speed in bytes/sec
        download_speed (float): Current download speed in bytes/sec
        speed_history (list): Historical speed measurements
        config (dict): Widget configuration
    """

    def __init__(self) -> None:
        super().__init__(None)
        self.logger = setup_error_logging()
        self._init_icon()
        self._init_config()
        self._init_screen_handling()
        self._init_data()
        self._init_ui()
        self.default_color = QColor('#FFFFFF')
        self.high_color = QColor('#00FF00')
        self.low_color = QColor('#FFA500')
        
        # Use logical pixels consistently
        self.font = QFont(DEFAULT_FONT, DEFAULT_FONT_SIZE)  # Logical size
        self.setFont(self.font)
        self.metrics = QFontMetrics(self.font)
        self.arrow_width = self.metrics.horizontalAdvance("↑ ")
        
        # Calculate size in logical pixels
        max_text_width = self.metrics.horizontalAdvance("↑ 999.9 Mb/s") + self.metrics.averageCharWidth()
        taskbar_height = self.get_taskbar_height()  # Ensure this returns logical pixels
        self.setFixedSize(max_text_width, taskbar_height)
        self.first_update = True

    def _init_icon(self):

        """Initialize application icon from ico file."""

        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(script_dir, 'NetSpeedTray.ico')
            
            if os.path.exists(icon_path):
                self.app_icon = QIcon(icon_path)
                self.setWindowIcon(self.app_icon)
                QApplication.setWindowIcon(self.app_icon)

            else:

                pass

        except Exception as e:

            print(f"Failed to load icon: {e}")
            import traceback
            traceback.print_exc()

    def _init_screen_handling(self):
        self.taskbar_hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.dragging = False
        self.offset = QPoint()
        self.current_screen = QApplication.primaryScreen()
        for screen in QApplication.screens():
            screen.geometryChanged.connect(self.handle_screen_change)
            screen.availableGeometryChanged.connect(self.handle_screen_change)
        self.initialize_position()
        if self.taskbar_hwnd:
            taskbar_z = win32gui.GetWindow(self.taskbar_hwnd, win32con.GW_HWNDPREV)
            win32gui.SetWindowPos(self.winId(), taskbar_z, 0, 0, 0, 0,
                                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

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
        self.speed_history = deque(maxlen=MAX_HISTORY_POINTS)
        self.graph_window = None

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
        max_text_width = metrics.horizontalAdvance("↑ 999.9 Mb/s") + 10  # Padding
        taskbar_height = self.get_taskbar_height()  # Logical pixels
        self.setFixedSize(max_text_width, taskbar_height)  # Match taskbar height
        self.context_menu = QMenu(self)
        self.context_menu.addAction("Settings", self.show_settings)
        self.context_menu.addSeparator()
        self.context_menu.addAction("Exit", self.close)
        # self.logger.debug(f"UI setup, Size: {self.size()}")

    def setupTimers(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_stats)
        self.timer.start(max(50, self.config.get('update_rate', DEFAULT_UPDATE_RATE) * 1000))
        
        self.keep_alive_timer = QTimer()
        self.keep_alive_timer.timeout.connect(self.ensure_visibility)
        self.keep_alive_timer.start(100)  # Set to 100ms as tested

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            try:
                self.dragging = True
                self.offset = event.position().toPoint()
                self.keep_alive_timer.stop()  # Stop timer while dragging
                event.accept()
            except Exception as e:
                self.logger.error(f"Mouse press error: {e}")
                self.dragging = False

    def mouseMoveEvent(self, event):
        if self.dragging:
            try:
                new_pos = event.globalPosition().toPoint() - self.offset
                if self.taskbar_hwnd:
                    taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
                    screen = self.current_screen.availableGeometry()  # Logical pixels
                    taskbar_top = taskbar_rect[1]
                    taskbar_height = taskbar_rect[3] - taskbar_rect[1]
                    taskbar_width = taskbar_rect[2] - taskbar_rect[0]
                    taskbar_left = taskbar_rect[0]
                    taskbar_right = taskbar_rect[2]
                    if taskbar_height > taskbar_width:  # Vertical
                        y = max(0, min(new_pos.y(), screen.height() - self.height()))
                        x = max(taskbar_left, min(new_pos.x(), taskbar_right - self.width()))
                    else:  # Horizontal
                        y = max(taskbar_top, min(taskbar_rect[3] - self.height(), new_pos.y()))
                        x = max(taskbar_left, min(new_pos.x(), taskbar_right - self.width()))
                    new_pos.setX(int(x))
                    new_pos.setY(int(y))
                self.move(new_pos)
                for screen in QApplication.screens():
                    if screen.geometry().contains(new_pos):
                        self.current_screen = screen
                        break
                event.accept()
            except Exception as e:
                self.logger.error(f"Mouse move error: {e}")
                self.dragging = False
                self.ensure_visibility()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.save_position()  # Save only X, since Y is locked
            self.keep_alive_timer.start(100)  # Restart timer
            self.ensure_visibility()  # Final adjustment
            event.accept()


    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Use a semi-transparent black background like the taskbar
        painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
        if self.config.get('graph_enabled', False):
            self._draw_graph(painter)
        self._draw_speed_text(painter)

        # Ensure background is drawn (even if translucent)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        if self.config.get('graph_enabled', False):
            self._draw_graph(painter)
        self._draw_speed_text(painter) 

    def contextMenuEvent(self, event):

        """Handle right-click events and show context menu centered above the widget"""

        widget_pos = self.mapToGlobal(QPoint(0, 0))
        widget_width = self.width()
        menu_width = self.context_menu.sizeHint().width()
        menu_height = self.context_menu.sizeHint().height()     
        menu_x = widget_pos.x() + (widget_width // 2) - (menu_width // 2)
        menu_y = widget_pos.y() - menu_height
        screen = QApplication.primaryScreen().geometry()
        menu_x = max(0, min(menu_x, screen.width() - menu_width))
        menu_y = max(0, menu_y)
        self.context_menu.exec(QPoint(menu_x, menu_y))
        event.accept()

    def closeEvent(self, event):
        try:
            self.timer.stop()
            self.keep_alive_timer.stop()
            self.speed_history.clear()  # Clear history
            self.save_position()
            self.save_config()
            if self.graph_window is not None:
                self.graph_window.figure.clear()
                self.graph_window.close()
                self.graph_window = None
            plt.close('all')
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}", exc_info=True)
        finally:
            QApplication.quit()

    def _draw_background(self, painter):
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

    def _draw_graph(self, painter):
        if self.speed_history:
            graph_width = self.width()
            graph_height = self.height()
            GRAPH_POINTS = 30  # Define locally or as a constant
            # Convert deque to list and take the last GRAPH_POINTS elements
            history_points = list(self.speed_history)[-GRAPH_POINTS:]
            
            if history_points:
                max_speed = max(
                    max(point[0] for point in history_points),  # Index 0 = upload
                    max(point[1] for point in history_points)   # Index 1 = download
                )
                
                if max_speed > 0:
                    painter.setPen(QColor(0, 255, 0, 128))  # Green for upload
                    for i in range(len(history_points) - 1):
                        x1 = i * graph_width / (len(history_points) - 1)
                        x2 = (i + 1) * graph_width / (len(history_points) - 1)
                        y1 = graph_height * (1 - history_points[i][0] / max_speed)
                        y2 = graph_height * (1 - history_points[i + 1][0] / max_speed)
                        painter.drawLine(int(x1), int(y1), int(x2), int(y2))
                    
                    painter.setPen(QColor(0, 120, 255, 128))  # Blue for download
                    for i in range(len(history_points) - 1):
                        x1 = i * graph_width / (len(history_points) - 1)
                        x2 = (i + 1) * graph_width / (len(history_points) - 1)
                        y1 = graph_height * (1 - history_points[i][1] / max_speed)
                        y2 = graph_height * (1 - history_points[i + 1][1] / max_speed)
                        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

    def _draw_speed_text(self, painter):
        painter.setFont(self.font)
        text_height = self.metrics.height()
        widget_height = self.height()
        arrow_width = self.arrow_width

        # Fixed starting position for arrows (left side with padding)
        arrow_x = 5  # Fixed X position with some padding from left edge
        text_x = arrow_x + arrow_width  # Text starts right after the arrow

        # Vertical positioning for up and down text
        top_text_y = int(widget_height / 2 - text_height - 2)
        bottom_text_y = int(widget_height / 2 + 2)

        # Draw upload speed (↑)
        up_speed = self.format_speed(self.upload_speed)
        up_color = self.get_speed_color(self.upload_speed)
        painter.setPen(up_color)
        painter.drawText(arrow_x, top_text_y + self.metrics.ascent(), "↑")
        painter.drawText(text_x, top_text_y + self.metrics.ascent(), up_speed)

        # Draw download speed (↓)
        down_speed = self.format_speed(self.download_speed)
        down_color = self.get_speed_color(self.download_speed)
        painter.setPen(down_color)
        painter.drawText(arrow_x, bottom_text_y + self.metrics.ascent(), "↓")
        painter.drawText(text_x, bottom_text_y + self.metrics.ascent(), down_speed)

    def save_position(self):
        self.config['position_x'] = self.x()
        # Don't save position_y since it's fixed to taskbar
        self.save_config()

    def initialize_position(self):
        try:
            x = self.config.get('position_x')
            screen = self.current_screen.availableGeometry()
            if x is not None and 0 <= x <= screen.width() - self.width():
                self.move(x, screen.height())  # Y will be adjusted by ensure_visibility
            else:
                tray_hwnd = win32gui.FindWindowEx(self.taskbar_hwnd, None, "TrayNotifyWnd", None)
                if tray_hwnd and self.taskbar_hwnd:
                    tray_rect = win32gui.GetWindowRect(tray_hwnd)
                    taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
                    x = tray_rect[0] - self.width() - 5
                    y = taskbar_rect[3] - self.height() if taskbar_rect[1] > screen.height() / 2 else taskbar_rect[1]
                    self.move(max(0, min(x, screen.width() - self.width())), y)
                else:
                    self.move(screen.width() - self.width() - 50, screen.height() - self.height() - 40)
            self.show()
            QTimer.singleShot(500, self.ensure_visibility)
        except Exception as e:
            self.logger.error(f"Error initializing position: {e}")
            self.move(100, 100)
            self.show()

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    if 'history_hours' in config:
                        self.logger.info("Migrating history_hours to history_minutes")
                        config['history_minutes'] = int(config.pop('history_hours') * 60)
                    # Ensure position values are integers or None
                    for key in ['position_x', 'position_y']:
                        if key in config and config[key] is not None:
                            config[key] = int(config[key])
                    return self.validate_config({**DEFAULT_CONFIG, **config})
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
            for key, (min_val, max_val, default) in {
                'update_rate': (0, 60, 1),
                'high_speed_threshold': (0.1, 1000.0, 1.0),
                'low_speed_threshold': (0.0, 999.9, 0.1),
                'history_minutes': (5, 1440, 30)  # Min = 5 mins, Max = 24 hours, Default = 30 mins
            }.items():
                validated[key] = max(min_val, min(max_val, config.get(key, default)))
            for color_key in ['high_speed_color', 'low_speed_color', 'default_color']:
                color = config.get(color_key, '#FFFFFF')
                if not re.match(r'^#(?:[0-9a-fA-F]{3}){1,2}$', color):
                    validated[color_key] = '#FFFFFF'
            return validated
        except Exception as e:
            raise ConfigError(f"Configuration validation failed: {e}")

    def update_stats(self):
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
                    self.upload_speed = 0  # Display 0 until second update
                    self.download_speed = 0
                    self.first_update = False
                else:
                    self.upload_speed = total_upload
                    self.download_speed = total_download
                
                self.last_time = current_time
                if self.config.get('graph_enabled', False) and not self.first_update:
                    self.speed_history.append((total_upload, total_download))
                gc.collect()
                self.update()  # Ensure widget repaints with new values
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
            return self.default_color
        speed_mbps = speed * 8 / 1000000
        if speed_mbps > self.config.get('high_speed_threshold', 5.0):
            return self.high_color
        elif speed_mbps > self.config.get('low_speed_threshold', 1.0):
            return self.low_color
        return self.default_color

    def show_settings(self):

        dialog = SettingsDialog(self, self.config) 

        win32gui.SetWindowPos(self.winId(), win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

        widget_pos = self.mapToGlobal(QPoint(0, 0))
        widget_width = self.width()
        dialog_width = dialog.sizeHint().width()
        dialog_height = dialog.sizeHint().height()  
        dialog_x = widget_pos.x() + (widget_width // 2) - (dialog_width // 2)
        taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
        dialog_y = taskbar_rect[1] - dialog_height - 10
        screen = QApplication.primaryScreen().geometry()
        dialog_x = max(0, min(dialog_x, screen.width() - dialog_width))
        dialog_y = max(0, min(dialog_y, screen.height() - dialog_height))
        dialog.move(dialog_x, dialog_y)
        # self.logger.debug(f"Settings dialog opened at: {dialog_x}, {dialog_y}")

        if dialog.exec():
            old_config = self.config.copy()
            self.config = dialog.get_settings()
            try:
                self.save_config()
                new_rate = self.config['update_rate']
                self.timer.setInterval(50 if new_rate == 0 else new_rate * 1000)
            except Exception as e:
                self.logger.error(f"Failed to apply new config: {e}")
                self.config = old_config  # Roll back to old config
                self.save_config()  # Restore old config to file
        win32gui.SetWindowPos(self.winId(), win32con.HWND_TOPMOST, 0, 0, 0, 0,
                            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def show_graph(self):
        """Opens a window displaying network speed history graph if data is available"""
        if not self.graph_window:
            self.graph_window = GraphWindow(self)  # Pass self as parent
        if self.speed_history:
            self.graph_window.update_graph(self.speed_history)
            self.graph_window.show()
        else:
            print("No history data available")

    def handle_screen_change(self, geometry):
        for screen in QApplication.screens():
            if screen.geometry().contains(self.pos()):
                if screen != self.current_screen:
                    self.current_screen = screen
                    self.ensure_visibility()
                break
        QTimer.singleShot(100, self.ensure_visibility)  # Reduced delay for responsiveness

    def is_admin(self):

        import ctypes

        try:

            return ctypes.windll.shell32.IsUserAnAdmin()

        except:

            return False

    def is_fullscreen_app_active(self):
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or not win32gui.IsWindow(hwnd) or hwnd == self.winId():
                return False
            
            # Check if fullscreen app is on the widget's screen
            rect = win32gui.GetWindowRect(hwnd)
            widget_screen = self.current_screen.geometry()  # Logical pixels
            hwnd_rect = QRect(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
            
            if not widget_screen.intersects(hwnd_rect):
                return False
            
            # Convert to logical pixels for comparison
            dpr = self.devicePixelRatioF()
            screen = self.current_screen.geometry()
            return (rect[0] <= screen.x() * dpr and 
                    rect[1] <= screen.y() * dpr and 
                    rect[2] >= (screen.width() + screen.x()) * dpr and 
                    rect[3] >= (screen.height() + screen.y()) * dpr)
        except Exception as e:
            self.logger.error(f"Error checking fullscreen: {e}")
            return False  # Default to not hiding the widget

    def ensure_visibility(self):
        if self.dragging or not self.taskbar_hwnd or not win32gui.IsWindowVisible(self.taskbar_hwnd):
            return
        
        try:
            if self.is_fullscreen_app_active():
                if self.isVisible():
                    self.hide()
                return
            
            screen = self.current_screen.availableGeometry()  # Logical pixels
            taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
            taskbar_height = taskbar_rect[3] - taskbar_rect[1]
            taskbar_width = taskbar_rect[2] - taskbar_rect[0]
            taskbar_left = taskbar_rect[0]
            taskbar_top = taskbar_rect[1]
            taskbar_bottom = taskbar_rect[3]
            
            current_pos = self.pos()
            x = current_pos.x()
            y = current_pos.y()
            
            if taskbar_height > taskbar_width:  # Vertical taskbar
                if taskbar_left < screen.width() / 2:  # Left
                    x = taskbar_left + taskbar_width
                else:  # Right
                    x = taskbar_left - self.width()
                y = max(0, min(y, screen.height() - self.height()))
            else:  # Horizontal taskbar
                tray_width_estimate = 150  # Logical pixels
                x_max = screen.width() - tray_width_estimate - self.width()
                x = max(0, min(x, x_max))
                y = taskbar_top if taskbar_top < screen.height() / 2 else taskbar_bottom - self.height()
            
            if current_pos != QPoint(x, y):
                self.move(x, y)
                self.save_position()
            
            if not self.isVisible():
                self.show()
            
            # Maintain z-order relative to taskbar
            taskbar_z = win32gui.GetWindow(self.taskbar_hwnd, win32con.GW_HWNDPREV)
            win32gui.SetWindowPos(self.winId(), taskbar_z, 0, 0, 0, 0,
                                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            
            self.update()
        except Exception as e:
            self.logger.error(f"Error in ensure_visibility: {e}")
            # Fallback to a safe position instead of calling set_default_position()
            self.move(100, 100)  # Simple fallback to a visible location
            self.show()
            QTimer.singleShot(500, self.ensure_visibility)  # Retry after a delay to stabilize

    def get_taskbar_height(self):
        if self.taskbar_hwnd:
            rect = win32gui.GetWindowRect(self.taskbar_hwnd)
            height = rect[3] - rect[1]  # Physical pixels
            logical_height = self.current_screen.logicalDotsPerInchY() / 96.0  # Base DPI
            return int(height / logical_height)  # Convert to logical pixels
        return 40  # Fallback in logical pixels

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

    def show_error_log(self):

        """Open error log file with default text editor"""

        try:

            if os.path.exists(ERROR_LOG_FILE):

                os.startfile(ERROR_LOG_FILE)

            else:

                QMessageBox.information(self, "Error Log", "No errors have been logged.")

        except Exception as e:

            QMessageBox.warning(self, "Error", f"Could not open error log: {e}")

    def export_error_log(self):

        """Export error log to user-selected location"""

        try:

            if not os.path.exists(ERROR_LOG_FILE):

                QMessageBox.information(self, "Error Log", "No errors have been logged.")

                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_name = f"NetSpeedTray_ErrorLog_{timestamp}.log"
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Error Log",
                suggested_name,
                "Log Files (*.log);;All Files (*.*)"
            )

            if file_path:

                import shutil
                shutil.copy2(ERROR_LOG_FILE, file_path)
                QMessageBox.information(
                    self,
                    "Success",
                    f"Error log exported to:\n{file_path}"
                )

        except Exception as e:

            QMessageBox.warning(self, "Error", f"Failed to export error log: {e}")

    def log_error(self, error_msg, exc_info=None):
        self.logger.error(f"{error_msg}\n{traceback.format_exc() if exc_info else ''}")


class StayOnTopComboBox(QComboBox):

    def showPopup(self):

        super().showPopup()
        popup = self.findChild(QWidget)  # Get the dropdown popup widget

        if popup:

            # Ensure the popup stays on top

            popup.setWindowFlags(popup.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            popup.raise_()


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
        
        # General Settings
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

        # Color Settings
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
                self.original_pos = current_pos  # Store position when enabling
                old_height = self.height()
                self.color_settings.setVisible(True)
                self.adjustSize()
                new_height = self.sizeHint().height()
                new_y = current_pos.y() - (new_height - old_height)  # Shift up to fit expanded size
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
                    if taskbar_top < screen.height() / 2:  # Top taskbar
                        new_y = taskbar_bottom + 10  # Below top taskbar
                    else:  # Bottom taskbar
                        new_y = taskbar_top - new_height - 10  # Above bottom taskbar
                    new_x = current_pos.x() if self.original_pos is None else self.original_pos.x()
                    new_y = max(0, min(new_y, screen.height() - new_height))
                    self.move(int(new_x), int(new_y))  # Convert both to int
                self.color_settings.setMaximumHeight(16777215)

        self.enable_colors.toggled.connect(toggle_color_settings)
        layout.addWidget(color_group)

        # Graph Settings
        graph_group = QGroupBox("Graph Settings")
        graph_layout = QGridLayout()
        self.enable_graph = QCheckBox("Enable Graph")
        self.enable_graph.setChecked(self.config.get('graph_enabled', False))
        graph_layout.addWidget(self.enable_graph, 0, 0, 1, 2)
        self.history_duration = StayOnTopComboBox()
        current_minutes = self.config.get('history_minutes', 30)  # Direct minutes value
        for minutes, label in TIME_INTERVALS:
            self.history_duration.addItem(label, minutes)
            if minutes == current_minutes:  # Simple integer comparison
                self.history_duration.setCurrentIndex(self.history_duration.count() - 1)
        self.history_duration.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        graph_layout.addWidget(QLabel("History Duration:"), 1, 0)
        graph_layout.addWidget(self.history_duration, 1, 1)
        graph_group.setLayout(graph_layout)
        layout.addWidget(graph_group)

        # Network Interfaces
        interface_group = QGroupBox("Network Interfaces")
        interface_layout = QGridLayout()
        self.interfaces = self.get_network_interfaces()
        self.interface_mode = StayOnTopComboBox()
        self.interface_mode.addItems(['Monitor All', 'Monitor Selected', 'Exclude Selected'])
        current_mode = self.config.get('interface_mode', 'all')
        self.interface_mode.setCurrentText({
            'all': 'Monitor All',
            'include': 'Monitor Selected',
            'exclude': 'Exclude Selected'
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

        # Troubleshooting
        error_group = QGroupBox("Troubleshooting")
        error_layout = QVBoxLayout()
        export_button = QPushButton("Export Error Log")
        export_button.clicked.connect(self.export_error_log)
        error_layout.addWidget(export_button)
        error_group.setLayout(error_layout)
        layout.addWidget(error_group)

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
        selected_minutes = self.history_duration.currentData()  # Already in minutes

        settings = {
            'update_rate': self.update_rate.value(),
            'color_coding': self.enable_colors.isChecked(),
            'high_speed_threshold': self.high_speed_threshold.value(),
            'low_speed_threshold': self.low_speed_threshold.value(),
            'high_speed_color': self.high_speed_color.palette().button().color().name(),
            'low_speed_color': self.low_speed_color.palette().button().color().name(),
            'default_color': self.default_color.palette().button().color().name(),
            'graph_enabled': self.enable_graph.isChecked(),
            'history_minutes': selected_minutes,  # Direct integer value
            'start_with_windows': self.start_with_windows.isChecked(),
            'interface_mode': {
                'Monitor All': 'all',
                'Monitor Selected': 'include',
                'Exclude Selected': 'exclude'
            }[self.interface_mode.currentText()],
            'selected_interfaces': [
                iface for iface, cb in self.interface_checkboxes.items() 
                if cb.isChecked()
            ]
        }    

        if self.start_with_windows.isChecked() != self.parent().is_startup_enabled():
            self.parent().toggle_startup()

        return settings

    def get_network_interfaces(self):

        """Get list of available network interfaces"""

        interfaces = []

        try:

            stats = psutil.net_if_stats()
            interfaces = [iface for iface, stats in stats.items() if stats.isup]

        except Exception as e:

            print(f"Error getting network interfaces: {e}")

        return sorted(interfaces)

    def export_error_log(self):

        """Export error log to user-selected location"""

        try:

            if not os.path.exists(ERROR_LOG_FILE):

                QMessageBox.information(self, "Error Log", "No errors have been logged.")

                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suggested_name = f"NetSpeedTray_ErrorLog_{timestamp}.log"
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Error Log",
                suggested_name,
                "Log Files (*.log);;All Files (*.*)"
            )        

            if file_path:

                import shutil

                shutil.copy2(ERROR_LOG_FILE, file_path)
                QMessageBox.information(
                    self,
                    "Success",
                    f"Error log exported to:\n{file_path}"
                )

        except Exception as e:

            QMessageBox.warning(self, "Error", f"Failed to export error log: {e}")


class GraphWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = parent  # Store parent explicitly
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
        # Initialize reusable plot lines
        self.ax = self.figure.add_subplot(111)
        self.upload_line, = self.ax.plot([], [], label='Upload (Mbps)', color='green', linewidth=2)
        self.download_line, = self.ax.plot([], [], label='Download (Mbps)', color='blue', linewidth=2)
        self.ax.set_xlabel('Time')
        self.ax.set_ylabel('Speed (Mbps)')
        self.ax.grid(True, linestyle='--', alpha=0.7)
        self.ax.legend(loc='upper left')

    def update_graph(self, history):
        if not history:
            return
        if self._parent and 'history_minutes' in self._parent.config:
            max_points = int(self._parent.config['history_minutes'] * 60)
        else:
            max_points = MAX_HISTORY_POINTS
        history = list(history)[-max_points:]
        upload = [x[0] * 8 / 1000000 for x in history]
        download = [x[1] * 8 / 1000000 for x in history]
        # Approximate time axis (assuming 1-second intervals)
        times = [datetime.now() - timedelta(seconds=(len(history) - i - 1)) for i in range(len(history))]
        self.upload_line.set_data(times, upload)
        self.download_line.set_data(times, download)
        self.ax.relim()
        self.ax.autoscale_view()
        self.figure.autofmt_xdate()  # Rotate and format dates
        self.canvas.draw()


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
        self.widget.initialize_position()
        self.widget.show()
        signal.signal(signal.SIGINT, self.signal_handler)
        self.logger = logging.getLogger('NetSpeedTray')

    def signal_handler(self, sig, frame):
        self.logger.debug("Received SIGINT, shutting down")
        self.widget.close()  # Trigger closeEvent
        # Release the mutex when shutting down
        if hasattr(self, 'mutex'):
            win32api.CloseHandle(self.mutex)
        sys.exit(0)

    def run(self):
        return self.app.exec()

if __name__ == "__main__":
    monitor = NetworkMonitor()
    sys.exit(monitor.run())


# Development roadmap

# TODO: Implement data export functionality
# TODO: Add bandwidth alerts and usage tracking
# TODO: Implement automatic updates
# TODO: Implement smart taskbar positioning
# TODO: double click on widget to open detailed graph & stats
