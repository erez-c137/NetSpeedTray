"""
The adapter classifier (2.2 scope C): one answer to "is this my real connection?", shared by the
Settings interface picker's grouping and the all_physical aggregation.

The fixture table is real: the first block is the dev machine's adapters as GetIfTable2 reports them
(Get-NetAdapter -IncludeHidden + psutil.net_if_stats, 2026-10-19); the rest are adapters seen on
Windows in the wild (VPN clients, VM hosts and guests, tethering). The 2.1 keyword list missed
Tailscale, WireGuard, NordLynx, ZeroTier and vEthernet (accuracy audit, finding 5) - every one of them
is in the table.
"""
import pytest

from netspeedtray import constants
from netspeedtray.utils.interface_classifier import (
    AdapterInfo, InterfaceClassifier, classify_interface, group_interfaces,
)

PHYS = constants.network.interface.KIND_PHYSICAL
VIRT = constants.network.interface.KIND_VIRTUAL
PSEUDO = constants.network.interface.KIND_PSEUDO

# (friendly name, adapter description, IANA ifType, HardwareInterface flag, expected kind)
REAL_ADAPTERS = [
    # --- the dev machine ---
    ("Ethernet", "Intel(R) Ethernet Connection (17) I219-LM", 6, True, PHYS),
    ("Wi-Fi 3", "Realtek 8851BU Wireless LAN WiFi 6 USB NIC", 71, True, PHYS),
    ("Wi-Fi 2", "Realtek 8811CU Wireless LAN 802.11ac USB NIC", 71, True, PHYS),
    ("vEthernet (Default Switch)", "Hyper-V Virtual Ethernet Adapter", 6, False, VIRT),
    ("vEthernet (WSL (Hyper-V firewall))", "Hyper-V Virtual Ethernet Adapter #2", 6, False, VIRT),
    ("Ethernet 3", "VirtualBox Host-Only Ethernet Adapter", 6, False, VIRT),   # name looks physical
    ("Tailscale", "Tailscale Tunnel", 53, False, VIRT),
    ("Local Area Connection* 12", "Microsoft Wi-Fi Direct Virtual Adapter #4", 71, False, VIRT),
    ("Local Area Connection* 7", "WAN Miniport (PPPOE)", 23, False, VIRT),
    ("Ethernet (Kernel Debugger)", "Microsoft Kernel Debug Network Adapter", 6, False, VIRT),
    ("Loopback Pseudo-Interface 1", "Software Loopback Interface 1", 24, False, PSEUDO),
    ("Teredo Tunneling Pseudo-Interface", "Microsoft Teredo Tunneling Adapter", 131, False, PSEUDO),
    ("6to4 Adapter", "Microsoft 6to4 Adapter", 131, False, PSEUDO),
    # --- VPN clients ---
    ("wg-home", "WireGuard Tunnel", 53, False, VIRT),
    ("NordLynx", "NordLynx Tunnel", 53, False, VIRT),
    ("ZeroTier One [8056c2e21c000001]", "ZeroTier Virtual Port", 6, False, VIRT),
    ("OpenVPN TAP-Windows6", "TAP-Windows Adapter V9", 6, False, VIRT),
    ("OpenVPN Wintun", "Wintun Userspace Tunnel", 53, False, VIRT),
    ("Ethernet 5", "PANGP Virtual Ethernet Adapter Secure", 6, False, VIRT),
    ("Ethernet 6", "Cisco AnyConnect Secure Mobility Client Virtual Miniport Adapter for Windows x64",
     6, False, VIRT),
    # --- VM hosts, PAN ---
    ("VMware Network Adapter VMnet8", "VMware Virtual Ethernet Adapter for VMnet8", 6, False, VIRT),
    ("Bluetooth Network Connection", "Bluetooth Device (Personal Area Network)", 6, True, VIRT),
    # --- real adapters that must NOT be caught by a keyword ---
    ("Ethernet 4", "Remote NDIS based Internet Sharing Device", 6, True, PHYS),   # USB tethering
    ("Ethernet", "Microsoft Hyper-V Network Adapter", 6, True, PHYS),             # inside a Hyper-V VM
    ("Ethernet0", "vmxnet3 Ethernet Adapter", 6, True, PHYS),                     # inside a VMware VM
    ("Ethernet 2", "Realtek USB GbE Family Controller", 6, True, PHYS),
    ("Wi-Fi", "Intel(R) Wi-Fi 6E AX211 160MHz", 71, True, PHYS),
    # --- no keyword anywhere: only the OS signals know ---
    ("Ethernet 7", "Acme SecureLink Adapter", 53, True, VIRT),      # ifType 53 beats the flag
    ("Ethernet 8", "Acme Network Adapter", 6, False, VIRT),         # not a hardware interface
    ("Ethernet 9", "Acme Secure Thing", 131, True, VIRT),           # ifType 131 beats the flag
]


@pytest.mark.parametrize("name,desc,if_type,hw,expected", REAL_ADAPTERS,
                         ids=[f"{r[0]}|{r[1][:24]}" for r in REAL_ADAPTERS])
def test_real_adapter_table(name, desc, if_type, hw, expected):
    info = AdapterInfo(name=name, description=desc, if_type=if_type, hardware=hw)
    assert classify_interface(name, info) == expected


# When the OS can't be asked (off Windows, or a name only seen in the history DB), the name decides.
NAME_ONLY = [
    ("Wi-Fi", PHYS), ("Ethernet", PHYS), ("Ethernet 2", PHYS),
    ("Tailscale", VIRT), ("vEthernet (WSL (Hyper-V firewall))", VIRT), ("NordLynx", VIRT),
    ("ZeroTier One [8056c2e21c000001]", VIRT), ("Local Area Connection* 3", VIRT),
    ("OpenVPN TAP-Windows6", VIRT), ("Bluetooth Network Connection", VIRT),
    ("Loopback Pseudo-Interface 1", PSEUDO), ("isatap.{1234}", PSEUDO),
]


@pytest.mark.parametrize("name,expected", NAME_ONLY)
def test_name_only_fallback(name, expected):
    assert classify_interface(name, None) == expected


def test_user_exclusions_still_veto():
    """`excluded_interfaces` is a user-editable config key; a hit keeps an adapter out of the
    physical set even when the OS calls it hardware (back-compat with the 2.1 list)."""
    info = AdapterInfo(name="Office NIC", description="Intel(R) I219-LM", if_type=6, hardware=True)
    assert classify_interface("Office NIC", info) == PHYS
    assert classify_interface("Office NIC", info, exclusions=["office"]) == VIRT


# --- the cached classifier ----------------------------------------------------------------------

def _table(*rows):
    return {r.name: r for r in rows}


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_classifier_reads_os_once_and_caches():
    calls = []

    def loader():
        calls.append(1)
        return _table(AdapterInfo("Ethernet 3", "VirtualBox Host-Only Ethernet Adapter", 6, False))

    c = InterfaceClassifier(loader=loader, clock=_Clock())
    assert c.kind("Ethernet 3") == VIRT
    assert c.kind("Ethernet 3") == VIRT
    assert len(calls) == 1


def test_unknown_name_refreshes_but_rate_limited():
    """A VPN that comes up after launch is a new name; the table is re-read for it, at most every
    CLASSIFIER_REFRESH_SEC so a name the OS never reports can't cause a read per poll."""
    clock = _Clock()
    tables = [_table(), _table(AdapterInfo("Acme Link", "Acme Adapter", 53, False))]
    calls = []

    def loader():
        calls.append(1)
        return tables[min(len(calls) - 1, 1)]

    c = InterfaceClassifier(loader=loader, clock=clock)
    assert c.kind("Acme Link") == PHYS          # first table doesn't know it; the name looks physical
    assert c.kind("Acme Link") == PHYS          # inside the window: no re-read
    assert len(calls) == 1
    clock.t += constants.network.interface.CLASSIFIER_REFRESH_SEC + 1
    assert c.kind("Acme Link") == VIRT          # re-read finds it: ifType 53
    assert len(calls) == 2


def test_loader_failure_falls_back_to_names():
    def loader():
        raise OSError("iphlpapi unavailable")

    c = InterfaceClassifier(loader=loader, clock=_Clock())
    assert c.kind("Tailscale") == VIRT
    assert c.kind("Wi-Fi") == PHYS


def test_exclusions_change_is_honoured():
    c = InterfaceClassifier(loader=dict, clock=_Clock())
    assert c.kind("Office NIC") == PHYS
    assert c.kind("Office NIC", exclusions=["office"]) == VIRT


def test_read_os_adapters_on_this_machine_is_well_formed():
    """Live (read-only) GetIfTable2 read: every row is an AdapterInfo keyed by its friendly name,
    and loopback is always there on Windows. Off Windows it is simply empty."""
    import sys
    from netspeedtray.utils.interface_classifier import read_os_adapters
    table = read_os_adapters()
    if sys.platform != "win32":
        assert table == {}
        return
    assert table, "GetIfTable2 returned nothing on Windows"
    for name, info in table.items():
        assert isinstance(info, AdapterInfo) and info.name == name
    loop = [i for i in table.values() if i.if_type == constants.network.interface.IF_TYPE_SOFTWARE_LOOPBACK]
    assert loop and classify_interface(loop[0].name, loop[0]) == PSEUDO


# --- picker grouping ------------------------------------------------------------------------------

_KINDS = {"Wi-Fi 3": PHYS, "Ethernet": PHYS, "Tailscale": VIRT, "vEthernet (Default Switch)": VIRT,
          "Loopback Pseudo-Interface 1": PSEUDO}


def test_group_puts_primary_first_and_sorts_the_rest():
    g = group_interfaces(list(_KINDS), _KINDS.get, primary="Wi-Fi 3")
    assert g.physical == ["Wi-Fi 3", "Ethernet"]
    assert g.virtual == ["Loopback Pseudo-Interface 1", "Tailscale", "vEthernet (Default Switch)"]
    assert g.primary == "Wi-Fi 3"


def test_group_primary_can_be_a_vpn():
    """Full-tunnel VPN: the routing interface IS the tunnel. It's marked and first in its group."""
    g = group_interfaces(list(_KINDS), _KINDS.get, primary="Tailscale")
    assert g.virtual[0] == "Tailscale"
    assert g.physical == ["Ethernet", "Wi-Fi 3"]


def test_group_ignores_a_primary_not_in_the_list():
    g = group_interfaces(["Ethernet"], _KINDS.get, primary="Ghost")
    assert g.primary is None and g.physical == ["Ethernet"] and g.virtual == []
