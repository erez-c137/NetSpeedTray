"""
Network-related utility functions for NetSpeedTray.

This module provides functions for discovering network interfaces, mapping
GUIDs to friendly names, and determining the primary internet-facing interface.
"""

import ctypes
import logging
import socket
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

import psutil

logger = logging.getLogger("NetSpeedTray.NetworkUtils")

# Windows error code returned by WlanQueryInterface for the SSID / BSS-scan opcodes when the
# Location privacy permission is off (Win11 24H2/25H2). The band (channel) opcode is NOT gated.
_ERROR_ACCESS_DENIED = 5
# WLAN_INTF_OPCODE values (wlanapi.h).
_WLAN_OPCODE_CURRENT_CONNECTION = 7   # SSID / profile — Location-GATED
_WLAN_OPCODE_CHANNEL_NUMBER = 8       # channel — Location-FREE


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


def get_primary_interface_name() -> Optional[str]:
    """
    Determines the name of the network interface associated with the default gateway.

    This is the interface most likely responsible for the main internet connection.
    It works by creating a temporary UDP socket to a public DNS server (without
    sending any data) and checking which local IP address the OS chooses.

    Returns:
        The name of the primary interface (e.g., "Wi-Fi"), or None if it
        cannot be determined.
    """
    local_ip = "0.0.0.0" # Initialize for logging in case of early exit
    try:
        # Create a UDP socket to a public IP (Google's DNS) to find the default route
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.1)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        
        if local_ip == "0.0.0.0":
            logger.warning("Could not determine a specific local IP; got 0.0.0.0.")
            return None

        # Find the interface that has this local IP address
        all_addrs = psutil.net_if_addrs()
        for iface_name, addrs in all_addrs.items():
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address == local_ip:
                    logger.debug(f"Determined primary interface: '{iface_name}' with IP {local_ip}")
                    return iface_name
                    
    except (OSError, socket.gaierror) as e:
        logger.warning(f"Could not determine primary interface due to network error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error determining primary interface: {e}", exc_info=True)
        return None
        
    logger.warning(f"Could not find an interface matching the local IP {local_ip}.")
    return None


# --- Network identity (v2.1): Wi-Fi band / SSID on the widget --------------------------------
#
# Design note (verified live on Win11 26200, 2026-07-03 — see releases/v2.1/KICKOFF.md §2):
# Windows 11 gates SSID retrieval and BSS scanning behind the Location privacy permission. The
# *band*, however, is derived from the channel number (WLAN_INTF_OPCODE_CHANNEL_NUMBER), which is
# Location-FREE. So band is the always-available default; SSID is opt-in and degrades cleanly to
# `ssid_blocked=True` (rather than a misleading blank) when Location is off.


@dataclass(frozen=True)
class NetworkIdentity:
    """The connected network's glanceable identity, for the widget's identity element.

    Attributes:
        name:         SSID (Wi-Fi) or the active connection's friendly name (wired); None if unknown
                      or, for Wi-Fi, blocked by the Location gate.
        band:         "2.4G" / "5G" / "6G" for Wi-Fi, else None. Available without Location permission.
        is_wireless:  True if the active interface is a connected WLAN adapter.
        ssid_blocked: True when the SSID could not be read because Windows Location permission is off
                      (the widget should surface an opt-in "enable Location" nudge, not a blank).
        connected:    True if there is an active internet-facing interface at all.
    """
    name: Optional[str] = None
    band: Optional[str] = None
    is_wireless: bool = False
    ssid_blocked: bool = False
    connected: bool = False


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", _GUID),
                ("strInterfaceDescription", wintypes.WCHAR * 256),
                ("isState", wintypes.DWORD)]


class _WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    # A flexible-array header; index InterfaceInfo[i] up to dwNumberOfItems (we size [8], plenty).
    _fields_ = [("dwNumberOfItems", wintypes.DWORD),
                ("dwIndex", wintypes.DWORD),
                ("InterfaceInfo", _WLAN_INTERFACE_INFO * 8)]


_WLAN_INTERFACE_STATE_CONNECTED = 1  # wlan_interface_state_connected


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

    2.4 GHz = channels 1-14; 5 GHz = 32-177. 6 GHz reuses 1-233 and therefore *collides* with the
    2.4 GHz range on channel number alone — the unambiguous 6 GHz discriminator is the BSS center
    frequency, which is Location-gated. So 2.4/5 are reliable Location-free; 6 GHz is best-effort
    only when Location is granted. Returns None for a non-positive/unknown channel.
    """
    if 1 <= channel <= 14:
        return "2.4G"
    if 32 <= channel <= 177:
        return "5G"
    return None


def _wired_identity() -> NetworkIdentity:
    """Fallback identity for a wired / non-Wi-Fi link: the active interface friendly name."""
    name = get_primary_interface_name()
    return NetworkIdentity(name=name, band=None, is_wireless=False,
                           ssid_blocked=False, connected=name is not None)


def get_connected_network_identity() -> NetworkIdentity:
    """Resolve the connected network's identity (band + SSID) for the widget.

    Never raises: any failure (no adapter, WLAN service down, ctypes error, non-Windows) degrades to
    a wired fallback or an empty ``NetworkIdentity``. Call on a slow (~5s) sub-poll — never on the
    per-paint or per-tick hot path.
    """
    if sys.platform != "win32":
        return _wired_identity()

    handle = wintypes.HANDLE()
    p_list = ctypes.POINTER(_WLAN_INTERFACE_INFO_LIST)()
    try:
        wlanapi = ctypes.windll.wlanapi
    except (OSError, AttributeError):
        # No WLAN API available on this SKU — treat as wired.
        return _wired_identity()

    try:
        negotiated = wintypes.DWORD()
        if wlanapi.WlanOpenHandle(2, None, ctypes.byref(negotiated), ctypes.byref(handle)) != 0:
            return _wired_identity()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"WlanOpenHandle failed: {e}")
        return _wired_identity()

    try:
        if wlanapi.WlanEnumInterfaces(handle, None, ctypes.byref(p_list)) != 0 or not p_list:
            return _wired_identity()
        iface_list = p_list.contents
        connected = next(
            (iface_list.InterfaceInfo[i] for i in range(min(iface_list.dwNumberOfItems, 8))
             if iface_list.InterfaceInfo[i].isState == _WLAN_INTERFACE_STATE_CONNECTED),
            None,
        )
        if connected is None:
            # A WLAN adapter exists but nothing is associated (airplane mode / unplugged USB NIC).
            return _wired_identity()

        guid = connected.InterfaceGuid

        # Band — Location-FREE (channel number opcode).
        band: Optional[str] = None
        p_data = ctypes.c_void_p()
        data_size = wintypes.DWORD()
        opcode_type = wintypes.DWORD()
        if wlanapi.WlanQueryInterface(handle, ctypes.byref(guid), _WLAN_OPCODE_CHANNEL_NUMBER,
                                      None, ctypes.byref(data_size), ctypes.byref(p_data),
                                      ctypes.byref(opcode_type)) == 0 and p_data:
            channel = ctypes.cast(p_data, ctypes.POINTER(wintypes.ULONG)).contents.value
            band = band_from_channel(channel)
            wlanapi.WlanFreeMemory(p_data)

        # SSID — Location-GATED (current-connection opcode); detect ERROR_ACCESS_DENIED cleanly.
        ssid: Optional[str] = None
        ssid_blocked = False
        p_conn = ctypes.c_void_p()
        rc = wlanapi.WlanQueryInterface(handle, ctypes.byref(guid), _WLAN_OPCODE_CURRENT_CONNECTION,
                                        None, ctypes.byref(data_size), ctypes.byref(p_conn),
                                        ctypes.byref(opcode_type))
        if rc == _ERROR_ACCESS_DENIED:
            ssid_blocked = True
        elif rc == 0 and p_conn:
            # WLAN_CONNECTION_ATTRIBUTES.wlanAssociationAttributes.dot11Ssid: uSSIDLength@offset,
            # then 32 SSID bytes. Parse defensively from the raw buffer.
            try:
                buf = ctypes.cast(p_conn, ctypes.POINTER(ctypes.c_ubyte * data_size.value)).contents
                # isState(4) + wlanConnectionMode(4) + strProfileName(256*2) = 520 bytes precede
                # wlanAssociationAttributes; dot11Ssid = ULONG length + 32 bytes.
                assoc = 520
                ssid_len = int.from_bytes(bytes(buf[assoc:assoc + 4]), "little")
                if 0 < ssid_len <= 32:
                    raw = bytes(buf[assoc + 4:assoc + 4 + ssid_len])
                    ssid = raw.decode("utf-8", "replace") or None
            except Exception as e:  # pragma: no cover - defensive parse
                logger.debug(f"SSID parse failed: {e}")
            finally:
                wlanapi.WlanFreeMemory(p_conn)

        return NetworkIdentity(name=ssid, band=band, is_wireless=True,
                               ssid_blocked=ssid_blocked, connected=True)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"get_connected_network_identity failed: {e}")
        return _wired_identity()
    finally:
        if p_list:
            try:
                ctypes.windll.wlanapi.WlanFreeMemory(p_list)
            except Exception:
                pass
        try:
            ctypes.windll.wlanapi.WlanCloseHandle(handle, None)
        except Exception:
            pass