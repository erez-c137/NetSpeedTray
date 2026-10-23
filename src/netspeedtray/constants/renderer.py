"""
Constants influencing the rendering of the network speed widget.
"""
from typing import Final
from netspeedtray.constants.color import color # Import the master color palette

class RendererConstants:
    """Defines constants for rendering the widget display."""
    MIN_GRAPH_POINTS: Final[int] = 2
    MIN_SPEED_FOR_COLOR: Final[float] = 0.01
    LINE_WIDTH: Final[int] = 1
    
    # --- Padding and Margins ---
    TEXT_MARGIN: Final[int] = 2
    # Breathing room kept between the RIGHT-ALIGNED side-by-side content and the widget's tray-side edge,
    # so the readout doesn't crowd the tray-overflow chevron once it's anchored right. Larger than
    # TEXT_MARGIN (which only kept text off the very edge) - this is deliberate visual padding.
    RIGHT_ANCHOR_PADDING: Final[int] = 7

    # --- Paused cue (Pause in the menu / click action) ---
    # A paused widget draws its content at this opacity (still readable) and a two-bar pause glyph in
    # place of the arrow column - or in the free left margin when no arrows are drawn - so frozen
    # numbers read as paused rather than hung. The glyph never overlaps text.
    PAUSED_CONTENT_OPACITY: Final[float] = 0.55
    PAUSE_GLYPH_HEIGHT_RATIO: Final[float] = 0.45         # of the arrow column's height (both rows)
    PAUSE_GLYPH_MARGIN_HEIGHT_RATIO: Final[float] = 0.3   # of the widget's height, margin placement
    PAUSE_GLYPH_MIN_HEIGHT: Final[int] = 7
    PAUSE_GLYPH_MAX_HEIGHT: Final[int] = 14
    PAUSE_GLYPH_WIDTH_RATIO: Final[float] = 0.6           # glyph width (two bars + gap) per height
    PAUSE_GLYPH_MARGIN_PAD: Final[int] = 2                # clear space each side in the margin
    GRAPH_MARGIN: Final[int] = 3
    GRAPH_LEFT_PADDING: Final[int] = 2
    GRAPH_RIGHT_PADDING: Final[int] = 2
    GRAPH_BOTTOM_PADDING: Final[int] = 1
    VALUE_UNIT_GAP: Final[int] = 5
    ARROW_NUMBER_GAP: Final[int] = 5
    
    # --- Sizing and Scaling ---
    GRAPH_HEIGHT_PERCENTAGE: Final[float] = 0.8
    DEFAULT_ARROW_WIDTH: Final[int] = 5
    # Lowest top of the WIDGET mini graph's auto-fit axis, in bytes/sec: 125,000 B/s = 1 Mbps (legibility
    # "L", 3a). At the old 12.5 KB/s (100 Kbps) floor, 1-9 KB/s of background chatter drew a half-full
    # graph under a readout of 0.0 - the everyday-user review read it as "something is downloading".
    # The Monitor window's axes are separate (views/graph) and keep their own floor.
    MIN_Y_SCALE: Final[int] = 125000
    GRAPH_Y_AXIS_PADDING_FACTOR: Final[float] = 1.15 # Add 15% headroom
    # Gap between the upload and download panels when the graphs are split (a faint divider sits in it).
    GRAPH_SPLIT_GAP: Final[int] = 4

    # --- Mini graph "Trend" style (graph_style == "trend") ---
    # Smoothing window as a fraction of the samples on screen (so the curve looks equally calm at any
    # timespan / graph width), never fewer than TREND_MIN_WINDOW samples. 0.06 of a 3 min window at
    # 1 s polls = 11 s; of a 30 min window = 108 s. See widget_renderer.trend_smooth().
    TREND_WINDOW_FRACTION: Final[float] = 0.06
    TREND_MIN_WINDOW: Final[int] = 3
    # Window at the right edge ("now"), in samples: fixed, so the newest point's lag does not grow with
    # the timespan (11 = the 3 min default's own window; ~11 s at 1 s polls).
    TREND_EDGE_WINDOW: Final[int] = 11
    # Ridge on the fitted slope at the series' ends (0 = full local-linear, large = plain truncated
    # average): trades the edge's lag against its per-second wag. Numbers in trend_smooth's docstring.
    TREND_EDGE_SLOPE_DAMPING: Final[float] = 0.5
    TREND_LINE_WIDTH: Final[float] = 1.75
    # Top alpha of an optional fill under a trend line, fading to 0 at the bottom like Detailed's
    # (whose top alpha is 120). 0 = no fill, the choice: rendered side by side, a faint wash (36) was
    # all but invisible on a dark bar at 1x and only tinted the text behind it on a light one - the
    # Trend style's point is a clean line. Kept as a one-number switch.
    TREND_FILL_ALPHA: Final[int] = 0
    # Top alpha of the Detailed style's fill gradient (fades to 0 at the bottom). 120 before the
    # legibility package; with the blend now plain SourceOver (no additive "glow") 80 keeps the area
    # readable as an area without washing out the digits it sits behind.
    DETAILED_FILL_ALPHA: Final[int] = 80

    # --- Erasing halo (legibility "L", item 1) ---
    # With the mini graph on, the graph is drawn into its own layer and a soft outline around every text
    # run (digits, units, arrows, hardware text and icons) is ERASED from that layer before it is
    # composited, so the taskbar itself shows through around the glyphs: no colour to guess (a painted
    # halo needs the real bar colour, and Windows never outlines taskbar text). Radius in logical px -
    # the layers are device-pixel-ratio aware, so it scales with the display (1.5 px at 100 %, 2.25 at
    # 150 %) - fully erased out to HALO_RADIUS_PX, then fading to nothing over HALO_FEATHER_PX.
    HALO_RADIUS_PX: Final[float] = 1.5
    HALO_FEATHER_PX: Final[float] = 1.0
    HALO_DIRECTIONS: Final[int] = 16      # stamps per ring; 16 keeps a 2.5 px ring free of gaps

    # Accent-coloured taskbar (legibility "L", 5a): any themed pen under this contrast against the real
    # bar colour is painted in the default text colour instead (paint time only - the config is untouched).
    # 3:1 is the WCAG non-text / large-text floor; a band colour on a same-hue accent bar fell to ~1.5:1.
    ACCENT_PEN_MIN_CONTRAST: Final[float] = 3.0

    # Which half of the colour scheme a backdrop gets (utils/color_scheme.backdrop_is_light): light -
    # dark text - above this relative luminance. 0.1791 is where black and white text have EQUAL WCAG
    # contrast (sqrt(1.05 * 0.05) - 0.05), so this picks the better-reading text on every backdrop; a
    # 0.5 split would put white text on mid greys that black reads far better on (#808080: 3.9 vs
    # 5.3:1). Both taskbar references sit far from it (#202020 0.014, #EEEEEE 0.855).
    BACKDROP_LUMINANCE_SPLIT: Final[float] = 0.1791

    # Two-row readout geometry, shared by the renderer and the layout's fit check (#322): the gap
    # between the upload and download rows, and the CPU/GPU icon box (drawn up from the baseline).
    TWO_ROW_GAP_PX: Final[int] = 1
    HW_ICON_SIZE_PX: Final[int] = 11
    # Rows of clear space the fit check keeps above the measured ink (hinting at 125/150% DPI).
    INK_FIT_TOP_MARGIN_PX: Final[int] = 1
    # Display scales the fit check must hold at (Windows' standard 100-250% steps).
    INK_FIT_SCALES: Final[tuple] = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5)
    # The fixed strings the docked readout paints, for the ink-extent fit check (#322) - each measured
    # on its own, as it is drawn. The unit labels are added from the active locale at runtime. Only
    # glyphs the readout really draws: a generic "gjpqy|" sample measured 1 px deeper than any of them.
    INK_REFERENCE_STRINGS: Final[tuple] = ("0123456789.,", "100%", "(99°C, 250.0W)", "(N/A)",
                                           "CPU GPU", "RAM VRAM", "99.9/99.9G")

    # --- Side (LEFT/RIGHT) taskbar compact readout (the "fits" ladder T1-T5) ---
    # Tighter than ARROW_NUMBER_GAP / VALUE_UNIT_GAP: on a 48 px bar (44 px inside the margins) the
    # classic gaps alone cost 10 px. About one space wide in the default 9 pt font.
    # 3, not 2, since 2.2 rc.2 (7e): at 2 px the arrow's ink touched the first digit on a 48 px bar.
    COMPACT_ARROW_GAP_PX: Final[int] = 3
    COMPACT_UNIT_GAP_PX: Final[int] = 3
    # Icon + gap in front of a compact CPU/GPU percent (the icon is HW_ICON_SIZE_PX wide).
    COMPACT_HW_ICON_GAP_PX: Final[int] = 2

    # --- Display Cycling ---
    CYCLE_INTERVAL_MS: Final[int] = 5000 # 5 seconds per metric
    
    # --- Theming ---
    GRAPH_LINE_COLOR: Final[str] = color.WHITE # Reference master palette
    CPU_LINE_COLOR: Final[str] = "#3498db" # Blue
    GPU_LINE_COLOR: Final[str] = "#e74c3c" # Red
    # Optional labels in front of the memory values (#250). Not translated - same as the
    # hard-coded CPU/GPU labels, these are universal abbreviations.
    MEMORY_LABEL_RAM: Final[str] = "RAM"
    MEMORY_LABEL_VRAM: Final[str] = "VRAM"

    # ========== MATPLOTLIB GRAPH WINDOW CONSTANTS ==========
    
    # Gap Detection & Interpolation
    GAP_DETECTION_MULTIPLIER: Final[float] = 2.5  # median_interval * multiplier = gap threshold
    MIN_GAP_THRESHOLD_SEC: Final[float] = 10.0    # Minimum gap size (prevents noise)
    SPLINE_INTERPOLATION_POINT_THRESHOLD: Final[int] = 600  # Skip interpolation if > this many points
    SPLINE_INTERPOLATION_DENSITY: Final[int] = 4  # Interpolate 3 new points between each pair
    
    # Axis & Display
    Y_AXIS_PADDING_FACTOR: Final[float] = 1.05   # Extra space for peak labels
    MIN_Y_AXIS_RANGE_MBPS: Final[float] = 1.0    # Fallback range when data is flat
    
    # Settings Panel Resizing
    PANEL_RESIZE_THRESHOLD_PX: Final[int] = 5    # Threshold to detect actual panel changes
    MIN_SETTINGS_PANEL_WIDTH_PX: Final[int] = 320  # Fallback panel width
    
    # Peak Markers - Glowing Effects
    PEAK_MARKER_SIZE_OUTER: Final[int] = 14      # Outer circle size (pts)
    PEAK_MARKER_SIZE_MIDDLE: Final[int] = 9      # Middle circle size (pts)
    PEAK_MARKER_SIZE_INNER: Final[int] = 5       # Inner circle size (pts)
    PEAK_MARKER_ALPHA_OUTER: Final[float] = 0.15 # Outer transparency
    PEAK_MARKER_ALPHA_MIDDLE: Final[float] = 0.35  # Middle transparency
    PEAK_MARKER_ALPHA_INNER: Final[float] = 1.0  # Inner (solid)
    MIN_SPEED_FOR_PEAK_MARKER_MBPS: Final[float] = 0.1  # Don't show markers for negligible peaks
    
    # Gradient Fills
    GRADIENT_ALPHA_TOP: Final[float] = 0.35      # Gradient opacity at top
    GRADIENT_ALPHA_BOTTOM: Final[float] = 0.0    # Gradient opacity at bottom
    GRADIENT_IMAGE_HEIGHT: Final[int] = 256      # Gradient resolution
    
    # Event Markers - System Boot Time
    BOOT_MARKER_LINEWIDTH: Final[int] = 1        # Boot marker line width
    BOOT_MARKER_ALPHA: Final[float] = 0.6        # Boot marker transparency
    BOOT_MARKER_LINESTYLE: Final[str] = '--'     # Dashed line
    BOOT_LABEL_Y_POSITION: Final[float] = 0.92   # Label Y position (% of axis)
    
    # Interpolation Guards
    EXTENT_EPSILON: Final[float] = 1e-9          # Prevent singular extent errors
    FLAT_DATA_FALLBACK_RANGE: Final[float] = 1.0  # Range when all data is 0

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.MIN_GRAPH_POINTS < 2:
            raise ValueError("MIN_GRAPH_POINTS must be at least 2")
        if not (0.0 < self.GRAPH_HEIGHT_PERCENTAGE <= 1.0):
            raise ValueError("GRAPH_HEIGHT_PERCENTAGE must be between 0 and 1")
        if self.GRAPH_Y_AXIS_PADDING_FACTOR < 1.0:
            raise ValueError("GRAPH_Y_AXIS_PADDING_FACTOR must be >= 1.0")

# Singleton instance for easy access
renderer = RendererConstants()