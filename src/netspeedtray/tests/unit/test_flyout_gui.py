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
