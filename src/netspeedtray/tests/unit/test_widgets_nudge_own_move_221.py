"""The one-time #200 "overlaps the Widgets panel" notice never fires for a spot the app chose (2.2.1).

Found live on 2026-11-16 testing #522: on a left-aligned bar full up to the Widgets button, the #24
yield's full-bar fallback moved the widget to the rc.2 spot by the tray ("staying by the tray instead"),
and the notice told the user it was sitting over the Widgets panel. The fallback takes that spot only
when it clears the weather text (#466), but the notice compared the widget with the #200 probe's rect -
the union of every descendant of the button, its full-size container too (1538..1674) - instead of the
icon + text extent the placement uses (1541..1644), and it was explicitly allowed while the fallback
was active. Now the app's own spots (default and fallback, hiding on or off) never show it, and a drag
is judged against the weather text.
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.utils import taskbar_utils
from netspeedtray.views.widget.main import NetworkSpeedWidget

E = constants.taskbar.edge
GAP = constants.layout.WIDGETS_BUTTON_MIN_GAP_PX
CONTAINER = (1538, 1036, 1674, 1076)     # the #200 probe's rect: the button's whole container
TEXT = (1541, 1036, 1644, 1076)          # the layout probe's weather icon + text
W = 85
TRAY_SPOT_X = TEXT[2] + GAP              # the fallback's tray spot: clear of the text by the gap


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QApplication.instance() or QApplication([])


def _fake(x, config, fallback=False):
    pm = MagicMock()
    pm.is_widgets_fallback_active.return_value = fallback
    pm.is_yielding_to_apps.return_value = False
    return types.SimpleNamespace(
        _widgets_nudge_shown=False, logger=MagicMock(), config=config, update_config=MagicMock(),
        position_manager=pm, i18n=MagicMock(),
        x=lambda: x, y=lambda: 1032, width=lambda: W, height=lambda: 48)


def _bar():
    return types.SimpleNamespace(widgets_rect=CONTAINER, hwnd=0x4242, is_primary=True, dpi_scale=1.0,
                                 get_edge_position=lambda: E.BOTTOM)


def _run(fake, tb, content=TEXT):
    with patch("netspeedtray.views.widget.main.QTimer.singleShot") as shot, \
            patch.object(taskbar_utils, "set_widgets_probe_enabled"), \
            patch.object(taskbar_utils, "taskbar_layout_widgets_content", return_value=content):
        NetworkSpeedWidget._maybe_nudge_widgets_overlap(fake, tb)
    return shot


def test_the_tray_spot_overlaps_the_container_but_clears_the_text():
    # The live geometry the bug needs: the fallback's spot is on the button's box, off its text.
    assert TRAY_SPOT_X < CONTAINER[2] and TRAY_SPOT_X >= TEXT[2] + GAP


@pytest.mark.parametrize("hide_when_apps_cover", [False, True])
def test_no_notice_when_the_fallback_moved_the_widget_to_the_tray_spot(hide_when_apps_cover):
    # Hiding off: #522's "staying by the tray instead"; hiding on: the yield's retreat (#463/#466).
    fake = _fake(TRAY_SPOT_X, {"tray_offset_x": 0, "hide_when_apps_cover": hide_when_apps_cover},
                 fallback=True)
    shot = _run(fake, _bar())
    assert fake._widgets_nudge_shown is False
    fake.update_config.assert_not_called()
    shot.assert_not_called()


@pytest.mark.parametrize("hide_when_apps_cover", [False, True])
def test_no_notice_for_the_fallback_even_before_the_layout_probe_answers(hide_when_apps_cover):
    # Only the #200 container rect known: the app's own spot still never shows it.
    fake = _fake(TRAY_SPOT_X, {"tray_offset_x": 0, "hide_when_apps_cover": hide_when_apps_cover},
                 fallback=True)
    _run(fake, _bar(), content=None)
    assert fake._widgets_nudge_shown is False


def test_a_widget_dragged_onto_the_weather_text_still_gets_the_notice():
    fake = _fake(1560, {"tray_offset_x": 50})            # 1560..1645 over the text 1541..1644
    shot = _run(fake, _bar())
    assert fake._widgets_nudge_shown is True
    fake.update_config.assert_called_once_with({"widgets_overlap_nudge_shown": True})
    shot.assert_called_once()


def test_a_drag_is_judged_on_the_weather_text_not_the_container():
    # Dropped on the button's padding past the text: over the container, clear of the weather.
    fake = _fake(TRAY_SPOT_X, {"tray_offset_x": 50})
    _run(fake, _bar())
    assert fake._widgets_nudge_shown is False


def test_a_drag_falls_back_to_the_200_rect_while_the_layout_probe_has_no_answer():
    fake = _fake(TRAY_SPOT_X, {"tray_offset_x": 50})
    _run(fake, _bar(), content=None)
    assert fake._widgets_nudge_shown is True
