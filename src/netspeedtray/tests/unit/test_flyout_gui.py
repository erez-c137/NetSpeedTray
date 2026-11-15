"""GUI smoke tests for the frameless Flyout callout (pytest-qt, offscreen)."""
from PyQt6.QtCore import QPoint

from netspeedtray.views.flyout import Flyout


def test_flyout_constructs_and_dismisses(qtbot):
    f = Flyout("Title", "Message body", action_text="Go", auto_dismiss_ms=0)
    qtbot.addWidget(f)
    with qtbot.waitSignal(f.dismissed, timeout=500):
        f._dismiss()


def test_flyout_action_emits_then_dismisses(qtbot):
    f = Flyout("T", "M", action_text="Go", auto_dismiss_ms=0)
    qtbot.addWidget(f)
    fired = []
    f.action_clicked.connect(lambda: fired.append(True))
    with qtbot.waitSignal(f.dismissed, timeout=500):
        f._on_action()
    assert fired == [True]


def test_flyout_show_at_positions_and_arms_timer(qtbot):
    f = Flyout("T", "M", auto_dismiss_ms=5000)
    qtbot.addWidget(f)
    f.show_at(QPoint(800, 600))
    assert f.isVisible()
    assert f._timer.isActive()


def test_flyout_no_action_button_is_fine(qtbot):
    f = Flyout("T", "M", action_text=None, auto_dismiss_ms=0)
    qtbot.addWidget(f)
    assert f is not None


class _Screen:
    def __init__(self, rect):
        self._rect = rect

    def availableGeometry(self):
        return self._rect


def _shown_on(qtbot, monkeypatch, area, anchor):
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QGuiApplication
    screen = _Screen(QRect(*area))
    monkeypatch.setattr(QGuiApplication, "screenAt", staticmethod(lambda p: screen))
    f = Flyout("T", "M", auto_dismiss_ms=0)
    qtbot.addWidget(f)
    f.show_at(QPoint(*anchor))
    return f, QRect(*area)


def test_flyout_stays_on_a_monitor_left_of_the_primary(qtbot, monkeypatch):
    """2.2.1 review: negative coordinates were clamped to 0, so a widget on a monitor left of the
    primary (x < 0) got its flyout - the update notice too - on the primary monitor."""
    f, area = _shown_on(qtbot, monkeypatch, (-1920, 0, 1920, 1040), (-300, 1000))
    assert area.contains(f.geometry())
    assert f.geometry().right() < -300 + 1            # still opens to the anchor's left


def test_flyout_stays_on_a_monitor_above_the_primary(qtbot, monkeypatch):
    f, area = _shown_on(qtbot, monkeypatch, (0, -1080, 1920, 1040), (1600, -60))
    assert area.contains(f.geometry())


def test_flyout_clears_a_left_taskbar(qtbot, monkeypatch):
    """The anchor sits in a left taskbar (outside the work area): the flyout opens beside it, never
    over the taskbar."""
    f, area = _shown_on(qtbot, monkeypatch, (48, 0, 1872, 1080), (4, 900))
    assert area.contains(f.geometry())
