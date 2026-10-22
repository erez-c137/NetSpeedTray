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

    # Integrated-GPU shared memory as "VRAM". An adapter whose `\\GPU Adapter Memory\\Dedicated Usage`
    # never reaches this has no dedicated video memory worth showing: Intel iGPUs report 0 there (the
    # BIOS "stolen" DVMT pre-allocation, 32-64 MiB by default, is not exposed as dedicated usage), and
    # the smallest AMD APU UMA frame buffers sit at or below it. Any discrete GPU driving a desktop
    # holds well above it in framebuffers and compositor surfaces alone. The two mistakes are not
    # equal: calling a dGPU "shared" would change a discrete user's readout, calling an APU's
    # carve-out "dedicated" is simply today's behavior - so the threshold sits low, and one reading at
    # or above it latches the dedicated path for the life of the process (see _poll_gpu_hybrid).
    IGPU_DEDICATED_THRESHOLD_MIB: Final[float] = 128.0
    # PDH has no Shared/Dedicated *Limit* counter (`\\GPU Adapter Memory` exposes only Dedicated
    # Usage, Shared Usage and Total Committed), so the shared budget is Windows' own rule: WDDM lets
    # an adapter use up to half of physical memory as shared GPU memory - the "Shared GPU memory"
    # total Task Manager shows.
    SHARED_BUDGET_FRACTION_OF_RAM: Final[float] = 0.5

    def __init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.UTIL_MIN_PERCENT < self.UTIL_MAX_PERCENT:
            raise ValueError("UTIL_MIN_PERCENT must be below UTIL_MAX_PERCENT.")
        if self.QUERY_RETRY_SEC <= 0:
            raise ValueError("QUERY_RETRY_SEC must be positive.")
        if self.IGPU_DEDICATED_THRESHOLD_MIB <= 0:
            raise ValueError("IGPU_DEDICATED_THRESHOLD_MIB must be positive.")
        if not 0 < self.SHARED_BUDGET_FRACTION_OF_RAM <= 1:
            raise ValueError("SHARED_BUDGET_FRACTION_OF_RAM must be in (0, 1].")


# Singleton instance for easy access
gpu = GpuConstants()
