"""
Application entry point and lifecycle management for NetSpeedTray.
"""

import logging
import signal
import sys
from typing import Optional

import win32gui
import win32con
import win32api
import win32event
import winerror
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

from netspeedtray import constants
from netspeedtray.utils.config import ConfigManager, ConfigError
from netspeedtray.utils.taskbar_utils import get_taskbar_height
from netspeedtray.views.widget import NetworkSpeedWidget

class SingleInstanceChecker:
    """
    Ensures that only one instance of the application can run at a time using a system-wide mutex.

    This class is designed to be used as a context manager.
    
    Raises:
        RuntimeError: If another instance of the application is already running or if the
                      mutex cannot be created.
    """
    def __init__(self):
        self.mutex = None
        self.logger = logging.getLogger("NetSpeedTray.SingleInstanceChecker")
        try:
            self.mutex = win32event.CreateMutex(None, False, constants.app.MUTEX_NAME)
            if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
                self.logger.error("Another instance of NetSpeedTray is already running.")
                raise RuntimeError("Application is already running.")
        except win32api.error as e:
            self.logger.error("Failed to create mutex: %s", e)
            raise RuntimeError(f"Failed to create mutex: {e}") from e

    def __enter__(self):
        """Enter the context manager, acquiring the mutex."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context manager, releasing the mutex handle."""
        if self.mutex:
            try:
                win32api.CloseHandle(self.mutex)
            except win32api.error as e:
                self.logger.error("Failed to release mutex: %s", e)

def main() -> int:
    """
    Main entry point for the NetSpeedTray application.

    Orchestrates the application's startup sequence:
    1. Sets up logging.
    2. Ensures single-instance execution (or handles --shutdown command).
    3. Loads configuration.
    4. Initializes internationalization with the user's chosen language.
    5. Creates the main widget and runs the application event loop.

    Returns:
        An integer exit code.
    """
    # 1. Set up logging immediately so that any subsequent errors can be recorded.
    ConfigManager.setup_logging()
    logger = logging.getLogger("NetSpeedTray.Main")
    
    # The QApplication must be created before any UI elements.
    app = QApplication(sys.argv)

    if "--shutdown" in sys.argv:
        # This is a special mode triggered by the installer.
        # We broadcast a custom Windows message that only our running application will understand.
        logger.info("Shutdown command received. Broadcasting WM_USER_SHUTDOWN.")
        try:
            # Register a custom Windows message. The name must be unique.
            WM_USER_SHUTDOWN = win32gui.RegisterWindowMessageW("NetSpeedTray_WM_SHUTDOWN")
            
            # Broadcast this message to all top-level windows. Our app will be listening.
            win32gui.PostMessage(win32con.HWND_BROADCAST, WM_USER_SHUTDOWN, 0, 0)
            
            logger.info("Broadcast message sent. Exiting shutdown command.")
            return 0  # Exit successfully
        except Exception as e:
            logger.error(f"Error during shutdown command: {e}", exc_info=True)
            return 1  # Return an error code

    i18n_strings: Optional[constants.i18n.I18nStrings] = None 

    try:
        # 2. Use the context manager to ensure only one instance is running.
        with SingleInstanceChecker():
            # 3. Load the application configuration from the file.
            config_manager = ConfigManager()
            config = config_manager.load()

            # 4. Initialize the internationalization module with the user's saved language.
            i18n_strings = constants.i18n.get_i18n(config.get("language"))

            # 5. Create and configure the main widget.
            taskbar_height = get_taskbar_height()
            widget = NetworkSpeedWidget(
                taskbar_height=taskbar_height,
                config=config,
                i18n=i18n_strings
            )
            widget.set_app_version(constants.app.VERSION)
            
            # 6. Configure the application to behave like a tray utility.
            app.setQuitOnLastWindowClosed(False)

            # 7. Set up signal handlers to gracefully call the widget's own cleanup routine.
            signal.signal(signal.SIGINT, lambda s, f: QApplication.instance().quit())
            signal.signal(signal.SIGTERM, lambda s, f: QApplication.instance().quit())

            # 8. Show the widget after a short delay to ensure the event loop is running.
            QTimer.singleShot(500, widget.show)

            # 9. Start the application event loop.
            return app.exec()

    except Exception as e:
        # This is a global catch-all for any critical error during startup.
        logger.critical("A critical error occurred during startup: %s", e, exc_info=True)
        title = i18n_strings.ERROR_WINDOW_TITLE if i18n_strings else "Application Error"
        QMessageBox.critical(None, title, f"A critical error occurred and NetSpeedTray must close:\n\n{e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())