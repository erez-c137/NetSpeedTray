"""
Choosing which items show in the widget's right-click menu (2.2.1, from #526).

`context_menu_hidden_items` lists the optional items the user turned off. Settings and Exit always show,
whatever the config says; separators never lead, trail or double up; the schema drops unknown ids one by
one; and Settings > General round-trips the list.
"""
import itertools
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.tray_manager import TrayIconManager
from netspeedtray.utils.config import ConfigManager
from netspeedtray.views.settings.pages.general import GeneralPage, strip_mnemonic

D = constants.config.defaults
HIDEABLE = list(D.CONTEXT_MENU_HIDEABLE_ITEMS)


@pytest.fixture
def i18n():
    return I18nStrings("en_US")


def _menu(qt_owned, i18n, config, moved=True, policy=False):
    """A real TrayIconManager over a bare widget; `moved` makes Reset Position eligible."""
    widget = qt_owned(QWidget())
    widget.config = config
    widget.has_custom_position = lambda: moved
    widget.fully_exit_application = lambda: None
    with patch("os.path.exists", return_value=False):
        manager = TrayIconManager(widget, i18n)
        manager.initialize()
    with patch("netspeedtray.core.update_checker.update_check_disabled_by_policy", return_value=policy):
        manager._refresh_dynamic_items()
    return manager


def _shown(manager):
    """The menu as the user sees it: visible item texts, '-' for each visible separator."""
    return ["-" if a.isSeparator() else a.text() for a in manager.context_menu.actions() if a.isVisible()]


def test_default_menu(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {})
    # 2.2.2 D7 added Check connection after Show Monitor (the one item the default menu gained).
    assert _shown(m) == [i18n.SETTINGS_MENU_ITEM, i18n.SHOW_MONITOR_MENU_ITEM, i18n.CHECK_CONN_MENU_ITEM, "-",
                         i18n.RESET_POSITION_MENU_ITEM, "-",
                         i18n.CHECK_FOR_UPDATES_MENU_ITEM, i18n.SUPPORT_MENU_ITEM, "-",
                         i18n.EXIT_MENU_ITEM]


def test_hidden_items_are_omitted(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {"context_menu_hidden_items": ["monitor", "support"]})
    shown = _shown(m)
    assert i18n.SHOW_MONITOR_MENU_ITEM not in shown and i18n.SUPPORT_MENU_ITEM not in shown
    assert i18n.CHECK_FOR_UPDATES_MENU_ITEM in shown and i18n.RESET_POSITION_MENU_ITEM in shown


def test_settings_and_exit_always_show(qt_owned, i18n):
    """Even a hand-edited config that names them (plus every optional item) leaves Settings and Exit."""
    m = _menu(qt_owned, i18n, {"context_menu_hidden_items": ["settings", "exit"] + HIDEABLE})
    assert _shown(m) == [i18n.SETTINGS_MENU_ITEM, "-", i18n.EXIT_MENU_ITEM]


@pytest.mark.parametrize("bad", ["monitor", None, 7, {"monitor": True}, [None, 3]])
def test_bad_value_hides_nothing(qt_owned, i18n, bad):
    m = _menu(qt_owned, i18n, {"context_menu_hidden_items": bad})
    assert i18n.SHOW_MONITOR_MENU_ITEM in _shown(m) and i18n.SUPPORT_MENU_ITEM in _shown(m)


def test_reset_position_hidden_even_when_moved(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {"context_menu_hidden_items": ["reset_position"]}, moved=True)
    assert not m.reset_position_action.isVisible()


def test_items_come_back_when_unhidden(qt_owned, i18n):
    """The menu is built once and refreshed on every open: un-hiding must re-show (separators too)."""
    m = _menu(qt_owned, i18n, {"context_menu_hidden_items": HIDEABLE})
    m.widget.config = {"context_menu_hidden_items": []}
    with patch("netspeedtray.core.update_checker.update_check_disabled_by_policy", return_value=False):
        m._refresh_dynamic_items()
    assert _shown(m) == _shown(_menu(qt_owned, i18n, {}))


_SUBSETS = [list(c) for n in range(len(HIDEABLE) + 1) for c in itertools.combinations(HIDEABLE, n)]


@pytest.mark.parametrize("hidden", _SUBSETS, ids=lambda h: "+".join(h) or "none")
@pytest.mark.parametrize("pause", [False, True])
@pytest.mark.parametrize("moved", [False, True])
def test_separators_never_lead_trail_or_double(qt_owned, i18n, hidden, pause, moved):
    m = _menu(qt_owned, i18n, {"context_menu_hidden_items": hidden, "pause_in_menu": pause}, moved=moved)
    shown = _shown(m)
    assert shown[0] == i18n.SETTINGS_MENU_ITEM and shown[-1] == i18n.EXIT_MENU_ITEM
    assert all(not (a == "-" and b == "-") for a, b in zip(shown, shown[1:])), shown
    # And each group that has an item keeps its divider: the count is (non-empty groups - 1).
    groups = [[i18n.SETTINGS_MENU_ITEM, i18n.SHOW_MONITOR_MENU_ITEM],
              [i18n.PAUSE_MENU_ITEM, i18n.RESET_POSITION_MENU_ITEM],
              [i18n.CHECK_FOR_UPDATES_MENU_ITEM, i18n.SUPPORT_MENU_ITEM],
              [i18n.EXIT_MENU_ITEM]]
    non_empty = sum(1 for g in groups if any(t in shown for t in g))
    assert shown.count("-") == non_empty - 1, shown


def test_separators_still_collapse_when_a_refresh_step_fails(qt_owned, i18n):
    """A step of _refresh_dynamic_items that raises (here the update-policy read) must not leave the
    separators of the previous open: the Pause/Reset group emptied since then would show a doubled
    divider."""
    config = {"context_menu_hidden_items": [], "pause_in_menu": True}
    m = _menu(qt_owned, i18n, config, moved=True)          # every group has items
    config["pause_in_menu"] = False                         # the Pause/Reset group is now empty...
    m.widget.has_custom_position = lambda: False
    with patch("netspeedtray.core.update_checker.update_check_disabled_by_policy",
               side_effect=OSError("policy read failed")):
        m._refresh_dynamic_items()
    shown = _shown(m)
    assert all(not (a == "-" and b == "-") for a, b in zip(shown, shown[1:])), shown
    assert shown[0] != "-" and shown[-1] != "-", shown


def test_accelerators_kept_on_shown_items(qt_owned, i18n):
    m = _menu(qt_owned, i18n, {"context_menu_hidden_items": ["monitor"]})
    assert m.update_action.text() == i18n.CHECK_FOR_UPDATES_MENU_ITEM and "&" in m.update_action.text()


# ------------------------------------------------------------------------------------------ schema

def _cm():
    return ConfigManager(config_path=Path(".") / "NetSpeedTray_Config.json")


def test_schema_default_is_nothing_hidden():
    assert D.DEFAULT_CONFIG["context_menu_hidden_items"] == []
    assert _cm()._validate_config({})["context_menu_hidden_items"] == []


def test_schema_drops_unknown_ids_one_by_one():
    out = _cm()._validate_config({"context_menu_hidden_items":
                                  ["support", "settings", "bogus", 3, None, "support", "exit", "monitor"]})
    assert out["context_menu_hidden_items"] == ["support", "monitor"]


@pytest.mark.parametrize("bad", ["monitor", None, 5, {"a": 1}])
def test_schema_resets_a_non_list(bad):
    assert _cm()._validate_config({"context_menu_hidden_items": bad})["context_menu_hidden_items"] == []


def test_schema_keeps_a_valid_list():
    assert _cm()._validate_config({"context_menu_hidden_items": HIDEABLE})["context_menu_hidden_items"] == HIDEABLE


# ---------------------------------------------------------------------------------------- settings

@pytest.mark.parametrize("label, plain", [("Check for &Updates", "Check for Updates"),
                                          ("E&xit", "Exit"), ("Show Monitor", "Show Monitor"),
                                          ("設定(&S)", "設定"), ("সেটিংস (&S)", "সেটিংস"),
                                          ("Tom && Jerry", "Tom & Jerry")])
def test_strip_mnemonic(label, plain):
    assert strip_mnemonic(label) == plain


@pytest.fixture
def page(qt_owned, i18n):
    return qt_owned(GeneralPage(i18n, MagicMock()))


def test_settings_lists_only_the_optional_items(page, i18n):
    assert list(page.context_menu_toggles) == HIDEABLE
    assert all(t.isChecked() for t in page.context_menu_toggles.values())


def test_settings_default_round_trip(page):
    page.load_settings({"language": None, "update_rate": 1.0}, is_startup_enabled=False)
    assert page.get_settings()["context_menu_hidden_items"] == []


def test_settings_round_trip(page):
    page.load_settings({"context_menu_hidden_items": ["support", "reset_position"]}, is_startup_enabled=False)
    assert not page.context_menu_toggles["support"].isChecked()
    assert page.context_menu_toggles["monitor"].isChecked()
    # Saved in CONTEXT_MENU_HIDEABLE_ITEMS order, so an untouched page writes back an equal list.
    assert page.get_settings()["context_menu_hidden_items"] == ["reset_position", "support"]
    page.context_menu_toggles["support"].setChecked(True)
    page.context_menu_toggles["monitor"].setChecked(False)
    assert page.get_settings()["context_menu_hidden_items"] == ["monitor", "reset_position"]


def test_settings_bad_value_loads_as_all_shown(page):
    page.load_settings({"context_menu_hidden_items": "monitor"}, is_startup_enabled=False)
    assert all(t.isChecked() for t in page.context_menu_toggles.values())


def test_settings_section_names_settings_and_exit(page, i18n):
    from PyQt6.QtWidgets import QLabel
    texts = [l.text() for l in page.context_menu_items.findChildren(QLabel)]
    assert i18n.CONTEXT_MENU_ITEMS_LABEL in texts
    assert any("Settings" in t and "Exit" in t and "&" not in t for t in texts), texts
    assert "Check for updates" in texts and "Support this project" in texts   # sentence case (2.2.2 D7)


# ------------------------------------------------- Pause / Resume row (folded into the list, 2.2.1)

def test_pause_row_sits_in_the_list_after_show_monitor(page, i18n):
    """The standalone 'Add Pause/Resume to the right-click menu' card is gone: Pause / Resume is a row
    of the expander, in menu order (after Show Monitor), titled with CLICK_ACTION_PAUSE_LABEL."""
    from netspeedtray.utils.components import SettingCard
    assert page.context_menu_items.isAncestorOf(page.pause_in_menu)
    rows = [c._title_label.text() for c in page.context_menu_items.findChildren(SettingCard)]
    assert rows == ["Show Monitor", "Check connection", i18n.CLICK_ACTION_PAUSE_LABEL, "Reset position",
                    "Check for updates", "Support this project"]
    assert "pause" not in page.context_menu_toggles   # its own key, not a hidden-items id
    titles = [c._title_label.text() for c in page.findChildren(SettingCard)]
    assert "Add Pause/Resume to the right-click menu" not in titles


@pytest.mark.parametrize("on", [False, True])
def test_pause_row_round_trips_pause_in_menu(page, on):
    page.load_settings({"pause_in_menu": on}, is_startup_enabled=False)
    assert page.pause_in_menu.isChecked() is on
    out = page.get_settings()
    assert out["pause_in_menu"] is on and out["context_menu_hidden_items"] == []
    page.pause_in_menu.setChecked(not on)
    assert page.get_settings()["pause_in_menu"] is (not on)


def test_pause_row_defaults_off(page):
    page.load_settings({}, is_startup_enabled=False)
    assert page.pause_in_menu.isChecked() is False and D.DEFAULT_CONFIG["pause_in_menu"] is False


@pytest.fixture
def dialog(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from netspeedtray.views.settings.dialog import SettingsDialog
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    parent = MagicMock()
    parent.config = {}
    parent.config_manager = MagicMock()
    dlg = SettingsDialog(main_widget=parent, config=dict(D.DEFAULT_CONFIG), version="0",
                         i18n=I18nStrings("en_US"), available_interfaces=["Ethernet", "Wi-Fi"],
                         is_startup_enabled=False)
    qtbot.addWidget(dlg)
    return dlg, parent


def _applied(parent):
    args, kwargs = parent.handle_settings_changed.call_args
    return args[0], kwargs.get("save_to_disk", args[1] if len(args) > 1 else None)


def test_dialog_save_applies_pause_and_hidden_items(qtbot, dialog):
    from PyQt6.QtCore import Qt
    dlg, parent = dialog
    dlg.general_page.pause_in_menu.setChecked(True)
    dlg.general_page.context_menu_toggles["support"].setChecked(False)
    qtbot.mouseClick(dlg.save_button, Qt.MouseButton.LeftButton)
    cfg, to_disk = _applied(parent)
    assert to_disk is True
    assert cfg["pause_in_menu"] is True and cfg["context_menu_hidden_items"] == ["support"]


def test_dialog_cancel_reverts_pause_and_hidden_items(qtbot, dialog):
    from PyQt6.QtCore import Qt
    dlg, parent = dialog
    dlg.general_page.pause_in_menu.setChecked(True)
    dlg.general_page.context_menu_toggles["monitor"].setChecked(False)
    qtbot.mouseClick(dlg.cancel_button, Qt.MouseButton.LeftButton)
    cfg, to_disk = _applied(parent)
    assert to_disk is False
    assert cfg["pause_in_menu"] is False and cfg["context_menu_hidden_items"] == []
