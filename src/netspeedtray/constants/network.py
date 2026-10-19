"""
Constants related to network speeds, units, and interfaces.
"""
from typing import Dict, Final, Set, List

class UnitConstants:
    """Constants for unit conversions and i18n keys for labels."""
    BITS_PER_BYTE: Final[int] = 8
    
    # Decimal (SI) divisors - powers of 1000
    KILO_DIVISOR: Final[int] = 1_000
    MEGA_DIVISOR: Final[int] = 1_000_000
    GIGA_DIVISOR: Final[int] = 1_000_000_000
    
    # Binary (IEC) divisors - powers of 1024
    KIBI_DIVISOR: Final[int] = 1_024
    MEBI_DIVISOR: Final[int] = 1_048_576
    GIBI_DIVISOR: Final[int] = 1_073_741_824
    
    KILO_THRESHOLD: Final[int] = KILO_DIVISOR
    MEGA_THRESHOLD: Final[int] = MEGA_DIVISOR
    GIGA_THRESHOLD: Final[int] = GIGA_DIVISOR
    MINIMUM_DISPLAY_SPEED: Final[float] = 0.0
    
    # Decimal (SI) unit labels - i18n keys
    BPS_LABEL: Final[str] = "BPS_LABEL"
    KBPS_LABEL: Final[str] = "KBPS_LABEL"
    MBPS_LABEL: Final[str] = "MBPS_LABEL"
    GBPS_LABEL: Final[str] = "GBPS_LABEL"
    BITS_LABEL: Final[str] = "BITS_LABEL"
    KBITS_LABEL: Final[str] = "KBITS_LABEL"
    MBITS_LABEL: Final[str] = "MBITS_LABEL"
    GBITS_LABEL: Final[str] = "GBITS_LABEL"
    
    # Binary (IEC) unit labels - i18n keys
    BIBPS_LABEL: Final[str] = "BIBPS_LABEL"      # B/s (same as BPS)
    KIBPS_LABEL: Final[str] = "KIBPS_LABEL"      # KiB/s
    MIBPS_LABEL: Final[str] = "MIBPS_LABEL"      # MiB/s
    GIBPS_LABEL: Final[str] = "GIBPS_LABEL"      # GiB/s
    KIBITS_LABEL: Final[str] = "KIBITS_LABEL"    # Kibps
    MIBITS_LABEL: Final[str] = "MIBITS_LABEL"    # Mibps
    GIBITS_LABEL: Final[str] = "GIBITS_LABEL"    # Gibps

    # Short unit labels (e.g. "Mb" instead of "Mbps")
    BPS_SHORT_LABEL: Final[str] = "BPS_SHORT_LABEL"
    KBPS_SHORT_LABEL: Final[str] = "KBPS_SHORT_LABEL"
    MBPS_SHORT_LABEL: Final[str] = "MBPS_SHORT_LABEL"
    GBPS_SHORT_LABEL: Final[str] = "GBPS_SHORT_LABEL"
    BITS_SHORT_LABEL: Final[str] = "BITS_SHORT_LABEL"
    KBITS_SHORT_LABEL: Final[str] = "KBITS_SHORT_LABEL"
    MBITS_SHORT_LABEL: Final[str] = "MBITS_SHORT_LABEL"
    GBITS_SHORT_LABEL: Final[str] = "GBITS_SHORT_LABEL"
    
    # Binary Short
    BIBPS_SHORT_LABEL: Final[str] = "BIBPS_SHORT_LABEL"
    KIBPS_SHORT_LABEL: Final[str] = "KIBPS_SHORT_LABEL"
    MIBPS_SHORT_LABEL: Final[str] = "MIBPS_SHORT_LABEL"
    GIBPS_SHORT_LABEL: Final[str] = "GIBPS_SHORT_LABEL"
    KIBITS_SHORT_LABEL: Final[str] = "KIBITS_SHORT_LABEL"
    MIBITS_SHORT_LABEL: Final[str] = "MIBITS_SHORT_LABEL"
    GIBITS_SHORT_LABEL: Final[str] = "GIBITS_SHORT_LABEL"

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.BITS_PER_BYTE != 8:
            raise ValueError("BITS_PER_BYTE must be 8")
        if not (self.KILO_THRESHOLD < self.MEGA_THRESHOLD < self.GIGA_THRESHOLD):
            raise ValueError("Thresholds must be in increasing order")

class NetworkSpeedConstants:
    """Constants for network speed calculations."""
    DEFAULT_SPEED: Final[float] = 0.0
    # Clamp time deltas to prevent scheduling jitter from producing phantom spikes.
    # 10ms (0.01s) is conservative but significantly reduces false positives from OS scheduling artifacts.
    MIN_TIME_DIFF: Final[float] = 0.01
    MIN_RECORDABLE_SPEED_BPS: Final[float] = 1.0
    
    # These are now i18n keys
    DEFAULT_UNIT_BITS: Final[str] = "BITS_LABEL"
    DEFAULT_UNIT_BYTES: Final[str] = "BPS_LABEL"

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.DEFAULT_SPEED < 0:
            raise ValueError("DEFAULT_SPEED must be non-negative")
        if self.MIN_TIME_DIFF <= 0:
            raise ValueError("MIN_TIME_DIFF must be positive")

class InterfaceConstants:
    """Constants related to network interface management."""
    DEFAULT_MODE: Final[str] = "auto"
    VALID_INTERFACE_MODES: Final[Set[str]] = {"auto", "all_physical", "all_virtual", "selected"}
    
    # The default config must be JSON serializable, so this must be a list.
    DEFAULT_EXCLUSIONS: Final[List[str]] = [
        "loopback", "teredo", "isatap", "bluetooth", "vpn", "virtual", "vmware", "vbox"
    ]
    
    # Absolute fallback ceiling (100 Gbps) for when physical link speeds cannot be determined (e.g. virtual adapters) 
    MAX_REASONABLE_SPEED_BPS: Final[int] = 12_500_000_000

    # Primary-interface resolution: a UDP connect() (no packet is sent) to a public address makes
    # the OS pick the default-route source address. IPv4 first; the IPv6 probe makes an IPv6-only
    # network resolve too (otherwise auto mode meters nothing there - data-usage audit, finding 2).
    ROUTE_PROBE_V4: Final[tuple] = ("8.8.8.8", 80)
    ROUTE_PROBE_V6: Final[tuple] = ("2001:4860:4860::8888", 80)

    # --- Adapter classification (utils/interface_classifier.py) ---------------------------------
    # One classifier decides "is this a real adapter?" for both the Settings picker's grouping and
    # the all_physical aggregation. Kinds:
    KIND_PHYSICAL: Final[str] = "physical"
    KIND_VIRTUAL: Final[str] = "virtual"   # VPN / tunnel / VM / container / Wi-Fi Direct / PAN
    KIND_PSEUDO: Final[str] = "pseudo"     # loopback + the IPv6 transition shims; never a connection
    # IANA ifType values as reported by GetIfTable2 (MIB_IF_ROW2.Type).
    IF_TYPE_ETHERNET: Final[int] = 6
    IF_TYPE_PPP: Final[int] = 23
    IF_TYPE_SOFTWARE_LOOPBACK: Final[int] = 24
    IF_TYPE_PROP_VIRTUAL: Final[int] = 53      # Tailscale, WireGuard, NordLynx, Wintun
    IF_TYPE_IEEE80211: Final[int] = 71
    IF_TYPE_TUNNEL: Final[int] = 131           # Teredo, 6to4, IP-HTTPS, WAN Miniport (SSTP/L2TP/...)
    # Types that are virtual whatever the hardware flag says (a PPPoE session rides on a real NIC
    # whose bytes are already counted, so counting it too would double-count).
    VIRTUAL_IF_TYPES: Final[frozenset] = frozenset({IF_TYPE_PPP, IF_TYPE_PROP_VIRTUAL, IF_TYPE_TUNNEL})
    # Friendly-name keywords (lower-case substrings) for pseudo-interfaces.
    PSEUDO_NAME_KEYWORDS: Final[tuple] = ("loopback", "teredo", "isatap", "6to4", "ip-https")
    # Friendly-name keywords that mark a virtual adapter. Applied on top of the user's
    # `excluded_interfaces` (whose defaults are the 2.1 list) and used alone when the OS can't be
    # asked (off Windows, a name only seen in history). "local area connection*" is how Windows
    # names hidden miniports and Wi-Fi Direct adapters - the asterisk is part of the name.
    VIRTUAL_NAME_KEYWORDS: Final[tuple] = (
        "vethernet", "vswitch", "wsl", "hyper-v", "tailscale", "wireguard", "nordlynx", "zerotier",
        "openvpn", "wintun", "tap-windows", "vpn", "virtual", "vmware", "vmnet", "vbox", "virtualbox",
        "docker", "hamachi", "radmin", "mullvad", "bluetooth", "local area connection*",
    )
    # Adapter-description keywords (lower-case substrings). Narrower than the name list on purpose:
    # a Hyper-V *guest* NIC is "Microsoft Hyper-V Network Adapter" and is the VM's real connection,
    # so there is no bare "hyper-v" here. These veto even an adapter the OS calls hardware.
    VIRTUAL_DESCRIPTION_KEYWORDS: Final[tuple] = (
        "tunnel", "host-only", "tap-windows", "wintun", "wireguard", "nordlynx",
        "zerotier", "tailscale", "wan miniport", "kernel debug", "vpn", "loopback",
        "personal area network", "npcap",
    )
    # Soft description keywords: they mark an adapter virtual only when the OS does NOT say it is a
    # hardware interface. An SR-IOV virtual function (Azure accelerated networking: "Mellanox
    # ConnectX-4 Lx Virtual Ethernet Adapter") is hardware and can be the VM's real data path.
    VIRTUAL_DESCRIPTION_SOFT_KEYWORDS: Final[tuple] = ("virtual",)
    # A name the cached GetIfTable2 table doesn't know (a VPN that just came up) re-reads it at once,
    # throttled to one read per this many seconds. A name-only answer is never cached, so the first
    # read after the throttle corrects it.
    CLASSIFIER_UNKNOWN_REREAD_SEC: Final[float] = 2.0

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.DEFAULT_MODE not in self.VALID_INTERFACE_MODES:
            raise ValueError(f"DEFAULT_MODE '{self.DEFAULT_MODE}' must be in VALID_INTERFACE_MODES")

class LatencyConstants:
    """The latency probe's live loss windows and persisted loss series."""
    # Cycles kept in each hop's live loss window (~2 min at the probe's 3 s cadence).
    LIVE_LOSS_WINDOW: Final[int] = 40
    # A hop's live loss % is shown only once its window holds this many cycles (~15 s); before that
    # one timeout right after the public probe is switched on would read as 100% loss.
    LOSS_MIN_SAMPLES: Final[int] = 5
    # The skip-and-record-lost rule (gateway timed out -> anchor not probed, counted lost) applies only
    # while the gateway has answered within this many cycles - one live window. A gateway that never
    # answers ICMP (firewalled CPE, some mesh systems, a full-tunnel VPN's virtual gateway) or has been
    # silent a whole window tells us nothing about the link, so the anchor is probed normally.
    GATEWAY_REPLY_RECENT_CYCLES: Final[int] = LIVE_LOSS_WINDOW
    # Each cycle sends a burst of this many echoes per hop (gateway; and the anchor when opted in), so a
    # 40-cycle window holds 160 probes and resolves 0.6% loss steps. ~1.3 packets/s per hop at 3 s.
    BURST_SIZE: Final[int] = 4
    # Gap between the echoes of one burst. Far below any ICMP rate limit seen on consumer routers.
    BURST_SPACING_MS: Final[int] = 100
    # Persisted per-cycle loss series: lost / sent of that cycle's burst (0, .25, .5, .75, 1; rows from
    # before 2.2 are 0/1 for a single echo). The rollups average either shape into a 0-1 rate.
    STAT_GW_TIMEOUT: Final[str] = "latency_gw_timeout"
    STAT_ANCHOR_TIMEOUT: Final[str] = "latency_anchor_timeout"
    # RTT series -> the loss series of the same hop (the exporter's loss_pct).
    LOSS_SERIES_FOR: Final[Dict[str, str]] = {
        "latency_gw": STAT_GW_TIMEOUT,
        "latency_anchor": STAT_ANCHOR_TIMEOUT,
    }


class UsageOdometerConstants:
    """The data-usage odometer's launch reconciliation (counter snapshot persisted with the total)."""
    # metadata-table key holding the JSON counter snapshot written in the same transaction as the
    # usage_counter row. A key/value row, not a column: no schema change, no migration.
    SNAPSHOT_METADATA_KEY: Final[str] = "usage_counter_snapshot"
    SNAPSHOT_FORMAT_VERSION: Final[int] = 1
    # Two psutil.boot_time() readings belong to the same boot session when they agree within this.
    # On Windows boot_time() is wall-clock now minus uptime, so it jitters (~1 s, psutil #1007) and
    # follows wall-clock corrections; two different boots are always at least one whole session
    # apart. Erring wide only risks treating a sub-30 s session as "same boot", and the
    # counters-went-backwards guard still catches a reboot (its counters restart from zero).
    BOOT_TIME_TOLERANCE_SEC: Final[float] = 30.0


class NetworkConstants:
    """Container for network-related constant groups."""
    def __init__(self) -> None:
        self.units = UnitConstants()
        self.speed = NetworkSpeedConstants()
        self.interface = InterfaceConstants()
        self.latency = LatencyConstants()
        self.usage = UsageOdometerConstants()

# Singleton instance for easy access
network = NetworkConstants()