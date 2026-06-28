"""
Accessibility pass for the Monitor: the at-a-glance Overview cards and the pivot tab strip must be
usable without a mouse (keyboard focus + activation + accessible names), and decorative motion must
yield to the OS "reduce animations" preference.
"""
import pytest
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QKeyEvent

from netspeedtray.constants.i18n import I18nStrings


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _press(widget, key) -> None:
    widget.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def test_stat_tile_is_focusable_named_and_keyboard_activatable(q_app):
    from netspeedtray.views.monitor.overview.tiles import StatTile
    t = StatTile("CPU", "#4CAF50")
    assert t.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert t.accessibleName() == "CPU"
    seen = []
    t.clicked.connect(lambda: seen.append(1))
    _press(t, Qt.Key.Key_Return)
    _press(t, Qt.Key.Key_Space)
    assert seen == [1, 1]                       # both Enter and Space activate the card
    t.set_label("Processor")                     # accessible name tracks a relabel
    assert t.accessibleName() == "Processor"


def test_network_hero_is_focusable_and_named(q_app):
    from netspeedtray.views.monitor.overview.tiles import NetworkHero
    h = NetworkHero(I18nStrings("en_US"), "#60CDFF", "#FFB900")
    assert h.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert h.accessibleName()                    # "Network"
    seen = []
    h.clicked.connect(lambda: seen.append(1))
    _press(h, Qt.Key.Key_Enter)
    assert seen == [1]


def test_usage_cap_hint_is_keyboard_accessible(q_app):
    from netspeedtray.views.monitor.overview.tiles import UsageTile
    u = UsageTile(I18nStrings("en_US"))
    flags = u._cap_hint.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    assert u._cap_hint.accessibleName()


def test_tab_bar_buttons_keyboard_reachable_and_named(q_app):
    from netspeedtray.views.monitor.tab_bar import FlatTabBar
    bar = FlatTabBar([("overview", "Overview"), ("network", "Network")])
    for tid, lbl in (("overview", "Overview"), ("network", "Network")):
        b = bar._buttons[tid]
        assert b.focusPolicy() == Qt.FocusPolicy.TabFocus
        assert b.accessibleName() == lbl


def test_prefers_reduced_motion_returns_bool():
    from netspeedtray.utils.styles import prefers_reduced_motion
    assert isinstance(prefers_reduced_motion(), bool)   # never throws; defaults to animate (False)
