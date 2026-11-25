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

@pytest.mark.parametrize("attr, title, history", [
    ("monitor_cpu", "Show CPU usage", True), ("monitor_gpu", "Show GPU usage", True),
    ("monitor_ram", "Show RAM usage", True),
    ("monitor_vram", "Show VRAM usage", False),   # VRAM history is not kept (controller.handle_stats)
])
def test_usage_switches_say_show_and_whether_history_is_kept(dlg, attr, title, history):
    card = _card_of(getattr(dlg.hardware_page, attr))
    texts = [l.text() for l in card.findChildren(QLabel)]
    assert title in texts
    assert (dlg.i18n.HARDWARE_USAGE_DESC in texts) is history
    assert dlg.i18n.HARDWARE_USAGE_DESC == "Also keeps its history in the Monitor."


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
    assert i18n.MONITOR_MOST_CONNECTIONS_CAPTION == "Ranked by open connections, not speed."
    assert i18n.MONITOR_MOST_CONNECTIONS_CAPTION in texts
    assert card.accessibleDescription() == i18n.MONITOR_MOST_CONNECTIONS_CAPTION


def _cell(root, caption):
    """The stat cell whose caption label reads ``caption`` (value label first, caption second)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QFrame
    found = [f for f in root.findChildren(QFrame)
             if [l.text() for l in f.findChildren(QLabel, options=Qt.FindChildOption.FindDirectChildrenOnly)][1:2]
             == [caption]]
    assert len(found) >= 1, caption
    return found[0]


def _stats_sheet(i18n, exact=True, **cfg):
    from netspeedtray.tests.unit.test_stats_detail import _open, _WS, _window, _cfg
    kind = ({"key": "download", "label": "Download", "unit": "Mbps", "kind": "net_down", "primary": True}
            if exact else {"key": "cpu", "label": "CPU", "unit": "%", "kind": "hw", "primary": True})
    return _open(_WS(), [kind], _window(), _cfg(**cfg), i18n)


def test_stats_sheet_percentile_cells_explain_themselves(q_app):
    """Each tooltip on ITS cell (a swap of the two would fail), as tooltip and accessible description;
    the other figures carry none."""
    i18n = I18nStrings("en_US")
    sheet = _stats_sheet(i18n)
    for caption, tip in ((i18n.STAT_CELL_P95, i18n.STAT_CELL_P95_TOOLTIP),
                         (i18n.STAT_CELL_P99, i18n.STAT_CELL_P99_TOOLTIP)):
        cell = _cell(sheet, caption)
        assert cell.toolTip() == tip and cell.accessibleDescription() == tip, caption
    assert i18n.STAT_CELL_P95_TOOLTIP.startswith("95%") and i18n.STAT_CELL_P99_TOOLTIP.startswith("99%")
    for caption in (i18n.STAT_CELL_MEDIAN, i18n.STAT_CELL_MIN, i18n.STAT_CELL_AVG, i18n.STAT_CELL_PEAK):
        cell = _cell(sheet, caption)
        assert cell.toolTip() == "" and cell.accessibleDescription() == "", caption
    sheet.close()


def test_stats_sheet_dashed_percentiles_have_no_tooltip(q_app):
    i18n = I18nStrings("en_US")
    sheet = _stats_sheet(i18n, exact=False)   # rolled-up data: the percentile cells read "-"
    for caption in (i18n.STAT_CELL_P95, i18n.STAT_CELL_P99):
        cell = _cell(sheet, caption)
        assert cell.findChildren(QLabel)[0].text() == "-"
        assert cell.toolTip() == "" and cell.accessibleDescription() == "", caption
    sheet.close()


@pytest.mark.parametrize("lang, unit", [("en_US", "Mbps"), ("de_DE", "Mbit/s"), ("ru_RU", "Мбит/с")])
def test_below_plan_line_uses_the_locale_unit(q_app, lang, unit):
    sheet = _stats_sheet(I18nStrings(lang), plan_down_mbps=100)
    texts = [l.text() for l in sheet.findChildren(QLabel)]
    assert any(f"100 {unit}" in t for t in texts), texts
    sheet.close()


def test_check_connection_cells_explain_p95_and_jitter(q_app):
    from netspeedtray.views.monitor.connection_check import ConnectionCheckSheet
    i18n = I18nStrings("en_US")
    sheet = ConnectionCheckSheet({"latency_public_enabled": False}, i18n, gateway_ip="192.168.1.1",
                                 app_version="9.9.9", autostart=False)
    from netspeedtray.core import connection_check as CC
    card = sheet._cards[CC.HOP_GATEWAY]
    for caption, tip in ((i18n.STAT_CELL_P95, i18n.CHECK_CONN_P95_TOOLTIP),
                         (i18n.CHECK_CONN_JITTER, i18n.CHECK_CONN_JITTER_TOOLTIP)):
        cell = _cell(card, caption)
        assert cell.toolTip() == tip and cell.accessibleDescription() == tip, caption
    assert "replies" in i18n.CHECK_CONN_P95_TOOLTIP                 # ping times: its own text
    assert i18n.CHECK_CONN_P95_TOOLTIP != i18n.STAT_CELL_P95_TOOLTIP
    for caption in (i18n.STAT_CELL_MEDIAN, i18n.CHECK_CONN_MAX, i18n.STAT_CELL_MIN):
        cell = _cell(card, caption)
        assert cell.toolTip() == "" and cell.accessibleDescription() == "", caption
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
    assert shown == [i18n.SETTINGS_MENU_ITEM, i18n.SHOW_MONITOR_MENU_ITEM, i18n.CHECK_CONN_MENU_ITEM,
                     i18n.LOCK_POSITION_MENU_ITEM,
                     i18n.CHECK_FOR_UPDATES_MENU_ITEM, i18n.SUPPORT_MENU_ITEM, i18n.EXIT_MENU_ITEM]
    # The 2.2.1 default menu had 5 items; 2.2.2 adds Check connection (D7) and Lock position (#616, the
    # owner's call: findable where users are told it is). Usage history (#619) is opt-in.
    assert len(shown) <= 7
    m.check_connection_action.trigger()
    widget.open_connection_check.assert_called_once()


def test_check_connection_can_be_hidden(qt_owned):
    i18n = I18nStrings("en_US")
    assert D.CONTEXT_MENU_ITEM_CHECK_CONNECTION in D.CONTEXT_MENU_HIDEABLE_ITEMS
    m, _ = _menu(qt_owned, i18n, {"context_menu_hidden_items": [D.CONTEXT_MENU_ITEM_CHECK_CONNECTION]})
    assert i18n.CHECK_CONN_MENU_ITEM not in _shown(m)


def test_settings_lists_check_connection_in_menu_order(qt_owned):
    from netspeedtray.views.settings.pages.general import GeneralPage
    page = qt_owned(GeneralPage(I18nStrings("en_US"), MagicMock()))
    rows = [c._title_label.text() for c in page.context_menu_items.findChildren(SettingCard)]
    # Usage history (#619, opt-in) sits between Show Monitor and Check connection, as in the menu.
    assert rows[:4] == ["Show Monitor", "Usage history", "Check connection", "Pause / Resume"]
    page.load_settings({"context_menu_hidden_items": ["check_connection"]}, is_startup_enabled=False)
    assert not page.context_menu_toggles["check_connection"].isChecked()
    assert page.get_settings()["context_menu_hidden_items"] == ["check_connection"]


# --- review round 1: tooltips name the switch; one description size ---------------------------------------

@pytest.mark.parametrize("path", sorted(LOCALES.glob("*.json")), ids=lambda p: p.stem)
def test_grayed_tooltips_name_the_switch_in_every_locale(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["MONITOR_CPU_LABEL"] in d["HARDWARE_NEEDS_CPU_TOOLTIP"], path.stem
    assert d["MONITOR_GPU_LABEL"] in d["HARDWARE_NEEDS_GPU_TOOLTIP"], path.stem
    assert d["MONITOR_CPU_LABEL"] in d["HARDWARE_NEEDS_CPU_OR_GPU_TOOLTIP"], path.stem
    assert d["MONITOR_GPU_LABEL"] in d["HARDWARE_NEEDS_CPU_OR_GPU_TOOLTIP"], path.stem
    assert "{unit}" in d["STATS_DETAIL_BELOW_PLAN_TEMPLATE"], path.stem


def test_adapter_list_subtitles_use_the_description_font(dlg):
    from netspeedtray.constants.styles import styles as tokens
    from netspeedtray.utils import styles as su
    n = dlg.interfaces_page
    mode_card = n.auto_interface_radio.parentWidget()
    subtitle = next(l for l in mode_card.findChildren(QLabel) if l.text() == dlg.i18n.MONITORING_MODE_AUTO_SUBTITLE)
    assert subtitle.font().pointSizeF() == su.font(tokens.TYPE_CAPTION).pointSizeF()
    assert "font-size" not in subtitle.styleSheet()
