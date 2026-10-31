"""Data-cap "Data usage" settings section - round-trips all five data_cap_* keys."""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.pages.datacap_section import DataCapSettings
from netspeedtray.views.settings.pages.interfaces import InterfacesPage


@pytest.fixture
def section(q_app):
    return DataCapSettings(lambda: None)


def test_datacap_section_round_trips(section):
    section.load_settings({
        "data_cap_enabled": True, "data_cap_gb": 500, "data_cap_reset_day": 15,
        "data_cap_count": "download", "data_cap_alert_enabled": False,
    })
    out = section.get_settings()
    assert out == {
        "data_cap_enabled": True, "data_cap_gb": 500.0, "data_cap_reset_day": 15,
        "data_cap_count": "download", "data_cap_alert_enabled": False,
    }


def test_datacap_defaults_when_unset(section):
    section.load_settings({})
    out = section.get_settings()
    assert out["data_cap_enabled"] is False
    assert out["data_cap_count"] == "total"
    assert out["data_cap_alert_enabled"] is True


@pytest.mark.parametrize("lang", ["en_US", "de_DE", "he_IL"])
def test_monitoring_mode_tooltips_say_what_the_cap_meters(q_app, lang):
    """rc.2 D5: Auto meters only the routing adapter (a VPN's, with one up); the tooltip names the
    Physical mode by its own label in that locale, and Physical says why tunnels are left out."""
    i18n = I18nStrings(lang)
    page = InterfacesPage(i18n, ["Ethernet"], lambda: None)
    auto_tip = page.auto_interface_radio.toolTip()
    assert "VPN" in auto_tip and i18n.MONITORING_MODE_PHYSICAL in auto_tip and "{mode}" not in auto_tip
    assert page.all_physical_interfaces_radio.toolTip() == i18n.MONITORING_MODE_PHYSICAL_TOOLTIP


def test_interfaces_page_exposes_datacap_keys(q_app):
    """The Network page (interfaces) must surface the data-cap keys in get_settings."""
    page = InterfacesPage(I18nStrings("en_US"), ["Ethernet"], lambda: None)
    page.load_settings({"interface_mode": "auto", "data_cap_enabled": True, "data_cap_gb": 100})
    g = page.get_settings()
    assert g["interface_mode"] == "auto"
    assert g["data_cap_enabled"] is True
    assert g["data_cap_gb"] == 100.0
