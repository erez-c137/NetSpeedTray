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


def test_interfaces_page_exposes_datacap_keys(q_app):
    """The Network page (interfaces) must surface the data-cap keys in get_settings."""
    page = InterfacesPage(I18nStrings("en_US"), ["Ethernet"], lambda: None)
    page.load_settings({"interface_mode": "auto", "data_cap_enabled": True, "data_cap_gb": 100})
    g = page.get_settings()
    assert g["interface_mode"] == "auto"
    assert g["data_cap_enabled"] is True
    assert g["data_cap_gb"] == 100.0
