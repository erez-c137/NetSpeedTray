"""
Application lifecycle management for NetSpeedTray.
"""

import logging
import signal
import sys
from typing import Optional

import win32api
import win32con
import win32event
import win32gui
import winerror
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

# The new, single, correct way to import all constants
from netspeedtray import constants

from netspeedtray.utils.config import ConfigManager, ConfigError
from netspeedtray.utils.taskbar_utils import get_taskbar_height
from netspeedtray.views.widget import NetworkSpeedWidget

class NetworkMonitor:
    """Manages the NetSpeedTray application lifecycle and single-instance execution."""

    def __init__(self) -> None:
        """Initialize the NetworkMonitor."""
        try:
            ConfigManager.setup_logging()
        except Exception as e:
            logging.basicConfig(level=logging.ERROR, stream=sys.stderr)
            logging.error("Failed to initialize logging: %s", e)
        self.app: Optional[QApplication] = None
        self.widget: Optional[NetworkSpeedWidget] = None
        self.mutex: Optional[int] = None
        self.i18n = constants.strings
        self.logger = logging.getLogger("NetSpeedTray.Monitor")

        self._setup_mutex()
        self._setup_app()
        self._setup_signal_handlers()

    def _setup_mutex(self) -> None:
        """Create a mutex for single-instance enforcement."""
        try:
            self.mutex = win32event.CreateMutex(None, False, constants.app.MUTEX_NAME)
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                self.logger.error("Another instance of NetSpeedTray is already running")
                sys.exit(1)
        except win32api.error as e:
            self.logger.error("Failed to create mutex: %s", e)
            sys.exit(1)

    def _setup_app(self) -> None:
        """Initialize the QApplication."""
        self.app = QApplication(sys.argv)

    def _setup_signal_handlers(self) -> None:
        """Configure signal handlers for shutdown."""
        signal.signal(signal.SIGINT, lambda s, f: self._cleanup(0))
        signal.signal(signal.SIGTERM, lambda s, f: self._cleanup(0))

    def _cleanup(self, exit_code: int = 0) -> None:
        """Clean up resources and exit."""
        if self.widget:
            if hasattr(self.widget, "controller") and self.widget.controller:
                try:
                    self.widget.controller.cleanup()
                except Exception as e:
                    self.logger.error("Failed to clean up controller: %s", e)
            if hasattr(self.widget, "widget_state") and self.widget.widget_state:
                try:
                    self.widget.widget_state.cleanup()
                except Exception as e:
                    self.logger.error("Failed to clean up widget state: %s", e)
            try:
                self.widget.close()
            except Exception as e:
                self.logger.error("Failed to close widget: %s", e)
        if self.mutex:
            try:
                win32api.CloseHandle(self.mutex)
            except win32api.error as e:
                self.logger.error("Failed to release mutex: %s", e)
        if self.app:
            try:
                self.app.quit()
            except Exception as e:
                self.logger.error("Failed to quit QApplication: %s", e)
        sys.exit(exit_code)

    def _show_error(self, message: str) -> None:
        """Display an error message."""
        self.logger.error(message)
        if self.app:
            msg = QMessageBox()
            msg.setWindowTitle(self.i18n.ERROR_WINDOW_TITLE)
            msg.setText(message)
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.exec()

    def run(self) -> int:
        """Launch the NetSpeedTray application."""
        if not self.app:
            self._show_error("QApplication not initialized")
            return 1

        try:
            from netspeedtray.utils.helpers import get_app_data_path
            app_data_path = get_app_data_path()
            config_path = str(app_data_path / constants.config.defaults.CONFIG_FILENAME).replace('\\', '/')
            config_manager = ConfigManager(str(config_path))
            config = config_manager.load()
        except ConfigError as e:
            self._show_error(f"Failed to load configuration: {e}")
            return 1
        except Exception as e:
            self._show_error(f"Unexpected error loading configuration: {e}")
            return 1

        try:
            taskbar_height = get_taskbar_height()
            self.widget = NetworkSpeedWidget(
                taskbar_height=taskbar_height,
                config=config
            )
            self.widget.set_app_version(constants.app.VERSION)
            QTimer.singleShot(1000, self._safe_show_widget)
        except Exception as e:
            self.logger.error("Widget initialization failed: %s (%s)", e, type(e).__name__)
            self._show_error(f"Widget initialization failed: {e}")
            return 1

        try:
            return self.app.exec()
        except Exception as e:
            self.logger.error("Error in QApplication.exec(): %s", e)
            return 1

    def _safe_show_widget(self) -> None:
        """Safely show the widget, checking if it was initialized successfully."""
        if self.widget and hasattr(self.widget, 'renderer') and self.widget.renderer:
            self.widget.show()
        else:
            self.logger.error("Cannot show widget: Initialization incomplete or renderer missing")
            if self.app:
                self.app.quit()

if __name__ == "__main__":
    monitor = NetworkMonitor()
    sys.exit(monitor.run())