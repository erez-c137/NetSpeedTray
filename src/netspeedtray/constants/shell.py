"""
Constants for Windows Shell and API interactions.
"""
from typing import Final, Set

class ShellConstants:
    """Constants for identifying special Windows Shell UI elements."""
    # Class names of shell flyouts that can be potential obstructions.
    UI_CLASS_NAMES_TO_CHECK: Final[Set[str]] = {
        "Windows.UI.Core.CoreWindow",
        "ApplicationFrameWindow",
    }
    # Executable names of specific shell processes that ARE obstructions.
    UI_PROCESS_NAMES_TO_HIDE: Final[Set[str]] = {
        "startmenuexperiencehost.exe",
        "searchexperiencehost.exe",
        "shellexperiencehost.exe",
    }

    def __init__(self) -> None:
        self.validate()
        
    def validate(self) -> None:
        if not self.UI_CLASS_NAMES_TO_CHECK:
            raise ValueError("UI_CLASS_NAMES_TO_CHECK must not be empty.")
        if not self.UI_PROCESS_NAMES_TO_HIDE:
            raise ValueError("UI_PROCESS_NAMES_TO_HIDE must not be empty.")

class WindowsAPIConstants:
    """Constants for Windows API calls."""
    ABM_GETTASKBARPOS: Final[int] = 0x00000005
    ABM_GETSTATE: Final[int] = 0x00000004
    ABS_AUTOHIDE: Final[int] = 0x00000001
    ABS_ALWAYSONTOP: Final[int] = 0x00000002
    ABE_LEFT: Final[int] = 0
    ABE_TOP: Final[int] = 1
    ABE_RIGHT: Final[int] = 2
    ABE_BOTTOM: Final[int] = 3
    
    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        # Simple check for non-negative integers
        for attr_name in dir(self):
            if attr_name.isupper() and not attr_name.startswith('_'):
                value = getattr(self, attr_name)
                if not isinstance(value, int) or value < 0:
                    raise ValueError(f"{attr_name} must be a non-negative integer.")

class ShellInteractionConstants:
    """Container for Shell and API constants."""
    def __init__(self) -> None:
        self.shell = ShellConstants()
        self.api = WindowsAPIConstants()

# Singleton instance for easy access
shell = ShellInteractionConstants()