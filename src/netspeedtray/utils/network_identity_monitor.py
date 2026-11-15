"""Event-driven connected-network identity cache, owned by the stats worker thread.

Callbacks only set an Event. WinRT access, refreshes, subscription teardown and
apartment teardown all run on the same worker, never the GUI or callback thread.
"""
import logging
import sys
import threading
import time
from dataclasses import replace
from typing import Optional

from netspeedtray.utils.network_utils import (
    NetworkIdentity, _get_network_information, get_connected_network_identity,
)

logger = logging.getLogger("NetSpeedTray.NetworkIdentityMonitor")
# Recover after a missed notification, suspend/resume, or a transient API error.
# This is a safety refresh using only the SSID-only API, not a location request.
IDENTITY_REFRESH_INTERVAL_SEC = 60.0
BAND_REFRESH_INTERVAL_SEC = 5.0


def _subscribe_network_changes(callback):
    """Return a same-thread cleanup callable; balance only successful WinRT init."""
    if sys.platform != "win32":
        return None
    from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
    init_apartment(ApartmentType.MULTI_THREADED)
    try:
        info = _get_network_information()
        token = info.add_network_status_changed(callback)
    except Exception:
        uninit_apartment()
        raise

    def unsubscribe():
        try:
            info.remove_network_status_changed(token)
        finally:
            uninit_apartment()
    return unsubscribe


class NetworkIdentityMonitor:
    """Refresh at startup, after network changes, mode changes, or the safety interval.

    ``read`` may be called on every stats tick; unchanged ticks return a cached
    dataclass without calling Windows. ``close`` must run on the owning thread.
    Re-enabling after close starts fresh. Repeated notifications coalesce, and an
    event arriving DURING a read remains pending for the next tick.
    """

    def __init__(self, *, reader=None, subscribe=None, clock=None):
        self._reader = reader or get_connected_network_identity
        self._subscribe = subscribe or _subscribe_network_changes
        self._clock = clock or time.monotonic
        self._dirty = threading.Event()
        self._identity: Optional[NetworkIdentity] = None
        self._mode: Optional[str] = None
        self._last_refresh = float("-inf")
        self._last_band_refresh = float("-inf")
        self._next_subscribe = float("-inf")
        self._unsubscribe = None

    def _on_network_changed(self, _sender):
        self._dirty.set()

    def read(self, mode: str = "both") -> NetworkIdentity:
        now = self._clock()
        if self._unsubscribe is None and now >= self._next_subscribe:
            self._next_subscribe = now + IDENTITY_REFRESH_INTERVAL_SEC
            try:
                self._unsubscribe = self._subscribe(self._on_network_changed)
            except Exception as e:
                logger.debug("Network change notifications unavailable: %s", e)
        if (self._identity is None or mode != self._mode or self._dirty.is_set()
                or now - self._last_refresh >= IDENTITY_REFRESH_INTERVAL_SEC):
            # Clear BEFORE querying, so a concurrent network change isn't lost.
            self._dirty.clear()
            self._mode = mode
            self._last_refresh = now
            if mode in ("band", "both"):
                self._last_band_refresh = now
            try:
                self._identity = self._reader(
                    include_ssid=mode in ("ssid", "both"),
                    include_band=mode in ("band", "both"))
            except Exception as e:
                # Never retain the old network's name after an unsuccessful refresh.
                logger.debug("Network identity refresh failed: %s", e)
                self._identity = NetworkIdentity()
        elif mode in ("band", "both") and now - self._last_band_refresh >= BAND_REFRESH_INTERVAL_SEC:
            # A same-network band change need not raise a network-status event.
            # Preserve the SSID cache while polling the location-free band alone.
            self._last_band_refresh = now
            try:
                band_identity = self._reader(include_ssid=False, include_band=True)
                if (mode == "both" and band_identity.is_wireless and self._identity.is_wireless
                        and band_identity.band == self._identity.band):
                    self._identity = replace(band_identity, name=self._identity.name)
                elif mode == "both" and band_identity.is_wireless:
                    self._identity = self._reader(include_ssid=True, include_band=True)
                    self._last_refresh = now
                else:
                    self._identity = band_identity
            except Exception as e:
                logger.debug("Network band refresh failed: %s", e)
                self._identity = NetworkIdentity()
        return self._identity

    def close(self):
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            try:
                unsubscribe()
            except Exception as e:
                logger.debug("Network change notification cleanup failed: %s", e)
        self._identity = None
        self._mode = None
        self._dirty.clear()
        self._last_refresh = float("-inf")
        self._last_band_refresh = float("-inf")
        self._next_subscribe = float("-inf")
