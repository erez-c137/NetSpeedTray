"""#200 - the pure overlap decision behind the one-time Widgets/weather nudge (rect_overlaps_x)."""
from netspeedtray.utils.taskbar_utils import rect_overlaps_x


def test_no_overlap_when_widget_is_right_of_weather_content():
    # Weather content 1489..1589 (physical), dpi 1.0; the minimal widget docks at 1605..1690 (in the
    # element's dead space + the gap) -> NOT counted as overlap, so no nudge.
    assert rect_overlaps_x(1605, 1690, (1489, 1032, 1589, 1080), 1.0) is False


def test_overlap_when_widget_covers_weather_content():
    # A wider widget docked at 1440..1690 reaches into the visible content (ends 1589) -> nudge.
    assert rect_overlaps_x(1440, 1690, (1489, 1032, 1589, 1080), 1.0) is True


def test_none_widgets_rect_is_no_overlap():
    assert rect_overlaps_x(0, 100, None, 1.0) is False


def test_nonpositive_dpi_is_no_overlap():
    assert rect_overlaps_x(0, 100, (10, 0, 90, 40), 0) is False


def test_dpi_scaling_is_applied():
    # Physical content 1489..1589 at dpi 2.0 -> logical 744.5..794.5.
    assert rect_overlaps_x(800, 900, (1489, 1032, 1589, 1080), 2.0) is False   # widget right of it
    assert rect_overlaps_x(700, 760, (1489, 1032, 1589, 1080), 2.0) is True    # widget reaches into it


def test_touching_edges_do_not_overlap():
    # Widget's left edge exactly at the content's right edge is adjacent, not overlapping (strict).
    assert rect_overlaps_x(1589, 1700, (1489, 1032, 1589, 1080), 1.0) is False
