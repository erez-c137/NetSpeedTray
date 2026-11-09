"""Platform-independent regressions for the SSID-only reader and event cache.

Keep these outside tests/unit: that suite's global fixtures require Windows DLLs.
No real network, WinRT activation, privacy settings, or GUI are touched here.
"""
import ctypes
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from netspeedtray.utils import network_utils as nu
from netspeedtray.utils import network_identity_monitor as nm


def profile(ssid="家里的 Wi-Fi", *, wireless=True, level=3, adapter=None):
    return SimpleNamespace(
        is_wlan_connection_profile=wireless,
        get_network_connectivity_level=Mock(return_value=level),
        wlan_connection_profile_details=SimpleNamespace(get_connected_ssid=Mock(return_value=ssid)),
        network_adapter=SimpleNamespace(network_adapter_id=adapter or UUID(int=1)),
    )


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(nu.sys, "platform", "win32")
    monkeypatch.setattr(nu, "get_primary_interface_name", lambda: None)
    band = Mock(return_value="5G")
    monkeypatch.setattr(nu, "_get_wifi_band", band)
    info = SimpleNamespace(get_internet_connection_profile=Mock(return_value=None),
                           get_connection_profiles=Mock(return_value=[]))
    monkeypatch.setattr(nu, "_get_network_information", lambda: info)
    return info, band


def test_ssid_only_preserves_unicode_and_never_queries_wlan(windows):
    info, band = windows
    wifi = profile()
    info.get_internet_connection_profile.return_value = wifi
    result = nu.get_connected_network_identity(include_band=False)
    assert result == nu.NetworkIdentity(name="家里的 Wi-Fi", is_wireless=True, connected=True)
    wifi.wlan_connection_profile_details.get_connected_ssid.assert_called_once_with()
    band.assert_not_called()
    info.get_connection_profiles.assert_not_called()


def test_band_only_never_reads_ssid(windows):
    info, band = windows
    wifi = profile()
    info.get_internet_connection_profile.return_value = wifi
    result = nu.get_connected_network_identity(include_ssid=False)
    assert result.name is None and result.band == "5G"
    wifi.wlan_connection_profile_details.get_connected_ssid.assert_not_called()
    band.assert_called_once_with(wifi.network_adapter.network_adapter_id)


def test_both_fields_refer_to_same_adapter(windows):
    info, band = windows
    wifi = profile(adapter=UUID(int=42))
    info.get_internet_connection_profile.return_value = wifi
    assert nu.get_connected_network_identity().name == "家里的 Wi-Fi"
    band.assert_called_once_with(UUID(int=42))


@pytest.mark.parametrize("primary", [None, profile("VPN", wireless=False), profile("Ethernet", wireless=False)])
def test_wifi_remains_visible_without_internet_or_behind_vpn(windows, primary):
    info, _ = windows
    info.get_internet_connection_profile.return_value = primary
    info.get_connection_profiles.return_value = [profile("old", level=0), profile("local", level=1)]
    assert nu.get_connected_network_identity().name == "local"


def test_no_stale_ssid_after_disconnect(windows):
    info, _ = windows
    info.get_connection_profiles.return_value = [profile("remembered", level=0)]
    assert nu.get_connected_network_identity() == nu.NetworkIdentity()


def test_wired_fallback(windows, monkeypatch):
    monkeypatch.setattr(nu, "get_primary_interface_name", lambda: "Ethernet")
    assert nu.get_connected_network_identity() == nu.NetworkIdentity(name="Ethernet", connected=True)


@pytest.mark.parametrize("error", [PermissionError("denied"), RuntimeError("unavailable")])
def test_ssid_error_never_triggers_location_prompt_or_legacy_fallback(windows, error):
    info, _ = windows
    wifi = profile()
    wifi.wlan_connection_profile_details.get_connected_ssid.side_effect = error
    info.get_internet_connection_profile.return_value = wifi
    result = nu.get_connected_network_identity()
    assert result.name is None and result.band == "5G"
    assert result.is_wireless and result.connected and not result.ssid_blocked


def test_band_error_does_not_hide_ssid(windows):
    info, band = windows
    info.get_internet_connection_profile.return_value = profile()
    band.side_effect = RuntimeError("WLAN unavailable")
    result = nu.get_connected_network_identity()
    assert result.name == "家里的 Wi-Fi" and result.band is None


@pytest.mark.parametrize("details", [None, SimpleNamespace(get_connected_ssid=Mock(return_value=""))])
def test_missing_details_or_empty_name_is_safe(windows, details):
    info, _ = windows
    wifi = profile()
    wifi.wlan_connection_profile_details = details
    wifi.network_adapter = None
    info.get_internet_connection_profile.return_value = wifi
    assert nu.get_connected_network_identity() == nu.NetworkIdentity(is_wireless=True, connected=True)


def test_missing_winrt_never_calls_native_ssid_api(windows, monkeypatch):
    monkeypatch.setattr(nu, "_get_network_information", Mock(side_effect=ImportError("missing")))
    assert nu.get_connected_network_identity() == nu.NetworkIdentity()
    windows[1].assert_not_called()


def test_native_band_uses_only_channel_opcode_and_frees_resources(monkeypatch):
    channel = ctypes.c_uint32(36)
    calls = []
    def open_handle(_version, _reserved, _negotiated, handle):
        handle._obj.value = 123
        return 0
    def query(_handle, guid, opcode, _reserved, size, data, _type):
        assert bytes(guid._obj) == UUID(int=42).bytes_le
        calls.append(opcode)
        size._obj.value = ctypes.sizeof(channel)
        data._obj.value = ctypes.addressof(channel)
        return 0
    api = SimpleNamespace(WlanOpenHandle=Mock(side_effect=open_handle),
                          WlanQueryInterface=Mock(side_effect=query),
                          WlanFreeMemory=Mock(), WlanCloseHandle=Mock())
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(wlanapi=api), raising=False)
    assert nu._get_wifi_band(UUID(int=42)) == "5G"
    assert calls == [8]
    api.WlanFreeMemory.assert_called_once()
    api.WlanCloseHandle.assert_called_once()


@pytest.mark.parametrize("rc,size", [(5, 4), (0, 0)])
def test_native_band_failure_releases_returned_buffer(monkeypatch, rc, size):
    channel = ctypes.c_uint32(36)
    def query(_h, _g, opcode, _r, sz, data, _t):
        assert opcode == 8
        sz._obj.value = size
        data._obj.value = ctypes.addressof(channel)
        return rc
    api = SimpleNamespace(WlanOpenHandle=Mock(return_value=0), WlanQueryInterface=query,
                          WlanFreeMemory=Mock(), WlanCloseHandle=Mock())
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(wlanapi=api), raising=False)
    assert nu._get_wifi_band(UUID(int=1)) is None
    api.WlanFreeMemory.assert_called_once()
    api.WlanCloseHandle.assert_called_once()


def make_monitor():
    now = [0.0]
    reader = Mock(return_value=nu.NetworkIdentity(name="first", connected=True))
    unsubscribe = Mock()
    subscribe = Mock(return_value=unsubscribe)
    monitor = nm.NetworkIdentityMonitor(reader=reader, subscribe=subscribe, clock=lambda: now[0])
    return monitor, reader, subscribe, unsubscribe, now


def test_unchanged_ticks_do_not_repeat_any_api_read():
    monitor, reader, subscribe, _, now = make_monitor()
    first = monitor.read("ssid")
    for second in range(1, 60):
        now[0] = float(second)
        assert monitor.read("ssid") is first
    reader.assert_called_once_with(include_ssid=True, include_band=False)
    subscribe.assert_called_once()
    now[0] = 60
    monitor.read("ssid")
    assert reader.call_count == 2


def test_band_only_refreshes_every_five_seconds_without_reading_ssid():
    monitor, reader, _, _, now = make_monitor()
    reader.return_value = nu.NetworkIdentity(band="5G", is_wireless=True, connected=True)
    assert monitor.read("band").band == "5G"
    now[0] = 4.9
    monitor.read("band")
    assert reader.call_count == 1
    now[0] = 5.0
    reader.return_value = nu.NetworkIdentity(band="2.4G", is_wireless=True, connected=True)
    assert monitor.read("band").band == "2.4G"
    reader.assert_called_with(include_ssid=False, include_band=True)


def test_combined_mode_refreshes_band_without_repeating_ssid():
    monitor, reader, _, _, now = make_monitor()
    reader.return_value = nu.NetworkIdentity(name="home", band="5G", is_wireless=True, connected=True)
    assert monitor.read("both").name == "home"
    now[0] = 5.0
    reader.return_value = nu.NetworkIdentity(band="5G", is_wireless=True, connected=True)
    updated = monitor.read("both")
    assert (updated.name, updated.band) == ("home", "5G")
    reader.assert_called_with(include_ssid=False, include_band=True)


@pytest.mark.parametrize("old_band,new_band", [("5G", "2.4G"), (None, "5G"), ("5G", None)])
def test_changed_band_refreshes_ssid_to_avoid_mixing_networks(old_band, new_band):
    monitor, reader, _, _, now = make_monitor()
    reader.return_value = nu.NetworkIdentity(name="first", band=old_band, is_wireless=True, connected=True)
    monitor.read("both")
    now[0] = 5.0
    reader.side_effect = [
        nu.NetworkIdentity(band=new_band, is_wireless=True, connected=True),
        nu.NetworkIdentity(name="second", band=new_band, is_wireless=True, connected=True),
    ]
    result = monitor.read("both")
    assert (result.name, result.band) == ("second", new_band)
    reader.assert_called_with(include_ssid=True, include_band=True)


def test_combined_mode_clears_cached_ssid_when_band_refresh_finds_disconnect():
    monitor, reader, _, _, now = make_monitor()
    reader.return_value = nu.NetworkIdentity(name="home", band="5G", is_wireless=True, connected=True)
    monitor.read("both")
    now[0] = 5.0
    reader.return_value = nu.NetworkIdentity()
    assert monitor.read("both").name is None


def test_combined_mode_refreshes_ssid_when_switching_from_wired_to_wifi():
    monitor, reader, _, _, now = make_monitor()
    reader.return_value = nu.NetworkIdentity(name="Ethernet", connected=True)
    monitor.read("both")
    now[0] = 5.0
    reader.side_effect = [
        nu.NetworkIdentity(band="5G", is_wireless=True, connected=True),
        nu.NetworkIdentity(name="home", band="5G", is_wireless=True, connected=True),
    ]
    assert monitor.read("both").name == "home"
    reader.assert_called_with(include_ssid=True, include_band=True)


def test_combined_mode_replaces_wifi_name_when_switching_to_wired():
    monitor, reader, _, _, now = make_monitor()
    reader.return_value = nu.NetworkIdentity(name="home", band="5G", is_wireless=True, connected=True)
    monitor.read("both")
    now[0] = 5.0
    reader.return_value = nu.NetworkIdentity(name="Ethernet", connected=True)
    assert monitor.read("both").name == "Ethernet"


def test_notifications_coalesce_and_switch_network():
    monitor, reader, subscribe, _, _ = make_monitor()
    monitor.read()
    callback = subscribe.call_args.args[0]
    for _ in range(10):
        callback(None)
    reader.return_value = nu.NetworkIdentity(name="second", connected=True)
    assert monitor.read().name == "second"
    assert reader.call_count == 2
    monitor.read()
    assert reader.call_count == 2


def test_notification_during_read_is_not_lost():
    monitor, reader, subscribe, _, _ = make_monitor()
    monitor.read()
    callback = subscribe.call_args.args[0]
    callback(None)
    reader.side_effect = lambda **_kwargs: (callback(None), nu.NetworkIdentity())[1]
    monitor.read()
    reader.side_effect = None
    monitor.read()
    assert reader.call_count == 3


def test_mode_change_refreshes_requested_fields_immediately():
    monitor, reader, _, _, _ = make_monitor()
    monitor.read("band")
    reader.assert_called_with(include_ssid=False, include_band=True)
    monitor.read("ssid")
    reader.assert_called_with(include_ssid=True, include_band=False)
    assert reader.call_count == 2


def test_disable_cleanup_idempotent_and_reenable_fresh():
    monitor, reader, subscribe, unsubscribe, _ = make_monitor()
    monitor.read()
    monitor.close()
    monitor.close()
    unsubscribe.assert_called_once()
    reader.return_value = nu.NetworkIdentity()
    assert monitor.read() == nu.NetworkIdentity()
    assert subscribe.call_count == 2 and reader.call_count == 2


def test_subscription_failure_retries_only_at_safety_interval():
    monitor, reader, subscribe, _, now = make_monitor()
    subscribe.side_effect = RuntimeError("service unavailable")
    for second in range(60):
        now[0] = second
        monitor.read("ssid")
    assert subscribe.call_count == 1 and reader.call_count == 1
    now[0] = 60
    monitor.read("ssid")
    assert subscribe.call_count == 2 and reader.call_count == 2


def test_failed_refresh_does_not_keep_old_ssid():
    monitor, reader, subscribe, _, _ = make_monitor()
    assert monitor.read().name == "first"
    reader.side_effect = RuntimeError("service down")
    subscribe.call_args.args[0](None)
    assert monitor.read() == nu.NetworkIdentity()


def test_cleanup_failure_still_clears_cache():
    monitor, _, _, unsubscribe, _ = make_monitor()
    monitor.read()
    unsubscribe.side_effect = RuntimeError("already removed")
    monitor.close()
    assert monitor._identity is None
    monitor.close()
    unsubscribe.assert_called_once()


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(nm.sys, "platform", "win32")
    runtime = SimpleNamespace(ApartmentType=SimpleNamespace(MULTI_THREADED=0),
                              init_apartment=Mock(), uninit_apartment=Mock())
    monkeypatch.setitem(sys.modules, "winrt.runtime", runtime)
    info = SimpleNamespace(add_network_status_changed=Mock(return_value="token"),
                           remove_network_status_changed=Mock())
    monkeypatch.setattr(nm, "_get_network_information", lambda: info)
    return runtime, info


def test_subscription_initializes_and_revokes_on_owner_thread(runtime):
    rt, info = runtime
    callback = Mock()
    close = nm._subscribe_network_changes(callback)
    rt.init_apartment.assert_called_once_with(0)
    info.add_network_status_changed.assert_called_once_with(callback)
    rt.uninit_apartment.assert_not_called()
    close()
    info.remove_network_status_changed.assert_called_once_with("token")
    rt.uninit_apartment.assert_called_once()


def test_subscribe_failure_balances_successful_apartment_init(runtime):
    rt, info = runtime
    info.add_network_status_changed.side_effect = RuntimeError("failure")
    with pytest.raises(RuntimeError):
        nm._subscribe_network_changes(Mock())
    rt.uninit_apartment.assert_called_once()


def test_failed_apartment_init_is_not_uninitialized(runtime):
    rt, info = runtime
    rt.init_apartment.side_effect = RuntimeError("RPC_E_CHANGED_MODE")
    with pytest.raises(RuntimeError):
        nm._subscribe_network_changes(Mock())
    rt.uninit_apartment.assert_not_called()
    info.add_network_status_changed.assert_not_called()


def test_revoke_failure_still_uninitializes(runtime):
    rt, info = runtime
    close = nm._subscribe_network_changes(Mock())
    info.remove_network_status_changed.side_effect = RuntimeError("failure")
    with pytest.raises(RuntimeError):
        close()
    rt.uninit_apartment.assert_called_once()


def test_identity_paths_never_call_location_gated_apis():
    # Guard against reintroducing a fallback that silently undoes this fix.
    import ast
    for module in (nu, nm):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not names.intersection({"WlanScan", "WlanGetAvailableNetworkList",
                                       "WlanGetNetworkBssList", "get_lan_identifiers"})
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "WlanQueryInterface"):
                assert isinstance(node.args[2], ast.Name)
                assert node.args[2].id == "_WLAN_OPCODE_CHANNEL_NUMBER"
        assert nu._WLAN_OPCODE_CHANNEL_NUMBER == 8
        assert "_WLAN_OPCODE_CURRENT_CONNECTION" not in {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


@pytest.mark.skipif(sys.platform != "win32", reason="Real monitor module requires pywin32")
@pytest.mark.parametrize("init_fails", [False, True])
def test_monitor_com_initialization_is_balanced(monkeypatch, init_fails):
    from PyQt6.QtCore import QThread
    from netspeedtray.core.monitor_thread import StatsMonitorThread
    thread = StatsMonitorThread.__new__(StatsMonitorThread)
    QThread.__init__(thread)
    thread.logger = Mock()
    com = SimpleNamespace(COINIT_MULTITHREADED=0, CoInitializeEx=Mock(), CoUninitialize=Mock())
    if init_fails:
        com.CoInitializeEx.side_effect = RuntimeError("initialization failed")
    monkeypatch.setitem(sys.modules, "pythoncom", com)
    thread._init_com()
    com.CoInitializeEx.assert_called_once_with(0)
    assert thread._com_initialized is not init_fails
    thread._cleanup_com()
    thread._cleanup_com()
    assert com.CoUninitialize.call_count == (0 if init_fails else 1)


@pytest.mark.skipif(sys.platform != "win32", reason="Real monitor module requires pywin32")
def test_monitor_closes_winrt_before_com_cleanup(monkeypatch):
    from PyQt6.QtCore import QThread
    from netspeedtray.core import monitor_thread
    thread = monitor_thread.StatsMonitorThread.__new__(monitor_thread.StatsMonitorThread)
    QThread.__init__(thread)
    thread.logger = Mock()
    thread._is_running = False
    order = []
    com = SimpleNamespace(COINIT_MULTITHREADED=0, CoInitializeEx=Mock(),
                          CoUninitialize=lambda: order.append("com"))
    monkeypatch.setitem(sys.modules, "pythoncom", com)
    monkeypatch.setattr(monitor_thread, "is_rdp_session", lambda: False)
    monkeypatch.setattr(monitor_thread.psutil, "cpu_percent", Mock())
    thread._network_identity_monitor = SimpleNamespace(close=lambda: order.append("winrt"))
    for name in ("_cleanup_gpu_query", "_cleanup_thermal_query", "_cleanup_power_query", "_cleanup_ohm_wmi"):
        setattr(thread, name, Mock())
    thread.run()
    assert order == ["winrt", "com"]
