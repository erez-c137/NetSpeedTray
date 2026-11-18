"""
2.2.2 D7 - clearer words and order in Settings (and the Monitor bits that go with them).

Pins: the Network page's new name and order (adapter list last) with every deep link still landing; one
specific section header per page instead of three "Behavior"s; honest CPU/GPU switch labels; the plan
speed boxes' unit from the locale; the "Most connections" caption; plain tooltips for p95 / p99 / jitter;
"Check connection" in the right-click menu (shown by default, hideable); and the cross-reference to the
Network page following the page's own name in every locale.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QLabel, QMessageBox, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.components import SettingCard, SettingExpander
from netspeedtray.views.settings.dialog import SettingsDialog

DEFAULTS = constants.config.defaults.DEFAULT_CONFIG
D = constants.config.defaults
LOCALES = Path(__file__).resolve().parents[2] / "constants" / "locales"


def _dialog(qtbot, monkeypatch, lang="en_US"):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    m = MagicMock(); m.config = {}; m.config_manager = MagicMock()
    d = SettingsDialog(main_widget=m, config=dict(DEFAULTS), version="2.2.2", i18n=I18nStrings(lang),
                       available_interfaces=["Ethernet"], is_startup_enabled=False)
    qtbot.addWidget(d)
    d._build_all_pages()
    return d


@pytest.fixture
def dlg(qtbot, monkeypatch):
    return _dialog(qtbot, monkeypatch)


def _top(page):
    lay = page.layout()
    return [w for w in (lay.itemAt(k).widget() for k in range(lay.count())) if w is not None]


def _card_of(control):
    w = control
    while w is not None and not isinstance(w, SettingCard):
        w = w.parentWidget()
    return w


def _focus_positions(dlg, widgets):
    chain, w = [], dlg.nextInFocusChain()
    while w is not dlg and len(chain) < 5000:
        chain.append(w)
        w = w.nextInFocusChain()
    return [chain.index(x) for x in widgets]


# --- the Network page: name, order, links ---------------------------------------------------------------

def test_network_page_is_called_network(dlg):
    idx = dlg.page_index("network")
    assert dlg.sidebar.item(idx).text() == "Network"
    dlg.navigate_to_page(idx)
    assert dlg._page_title.text() == "Network"


def test_network_page_order_puts_the_adapter_list_last(dlg):
    n = dlg.interfaces_page
    top = _top(n)
    mode_header = next(w for w in top if isinstance(w, QLabel) and w.text() == dlg.i18n.MONITORING_MODE_LABEL)
    mode_card = n.auto_interface_radio.parentWidget()
    assert top.index(n.datacap) < top.index(n.connection) < top.index(n.identity) \
        < top.index(mode_header) < top.index(mode_card)
    assert top[-1] is mode_card          # the "Select specific" reveal grows at the bottom, shifts nothing
    pos = _focus_positions(dlg, [n.datacap, n.connection, n.identity, n.auto_interface_radio])
    assert pos == sorted(pos)


@pytest.mark.parametrize("page", ["network", "interfaces", 4])
@pytest.mark.parametrize("anchor", ["data_cap", "latency_enabled", "plan_speed", "latency_public_enabled"])
def test_network_deep_links_still_land(dlg, page, anchor):
    assert dlg.open_page(page, anchor) is True
    assert dlg.stack.currentWidget().widget() is dlg.interfaces_page


def test_wifi_section_has_a_plain_title(dlg):
    assert dlg.interfaces_page.identity._exp.header().findChild(QLabel) is not None
    titles = [l.text() for l in dlg.interfaces_page.identity.findChildren(QLabel)]
    assert "Wi-Fi band and name" in titles and "Network identity" not in titles


# --- one specific header per page, no "Behavior" ----------------------------------------------------------

def test_each_page_names_its_own_section(dlg):
    i = dlg.i18n
    expected = {0: (i.STARTUP_UPDATES_GROUP_TITLE, dlg.general_page.start_with_windows),
                1: (i.PLACEMENT_GROUP_TITLE, dlg.widget_page.preferred_monitor_combo),
                5: (i.WINDOWS_MOTION_GROUP_TITLE, dlg.advanced_page.reduce_motion)}
    assert len({t for t, _ in expected.values()}) == 3
    for index, (title, first) in expected.items():
        page = dlg.stack.widget(index).widget()
        top = _top(page)
        header = next(w for w in top if isinstance(w, QLabel) and w.text() == title)
        assert top[top.index(header) + 1] is _card_of(first), title
    every_label = [l.text() for k in range(dlg.stack.count())
                   for l in dlg.stack.widget(k).widget().findChildren(QLabel)]
    assert "Behavior" not in every_label
    assert not hasattr(i, "BEHAVIOR_GROUP_TITLE")


def test_appearance_speed_text_header(dlg):
    texts = [l.text() for l in dlg.appearance_page.findChildren(QLabel)]
    assert "Speed text layout" in texts and "Interface layout" not in texts


# --- Hardware: say what the switch does -------------------------------------------------------------------

@pytest.mark.parametrize("attr, title", [("monitor_cpu", "Show CPU usage"), ("monitor_gpu", "Show GPU usage")])
def test_cpu_gpu_switches_say_show_and_record(dlg, attr, title):
    card = _card_of(getattr(dlg.hardware_page, attr))
    texts = [l.text() for l in card.findChildren(QLabel)]
    assert title in texts
    assert dlg.i18n.HARDWARE_USAGE_DESC in texts
    assert "record" in dlg.i18n.HARDWARE_USAGE_DESC.lower()


# --- the plan speed boxes carry the locale's unit ---------------------------------------------------------

@pytest.mark.parametrize("lang, unit", [("en_US", "Mbps"), ("de_DE", "Mbit/s"), ("ru_RU", "Мбит/с")])
def test_plan_speed_unit_is_translated(qtbot, lang, unit):
    from netspeedtray.views.settings.pages.connection_section import ConnectionSettings
    w = ConnectionSettings(lambda: None, I18nStrings(lang))
    qtbot.addWidget(w)
    assert w._down.suffix() == f" {unit}" and w._up.suffix() == f" {unit}"
    w._down.setValue(0)
    assert w._down.specialValueText() == I18nStrings(lang).CONN_PLAN_OFF


# --- the cross-reference follows the page name, in every locale ------------------------------------------

@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_fixed_scale_note_names_the_network_page(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["NETWORK_INTERFACES_GROUP"] in data["GRAPH_FIXED_SCALE_DESC"], path.stem
    assert data["NETWORK_INTERFACES_GROUP"] == data["MONITOR_TAB_NETWORK"], path.stem   # one word for it


# --- Monitor: the caption and the tooltips ----------------------------------------------------------------

def test_most_connections_says_why_not_speed(q_app):
    from netspeedtray.views.monitor.overview.busiest_apps import BusiestAppsCard
    i18n = I18nStrings("en_US")
    card = BusiestAppsCard(i18n)
    texts = [l.text() for l in card.findChildren(QLabel)]
    assert i18n.MONITOR_MOST_CONNECTIONS_CAPTION in texts
    assert "per-app speed" in i18n.MONITOR_MOST_CONNECTIONS_CAPTION
    assert card.accessibleDescription() == i18n.MONITOR_MOST_CONNECTIONS_CAPTION


def test_stats_sheet_percentile_cells_explain_themselves(q_app):
    from netspeedtray.tests.unit.test_stats_detail import _open, _WS, _window, _cfg
    i18n = I18nStrings("en_US")
    subjects = [{"key": "download", "label": "Download", "unit": "Mbps", "kind": "net_down", "primary": True}]
    sheet = _open(_WS(), subjects, _window(), _cfg(), i18n)
    tips = {w.toolTip() for w in sheet.findChildren(QWidget) if w.objectName() == "statCell"}
    assert i18n.STAT_CELL_P95_TOOLTIP in tips and i18n.STAT_CELL_P99_TOOLTIP in tips
    sheet.close()


def test_check_connection_cells_explain_p95_and_jitter(q_app):
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    i18n = I18nStrings("en_US")
    sheet = ConnectionCheckSheet({"latency_public_enabled": False}, i18n, gateway_ip="192.168.1.1",
                                 app_version="9.9.9", autostart=False)
    tips = {w.toolTip() for w in sheet.findChildren(QWidget)}
    assert i18n.CHECK_CONN_JITTER_TOOLTIP in tips and i18n.STAT_CELL_P95_TOOLTIP in tips
    sheet.done(0)


# --- "Check connection" in the right-click menu -----------------------------------------------------------

def _menu(qt_owned, i18n, config):
    from netspeedtray.core.tray_manager import TrayIconManager
    widget = qt_owned(QWidget())
    widget.config = config
    widget.has_custom_position = lambda: False
    widget.fully_exit_application = lambda: None
    widget.open_connection_check = MagicMock()
    with patch("os.path.exists", return_value=False):
        manager = TrayIconManager(widget, i18n)
        manager.initialize()
    with patch("netspeedtray.core.update_checker.update_check_disabled_by_policy", return_value=False):
        manager._refresh_dynamic_items()
    return manager, widget


def _shown(manager):
    return [a.text() for a in manager.context_menu.actions() if a.isVisible() and not a.isSeparator()]


def test_menu_offers_check_connection_after_monitor(qt_owned):
    i18n = I18nStrings("en_US")
    m, widget = _menu(qt_owned, i18n, {})
    shown = _shown(m)
    assert shown == [i18n.SETTINGS_MENU_ITEM, i18n.SHOW_MONITOR_MENU_ITEM, i18n.CHECK_CONN_ACTION,
                     i18n.CHECK_FOR_UPDATES_MENU_ITEM, i18n.SUPPORT_MENU_ITEM, i18n.EXIT_MENU_ITEM]
    assert len(shown) <= 6        # the 2.2.1 default menu had 5 items: at most one more
    m.check_connection_action.trigger()
    widget.open_connection_check.assert_called_once()


def test_check_connection_can_be_hidden(qt_owned):
    i18n = I18nStrings("en_US")
    assert D.CONTEXT_MENU_ITEM_CHECK_CONNECTION in D.CONTEXT_MENU_HIDEABLE_ITEMS
    m, _ = _menu(qt_owned, i18n, {"context_menu_hidden_items": [D.CONTEXT_MENU_ITEM_CHECK_CONNECTION]})
    assert i18n.CHECK_CONN_ACTION not in _shown(m)


def test_settings_lists_check_connection_in_menu_order(qt_owned):
    from netspeedtray.views.settings.pages.general import GeneralPage
    page = qt_owned(GeneralPage(I18nStrings("en_US"), MagicMock()))
    rows = [c._title_label.text() for c in page.context_menu_items.findChildren(SettingCard)]
    assert rows[:3] == ["Show Monitor", "Check connection", "Pause / Resume"]
    page.load_settings({"context_menu_hidden_items": ["check_connection"]}, is_startup_enabled=False)
    assert not page.context_menu_toggles["check_connection"].isChecked()
    assert page.get_settings()["context_menu_hidden_items"] == ["check_connection"]
