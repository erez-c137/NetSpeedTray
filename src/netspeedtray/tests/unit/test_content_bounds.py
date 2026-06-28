"""
Regression test for the context-menu centering bug: with hardware monitoring on, the widget draws a
network segment AND a CPU/GPU segment side by side. The menu must center over the WHOLE widget, not
the last-drawn segment (which would shove it to the right). WidgetRenderer.get_content_bounds()
returns the union of all segments drawn in a paint; the tray menu uses it instead of the last rect.
"""
import pytest
from PyQt6.QtCore import QRect

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.widget_renderer import WidgetRenderer


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _renderer():
    return WidgetRenderer({}, I18nStrings("en_US"))


def test_content_bounds_unions_all_segments(q_app):
    r = _renderer()
    r.reset_content_bounds()
    # network segment on the left, hardware (CPU/GPU) segment on the right
    r._extend_content_bounds(QRect(0, 5, 100, 20))
    r._last_text_rect = QRect(0, 5, 100, 20)
    r._extend_content_bounds(QRect(140, 5, 60, 20))
    r._last_text_rect = QRect(140, 5, 60, 20)        # last segment = just the hardware part

    cb = r.get_content_bounds()
    assert cb.left() == 0 and cb.right() == 199       # spans BOTH segments
    assert cb.width() == 200
    # The menu centers on cb.center(); over the whole 200px, not the 140-199 hardware segment.
    assert cb.center().x() == 99
    assert cb.center().x() != r.get_last_text_rect().center().x()   # the bug would use the right one


def test_content_bounds_resets_per_paint(q_app):
    r = _renderer()
    r._extend_content_bounds(QRect(0, 0, 300, 20))    # stale from a previous (wide) paint
    r.reset_content_bounds()
    r._extend_content_bounds(QRect(0, 0, 80, 20))     # network-only paint now
    r._last_text_rect = QRect(0, 0, 80, 20)
    assert r.get_content_bounds().width() == 80       # no leftover width from last paint


def test_content_bounds_falls_back_to_last_rect_when_empty(q_app):
    r = _renderer()
    r.reset_content_bounds()
    r._last_text_rect = QRect(10, 0, 50, 20)
    assert r.get_content_bounds() == r._last_text_rect   # nothing accumulated -> last rect


def test_extend_ignores_invalid_rects(q_app):
    r = _renderer()
    r.reset_content_bounds()
    r._extend_content_bounds(QRect())                 # null
    r._extend_content_bounds(QRect(0, 0, 0, 20))      # empty width
    r._extend_content_bounds(QRect(5, 0, 40, 20))     # valid
    assert r.get_content_bounds() == QRect(5, 0, 40, 20)
