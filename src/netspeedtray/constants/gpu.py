"""
GPU Monitoring Constants Module.

Defines the PDH counter-instance grammar and limits used to turn per-process
`\\GPU Engine(*)\\Utilization Percentage` samples into one utilization figure.
"""

import re
from typing import Final, Pattern


class GpuConstants:
    """Constants for PDH-based GPU utilization aggregation."""

    # A \GPU Engine instance is one PROCESS on one ENGINE:
    #   pid_1234_luid_0x00000000_0x0000A1B2_phys_0_eng_3_engtype_3D
    # The engine's identity is (luid high, luid low, phys, eng). engtype is only a label - it is
    # often blank and is deliberately not part of the key. Matched case-insensitively; hex and
    # decimal fields are parsed to ints so padding/case differences cannot split one engine.
    ENGINE_INSTANCE_PATTERN: Final[Pattern[str]] = re.compile(
        r"^pid_\d+_luid_0x([0-9a-f]+)_0x([0-9a-f]+)_phys_(\d+)_eng_(\d+)(?:_engtype_.*)?$",
        re.IGNORECASE,
    )

    # Utilization is reported as a percentage; a per-engine sum can briefly exceed 100 because
    # per-process busy intervals are sampled independently.
    UTIL_MIN_PERCENT: Final[float] = 0.0
    UTIL_MAX_PERCENT: Final[float] = 100.0

    # A missing/broken PDH GPU provider is re-opened at most this often, never on every poll.
    QUERY_RETRY_SEC: Final[float] = 30.0

    # Integrated-GPU shared memory as "VRAM". The question "does this machine have a GPU with real
    # dedicated video memory?" is answered from a STATIC adapter fact, read once at query init (and on
    # every rebuild), never from a usage reading - a discrete GPU idle at startup (a hybrid laptop's
    # Arc/Radeon, an idle eGPU, a desktop Radeon with the monitor on the motherboard) reads ~0
    # Dedicated Usage and would otherwise be called "integrated", then flip mid-session.
    #
    # Source: DXGI IDXGIAdapter1::GetDesc1 DedicatedVideoMemory (core/gpu_adapters.py; ctypes, no
    # device creation). Matched to PDH instances by adapter LUID; its Flags/VendorId identify the
    # Microsoft Basic Render Driver. Rejected alternative: the display-class registry
    # HardwareInformation.MemorySize, which reports 2 GiB for the Intel UHD 770 that DXGI correctly
    # reports at 128 MiB (probed 2026-10-22). DXGI's figure for an iGPU is its BIOS "stolen" DVMT
    # pre-allocation - 128 MiB on current Intel UHD/Iris parts, 32-256 MiB on older ones - so the
    # bar sits well above that. AMD APUs report their UMA frame buffer here (512 MiB by default,
    # up to several GiB) and stay on the dedicated readout, which on an APU is real carve-out usage
    # - today's behavior. Discrete GPUs on current WDDM drivers ship with 1 GiB or more (and NVIDIA
    # is caught by nvidia-smi regardless).
    DEDICATED_VIDEO_MEMORY_MIN_MIB: Final[float] = 512.0
    # Fallback ONLY when DXGI is unavailable: one Dedicated Usage reading at or above this latches the
    # dedicated path for the process. Low on purpose - calling a dGPU "shared" changes a discrete
    # user's readout; calling an APU carve-out "dedicated" is just today's behavior. Can still flip
    # once on an idle-at-startup non-NVIDIA dGPU; that is why it is only the fallback.
    DEDICATED_USAGE_FALLBACK_MIB: Final[float] = 128.0
    # Fallback ONLY: a shared reading below this is no iGPU at all - the Microsoft Basic Render
    # Driver (VMs, servers, a broken GPU driver) reports 0 shared and must keep today's hidden VRAM.
    SHARED_USAGE_FALLBACK_MIN_MIB: Final[float] = 1.0
    # Shared budget when DXGI's per-adapter SharedSystemMemory is unavailable. PDH has no limit
    # counter (`\\GPU Adapter Memory` exposes only Dedicated Usage, Shared Usage and Total Committed);
    # Windows lets an adapter use up to half of physical memory as shared GPU memory - the total
    # Task Manager shows (and what DXGI reports: 7.839 GiB of 15.68 GiB on the probe PC).
    SHARED_BUDGET_FRACTION_OF_RAM: Final[float] = 0.5

    # GPU picker (#236). "" = Automatic: the busiest engine on ANY adapter, VRAM summed across all of
    # them - exactly the pre-picker readout. Otherwise the DXGI adapter Description the user picked;
    # stored as the NAME, never the LUID, because LUIDs are reassigned on every boot.
    ADAPTER_AUTO: Final[str] = ""
    # PCI vendor ids (DXGI_ADAPTER_DESC1.VendorId). A picked adapter's temperature / power are only
    # taken from a sensor of the SAME maker: LibreHardwareMonitor's GPU groups and its WMI identifiers
    # name the vendor (/gpu-nvidia/, /gpu-amd/, /gpu-intel-integrated/), nvidia-smi is NVIDIA-only and
    # Intel RAPL PP1 is the Intel iGPU's. A sensor of another maker belongs to the other GPU, so the
    # reading is N/A rather than the wrong card's.
    # rc.2 G4: when PDH's adapter set does not match a fresh DXGI read (PDH can lag a hot-plug by a
    # few seconds, or name a LUID DXGI never lists), re-read DXGI at most this often, this many times
    # per adapter set - ~0.3 ms each - then accept the set as it is.
    ADAPTER_RECHECK_SEC: Final[float] = 5.0
    ADAPTER_RECHECK_MAX: Final[int] = 6

    # The hover card's per-GPU list (#236, 2.2.1). It never runs nvidia-smi: it reuses the Automatic
    # poll's cached reading, and only while that is recent - at most this many nvidia-smi sub-cadence
    # intervals old (a cache nvidia-smi stopped refreshing is not shown as a live reading).
    LIST_SMI_CACHE_MAX_AGE_POLLS: Final[float] = 3.0
    # A listed GPU's name is elided at this many logical px, so a long driver name never widens the card.
    LIST_NAME_MAX_WIDTH_PX: Final[int] = 170

    # Hybrid (Optimus) laptops, 2.2.1: nvidia-smi goes through NVML, and asking a sleeping dGPU
    # anything powers it up - so a 5 s nvidia-smi poll keeps it from ever sleeping (battery drain).
    # On a laptop (battery, mobile platform role) with an NVIDIA GPU AND a GPU of another maker (the
    # iGPU), nvidia-smi is only asked for temperature / power while the dGPU is already awake: its PDH
    # engine utilization (per LUID, read every tick anyway - PDH and DXGI never wake a GPU, Task
    # Manager reads the same counters) was above HYBRID_DGPU_AWAKE_MIN_UTIL_PERCENT within the hold:
    # HYBRID_DGPU_AWAKE_HOLD_SEC, or HYBRID_DGPU_AWAKE_HOLD_WINDOWS PDH sample windows when the update
    # rate is slow (Power Saver's 10 s). The hold rides out a game's 0 % loading-screen ticks; once
    # the work stops the queries stop and the dGPU sleeps. A sample whose window holds our own
    # nvidia-smi call never counts (see StatsMonitorThread._nvidia_awake), so a wake nvidia-smi itself
    # caused can never keep the gate open, at any update rate.
    HYBRID_DGPU_AWAKE_MIN_UTIL_PERCENT: Final[float] = 0.0   # strictly above
    HYBRID_DGPU_AWAKE_HOLD_SEC: Final[float] = 15.0
    HYBRID_DGPU_AWAKE_HOLD_WINDOWS: Final[float] = 3.0
    # A hybrid whose PDH GPU utilization cannot be read polls nvidia-smi without the gate (readings
    # never go blank): every 5 s on AC or an unknown power source, every this many seconds on battery.
    HYBRID_BLIND_BATTERY_POLL_SEC: Final[float] = 30.0
    # nvidia-smi that keeps failing (timeout, non-zero exit) is retried after BASE * 2^(n-1) s for the
    # n-th failure in a row, capped at MAX - never every 5 s forever. Logged once per failure streak,
    # at the LOG_AFTER-th failure.
    NVIDIA_SMI_BACKOFF_BASE_SEC: Final[float] = 10.0
    NVIDIA_SMI_BACKOFF_MAX_SEC: Final[float] = 300.0
    NVIDIA_SMI_BACKOFF_LOG_AFTER: Final[int] = 3

    VENDOR_NVIDIA: Final[int] = 0x10DE
    VENDOR_AMD: Final[int] = 0x1002
    VENDOR_AMD_ALT: Final[int] = 0x1022
    VENDOR_INTEL: Final[int] = 0x8086
    # Lower-case tokens that identify each maker in an LHM hardware id / name or a WMI identifier.
    VENDOR_SENSOR_TOKENS: Final[dict] = {
        VENDOR_NVIDIA: ("nvidia",),
        VENDOR_AMD: ("amd", "radeon"),
        VENDOR_AMD_ALT: ("amd", "radeon"),
        VENDOR_INTEL: ("intel",),
    }

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.UTIL_MIN_PERCENT < self.UTIL_MAX_PERCENT:
            raise ValueError("UTIL_MIN_PERCENT must be below UTIL_MAX_PERCENT.")
        if self.QUERY_RETRY_SEC <= 0:
            raise ValueError("QUERY_RETRY_SEC must be positive.")
        if not 0 < self.DEDICATED_USAGE_FALLBACK_MIB <= self.DEDICATED_VIDEO_MEMORY_MIN_MIB:
            raise ValueError("DEDICATED_USAGE_FALLBACK_MIB must be in (0, DEDICATED_VIDEO_MEMORY_MIN_MIB].")
        if self.SHARED_USAGE_FALLBACK_MIN_MIB < 0:
            raise ValueError("SHARED_USAGE_FALLBACK_MIN_MIB must not be negative.")
        if not 0 < self.SHARED_BUDGET_FRACTION_OF_RAM <= 1:
            raise ValueError("SHARED_BUDGET_FRACTION_OF_RAM must be in (0, 1].")
        if self.LIST_SMI_CACHE_MAX_AGE_POLLS <= 0 or self.LIST_NAME_MAX_WIDTH_PX <= 0:
            raise ValueError("LIST_SMI_CACHE_MAX_AGE_POLLS and LIST_NAME_MAX_WIDTH_PX must be positive.")
        if (self.HYBRID_DGPU_AWAKE_MIN_UTIL_PERCENT < 0 or self.HYBRID_DGPU_AWAKE_HOLD_SEC <= 0
                or self.HYBRID_BLIND_BATTERY_POLL_SEC <= 0):
            raise ValueError("HYBRID_DGPU_AWAKE_* must be non-negative / positive.")
        if not 0 < self.NVIDIA_SMI_BACKOFF_BASE_SEC <= self.NVIDIA_SMI_BACKOFF_MAX_SEC:
            raise ValueError("NVIDIA_SMI_BACKOFF_BASE_SEC must be in (0, NVIDIA_SMI_BACKOFF_MAX_SEC].")
        if self.NVIDIA_SMI_BACKOFF_LOG_AFTER < 1 or self.HYBRID_DGPU_AWAKE_HOLD_WINDOWS < 2:
            # Fewer than 2 windows: one ambiguous sample between two clean ones would close the gate.
            raise ValueError("NVIDIA_SMI_BACKOFF_LOG_AFTER must be >= 1, HYBRID_DGPU_AWAKE_HOLD_WINDOWS >= 2.")


# Singleton instance for easy access
gpu = GpuConstants()
