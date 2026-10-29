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
    # The Settings sidebar grows past SIDEBAR_WIDTH to fit its longest translated page name, up to this.
    SIDEBAR_MAX_WIDTH: Final[int] = 260
    # Horizontal breathing room around the Settings card column (Win11 Settings insets its content
    # generously from the sidebar + window edge; the bare 16px read edge-to-edge). The window width
    # formula and the content layout both use this so the cards keep their width as the inset grows.
    SETTINGS_CONTENT_MARGIN: Final[int] = 32
    # A SettingCard keeps its control beside the title only while the title still gets at least this
    # much width (or its whole one-line width, if shorter); below that the control drops under the
    # title - the Win11 Settings narrow-window reflow - so no card ever forces the page to scroll sideways.
    SETTING_CARD_MIN_TITLE_WIDTH: Final[int] = 120
    # The same floor for a card's description (or its whole one-line width, if shorter): a long
    # description never wraps into a thin column beside the control - the control drops under it.
    SETTING_CARD_MIN_DESC_WIDTH: Final[int] = 200

    # --- Interface Page Specific ---
    INTERFACE_SCROLL_MAX_ITEMS: Final[int] = 7
    INTERFACE_SCROLL_MIN_HEIGHT: Final[int] = 80
    
    # --- Monitor Window ---
    # One fixed-height command band sits at the very top of every Monitor tab (directly under the
    # pivot strip). Identical height + top origin on all tabs is what stops the timeline/NIC controls
    # from jumping vertically when the user switches tabs. Tab body content gets this side inset so it
    # lines up under the band's controls.
    MONITOR_HEADER_BAND_HEIGHT: Final[int] = 56
    # The Network header's "From recorded history" note (D5): a wrapped caption beside the totals.
    # It wraps to at most MAX_LINES inside the fixed band; anything longer ends in "…" (tooltip: all).
    MONITOR_HISTORY_NOTE_MIN_WIDTH: Final[int] = 160
    MONITOR_HISTORY_NOTE_MAX_WIDTH: Final[int] = 340
    # Near the Monitor's 660 px minimum the note gives way instead of the controls: it narrows down to
    # this floor (two clamped lines still read as a phrase), and below it hides - its text moves to the
    # totals' tooltips. Holding 160 px there squeezed the timeline and Live pill to "Mu…" / "tempo r"
    # in every locale (rc.2 S7).
    MONITOR_HISTORY_NOTE_COMPACT_MIN_WIDTH: Final[int] = 96
    MONITOR_HISTORY_NOTE_MAX_LINES: Final[int] = 2
    MONITOR_BODY_MARGIN: Final[int] = 16
    # Gap between the right-cluster command-band controls (NIC / timeline / Live). Shared by every tab
    # so the spacing around the Live pill is identical on Network and Hardware.
    MONITOR_CONTROL_SPACING: Final[int] = 10
    # The Network header's interface dropdown: as wide as its name up to MAX, eliding down to MIN when
    # the window is narrow (it used to be pinned at 190 px and slid under the timeline below ~800 px).
    MONITOR_IFACE_BUTTON_MIN_WIDTH: Final[int] = 96
    MONITOR_IFACE_BUTTON_MAX_WIDTH: Final[int] = 240

    # --- Main Widget Specific ---
    WIDGET_DEFAULT_RIGHT_PADDING_PX: Final[int] = 10
    SMALL_TASKBAR_HEIGHT_THRESHOLD: Final[int] = 34
    HORIZONTAL_LAYOUT_SEPARATOR: Final[str] = " | "
    MINI_GRAPH_HORIZONTAL_WIDTH: Final[int] = 40
    # Segment gaps for Side-by-Side mode (Network | CPU/GPU). Kept compact so the network readout sits
    # close to the hardware rather than floating far from it.
    WIDGET_SEGMENT_GAP_AFTER_NETWORK_PX: Final[int] = 10
    WIDGET_SEGMENT_GAP_BETWEEN_HARDWARE_PX: Final[int] = 5
    # A side (LEFT/RIGHT) taskbar is too narrow for the segments in a row, so they stack top-to-bottom
    # with this gap between them instead.
    WIDGET_SEGMENT_GAP_STACKED_PX: Final[int] = 4
    # Minimum gap (logical px) kept between the widget's tray-side edge and the tray boundary /
    # the "^" show-hidden-icons chevron, so the widget never abuts it and steals its clicks (#161 pt1).
    TRAY_EDGE_MIN_GAP_PX: Final[int] = 8
    # Minimum gap (logical px) kept between the widget and the Win11 Widgets/weather button when that
    # button sits between the widget's default spot and the tray (a LEFT-aligned bar puts it right
    # beside the tray). The same rule as the tray gap: never abut a shell button and steal its clicks
    # (rc.3 VM 2026-10-29: the default spot covered the button's right half, arrows touching "Windy").
    WIDGETS_BUTTON_MIN_GAP_PX: Final[int] = 8
    # The #24 yield's full-bar fallback (rc.3) holds the widget at the rc.2 tray spot while the app
    # buttons reach the spot left of the Widgets button; it returns only once they are this much MORE
    # than the usual gap clear of it (logical px, so DPI-scaled; half a button slot). Spatial
    # hysteresis: a group end hovering at the threshold must not swing the widget a whole Widgets
    # button left and right on every probe.
    WIDGETS_FALLBACK_RETURN_MARGIN_PX: Final[int] = 22
    # A Win11 SECONDARY side (left/right) taskbar has no TrayNotifyWnd, so before (or without) a UIA
    # probe of its clock, this many logical px at the bar's bottom are kept for it - the vertical twin
    # of DEFAULT_SECONDARY_CLOCK_RESERVE_PX. Probed live (VM, Win11 26300, 100 %, 2026-10-23): the
    # clock button spans 1036..1072 on a 1080 px bar, so 44 = its top; equal to the probe, the widget
    # does not move when the real clock rect lands.
    SECONDARY_SIDE_CLOCK_RESERVE_PX: Final[int] = 44
    # Classic (Win10) taskbar toolbars (#324, find_toolbar_bands_rect): a ReBar band counts as "after
    # the task list" when it starts no more than this many physical px before the task band's end
    # (adjacent bands share the edge; slack for a gripper), and at most this many ReBar children are
    # looked at (a real taskbar has a handful).
    TOOLBAR_BAND_ADJACENCY_TOLERANCE_PX: Final[int] = 2
    TOOLBAR_BAND_SCAN_LIMIT: Final[int] = 32
    # One Win11 taskbar button slot along a horizontal bar, logical px (44 physical px at 96 DPI,
    # scaled with the DPI; owner's probe 2026-10-22). Only an ESTIMATE for the #24 yield's "is the app
    # group near the widget" poll gating - the collision itself always uses UIA's real rects.
    APP_BUTTON_SLOT_PX: Final[int] = 44
    # The #24 yield only trusts a UIA app group that lies inside the taskbar's own window rect (read
    # just before and after the probe), within this many physical px. Right after a live display-scale
    # change Explorer can report the buttons in a stale scale's coordinates for seconds (rc.2 final VM
    # run, 150 -> 100 % with an auto-hide bar: a centred group read as 1007..1873 = 1.5 x its real
    # 671..1249, a 10 s false step-aside); such a rect no longer fits the bar. Real buttons sit well
    # inside it; the slack only absorbs rounding.
    APP_GROUP_BAR_FIT_TOLERANCE_PX: Final[int] = 4

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
