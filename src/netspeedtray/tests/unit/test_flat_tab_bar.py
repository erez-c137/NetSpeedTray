"""FlatTabBar - the Monitor window's Fluent pivot tab strip (no matplotlib, owns no pages)."""
import pytest
from PyQt6.QtWidgets import QApplication

from netspeedtray.views.monitor.tab_bar import FlatTabBar

_TABS = [("overview", "Overview"), ("network", "Network"), ("hardware", "Hardware")]


@pytest.fixture(scope="session")
def q_app():
    return QApplication.instance() or QApplication([])


def test_first_tab_checked_by_default(q_app):
    bar = FlatTabBar(_TABS)
    assert bar._buttons["overview"].isChecked()


def test_click_emits_stack_index(q_app):
    bar = FlatTabBar(_TABS)
    seen = []
    bar.tab_selected.connect(seen.append)
    bar._buttons["network"].click()
    assert seen == [1]


def test_set_current_index_selects_and_emits(q_app):
    bar = FlatTabBar(_TABS)
    seen = []
    bar.tab_selected.connect(seen.append)
    bar.setCurrentIndex(2)
    assert bar._buttons["hardware"].isChecked()
    assert seen == [2]


def test_set_tab_visible_hides_button(q_app):
    bar = FlatTabBar(_TABS)
    bar.set_tab_visible("hardware", False)
    assert bar._buttons["hardware"].isHidden()
    bar.set_tab_visible("hardware", True)
    assert not bar._buttons["hardware"].isHidden()
