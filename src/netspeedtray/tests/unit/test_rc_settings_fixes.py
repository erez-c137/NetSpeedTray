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
    # color_is_automatic False: an automatic text colour follows the live Windows theme on Save
    # (#405) - legitimately a change on a light taskbar, and not what this test is about.
    cfg = dict(DEFAULTS, graph_opacity=opacity, keep_data=365, font_family="Consolas",
               record_hardware_history=True, color_is_automatic=False)
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
               keep_data=365, metrics_endpoint_port=9555, color_is_automatic=False)   # see above
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


def test_typed_colour_survives_a_live_theme_flip(qtbot, monkeypatch):
    """S3 + #405: while Settings is open a Windows Light/Dark flip makes the dialog pass the theme's
    colour for an AUTOMATIC text colour. A typed hex is the user's choice: it must win, in the live
    preview and on Save - while an untouched automatic colour still follows the theme."""
    from netspeedtray.views.settings import dialog as D
    dlg, main = _make(qtbot, dict(DEFAULTS, default_color="#FFFFFF", color_is_automatic=True))
    monkeypatch.setattr(D, "shell_theme_text_color", lambda: "#000000")   # the theme flipped to light
    assert dlg.get_settings()["default_color"] == "#000000"               # untouched: follows the theme
    dlg.appearance_page.default_color_input.setText("#FF8800")
    _flush(dlg)
    s = dlg.get_settings()
    assert (s["default_color"], s["color_is_automatic"]) == ("#FF8800", False)
    assert main.config["default_color"] == "#FF8800" and main.config["color_is_automatic"] is False
    dlg.save_button.click()
    assert (main.saved[-1]["default_color"], main.saved[-1]["color_is_automatic"]) == ("#FF8800", False)


def test_cancel_after_reset_all_follows_the_theme_for_an_automatic_colour(qtbot, monkeypatch):
    """Cancel restores the pre-dialog config (S5); its automatic colour still takes the live theme
    colour (#405), judged against that same pre-dialog config, not the reset one."""
    from netspeedtray.views.settings import dialog as D
    dlg, main = _make(qtbot, dict(DEFAULTS, default_color="#FFFFFF", color_is_automatic=True, font_size=8))
    dlg.advanced_page.reset_all_btn.click()
    _flush(dlg)
    monkeypatch.setattr(D, "shell_theme_text_color", lambda: "#000000")
    dlg.cancel_button.click()
    assert main.config["font_size"] == 8 and main.config["default_color"] == "#000000"


def test_pick_then_cancel_still_follows_the_theme(qtbot, monkeypatch):
    """Review item 2: a colour picked in the session is undone by Cancel, so the restored automatic
    colour follows the live theme - it used to stay white on a light taskbar."""
    from netspeedtray.views.settings import dialog as D
    dlg, main = _make(qtbot, dict(DEFAULTS, default_color="#FFFFFF", color_is_automatic=True))
    monkeypatch.setattr(D, "shell_theme_text_color", lambda: "#000000")
    dlg._user_chose_default_color = True          # what a pick in the colour dialog sets
    dlg.appearance_page.set_color_input("default", "#123456")
    _flush(dlg)
    dlg.cancel_button.click()
    assert (main.config["default_color"], main.config["color_is_automatic"]) == ("#000000", True)


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


# --- S6: Reset this page re-greys Record hardware history from the live toggles --------------------

def test_reset_advanced_page_uses_the_live_hardware_toggles(qtbot):
    dlg, _ = _make(qtbot, dict(DEFAULTS, monitor_cpu_enabled=False, monitor_gpu_enabled=False))
    dlg.hardware_page.monitor_cpu.setChecked(True)   # turned on in this session
    assert dlg.advanced_page._record_card.isEnabled()
    dlg.advanced_page.reset_page_btn.click()
    assert dlg.advanced_page._record_card.isEnabled()


# --- S7: Save keeps a preferred monitor / selected adapter that isn't connected right now ----------

def test_untouched_save_keeps_unplugged_monitor_and_adapter(qtbot):
    cfg = dict(DEFAULTS, interface_mode="selected", selected_interfaces=["Wi-Fi", "WireGuard Tunnel"],
               preferred_monitor=r"\\.\DISPLAY9")
    dlg, main = _make(qtbot, cfg, ifaces=("Ethernet", "Wi-Fi"))
    combo = dlg.widget_page.preferred_monitor_combo
    assert combo.currentData() == r"\\.\DISPLAY9"
    assert dlg.i18n.SETTINGS_NOT_CONNECTED_TAG in combo.currentText()
    assert "WireGuard Tunnel" in dlg.interfaces_page.interface_checkboxes   # shown, still ticked
    dlg.save_button.click()
    assert main.saved[-1]["preferred_monitor"] == r"\\.\DISPLAY9"
    assert sorted(main.saved[-1]["selected_interfaces"]) == ["Wi-Fi", "WireGuard Tunnel"]


def test_unticking_an_unplugged_adapter_removes_it(qtbot):
    cfg = dict(DEFAULTS, interface_mode="selected", selected_interfaces=["Wi-Fi", "WireGuard Tunnel"])
    dlg, _ = _make(qtbot, cfg, ifaces=("Ethernet", "Wi-Fi"))
    dlg.interfaces_page.interface_checkboxes["WireGuard Tunnel"].setChecked(False)
    assert dlg.get_settings()["selected_interfaces"] == ["Wi-Fi"]


def _visual_ltr(text: str, rtl: bool) -> str:
    """The characters of ``text`` in visual left-to-right order as Qt lays them out (formatting
    marks dropped) - the bidi result a QLabel / combo item paints."""
    from PyQt6.QtGui import QFont, QTextLayout, QTextOption
    lay = QTextLayout(text, QFont("Segoe UI", 12))
    opt = QTextOption()
    opt.setTextDirection(Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight)
    lay.setTextOption(opt)
    lay.beginLayout()
    line = lay.createLine()
    line.setLineWidth(2000)
    lay.endLayout()
    marks = "‎‏‪‫‬⁦⁧⁨⁩"
    xs = [(min(line.cursorToX(i)[0], line.cursorToX(i + 1)[0]), ch) for i, ch in enumerate(text)
          if ch not in marks]
    return "".join(ch for _, ch in sorted(xs))


def test_unplugged_monitor_name_reads_correctly_in_hebrew(qtbot):
    """Review item 9: in he_IL '\\\\.\\DISPLAY2 (לא מחובר)' painted as '... 2DISPLAY\\.\\\\' - the
    neutral '\\\\.' and the digit drifted out of the device name."""
    name = "\\\\.\\DISPLAY2"
    q_app = QApplication.instance()
    q_app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    try:
        cfg = dict(DEFAULTS, preferred_monitor=name)
        main = _Main(cfg)
        dlg = SettingsDialog(main_widget=main, config=cfg, version="2.2.0", i18n=I18nStrings("he_IL"),
                             available_interfaces=["Ethernet"], is_startup_enabled=False)
        qtbot.addWidget(dlg)
        combo = dlg.widget_page.preferred_monitor_combo
        text = combo.itemText(combo.findData(name))
        visual = _visual_ltr(text, rtl=True)
        assert name in visual, visual                                  # the name stays one LTR unit
        assert visual.index(name) > visual.index(")")                  # read first, on the right
    finally:
        q_app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)


@pytest.mark.parametrize("lang", ["ru_RU", "de_DE", "ja_JP"])
def test_monitor_names_are_translated(qtbot, lang):
    """S9: "Monitor {i}: WxH (primary)" was hard-coded English in every language."""
    cfg = dict(DEFAULTS)
    main = _Main(cfg)
    dlg = SettingsDialog(main_widget=main, config=cfg, version="2.2.0", i18n=I18nStrings(lang),
                         available_interfaces=["Ethernet"], is_startup_enabled=False)
    qtbot.addWidget(dlg)
    combo = dlg.widget_page.preferred_monitor_combo
    if combo.count() < 2:
        pytest.skip("no screens on this test platform")
    text = combo.itemText(1)
    assert not text.startswith("Monitor 1:") or lang == "de_DE"
    assert "(primary)" not in text
    assert text.startswith(dlg.i18n.PREFERRED_MONITOR_ITEM.split("{")[0].strip() or text[:1])


# --- S9: small Settings polish ------------------------------------------------------------------------

def test_threshold_unit_follows_a_unit_change(qtbot):
    dlg, _ = _make(qtbot, dict(DEFAULTS, unit_type="bits_decimal", color_coding=True))
    before = dlg.colors_page.high_speed_threshold.suffix()
    ut = dlg.units_page.unit_type
    ut.setCurrentIndex(ut.findData("bytes_binary"))
    after = dlg.colors_page.high_speed_threshold.suffix()
    assert after != before
    assert after == dlg.colors_page._threshold_suffix({"unit_type": "bytes_binary"})


def test_page_title_is_drawn_at_24px(qtbot):
    dlg, _ = _make(qtbot)
    dlg.show()
    dlg._page_title.ensurePolished()   # the dialog QSS is applied at polish - that's what shrank it
    assert dlg._page_title.font().pixelSize() == 24


def test_data_cap_accepts_decimals(qtbot):
    dlg, _ = _make(qtbot, dict(DEFAULTS, data_cap_enabled=True, data_cap_gb=250.5))
    assert dlg.get_settings()["data_cap_gb"] == 250.5


def test_inert_controls_grey_out(qtbot):
    dlg, _ = _make(qtbot, dict(DEFAULTS, graph_enabled=False, preferred_monitor=None))
    mg = dlg.widget_page.mini_graph_section
    assert not mg._duration_card.isEnabled() and not mg._opacity_card.isEnabled()
    assert not mg._fixed_card.isEnabled()
    mg.enable_graph.setChecked(True)
    assert mg._duration_card.isEnabled() and mg._opacity_card.isEnabled() and mg._fixed_card.isEnabled()
    assert not dlg.widget_page._free_float_card.isEnabled()   # Primary (auto): floating can't apply


def test_reset_all_resets_hidden_settings_and_keeps_state(qtbot):
    """Review item 5: Reset all no longer reset settings no page shows (2.1.7 did), so the user had
    no way back to their defaults; app state (onboarding, positions, ...) must still survive."""
    hidden = {"text_alignment": "left", "force_decimals": not DEFAULTS["force_decimals"],
              "dynamic_update_enabled": not DEFAULTS["dynamic_update_enabled"],
              "dark_mode": not DEFAULTS["dark_mode"], "show_legend": not DEFAULTS["show_legend"],
              "monitor_graph_smoothing": not DEFAULTS["monitor_graph_smoothing"]}
    state = {"location_onboarding_dismissed": True, "monitor_active_tab": "hardware", "paused": True,
             "history_period_chosen": True, "history_period_slider_value": 3}
    dlg, _ = _make(qtbot, dict(DEFAULTS, **hidden, **state))
    dlg.advanced_page.reset_all_btn.click()
    for k in hidden:
        assert dlg.config[k] == DEFAULTS[k], k
    for k, v in state.items():
        assert dlg.config[k] == v, k


def test_every_config_key_has_a_reset_all_decision(qtbot):
    """A new config key must be reset by a page, listed as hidden-but-reset, or kept as state."""
    dlg, _ = _make(qtbot)
    owned = {"color_is_automatic"}
    for p in dlg._pages():
        owned |= set(p.get_settings())
    undecided = set(DEFAULTS) - owned - set(dlg.RESET_ALL_HIDDEN) - set(dlg.RESET_ALL_KEEPS)
    assert not undecided, f"decide Reset all behaviour for: {sorted(undecided)}"


@pytest.mark.parametrize("mode", ["network_only", "cycle"])
@pytest.mark.parametrize("stack", [True, False])
def test_untouched_save_keeps_stack_hardware_stats(qtbot, mode, stack):
    """Review item 3: the dropdown wrote stack_hardware_stats = (mode == side_by_stack) for every
    mode, so an untouched Save in network-only / cycle flipped the factory True to False."""
    dlg, _ = _make(qtbot, dict(DEFAULTS, widget_display_mode=mode, stack_hardware_stats=stack))
    assert dlg.get_settings()["stack_hardware_stats"] is stack


def test_enabling_hardware_returns_to_the_stacked_layout(qtbot):
    dlg, _ = _make(qtbot, dict(DEFAULTS, widget_display_mode="network_only", stack_hardware_stats=True))
    dlg.widget_page.ensure_hardware_visible()
    s = dlg.get_settings()
    assert (s["widget_display_mode"], s["stack_hardware_stats"]) == ("side_by_side", True)


def test_no_dead_settings_doors_on_the_widget():
    """open_hardware_settings had no caller (the tray row it served is gone); every door the widget
    keeps must be reachable. open_data_usage_settings is the Monitor's 'Set a limit' link."""
    import pathlib
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    src = pathlib.Path(__file__).resolve().parents[2]
    callers = lambda name: [p for p in src.rglob("*.py") if "tests" not in p.parts
                            and p.name != "main.py" and name in p.read_text(encoding="utf-8")]
    for name in [n for n in dir(NetworkSpeedWidget) if n.startswith("open_") and n.endswith("_settings")]:
        assert callers(name), f"{name} has no caller"


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
