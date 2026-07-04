"""
Constants defining default margins and spacing for UI layouts.
"""
from typing import Final

class LayoutConstants:
    """Defines default margins and spacing for Qt layouts."""
    # --- General Purpose Spacing ---
    HORIZONTAL_SPACING_SMALL: Final[int] = 5
    HORIZONTAL_SPACING_MEDIUM: Final[int] = 8
    VERTICAL_SPACING: Final[int] = 5
    SPACING: Final[int] = 8
    DEFAULT_PADDING: Final[int] = 4

    # --- General Dialog Layout ---
    MAIN_MARGIN: Final[int] = 16
    MAIN_SPACING: Final[int] = 12
    GROUP_BOX_SPACING: Final[int] = 12
    SIDEBAR_WIDTH: Final[int] = 200
    # Horizontal breathing room around the Settings card column (Win11 Settings insets its content
    # generously from the sidebar + window edge; the bare 16px read edge-to-edge). The window width
    # formula and the content layout both use this so the cards keep their width as the inset grows.
    SETTINGS_CONTENT_MARGIN: Final[int] = 32

    # --- Interface Page Specific ---
    INTERFACE_SCROLL_MAX_ITEMS: Final[int] = 7
    INTERFACE_SCROLL_MIN_HEIGHT: Final[int] = 80
    
    # --- Collapsible Section ---
    COLLAPSIBLE_HEADER_HEIGHT: Final[int] = 36
    COLLAPSIBLE_SECTION_SPACING: Final[int] = 8

    # --- Monitor Window ---
    # One fixed-height command band sits at the very top of every Monitor tab (directly under the
    # pivot strip). Identical height + top origin on all tabs is what stops the timeline/NIC controls
    # from jumping vertically when the user switches tabs. Tab body content gets this side inset so it
    # lines up under the band's controls.
    MONITOR_HEADER_BAND_HEIGHT: Final[int] = 56
    MONITOR_BODY_MARGIN: Final[int] = 16
    # Gap between the right-cluster command-band controls (NIC / timeline / Live). Shared by every tab
    # so the spacing around the Live pill is identical on Network and Hardware.
    MONITOR_CONTROL_SPACING: Final[int] = 10

    # --- Main Widget Specific ---
    WIDGET_DEFAULT_RIGHT_PADDING_PX: Final[int] = 10
    SMALL_TASKBAR_HEIGHT_THRESHOLD: Final[int] = 34
    HORIZONTAL_LAYOUT_SEPARATOR: Final[str] = " | "
    MINI_GRAPH_HORIZONTAL_WIDTH: Final[int] = 40
    # Segment gaps for Side-by-Side mode (Network | CPU/GPU). Kept compact so the network readout sits
    # close to the hardware rather than floating far from it.
    WIDGET_SEGMENT_GAP_AFTER_NETWORK_PX: Final[int] = 10
    WIDGET_SEGMENT_GAP_BETWEEN_HARDWARE_PX: Final[int] = 5
    # Minimum gap (logical px) kept between the widget's tray-side edge and the tray boundary /
    # the "^" show-hidden-icons chevron, so the widget never abuts it and steals its clicks (#161 pt1).
    TRAY_EDGE_MIN_GAP_PX: Final[int] = 8

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate that all layout constants are valid non-negative numbers or strings."""
        for attr_name in dir(self):
            if not attr_name.startswith('_') and attr_name.isupper():
                value = getattr(self, attr_name)
                if isinstance(value, int) and value < 0:
                    raise ValueError(f"{attr_name} must be a non-negative integer.")
                elif not isinstance(value, (int, str)):
                    raise ValueError(f"{attr_name} must be an integer or a string.")

# Singleton instance for easy access
layout = LayoutConstants()
