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


# --- lessons learned from the audit: the LIST ROWS (the tabs' actual content) were mouse-only -------

def _row(**over):
    r = {"identity_key": "app-1", "display_name": "Firefox", "conn_count": 5,
         "established_count": 3, "host_count": 2}
    r.update(over)
    return r


def test_app_row_is_keyboard_activatable_and_named(q_app):
    from netspeedtray.views.monitor.network.app_list import AppRow
    row = AppRow(I18nStrings("en_US"))
    row.update_row(_row(), max_conn=10)
    assert row.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert "Firefox" in row.accessibleName()        # screen readers get the content, not a blank frame
    got = []
    row.clicked.connect(got.append)
    _press(row, Qt.Key.Key_Return)
    _press(row, Qt.Key.Key_Space)
    assert got == ["app-1", "app-1"]                 # Enter and Space both open the app's detail


def test_hardware_row_has_accessible_name(q_app):
    from netspeedtray.views.monitor.hardware.list import HardwareRow
    row = HardwareRow(I18nStrings("en_US"))
    row.update_row({"display_name": "python.exe", "cpu_percent": 12.0, "rss": 1.5e8, "gpu_percent": 0.0},
                   max_cpu=50.0, gpu_available=True)
    assert "python.exe" in row.accessibleName()


def test_top_talkers_card_is_keyboard_activatable(q_app):
    from netspeedtray.views.monitor.overview.busiest_apps import BusiestAppsCard
    card = BusiestAppsCard(I18nStrings("en_US"))
    assert card.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert card.accessibleName()
    got = []
    card.go_to_network.connect(lambda: got.append(1))
    _press(card, Qt.Key.Key_Return)
    assert got == [1]


def test_timeline_selector_is_focusable_and_named(q_app):
    from netspeedtray.views.monitor.timeline_selector import TimelineSelector
    ts = TimelineSelector(I18nStrings("en_US"), current_index=2)
    assert ts._btn.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert ts._btn.accessibleName()    # announces the selected window


def test_subtle_dark_text_meets_wcag_aa_on_the_card(q_app):
    """The app-wide caption colour must clear WCAG AA (4.5:1) on the dark card - regression for #808080."""
    from netspeedtray.constants.styles import styles as S

    def _lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def _lum(hexstr):
        r, g, b = (int(hexstr[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)

    l1, l2 = _lum(S.SUBTLE_TEXT_COLOR_DARK), _lum(S.CARD_BG_DARK)
    ratio = (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
    assert ratio >= 4.5, f"subtle dark text contrast {ratio:.2f}:1 fails WCAG AA"
