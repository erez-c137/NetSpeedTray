"""
Network-related utility functions for NetSpeedTray.

This module provides functions for discovering network interfaces, mapping
GUIDs to friendly names, and determining the primary internet-facing interface.
"""

import ctypes
import ipaddress
import logging
import socket
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import psutil

from netspeedtray import constants

logger = logging.getLogger("NetSpeedTray.NetworkUtils")

# Only query the channel number. Never request current_connection (opcode 7): it
# includes BSSID and is location-gated on recent Windows versions.
_WLAN_OPCODE_CHANNEL_NUMBER = 8


def guid_to_friendly_name(guid: str) -> Optional[str]:
    """
    On Windows, maps a network interface GUID (e.g., '{7BB8DA25-E8B9-44C0-8BC8-09F08D0BC446}')
    to its friendly name (e.g., 'Ethernet') using WMI.
    Returns the friendly name if found, else None.
    """
    if sys.platform != "win32":
        return None
    try:
        import wmi
        c = wmi.WMI()
        for iface in c.Win32_NetworkAdapter():
            # NetConnectionID is the friendly name, GUID is in the GUID property
            if hasattr(iface, 'GUID') and iface.GUID and iface.GUID.lower() == guid.strip('{}').lower():
                return getattr(iface, 'NetConnectionID', None)
    except ImportError:
        logger.warning("The 'wmi' module is not installed; cannot map GUID to friendly name.")
    except Exception as e:
        logger.error(f"Error mapping GUID to friendly name: {e}", exc_info=True)
    return None

# --- transient-state logging ------------------------------------------------------------------
#
# `get_primary_interface_name()` sits on the per-poll path. Logging a warning on every failed
# attempt turned a normal condition - a laptop with no route - into a flood: one support bundle
# arrived with 137,497 of its 139,638 lines from this one call, which rotated away every genuinely
# useful entry and made the report undiagnosable (#260). Log the EDGE instead of the state.
_unreachable_since: Optional[float] = None


def _note_unreachable(err: BaseException) -> None:
    """First failure warns; repeats drop to DEBUG."""
    global _unreachable_since
    if _unreachable_since is None:
        _unreachable_since = time.monotonic()
        logger.warning("No primary interface - the network looks unreachable (%s). "
                       "Further attempts log at DEBUG until it comes back.", err)
    else:
        logger.debug("Primary interface still unreachable (%s).", err)


def _note_reachable() -> None:
    """Recovery closes the loop, so a reader can see how long the gap was."""
    global _unreachable_since
    if _unreachable_since is not None:
        gap = time.monotonic() - _unreachable_since
        _unreachable_since = None
        logger.info("Primary interface reachable again after %.0fs.", gap)



def _route_source_address(family: int, target: tuple) -> Optional[str]:
    """The local address the OS would use to reach `target` (UDP connect - no packet is sent).
    Raises OSError when the family has no route."""
    with socket.socket(family, socket.SOCK_DGRAM) as s:
        s.settimeout(0.1)
        s.connect(target)
        local_ip = s.getsockname()[0]
    if local_ip in ("0.0.0.0", "::"):
        return None
    return local_ip


def _same_address(a: str, b: str) -> bool:
    """Address equality that survives IPv6 spelling differences ('2001:DB8::1' vs
    '2001:db8:0:0:0:0:0:1') and a '%scope' suffix on either side."""
    try:
        return ipaddress.ip_address(a.split("%", 1)[0]) == ipaddress.ip_address(b.split("%", 1)[0])
    except ValueError:
        return a == b


def get_primary_interface_name() -> Optional[str]:
    """
    Determines the name of the network interface associated with the default gateway.

    This is the interface most likely responsible for the main internet connection.
    It works by creating a temporary UDP socket to a public DNS server (without
    sending any data) and checking which local IP address the OS chooses. IPv4 is
    tried first, then IPv6 - an IPv6-only network has no IPv4 default route, and
    resolving nothing there left auto mode metering zero bytes indefinitely
    (data-usage accuracy audit, finding 2).

    Returns:
        The name of the primary interface (e.g., "Wi-Fi"), or None if it
        cannot be determined.
    """
    probes = (
        (socket.AF_INET, constants.network.interface.ROUTE_PROBE_V4),
        (socket.AF_INET6, constants.network.interface.ROUTE_PROBE_V6),
    )
    last_error: Optional[BaseException] = None
    tried_ips = []
    try:
        for family, target in probes:
            try:
                local_ip = _route_source_address(family, target)
            except (OSError, socket.gaierror) as e:
                last_error = e
                continue
            if local_ip is None:
                continue
            tried_ips.append(local_ip)

            # Find the interface that has this local IP address
            for iface_name, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == family and _same_address(addr.address, local_ip):
                        _note_reachable()
                        logger.debug("Determined primary interface: '%s' with IP %s", iface_name, local_ip)
                        return iface_name
    except Exception as e:
        logger.error(f"Unexpected error determining primary interface: {e}", exc_info=True)
        return None

    if not tried_ips:
        if last_error is not None:
            # Having no route is a NORMAL, recurring state (offline, VPN reconnecting, resuming from
            # sleep), not an event worth a line per attempt. Log the EDGE: once on the way in, once on
            # the way out. See _note_unreachable().
            _note_unreachable(last_error)
        else:
            logger.warning("Could not determine a specific local IP; got an unspecified address.")
        return None

    logger.warning(f"Could not find an interface matching the local IP {tried_ips[-1]}.")
    return None


# --- Network identity (v2.1): Wi-Fi band / SSID on the widget --------------------------------
#
# SSIDs are read through WinRT WlanConnectionProfileDetails.GetConnectedSsid,
# Microsoft's recommended API when only the connected name is needed. Do not fall
# back to WlanQueryInterface(current_connection), scanning, or GetLanIdentifiers.
# https://learn.microsoft.com/windows/win32/nativewifi/wi-fi-access-location-changes


@dataclass(frozen=True)
class NetworkIdentity:
    """The connected network's glanceable identity, for the widget's identity element.

    Attributes:
        name:         SSID (Wi-Fi) or the active connection's friendly name (wired); None if unknown
                      or unavailable from the connected profile.
        band:         "2.4G" / "5G" / "6G" for Wi-Fi, else None. Available without Location permission.
        is_wireless:  True if the active interface is a connected WLAN adapter.
        connected:    True if there is an active internet-facing interface at all.
    """
    name: Optional[str] = None
    band: Optional[str] = None
    is_wireless: bool = False
    connected: bool = False


class _GUID(ctypes.Structure):
    # Fixed-width fields also keep the ABI testable on non-Windows hosts.
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


class _WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", _GUID),
                ("strInterfaceDescription", ctypes.c_uint16 * 256),
                ("isState", ctypes.c_uint32)]


class _WLAN_INTERFACE_INFO_LIST_HEADER(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", ctypes.c_uint32), ("dwIndex", ctypes.c_uint32)]


# SSID display (v2.1 item 2). SSIDs can be up to 32 chars; cap the on-widget length and ellipsize so a
# long name can't blow out the taskbar. The width reserve tracks the ACTUAL (truncated) string, so a
# short SSID stays tight - only a network change (rare) re-lays out.
MAX_SSID_DISPLAY_CHARS = 16


def truncate_ssid(name: Optional[str], max_chars: int = MAX_SSID_DISPLAY_CHARS) -> Optional[str]:
    """Cap an SSID / connection name to `max_chars`, ellipsizing with '…'. None/empty -> None."""
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    return name if len(name) <= max_chars else name[:max_chars - 1] + "…"


# Band presentation (v2.1 "next level"). A band is "degraded" if it's the slow 2.4 GHz one - that's
# the thing worth alerting on ("did my PC silently rejoin 2.4 GHz?"). Colors read on light+dark taskbars.
BAND_ALERT_SET = frozenset({"2.4G"})            # bands treated as degraded / worth a warning
BAND_COLORS = {"2.4G": "#FFB300", "5G": "#66BB6A", "6G": "#42A5F5"}  # amber / green / blue
BAND_ALERT_COLOR = "#FF7043"                     # deep-orange warning used by alert_only mode


def resolve_band_presentation(band: Optional[str], band_display: str) -> "tuple[Optional[str], Optional[str], bool]":
    """Given the connected band and the display mode, return (text_to_draw, color_hex, solid).

    The band renders as a rounded pill/badge. `solid` picks the pill treatment: a SOLID filled badge
    (the attention-grabbing alert) vs an OUTLINE badge (calm, everyday).

    band_display:
      - "always"     -> show the band, OUTLINE pill in the default text color (color None, solid False).
      - "colored"    -> always show, OUTLINE pill tinted by band (2.4G amber / 5G green / 6G blue).
      - "alert_only" -> show ONLY on a degraded band (2.4G) as a SOLID warning pill; otherwise
                        (None, None, False) so a clean widget means "you're on a good band".
    A falsy band (wired / unknown / Location-off) always yields (None, None, False).
    """
    if not band:
        return None, None, False
    if band_display == "alert_only":
        if band in BAND_ALERT_SET:
            return band, BAND_ALERT_COLOR, True
        return None, None, False
    if band_display == "colored":
        return band, BAND_COLORS.get(band), False
    return band, None, False  # "always" (neutral outline)


def band_from_channel(channel: int) -> Optional[str]:
    """Map an 802.11 channel number to a coarse band tag.

    Channels 1-14 are labeled 2.4G and 32-177 are labeled 5G. Because 6 GHz channel numbers
    overlap these ranges, a channel number alone cannot distinguish all 6 GHz connections and
    may label them as 2.4G or 5G. This function does not query the BSS center frequency.
    Returns None for channels outside these ranges.
    """
    if 1 <= channel <= 14:
        return "2.4G"
    if 32 <= channel <= 177:
        return "5G"
    return None


def _wired_identity() -> NetworkIdentity:
    """Fallback identity for a wired / non-Wi-Fi link: the active interface friendly name."""
    name = get_primary_interface_name()
    return NetworkIdentity(name=name, band=None, is_wireless=False, connected=name is not None)


def _get_network_information():
    # Lazy import: these Windows-only packages are not available on Linux, and
    # identity sensing is optional. The frozen build explicitly bundles them.
    from winrt.windows.networking.connectivity import NetworkInformation
    return NetworkInformation


def _get_connected_wifi_profile(network_information):
    """Prefer the internet WLAN profile; still show Wi-Fi behind a VPN or offline."""
    primary = network_information.get_internet_connection_profile()
    if primary is not None and primary.is_wlan_connection_profile:
        if int(primary.get_network_connectivity_level()) != 0:
            return primary
    # get_connection_profiles can contain disconnected profiles. Never display a
    # remembered SSID after disconnect, or mistake a VPN profile name for an SSID.
    for profile in network_information.get_connection_profiles():
        if (profile.is_wlan_connection_profile
                and int(profile.get_network_connectivity_level()) != 0):
            return profile
    return None


def _get_wifi_band(adapter_id) -> Optional[str]:
    """Read only the location-free channel opcode, for the SAME adapter as the SSID."""
    handle = wintypes.HANDLE()
    data = ctypes.c_void_p()
    opened = False
    try:
        guid = _GUID.from_buffer_copy(UUID(str(adapter_id)).bytes_le)
        wlanapi = ctypes.windll.wlanapi
        negotiated = wintypes.DWORD()
        if wlanapi.WlanOpenHandle(2, None, ctypes.byref(negotiated), ctypes.byref(handle)) != 0:
            return None
        opened = True
        size = wintypes.DWORD()
        opcode_type = wintypes.DWORD()
        rc = wlanapi.WlanQueryInterface(
            handle, ctypes.byref(guid), _WLAN_OPCODE_CHANNEL_NUMBER, None,
            ctypes.byref(size), ctypes.byref(data), ctypes.byref(opcode_type))
        if rc == 0 and data and size.value >= ctypes.sizeof(ctypes.c_uint32):
            channel = ctypes.cast(data, ctypes.POINTER(ctypes.c_uint32)).contents.value
            return band_from_channel(channel)
    except Exception as e:
        logger.debug("Wi-Fi channel unavailable: %s", e)
    finally:
        if data:
            try:
                wlanapi.WlanFreeMemory(data)
            except Exception:
                pass
        if opened:
            try:
                wlanapi.WlanCloseHandle(handle, None)
            except Exception:
                pass
    return None


def _native_band_identity() -> NetworkIdentity:
    """Keep the band available when WinRT fails, without reading a native SSID.

    WlanEnumInterfaces exposes connection state and adapter GUIDs. The existing
    channel-only reader then reads the first connected adapter's band.
    """
    handle = wintypes.HANDLE()
    interfaces_ptr = ctypes.c_void_p()
    opened = False
    try:
        wlanapi = ctypes.windll.wlanapi
        negotiated = wintypes.DWORD()
        if wlanapi.WlanOpenHandle(2, None, ctypes.byref(negotiated), ctypes.byref(handle)) != 0:
            return _wired_identity()
        opened = True
        rc = wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(interfaces_ptr))
        if rc != 0 or not interfaces_ptr:
            return _wired_identity()
        header = ctypes.cast(interfaces_ptr, ctypes.POINTER(_WLAN_INTERFACE_INFO_LIST_HEADER)).contents
        address = interfaces_ptr.value + ctypes.sizeof(_WLAN_INTERFACE_INFO_LIST_HEADER)
        interfaces = (_WLAN_INTERFACE_INFO * header.dwNumberOfItems).from_address(address)
        for interface in interfaces:
            if interface.isState == 1:  # wlan_interface_state_connected
                adapter_id = UUID(bytes_le=bytes(interface.InterfaceGuid))
                return NetworkIdentity(band=_get_wifi_band(adapter_id), is_wireless=True, connected=True)
    except Exception as e:
        logger.debug("Native Wi-Fi band unavailable: %s", e)
    finally:
        if interfaces_ptr:
            try:
                wlanapi.WlanFreeMemory(interfaces_ptr)
            except Exception:
                pass
        if opened:
            try:
                wlanapi.WlanCloseHandle(handle, None)
            except Exception:
                pass
    return _wired_identity()


def get_connected_network_identity(*, include_ssid: bool = True,
                                   include_band: bool = True) -> NetworkIdentity:
    """Read a fresh connected profile without any location-gated Wi-Fi API.

    Call from the initialized monitoring thread, normally via NetworkIdentityMonitor.
    Only the SSID-only WinRT API reads the name; a missing dependency or failed read
    never falls back to opcode 7, scans, or location-permission prompts. A channel
    failure must not hide an otherwise valid SSID, and vice versa.
    """
    if sys.platform != "win32":
        return _wired_identity()
    try:
        profile = _get_connected_wifi_profile(_get_network_information())
    except Exception as e:
        logger.debug("Connected Wi-Fi profile unavailable: %s", e)
        return _native_band_identity() if include_band else _wired_identity()
    if profile is None:
        return _wired_identity()

    ssid = None
    if include_ssid:
        try:
            details = profile.wlan_connection_profile_details
            ssid = details.get_connected_ssid() if details is not None else None
            ssid = ssid or None
        except Exception as e:
            # An unavailable name is not evidence that Location permission is needed.
            logger.debug("Connected Wi-Fi name unavailable: %s", e)
    band = None
    if include_band:
        try:
            adapter = profile.network_adapter
            if adapter is not None:
                band = _get_wifi_band(adapter.network_adapter_id)
        except Exception as e:
            logger.debug("Connected Wi-Fi adapter unavailable: %s", e)
    return NetworkIdentity(name=ssid, band=band, is_wireless=True, connected=True)
