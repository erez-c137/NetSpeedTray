"""
Settings -> Network Interfaces: the picker groups adapters into physical vs virtual (2.2 scope C), the
primary (routing) connection is first and marked, and selection still round-trips by name.
"""
import pytest
from PyQt6.QtWidgets import QLabel

from netspeedtray import constants
from netspeedtray.utils.interface_classifier import AdapterInfo, InterfaceClassifier
from netspeedtray.views.settings.pages.interfaces import InterfacesPage

_TABLE = {
    "Wi-Fi 3": AdapterInfo("Wi-Fi 3", "Realtek 8851BU Wireless LAN WiFi 6 USB NIC", 71, True),
    "Ethernet": AdapterInfo("Ethernet", "Intel(R) Ethernet Connection (17) I219-LM", 6, True),
    "Tailscale": AdapterInfo("Tailscale", "Tailscale Tunnel", 53, False),
    "Ethernet 3": AdapterInfo("Ethernet 3", "VirtualBox Host-Only Ethernet Adapter", 6, False),
    "vEthernet (Default Switch)": AdapterInfo("vEthernet (Default Switch)", "Hyper-V Virtual Ethernet Adapter", 6, False),
}


@pytest.fixture
def i18n():
    return constants.i18n.I18nStrings("en_US")


def _page(i18n, primary="Wi-Fi 3", names=None):
    return InterfacesPage(i18n, list(_TABLE if names is None else names), lambda: None,
                          classifier=InterfaceClassifier(loader=lambda: dict(_TABLE)),
                          primary_interface=primary)


def _layout_items(page):
    """The picker list top-to-bottom: ('header', text) / ('iface', name, marked)."""
    out = []
    lay = page.interfaces_container_layout
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is None:
            continue
        role = w.property("pickerRole")
        if role == "header":
            out.append(("header", w.text()))
        elif role == "row":
            out.append(("iface", w.property("interfaceName"), bool(w.property("isPrimary"))))
    return out


def test_two_groups_primary_first_and_marked(q_app, i18n):
    items = _layout_items(_page(i18n))
    assert items == [
        ("header", i18n.INTERFACE_GROUP_PHYSICAL),
        ("iface", "Wi-Fi 3", True),
        ("iface", "Ethernet", False),
        ("header", i18n.INTERFACE_GROUP_VIRTUAL),
        ("iface", "Ethernet 3", False),
        ("iface", "Tailscale", False),
        ("iface", "vEthernet (Default Switch)", False),
    ]


def test_primary_mark_text_is_shown(q_app, i18n):
    page = _page(i18n)
    marks = [w.text() for w in page.findChildren(QLabel) if w.property("pickerRole") == "primaryMark"]
    assert marks == [i18n.INTERFACE_ACTIVE_CONNECTION]


def test_no_virtual_adapters_means_no_virtual_header(q_app, i18n):
    items = _layout_items(_page(i18n, names=["Wi-Fi 3", "Ethernet"]))
    assert ("header", i18n.INTERFACE_GROUP_VIRTUAL) not in items
    assert items[0] == ("header", i18n.INTERFACE_GROUP_PHYSICAL)


def test_unknown_primary_marks_nothing(q_app, i18n):
    page = _page(i18n, primary=None)
    assert not any(it[0] == "iface" and it[2] for it in _layout_items(page))


def test_selection_round_trips_across_groups(q_app, i18n):
    page = _page(i18n)
    page.load_settings({"interface_mode": "selected", "selected_interfaces": ["Tailscale", "Ethernet"]})
    got = page.get_settings()
    assert got["interface_mode"] == "selected"
    assert set(got["selected_interfaces"]) == {"Tailscale", "Ethernet"}


def test_update_interface_list_regroups_with_new_primary(q_app, i18n):
    page = _page(i18n)
    page.update_interface_list(list(_TABLE), primary_interface="Tailscale")
    items = _layout_items(page)
    virt = items.index(("header", i18n.INTERFACE_GROUP_VIRTUAL))
    assert items[virt + 1] == ("iface", "Tailscale", True)


def test_no_primary_on_update_clears_the_mark(q_app, i18n):
    """The lookup found no route this time (offline, VPN reconnecting): the old mark must go, not
    linger on an adapter that is no longer the active connection."""
    page = _page(i18n)                      # Wi-Fi 3 marked
    page.update_interface_list(list(_TABLE), primary_interface=None)
    assert page.primary_interface is None
    assert not any(it[0] == "iface" and it[2] for it in _layout_items(page))


def test_empty_list_still_says_none_found(q_app, i18n):
    page = _page(i18n, names=[])
    page.interfaces_container_layout.count()
    texts = [w.text() for w in page.findChildren(QLabel)]
    assert i18n.NO_INTERFACES_FOUND in texts


def test_settings_dialog_passes_the_primary_to_the_picker(q_app, i18n):
    from unittest.mock import MagicMock
    from netspeedtray.views.settings import SettingsDialog
    parent = MagicMock()
    parent.config = constants.config.defaults.DEFAULT_CONFIG.copy()
    d = SettingsDialog(main_widget=parent, config=parent.config.copy(), version="2.2.0", i18n=i18n,
                       available_interfaces=["Wi-Fi 3", "Ethernet"], is_startup_enabled=False,
                       primary_interface="Ethernet")
    try:
        assert d.interfaces_page.primary_interface == "Ethernet"
        d.update_interface_list(["Wi-Fi 3", "Ethernet"], "Wi-Fi 3")
        assert d.interfaces_page.primary_interface == "Wi-Fi 3"
        d.update_interface_list(["Wi-Fi 3", "Ethernet"], None)     # no route now: mark cleared
        assert d.primary_interface is None
        assert d.interfaces_page.primary_interface is None
    finally:
        d.deleteLater()
