"""
Constants related to logging configuration and file management.
"""
import logging
from typing import Final

class LogConstants:
    """Defines constants for logging configuration."""
    LOG_FILENAME: Final[str] = "NetSpeedTray_Log.log"
    LOG_FORMAT: Final[str] = "%(asctime)s - %(name)s - %(levelname)s - %(module)s.%(funcName)s:%(lineno)d - %(message)s"
    LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"
    FILE_LOG_LEVEL: Final[int] = logging.INFO
    CONSOLE_LOG_LEVEL: Final[int] = logging.INFO
    PRODUCTION_LOG_LEVEL: Final[int] = logging.WARNING
    
    # ADDED/RENAMED constants
    MAX_LOG_SIZE: Final[int] = 10 * 1024 * 1024
    BYTES_TO_MEGABYTES: Final[int] = 1024 * 1024
    LOG_BACKUP_COUNT: Final[int] = 3

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.LOG_FILENAME:
            raise ValueError("LOG_FILENAME must not be empty")
        if self.MAX_LOG_SIZE <= 0:
            raise ValueError("MAX_LOG_SIZE must be positive")
        valid_levels = [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]
        if self.FILE_LOG_LEVEL not in valid_levels:
            raise ValueError(f"Invalid FILE_LOG_LEVEL: {self.FILE_LOG_LEVEL}")

# Singleton instance for easy access
logs = LogConstants()