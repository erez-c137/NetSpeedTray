"""
Constants related to application state, controller logic, and data storage.
"""
from typing import Final

class WidgetStateConstants:
    """Constants related to the internal state of the main widget."""
    # Tolerance in pixels for considering the widget position unchanged.
    POSITION_TOLERANCE: Final[int] = 5
    
    # How often to commit collected speed data to the database, in seconds.
    DB_COMMIT_INTERVAL: Final[int] = 60

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.POSITION_TOLERANCE < 0:
            raise ValueError("POSITION_TOLERANCE must be non-negative")
        if self.DB_COMMIT_INTERVAL <= 0:
            raise ValueError("DB_COMMIT_INTERVAL must be positive")

class ControllerConstants:
    """Constants specific to the application's main controller."""
    # Log the current speed every N updates for debugging purposes.
    SPEED_LOGGING_FREQUENCY: Final[int] = 60

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.SPEED_LOGGING_FREQUENCY <= 0:
            raise ValueError("SPEED_LOGGING_FREQUENCY must be positive")

class StateAndLogicConstants:
    """Container for state and controller logic constants."""
    def __init__(self) -> None:
        self.widget = WidgetStateConstants()
        self.controller = ControllerConstants()

# Singleton instance for easy access
state = StateAndLogicConstants()