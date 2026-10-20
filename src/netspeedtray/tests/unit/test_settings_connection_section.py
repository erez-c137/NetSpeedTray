"""The Settings latency card names the hop it actually measures.

It was titled "Internet latency" while its description (and the default probe) said "to your router".
The title is now true for both hops, and the description follows the public-probe toggle: the router by
default, the internet test server once the user opts in - the Check-connection sheet's vocabulary.
"""
import pytest
from PyQt6.QtWidgets import QLabel

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.settings.pages.connection_section import ConnectionSettings


def _texts(expander):
    return [lbl.text() for lbl in expander._header.findChildren(QLabel) if lbl.text()]


@pytest.fixture
def section(q_app):
    return ConnectionSettings(lambda: None, I18nStrings("en_US"))


def test_title_does_not_claim_the_internet(section):
    title = _texts(section._lat)[0]
    assert "internet" not in title.lower(), title


def test_description_says_router_by_default(section):
    section.load_settings({"latency_enabled": True, "latency_public_enabled": False})
    desc = _texts(section._lat)[1]
    assert "router" in desc.lower() and "internet" not in desc.lower(), desc


def test_description_follows_the_public_probe_toggle(section):
    section.load_settings({"latency_enabled": True, "latency_public_enabled": True})
    assert "internet" in _texts(section._lat)[1].lower()
    section._public.setChecked(False)
    assert "internet" not in _texts(section._lat)[1].lower()
    section._public.setChecked(True)
    assert "internet" in _texts(section._lat)[1].lower()
