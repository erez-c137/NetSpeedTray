"""
Utilities submodule for NetSpeedTray.

Provides helper functions and configuration management.

The exports are lazy (PEP 562), for the same reason as ``netspeedtray.core``: importing any
``netspeedtray.utils.<module>`` runs this file first, and the eager imports here loaded PyQt6
(``styles``, ``core.database``) for callers that need none of it - ``--shutdown``'s logging setup.
"""

__all__ = ["ConfigManager", "DatabaseWorker", "get_app_data_path", "is_dark_mode"]


def __getattr__(name):
    if name == "ConfigManager":
        from netspeedtray.utils.config import ConfigManager
        return ConfigManager
    if name == "DatabaseWorker":
        from netspeedtray.core.database import DatabaseWorker
        return DatabaseWorker
    if name == "get_app_data_path":
        from netspeedtray.utils.helpers import get_app_data_path
        return get_app_data_path
    if name == "is_dark_mode":
        from netspeedtray.utils.styles import is_dark_mode
        return is_dark_mode
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
