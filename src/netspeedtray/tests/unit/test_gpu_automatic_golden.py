"""
The GPU picker's "Automatic" (gpu_adapter == "") must read exactly what the app read before the
picker existed (#236). The pre-picker poll (origin/main @ bd08da17's _poll_gpu_hybrid) was run over 14
machine types x 4 temp/power combinations x 6 polls - idle, busy, failing reads, a >100% sum - and
its results were frozen in gpu_automatic_golden.json. Any later change that alters Automatic on any
of them fails here (adapted from the #435 review's side-by-side test, which needed a copy of main).

Regenerate ONLY for a deliberate, reviewed change to Automatic:  python -m
netspeedtray.tests.unit.test_gpu_automatic_golden --write   (from src/, with the change explained in
the PR - the file is the contract).
"""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.core import gpu_adapters
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.lhm_http import LhmGpu, LhmSnapshot

GOLDEN = Path(__file__).with_name("gpu_automatic_golden.json")

MIB = 1024.0 * 1024.0
GIB = 1024 ** 3
IG, DG, BR = 0xD9C1, 0xAB01, 0xDD11


def inst(luid):
    return f"luid_0x00000000_0x{luid:08X}_phys_0"


def eng(luid, pid, e=0):
    return f"pid_{pid}_luid_0x00000000_0x{luid:08X}_phys_0_eng_{e}_engtype_3D"


UHD = AdapterInfo("Intel(R) UHD Graphics 770", 0x8086, (0, IG), 128 * 2 ** 20, int(7.8 * GIB), False)
BRD = AdapterInfo("Microsoft Basic Render Driver", 0x1414, (0, BR), 0, int(7.8 * GIB), True)
RTX = AdapterInfo("NVIDIA GeForce RTX 4070", 0x10DE, (0, DG), 12 * GIB, int(7.8 * GIB), False)
RX = AdapterInfo("AMD Radeon RX 6600", 0x1002, (0, DG), 8 * GIB, int(7.8 * GIB), False)
ARC = AdapterInfo("Intel(R) Arc(TM) A770", 0x8086, (0, DG), 16 * GIB, int(7.8 * GIB), False)
APU = AdapterInfo("AMD Radeon(TM) Graphics", 0x1002, (0, IG), 512 * 2 ** 20, int(7 * GIB), False)

LHM_NV = LhmSnapshot(gpus=(LhmGpu("/gpu-nvidia/0", "NVIDIA GeForce RTX 4070", 61.0, "t", 95.0, "p"),
                           LhmGpu("/gpu-intel-integrated/0", "Intel(R) UHD Graphics 770", None, None, 3.0, "p")))
LHM_AMD = LhmSnapshot(gpus=(LhmGpu("/gpu-amd/0", "AMD Radeon RX 6600", 55.0, "t", 80.0, "p"),))


class _Fail(RuntimeError):
    pass


E = _Fail("PDH_CSTATUS_INVALID_DATA")


def polls(dg_luid, ig_luid=None, extra=None):
    """(util, dedicated, shared) PDH arrays per poll: idle, busy, failing reads, a >100% sum."""
    luids = [x for x in (ig_luid, dg_luid) if x is not None] + list(extra or [])

    def d(vals):
        return {inst(x): v for x, v in zip(luids, vals)}
    n = len(luids)
    return [
        ({}, d([0.0] * n), d([600 * MIB] * n)),
        ({eng(luids[0], 1): 30.0, eng(luids[-1], 2): 20.0, eng(luids[-1], 3): 15.0},
         d([10 * MIB] + [3000 * MIB] * (n - 1)), d([650 * MIB] * n)),
        ({eng(luids[0], 1): 80.0, "garbage_instance": 5.0}, E, d([700 * MIB] * n)),
        (E, d([0.0] * n), E),
        ({eng(luids[-1], 9): 150.0}, d([5 * MIB] * n), d([0.0] * n)),
        ({eng(luids[0], 1): 12.0}, d([0.0] * n), d([680 * MIB] * n)),
    ]


SMI = "64, 12282, 110.5"
SCENARIOS = {
    # name: (DXGI adapters or None, PDH luids (dgpu, igpu, extra), nvidia-smi output, LHM snapshot)
    "nvidia_dgpu_smi": ([RTX, BRD], (DG, None, None), SMI, None),
    "nvidia_dgpu_smi_lhm": ([RTX, BRD], (DG, None, None), SMI, LHM_NV),
    "amd_dgpu_nosmi": ([RX, BRD], (DG, None, None), None, LHM_AMD),
    "intel_arc_nosmi": ([ARC, BRD], (DG, None, None), None, None),
    "igpu_only_uhd770": ([UHD, BRD], (IG, None, [BR]), None, None),
    "igpu_only_no_brd_pdh": ([UHD, BRD], (IG, None, None), None, None),
    "apu_only": ([APU], (IG, None, None), None, None),
    "hybrid_intel_nvidia_smi": ([UHD, RTX, BRD], (DG, IG, None), SMI, LHM_NV),
    "hybrid_intel_radeon": ([UHD, RX, BRD], (DG, IG, None), None, LHM_AMD),
    "hybrid_intel_arc": ([UHD, ARC, BRD], (DG, IG, None), None, None),
    "basic_render_only": ([BRD], (BR, None, None), None, None),
    "no_dxgi_fallback_igpu": (None, (IG, None, None), None, None),
    "no_dxgi_fallback_dgpu": (None, (DG, IG, None), None, None),
    "pdh_names_unknown_luid": ([UHD, BRD], (IG, None, [0xEEEE]), None, None),
}
COMBOS = [(False, False), (True, False), (True, True), (False, True)]


def _thread(cls, adapters, smi, lhm):
    t = cls(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter, t._gpu_vram_counter, t._gpu_shared_counter = 1, 2, 3
    t._nvidia_smi_path = "C:/fake/nvidia-smi.exe" if smi else None
    t._run_child = MagicMock(return_value=smi or "")
    t._NVIDIA_POLL_INTERVAL_SEC = 0.0
    t._wmi_ohm = False
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = lhm
    t._gpu_adapters = list(adapters) if adapters is not None else None
    t._init_power_query = MagicMock()
    t._power_query = None
    t.config = dict(t.config or {}, gpu_adapter="")
    return t


def run_all(cls, mod) -> dict:
    """Every scenario x combo -> the list of poll results, as plain JSON values."""
    out = {}
    for name, (adapters, (dg, ig, extra), smi, lhm) in SCENARIOS.items():
        for temp, power in COMBOS:
            t = _thread(cls, adapters, smi, lhm)
            rows = []
            for util, ded, sh in polls(dg, ig, extra):
                with patch("win32pdh.CollectQueryData"), \
                     patch("win32pdh.GetFormattedCounterArray", side_effect=[util, ded, sh]), \
                     patch.object(mod.psutil, "virtual_memory",
                                  return_value=types.SimpleNamespace(total=32 * GIB)), \
                     patch.object(gpu_adapters, "enumerate_adapters",
                                  return_value=list(adapters) if adapters is not None else None), \
                     patch.object(cls, "_init_ohm_wmi"):
                    r = t._poll_gpu_hybrid(include_temp=temp, include_power=power)
                rows.append(list(r) + [t._gpu_engine_seen, t._gpu_dedicated_seen])
            out[f"{name}|temp={temp}|power={power}"] = rows
    t = _thread(cls, [UHD, BRD], None, None)
    with patch("win32pdh.CollectQueryData", side_effect=E), \
         patch.object(mod.psutil, "virtual_memory", return_value=types.SimpleNamespace(total=32 * GIB)):
        out["pdh_collect_fails"] = [list(t._poll_gpu_hybrid(include_temp=False, include_power=False))]
    return json.loads(json.dumps(out))


def dump(data: dict) -> str:
    """The golden file's text: one scenario per line, so a diff names the scenario that moved."""
    lines = [f" {json.dumps(k)}: {json.dumps(data[k])}" for k in sorted(data)]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def test_automatic_matches_the_pre_picker_golden(q_app):
    import netspeedtray.core.monitor_thread as mt
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    current = run_all(mt.StatsMonitorThread, mt)
    assert set(current) == set(golden)
    diffs = [k for k in golden if current[k] != golden[k]]
    assert not diffs, f"Automatic changed on: {diffs[:5]}"
    assert len(golden) == len(SCENARIOS) * len(COMBOS) + 1


if __name__ == "__main__" and "--write" in sys.argv:
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import netspeedtray.core.monitor_thread as mt
    GOLDEN.write_text(dump(run_all(mt.StatsMonitorThread, mt)), encoding="utf-8")
    print("wrote", GOLDEN)
