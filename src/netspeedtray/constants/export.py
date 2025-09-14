"""
Constants related to exporting data (CSV) and graphs (Images).
"""
import os
from typing import Final

class ExportConstants:
    """Defines constants for data and image export."""
    # The default directory for all exports, typically the user's Documents folder.
    DEFAULT_EXPORT_PATH: Final[str] = os.path.join(os.path.expanduser("~"), "Documents")
    
    # Filename templates, using a clear timestamp format.
    CSV_SUGGESTED_NAME_TEMPLATE: Final[str] = "nst_history_{timestamp}.csv"
    IMAGE_SUGGESTED_NAME_TEMPLATE: Final[str] = "nst_graph_{timestamp}.png"
    
    # The format used to generate the {timestamp} placeholder.
    TIMESTAMP_FORMAT: Final[str] = "%Y%m%d_%H%M%S"
    
    # Resolution for exported graph images.
    IMAGE_DPI: Final[int] = 150

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if "{timestamp}" not in self.CSV_SUGGESTED_NAME_TEMPLATE:
             raise ValueError("CSV_SUGGESTED_NAME_TEMPLATE must contain {timestamp}")
        if "{timestamp}" not in self.IMAGE_SUGGESTED_NAME_TEMPLATE:
             raise ValueError("IMAGE_SUGGESTED_NAME_TEMPLATE must contain {timestamp}")
        if self.IMAGE_DPI <= 0:
            raise ValueError("IMAGE_DPI must be positive")

# Singleton instance for easy access
export = ExportConstants()