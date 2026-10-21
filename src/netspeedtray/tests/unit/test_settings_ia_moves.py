"""
Settings IA pass (2.2, docs: settings-ia-review-2026-10.md, changes 1-6) - moves only.

Pins where each moved control now lives, that it still saves to the SAME config key, the exact scope
of the Advanced page's "Reset this page" (the only per-page reset; change 3 shrank it), that
conditional reveals still work after the move, and the new section order / keyboard focus order on
every changed page. test_settings_reshuffle.py remains the structure-agnostic key-coverage net.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.components import SettingCard, SettingExpander
from netspeedtray.views.settings.dialog import SettingsDialog

DEFAULTS = constants.config.defaults.DEFAULT_CONFIG

# Keys owned by the Mini Graph section, now on the Widget page (was Appearance).
GRAPH_KEYS = {"graph_enabled", "history_minutes", "graph_opacity", "graph_fixed_scale",
              "graph_scale_headroom", "graph_show_ceiling", "graph_split_directions", "graph_style"}


@pytest.fixture
def dlg(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    m = MagicMock(); m.config = {}; m.config_manager = MagicMock()
    d = SettingsDialog(main_widget=m, config=dict(DEFAULTS), version="2.2.0", i18n=I18nStrings("en_US"),
                       available_interfaces=["Ethernet"], is_startup_enabled=False)
    qtbot.addWidget(d)
    return d


def _page(dlg, index: int) -> QWidget:
    return dlg.stack.widget(index).widget()


def _flatten(layout):
    """Every widget in a page's layout, in visual (top-to-bottom) order. Descends into embedded
    section widgets, but not into cards / expanders (their inner labels aren't page structure)."""
    out = []
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if w is None:
            continue
        out.append(w)
        if not isinstance(w, (SettingCard, SettingExpander)) and w.layout() is not None:
            out.extend(_flatten(w.layout()))
    return out


def _headers(page, candidates):
    return [w.text() for w in _flatten(page.layout()) if isinstance(w, QLabel) and w.text() in candidates]


def _card_of(control):
    w = control
    while w is not None and not isinstance(w, SettingCard):
        w = w.parentWidget()
    return w


def _focus_positions(dlg, widgets):
    """Each widget's index in the dialog's focus chain (the Tab order)."""
    chain, w = [], dlg.nextInFocusChain()
    while w is not dlg and len(chain) < 5000:
        chain.append(w)
        w = w.nextInFocusChain()
    return [chain.index(x) for x in widgets]


# --- change 1: Appearance order ---------------------------------------------------------------------

def test_appearance_section_order(dlg):
    i = dlg.i18n
    order = [i.FONT_SETTINGS_GROUP_TITLE, i.DISPLAY_FORMAT_GROUP, i.INTERFACE_LAYOUT_GROUP,
             i.COLOR_CODING_GROUP, i.BACKGROUND_SETTINGS_GROUP_TITLE, i.ARROW_STYLING_GROUP]
    assert _headers(_page(dlg, 2), set(order) | {i.MINI_GRAPH_SECTION_TITLE}) == order


def test_appearance_tab_order_follows_the_new_section_order(dlg):
    a = dlg.appearance_page
    pos = _focus_positions(dlg, [a.font_family_button, a.units_section.unit_type,
                                 a.units_section.hide_arrows, a.colors_section.enable_colors,
                                 a.background_color_button, a.use_separate_arrow_font,
                                 a.use_custom_arrow_colors])
    assert pos == sorted(pos)


# --- change 2: Mini Graph -> Widget (last) ----------------------------------------------------------

def test_mini_graph_is_the_last_section_on_the_widget_page(dlg):
    i = dlg.i18n
    order = [i.WIDGET_DISPLAY_MODE_LABEL, i.WIDGET_DISPLAY_ORDER_LABEL, i.BEHAVIOR_GROUP_TITLE,
             i.MINI_GRAPH_SECTION_TITLE]
    assert _headers(_page(dlg, 1), set(order)) == order
    top = [w for w in (dlg.widget_page.layout().itemAt(k).widget()
                       for k in range(dlg.widget_page.layout().count())) if w is not None]
    assert top[-1] is dlg.widget_page.mini_graph_section   # nothing below it to shift
    assert not dlg.appearance_page.findChildren(type(dlg.widget_page.mini_graph_section))


def test_hardware_graph_note_moved_under_the_graph_controls(dlg):
    flat = _flatten(dlg.widget_page.layout())
    notes = [w for w in flat if isinstance(w, QLabel) and w.text() == dlg.i18n.HARDWARE_GRAPH_NOTE]
    assert len(notes) == 1
    note = notes[0]
    g = dlg.widget_page.mini_graph_section
    assert g.isAncestorOf(note)
    assert flat.index(_card_of(g.graph_opacity)) < flat.index(note) < flat.index(g._split_card)
    # The slot under Display Mode holds only its one note (side bar #322 / hover card), then Display Order.
    after = flat.index(dlg.widget_page.display_mode_combo)
    assert flat[after + 1] is dlg.widget_page.display_mode_note
    assert flat[after + 2].text() == dlg.i18n.WIDGET_DISPLAY_ORDER_LABEL


@pytest.mark.parametrize("attr,key,value", [
    ("enable_graph", "graph_enabled", False),
    ("history_duration", "history_minutes", 7),
    ("graph_opacity", "graph_opacity", 42),
    ("graph_split", "graph_split_directions", True),
    ("graph_fixed_scale", "graph_fixed_scale", True),
    ("graph_show_ceiling", "graph_show_ceiling", False),
])
def test_graph_controls_live_on_widget_page_and_save_to_their_keys(dlg, attr, key, value):
    g = dlg.widget_page.mini_graph_section
    assert _page(dlg, 1).isAncestorOf(g) and not _page(dlg, 2).isAncestorOf(g)
    control = getattr(g, attr)
    if isinstance(value, bool):
        control.setChecked(value)
    else:
        control.setValue(value)
    assert dlg.get_settings()[key] == value
    assert dlg.widget_page.get_settings()[key] == value


def test_graph_headroom_saves_to_its_key(dlg):
    g = dlg.widget_page.mini_graph_section
    g.graph_scale_headroom.setCurrentIndex(g.graph_scale_headroom.findData(30))
    assert dlg.get_settings()["graph_scale_headroom"] == 30


def test_graph_keys_are_owned_by_widget_page_only(dlg):
    assert GRAPH_KEYS <= set(dlg.widget_page.get_settings())
    assert not GRAPH_KEYS & set(dlg.appearance_page.get_settings())


def test_graph_keys_load_into_the_widget_page(dlg):
    cfg = dict(DEFAULTS, graph_enabled=False, history_minutes=9, graph_opacity=33, graph_fixed_scale=True,
               graph_scale_headroom=10, graph_show_ceiling=False, graph_split_directions=True,
               graph_style="trend")
    dlg.reset_with_config(cfg, False)
    got = dlg.get_settings()
    assert {k: got[k] for k in GRAPH_KEYS} == {k: cfg[k] for k in GRAPH_KEYS}


def test_graph_reveals_still_work_after_the_move(dlg):
    """Fixed scale reveals headroom + plan line (and resizes via the Widget page's layout_changed); the
    split toggle greys out while the graph is off."""
    g = dlg.widget_page.mini_graph_section
    fired = []
    dlg.widget_page.layout_changed.connect(lambda: fired.append(1))
    g.graph_fixed_scale.setChecked(False)
    assert g.graph_scale_container.isVisibleTo(g) is False
    g.graph_fixed_scale.setChecked(True)
    assert g.graph_scale_container.isVisibleTo(g) is True
    assert fired
    g.enable_graph.setChecked(False)
    assert g._split_card.isEnabled() is False
    g.enable_graph.setChecked(True)
    assert g._split_card.isEnabled() is True


def test_graph_change_updates_the_live_preview(dlg, qtbot):
    seen = []
    dlg.preview_widget.set_config = lambda cfg: seen.append(cfg)
    g = dlg.widget_page.mini_graph_section
    g.graph_opacity.setValue(55)
    qtbot.waitUntil(lambda: bool(seen), timeout=2000)
    assert seen[-1]["graph_opacity"] == 55


def test_graph_style_change_updates_the_live_preview(dlg, qtbot):
    seen = []
    dlg.preview_widget.set_config = lambda cfg: seen.append(cfg)
    g = dlg.widget_page.mini_graph_section
    g.graph_style.setCurrentIndex(g.graph_style.findData("trend"))
    qtbot.waitUntil(lambda: bool(seen) and seen[-1].get("graph_style") == "trend", timeout=2000)
    assert dlg.get_settings()["graph_style"] == "trend"


# --- change 4: Preferred Monitor -> Widget > Behavior ----------------------------------------------

def test_preferred_monitor_is_on_widget_page_above_free_float(dlg):
    w = dlg.widget_page
    assert not hasattr(dlg.general_page, "preferred_monitor_combo")
    assert "preferred_monitor" not in dlg.general_page.get_settings()
    assert _page(dlg, 1).isAncestorOf(w.preferred_monitor_combo)
    flat = _flatten(w.layout())
    cards = [flat.index(_card_of(c)) for c in (w.preferred_monitor_combo, w.free_move, w.free_float,
                                               w.keep_visible_fullscreen)]
    assert cards == sorted(cards)
    assert flat.index(next(x for x in flat if isinstance(x, QLabel)
                           and x.text() == dlg.i18n.BEHAVIOR_GROUP_TITLE)) == cards[0] - 1


def test_preferred_monitor_saves_and_loads_the_same_key(dlg):
    from PyQt6.QtGui import QGuiApplication
    name = QGuiApplication.primaryScreen().name()
    combo = dlg.widget_page.preferred_monitor_combo
    combo.setCurrentIndex(combo.findData(name))
    assert dlg.get_settings()["preferred_monitor"] == name
    dlg.reset_with_config(dict(DEFAULTS, preferred_monitor=name), False)
    assert dlg.get_settings()["preferred_monitor"] == name
    dlg.reset_with_config(dict(DEFAULTS, preferred_monitor="NO-SUCH-DISPLAY"), False)
    assert dlg.get_settings()["preferred_monitor"] is None   # unknown display -> "Primary (auto)"


def test_preferred_monitor_list_refreshes_when_the_widget_page_shows(dlg, monkeypatch):
    calls = []
    monkeypatch.setattr(dlg.widget_page, "_populate_monitor_combo", lambda: calls.append(1))
    QApplication.sendEvent(dlg.widget_page, QShowEvent())   # no real window is shown
    assert calls


def test_monitor_refresh_on_show_does_not_live_apply(dlg):
    """Review: the combo was connected to on_change twice (construction + the refresh's reconnect), so the
    refresh's single disconnect left one live and every visit to the page re-applied all settings."""
    combo = dlg.widget_page.preferred_monitor_combo
    assert combo.receivers(combo.currentIndexChanged) == 1
    emitted = []
    dlg.settings_changed.connect(lambda *_: emitted.append(1))
    QApplication.sendEvent(dlg.widget_page, QShowEvent())
    assert combo.receivers(combo.currentIndexChanged) == 1
    assert not emitted


def test_widget_page_tab_order(dlg):
    w, g = dlg.widget_page, dlg.widget_page.mini_graph_section
    pos = _focus_positions(dlg, [w.display_mode_combo, w.pos_combos[2], w.preferred_monitor_combo,
                                 w.free_move, w.free_float, w.keep_visible_fullscreen,
                                 g.enable_graph, g.history_duration, g.graph_split, g.graph_fixed_scale,
                                 g.graph_scale_headroom])
    assert pos == sorted(pos)


# --- change 5: Hardware "Appearance" header + throttle temp next to temps ---------------------------

def test_hardware_appearance_header_and_throttle_position(dlg):
    h = dlg.hardware_page
    flat = _flatten(h.layout())
    note = next(x for x in flat if isinstance(x, QLabel) and x.text() == dlg.i18n.HARDWARE_TEMPS_LIMITATION_NOTE)
    header = [x for x in flat if isinstance(x, QLabel) and x.text() == dlg.i18n.APPEARANCE_SETTINGS_GROUP]
    assert len(header) == 1
    throttle = flat.index(_card_of(h.throttle_temp))
    assert flat.index(_card_of(h.show_temps)) < flat.index(note) < throttle
    assert throttle + 1 == flat.index(header[0])
    after = [flat.index(_card_of(h.label_style)), flat.index(_card_of(h.label_color)),
             flat.index(h.label_colors_container),
             flat.index(next(x for x in flat if isinstance(x, SettingExpander)))]
    assert flat.index(header[0]) < min(after) and after == sorted(after)


def test_hardware_throttle_still_saves_to_its_key(dlg):
    dlg.hardware_page.throttle_temp.setValue(88)
    assert dlg.get_settings()["throttle_temp_c"] == 88


def test_hardware_tab_order(dlg):
    h = dlg.hardware_page
    pos = _focus_positions(dlg, [h.show_power, h.throttle_temp, h.label_style, h.label_color,
                                 h.cpu_label_color_button, h.cpu_load_high])
    assert pos == sorted(pos)


# --- change 6: Network order + plan speed collapsed --------------------------------------------------

def test_network_section_order_and_plan_collapsed(dlg):
    n = dlg.interfaces_page
    top = [n.layout().itemAt(k).widget() for k in range(n.layout().count())]
    assert top.index(n.datacap) < top.index(n.connection) < top.index(n.identity)
    assert n.connection._plan.isExpanded() is False
    pos = _focus_positions(dlg, [n.auto_interface_radio, n.datacap, n.connection, n.identity])
    assert pos == sorted(pos)


# --- change 3: hover / pause toggles -> General > Interaction ------------------------------------------

INTERACTION_KEYS = {"pause_in_menu": True, "show_usage_on_hover": False, "show_hover_tips": False}  # non-defaults
INTERACTION_ATTRS = {"pause_in_menu": "pause_in_menu", "show_usage_on_hover": "show_usage_hover",
                     "show_hover_tips": "show_hover_tips"}


def test_interaction_toggles_left_advanced(dlg):
    assert not set(INTERACTION_KEYS) & set(dlg.advanced_page.get_settings())
    for attr in INTERACTION_ATTRS.values():
        assert not hasattr(dlg.advanced_page, attr)
    labels = {dlg.i18n.HOVER_USAGE_CARD_LABEL, dlg.i18n.HOVER_TIPS_LABEL, dlg.i18n.PAUSE_IN_MENU_LABEL}
    assert not [c for c in dlg.advanced_page.findChildren(SettingCard) if c._title_label.text() in labels]


@pytest.mark.parametrize("key", sorted(INTERACTION_KEYS))
def test_interaction_toggles_live_on_general_and_save_to_their_keys(dlg, key):
    control = getattr(dlg.general_page, INTERACTION_ATTRS[key])
    assert _page(dlg, 0).isAncestorOf(control) and not _page(dlg, 5).isAncestorOf(control)
    control.setChecked(INTERACTION_KEYS[key])
    assert dlg.general_page.get_settings()[key] is INTERACTION_KEYS[key]
    assert dlg.get_settings()[key] is INTERACTION_KEYS[key]
    control.setChecked(not INTERACTION_KEYS[key])
    assert dlg.get_settings()[key] is (not INTERACTION_KEYS[key])


def test_interaction_toggles_load_from_config_and_keep_their_defaults(dlg):
    dlg.reset_with_config(dict(DEFAULTS, **INTERACTION_KEYS), False)
    got = dlg.get_settings()
    assert {k: got[k] for k in INTERACTION_KEYS} == INTERACTION_KEYS
    # Absent keys fall back to the same defaults Advanced used (pause off, hover usage + tips on).
    dlg.general_page.load_settings({"language": None, "update_rate": 1.0}, is_startup_enabled=False)
    s = dlg.general_page.get_settings()
    assert (s["pause_in_menu"], s["show_usage_on_hover"], s["show_hover_tips"]) == (False, True, True)
    assert {k: DEFAULTS[k] for k in INTERACTION_KEYS} == {"pause_in_menu": False, "show_usage_on_hover": True,
                                                          "show_hover_tips": True}


def test_interaction_order_on_general(dlg):
    """Click actions, then Pause-in-menu (the right-click route to the same Pause / Resume the click
    lists offer), then the hover card's usage + tips toggles - all under the Interaction header, last on
    the page."""
    g, i = dlg.general_page, dlg.i18n
    flat = _flatten(g.layout())
    header = flat.index(next(x for x in flat if isinstance(x, QLabel) and x.text() == i.INTERACTION_GROUP_TITLE))
    cards = [flat.index(_card_of(c)) for c in (g.double_click_action, g.middle_click_action, g.pause_in_menu,
                                               g.show_usage_hover, g.show_hover_tips)]
    assert cards == list(range(header + 1, header + 6))
    assert cards[-1] == len(flat) - 1
    assert _headers(_page(dlg, 0), {i.UPDATE_RATE_GROUP_TITLE, i.BEHAVIOR_GROUP_TITLE, i.INTERACTION_GROUP_TITLE})         == [i.UPDATE_RATE_GROUP_TITLE, i.BEHAVIOR_GROUP_TITLE, i.INTERACTION_GROUP_TITLE]


def test_general_tab_order(dlg):
    g = dlg.general_page
    pos = _focus_positions(dlg, [g.language_combo, g.start_with_windows, g.check_for_updates,
                                 g.double_click_action, g.middle_click_action, g.pause_in_menu,
                                 g.show_usage_hover, g.show_hover_tips])
    assert pos == sorted(pos)


def test_advanced_keeps_its_low_frequency_sections(dlg):
    """Advanced > Behavior keeps reduce-motion + keep-on-top (two cards, so no fold is needed)."""
    a, i = dlg.advanced_page, dlg.i18n
    flat = _flatten(a.layout())
    order = [i.ADVANCED_DATA_GROUP, i.BEHAVIOR_GROUP_TITLE, i.INTEGRATIONS_GROUP, i.ADVANCED_RESET_GROUP]
    assert _headers(_page(dlg, 5), set(order)) == order
    beh = flat.index(next(x for x in flat if isinstance(x, QLabel) and x.text() == i.BEHAVIOR_GROUP_TITLE))
    assert [flat.index(_card_of(a.reduce_motion)), flat.index(_card_of(a.keep_windows_on_top))] == [beh + 1, beh + 2]
    pos = _focus_positions(dlg, [a.keep_data, a.record_hardware, a.delete_history_btn, a.reduce_motion,
                                 a.keep_windows_on_top, a.metrics_enabled, a.reset_page_btn, a.reset_all_btn])
    assert pos == sorted(pos)


def test_interaction_toggle_live_applies(dlg, qtbot):
    emitted = []
    dlg.settings_changed.connect(lambda cfg: emitted.append(cfg))
    dlg.general_page.pause_in_menu.setChecked(True)
    qtbot.waitUntil(lambda: bool(emitted) and emitted[-1].get("pause_in_menu") is True, timeout=2000)


# --- Reset scope ---------------------------------------------------------------------------------------

# "Reset this page" exists only on Advanced. Change 3 moved pause_in_menu / show_usage_on_hover /
# show_hover_tips off Advanced, so its scope shrank to exactly these keys; the moved keys (from every
# change in the pass) must be untouched by it, and "Reset all" must still reset them.
ADVANCED_RESET_KEYS = {"keep_data", "keep_windows_on_top", "metrics_endpoint_enabled", "metrics_endpoint_port",
                       "record_hardware_history", "reduce_motion"}
MOVED_NON_DEFAULTS = {"graph_enabled": False, "graph_opacity": 33, "graph_split_directions": True,
                      "graph_fixed_scale": True, "throttle_temp_c": 85, "plan_down_mbps": 300,
                      **INTERACTION_KEYS}


def test_reset_this_page_scope_and_skips_moved_keys(dlg):
    assert set(dlg.advanced_page.get_settings()) == ADVANCED_RESET_KEYS
    dlg.reset_with_config(dict(DEFAULTS, **MOVED_NON_DEFAULTS, reduce_motion=True), False)
    dlg._reset_advanced_page()
    got = dlg.get_settings()
    assert got["reduce_motion"] == DEFAULTS["reduce_motion"]
    assert {k: got[k] for k in MOVED_NON_DEFAULTS} == MOVED_NON_DEFAULTS


def test_reset_all_still_resets_the_moved_keys(dlg, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg.reset_with_config(dict(DEFAULTS, **MOVED_NON_DEFAULTS), False)
    dlg._reset_all_to_defaults()
    got = dlg.get_settings()
    assert {k: got[k] for k in MOVED_NON_DEFAULTS} == {k: DEFAULTS[k] for k in MOVED_NON_DEFAULTS}


# --- deep links still land on the right pages -----------------------------------------------------

def test_deep_links_unchanged(dlg):
    dlg.navigate_to_page(SettingsDialog.PAGE_HARDWARE)
    assert dlg.stack.currentWidget().widget() is dlg.hardware_page
    dlg.navigate_to_page(SettingsDialog.PAGE_NETWORK)
    assert dlg.stack.currentWidget().widget() is dlg.interfaces_page
