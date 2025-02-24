# Standard library imports
import os, sys, json, re, logging, platform, winreg, traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Dict

# Third-party imports
import win32gui, win32con, win32event, win32api, winerror, psutil, signal
import matplotlib
matplotlib.use('qtagg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import matplotlib.pyplot as plt

# Qt imports - grouped by functionality
from PyQt6.QtCore import Qt, QTimer, QPoint
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

APP_VERSION = "1.0.2-beta.2"

DEFAULT_FONT = "Segoe UI"

DEFAULT_FONT_SIZE = 9

DEFAULT_UPDATE_RATE = 1

MAX_HISTORY_POINTS = 3600

MAX_GRAPH_POINTS = 1000



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

    'color_coding': False,          # Enable/disable speed color coding

    'high_speed_threshold': 5.0,    # Threshold for high speed in Mbps

    'low_speed_threshold': 1.0,     # Threshold for low speed in Mbps

    'high_speed_color': '#00FF00',  # Color for high speed (green)

    'low_speed_color': '#FFA500',   # Color for low speed (orange)

    'default_color': '#FFFFFF',     # Default text color (white)

    'graph_enabled': False,         # Enable/disable speed graph

    'history_hours': 1,             # Hours of history to keep

    'update_rate': DEFAULT_UPDATE_RATE,  # Update interval in seconds

    'position_x': None,             # Last X position on screen

    'position_y': None,             # Last Y position on screen

    'start_with_windows': False,    # Start application with Windows

    'interface_mode': 'all',        # 'all', 'include', or 'exclude'

    'selected_interfaces': []       # List of interface names to include/exclude

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

        self._init_screen_handling()  # Handles positioning and showing

        self._init_data()

        self._init_ui()

        font = QFont(DEFAULT_FONT, DEFAULT_FONT_SIZE)

        metrics = QFontMetrics(font)

        max_text_width = metrics.horizontalAdvance("↑ 999.9 Mb/s") + 10

        taskbar_height = self.get_taskbar_height()

        self.setFixedSize(max_text_width, taskbar_height)



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

        # self.logger.debug(f"Taskbar HWND: {self.taskbar_hwnd}")

        

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        

        self.dragging = False

        self.offset = QPoint()

        

        QApplication.primaryScreen().geometryChanged.connect(self.handle_screen_change)

        QApplication.primaryScreen().availableGeometryChanged.connect(self.handle_screen_change)

        

        self.load_position()

        self.ensure_visibility()

        self.show()  # Show without forcing topmost here; let ensure_visibility handle it

        # self.logger.debug(f"Initial position: {self.pos()}, Visible: {self.isVisible()}, State: {self.windowState()}")



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

        taskbar_height = self.get_taskbar_height()

        self.setFixedSize(max_text_width, taskbar_height)

        self.context_menu = QMenu(self)

        self.context_menu.addAction("Settings", self.show_settings)

        self.context_menu.addSeparator()

        self.context_menu.addAction("Exit", self.close)

        # self.logger.debug(f"UI setup, Size: {self.size()}")



    def setupTimers(self):

        self.timer = QTimer()

        self.timer.timeout.connect(self.update_stats)

        update_rate = self.config.get('update_rate', DEFAULT_UPDATE_RATE)

        self.timer.start(max(50, update_rate * 1000) if update_rate > 0 else 50)

        

        self.keep_alive_timer = QTimer()

        self.keep_alive_timer.timeout.connect(self.ensure_visibility)

        self.keep_alive_timer.start(100)  # 100ms

        # self.logger.debug("Timers started")

    

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

            new_pos.setY(taskbar_rect[1])  # Lock to taskbar top

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

        # self.logger.debug("Paint event triggered")

    

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

    

    def closeEvent(self, event) -> None:

        # self.logger.debug("Closing widget")

        try:

            self.timer.stop()

            self.keep_alive_timer.stop()

            # self.logger.debug("Stopped timers")

            self.save_position()

            self.save_config()

            if self.graph_window is not None:

                self.graph_window.close()

                self.graph_window = None

            plt.close('all')  # Use plt instead of matplotlib.pyplot

            # self.logger.debug("Closed graph window and matplotlib resources")

        except Exception as e:

            self.logger.error(f"Error during shutdown: {e}")

        finally:

            QApplication.quit()

            # self.logger.debug("QApplication quit called")



    def _draw_background(self, painter):

        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))

    

    def _draw_graph(self, painter):

        if self.speed_history:

            graph_width = self.width()

            graph_height = self.height()

            

            history_points = self.speed_history[-60:]

            if history_points:

                max_speed = max(

                    max(point['upload'] for point in history_points),

                    max(point['download'] for point in history_points)

                )

                if max_speed > 0:

                    painter.setPen(QColor(0, 255, 0, 128))

                    for i in range(len(history_points) - 1):

                        x1 = i * graph_width / (len(history_points) - 1)

                        x2 = (i + 1) * graph_width / (len(history_points) - 1)

                        y1 = graph_height * (1 - history_points[i]['upload'] / max_speed)

                        y2 = graph_height * (1 - history_points[i + 1]['upload'] / max_speed)

                        painter.drawLine(int(x1), int(y1), int(x2), int(y2))

                    

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



    def load_position(self):

        x = self.config.get('position_x')

        y = self.config.get('position_y')

        if x is not None and y is not None:

            self.move(x, y)

        else:

            self.set_default_position()



    def save_position(self):

        self.config['position_x'] = self.x()

        self.config['position_y'] = self.y()

        self.save_config()



    def set_default_position(self):

        try:

            screen = QApplication.primaryScreen().geometry()

            if self.taskbar_hwnd:

                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)

                x = screen.width() - self.width() - 200

                y = taskbar_rect[1]  # On taskbar top edge

            else:

                x = screen.width() - self.width() - 200

                y = screen.height() - self.height() - 40

            self.move(x, y)

            # self.logger.debug(f"Set default position to: {x}, {y}")

            self.show()

        except Exception as e:

            self.logger.error(f"Error in set_default_position: {e}")

            self.move(100, 100)

            self.show()



    def ensure_on_screen(self):

        screen = QApplication.primaryScreen().geometry()

        current_pos = self.pos()

        new_x = max(0, min(current_pos.x(), screen.width() - self.width()))

        new_y = current_pos.y()

        

        if new_x != current_pos.x():

            self.move(new_x, new_y)

            self.save_position()



    def recover_position(self):

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

            

            for key, (min_val, max_val, default) in {

                'update_rate': (0, 60, 1),

                'high_speed_threshold': (0.1, 1000.0, 1.0),

                'low_speed_threshold': (0.0, 999.9, 0.1),

                'history_hours': (1, 24, 1)

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

                    if time_diff > 0:

                        total_upload += (stats.bytes_sent - last_up) / time_diff

                        total_download += (stats.bytes_recv - last_down) / time_diff

                    

                    setattr(self, f'last_upload_{iface}', stats.bytes_sent)

                    setattr(self, f'last_download_{iface}', stats.bytes_recv)

            

            self.upload_speed = total_upload

            self.download_speed = total_download

            self.last_time = current_time

            

            if self.config.get('graph_enabled', False):

                self.speed_history.append({

                    'timestamp': current_time,

                    'upload': total_upload,

                    'download': total_download

                })

                

                max_points = MAX_HISTORY_POINTS

                if len(self.speed_history) > max_points:

                    self.speed_history = self.speed_history[-max_points:]

            

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

            self.save_config()

            

            new_rate = self.config['update_rate']

            self.timer.setInterval(50 if new_rate == 0 else new_rate * 1000)

        

        win32gui.SetWindowPos(self.winId(), win32con.HWND_TOPMOST, 0, 0, 0, 0,

                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)



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

        try:

            QTimer.singleShot(500, self.recover_position)

        except Exception as e:

            print(f"Screen change error: {e}")

            self.set_default_position()



    def is_admin(self):

        import ctypes

        try:

            return ctypes.windll.shell32.IsUserAnAdmin()

        except:

            return False


    def is_fullscreen_app_active(self):
        try:
            hwnd = win32gui.GetForegroundWindow()
            
            # If hwnd is invalid (0) or not a window, return False
            if not hwnd or not win32gui.IsWindow(hwnd):
                return False
            
            # Ignore our own window, taskbar, and desktop
            if (hwnd == self.winId() or 
                hwnd == self.taskbar_hwnd or
                win32gui.GetClassName(hwnd) == "WorkerW" or  # Desktop
                win32gui.GetClassName(hwnd) == "Progman"):   # Program Manager (Desktop)
                return False
            
            # Get window and screen dimensions
            rect = win32gui.GetWindowRect(hwnd)
            screen = QApplication.primaryScreen().geometry()
            
            # Check if window covers the entire screen
            is_fullscreen = (rect[0] <= 0 and 
                            rect[1] <= 0 and 
                            rect[2] >= screen.width() and 
                            rect[3] >= screen.height())
            
            return is_fullscreen
            
        except Exception as e:
            self.logger.error(f"Error checking fullscreen: {e}")
            return False


    def ensure_visibility(self):

        # self.logger.debug(f"Checking visibility, current state: {self.isVisible()}, State: {self.windowState()}")

        if self.is_fullscreen_app_active():

            self.hide()

            # self.logger.debug("Fullscreen app detected, widget hidden")

            return

        

        if not self.isVisible():

            self.show()

            self.raise_()

            # self.logger.debug("Widget was not visible, called show() and raise()")

            

        try:

            if self.taskbar_hwnd and win32gui.IsWindowVisible(self.taskbar_hwnd):

                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)

                current_pos = self.pos()

                # self.logger.debug(f"Taskbar rect: {taskbar_rect}, Current pos: {current_pos}")

                

                if current_pos.y() != taskbar_rect[1]:

                    x = self.config.get('position_x', current_pos.x())

                    y = taskbar_rect[1]

                    # self.logger.debug(f"Adjusting position to: {x}, {y}")

                    self.move(x, y)

                    self.save_position()

                

                self.show()

                # Use taskbar_z instead of HWND_TOPMOST unless settings dialog is open

                taskbar_z = win32gui.GetWindow(self.taskbar_hwnd, win32con.GW_HWNDPREV)

                win32gui.SetWindowPos(self.winId(), taskbar_z, 0, 0, 0, 0, 

                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

                self.update()

                # self.logger.debug(f"Widget shown at: {self.pos()}, Visible: {self.isVisible()}, State: {self.windowState()}")

            else:

                # self.logger.debug("Taskbar not visible or not found, hiding widget")

                self.hide()

                

        except Exception as e:

            self.logger.error(f"Error in ensure_visibility: {e}")

            self.set_default_position()

            self.show()

            self.raise_()

            # self.logger.debug(f"Fallback position: {self.pos()}, Visible: {self.isVisible()}")

    

    def get_taskbar_height(self):

        if self.taskbar_hwnd:

            rect = win32gui.GetWindowRect(self.taskbar_hwnd)

            return rect[3] - rect[1]  # Bottom - Top

        return 40  # Fallback height



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

            # self.logger.error(f"Failed to toggle startup: {str(e)}")

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

        """Enhanced error logging with system information"""

        try:

            error_details = {

                'message': error_msg,

                'timestamp': datetime.now().isoformat(),

                'app_version': APP_VERSION,

                'os_version': platform.platform(),

                'python_version': sys.version,

                'stack_trace': traceback.format_exc() if exc_info else None,

                'config': {k: v for k, v in self.config.items() if k not in ['selected_interfaces']},

                'screen_info': {

                    'resolution': QApplication.primaryScreen().geometry().getRect(),

                    'taskbar_visible': bool(self.taskbar_hwnd and win32gui.IsWindowVisible(self.taskbar_hwnd))

                }

            }

            self.logger.error(f"{json.dumps(error_details, indent=2)}")

        except Exception as e:

            print(f"Failed to log error: {e}")



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

        self.setup_ui()



    def setup_ui(self):

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

        self.original_pos = None

        

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

                

                if self.original_pos:

                    self.move(self.original_pos)

                

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

        current_minutes = self.config.get('history_hours', 1) * 60

        

        for minutes, label in TIME_INTERVALS:

            self.history_duration.addItem(label, minutes)

            if minutes == current_minutes:

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

            # self.logger.debug(f"Mode changed to: {text}, Enabled: {enabled}, ComboBox focused: {self.interface_mode.hasFocus()}")

        

        self.interface_mode.currentTextChanged.connect(update_interface_list_state)

        # self.interface_mode.activated.connect(lambda: self.logger.debug("Mode dropdown activated"))

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

        color = QColorDialog.getColor()

        if (color.isValid()):

            button.setStyleSheet(f"background-color: {color.name()}")



    def get_settings(self):

        selected_minutes = self.history_duration.currentData()

        hours = selected_minutes / 60

        

        settings = {

            'update_rate': self.update_rate.value(),

            'color_coding': self.enable_colors.isChecked(),

            'high_speed_threshold': self.high_speed_threshold.value(),

            'low_speed_threshold': self.low_speed_threshold.value(),

            'high_speed_color': self.high_speed_color.palette().button().color().name(),

            'low_speed_color': self.low_speed_color.palette().button().color().name(),

            'default_color': self.default_color.palette().button().color().name(),

            'graph_enabled': self.enable_graph.isChecked(),

            'history_hours': hours,

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

        upload = [x['upload']*8/1000000 for x in history]

        download = [x['download']*8/1000000 for x in history]

        

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
        # Create a unique mutex name (should be globally unique for your app)
        mutex_name = "Global\\NetSpeedTray_SingleInstanceMutex"
        
        # Attempt to create the mutex
        self.mutex = win32event.CreateMutex(None, False, mutex_name)
        last_error = win32api.GetLastError()
        
        # If the mutex already exists, another instance is running
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            print("Another instance of NetSpeedTray is already running.")
            # Optionally, bring the existing instance to the foreground (requires additional code)
            sys.exit(1)  # Exit the new instance
        
        # Proceed with initialization if this is the only instance
        self.app = QApplication(sys.argv)
        self.widget = NetworkSpeedWidget()
        self.widget.load_position()
        self.widget.show()
        signal.signal(signal.SIGINT, self.signal_handler)
        
        # Store the logger for use in signal_handler
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
