"""
LibreHardwareMonitor (LHM) HTTP Sensor Source Constants Module.

LHM v0.9.5+ removed its WMI provider; its built-in web server (Options -> Remote Web Server,
default port 8085) is the live interface. These values bound what reading it may cost the
monitor thread, which polls once a second and must never stall on it.
"""

from typing import Final


class LhmConstants:
    """Endpoint, time budget and cache/backoff values for the LHM web-server reader."""

    # Numeric loopback, never "localhost": that resolves to ::1 first, and on a machine where a closed
    # loopback port drops the SYN instead of resetting it, every attempt paid the timeout twice.
    HTTP_HOST: Final[str] = "127.0.0.1"
    HTTP_PORT: Final[int] = 8085
    HTTP_PATH: Final[str] = "/data.json"

    # Connecting to a listening loopback port takes well under a millisecond; this only bounds the
    # dropped-SYN case (nothing listening, on machines whose loopback does not reset). That attempt
    # runs on the monitor thread once per backoff window, so it must stay short (PR #276 review:
    # 0.35 s here was a visible stall every 30 s).
    CONNECT_TIMEOUT_SEC: Final[float] = 0.1
    # Hard budget for the WHOLE request (connect + status line + headers + body). Each socket read
    # gets only what is left of it, so a server that trickles bytes cannot hold a poll for longer.
    TOTAL_DEADLINE_SEC: Final[float] = 0.5
    # data.json is tens to a few hundred KB even on large machines; anything bigger is not LHM.
    MAX_RESPONSE_BYTES: Final[int] = 4 * 1024 * 1024

    # One poll asks for CPU temp, CPU power and GPU temp/power; a successful response is shared
    # across those reads for this long (under the default 1 s update interval).
    CACHE_SEC: Final[float] = 0.75
    # After any failure (nothing listening, timeout, not-LHM on the port, bad JSON) the source is
    # skipped for this long - so an absent LHM costs one short attempt per window, and an LHM
    # started later is still picked up.
    FAILURE_BACKOFF_SEC: Final[float] = 30.0

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not 0 < self.HTTP_PORT < 65536:
            raise ValueError("HTTP_PORT must be a valid TCP port.")
        if not self.HTTP_PATH.startswith("/"):
            raise ValueError("HTTP_PATH must be absolute.")
        if not 0 < self.CONNECT_TIMEOUT_SEC <= self.TOTAL_DEADLINE_SEC:
            raise ValueError("CONNECT_TIMEOUT_SEC must be positive and within TOTAL_DEADLINE_SEC.")
        if self.TOTAL_DEADLINE_SEC >= 1.0:
            raise ValueError("TOTAL_DEADLINE_SEC must stay well under the 1 s poll interval.")
        if self.MAX_RESPONSE_BYTES <= 0:
            raise ValueError("MAX_RESPONSE_BYTES must be positive.")
        if not 0 < self.CACHE_SEC < 1.0:
            raise ValueError("CACHE_SEC must be positive and under one poll interval.")
        if self.FAILURE_BACKOFF_SEC <= 0:
            raise ValueError("FAILURE_BACKOFF_SEC must be positive.")


# Singleton instance for easy access
lhm = LhmConstants()
