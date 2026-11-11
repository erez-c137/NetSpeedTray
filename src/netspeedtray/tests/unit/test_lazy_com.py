"""Memory plan 2.5: wmi / win32com.client load only when a source needs them, and the latency probe's
default gateway comes from the IP Helper route table instead of WMI.

* The route-table parser, against hand-built MIB_IPFORWARD_TABLE2 buffers (no route, IPv6 rows,
  on-link VPN defaults, several default routes ranked by effective metric, disconnected adapters).
* default_gateway_ip() falls back to WMI only when the table cannot be read at all.
* A subprocess imports the startup path and checks wmi / win32com.client stay out of sys.modules.
* A subprocess imports wmi FIRST on a worker that then CoUninitializes - the case the old GUI-thread
  preload existed for - and checks pywin32's exit-time "releasing IUnknown" error is gone (and that
  the same script without com_utils still reproduces it, so the check can fail).
"""
import ctypes
import socket
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from netspeedtray.core import latency_probe as LP

SRC = str(Path(__file__).resolve().parents[3])
AF_INET, AF_INET6 = 2, 23


# --- hand-built route tables ----------------------------------------------------------------------

def _table(rows):
    """A MIB_IPFORWARD_TABLE2 laid out as Windows returns it: NumEntries, then the rows at offset 8.
    Each row: (family, prefix_len, next_hop, metric, luid) - plus an optional loopback flag."""
    row_size = ctypes.sizeof(LP._MIB_IPFORWARD_ROW2)
    buf = ctypes.create_string_buffer(8 + row_size * max(1, len(rows)))
    tbl = LP._MIB_IPFORWARD_TABLE2.from_buffer(buf)
    tbl.NumEntries = len(rows)
    for i, spec in enumerate(rows):
        family, plen, hop, metric, luid = spec[:5]
        r = LP._MIB_IPFORWARD_ROW2.from_buffer(buf, 8 + i * row_size)
        r.InterfaceLuid = luid
        r.Metric = metric
        r.Loopback = 1 if len(spec) > 5 and spec[5] else 0
        r.DestinationPrefix.PrefixLength = plen
        r.DestinationPrefix.Prefix.si_family = family
        r.NextHop.si_family = family
        if family == AF_INET:
            r.NextHop.Ipv4.sin_addr[:] = list(socket.inet_aton(hop))
        else:
            r.NextHop.Ipv6.sin6_addr[:] = list(socket.inet_pton(socket.AF_INET6, hop))
    tbl._keep = buf
    return tbl


class _FakeLib:
    """Stands in for iphlpapi: GetIpForwardTable2 hands back `table`, GetIpInterfaceEntry answers
    from `ifaces` {luid: (metric, connected)}."""

    def __init__(self, table=None, status=0, ifaces=None):
        self.table, self.status, self.ifaces = table, status, ifaces or {}
        self.freed = 0

    def GetIpForwardTable2(self, family, pptr):
        assert family == AF_INET
        if self.status == 0:
            pptr._obj.contents = self.table
        return self.status

    def FreeMibTable(self, ptr):
        self.freed += 1

    def InitializeIpInterfaceEntry(self, prow):
        pass

    def GetIpInterfaceEntry(self, prow):
        row = prow._obj
        st = self.ifaces.get(row.InterfaceLuid)
        if st is None:
            return 1168   # ERROR_NOT_FOUND
        row.Metric, row.Connected = st[0], int(st[1])
        return 0


def test_the_structures_match_the_windows_layout():
    assert ctypes.sizeof(LP._SOCKADDR_INET) == 28
    assert ctypes.sizeof(LP._MIB_IPFORWARD_ROW2) == 104
    assert ctypes.sizeof(LP._MIB_IPINTERFACE_ROW) == 168
    assert LP._MIB_IPFORWARD_TABLE2.Table.offset == 8
    assert LP._MIB_IPINTERFACE_ROW.Metric.offset == 148
    assert LP._MIB_IPINTERFACE_ROW.Connected.offset == 156


def test_only_ipv4_default_routes_through_a_router_count():
    tbl = _table([
        (AF_INET, 8, "10.0.0.1", 5, 1),             # not a default route
        (AF_INET6, 0, "fe80::1", 1, 2),             # IPv6 default: the probe pings IPv4
        (AF_INET, 0, "0.0.0.0", 1, 3),              # on-link default (WireGuard / PPP VPN)
        (AF_INET, 0, "127.0.0.1", 1, 4, True),      # loopback
        (AF_INET, 0, "192.168.1.1", 25, 5),
    ])
    assert [r.next_hop for r in LP._ipv4_default_routes(tbl)] == ["192.168.1.1"]


def test_no_default_route_is_none_and_never_wmi(monkeypatch):
    lib = _FakeLib(_table([(AF_INET, 24, "192.168.1.0", 1, 1), (AF_INET6, 0, "fe80::1", 1, 2)]))
    assert LP._iphlp_default_gateway(lib) is None
    assert lib.freed == 1
    monkeypatch.setattr(LP, "_route_api", lambda: lib)
    monkeypatch.setattr(LP, "_wmi_default_gateway_ip",
                        lambda: pytest.fail("WMI queried although the route table was read"))
    assert LP.default_gateway_ip() is None
    assert LP.default_gateway_ip.__module__ == LP.__name__


def test_an_empty_table_is_none():
    assert LP._iphlp_default_gateway(_FakeLib(_table([]))) is None


def test_several_default_routes_pick_the_lowest_effective_metric():
    # Ethernet + Wi-Fi: Windows routes through the lower route + interface metric (35 < 50).
    tbl = _table([(AF_INET, 0, "192.168.1.1", 0, 10), (AF_INET, 0, "192.168.0.1", 0, 20)])
    lib = _FakeLib(tbl, ifaces={10: (50, True), 20: (35, True)})
    assert LP._iphlp_default_gateway(lib) == "192.168.0.1"
    # The route metric counts too.
    lib = _FakeLib(_table([(AF_INET, 0, "192.168.1.1", 0, 10), (AF_INET, 0, "192.168.0.1", 20, 20)]),
                   ifaces={10: (50, True), 20: (35, True)})
    assert LP._iphlp_default_gateway(lib) == "192.168.1.1"


def test_a_disconnected_adapter_and_ties():
    tbl = _table([(AF_INET, 0, "192.168.1.1", 0, 10), (AF_INET, 0, "10.0.0.1", 0, 20)])
    assert LP._iphlp_default_gateway(_FakeLib(tbl, ifaces={10: (5, False), 20: (90, True)})) == "10.0.0.1"
    # Equal metrics (or no interface data at all): table order, like WMI's first adapter.
    assert LP._iphlp_default_gateway(_FakeLib(tbl, ifaces={10: (25, True), 20: (25, True)})) == "192.168.1.1"
    assert LP._iphlp_default_gateway(_FakeLib(tbl)) == "192.168.1.1"


def test_an_unreadable_table_falls_back_to_wmi(monkeypatch):
    with pytest.raises(LP.RouteTableUnavailable):
        LP._iphlp_default_gateway(_FakeLib(status=87))

    def _no_api():
        raise OSError("iphlpapi missing")
    monkeypatch.setattr(LP, "_route_api", _no_api)
    monkeypatch.setattr(LP, "_wmi_default_gateway_ip", lambda: "192.168.1.254")
    assert LP.default_gateway_ip() == "192.168.1.254"


def test_the_latency_probe_gets_its_gateway_from_the_route_table(monkeypatch):
    lib = _FakeLib(_table([(AF_INET, 0, "192.168.1.1", 0, 10)]), ifaces={10: (25, True)})
    monkeypatch.setattr(LP, "_route_api", lambda: lib)
    monkeypatch.setattr(LP, "_wmi_default_gateway_ip", lambda: pytest.fail("WMI queried"))
    probe = LP.LatencyProbe({"latency_enabled": True})
    assert probe._gateway_ip() == "192.168.1.1"
    assert probe.gateway == "192.168.1.1"


def test_network_details_describe_the_adapter_of_the_routed_gateway(monkeypatch):
    """Two adapters with a gateway: the sheet's details follow the router the probe pings (the route
    table's pick), not merely WMI's first adapter."""
    def nic(desc, ip, gw):
        return types.SimpleNamespace(Description=desc, IPAddress=(ip,), DefaultIPGateway=(gw,),
                                     DNSServerSearchOrder=(gw,))
    nics = [nic("Ethernet", "192.168.1.20", "192.168.1.1"), nic("Wi-Fi", "192.168.0.20", "192.168.0.1")]
    fake = types.ModuleType("wmi")
    fake.WMI = lambda: types.SimpleNamespace(
        Win32_NetworkAdapterConfiguration=lambda IPEnabled=True: list(nics))
    monkeypatch.setitem(sys.modules, "wmi", fake)
    monkeypatch.setattr(LP, "_iphlp_default_route", lambda lib=None: LP._DefaultRoute("192.168.0.1", 0, 1))
    d = LP.network_details()
    assert (d.adapter, d.gateway, d.ipv4) == ("Wi-Fi", "192.168.0.1", ("192.168.0.20",))
    # A routed gateway no adapter reports (or no readable route table): WMI's first adapter.
    monkeypatch.setattr(LP, "_iphlp_default_route", lambda lib=None: LP._DefaultRoute("10.0.0.1", 0, 1))
    assert LP.network_details().adapter == "Ethernet"

    def _unreadable(lib=None):
        raise LP.RouteTableUnavailable("x")
    monkeypatch.setattr(LP, "_iphlp_default_route", _unreadable)
    assert LP.network_details().adapter == "Ethernet"


def test_network_details_follow_the_routed_interface_when_two_adapters_share_a_gateway(monkeypatch):
    """A docked laptop: Ethernet and Wi-Fi on one LAN, both with gateway 192.168.1.1. Windows routes
    through the lower metric (Ethernet here, interface 7); WMI lists Wi-Fi first. Matching on the
    gateway IP alone described the Wi-Fi adapter (its address, its DNS) for the Ethernet route."""
    def nic(desc, ip, idx):
        return types.SimpleNamespace(Description=desc, IPAddress=(ip,), DefaultIPGateway=("192.168.1.1",),
                                     DNSServerSearchOrder=("192.168.1.1",), InterfaceIndex=idx)
    nics = [nic("Wi-Fi", "192.168.1.30", 12), nic("Ethernet", "192.168.1.20", 7)]
    fake = types.ModuleType("wmi")
    fake.WMI = lambda: types.SimpleNamespace(
        Win32_NetworkAdapterConfiguration=lambda IPEnabled=True: list(nics))
    monkeypatch.setitem(sys.modules, "wmi", fake)
    monkeypatch.setattr(LP, "_iphlp_default_route",
                        lambda lib=None: LP._DefaultRoute("192.168.1.1", 0, 99, 7))
    d = LP.network_details()
    assert (d.adapter, d.ipv4, d.gateway) == ("Ethernet", ("192.168.1.20",), "192.168.1.1")


def test_the_route_carries_its_interface_index():
    tbl = _table([(AF_INET, 0, "192.168.1.1", 0, 10)])
    tbl.Table[0].InterfaceIndex = 7
    assert LP._iphlp_default_route(_FakeLib(tbl)).if_index == 7


def test_the_real_route_table_reads_without_wmi():
    """Live, read-only: on the CI box / dev machine the real table parses (whatever it holds)."""
    gw = LP._iphlp_default_gateway()
    assert gw is None or socket.inet_aton(gw)


# --- the startup path leaves wmi / win32com.client unloaded ---------------------------------------

def _run(code: str, timeout: float = 120):
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=SRC, capture_output=True,
                          text=True, timeout=timeout)


def test_the_startup_path_imports_neither_wmi_nor_win32com_client():
    r = _run("""
        import sys
        sys.path.insert(0, ".")
        import monitor                                    # src/monitor.py: main() is not run
        import netspeedtray.views.widget.main
        import netspeedtray.core.monitor_thread, netspeedtray.core.latency_probe as LP
        import netspeedtray.core.connection_check, netspeedtray.views.monitor.connection_check
        import netspeedtray.utils.power_utils as pu, netspeedtray.utils.network_utils
        pu.read_power_state(); pu.has_battery()
        LP.default_gateway_ip()                           # the probe's lookup: no WMI either
        print("LOADED", sorted(m for m in ("wmi", "win32com.client") if m in sys.modules))
    """)
    assert r.returncode == 0, r.stderr
    assert "LOADED []" in r.stdout, r.stdout + r.stderr


def test_a_desktop_battery_poll_never_imports_wmi(monkeypatch):
    from netspeedtray.core import monitor_thread as MT
    from netspeedtray.utils import power_utils
    monkeypatch.setattr(power_utils, "has_battery", lambda: False)
    monkeypatch.setattr(MT, "import_wmi", lambda: pytest.fail("wmi imported on a desktop"))
    t = MT.StatsMonitorThread.__new__(MT.StatsMonitorThread)
    t._battery_ac_line = None
    t._battery_cache = (0.0, None)
    t._battery_cadence_sec = 8.0
    t._wmi_battery = None
    t._battery_retry_at = 0.0
    assert t._poll_battery_power() is None
    assert t._wmi_battery is None    # not "permanently unavailable": a battery may still appear


# --- the COM apartment reason the GUI-thread preload existed for ----------------------------------

_WORKER_FIRST = """
    import sys, threading
    sys.path.insert(0, ".")
    def work():
        import pythoncom
        pythoncom.CoInitialize()
        try:
            {imp}
            c = wmi.WMI()
            list(c.Win32_OperatingSystem())
            vars(c).get("_classes_map", {{}}).clear()
            del c
        finally:
            pythoncom.CoUninitialize()
    t = threading.Thread(target=work); t.start(); t.join()
    import wmi
    print("OBJ", getattr(wmi, "obj", "missing") is None, "FLAG", isinstance(wmi.wbemFlagForwardOnly, int))
"""


def test_wmi_imported_first_on_a_worker_leaves_no_com_object_behind():
    fixed = _run(_WORKER_FIRST.format(
        imp="from netspeedtray.utils.com_utils import import_wmi; wmi = import_wmi()"))
    assert fixed.returncode == 0, fixed.stderr
    assert "OBJ True FLAG True" in fixed.stdout, fixed.stdout + fixed.stderr
    assert "releasing IUnknown" not in fixed.stdout + fixed.stderr
    # The control: a plain `import wmi` on that worker still reproduces pywin32's exit-time error,
    # so the assertion above can fail (if a pywin32 / wmi bump changes this, revisit com_utils).
    raw = _run(_WORKER_FIRST.format(imp="import wmi"))
    assert "releasing IUnknown" in raw.stdout + raw.stderr, raw.stdout + raw.stderr


def test_import_wmi_honours_a_missing_module(monkeypatch):
    from netspeedtray.utils import com_utils
    monkeypatch.setitem(sys.modules, "wmi", None)
    with pytest.raises(ImportError):
        com_utils.import_wmi()
    fake = types.ModuleType("wmi")
    monkeypatch.setitem(sys.modules, "wmi", fake)
    assert com_utils.import_wmi() is fake


def test_a_half_imported_win32com_client_is_not_handed_out(monkeypatch):
    """Two workers' first use at once: while one is still running win32com.client's module body (it
    is in sys.modules from the start), the other must wait for the finished module, not get a
    half-built one without GetObject."""
    import importlib.machinery
    import threading
    from netspeedtray.utils import com_utils
    half = types.ModuleType("win32com.client")
    half.__spec__ = importlib.machinery.ModuleSpec("win32com.client", None)
    half.__spec__._initializing = True
    monkeypatch.setitem(sys.modules, "win32com.client", half)
    got = []
    with com_utils._lock:                       # the other worker, mid-import
        t = threading.Thread(target=lambda: got.append(com_utils.win32com_client()), daemon=True)
        t.start()
        t.join(0.3)
        assert got == [], "a half-imported win32com.client was handed out"
        half.__spec__._initializing = False     # that import finishes
    t.join(5)
    assert got and got[0] is not None
    # A finished import takes the fast path, no lock.
    with com_utils._lock:
        assert com_utils.win32com_client() is half
