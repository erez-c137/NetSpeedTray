"""
2.2 release-candidate checkup - Settings dialog fixes (C1, S1-S5, S8).

Each test reproduces a reviewer harness finding (rc-check-settings/harness/{config,settings}) and
fails without its fix.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.config import ConfigManager
from netspeedtray.views.settings.dialog import SettingsDialog

DEFAULTS = constants.config.defaults.DEFAULT_CONFIG
# Keys a Save legitimately rewrites (window position, the startup mirror).
_VOLATILE = {"settings_window_pos", "start_with_windows"}


class _Main:
    """The parts of NetworkSpeedWidget the dialog touches, with ConfigController's merge semantics."""

    def __init__(self, cfg):
        self.config = dict(cfg)
        self.config_manager = MagicMock()
        self.saved = []
        self.calls = []
        self.metrics_endpoint = None

    def handle_settings_changed(self, updated, save_to_disk=True):
        self.calls.append((dict(updated), save_to_disk))
        self.config.update(updated)
        if save_to_disk:
            self.saved.append(dict(self.config))

    def toggle_startup(self, on):
        pass

    def update(self):
        pass

    def width(self):
        return 200


@pytest.fixture(autouse=True)
def _quiet_boxes(monkeypatch):
    for n in ("critical", "information", "warning"):
        monkeypatch.setattr(QMessageBox, n, lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)


def _make(qtbot, cfg=None, ifaces=("Ethernet", "Wi-Fi")):
    cfg = dict(DEFAULTS if cfg is None else cfg)
    main = _Main(cfg)
    dlg = SettingsDialog(main_widget=main, config=cfg, version="2.2.0", i18n=I18nStrings("en_US"),
                         available_interfaces=list(ifaces), is_startup_enabled=False)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dlg.settings_changed.connect(lambda c: main.handle_settings_changed(c, save_to_disk=False))
    qtbot.addWidget(dlg)
    return dlg, main


def _flush(dlg):
    if dlg._update_timer.isActive():
        dlg._update_timer.stop()
    dlg._emit_settings_changed_throttled()


def _changed(before, after):
    return sorted(k for k in before if k not in _VOLATILE and before.get(k) != after.get(k))


# --- C1: a decimal graph_opacity must not blank out the rest of the dialog --------------------------

@pytest.mark.parametrize("opacity", [55.0, 10.5])
def test_float_graph_opacity_loads_and_a_noop_save_changes_nothing(qtbot, opacity):
    cfg = dict(DEFAULTS, graph_opacity=opacity, keep_data=365, font_family="Consolas",
               record_hardware_history=True)
    dlg, _ = _make(qtbot, cfg)
    assert dlg._failed_pages == set()
    got = dlg.get_settings()
    others = [k for k in _changed(cfg, got) if k != "graph_opacity"]
    assert others == [], f"a no-op Save would rewrite {others}"
    assert got["graph_opacity"] == round(opacity)


def test_a_page_that_fails_to_load_never_writes_its_keys(qtbot, monkeypatch):
    """Whatever the next load bug is: the failed page's keys keep their loaded values on Save,
    and the pages after it still load."""
    from netspeedtray.views.settings.pages.hardware import HardwarePage

    def boom(self, config):
        raise TypeError("simulated load failure")
    monkeypatch.setattr(HardwarePage, "load_settings", boom)
    cfg = dict(DEFAULTS, monitor_cpu_enabled=True, cpu_load_high_threshold=91,
               keep_data=365, metrics_endpoint_port=9555)
    dlg, main = _make(qtbot, cfg)
    assert dlg._failed_pages == {"hardware"}
    got = dlg.get_settings()
    assert _changed(cfg, got) == []
    # The page after Hardware (Advanced) still loaded its real values.
    assert dlg.advanced_page.get_settings()["metrics_endpoint_port"] == 9555
    dlg.save_button.click()
    assert main.saved and main.saved[-1]["cpu_load_high_threshold"] == 91
    assert main.saved[-1]["monitor_cpu_enabled"] is True


def test_config_validation_stores_graph_opacity_as_int(tmp_path):
    cm = ConfigManager(tmp_path / "c.json")
    v = cm._validate_config({"graph_opacity": 55.6})
    assert v["graph_opacity"] == 56 and isinstance(v["graph_opacity"], int)
    assert cm._validate_config({"graph_opacity": 40})["graph_opacity"] == 40


# --- S1: Esc reverts the live preview like Cancel ---------------------------------------------------

def test_escape_reverts_the_live_preview(qtbot):
    from PyQt6.QtTest import QTest
    dlg, main = _make(qtbot)
    dlg.show()
    dlg.appearance_page.bg_opacity.setValue(77)
    _flush(dlg)
    assert main.config["background_opacity"] == 77
    QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert not dlg.isVisible()
    assert main.config["background_opacity"] == DEFAULTS["background_opacity"]


# --- S2: re-opening Settings while it is open keeps the unsaved adapter selection ------------------

def test_reopen_while_open_keeps_selected_interfaces(qtbot):
    cfg = dict(DEFAULTS, interface_mode="selected", selected_interfaces=["Wi-Fi"])
    dlg, _ = _make(qtbot, cfg, ifaces=("Ethernet", "Wi-Fi", "vEthernet (WSL)"))
    dlg.interfaces_page.interface_checkboxes["Ethernet"].setChecked(True)   # unsaved edit
    dlg.update_interface_list(["Ethernet", "Wi-Fi", "vEthernet (WSL)"], "Wi-Fi")   # tray click again
    assert sorted(dlg.get_settings()["selected_interfaces"]) == ["Ethernet", "Wi-Fi"]


# --- S3: a text colour typed as hex is user-chosen ---------------------------------------------------

def test_typed_default_color_is_marked_user_chosen(qtbot):
    from PyQt6.QtTest import QTest
    dlg, _ = _make(qtbot)
    field = dlg.appearance_page.default_color_input
    field.selectAll()
    QTest.keyClicks(field, "#FF8800")   # textEdited, like a real user
    s = dlg.get_settings()
    assert s["default_color"].upper() == "#FF8800"
    assert s["color_is_automatic"] is False


def test_loading_a_colour_does_not_mark_it_user_chosen(qtbot):
    dlg, _ = _make(qtbot, dict(DEFAULTS, default_color="#123456", color_is_automatic=True))
    assert dlg.get_settings()["color_is_automatic"] is True


# --- S4: keyboard page switching ----------------------------------------------------------------------

def test_sidebar_takes_focus_and_ctrl_tab_switches_pages(qtbot):
    # Window shortcuts need an ACTIVE window, which the offscreen test platform never gives; the
    # real key path is exercised by the rc harness (h5_focus) on the windows platform. Here: the
    # bindings exist on the dialog and each one steps the page the right way.
    from PyQt6.QtGui import QKeySequence, QShortcut
    dlg, _ = _make(qtbot)
    assert dlg.sidebar.focusPolicy() & Qt.FocusPolicy.TabFocus
    by_key = {sc.key().toString(): sc for sc in dlg.findChildren(QShortcut)}

    def press(seq):
        by_key[QKeySequence(seq).toString()].activated.emit()

    dlg.navigate_to_page(0)
    press("Ctrl+Tab")
    assert dlg.sidebar.currentRow() == 1
    press("Ctrl+PgDown")
    assert dlg.sidebar.currentRow() == 2
    press("Ctrl+PgUp")
    assert dlg.sidebar.currentRow() == 1
    dlg.navigate_to_page(0)
    press("Ctrl+Shift+Tab")
    assert dlg.sidebar.currentRow() == dlg.sidebar.count() - 1   # wraps
    assert dlg.stack.currentIndex() == dlg.sidebar.count() - 1


# --- S5: Cancel undoes Reset all, on screen and on disk ---------------------------------------------

def test_cancel_after_reset_all_restores_the_user_config(qtbot):
    user = dict(DEFAULTS, font_size=8, pause_in_menu=True, keep_data=90, reduce_motion=True,
                color_coding=True, location_onboarding_dismissed=True,
                widgets_overlap_nudge_shown=True, monitor_active_tab="hardware")
    dlg, main = _make(qtbot, user)
    dlg.advanced_page.reset_all_btn.click()
    _flush(dlg)
    assert main.config["font_size"] == DEFAULTS["font_size"]
    # Reset all resets settings only - onboarding / nudge flags and UI state are kept.
    for k in ("location_onboarding_dismissed", "widgets_overlap_nudge_shown", "monitor_active_tab"):
        assert dlg.config[k] == user[k], k
    dlg.cancel_button.click()
    for k in ("font_size", "pause_in_menu", "keep_data", "reduce_motion", "color_coding"):
        assert main.config[k] == user[k], k


# --- S8: a half-typed hex never reaches the widget or the saved file ---------------------------------

def test_half_typed_hex_keeps_the_last_valid_colour(qtbot):
    # color_is_automatic False: a custom colour is the user's (an automatic one follows the live theme, #405)
    cfg = dict(DEFAULTS, default_color="#ABCDEF", color_is_automatic=False, background_color="#101010",
               high_speed_color="#00FF00", arrow_up_color="#11FF11")
    dlg, main = _make(qtbot, cfg)
    dlg.appearance_page.default_color_input.setText("#AB")
    dlg.appearance_page.background_color_input.setText("#1")
    dlg.appearance_page.arrow_up_color_input.setText("")
    dlg.colors_page.high_speed_color_input.setText("#00")
    _flush(dlg)
    s = dlg.get_settings()
    assert (s["default_color"], s["background_color"], s["arrow_up_color"], s["high_speed_color"]) == \
        ("#ABCDEF", "#101010", "#11FF11", "#00FF00")
    assert main.config["default_color"] == "#ABCDEF"
    dlg.appearance_page.default_color_input.setText("#123456")
    assert dlg.get_settings()["default_color"] == "#123456"
