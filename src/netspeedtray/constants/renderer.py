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
    GRAPH_MARGIN: Final[int] = 1
    GRAPH_LEFT_PADDING: Final[int] = 2
    GRAPH_RIGHT_PADDING: Final[int] = 2
    GRAPH_BOTTOM_PADDING: Final[int] = 1
    VALUE_UNIT_GAP: Final[int] = 5
    ARROW_NUMBER_GAP: Final[int] = 5
    
    # --- Sizing and Scaling ---
    GRAPH_HEIGHT_PERCENTAGE: Final[float] = 0.8
    DEFAULT_ARROW_WIDTH: Final[int] = 5
    # Min speed for y-axis scale, in bytes/sec (500 Kbps)
    MIN_Y_SCALE: Final[int] = 62500
    GRAPH_Y_AXIS_PADDING_FACTOR: Final[float] = 1.15 # Add 15% headroom
    
    # --- Display Cycling ---
    CYCLE_INTERVAL_MS: Final[int] = 5000 # 5 seconds per metric
    
    # --- Theming ---
    GRAPH_LINE_COLOR: Final[str] = color.WHITE # Reference master palette
    CPU_LINE_COLOR: Final[str] = "#3498db" # Blue
    GPU_LINE_COLOR: Final[str] = "#e74c3c" # Red

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