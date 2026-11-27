"""Auto-hide taskbar ride-along: the widget follows the bar's slide (offset), hides once the bar is
gone (hidden), and is re-placed by the full refresh only while the bar is fully out (fully shown)."""
import pytest

from netspeedtray import constants
from netspeedtray.utils.taskbar_utils import (
    autohide_bar_fully_shown, autohide_bar_hidden, autohide_bar_offset,
)

E = constants.taskbar.edge
MON = (0, 0, 1920, 1080)


# (edge, fully out, 18 px into the slide, hidden with 2 px peeking)
CASES = [
    (E.BOTTOM, (0, 1032, 1920, 1080), (0, 1050, 1920, 1098), (0, 1078, 1920, 1126)),
    (E.TOP, (0, 0, 1920, 48), (0, -18, 1920, 30), (0, -46, 1920, 2)),
    (E.LEFT, (0, 0, 48, 1080), (-18, 0, 30, 1080), (-46, 0, 2, 1080)),
    (E.RIGHT, (1872, 0, 1920, 1080), (1890, 0, 1938, 1080), (1918, 0, 1966, 1080)),
]


@pytest.mark.parametrize("edge, shown, sliding, hidden", CASES)
def test_offset_tracks_the_slide(edge, shown, sliding, hidden):
    assert autohide_bar_offset(shown, MON, edge) == 0
    assert autohide_bar_offset(sliding, MON, edge) == 18
    assert autohide_bar_offset(hidden, MON, edge) == 46


@pytest.mark.parametrize("edge, shown, sliding, hidden", CASES)
def test_shown_sliding_hidden_are_told_apart(edge, shown, sliding, hidden):
    assert autohide_bar_fully_shown(shown, MON, edge) and not autohide_bar_hidden(shown, MON, edge)
    assert not autohide_bar_fully_shown(sliding, MON, edge) and not autohide_bar_hidden(sliding, MON, edge)
    assert not autohide_bar_fully_shown(hidden, MON, edge) and autohide_bar_hidden(hidden, MON, edge)


def test_a_few_px_of_slack_still_counts_as_shown():
    assert autohide_bar_fully_shown((0, 1034, 1920, 1082), MON, E.BOTTOM)
