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
