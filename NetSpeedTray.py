import sys
import os
import json
import re
import logging
import winreg
import shutil
import csv
from datetime import datetime, timedelta, MINYEAR
from typing import Dict, List, Union, Optional
from collections import deque
from types import FrameType as frame

import psutil
import signal
import win32api
import win32con
import win32event
import win32gui
import winerror
import pythoncom
from win32com.shell import shell
import numpy as np
import threading

from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QColor, QFontMetrics, QIcon
from PyQt6.QtWidgets import (
    QApplication, QMenu, QDialog, QVBoxLayout, QGridLayout, QGroupBox, QCheckBox,
    QPushButton, QLabel, QWidget, QFileDialog, QMessageBox, QColorDialog, QSlider, QHBoxLayout, QSpacerItem, QSizePolicy
)

import matplotlib
matplotlib.use('qtagg')
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.dates import DateFormatter, MinuteLocator, DayLocator, HourLocator
from matplotlib.ticker import MaxNLocator
import matplotlib.pyplot as plt

from logging.handlers import RotatingFileHandler

def define_classes():
    """
    Define and return the classes for NetSpeedTray: NetworkSpeedWidget, SettingsDialog, and GraphWindow.
    """
    class NetworkSpeedWidget(QWidget):
        """
        A widget to display network speed in the Windows taskbar, with graph and settings functionality.
        """
        font_updated = pyqtSignal()

        def __init__(self) -> None:
            """Initialize the NetworkSpeedWidget with default values and setup."""
            super().__init__(None)
            if sys.executable.endswith("python.exe"):
                import ctypes
                hwnd = ctypes.windll.kernel32.GetConsoleWindow()
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 0)
                    style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style | win32con.WS_EX_TOOLWINDOW)
                    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)

            # Initialize caches and locks
            self._taskbar_rect_cache: Optional[tuple] = None
            self._tray_rect_cache: Optional[tuple] = None
            self._last_taskbar_state = None  # Cache for taskbar state
            self._csv_lock = threading.Lock()  # Thread-safe lock for CSV

            self.logger = self.setup_error_logging()
            self._init_icon()
            self._init_config()
            self.initial_position_set = False
            self.dragging = False
            self.offset = QPoint()
            self._init_screen_handling()
            self._init_data()
            self._init_ui()

            self.default_color = QColor('#FFFFFF')
            self.high_color = QColor('#00FF00')
            self.low_color = QColor('#FFA500')
            self.font = QFont(self.DEFAULT_FONT, self.config.get('font_size', self.DEFAULT_FONT_SIZE),
                            QFont.Weight(self.config.get('font_weight', QFont.Weight.DemiBold)))
            self.setFont(self.font)
            self.metrics = QFontMetrics(self.font)
            self.arrow_width = self.metrics.horizontalAdvance("↑ ")

            max_text_width = self.metrics.horizontalAdvance("↑ 999.9 MB/s") + self.metrics.averageCharWidth()
            taskbar_height = self.get_taskbar_height()
            self.setFixedSize(max_text_width, taskbar_height)
            self.first_update = True
            self.speed_variance = deque(maxlen=self.MAX_HISTORY_POINTS)
            self._cached_interfaces = self.get_network_interfaces()  # Cache interfaces
            self.font_updated.connect(self.update)  # Connect signal to update

        def initialize_with_saved_position(self):
            try:
                if not self.taskbar_hwnd or not win32gui.IsWindowVisible(self.taskbar_hwnd):
                    self.logger.warning("Taskbar not visible, retrying in 500ms")
                    QTimer.singleShot(500, self.initialize_with_saved_position)
                    return
                self.use_saved_position()
                self.initial_position_set = True
                self.show()
                self.ensure_topmost()  # Set initial topmost state
                self.update()
                self.setupTimers()
            except Exception as e:
                self.logger.error(f"Error in initial positioning: {e}")
                QTimer.singleShot(500, self.initialize_with_saved_position)

        def _init_icon(self) -> None:
            """Initialize the application icon."""
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(base_path, 'NetSpeedTray.ico')
            if os.path.exists(icon_path):
                self.app_icon = QIcon(icon_path)
                self.setWindowIcon(self.app_icon)
                QApplication.setWindowIcon(self.app_icon)
            else:
                self.logger.warning(f"Icon file not found at: {icon_path}")

        def _init_config(self) -> None:
            """Initialize configuration from file or use defaults."""
            app_data = self.get_app_data_path()
            self.config_file = os.path.join(app_data, 'netspeedtray.conf')
            self.log_file = os.path.join(app_data, 'netspeedtray.log')
            self.config = self.load_config()

        def _init_screen_handling(self) -> None:
            """Initialize taskbar and tray window handles."""
            self.taskbar_hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
            self.tray_hwnd = win32gui.FindWindowEx(self.taskbar_hwnd, None, "TrayNotifyWnd", None) if self.taskbar_hwnd else None
            if not self.taskbar_hwnd:
                self.logger.error("Could not find primary taskbar (Shell_TrayWnd)")
            if not self.tray_hwnd:
                self.logger.warning("Could not find tray window (TrayNotifyWnd)")
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        def _init_ui(self) -> None:
            """Initialize the UI components."""
            self.setupUI()

        def _init_data(self) -> None:
            """Initialize data structures for network speed tracking."""
            self.upload_speed = self.download_speed = 0
            self.last_upload = self.last_download = 0
            self.last_time = datetime.now()
            self.speed_history = deque()
            self.graph_window = None

        def setupUI(self):
            font = QFont(self.DEFAULT_FONT, self.config.get('font_size', self.DEFAULT_FONT_SIZE),
                        QFont.Weight(self.config.get('font_weight', QFont.Weight.DemiBold)))
            self.setFont(font)
            self.metrics = QFontMetrics(font)
            self.arrow_width = self.metrics.horizontalAdvance("↑ ")
            max_text_width = self.metrics.horizontalAdvance("↑ 999.9 MB/s") + self.metrics.averageCharWidth()
            taskbar_height = self.get_taskbar_height()
            self.setFixedSize(max_text_width, taskbar_height)
            self.context_menu = QMenu(self)
            self.context_menu.addAction("Settings", self.show_settings)
            self.context_menu.addSeparator()
            self.context_menu.addAction("Exit", self.close)

        def setupTimers(self) -> None:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.check_and_update)
            self.timer.start(100)
            self.speed_timer = QTimer(self)
            self.speed_timer.timeout.connect(self.update_stats)
            update_rate = self.config.get('update_rate', self.DEFAULT_UPDATE_RATE)
            self.speed_timer.start(self.get_timer_interval(update_rate))
            # Removed: self.force_show_timer and its connections

        def get_timer_interval(self, update_rate: float) -> int:
            """Calculate the timer interval based on update rate."""
            if update_rate == 0:  # Smart mode
                return 2000  # 2 seconds base interval
            return int(update_rate * 1000)

        def use_saved_position(self):
            try:
                screen = self.find_taskbar_screen()
                screen_geometry = screen.availableGeometry()
                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd) if self.taskbar_hwnd else None
                tray_rect = win32gui.GetWindowRect(self.tray_hwnd) if self.tray_hwnd else None
                
                if not taskbar_rect:
                    self.logger.error("Taskbar rect not found, using default position")
                    x = screen_geometry.width() - self.width() - 5
                    y = screen_geometry.height() - self.height()
                else:
                    if tray_rect and win32gui.IsWindow(self.tray_hwnd):
                        x = tray_rect[0] - self.width() - 5  # Left of tray
                    else:
                        x = taskbar_rect[2] - self.width() - 5  # Right edge if no tray
                    y = taskbar_rect[1]  # Top of taskbar
                    
                    # Check saved position, override if invalid
                    saved_x = self.config.get('position_x')
                    saved_y = self.config.get('position_y')
                    if (saved_x is not None and saved_y is not None and
                        taskbar_rect[0] <= saved_x <= taskbar_rect[2] - self.width() and
                        saved_y == taskbar_rect[1]):
                        x = int(saved_x)
                        y = int(saved_y)
                    else:
                        self.save_position()
                
                self.move(x, y)
                self.taskbar_y = y
                self.logger.debug(f"Positioned widget at: x={x}, y={y}")
            except Exception as e:
                self.logger.error(f"Error using saved position: {e}")
                screen = QApplication.primaryScreen()
                x = screen.geometry().width() - self.width() - 5
                y = screen.geometry().height() - self.height()
                self.move(x, y)
                self.taskbar_y = y

        def _get_cached_taskbar_rect(self) -> Optional[tuple]:
            """Get cached taskbar rectangle."""
            if self._taskbar_rect_cache is None and self.taskbar_hwnd:
                self._taskbar_rect_cache = win32gui.GetWindowRect(self.taskbar_hwnd)
            return self._taskbar_rect_cache

        def _get_cached_tray_rect(self) -> Optional[tuple]:
            """Get cached tray rectangle."""
            if self._tray_rect_cache is None and self.tray_hwnd:
                self._tray_rect_cache = win32gui.GetWindowRect(self.tray_hwnd)
            return self._tray_rect_cache

        def update_position(self):
            if not self.initial_position_set or self.dragging:
                return
            try:
                screen, taskbar_hwnd = self.find_taskbar_screen_dynamic()
                if taskbar_hwnd != self.taskbar_hwnd:
                    self.taskbar_hwnd = taskbar_hwnd
                    self.tray_hwnd = win32gui.FindWindowEx(self.taskbar_hwnd, None, "TrayNotifyWnd", None)
                
                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd) if self.taskbar_hwnd else None
                tray_rect = win32gui.GetWindowRect(self.tray_hwnd) if self.tray_hwnd else None
                
                if not taskbar_rect:
                    self.logger.error("Taskbar rect not found in update")
                    return
                
                if tray_rect and win32gui.IsWindow(self.tray_hwnd):
                    x = tray_rect[0] - self.width() - 5  # Left of tray
                else:
                    x = taskbar_rect[2] - self.width() - 5  # Fallback to right edge
                y = taskbar_rect[1]  # Always top of taskbar
                
                current_pos = self.pos()
                if current_pos.x() != x or current_pos.y() != y:
                    self.move(x, y)
                    self.taskbar_y = y
                    self.save_position()
                    self.logger.debug(f"Widget repositioned to: x={x}, y={y}")
            except Exception as e:
                self.logger.error(f"Error positioning widget: {e}")

        def ensure_topmost(self):
            hwnd = int(self.winId())
            current_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if not (current_style & win32con.WS_EX_TOPMOST):
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                    current_style | win32con.WS_EX_TOPMOST)
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

        def find_taskbar_screen(self) -> 'QScreen':
            """Find the screen containing the taskbar."""
            taskbar_rect = self._get_cached_taskbar_rect()
            if not taskbar_rect:
                return QApplication.primaryScreen()
            for screen in QApplication.screens():
                if screen.geometry().intersects(QRect(taskbar_rect[0], taskbar_rect[1],
                                                    taskbar_rect[2] - taskbar_rect[0],
                                                    taskbar_rect[3] - taskbar_rect[1])):
                    return screen
            return QApplication.primaryScreen()

        def find_taskbar_screen_dynamic(self):
            try:
                taskbars = []
                def enum_windows(hwnd, results):
                    if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "Shell_TrayWnd":
                        rect = win32gui.GetWindowRect(hwnd)
                        results.append((hwnd, rect))
                win32gui.EnumWindows(enum_windows, taskbars)
                
                if not taskbars:
                    self.logger.error("No taskbars found")
                    return QApplication.primaryScreen(), self.taskbar_hwnd
                
                # Find closest taskbar to current widget position
                widget_pos = self.mapToGlobal(QPoint(0, 0))
                closest_taskbar = min(taskbars, key=lambda t: abs(widget_pos.y() - (t[1][1] + t[1][3]) / 2),
                                    default=(self.taskbar_hwnd, win32gui.GetWindowRect(self.taskbar_hwnd)))
                
                taskbar_hwnd, taskbar_rect = closest_taskbar
                for screen in QApplication.screens():
                    if screen.geometry().intersects(QRect(taskbar_rect[0], taskbar_rect[1],
                                                        taskbar_rect[2] - taskbar_rect[0],
                                                        taskbar_rect[3] - taskbar_rect[1])):
                        return screen, taskbar_hwnd
                return QApplication.primaryScreen(), taskbar_hwnd
            except Exception as e:
                self.logger.error(f"Error in dynamic taskbar search: {e}")
                return QApplication.primaryScreen(), self.taskbar_hwnd

        def get_taskbar_height(self):
            if self.taskbar_hwnd:
                rect = win32gui.GetWindowRect(self.taskbar_hwnd)
                height = rect[3] - rect[1]  # Physical height
                screen = self.find_taskbar_screen()
                logical_dpi = screen.logicalDotsPerInchY() / 96.0
                scaled_height = int(height / logical_dpi)
                self.logger.debug(f"Taskbar height: {scaled_height}px (physical: {height}, DPI scale: {logical_dpi})")
                return scaled_height
            self.logger.warning("No taskbar found, using default height")
            return 40

        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.dragging = True
                self.offset = event.position().toPoint()
                self.timer.stop()  # Only stop the existing timer
                event.accept()

        def mouseMoveEvent(self, event) -> None:
            if self.dragging:
                try:
                    new_pos = event.globalPosition().toPoint() - self.offset
                    screen, taskbar_hwnd = self.find_taskbar_screen_dynamic()
                    taskbar_rect = self._get_cached_taskbar_rect() if taskbar_hwnd == self.taskbar_hwnd else win32gui.GetWindowRect(taskbar_hwnd)
                    screen_geometry = screen.availableGeometry()
                    x = max(taskbar_rect[0], min(new_pos.x(), taskbar_rect[2] - self.width()))
                    y = taskbar_rect[1] if taskbar_rect[1] >= screen_geometry.y() else taskbar_rect[3] - self.height()
                    self.move(x, y)
                    self.taskbar_hwnd = taskbar_hwnd
                    self.tray_hwnd = win32gui.FindWindowEx(self.taskbar_hwnd, None, "TrayNotifyWnd", None)
                    self._taskbar_rect_cache = taskbar_rect
                    self._tray_rect_cache = None
                    event.accept()
                except Exception as e:
                    self.logger.error(f"Mouse move error: {str(e)}")
                    self.dragging = False
                    self.timer.start(100)  # Restart only the main timer

        def mouseReleaseEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.dragging = False
                self.save_position()
                self.timer.start(100)  # Restart only the main timer
                event.accept()

        def mouseDoubleClickEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.logger.debug("Double-click detected, calling show_graph")
                self.show_graph()
                event.accept()

        def contextMenuEvent(self, event) -> None:
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

        def closeEvent(self, event) -> None:
            """Handle window close."""
            try:
                self.timer.stop()
                self.speed_timer.stop()  # Only stop existing timers
                self.speed_history.clear()
                self.speed_variance.clear()
                self.save_config()
                if self.graph_window:
                    self.graph_window.close()
                    self.graph_window = None
                plt.close('all')
                win32gui.ShowWindow(int(self.winId()), win32con.SW_HIDE)
                win32gui.SetWindowLong(int(self.winId()), win32con.GWL_EXSTYLE, win32con.WS_EX_TOOLWINDOW)
                self._taskbar_rect_cache = None
                self._tray_rect_cache = None
            except Exception as e:
                self.logger.error(f"Error during shutdown: {str(e)}", exc_info=True)
            finally:
                QApplication.quit()

        def update_stats(self) -> None:
            """Update network speed statistics and history."""
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
                    self.upload_speed = self.download_speed = 0
                    self.first_update = False
                else:
                    self.upload_speed = total_upload
                    self.download_speed = total_download
                self.last_time = current_time
                if self.config.get('graph_enabled', False) and not self.first_update:
                    self.speed_history.append((current_time, total_upload, total_download))
                    history_seconds = self.config.get('history_minutes', 30) * 60
                    cutoff_time = current_time - timedelta(seconds=history_seconds)
                    while self.speed_history and self.speed_history[0][0] < cutoff_time:
                        self.speed_history.popleft()
                    while len(self.speed_history) > self.MAX_HISTORY_POINTS:
                        self.speed_history.popleft()
                    self.log_to_csv(current_time, total_upload, total_download)
                if self.config.get('update_rate', self.DEFAULT_UPDATE_RATE) == 0:
                    speed_total = total_upload + total_download
                    self.speed_variance.append(speed_total)
                    if len(self.speed_variance) == 10:
                        valid_speeds = [s for s in self.speed_variance if isinstance(s, (int, float)) and not np.isnan(s) and not np.isinf(s)]
                        if valid_speeds:
                            variance = np.std(valid_speeds)
                            threshold = self.config.get('smart_threshold', 100000)
                            interval = 5000 if variance < threshold else 2000  # Adjust around 2s base
                            self.speed_timer.setInterval(interval)
                        else:
                            self.logger.warning("No valid speed data in speed_variance")
                            self.speed_timer.setInterval(2000)
                self.update()
                if len(self.speed_history) % 100 == 0:
                    process = psutil.Process()
                    mem_info = process.memory_info()
                    self.logger.debug(f"Memory usage: RSS={mem_info.rss / 1024 / 1024:.2f} MB")
            except Exception as e:
                self.logger.error(f"Error updating stats: {str(e)}")

        def log_to_csv(self, timestamp: datetime, upload: float, download: float) -> None:
            """Log network speed data to CSV with reduced precision."""
            try:
                app_data = self.get_app_data_path()
                csv_path = os.path.join(app_data, 'nst_history.csv')
                
                with self._csv_lock:
                    keep_days = self.config.get('keep_data', 31)
                    cutoff = datetime.now() - timedelta(days=keep_days) if keep_days > 0 else None
                    exists = os.path.exists(csv_path)
                    
                    with open(csv_path, 'a' if exists else 'w', newline='') as f:
                        writer = csv.writer(f)
                        if not exists:
                            writer.writerow(['Timestamp', 'Upload (B/s)', 'Download (B/s)'])
                        writer.writerow([timestamp.replace(microsecond=0).isoformat(), 
                                    round(upload, 2), 
                                    round(download, 2)])
                    
                    if keep_days > 0 and exists:
                        with open(csv_path, 'r', newline='') as f:
                            reader = csv.reader(f)
                            lines = list(reader)
                        headers = lines[0]
                        data = [row for row in lines[1:] if datetime.fromisoformat(row[0]) >= cutoff]
                        with open(csv_path, 'w', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow(headers)
                            writer.writerows(data)
            except Exception as e:
                self.logger.error(f"Error logging to CSV: {str(e)}")

        def check_and_update(self):
            try:
                # Step 1: Check if taskbar is visible
                is_taskbar_visible = win32gui.IsWindowVisible(self.taskbar_hwnd)
                if not is_taskbar_visible:
                    if self.isVisible():
                        self.hide()
                    return

                # Step 2: Check for fullscreen application
                is_fullscreen = self.is_fullscreen_app_active()
                
                # Step 3: Update visibility and position
                if is_taskbar_visible and not is_fullscreen:
                    if not self.isVisible():
                        self.show()
                    if not self.dragging:
                        self.update_position()
                    self.ensure_topmost()  # Keep above taskbar
                elif is_fullscreen:
                    if self.isVisible():
                        self.hide()

            except Exception as e:
                self.logger.error(f"Error in check_and_update: {e}")

        def is_fullscreen_app_active(self):
            try:
                hwnd = win32gui.GetForegroundWindow()
                if not hwnd or hwnd == int(self.winId()):
                    return False

                # Exclude taskbar and its children
                current_hwnd = hwnd
                while current_hwnd:
                    if current_hwnd == self.taskbar_hwnd:
                        return False  # Taskbar or its child is foreground, not fullscreen
                    current_hwnd = win32gui.GetParent(current_hwnd)

                # Exclude desktop
                class_name = win32gui.GetClassName(hwnd)
                if class_name in ("Progman", "WorkerW"):
                    return False

                # Get taskbar screen dimensions
                taskbar_rect = win32gui.GetWindowRect(self.taskbar_hwnd)
                screen_width = taskbar_rect[2] - taskbar_rect[0]
                screen_height = taskbar_rect[3] - taskbar_rect[1]
                screen_x = taskbar_rect[0]
                screen_y = taskbar_rect[1]

                rect = win32gui.GetWindowRect(hwnd)
                covers_screen = (rect[0] <= screen_x and
                                rect[2] >= screen_x + screen_width and
                                rect[1] <= screen_y and
                                rect[3] >= screen_y + screen_height)
                
                return covers_screen and win32gui.IsWindowVisible(hwnd)
            
            except Exception as e:
                self.logger.error(f"Error checking fullscreen: {e}")
                return False

        def paintEvent(self, event) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
            if self.config.get('graph_enabled', False):
                self._draw_graph(painter)
            self._draw_speed_text(painter)

        def _draw_graph(self, painter: QPainter) -> None:
            """Draw a mini graph of network speeds."""
            if not self.speed_history or not self.config.get('graph_enabled', False):
                return
            graph_width = self.width()
            graph_height = self.height()
            num_points = len(self.speed_history)
            max_points = 100
            if num_points > max_points:
                step = num_points // max_points
                sampled_history = list(self.speed_history)[::step][:max_points]
            else:
                sampled_history = list(self.speed_history)
            if sampled_history:
                times, uploads, downloads = zip(*sampled_history)
                max_upload = max(uploads) if uploads else 0
                max_download = max(downloads) if downloads else 0
                max_speed = max(max_upload, max_download) if max_upload > 0 or max_download > 0 else 0.01
                opacity = max(25, self.config.get('graph_opacity', 100) * 255 // 100)
                painter.setPen(QColor(0, 204, 0, opacity))
                for i in range(len(sampled_history) - 1):
                    x1 = i * graph_width / (len(sampled_history) - 1)
                    x2 = (i + 1) * graph_width / (len(sampled_history) - 1)
                    y1 = graph_height * (1 - uploads[i] / max_speed)
                    y2 = graph_height * (1 - uploads[i + 1] / max_speed)
                    painter.drawLine(int(x1), int(y1), int(x2), int(y2))
                painter.setPen(QColor(0, 120, 255, opacity))
                for i in range(len(sampled_history) - 1):
                    x1 = i * graph_width / (len(sampled_history) - 1)
                    x2 = (i + 1) * graph_width / (len(sampled_history) - 1)
                    y1 = graph_height * (1 - downloads[i] / max_speed)
                    y2 = graph_height * (1 - downloads[i + 1] / max_speed)
                    painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        def _draw_speed_text(self, painter: QPainter) -> None:
            """Draw upload and download speeds."""
            painter.setFont(self.font)
            text_height = self.metrics.height()
            widget_height = self.height()
            arrow_width = self.arrow_width
            arrow_x = 5
            text_x = arrow_x + arrow_width
            total_text_height = 2 * text_height + 2
            base_y = (widget_height - total_text_height) // 2
            top_text_y = base_y
            bottom_text_y = base_y + text_height + 2
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

        def save_position(self) -> None:
            """Save the current widget position."""
            self.config['position_x'] = self.x()
            self.config['position_y'] = self.y()
            self.save_config()

        def format_speed(self, bytes_per_sec: float) -> str:
            """Format network speed."""
            if self.config.get('use_megabytes', False):
                bytes_val = bytes_per_sec / 1000000
                if bytes_val >= 1000:
                    return f"{bytes_val / 1000:5.1f} GB/s"
                elif bytes_val >= 1:
                    return f"{bytes_val:5.1f} MB/s"
                else:
                    return f"{bytes_val * 1000:5.1f} KB/s"
            else:
                bits_per_sec = bytes_per_sec * 8
                if bits_per_sec >= 1000000:
                    return f"{bits_per_sec / 1000000:5.1f} Mb/s"
                elif bits_per_sec >= 1000:
                    return f"{bits_per_sec / 1000:5.1f} Kb/s"
                else:
                    return f"{bits_per_sec:5.0f} b/s"

        def get_speed_color(self, speed: float) -> QColor:
            if not self.config.get('color_coding', False):
                return self.default_color  # Use instance variable
            speed_val = speed / 1000000 if self.config.get('use_megabytes', False) else speed * 8 / 1000000
            if speed_val > self.config.get('high_speed_threshold', 5.0):
                return self.high_color
            elif speed_val > self.config.get('low_speed_threshold', 1.0):
                return self.low_color
            return self.default_color

        def load_config(self) -> Dict:
            """Load configuration from file."""
            try:
                if os.path.exists(self.config_file):
                    with open(self.config_file, 'r') as f:
                        config = json.load(f)
                        if 'history_hours' in config:
                            self.logger.info("Migrating history_hours to history_minutes")
                            config['history_minutes'] = int(config.pop('history_hours') * 60)
                        if 'keep_data_days' in config:
                            self.logger.info("Migrating keep_data_days to keep_data")
                            config['keep_data'] = config.pop('keep_data_days')
                        if 'stats_period' in config:
                            self.logger.info("Migrating stats_period to history_period")
                            config['history_period'] = config.pop('stats_period')
                        for key in ['position_x', 'position_y']:
                            if key in config and config[key] is not None:
                                config[key] = int(config[key])
                        return self.validate_config({**self.DEFAULT_CONFIG, **config})
            except Exception as e:
                self.logger.error(f"Error loading config: {str(e)}")
            return self.DEFAULT_CONFIG

        def save_config(self) -> None:
            """Save the current configuration."""
            try:
                with open(self.config_file, 'w') as f:
                    json.dump(self.config, f, indent=4)
            except Exception as e:
                self.logger.error(f"Error saving config: {str(e)}")

        def validate_config(self, config: Dict) -> Dict:
            """Validate and sanitize configuration."""
            try:
                validated = config.copy()
                for key, (min_val, max_val, default) in {
                    'update_rate': (0, 10, 0),
                    'high_speed_threshold': (0.1, 1000.0, 5.0),
                    'low_speed_threshold': (0.0, 999.9, 1.0),
                    'history_minutes': (5, 720, 30),
                    'graph_opacity': (10, 100, 30),
                    'keep_data': (1, 365, 31),
                    'smart_threshold': (10000, 1000000, 100000),
                    'font_size': (7, 11, 9),
                    'font_weight': (0, 5, 4)
                }.items():
                    validated[key] = max(min_val, min(max_val, config.get(key, default)))
                for color_key in ['high_speed_color', 'low_speed_color', 'default_color']:
                    color = config.get(color_key, '#FFFFFF')
                    if not re.match(r'^#(?:[0-9a-fA-F]{3}){1,2}$', color):
                        validated[color_key] = '#FFFFFF'
                validated['history_period'] = config.get('history_period', 'System Uptime') if config.get('history_period') in ['Session', '24h', '1 Week', '1 Month', 'All', 'System Uptime'] else 'System Uptime'
                validated['legend_position'] = config.get('legend_position', 'off') if config.get('legend_position') in ['off', 'left', 'middle', 'right'] else 'off'
                validated['dark_mode'] = bool(config.get('dark_mode', False))
                return validated
            except Exception as e:
                raise ValueError(f"Configuration validation failed: {str(e)}")

        def update_colors(self) -> None:
            try:
                self.default_color = QColor(self.config.get('default_color', '#FFFFFF'))
                self.high_color = QColor(self.config.get('high_speed_color', '#00FF00'))
                self.low_color = QColor(self.config.get('low_speed_color', '#FFA500'))
            except Exception as e:
                self.logger.error(f"Error updating colors: {str(e)}")

        def show_settings(self) -> None:
            """Show the settings dialog."""
            dialog = SettingsDialog(self, self.config)
            if hasattr(self, 'app_icon'):
                dialog.setWindowIcon(self.app_icon)
            widget_pos = self.mapToGlobal(QPoint(0, 0))
            dialog_width = dialog.sizeHint().width()
            dialog_height = dialog.sizeHint().height()
            screen = self.find_taskbar_screen().geometry()
            dialog_x = widget_pos.x() + (self.width() // 2) - (dialog_width // 2)
            dialog_y = widget_pos.y() - dialog_height - 10
            dialog_x = max(screen.left(), min(dialog_x, screen.right() - dialog_width))
            dialog_y = max(screen.top(), min(dialog_y, screen.bottom() - dialog_height))
            dialog.move(dialog_x, dialog_y)
            dialog.show()
            dialog.raise_()
            if dialog.exec():
                old_config = self.config.copy()
                self.config = dialog.get_settings()
                try:
                    self.save_config()
                    new_rate = self.config['update_rate']
                    self.speed_timer.setInterval(self.get_timer_interval(new_rate))
                    self.update_font()
                    self.update_colors()  # Add this
                    self.logger.debug("Settings saved successfully")
                except Exception as e:
                    self.logger.error(f"Failed to apply new config: {str(e)}")
                    self.config = old_config
                    self.save_config()
            self.setVisible(True)
            self.raise_()
            win32gui.SetWindowPos(self.winId(), win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

        def update_font(self) -> None:
                try:
                    self.font = QFont(self.DEFAULT_FONT, self.config['font_size'], QFont.Weight(self.weight_to_value(self.config['font_weight'])))
                    self.setFont(self.font)
                    self.metrics = QFontMetrics(self.font)
                    self.arrow_width = self.metrics.horizontalAdvance("↑ ")
                    max_text_width = self.metrics.horizontalAdvance("↑ 999.9 MB/s") + self.metrics.averageCharWidth()
                    taskbar_height = self.get_taskbar_height()
                    self.setFixedSize(max_text_width, taskbar_height)
                    self.font_updated.emit()  # Emit signal instead of direct update
                except Exception as e:
                    self.logger.error(f"Error updating font: {e}", exc_info=True)

        def get_network_interfaces(self) -> List[str]:
            """Retrieve a list of active network interfaces."""
            try:
                stats = psutil.net_if_stats()
                return sorted([iface for iface, stats in stats.items() if stats.isup])
            except Exception as e:
                self.logger.error(f"Error getting network interfaces: {str(e)}")
                return []

        def weight_to_value(self, slider_value: int) -> int:
            """Convert slider value to QFont weight."""
            weights = [100, 200, 300, 400, 500, 700]
            return weights[slider_value]

        def show_graph(self) -> None:
            """Show the network speed history graph."""
            try:
                self.logger.debug("Entering show_graph")
                if not self.config.get('graph_enabled', False):
                    self.logger.warning("Graph not enabled in settings")
                    QMessageBox.warning(self, "Graph Disabled", "Please enable the graph in Settings to view network speed history.")
                    return
                if not self.graph_window:
                    self.logger.debug("Creating new GraphWindow")
                    self.graph_window = GraphWindow(self)
                if self.speed_history:
                    self.logger.debug(f"Updating graph with {len(self.speed_history)} data points")
                    self.graph_window.update_graph(self.speed_history)
                    self.logger.debug("Showing GraphWindow")
                    self.graph_window.show()
                    self.graph_window.raise_()
                else:
                    self.logger.warning("No speed history available")
                    QMessageBox.information(self, "No Data", "No network speed history available yet.")
            except Exception as e:
                self.logger.error(f"Error in show_graph: {str(e)}", exc_info=True)
                QMessageBox.critical(self, "Error", f"Failed to open graph: {str(e)}")

        def toggle_startup(self, enable: Optional[bool] = None) -> bool:
            """Toggle startup with Windows."""
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "NetSpeedTray"
            startup_folder = os.path.join(os.getenv('APPDATA'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
            portable_path = os.path.abspath(sys.executable) if getattr(sys, 'frozen', False) else sys.argv[0]
            try:
                if getattr(sys, 'frozen', False):
                    shortcut_path = os.path.join(startup_folder, f"{app_name}.lnk")
                    os.makedirs(startup_folder, exist_ok=True)
                    if enable is None:
                        if os.path.exists(shortcut_path):
                            os.remove(shortcut_path)
                            return False
                        else:
                            shell_link = pythoncom.CoCreateInstance(
                                shell.CLSID_ShellLink, None, pythoncom.CLSCTX_ALL, shell.IID_IShellLink
                            )
                            shell_link.SetPath(portable_path)
                            shell_link.SetWorkingDirectory(os.path.dirname(portable_path))
                            shell_link.SetIconLocation(portable_path, 0)
                            shell_link.SetDescription("Launch NetSpeedTray on startup")
                            persist_file = shell_link.QueryInterface(pythoncom.IID_IPersistFile)
                            persist_file.Save(shortcut_path, 0)
                            return True
                    elif enable:
                        shell_link = pythoncom.CoCreateInstance(
                            shell.CLSID_ShellLink, None, pythoncom.CLSCTX_ALL, shell.IID_IShellLink
                        )
                        shell_link.SetPath(portable_path)
                        shell_link.SetWorkingDirectory(os.path.dirname(portable_path))
                        shell_link.SetIconLocation(portable_path, 0)
                        shell_link.SetDescription("Launch NetSpeedTray on startup")
                        persist_file = shell_link.QueryInterface(pythoncom.IID_IPersistFile)
                        persist_file.Save(shortcut_path, 0)
                        return True
                    else:
                        if os.path.exists(shortcut_path):
                            os.remove(shortcut_path)
                        return False
                else:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
                    try:
                        if enable is None:
                            is_enabled = self.is_startup_enabled()
                            if is_enabled:
                                winreg.DeleteValue(key, app_name)
                                return False
                            else:
                                exe_path = os.path.abspath(sys.executable) if getattr(sys, 'frozen', False) else sys.argv[0]
                                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe_path}"')
                                return True
                        elif enable:
                            exe_path = os.path.abspath(sys.executable) if getattr(sys, 'frozen', False) else sys.argv[0]
                            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe_path}"')
                            return True
                        else:
                            try:
                                winreg.DeleteValue(key, app_name)
                                return False
                            except WindowsError:
                                return False
                    finally:
                        key.Close()
            except Exception as e:
                self.logger.error(f"Failed to toggle startup: {str(e)}", exc_info=True)
                return False

        def is_startup_enabled(self) -> bool:
            """Check if the application is set to start with Windows."""
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "NetSpeedTray"
            startup_folder = os.path.join(os.getenv('APPDATA'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
            shortcut_path = os.path.join(startup_folder, f"{app_name}.lnk")
            try:
                if getattr(sys, 'frozen', False):
                    return os.path.exists(shortcut_path)
                else:
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
            except Exception:
                return False

        def setup_error_logging(self) -> logging.Logger:
            """Set up error logging."""
            try:
                handler = RotatingFileHandler(self.ERROR_LOG_FILE, maxBytes=self.MAX_LOG_SIZE, backupCount=self.MAX_LOG_FILES)
                formatter = logging.Formatter(self.LOG_FORMAT)
                handler.setFormatter(formatter)
                logger = logging.getLogger('NetSpeedTray')
                logger.setLevel(logging.DEBUG)
                handler.setLevel(logging.DEBUG)
                logger.addHandler(handler)
                return logger
            except Exception as e:
                print(f"Failed to setup logging: {str(e)}")
                return None

        APP_NAME = "NetSpeedTray"
        APP_VERSION = "1.0.4"
        DEFAULT_FONT = "Segoe UI Variable Small"
        DEFAULT_FONT_SIZE = 9
        DEFAULT_UPDATE_RATE = 0
        LOG_FORMAT = '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
        ERROR_LOG_FILE = os.path.join(os.getenv('APPDATA'), 'NetSpeedTray', 'netspeedtray.log')
        MAX_LOG_SIZE = 10 * 1024 * 1024
        MAX_LOG_FILES = 3
        HISTORY_MINUTES_RANGE = (5, 720)
        MAX_HISTORY_POINTS = 10000

        DEFAULT_CONFIG = {
            'color_coding': False, 'high_speed_threshold': 5.0, 'low_speed_threshold': 1.0,
            'high_speed_color': '#00FF00', 'low_speed_color': '#FFA500', 'default_color': '#FFFFFF',
            'graph_enabled': True,
            'history_minutes': 30, 'update_rate': DEFAULT_UPDATE_RATE,
            'position_x': None, 'position_y': None, 'start_with_windows': False,
            'interface_mode': 'all', 'selected_interfaces': [], 'graph_opacity': 30,
            'use_megabytes': False, 'keep_data': 31,
            'smart_threshold': 100000,
            'history_period': 'System Uptime',
            'dark_mode': False, 'legend_position': 'off',
            'font_size': DEFAULT_FONT_SIZE, 'font_weight': 4
        }

        def get_app_data_path(self) -> str:
            """Get or create the application data directory."""
            app_data = os.path.join(os.getenv('APPDATA'), 'NetSpeedTray')
            if not os.path.exists(app_data):
                os.makedirs(app_data)
            return app_data

    class SettingsDialog(QDialog):
        def __init__(self, parent: Optional['NetworkSpeedWidget'] = None, config: Optional[Dict] = None) -> None:
            super().__init__(parent)
            if not parent:
                raise ValueError("SettingsDialog requires a valid NetworkSpeedWidget parent")
            self.parent_widget = parent
            self.config = config or {}
            self.original_config = self.config.copy()  # Store original for revert
            self.logger = parent.logger if hasattr(parent, 'logger') else logging.getLogger('NetSpeedTray')
            self.setWindowTitle(f"{NetworkSpeedWidget.APP_NAME} Settings v{NetworkSpeedWidget.APP_VERSION}")
            if hasattr(parent, 'app_icon'):
                self.setWindowIcon(parent.app_icon)
            self.original_pos = None
            # Store the original bottom position for anchoring
            self.anchor_bottom_y = None  # Will be set in showEvent
            self.setupUI()

        def showEvent(self, event) -> None:
            """
            Compute the anchor position (bottom of dialog) relative to the top of the NetworkSpeedWidget.
            """
            super().showEvent(event)
            if self.anchor_bottom_y is None:
                # Get the position of the NetworkSpeedWidget (on the taskbar)
                widget_pos = self.parent_widget.mapToGlobal(QPoint(0, 0))
                widget_height = self.parent_widget.height()
                widget_top_y = widget_pos.y()
                
                # Get the initial position and size of the dialog
                dialog_pos = self.pos()
                dialog_height = self.height()
                dialog_bottom_y = dialog_pos.y() + dialog_height
                
                # Compute the offset from the widget's top to the dialog's bottom
                self.anchor_bottom_y = dialog_bottom_y - widget_top_y
                self.logger.debug(f"Anchor bottom set: dialog_bottom_y={dialog_bottom_y}, widget_top_y={widget_top_y}, anchor_bottom_y={self.anchor_bottom_y}")

        def setupUI(self) -> None:
            """
            Set up the UI components for the settings dialog.
            """
            slider_style = """
                QSlider::groove:horizontal {
                    height: 8px;
                    background: #505050;
                    border-radius: 4px;
                }
                QSlider::handle:horizontal {
                    background: #4CAF50;
                    width: 20px;
                    height: 20px;
                    border-radius: 10px;
                    margin: -6px 0;
                }
                QSlider::groove:horizontal:hover {
                    background: #404040;
                }
                QSlider::handle:horizontal:hover {
                    background: #45a049;
                }
            """

            layout = QVBoxLayout()

            # General Settings
            general_group = QGroupBox("General Settings")
            general_layout = QGridLayout()
            self.update_rate = QSlider(Qt.Orientation.Horizontal)
            self.update_rate.setRange(0, 20)
            self.update_rate.setValue(int(self.config.get('update_rate', NetworkSpeedWidget.DEFAULT_UPDATE_RATE) * 2) if self.config.get('update_rate', NetworkSpeedWidget.DEFAULT_UPDATE_RATE) > 0 else 0)
            self.update_rate.setTickPosition(QSlider.TickPosition.TicksBelow)
            self.update_rate.setTickInterval(1)
            self.update_rate.setStyleSheet(slider_style)
            self.update_rate_label = QLabel(self.rate_to_text(self.update_rate.value()))
            self.update_rate.valueChanged.connect(lambda v: self.update_rate_label.setText(self.rate_to_text(v)))
            self.update_rate.valueChanged.connect(self.apply_settings_live)
            general_layout.addWidget(QLabel("Update Rate:"), 0, 0)
            general_layout.addWidget(self.update_rate, 0, 1)
            general_layout.addWidget(self.update_rate_label, 0, 2)
            self.start_with_windows = ToggleSwitch(self)
            self.start_with_windows.setValue(1 if self.parent().is_startup_enabled() else 0)
            self.start_with_windows.valueChanged.connect(self.apply_settings_live)
            general_layout.addWidget(self.start_with_windows, 1, 0)
            general_layout.addWidget(QLabel("Start with Windows"), 1, 1)
            self.font_size = QSlider(Qt.Orientation.Horizontal)
            self.font_size.setRange(7, 11)
            self.font_size.setValue(self.config.get('font_size', NetworkSpeedWidget.DEFAULT_FONT_SIZE))
            self.font_size.setStyleSheet(slider_style)
            self.font_size_label = QLabel(str(self.font_size.value()))
            self.font_size.valueChanged.connect(lambda v: self.font_size_label.setText(str(v)))
            self.font_size.valueChanged.connect(self.apply_font_changes)
            general_layout.addWidget(QLabel("Font Size:"), 2, 0)
            general_layout.addWidget(self.font_size, 2, 1)
            general_layout.addWidget(self.font_size_label, 2, 2)
            self.font_weight = QSlider(Qt.Orientation.Horizontal)
            self.font_weight.setRange(0, 5)
            self.font_weight.setTickPosition(QSlider.TickPosition.TicksBelow)
            self.font_weight.setTickInterval(1)
            self.font_weight.setValue(self.config.get('font_weight', 4))
            self.font_weight.setStyleSheet(slider_style)
            self.font_weight_label = QLabel(self.weight_to_text(self.font_weight.value()))
            self.font_weight.valueChanged.connect(lambda v: self.font_weight_label.setText(self.weight_to_text(v)))
            self.font_weight.valueChanged.connect(self.apply_font_changes)
            general_layout.addWidget(QLabel("Font Weight:"), 3, 0)
            general_layout.addWidget(self.font_weight, 3, 1)
            general_layout.addWidget(self.font_weight_label, 3, 2)
            general_group.setLayout(general_layout)
            layout.addWidget(general_group)

            # Speed Color Coding
            self.color_group = QGroupBox("Speed Color Coding")
            self.grid_layout = QGridLayout()
            self.grid_layout.setContentsMargins(10, 10, 10, 10)
            self.grid_layout.setHorizontalSpacing(6)
            self.grid_layout.setVerticalSpacing(2)
            self.enable_colors = ToggleSwitch(self)
            self.enable_colors.setValue(1 if self.config.get('color_coding', False) else 0)
            self.grid_layout.addWidget(self.enable_colors, 0, 0)
            self.dummy_width_label = QLabel("")
            self.grid_layout.addWidget(self.dummy_width_label, 0, 1)
            self.default_color = QPushButton()
            self.default_color.setObjectName("default_color")
            self.default_color.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.config.get('default_color', '#FFFFFF')};
                    outline: none;
                    border: 1px solid #505050;
                }}
                QPushButton:focus {{
                    outline: none;
                    border: 1px solid #505050;
                }}
            """)
            self.default_color.clicked.connect(lambda: self.choose_color(self.default_color))
            self.grid_layout.addWidget(QLabel("Default Color:"), 1, 0)
            self.grid_layout.addWidget(self.default_color, 1, 1)
            self.grid_layout.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum), 1, 2)
            self.high_speed_threshold = QSlider(Qt.Orientation.Horizontal)
            self.high_speed_threshold.setRange(1, 10000)
            self.high_speed_threshold.setValue(int(self.config.get('high_speed_threshold', 5.0) * 10))
            self.high_speed_label = QLabel(f"{self.high_speed_threshold.value() / 10:.1f} Mbps")
            self.high_speed_threshold.valueChanged.connect(self.update_threshold_label)
            self.high_speed_threshold.valueChanged.connect(self.apply_settings_live)
            self.grid_layout.addWidget(QLabel("High Speed Threshold:"), 2, 0)
            self.grid_layout.addWidget(self.high_speed_threshold, 2, 1)
            self.grid_layout.addWidget(self.high_speed_label, 2, 2)
            self.low_speed_threshold = QSlider(Qt.Orientation.Horizontal)
            self.low_speed_threshold.setRange(0, 9999)
            self.low_speed_threshold.setValue(int(self.config.get('low_speed_threshold', 1.0) * 10))
            self.low_speed_label = QLabel(f"{self.low_speed_threshold.value() / 10:.1f} Mbps")
            self.low_speed_threshold.valueChanged.connect(self.update_threshold_label)
            self.low_speed_threshold.valueChanged.connect(self.apply_settings_live)
            self.grid_layout.addWidget(QLabel("Low Speed Threshold:"), 3, 0)
            self.grid_layout.addWidget(self.low_speed_threshold, 3, 1)
            self.grid_layout.addWidget(self.low_speed_label, 3, 2)
            self.high_speed_color = QPushButton()
            self.high_speed_color.setObjectName("high_speed_color")
            self.high_speed_color.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.config.get('high_speed_color', '#00FF00')};
                    outline: none;
                    border: 1px solid #505050;
                }}
                QPushButton:focus {{
                    outline: none;
                    border: 1px solid #505050;
                }}
            """)
            self.high_speed_color.clicked.connect(lambda: self.choose_color(self.high_speed_color))
            self.grid_layout.addWidget(QLabel("High Speed Color:"), 4, 0)
            self.grid_layout.addWidget(self.high_speed_color, 4, 1)
            self.grid_layout.addWidget(QLabel(""), 4, 2)
            self.low_speed_color = QPushButton()
            self.low_speed_color.setObjectName("low_speed_color")
            self.low_speed_color.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.config.get('low_speed_color', '#FFA500')};
                    outline: none;
                    border: 1px solid #505050;
                }}
                QPushButton:focus {{
                    outline: none;
                    border: 1px solid #505050;
                }}
            """)
            self.low_speed_color.clicked.connect(lambda: self.choose_color(self.low_speed_color))
            self.grid_layout.addWidget(QLabel("Low Speed Color:"), 5, 0)
            self.grid_layout.addWidget(self.low_speed_color, 5, 1)
            self.grid_layout.addWidget(QLabel(""), 5, 2)
            self.grid_layout.setColumnStretch(0, 0)
            self.grid_layout.setColumnStretch(1, 1)
            self.grid_layout.setColumnStretch(2, 0)
            controls = [self.default_color, self.high_speed_threshold, self.low_speed_threshold, 
                        self.high_speed_color, self.low_speed_color]
            max_width = max(c.sizeHint().width() for c in controls)
            for control in controls:
                control.setFixedWidth(max_width)
            self.dummy_width_label.setFixedWidth(max_width)
            self.color_settings_rows = [2, 3, 4, 5]
            self.enable_colors.valueChanged.connect(self.toggle_color_settings)
            self.color_group.setLayout(self.grid_layout)
            layout.addWidget(self.color_group)

            # Graph Settings
            graph_group = QGroupBox("Graph Settings")
            graph_layout = QGridLayout()
            self.enable_graph = ToggleSwitch(self)
            self.enable_graph.setValue(1 if self.config.get('graph_enabled', False) else 0)
            self.enable_graph.valueChanged.connect(self.apply_settings_live)
            graph_layout.addWidget(self.enable_graph, 0, 0)
            graph_layout.addWidget(QLabel("Enable Graph"), 0, 1)
            self.graph_note = QLabel("Double-click the tray widget to open this graph.")
            self.graph_note.setWordWrap(True)
            graph_layout.addWidget(self.graph_note, 1, 0, 1, 2)
            self.history_duration = QSlider(Qt.Orientation.Horizontal)
            self.history_duration.setRange(NetworkSpeedWidget.HISTORY_MINUTES_RANGE[0], NetworkSpeedWidget.HISTORY_MINUTES_RANGE[1])
            self.history_duration.setValue(self.config.get('history_minutes', 30))
            self.history_duration.setStyleSheet(slider_style)
            self.history_label = QLabel(f"{self.history_duration.value()} mins")
            self.history_duration.valueChanged.connect(lambda v: self.history_label.setText(f"{v} mins"))
            self.history_duration.valueChanged.connect(self.apply_settings_live)
            graph_layout.addWidget(QLabel("History Duration:"), 2, 0)
            graph_layout.addWidget(self.history_duration, 2, 1)
            graph_layout.addWidget(self.history_label, 2, 2)
            self.graph_opacity = QSlider(Qt.Orientation.Horizontal)
            self.graph_opacity.setRange(10, 100)
            self.graph_opacity.setValue(self.config.get('graph_opacity', 30))
            self.graph_opacity.setStyleSheet(slider_style)
            self.opacity_label = QLabel(f"{self.graph_opacity.value()}%")
            self.graph_opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
            self.graph_opacity.valueChanged.connect(self.apply_settings_live)
            graph_layout.addWidget(QLabel("Graph Line Opacity:"), 3, 0)
            graph_layout.addWidget(self.graph_opacity, 3, 1)
            graph_layout.addWidget(self.opacity_label, 3, 2)
            graph_group.setLayout(graph_layout)
            layout.addWidget(graph_group)

            # Units
            units_group = QGroupBox("Units")
            units_layout = QHBoxLayout()
            self.use_megabytes = ToggleSwitch(self)
            self.use_megabytes.setValue(1 if self.config.get('use_megabytes', False) else 0)
            self.use_megabytes.valueChanged.connect(self.apply_settings_live)
            self.use_megabytes.valueChanged.connect(self.update_threshold_label)
            self.use_megabytes_label = QLabel("MB/s" if self.use_megabytes.value() == 1 else "Mb/s")
            self.use_megabytes_label.setStyleSheet("color: white;")
            units_layout.addWidget(self.use_megabytes)
            units_layout.addWidget(self.use_megabytes_label)
            units_layout.addStretch()
            self.use_megabytes.valueChanged.connect(
                lambda v: self.use_megabytes_label.setText("MB/s" if v == 1 else "Mb/s")
            )
            units_info = QLabel("MB/s = Megabytes/sec (1 MB/s = 8 Mb/s)\nMb/s = Megabits/sec (network standard)")
            units_info.setWordWrap(True)
            units_layout2 = QVBoxLayout()
            units_layout2.addLayout(units_layout)
            units_layout2.addWidget(units_info)
            units_group.setLayout(units_layout2)
            layout.addWidget(units_group)

            # Network Interfaces
            interface_group = QGroupBox("Network Interfaces")
            interface_layout = QVBoxLayout()
            self.interface_group = interface_group  # Store reference for later use
            self.interfaces = self.parent_widget._cached_interfaces

            # Create the toggle switch for "All Interfaces"
            self.all_interfaces_layout = QHBoxLayout()
            self.all_interfaces = ToggleSwitch(self)
            self.all_interfaces.setValue(1 if self.config.get('interface_mode', 'all') == 'all' else 0)
            self.all_interfaces_label = QLabel("All Interfaces")
            self.all_interfaces_label.setStyleSheet("color: white;")
            self.all_interfaces_label.setVisible(self.all_interfaces.value() == 1)
            self.all_interfaces_layout.addWidget(self.all_interfaces)
            self.all_interfaces_layout.addWidget(self.all_interfaces_label)
            self.all_interfaces_layout.addStretch()
            self.all_interfaces.valueChanged.connect(self.toggle_all_interfaces)
            self.all_interfaces.valueChanged.connect(self.apply_settings_live)
            self.all_interfaces.valueChanged.connect(
                lambda v: self.all_interfaces_label.setVisible(v == 1)
            )
            interface_layout.addLayout(self.all_interfaces_layout)

            # Create the container for the checkboxes
            self.interface_list = QWidget()
            list_layout = QVBoxLayout()
            self.interface_checkboxes = {}
            selected_interfaces = self.config.get('selected_interfaces', [])
            for iface in self.interfaces:
                checkbox = QCheckBox(iface)
                checkbox.setChecked(iface in selected_interfaces or self.all_interfaces.value() == 1)
                checkbox.setEnabled(self.all_interfaces.value() == 0)
                checkbox.stateChanged.connect(self.apply_settings_live)
                self.interface_checkboxes[iface] = checkbox
                list_layout.addWidget(checkbox)
            self.interface_list.setLayout(list_layout)

            # Decide whether to wrap the interface list in a scroll area
            if len(self.interfaces) > 5:
                self.interface_scroll = QScrollArea()
                self.interface_scroll.setWidget(self.interface_list)
                self.interface_scroll.setWidgetResizable(True)
                self.interface_scroll.setMaximumHeight(150)
                interface_layout.addWidget(self.interface_scroll)
            else:
                interface_layout.addWidget(self.interface_list)

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

            if self.config.get('dark_mode', False):
                self.setStyleSheet("QDialog { background-color: #2b2b2b; color: white; }")

            # Initialize interface visibility based on the actual toggle value
            try:
                self.toggle_interface_settings(self.all_interfaces.value())
            except Exception as e:
                self._get_logger().error(f"Error initializing interface settings: {str(e)}")
                self.toggle_interface_settings(1)  # Default to hiding checkboxes if error occurs

            self.toggle_color_settings(self.enable_colors.value())

        def toggle_interface_settings(self, value: int) -> None:
            """
            Toggle visibility of interface checkboxes based on 'All Interfaces' setting.
            Hides checkboxes when 'All Interfaces' is on, shows them when off.
            """
            logger = self._get_logger()
            try:
                # Hide interface list when 'All Interfaces' is on (value == 1), show when off (value == 0)
                visible = value == 0
                self.interface_list.setVisible(visible)
                if hasattr(self, 'interface_scroll'):
                    self.interface_scroll.setVisible(visible)

                # Explicitly hide each checkbox to ensure they don't remain visible
                for checkbox in self.interface_checkboxes.values():
                    checkbox.setVisible(visible)
                    checkbox.setEnabled(visible)
                    checkbox.setChecked(not visible or self.all_interfaces.value() == 1)

                # Adjust layout and dialog size
                self.interface_group.updateGeometry()
                self.adjustSize()

                # Compute the adjusted height to match Qt's mintrack constraints
                requested_height = self.height()
                # Compute frame margins dynamically
                frame_geo = self.frameGeometry()
                geo = self.geometry()
                top_margin = frame_geo.top() - geo.top()
                bottom_margin = frame_geo.bottom() - geo.bottom()
                mintrack_height_with_frame = requested_height + top_margin + bottom_margin
                if requested_height < mintrack_height_with_frame - top_margin - bottom_margin:
                    requested_height = mintrack_height_with_frame - top_margin - bottom_margin
                    self.setMinimumHeight(requested_height)
                    self.resize(self.width(), requested_height)

                logger.debug(f"Computed size after adjustSize: {self.width()}x{self.height()}, margins: top={top_margin}, bottom={bottom_margin}")

                # Get screen and taskbar geometry
                screen = QApplication.primaryScreen().geometry()
                widget_pos = self.parent_widget.mapToGlobal(QPoint(0, 0))
                widget_top_y = widget_pos.y()

                # Anchor the bottom of the dialog to the widget's top
                new_bottom_y = widget_top_y + self.anchor_bottom_y
                new_y = new_bottom_y - requested_height  # Top position = bottom - height
                new_x = self.pos().x()

                # Ensure the dialog stays within screen bounds
                new_y = max(screen.top(), min(new_y, screen.height() - requested_height))
                new_x = max(screen.left(), min(new_x, screen.right() - self.width()))

                # Apply the geometry with the adjusted height
                self.setGeometry(new_x, new_y, self.width(), requested_height)
                logger.debug(f"Interface settings toggled to {visible}, Final geometry: {new_x}x{new_y}+{self.width()}+{self.height()}")
            except Exception as e:
                logger.error(f"Error toggling interface settings: {str(e)}")

        def toggle_color_settings(self, value: int) -> None:
            """
            Toggle visibility of color settings rows, adjust size, and reposition the dialog.
            """
            visible = value == 1
            try:
                # Toggle visibility of the color settings rows and adjust height constraints
                for row in self.color_settings_rows:
                    for col in range(self.grid_layout.columnCount()):
                        item = self.grid_layout.itemAtPosition(row, col)
                        if item and item.widget():
                            widget = item.widget()
                            widget.setVisible(visible)
                            # Reset height constraints to allow natural size when visible
                            if visible:
                                widget.setMinimumHeight(0)  # Reset to natural minimum
                                widget.setMaximumHeight(16777215)  # Reset to default maximum
                            else:
                                widget.setMinimumHeight(0)  # Collapse
                                widget.setMaximumHeight(0)  # Ensure no space

                # Adjust row heights in the grid layout
                for row in self.color_settings_rows:
                    if visible:
                        self.grid_layout.setRowMinimumHeight(row, 0)  # Let the row use its natural size
                        self.grid_layout.setRowStretch(row, 0)
                    else:
                        self.grid_layout.setRowMinimumHeight(row, 0)  # Collapse the row
                        self.grid_layout.setRowStretch(row, 0)

                # Force layout to update and propagate size changes
                self.grid_layout.invalidate()
                self.color_group.updateGeometry()
                self.color_group.adjustSize()
                self.adjustSize()

                # Compute the adjusted height to match Qt's mintrack constraints
                requested_height = self.height()
                # Compute frame margins dynamically
                frame_geo = self.frameGeometry()
                geo = self.geometry()
                top_margin = frame_geo.top() - geo.top()
                bottom_margin = frame_geo.bottom() - geo.bottom()
                mintrack_height_with_frame = requested_height + top_margin + bottom_margin
                if requested_height < mintrack_height_with_frame - top_margin - bottom_margin:
                    requested_height = mintrack_height_with_frame - top_margin - bottom_margin
                    self.setMinimumHeight(requested_height)
                    self.resize(self.width(), requested_height)

                self.logger.debug(f"Computed size after adjustSize: {self.width()}x{self.height()}, margins: top={top_margin}, bottom={bottom_margin}")

                # Get screen and widget geometry
                screen = QApplication.primaryScreen().geometry()
                widget_pos = self.parent_widget.mapToGlobal(QPoint(0, 0))
                widget_top_y = widget_pos.y()

                # Anchor the bottom of the dialog to the widget's top
                new_bottom_y = widget_top_y + self.anchor_bottom_y
                new_y = new_bottom_y - requested_height  # Top position = bottom - height
                new_x = self.pos().x() if self.original_pos is None else self.original_pos.x()

                # Ensure the dialog stays within screen bounds
                new_y = max(screen.top(), min(new_y, screen.height() - requested_height))
                new_x = max(screen.left(), min(new_x, screen.right() - self.width()))

                # Apply the geometry with the adjusted height
                self.setGeometry(new_x, new_y, self.width(), requested_height)
                self.logger.debug(f"Toggle {visible}, New height: {requested_height}, Final geometry: {new_x}s{new_y}+{self.width()}+{self.height()}")
            except Exception as e:
                self.logger.error(f"Error toggling color settings: {str(e)}")

        def toggle_all_interfaces(self, value: int) -> None:
            """
            Handle the 'All Interfaces' toggle and delegate visibility to toggle_interface_settings.
            """
            logger = self._get_logger()
            try:
                checked = value == 1
                for checkbox in self.interface_checkboxes.values():
                    checkbox.setChecked(checked)
                    checkbox.setEnabled(not checked)

                # Delegate visibility and repositioning to toggle_interface_settings
                self.toggle_interface_settings(value)

                # Log the toggle action
                visible = not checked
                logger.debug(f"Interface toggle to {visible}, New height: {self.height()}, New pos: ({self.x()}, {self.y()})")
            except Exception as e:
                logger.error(f"Error toggling all interfaces: {str(e)}")

        def _get_logger(self) -> logging.Logger:
            """
            Get or create a logger instance to ensure logging is available.
            """
            if hasattr(self, 'logger') and self.logger:
                return self.logger
            # Fallback logger if parent logger is not available
            logger = logging.getLogger('NetSpeedTray.SettingsDialog')
            logger.setLevel(logging.DEBUG)
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            logger.addHandler(handler)
            return logger

        def toggle_interface_settings(self, value: int) -> None:
            """
            Toggle visibility of interface checkboxes based on 'All Interfaces' setting.
            Hides checkboxes when 'All Interfaces' is on, shows them when off.
            """
            logger = self._get_logger()
            try:
                # Hide interface list when 'All Interfaces' is on (value == 1), show when off (value == 0)
                visible = value == 0
                self.interface_list.setVisible(visible)
                if hasattr(self, 'interface_scroll'):
                    self.interface_scroll.setVisible(visible)

                # Explicitly hide each checkbox to ensure they don't remain visible
                for checkbox in self.interface_checkboxes.values():
                    checkbox.setVisible(visible)
                    checkbox.setEnabled(visible)
                    checkbox.setChecked(not visible or self.all_interfaces.value() == 1)

                # Adjust layout and dialog size
                self.interface_group.updateGeometry()
                self.adjustSize()

                # Compute the adjusted height to match Qt's mintrack constraints
                requested_height = self.height()
                # Compute frame margins dynamically
                frame_geo = self.frameGeometry()
                geo = self.geometry()
                top_margin = frame_geo.top() - geo.top()
                bottom_margin = frame_geo.bottom() - geo.bottom()
                mintrack_height_with_frame = requested_height + top_margin + bottom_margin
                if requested_height < mintrack_height_with_frame - top_margin - bottom_margin:
                    requested_height = mintrack_height_with_frame - top_margin - bottom_margin
                    self.setMinimumHeight(requested_height)
                    self.resize(self.width(), requested_height)

                logger.debug(f"Computed size after adjustSize: {self.width()}x{self.height()}, margins: top={top_margin}, bottom={bottom_margin}")

                # Get screen and taskbar geometry
                screen = QApplication.primaryScreen().geometry()
                widget_pos = self.parent_widget.mapToGlobal(QPoint(0, 0))
                widget_top_y = widget_pos.y()

                # Anchor the bottom of the dialog to the widget's top
                new_bottom_y = widget_top_y + self.anchor_bottom_y
                new_y = new_bottom_y - requested_height  # Top position = bottom - height
                new_x = self.pos().x()

                # Ensure the dialog stays within screen bounds
                new_y = max(screen.top(), min(new_y, screen.height() - requested_height))
                new_x = max(screen.left(), min(new_x, screen.right() - self.width()))

                # Apply the geometry with the adjusted height
                self.setGeometry(new_x, new_y, self.width(), requested_height)
                logger.debug(f"Interface settings toggled to {visible}, Final geometry: {new_x}x{new_y}+{self.width()}+{self.height()}")
            except Exception as e:
                logger.error(f"Error toggling interface settings: {str(e)}")

        def apply_font_changes(self) -> None:
            """Apply font size and weight changes live."""
            try:
                self.config['font_size'] = self.font_size.value()
                self.config['font_weight'] = self.font_weight.value()
                self.parent_widget.update_font()
            except Exception as e:
                self.logger.error(f"Error applying font changes: {str(e)}")

        def rate_to_text(self, value: int) -> str:
            if value == 0:
                return "Smart"
            return f"{(value - 1) / 2 + 1:.1f} s"

        def weight_to_text(self, value: int) -> str:
            weights = {0: "Thin", 1: "Light", 2: "Normal", 3: "Medium", 4: "DemiBold", 5: "Bold"}
            return weights.get(value, "DemiBold")

        def choose_color(self, button):
            try:
                print(f"Opening color dialog for button: {button.objectName()}")
                color = QColorDialog.getColor(Qt.GlobalColor.white if button == self.default_color else Qt.GlobalColor.green,
                                            self,
                                            "Select Color")
                if color.isValid():
                    print(f"Selected color: {color.name()}")
                    button.setStyleSheet(f"QPushButton {{ background-color: {color.name()}; border: 1px solid #505050; }}")
                    self.apply_settings_live()  # Update config with new color
                else:
                    print("Color selection canceled")
            except Exception as e:
                print(f"Error in choose_color: {e}")
                raise

        def update_threshold_label(self) -> None:
            unit = "MB/s" if self.use_megabytes.value() == 1 else "Mbps"
            self.high_speed_label.setText(f"{self.high_speed_threshold.value() / 10:.1f} {unit}")
            self.low_speed_label.setText(f"{self.low_speed_threshold.value() / 10:.1f} {unit}")

        def apply_settings_live(self) -> None:
            """Apply settings live as they change."""
            try:
                settings = self.get_settings()
                self.parent_widget.config.update(settings)
                self.parent_widget.speed_timer.setInterval(self.parent_widget.get_timer_interval(settings['update_rate']))
                self.parent_widget.update_colors()
                self.parent_widget.update()  # Force widget update
            except Exception as e:
                self.logger.error(f"Error applying live settings: {str(e)}")

        def get_settings(self) -> Dict:
            try:
                interface_mode = 'all' if self.all_interfaces.value() == 1 else 'include'
                selected_interfaces = ([iface for iface, cb in self.interface_checkboxes.items() if cb.isChecked()]
                                    if interface_mode != 'all' else [])
                update_rate = 0 if self.update_rate.value() == 0 else (self.update_rate.value() - 1) / 2 + 1
                high = self.high_speed_threshold.value() / 10
                low = self.low_speed_threshold.value() / 10
                if low >= high:  # Ensure low < high
                    low = max(0, high - 0.1)
                    self.low_speed_threshold.setValue(int(low * 10))
                    self.update_threshold_label()
                settings = {
                    'update_rate': update_rate,
                    'color_coding': self.enable_colors.value() == 1,
                    'high_speed_threshold': high,
                    'low_speed_threshold': low,
                    'high_speed_color': self.high_speed_color.palette().button().color().name(),
                    'low_speed_color': self.low_speed_color.palette().button().color().name(),
                    'default_color': self.default_color.palette().button().color().name(),
                    'graph_enabled': self.enable_graph.value() == 1,
                    'history_minutes': self.history_duration.value(),
                    'graph_opacity': self.graph_opacity.value(),
                    'use_megabytes': self.use_megabytes.value() == 1,
                    'start_with_windows': self.start_with_windows.value() == 1,
                    'interface_mode': interface_mode,
                    'selected_interfaces': selected_interfaces,
                    'keep_data': self.parent_widget.config.get('keep_data', 31),
                    'smart_threshold': self.parent_widget.config.get('smart_threshold', 100000),
                    'history_period': self.parent_widget.config.get('history_period', 'System Uptime'),
                    'dark_mode': self.parent_widget.config.get('dark_mode', False),
                    'font_size': self.font_size.value(),
                    'font_weight': self.font_weight.value()
                }
                if settings['start_with_windows'] != self.parent_widget.is_startup_enabled():
                    self.parent_widget.toggle_startup(settings['start_with_windows'])
                return settings
            except Exception as e:
                self.logger.error(f"Error getting settings: {str(e)}")
                return self.config

        def reject(self) -> None:
            """Revert to original config on cancel."""
            self.parent_widget.config = self.original_config.copy()
            self.parent_widget.save_config()
            self.parent_widget.update_font()
            self.parent_widget.speed_timer.setInterval(self.parent_widget.get_timer_interval(self.original_config['update_rate']))
            super().reject()

        def export_error_log(self) -> None:
            try:
                if not os.path.exists(NetworkSpeedWidget.ERROR_LOG_FILE):
                    QMessageBox.information(self, "Error Log", "No errors have been logged.")
                    return
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                suggested_name = f"NetSpeedTray_ErrorLog_{timestamp}.log"
                file_path, _ = QFileDialog.getSaveFileName(self, "Export Error Log", suggested_name, "Log Files (*.log);;All Files (*.*)")
                if file_path:
                    shutil.copy2(NetworkSpeedWidget.ERROR_LOG_FILE, file_path)
                    QMessageBox.information(self, "Success", f"Error log exported to:\n{file_path}")
            except Exception as e:
                self.logger.error(f"Failed to export error log: {str(e)}")
                QMessageBox.warning(self, "Error", f"Failed to export error log: {str(e)}")

    class ToggleSwitch(QSlider):
        def __init__(self, parent=None):
            super().__init__(Qt.Orientation.Horizontal, parent)
            self.setRange(0, 1)
            self.setTickPosition(QSlider.TickPosition.NoTicks)
            self.setFixedSize(80, 30)
            # Define base_style with doubled curly braces for CSS, single for placeholders
            self.base_style = (
                r"QSlider::groove:horizontal {{height: 20px; background: #808080; border-radius: 10px; margin: 0 5px;}}"
                r"QSlider::handle:horizontal {{background: {handle_color}; width: 30px; height: 30px; border-radius: 15px; margin: -5px 0;}}"
                r"QSlider::groove:horizontal:hover {{background: #666666;}}"
                r"QSlider::handle:horizontal:hover {{background: {hover_color};}}"
            )
            self.update_style()  # Set initial style
            self.valueChanged.connect(self.update_style)

        def update_style(self):
            """Update the handle color based on the toggle state."""
            handle_color = "#4CAF50" if self.value() == 1 else "#B0B0B0"  # Green for on, gray for off
            hover_color = "#45a049" if self.value() == 1 else "#A0A0A0"  # Darker green or lighter gray for hover
            try:
                formatted_style = self.base_style.format(handle_color=handle_color, hover_color=hover_color)
                self.setStyleSheet(formatted_style)
            except KeyError as e:
                print(f"Format error: KeyError with key {e}")
                raise

        def mousePressEvent(self, event):
            groove_rect = self.rect().adjusted(5, 5, -5, -5)
            if groove_rect.contains(event.pos()):
                self.setValue(1 - self.value())
                event.accept()
            else:
                super().mousePressEvent(event)

    class GraphWindow(QWidget):
        """A window displaying network speed history."""
        def __init__(self, parent: Optional['NetworkSpeedWidget'] = None) -> None:
            super().__init__(parent)
            self._parent = parent
            self.logger = parent.logger
            self.logger.debug("Initializing GraphWindow")
            
            self._graph_update_pending = False
            self._last_history = None
            self._update_timer = QTimer(self)
            self._update_timer.setSingleShot(True)
            self._update_timer.timeout.connect(self._perform_graph_update)

            # Live Update timer
            self._realtime_timer = QTimer(self)
            self._realtime_timer.timeout.connect(self._update_realtime)
            
            self.settings_open = False
            self.app_start_time = datetime.now()
            self.setWindowTitle("Network Speed History")
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
            
            self.main_layout = QHBoxLayout()
            self.main_layout.setSpacing(0)
            self.main_layout.setContentsMargins(0, 0, 0, 0)
            
            self.graph_widget = QWidget(self)
            self.graph_layout = QVBoxLayout(self.graph_widget)
            self.graph_layout.setContentsMargins(0, 0, 0, 0)
            self.graph_layout.setSpacing(0)
            
            self.figure = Figure(figsize=(8, 6), facecolor='none')
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.graph_layout.addWidget(self.canvas)
            self.graph_widget.setFixedSize(802, 602)
            
            self.hamburger_icon = QPushButton("☰", self.graph_widget)
            self.hamburger_icon.setFixedSize(30, 30)
            self.hamburger_icon.setToolTip("Show graph settings")
            self.hamburger_icon.clicked.connect(self.toggle_settings)
            self.hamburger_icon.setStyleSheet("QPushButton { border: none; font-size: 16px; color: white; background: #2b2b2b; }")
            self.hamburger_icon.move(self.canvas.width() - self.hamburger_icon.width() - 2, 2)
            self.hamburger_icon.raise_()
            
            self.stats_bar = QLabel("Max: Up 0.00 Mbps, Down 0.00 Mbps | Bandwidth: Up 0.00 MB, Down 0.00 MB", self.graph_widget)
            self.stats_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.stats_bar.setStyleSheet("color: white; background-color: #2b2b2b; padding: 2px; font-size: 14px;")
            self.stats_bar.adjustSize()
            self.stats_bar.move((self.canvas.width() - self.stats_bar.width() - self.hamburger_icon.width() - 6) // 2, 15)
            self.stats_bar.raise_()
            
            self.main_layout.addWidget(self.graph_widget)
            
            self.settings_widget = QWidget(self)
            self.settings_widget.setStyleSheet("background-color: #2b2b2b;")
            self.settings_layout = QVBoxLayout(self.settings_widget)
            self.settings_layout.setSpacing(10)
            self.settings_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            # Toggle layout for Dark Mode and Live Update
            toggle_layout = QHBoxLayout()
            toggle_layout.setSpacing(20)
            
            # Dark Mode toggle
            self.dark_mode_layout = QVBoxLayout()
            self.dark_mode_label = QLabel("Dark Mode")
            self.dark_mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.dark_mode_label.setStyleSheet("color: white;")
            self.dark_mode_layout.addWidget(self.dark_mode_label)
            self.dark_mode = ToggleSwitch(self)
            self.dark_mode.setValue(1 if self._parent.config.get('dark_mode', False) else 0)
            self.dark_mode.valueChanged.connect(self.toggle_dark_mode)
            self.dark_mode_layout.addWidget(self.dark_mode)
            toggle_layout.addLayout(self.dark_mode_layout)
            
            # Live Update toggle
            self.realtime_layout = QVBoxLayout()
            self.realtime_label = QLabel("Live Update")
            self.realtime_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.realtime_label.setStyleSheet("color: white;")
            self.realtime_layout.addWidget(self.realtime_label)
            self.realtime = ToggleSwitch(self)
            self.realtime.setValue(1)  # Default on
            self.realtime.valueChanged.connect(self.toggle_live_update)
            self.realtime_layout.addWidget(self.realtime)
            toggle_layout.addLayout(self.realtime_layout)
            
            self.settings_layout.addLayout(toggle_layout)

            self.history_period_label = QLabel("Timeline: System Uptime")
            self.history_period_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_period_label.setStyleSheet("color: white;")
            self.settings_layout.addWidget(self.history_period_label)
            self.history_period = QSlider(Qt.Orientation.Horizontal)
            self.history_period.setRange(0, 5)
            self.history_period.setTickPosition(QSlider.TickPosition.TicksBelow)
            self.history_period.setTickInterval(1)
            self.history_period.setValue(0)
            self._parent.config['history_period'] = 'System Uptime'
            self.history_period.valueChanged.connect(self.update_history_period)
            self.history_period.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 8px;
                    background: #505050;
                    border-radius: 4px;
                }
                QSlider::handle:horizontal {
                    background: #4CAF50;
                    width: 20px;
                    height: 20px;
                    border-radius: 10px;
                    margin: -6px 0;
                }
            """)
            self.settings_layout.addWidget(self.history_period)

            self.keep_data_label = QLabel()
            keep_days = self._parent.config.get('keep_data', 31)
            self.update_keep_data_label(keep_days)
            self.keep_data_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.keep_data_label.setStyleSheet("color: white;")
            self.settings_layout.addWidget(self.keep_data_label)
            self.keep_data = QSlider(Qt.Orientation.Horizontal)
            self.keep_data.setRange(0, 5)
            self.keep_data.setTickPosition(QSlider.TickPosition.TicksBelow)
            self.keep_data.setTickInterval(1)
            self.keep_data.setValue({1: 0, 3: 1, 7: 2, 31: 3, 180: 4, 365: 5}.get(keep_days, 3))
            self.keep_data.valueChanged.connect(self.update_keep_data)
            self.keep_data.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 8px;
                    background: #505050;
                    border-radius: 4px;
                }
                QSlider::handle:horizontal {
                    background: #4CAF50;
                    width: 20px;
                    height: 20px;
                    border-radius: 10px;
                    margin: -6px 0;
                }
            """)
            self.settings_layout.addWidget(self.keep_data)

            self.legend_label = QLabel("Legend: Off")
            self.legend_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.legend_label.setStyleSheet("color: white;")
            self.settings_layout.addWidget(self.legend_label)
            self.legend_position = QSlider(Qt.Orientation.Horizontal)
            self.legend_position.setRange(0, 3)
            self.legend_position.setTickPosition(QSlider.TickPosition.TicksBelow)
            self.legend_position.setTickInterval(1)
            default_pos = self._parent.config.get('legend_position', 'off').lower()
            initial_value = {'off': 0, 'left': 1, 'middle': 2, 'right': 3}.get(default_pos, 0)
            self.legend_position.setValue(initial_value)
            self.legend_position.valueChanged.connect(self.update_legend_position)
            self.legend_position.setStyleSheet("""
                QSlider::groove:horizontal {
                    height: 8px;
                    background: #505050;
                    border-radius: 4px;
                }
                QSlider::handle:horizontal {
                    background: #4CAF50;
                    width: 20px;
                    height: 20px;
                    border-radius: 10px;
                    margin: -6px 0;
                }
            """)
            self.settings_layout.addWidget(self.legend_position)

            self.export_csv = QPushButton("Export to CSV")
            self.export_csv.clicked.connect(self.export_history)
            self.export_csv.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border-radius: 12px;
                    padding: 5px 10px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
            """)
            self.settings_layout.addWidget(self.export_csv)

            self.save_fig = QPushButton("Export Graph Image")
            self.save_fig.clicked.connect(self.save_figure)
            self.save_fig.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border-radius: 12px;
                    padding: 5px 10px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
            """)
            self.settings_layout.addWidget(self.save_fig)
            
            self.settings_widget.setFixedWidth(300)
            self.settings_widget.setVisible(False)
            self.main_layout.addWidget(self.settings_widget)
            
            self.setLayout(self.main_layout)
            self.adjustSize()
            screen = QApplication.primaryScreen().geometry()
            self.move(int((screen.width() - self.width()) / 2), int((screen.height() - self.height()) / 2))
            
            self.ax = self.figure.add_subplot(111)
            self.upload_line, = self.ax.plot([], [], label='Upload', color='green', linewidth=2)
            self.download_line, = self.ax.plot([], [], label='Download', color='blue', linewidth=2)
            self.ax.set_xlabel('Time')
            self.ax.set_ylabel('Speed (Mbps)')
            self.ax.grid(True, linestyle='--', alpha=0.7)
            self.toggle_dark_mode()
            self.canvas.draw()
            self.update_graph(self._parent.speed_history)
            self._realtime_timer.start(2000)  # Start live updates by default
            self.logger.debug("GraphWindow initialized")

        def toggle_settings(self) -> None:
            """Toggle the visibility of the settings panel."""
            try:
                self.logger.debug("Toggling settings panel")
                if self.settings_open:
                    self.settings_widget.hide()
                    self.adjustSize()
                    self.hamburger_icon.setToolTip("Show graph settings")
                    self.settings_open = False
                else:
                    self.settings_widget.show()
                    self.adjustSize()
                    self.hamburger_icon.setToolTip("Hide graph settings")
                    self.settings_open = True
                self.canvas.draw()
                self.update()
                self.logger.debug(f"Settings toggled. Window size: {self.size()}")
            except Exception as e:
                self.logger.error(f"Error toggling settings: {str(e)}", exc_info=True)

        def toggle_dark_mode(self, initial: bool = False) -> None:
            """Toggle between dark and light modes."""
            try:
                checked = self.dark_mode.value() == 1 if not initial else initial
                self.logger.debug(f"Toggling dark mode: {checked}")
                self._parent.config['dark_mode'] = checked
                self._parent.save_config()
                if checked:
                    self.figure.patch.set_facecolor('#2b2b2b')
                    self.ax.set_facecolor('#2b2b2b')
                    self.ax.spines['bottom'].set_color('white')
                    self.ax.spines['top'].set_color('white')
                    self.ax.spines['left'].set_color('white')
                    self.ax.spines['right'].set_color('white')
                    self.ax.tick_params(axis='both', colors='white', labelsize=10)
                    self.ax.xaxis.label.set_color('white')
                    self.ax.yaxis.label.set_color('white')
                    if self.ax.get_legend():
                        self.ax.legend().get_frame().set_facecolor('#404040')
                        self.ax.legend().get_frame().set_edgecolor('white')
                        for text in self.ax.legend().get_texts():
                            text.set_color('white')
                    self.stats_bar.setStyleSheet("color: white; background-color: #2b2b2b; padding: 2px; font-size: 14px;")
                else:
                    self.figure.patch.set_facecolor('white')
                    self.ax.set_facecolor('white')
                    self.ax.spines['bottom'].set_color('black')
                    self.ax.spines['top'].set_color('black')
                    self.ax.spines['left'].set_color('black')
                    self.ax.spines['right'].set_color('black')
                    self.ax.tick_params(axis='both', colors='black', labelsize=10)
                    self.ax.xaxis.label.set_color('black')
                    self.ax.yaxis.label.set_color('black')
                    if self.ax.get_legend():
                        self.ax.legend().get_frame().set_facecolor('white')
                        self.ax.legend().get_frame().set_edgecolor('black')
                        for text in self.ax.legend().get_texts():
                            text.set_color('black')
                    self.stats_bar.setStyleSheet("color: black; background-color: #f0f0f0; padding: 2px; font-size: 14px;")
                self.update_graph(self._parent.speed_history)
                self.canvas.draw()
                self.canvas.flush_events()
                self.logger.debug(f"Dark mode toggled to {checked}")
            except Exception as e:
                self.logger.error(f"Error in toggle_dark_mode: {str(e)}", exc_info=True)

        def resizeEvent(self, event) -> None:
            """Handle window resizing."""
            super().resizeEvent(event)
            self.hamburger_icon.move(self.canvas.width() - self.hamburger_icon.width() - 2, 2)
            self.stats_bar.move((self.canvas.width() - self.stats_bar.width() - self.hamburger_icon.width() - 6) // 2, 15)
            self.hamburger_icon.raise_()
            self.stats_bar.raise_()

        def toggle_live_update(self, value: int) -> None:
            """Toggle live updates for the graph."""
            try:
                enabled = value == 1
                self.logger.debug(f"Toggling live update: {enabled}")
                if enabled:
                    self._realtime_timer.start(2000)  # 2-second interval
                else:
                    self._realtime_timer.stop()
            except Exception as e:
                self.logger.error(f"Error in toggle_live_update: {str(e)}", exc_info=True)

        def _update_realtime(self) -> None:
            """Update the graph in real-time."""
            try:
                self.update_graph(self._parent.speed_history)
            except Exception as e:
                self.logger.error(f"Error in live update: {str(e)}", exc_info=True)

        def update_history_period(self, value: int) -> None:
            """Update the history period."""
            try:
                period_map = {0: 'System Uptime', 1: 'Session', 2: '24h', 3: '1 Week', 4: '1 Month', 5: 'All'}
                period = period_map[value]
                self.history_period_label.setText(f"Timeline: {period}")
                self._parent.config['history_period'] = period
                self.update_graph(self._parent.speed_history)
            except Exception as e:
                self.logger.error(f"Error in update_history_period: {str(e)}", exc_info=True)

        def update_keep_data_label(self, days: int) -> None:
            """Update the keep_data_label with days and file size if 1 year."""
            if days == 365:
                app_data = self._parent.get_app_data_path()
                csv_path = os.path.join(app_data, 'nst_history.csv')
                try:
                    size_mb = os.path.getsize(csv_path) / (1024 * 1024) if os.path.exists(csv_path) else 0
                    label_text = f"Data Retention: 1 Year (cached {size_mb:.1f} MB)"
                except Exception as e:
                    self.logger.error(f"Error getting file size: {str(e)}")
                    label_text = "Data Retention: 1 Year (cached ? MB)"
            else:
                label_text = f"Data Retention: {days} {'day' if days == 1 else 'days'}"
            self.keep_data_label.setText(label_text)

        def update_keep_data(self, value: int) -> None:
            """Update the data retention period."""
            try:
                days_map = {0: 1, 1: 3, 2: 7, 3: 31, 4: 180, 5: 365}
                days = days_map[value]
                self.update_keep_data_label(days)
                self._parent.config['keep_data'] = days
                self.update_graph(self._parent.speed_history)
            except Exception as e:
                self.logger.error(f"Error in update_keep_data: {str(e)}", exc_info=True)

        def update_legend_position(self, value: int) -> None:
            """Update the legend position."""
            try:
                position_map = {0: 'Off', 1: 'Left', 2: 'Middle', 3: 'Right'}
                position = position_map[value]
                self.legend_label.setText(f"Legend: {position}")
                self._parent.config['legend_position'] = position.lower()
                self.update_graph(self._parent.speed_history)
            except Exception as e:
                self.logger.error(f"Error in update_legend_position: {str(e)}", exc_info=True)

        def update_graph(self, history: List[tuple[datetime, float, float]]) -> None:
            """Queue graph updates to prevent UI lag."""
            self._last_history = history
            if not self._graph_update_pending:
                self._graph_update_pending = True
                self._update_timer.start(500)

        def format_usage(self, megabytes: float) -> tuple[str, str]:
            """Format bandwidth in MB, GB, TB, or PB based on magnitude."""
            if megabytes < 1000:
                return f"{megabytes:.2f}", "MB"
            elif megabytes < 1000000:
                return f"{megabytes / 1000:.2f}", "GB"
            elif megabytes < 1000000000:
                return f"{megabytes / 1000000:.2f}", "TB"
            else:
                return f"{megabytes / 1000000000:.2f}", "PB"

        def _perform_graph_update(self) -> None:
            """Execute the actual graph update."""
            try:
                self.logger.debug("Performing throttled graph update")
                history = self._last_history
                if not history:
                    self.ax.clear()
                    self.ax.text(0.5, 0.5, "No data available", ha='center', va='center', color='black', fontsize=12)
                    try:
                        self.ax.legend().remove()
                    except AttributeError:
                        pass
                    self.canvas.draw()
                    self.stats_bar.setText("Max: Up 0.00 Mbps, Down 0.00 Mbps | Bandwidth: Up 0.00 MB, Down 0.00 MB")
                    self.stats_bar.adjustSize()
                    self.stats_bar.move((self.canvas.width() - self.stats_bar.width() - self.hamburger_icon.width() - 6) // 2, 15)
                    self._graph_update_pending = False
                    return
                
                period_map = {0: 'System Uptime', 1: 'Session', 2: '24h', 3: '1 Week', 4: '1 Month', 5: 'All'}
                period_value = self.history_period.value()
                period = period_map[period_value]
                self.logger.debug(f"Period: {period}")
                
                position_map = {0: 'off', 1: 'left', 2: 'middle', 3: 'right'}
                slider_value = self.legend_position.value()
                legend_pos = position_map.get(slider_value, 'off')
                self._parent.config['legend_position'] = legend_pos
                legend_loc = {'off': None, 'left': 'upper left', 'middle': 'upper center', 'right': 'upper right'}.get(legend_pos, None)

                if legend_pos == 'off' or not legend_loc:
                    try:
                        self.ax.legend().remove()
                    except AttributeError:
                        pass
                else:
                    self.ax.legend(loc=legend_loc)
                
                app_data = self._parent.get_app_data_path()
                csv_path = os.path.join(app_data, 'nst_history.csv')
                
                if period == 'Session':
                    data = [h for h in history if h[0] >= self.app_start_time]
                    self.logger.debug(f"Session data points: {len(data)}")
                elif period == 'System Uptime':
                    try:
                        boot_time = datetime.fromtimestamp(psutil.boot_time())
                        data = list(history)
                        if os.path.exists(csv_path):
                            with open(csv_path, 'r', newline='') as f:
                                reader = csv.reader(f)
                                next(reader)
                                for row in reader:
                                    ts = datetime.fromisoformat(row[0])
                                    if ts >= boot_time:
                                        data.append((ts, float(row[1]), float(row[2])))
                            data.sort(key=lambda x: x[0])
                        self.logger.debug(f"System Uptime data points: {len(data)}")
                    except Exception as e:
                        self.logger.error(f"Error loading system uptime data: {str(e)}")
                        data = list(history)
                else:
                    cutoff = {
                        '24h': datetime.now() - timedelta(hours=24),
                        '1 Week': datetime.now() - timedelta(days=7),
                        '1 Month': datetime.now() - timedelta(days=30),
                        'All': datetime.now() - timedelta(days=365)
                    }[period]
                    data = list(history)
                    if os.path.exists(csv_path):
                        with open(csv_path, 'r', newline='') as f:
                            reader = csv.reader(f)
                            next(reader)
                            for row in reader:
                                ts = datetime.fromisoformat(row[0])
                                if ts >= cutoff:
                                    data.append((ts, float(row[1]), float(row[2])))
                        data.sort(key=lambda x: x[0])
                    self.logger.debug(f"{period} data points: {len(data)}")

                if data:
                    self.logger.debug(f"Processing {len(data)} data points")
                    max_points = 500
                    if len(data) > max_points:
                        step = max(1, len(data) // max_points)
                        data = data[::step][:max_points]
                        self.logger.debug(f"Reduced to {len(data)} points")

                    times, uploads, downloads = zip(*data)
                    factor = 1 / 1000000 if self._parent.config.get('use_megabytes', False) else 8 / 1000000
                    ylabel = 'MB/s' if self._parent.config.get('use_megabytes', False) else 'Mbps'
                    upload = [x * factor for x in uploads]
                    download = [x * factor for x in downloads]
                    max_up = max(upload) if upload else 0
                    max_down = max(download) if download else 0
                    total_up = total_down = 0
                    for i in range(1, len(times)):
                        dt = (times[i] - times[i-1]).total_seconds()
                        total_up += (uploads[i-1] + uploads[i]) / 2 * dt / 1000000
                        total_down += (downloads[i-1] + downloads[i]) / 2 * dt / 1000000
                
                    self.ax.clear()
                    self.upload_line, = self.ax.plot(times, upload, label='Upload', color='green', linewidth=2)
                    self.download_line, = self.ax.plot(times, download, label='Download', color='blue', linewidth=2)
                    self.ax.set_xlabel('Time')
                    self.ax.set_ylabel(f'Speed ({ylabel})')
                    self.ax.grid(True, linestyle='--', alpha=0.7)
                    if legend_loc:
                        self.ax.legend(loc=legend_loc)
                    else:
                        try:
                            self.ax.legend().remove()
                        except AttributeError:
                            pass
                
                    dark_mode = self._parent.config.get('dark_mode', False)
                    self.logger.debug(f"Applying label colors for dark_mode={dark_mode}")
                    if dark_mode:
                        self.ax.xaxis.label.set_color('white')
                        self.ax.yaxis.label.set_color('white')
                    else:
                        self.ax.xaxis.label.set_color('black')
                        self.ax.yaxis.label.set_color('black')
                
                    up_value, up_unit = self.format_usage(total_up)
                    down_value, down_unit = self.format_usage(total_down)
                    self.stats_bar.setText(f"Max: Up {max_up:.2f} {ylabel}, Down {max_down:.2f} {ylabel} | Bandwidth: Up {up_value} {up_unit}, Down {down_value} {down_unit}")
                    self.stats_bar.adjustSize()
                    self.stats_bar.move((self.canvas.width() - self.stats_bar.width() - self.hamburger_icon.width() - 6) // 2, 15)
                
                    time_span = (max(times) - min(times)).total_seconds() if len(times) > 1 else 1
                    if time_span < 3600:
                        self.ax.xaxis.set_major_formatter(DateFormatter('%H:%M'))
                        self.ax.xaxis.set_major_locator(MinuteLocator(interval=5))
                    elif time_span <= 24 * 3600:
                        self.ax.xaxis.set_major_formatter(DateFormatter('%H:%M'))
                        self.ax.xaxis.set_major_locator(HourLocator(interval=1))
                    else:
                        self.ax.xaxis.set_major_formatter(DateFormatter('%b %d %H:%M'))
                        self.ax.xaxis.set_major_locator(DayLocator())

                    max_ticks = max(5, min(15, self.width() // 50))
                    self.ax.xaxis.set_major_locator(MaxNLocator(nbins=max_ticks, prune='both'))
                    self.ax.set_xlim(left=min(times))
                    self.figure.autofmt_xdate(rotation=45, ha='right')
                    label_fontsize = max(6, min(10, self.height() // 60))
                    for label in self.ax.get_xticklabels() + self.ax.get_yticklabels():
                        label.set_fontsize(label_fontsize)
                else:
                    self.ax.clear()
                    self.ax.text(0.5, 0.5, "No data available", ha='center', va='center', color='black', fontsize=12)
                    self.stats_bar.setText("Max: Up 0.00 Mbps, Down 0.00 Mbps | Bandwidth: Up 0.00 MB, Down 0.00 MB")
                    self.stats_bar.adjustSize()
                    self.stats_bar.move((self.canvas.width() - self.stats_bar.width() - self.hamburger_icon.width() - 6) // 2, 15)
                    try:
                        self.ax.legend().remove()
                    except AttributeError:
                        pass
                
                self.canvas.draw()
                self.logger.debug("Graph updated successfully")
                self._graph_update_pending = False
            except Exception as e:
                self.logger.error(f"Error in _perform_graph_update: {str(e)}", exc_info=True)
                self.ax.clear()
                self.ax.text(0.5, 0.5, "Error rendering graph", ha='center', va='center', color='red', fontsize=12)
                self.canvas.draw()
                self._graph_update_pending = False

        def export_history(self) -> None:
            """Export the network speed history to CSV."""
            try:
                app_data = self._parent.get_app_data_path()
                csv_path = os.path.join(app_data, 'nst_history.csv')
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                suggested_name = f"nst_History_{timestamp}.csv"
                file_path, _ = QFileDialog.getSaveFileName(
                    self, "Export History", suggested_name, "CSV Files (*.csv);;All Files (*.*)"
                )
                if file_path:
                    if os.path.exists(csv_path):
                        shutil.copy2(csv_path, file_path)
                        QMessageBox.information(self, "Success", f"History exported to:\n{file_path}")
                    else:
                        QMessageBox.warning(self, "Error", "No history data available to export.")
            except Exception as e:
                self.logger.error(f"Error exporting history: {str(e)}", exc_info=True)

        def save_figure(self) -> None:
            """Save the current graph as a PNG."""
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                suggested_name = f"NetSpeedTray_Graph_{timestamp}.png"
                file_path, _ = QFileDialog.getSaveFileName(
                    self, "Save Graph Image", suggested_name, "PNG Files (*.png);;All Files (*.*)"
                )
                if file_path:
                    self.figure.savefig(file_path, bbox_inches='tight', dpi=100)
                    QMessageBox.information(self, "Success", f"Graph saved to:\n{file_path}")
            except Exception as e:
                self.logger.error(f"Error saving graph image: {str(e)}", exc_info=True)

        def closeEvent(self, event) -> None:
            """Handle window closure."""
            try:
                self.logger.debug("Closing GraphWindow")
                self._realtime_timer.stop()  # Stop the live update timer
                if self._parent:
                    self._parent.config['dark_mode'] = self.dark_mode.value() == 1
                    self._parent.config['keep_data'] = {0: 1, 1: 3, 2: 7, 3: 31, 4: 180, 5: 365}[self.keep_data.value()]
                    period_map = {0: 'System Uptime', 1: 'Session', 2: '24h', 3: '1 Week', 4: '1 Month', 5: 'All'}
                    self._parent.config['history_period'] = period_map[self.history_period.value()]
                    position_map = {0: 'off', 1: 'left', 2: 'middle', 3: 'right'}
                    self._parent.config['legend_position'] = position_map[self.legend_position.value()]
                    self._parent.save_config()
                self.ax.clear()
                self.canvas.draw()
                plt.close(self.figure)
            except Exception as e:
                self.logger.error(f"Error during GraphWindow close: {str(e)}", exc_info=True)
            super().closeEvent(event)

    return NetworkSpeedWidget, SettingsDialog, GraphWindow

class NetworkMonitor:
    """Monitor class to manage the NetSpeedTray application lifecycle."""
    def __init__(self) -> None:
        """Initialize the NetworkMonitor with a mutex."""
        print("NetworkMonitor.__init__ started")
        mutex_name = "Global\\NetSpeedTray_SingleInstanceMutex"
        self.mutex = win32event.CreateMutex(None, False, mutex_name)
        last_error = win32api.GetLastError()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            print("Another instance of NetSpeedTray is already running.")
            sys.exit(1)
        print("Before QApplication creation")
        self.app = QApplication(sys.argv)
        print("QApplication created")
        self.logger = logging.getLogger('NetSpeedTray')
        signal.signal(signal.SIGINT, self.signal_handler)
        print("NetworkMonitor.__init__ completed")

    def signal_handler(self, sig: int, frame: Optional[frame]) -> None:
        """Handle SIGINT to shut down."""
        self.logger.debug("Received SIGINT, shutting down")
        if hasattr(self, 'widget') and self.widget:
            self.widget.close()
        if hasattr(self, 'mutex'):
            win32api.CloseHandle(self.mutex)
        sys.exit(0)

    def run(self) -> int:
        """Run the NetSpeedTray application."""
        print("NetworkMonitor.run started")
        NetworkSpeedWidget, SettingsDialog, GraphWindow = define_classes()
        self.widget = NetworkSpeedWidget()
        print("Widget created")
        self.widget.initialize_with_saved_position()
        print("Widget initialized")
        return self.app.exec()

if __name__ == "__main__":
    """Main entry point."""
    print("Main started")
    monitor = NetworkMonitor()
    sys.exit(monitor.run())
