"""The default band display survives unavailable WinRT without querying SSID data."""
import ctypes
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from netspeedtray.utils import network_utils as nu


@pytest.fixture
def missing_winrt(monkeypatch):
    monkeypatch.setattr(nu.sys, "platform", "win32")
    monkeypatch.setattr(nu, "get_primary_interface_name", lambda: None)
    monkeypatch.setattr(nu, "_get_network_information", Mock(side_effect=ImportError("missing")))


@pytest.mark.parametrize("include_ssid", [False, True])
def test_band_fallback_does_not_require_winrt(missing_winrt, monkeypatch, include_ssid):
    identity = nu.NetworkIdentity(band="5G", is_wireless=True, connected=True)
    fallback = Mock(return_value=identity)
    monkeypatch.setattr(nu, "_native_band_identity", fallback)
    assert nu.get_connected_network_identity(include_ssid=include_ssid) == identity
    fallback.assert_called_once_with()


def test_ssid_only_does_not_query_native_wlan(missing_winrt, monkeypatch):
    fallback = Mock()
    monkeypatch.setattr(nu, "_native_band_identity", fallback)
    assert nu.get_connected_network_identity(include_band=False) == nu.NetworkIdentity()
    fallback.assert_not_called()


def native_api(monkeypatch, states, *, enum_result=0):
    class InterfaceList(ctypes.Structure):
        _fields_ = [("count", ctypes.c_uint32), ("index", ctypes.c_uint32),
                    ("interfaces", nu._WLAN_INTERFACE_INFO * len(states))]
    entries = InterfaceList()
    entries.count = len(states)
    for index, state in enumerate(states):
        entries.interfaces[index].InterfaceGuid = nu._GUID.from_buffer_copy(UUID(int=index + 1).bytes_le)
        entries.interfaces[index].isState = state

    def open_handle(_version, _reserved, _negotiated, handle):
        handle._obj.value = 123
        return 0

    def enumerate_interfaces(_handle, _reserved, result):
        result._obj.value = ctypes.addressof(entries)
        return enum_result

    api = SimpleNamespace(WlanOpenHandle=Mock(side_effect=open_handle),
                          WlanEnumInterfaces=Mock(side_effect=enumerate_interfaces),
                          WlanFreeMemory=Mock(), WlanCloseHandle=Mock())
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(wlanapi=api), raising=False)
    monkeypatch.setattr(nu, "get_primary_interface_name", lambda: None)
    return api, entries


def test_native_band_selects_connected_interface_past_eighth_entry(monkeypatch):
    api, entries = native_api(monkeypatch, [4] * 8 + [1])
    channel_reader = Mock(return_value="5G")
    monkeypatch.setattr(nu, "_get_wifi_band", channel_reader)
    assert nu._native_band_identity() == nu.NetworkIdentity(band="5G", is_wireless=True, connected=True)
    channel_reader.assert_called_once_with(UUID(int=9))
    assert entries.count == 9  # Keep the backing buffer alive through the native read.
    api.WlanFreeMemory.assert_called_once()
    api.WlanCloseHandle.assert_called_once()


@pytest.mark.parametrize("states,enum_result", [([4], 0), ([1], 5), ([], 0)])
def test_native_enumeration_failure_or_disconnect_releases_resources(monkeypatch, states, enum_result):
    api, entries = native_api(monkeypatch, states, enum_result=enum_result)
    channel_reader = Mock()
    monkeypatch.setattr(nu, "_get_wifi_band", channel_reader)
    assert nu._native_band_identity() == nu.NetworkIdentity()
    assert entries.count == len(states)
    channel_reader.assert_not_called()
    api.WlanFreeMemory.assert_called_once()
    api.WlanCloseHandle.assert_called_once()


def test_native_open_failure_does_not_free_or_close_unopened_handle(monkeypatch):
    api, entries = native_api(monkeypatch, [1])
    api.WlanOpenHandle.side_effect = None
    api.WlanOpenHandle.return_value = 5
    assert nu._native_band_identity() == nu.NetworkIdentity()
    assert entries.count == 1
    api.WlanEnumInterfaces.assert_not_called()
    api.WlanFreeMemory.assert_not_called()
    api.WlanCloseHandle.assert_not_called()
