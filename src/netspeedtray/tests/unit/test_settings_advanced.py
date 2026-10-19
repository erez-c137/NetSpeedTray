"""Advanced settings page (2.0 IA) - retention/reduce-motion round-trip + reset affordances (C6)."""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QMessageBox

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.pages.advanced import AdvancedPage
from netspeedtray.views.settings.dialog import SettingsDialog


@pytest.fixture
def page(q_app):
    return AdvancedPage(I18nStrings("en_US"), lambda: None)


def test_advanced_round_trips_retention_and_reduce_motion(page):
    page.load_settings({"keep_data": 90, "reduce_motion": True})  # 3 months - a valid ladder value
    out = page.get_settings()
    assert out["keep_data"] == 90
    assert out["reduce_motion"] is True


def test_keep_data_snaps_to_closest_valid_retention(page):
    page.load_settings({"keep_data": 33})  # not in DAYS_MAP -> closest is 31 (1 month floor)
    assert page.get_settings()["keep_data"] == 31


def test_advanced_round_trips_hover_and_pause_toggles(page):
    page.load_settings({"show_usage_on_hover": False, "show_hover_tips": False, "pause_in_menu": True})
    out = page.get_settings()
    assert out["show_usage_on_hover"] is False
    assert out["show_hover_tips"] is False
    assert out["pause_in_menu"] is True


@pytest.fixture
def dialog(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)
    m = MagicMock(); m.config = {}; m.config_manager = MagicMock()
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
    cfg.update({"reduce_motion": True, "keep_data": 7, "monitor_cpu_enabled": True})  # non-defaults
    dlg = SettingsDialog(
        main_widget=m, config=cfg, version="2.0.0", i18n=I18nStrings("en_US"),
        available_interfaces=["Ethernet"], is_startup_enabled=False,
    )
    qtbot.addWidget(dlg)
    return dlg


def test_reset_all_restores_defaults(qtbot, dialog, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._reset_all_to_defaults()
    settings = dialog.get_settings()
    defaults = constants.config.defaults.DEFAULT_CONFIG
    assert settings["reduce_motion"] == defaults["reduce_motion"]
    assert settings["keep_data"] == defaults["keep_data"]
    assert settings["monitor_cpu_enabled"] == defaults["monitor_cpu_enabled"]


def test_reset_all_cancelled_keeps_values(qtbot, dialog, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog._reset_all_to_defaults()
    assert dialog.get_settings()["reduce_motion"] is True  # unchanged


def test_hardware_deeplink_points_at_hardware_page(dialog):
    """The tray 'Hardware monitor' row deep-links via SettingsDialog.PAGE_HARDWARE; it must be the
    Hardware sidebar row. A future page reshuffle that moves Hardware trips this test instead of
    silently opening the wrong page."""
    idx = SettingsDialog.PAGE_HARDWARE
    assert dialog.sidebar.item(idx).text() == dialog.i18n.HARDWARE_MONITORING_GROUP


def test_data_usage_deeplink_points_at_network_page(dialog):
    """The Monitor's 'Set a monthly limit' hint deep-links via PAGE_NETWORK; it must be the Network
    Interfaces row (the data-cap 'Data usage' section lives there). Regression for the hint opening
    General - the headline data-cap feature reading as broken."""
    idx = SettingsDialog.PAGE_NETWORK
    assert dialog.sidebar.item(idx).text() == dialog.i18n.NETWORK_INTERFACES_GROUP


def test_reset_advanced_page_only_resets_its_keys(qtbot, dialog):
    dialog._reset_advanced_page()
    settings = dialog.get_settings()
    defaults = constants.config.defaults.DEFAULT_CONFIG
    assert settings["reduce_motion"] == defaults["reduce_motion"]  # reset
    assert settings["keep_data"] == defaults["keep_data"]          # reset
    assert settings["monitor_cpu_enabled"] is True                 # untouched (other page)


# --- 2.2 item C: hardware-recording toggle + Delete all history ------------------------------------

def test_record_hardware_history_round_trips(page):
    page.load_settings({"record_hardware_history": False})
    assert page.get_settings()["record_hardware_history"] is False
    page.load_settings({})                                  # absent -> the default (on)
    assert page.get_settings()["record_hardware_history"] is True


def test_delete_history_cancelled_deletes_nothing(dialog, monkeypatch):
    monkeypatch.setattr(dialog, "_confirm_delete_history", lambda: None)
    dialog._delete_all_history()
    dialog.parent_widget.widget_state.delete_all_history.assert_not_called()


def test_delete_history_keeps_usage_by_default(dialog, monkeypatch):
    monkeypatch.setattr(dialog, "_confirm_delete_history", lambda: False)
    dialog.config["usage_alert_state"] = "2026-10-01|100|total::80"
    dialog._delete_all_history()
    dialog.parent_widget.widget_state.delete_all_history.assert_called_once_with(include_usage=False)
    assert dialog.get_settings()["usage_alert_state"] == "2026-10-01|100|total::80"
    assert dialog.advanced_page.delete_history_btn.isEnabled() is False   # until the worker answers
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dialog._on_history_deleted(True)
    assert dialog.advanced_page.delete_history_btn.isEnabled() is True


def test_delete_history_with_usage_rearms_cap_alerts(dialog, monkeypatch):
    monkeypatch.setattr(dialog, "_confirm_delete_history", lambda: True)
    dialog.config["usage_alert_state"] = "2026-10-01|100|total::80"
    dialog.original_config["usage_alert_state"] = "2026-10-01|100|total::80"
    dialog._delete_all_history()
    dialog.parent_widget.widget_state.delete_all_history.assert_called_once_with(include_usage=True)
    dialog.parent_widget.update_config.assert_called_with({"usage_alert_state": ""})
    # Neither Save (config) nor Cancel (original_config) may restore the stale alert state.
    assert dialog.get_settings()["usage_alert_state"] == ""
    assert dialog.original_config["usage_alert_state"] == ""


def test_delete_confirmation_defaults_to_safe_choices(dialog, monkeypatch):
    seen = {}

    def fake_exec(box):
        seen["usage_checked"] = box.checkBox().isChecked()
        seen["default_is_cancel"] = box.defaultButton() is box.escapeButton()
        return 0                                             # closed without clicking Delete

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    assert dialog._confirm_delete_history() is None
    assert seen == {"usage_checked": False, "default_is_cancel": True}
