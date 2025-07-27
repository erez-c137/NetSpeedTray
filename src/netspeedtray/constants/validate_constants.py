"""
Validation script for NetSpeedTray constants.

This script ensures all constants meet their constraints by calling the `validate` method
on each constants class, with logging for progress and errors.
"""

import logging
from typing import List

from .constants import (
    AppConstants,
    ConfigConstants,
    HelperConstants,
    NetworkSpeedConstants,
    TaskbarConstants,
    PositionConstants,
    TimerConstants,
    RendererConstants,
    WidgetStateConstants,
    ControllerConstants,
    InterfaceConstants,
    HistoryConstants,
    UIConstants,
    DialogConstants,
    FontConstants,
    SliderConstants,
    ToggleSwitchConstants,
    LayoutConstants,
    InterfaceGroupConstants,
    DebugConstants,
    ColorConstants,
    UIStyleConstants,
    GraphConstants,
    HistoryPeriodConstants,
    DataRetentionConstants,
    LegendPositionConstants,
    ExportConstants,
)
from .i18n_strings import I18nStrings

logger = logging.getLogger("NetSpeedTray.ValidateConstants")


def validate_all_constants() -> bool:
    """
    Validate all constants in the application.

    Iterates over all constant classes and calls their `validate` method, logging progress
    and any errors encountered.

    Returns:
        bool: True if all validations pass, False if any fail.

    Raises:
        Exception: Propagates any unexpected errors during validation.

    Examples:
        >>> validate_all_constants()
        True  # If all constants are valid
    """
    constant_classes: List[object] = [
        AppConstants(),
        ConfigConstants(),
        HelperConstants(),
        NetworkSpeedConstants(),
        TaskbarConstants(),
        PositionConstants(),
        TimerConstants(),
        RendererConstants(),
        WidgetStateConstants(),
        ControllerConstants(),
        InterfaceConstants(),
        HistoryConstants(),
        UIConstants(),
        DialogConstants(),
        FontConstants(),
        SliderConstants(),
        ToggleSwitchConstants(),
        LayoutConstants(),
        InterfaceGroupConstants(),
        DebugConstants(),
        ColorConstants(),
        UIStyleConstants(),
        GraphConstants(),
        HistoryPeriodConstants(),
        DataRetentionConstants(),
        LegendPositionConstants(),
        ExportConstants(),
        I18nStrings(),
    ]

    all_valid = True
    for constant_class in constant_classes:
        class_name = constant_class.__class__.__name__
        logger.info("Validating %s...", class_name)
        try:
            constant_class.validate()
            logger.debug("%s validated successfully", class_name)
        except ValueError as e:
            logger.error("Validation failed for %s: %s", class_name, e)
            all_valid = False
        except Exception as e:
            logger.exception("Unexpected error validating %s: %s", class_name, e)
            raise

    if all_valid:
        logger.info("All constants validated successfully!")
    else:
        logger.warning("Some constants failed validation")
    return all_valid


if __name__ == "__main__":
    # Configure basic logging for standalone execution
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    success = validate_all_constants()
    exit(0 if success else 1)