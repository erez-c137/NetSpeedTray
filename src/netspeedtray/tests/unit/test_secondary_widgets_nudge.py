"""The one-time #200 Widgets-overlap notice on a SECONDARY taskbar (relu616, rc.4 on #373).

It never fired off the primary: the #200 probe (``widgets_rect``) is primary-only, so the notice
returned early, and even with a rect the overlap test divided by the scale without the screen's
origin. A secondary bar now uses its own Widgets box from the per-HWND layout cache
(``widgets_extent`` / its visible content), converted with ``physical_to_logical``.
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from netspeedtray import constants
from netspeedtray.utils import taskbar_utils
from netspeedtray.views.widget.main import NetworkSpeedWidget

E = constants.taskbar.edge
ORIGIN_X, SCALE = 1920.0, 1.5        # a 150 % monitor right of a 1920 px primary


def _phys(logical_x: float) -> int:
    return int(round(ORIGIN_X + (logical_x - ORIGIN_X) * SCALE))


# The Widgets content at logical 2070..2170 on the secondary, in physical px.
CONTENT = (_phys(2070), 1032, _phys(2170), 1080)
EXTENT = (_phys(2060), 1032, _phys(2180), 1080)


def _fake(x):
    return types.SimpleNamespace(
        _widgets_nudge_shown=False, logger=MagicMock(), config={"tray_offset_x": 50},
        update_config=MagicMock(), position_manager=MagicMock(), i18n=MagicMock(),
        x=lambda: x, y=lambda: 1032, width=lambda: 85, height=lambda: 32)


def _secondary(extent=EXTENT):
    screen = MagicMock()
    screen.geometry.return_value.left.return_value = ORIGIN_X
    screen.geometry.return_value.top.return_value = 0.0
    return types.SimpleNamespace(widgets_rect=None, widgets_extent=extent, is_primary=False, hwnd=0x2,
                                 dpi_scale=SCALE, get_edge_position=lambda: E.BOTTOM,
                                 get_screen=lambda: screen)


def _run(fake, tb, content=CONTENT):
    with patch("netspeedtray.views.widget.main.QTimer.singleShot") as shot, \
            patch.object(taskbar_utils, "set_widgets_probe_enabled"), \
            patch.object(taskbar_utils, "taskbar_layout_widgets_content", return_value=content):
        NetworkSpeedWidget._maybe_nudge_widgets_overlap(fake, tb)
    return shot


def test_a_widget_on_a_secondary_bars_widgets_button_gets_the_notice():
    fake = _fake(2090)                                   # 2090..2175 over the content 2070..2170
    shot = _run(fake, _secondary())
    assert fake._widgets_nudge_shown is True
    shot.assert_called_once()


def test_a_widget_clear_of_it_does_not():
    fake = _fake(2200)                                   # 2200..2285: right of the content
    _run(fake, _secondary())
    assert fake._widgets_nudge_shown is False


def test_the_full_box_answers_when_no_content_was_probed():
    fake = _fake(2172)                                   # right of the content, on the box's padding
    _run(fake, _secondary(), content=None)
    assert fake._widgets_nudge_shown is True


def test_no_widgets_box_on_that_bar_means_no_notice():
    fake = _fake(2090)
    _run(fake, _secondary(extent=None))
    assert fake._widgets_nudge_shown is False


def test_overlap_converts_around_the_screen_origin():
    # logical 2070..2170 <-> physical 2145..2295 on this screen; rect / scale alone says 1430..1530.
    assert taskbar_utils.rect_overlaps_x(2090, 2175, CONTENT, SCALE, origin=ORIGIN_X) is True
    assert taskbar_utils.rect_overlaps_x(1430, 1530, CONTENT, SCALE, origin=ORIGIN_X) is False
    # The primary (origin 0) is unchanged: rect / scale.
    assert taskbar_utils.rect_overlaps_x(1430, 1530, CONTENT, SCALE) is True
    assert taskbar_utils.rect_overlaps_y(1940, 1950, (0, 1950, 10, 1995), 1.5, origin=1920) is True
    assert taskbar_utils.rect_overlaps_y(1940, 1950, (0, 1950, 10, 1995), 1.5) is False
