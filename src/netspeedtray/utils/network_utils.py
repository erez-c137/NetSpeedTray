
import sys
from typing import Optional

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
            # NetConnectionID is the friendly name, GUID is in GUID
            if hasattr(iface, 'GUID') and iface.GUID and iface.GUID.lower() == guid.strip('{}').lower():
                return getattr(iface, 'NetConnectionID', None)
    except ImportError:
        logger.warning("wmi module not installed; cannot map GUID to friendly name.")
    except Exception as e:
        logger.error(f"Error mapping GUID to friendly name: {e}", exc_info=True)
    return None
"""
Network-related utility functions for NetSpeedTray.
"""

import logging
import socket
from typing import Optional

import psutil

logger = logging.getLogger("NetSpeedTray.NetworkUtils")

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
        
    logger.warning("Could not find an interface matching the local IP {local_ip}.")
    return None