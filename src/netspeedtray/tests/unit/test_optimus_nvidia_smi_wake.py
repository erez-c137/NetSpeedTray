"""
Optimus wake (2.2.1): nvidia-smi goes through NVML, and asking a sleeping hybrid-laptop dGPU anything
powers it up - so the Automatic readout's 5 s nvidia-smi poll kept the dGPU awake for good (battery).

On a hybrid laptop (battery + NVIDIA + a GPU of another maker) nvidia-smi is asked for temperature /
power only while the dGPU is already awake by its own PDH engine utilization (PDH never wakes a GPU);
asleep, they read "—". The VRAM total is a one-shot there, then DXGI's. Desktops are unchanged. A
failing nvidia-smi backs off exponentially instead of retrying every 5 s forever.
"""
import subprocess
import time as _time
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core import gpu_adapters
from netspeedtray.core import monitor_thread as mt
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.monitor_thread import StatsMonitorThread
from netspeedtray.utils import power_utils

MIB = 1024.0 * 1024.0
GIB = 1024 ** 3
IG_LUID, NV_LUID, BR_LUID = 0xD9C1, 0xAB01, 0xDD11
UHD = AdapterInfo("Intel(R) UHD Graphics", 0x8086, (0, IG_LUID), 128 * 2 ** 20, int(7.8 * GIB), False)
APU = AdapterInfo("AMD Radeon(TM) Graphics", 0x1002, (0, IG_LUID), 512 * 2 ** 20, int(15 * GIB), False)
RTX = AdapterInfo("NVIDIA GeForce RTX 4060 Laptop GPU", 0x10DE, (0, NV_LUID), 8 * GIB, int(7.8 * GIB), False)
BRD = AdapterInfo("Microsoft Basic Render Driver", 0x1414, (0, BR_LUID), 0, int(7.8 * GIB), True)
SMI = "52, 8188, 21.5\n"
DXGI_TOTAL_MIB = 8 * 1024.0


def _eng(luid_low, pid, eng=0):
    return f"pid_{pid}_luid_0x00000000_0x{luid_low:08X}_phys_0_eng_{eng}_engtype_3D"


def _inst(luid_low):
    return f"luid_0x00000000_0x{luid_low:08X}_phys_0"


IGPU_BUSY = {_eng(IG_LUID, 10): 25.0, _eng(NV_LUID, 11): 0.0}   # dGPU idle (asleep)
DGPU_BUSY = {_eng(IG_LUID, 10): 5.0, _eng(NV_LUID, 11): 40.0}   # a game on the dGPU


class Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(mt, "time", types.SimpleNamespace(monotonic=c.monotonic, sleep=_time.sleep,
                                                          time=_time.time))
    return c


def _thread(adapters, battery, monkeypatch, smi=SMI):
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: list(adapters))
    monkeypatch.setattr(power_utils, "has_battery", lambda: battery)
    t = StatsMonitorThread(interval=1.0)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter, t._gpu_vram_counter, t._gpu_shared_counter = 1, 2, 3
    t._nvidia_smi_path = "C:/fake/nvidia-smi.exe"
    t._run_child = MagicMock(return_value=smi)
    t._wmi_ohm = False
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    t._init_power_query = MagicMock()
    t._power_query = None
    t._gpu_adapters = list(adapters)
    t.config = dict(t.config or {}, gpu_adapter="")
    return t


def _tick(t, clock, util, temp=True, power=True):
    luids = sorted({gpu_adapters.pdh_instance_luid(k) for k in util} - {None})
    dedicated = {_inst(l[1]): 100 * MIB for l in luids}
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=[util, dedicated, {}]), \
         patch.object(mt.psutil, "virtual_memory", return_value=types.SimpleNamespace(total=32 * GIB)), \
         patch.object(StatsMonitorThread, "_init_ohm_wmi"):
        r = t._poll_gpu_hybrid(include_temp=temp, include_power=power)
    clock.t += 1.0
    return r


def _spawn_times(t, clock, utils):
    """Drive one tick per second over ``utils``; the tick indexes at which nvidia-smi ran."""
    out = []
    for i, util in enumerate(utils):
        before = t._run_child.call_count
        _tick(t, clock, util)
        if t._run_child.call_count > before:
            out.append(i)
    return out


# --- hybrid, dGPU asleep: nvidia-smi never wakes it --------------------------------------------

@pytest.mark.parametrize("igpu", [UHD, APU], ids=["intel", "amd_apu"])
def test_hybrid_idle_dgpu_no_nvidia_smi_spawns(q_app, monkeypatch, clock, igpu):
    t = _thread([igpu, RTX, BRD], True, monkeypatch)
    spawns = _spawn_times(t, clock, [IGPU_BUSY] * 120)
    # One query at startup for the VRAM total (the pre-2.2.1 Automatic contract, golden-pinned),
    # then nothing for two minutes: the dGPU is left asleep.
    assert spawns == [0]
    r = _tick(t, clock, IGPU_BUSY)
    assert (r.temp, r.power) == (None, None)        # asleep: "—", not a frozen reading
    assert r.vram_total == 8188.0                    # the one-shot's total stays


def test_hybrid_idle_with_failing_nvidia_smi_takes_the_total_from_dxgi(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    t._run_child.side_effect = subprocess.CalledProcessError(9, "nvidia-smi")
    spawns = _spawn_times(t, clock, [IGPU_BUSY] * 600)
    assert spawns == [0]                             # the total is never its own reason to wake it again
    r = _tick(t, clock, IGPU_BUSY)
    assert r.vram_total == DXGI_TOTAL_MIB


# --- hybrid, dGPU busy: normal cadence, real readings -----------------------------------------

def test_hybrid_busy_dgpu_spawns_at_the_normal_cadence(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    spawns = _spawn_times(t, clock, [DGPU_BUSY] * 30)
    assert spawns == [0, 5, 10, 15, 20, 25]
    r = _tick(t, clock, DGPU_BUSY)
    assert (r.temp, r.power) == (52.0, 21.5)


def test_hybrid_stops_querying_once_the_dgpu_goes_idle(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    spawns = _spawn_times(t, clock, [DGPU_BUSY] * 10 + [IGPU_BUSY] * 120)
    hold = constants.gpu.HYBRID_DGPU_AWAKE_HOLD_SEC
    assert spawns and spawns[-1] <= 10 + hold        # within the hold, then quiet for good
    assert len(spawns) <= 2 + (10 + hold) / 5


def test_activity_right_after_our_own_call_never_keeps_the_gate_open(q_app, monkeypatch, clock):
    """If waking the dGPU with nvidia-smi registered as engine activity, that must not count."""
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    utils = []
    calls = []

    def run_child(*a, **k):
        calls.append(clock.t)
        return SMI
    t._run_child = MagicMock(side_effect=run_child)
    for i in range(60):
        # Activity only in the second after each of our nvidia-smi calls (its own wake blip).
        busy = bool(calls) and clock.t - calls[-1] <= 1.0
        _tick(t, clock, DGPU_BUSY if busy else IGPU_BUSY)
    assert len(calls) == 1


def test_picked_dgpu_on_a_hybrid_is_left_asleep_too(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    t.config = dict(t.config, gpu_adapter=RTX.name)
    assert _spawn_times(t, clock, [IGPU_BUSY] * 60) == []
    r = _tick(t, clock, IGPU_BUSY)
    assert (r.temp, r.power, r.vram_total) == (None, None, DXGI_TOTAL_MIB)
    t._run_child.return_value = f"{RTX.name}, 52, 8188, 21.5\n"
    spawns = _spawn_times(t, clock, [DGPU_BUSY] * 11)
    assert spawns == [0, 5, 10]


# --- desktops / NVIDIA-only laptops: unchanged ----------------------------------------------------

@pytest.mark.parametrize("adapters,battery", [
    ([RTX, BRD], False),          # desktop, NVIDIA only
    ([UHD, RTX, BRD], False),     # desktop with the iGPU enabled: its dGPU never sleeps
    ([RTX, BRD], True),           # NVIDIA-only (MUX / dGPU mode) laptop
], ids=["desktop", "desktop_igpu_enabled", "nvidia_only_laptop"])
def test_non_hybrid_keeps_the_5s_cadence_even_when_idle(q_app, monkeypatch, clock, adapters, battery):
    t = _thread(adapters, battery, monkeypatch)
    spawns = _spawn_times(t, clock, [IGPU_BUSY] * 30)
    assert spawns == [0, 5, 10, 15, 20, 25]
    r = _tick(t, clock, IGPU_BUSY)
    assert (r.temp, r.power) == (52.0, 21.5)


# --- failure backoff ---------------------------------------------------------------------------

def test_failing_nvidia_smi_backs_off_and_logs_once(q_app, monkeypatch, clock):
    t = _thread([RTX, BRD], False, monkeypatch)
    t._run_child.side_effect = subprocess.TimeoutExpired("nvidia-smi", 1.5)
    spawns = _spawn_times(t, clock, [IGPU_BUSY] * 900)
    # 10, 20, 40, 80, 160, then 300 s apart - not every 5 s (180 calls) forever.
    assert spawns == [0, 10, 30, 70, 150, 310, 610]
    infos = [c for c in t.logger.info.call_args_list if "nvidia-smi failed" in c.args[0]]
    assert len(infos) == 1
    r = _tick(t, clock, IGPU_BUSY)
    assert (r.temp, r.power, r.vram_total) == (None, None, DXGI_TOTAL_MIB)   # DXGI's total, not N/A

    # It answers again: the streak ends, the normal cadence and the readings are back.
    t._run_child.side_effect = None
    t._run_child.return_value = SMI
    clock.t += 400
    spawns = _spawn_times(t, clock, [IGPU_BUSY] * 11)
    assert spawns == [0, 5, 10]
    assert t._nvidia_fail_streak == 0


def test_picked_card_failure_backs_off_too(q_app, monkeypatch, clock):
    t = _thread([RTX, BRD], False, monkeypatch)
    t.config = dict(t.config, gpu_adapter=RTX.name)
    t._run_child.side_effect = subprocess.CalledProcessError(9, "nvidia-smi")
    assert _spawn_times(t, clock, [IGPU_BUSY] * 100) == [0, 10, 30, 70]


# --- the hover list still never spawns -----------------------------------------------------------

def test_the_gpu_list_never_spawns_on_a_hybrid(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    _tick(t, clock, DGPU_BUSY)
    t._run_child.reset_mock()
    t._run_child.side_effect = AssertionError("the GPU list started nvidia-smi")
    for _ in range(10):
        t._gpu_cards(include_temp=True, include_power=True)
        clock.t += 1.0
    t._run_child.assert_not_called()
