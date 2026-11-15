"""
GPU poll cost (2.2.1, pre-beta review): what one poll spends on the GPU, at the new 0.5 s rate too.

1. The `\\GPU Engine(*)\\Utilization Percentage` array is parsed ONCE per poll; the taskbar readout,
   the hover card's per-GPU rows and the Optimus gate read that one parse. It used to be parsed up to
   three times (~6 ms a pass on a big array). The values must be exactly what the old per-use parses
   gave - the reference implementations below are the pre-2.2.1 functions, verbatim.
2. The Optimus gate is not judged on a poll that wants neither GPU temperature nor power (nvidia-smi
   cannot be asked then, and nothing else reads the gate).
3. A failed LibreHardwareMonitor / OpenHardwareMonitor WMI probe is not repeated for
   constants.lhm.WMI_PROBE_BACKOFF_SEC - it ran every poll, up to three times a poll, on every
   NVIDIA desktop without LHM.
"""
import math
import random
import time as _time
import types
from typing import Any, Dict, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core import gpu_adapters
from netspeedtray.core import monitor_thread as mt
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.monitor_thread import (StatsMonitorThread, aggregate_gpu_engine_utilization,
                                              parse_gpu_engine_utilization, per_adapter_engine_utilization)
from netspeedtray.utils import power_utils

MIB = 1024.0 * 1024.0
GIB = 1024 ** 3
IG_LUID, NV_LUID, BR_LUID = 0xD9C1, 0xAB01, 0xDD11
UHD = AdapterInfo("Intel(R) UHD Graphics", 0x8086, (0, IG_LUID), 128 * 2 ** 20, int(7.8 * GIB), False)
RTX = AdapterInfo("NVIDIA GeForce RTX 4060 Laptop GPU", 0x10DE, (0, NV_LUID), 8 * GIB, int(7.8 * GIB), False)
BRD = AdapterInfo("Microsoft Basic Render Driver", 0x1414, (0, BR_LUID), 0, int(7.8 * GIB), True)
SMI = "52, 8188, 21.5\n"


# --- the pre-2.2.1 functions, verbatim: the oracle for "values identical" -------------------------

def _old_aggregate(samples: Dict[Any, Any], luid: Optional[Tuple[int, int]] = None) -> float:
    engines: Dict[Any, float] = {}
    for instance, value in samples.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            continue
        match = (constants.gpu.ENGINE_INSTANCE_PATTERN.match(instance)
                 if isinstance(instance, str) else None)
        if match:
            luid_high, luid_low, phys, eng = match.groups()
            key: Any = (int(luid_high, 16), int(luid_low, 16), int(phys), int(eng))
            if luid is not None and key[:2] != tuple(luid):
                continue
        elif luid is not None:
            continue
        else:
            key = ("unparsed", instance)
        engines[key] = engines.get(key, 0.0) + value
    return max(engines.values(), default=0.0)


def _old_per_adapter(samples: Dict[Any, Any]) -> Dict[Tuple[int, int], float]:
    engines: Dict[Tuple[int, int, int, int], float] = {}
    for instance, value in samples.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            continue
        match = (constants.gpu.ENGINE_INSTANCE_PATTERN.match(instance)
                 if isinstance(instance, str) else None)
        if not match:
            continue
        luid_high, luid_low, phys, eng = match.groups()
        key = (int(luid_high, 16), int(luid_low, 16), int(phys), int(eng))
        engines[key] = engines.get(key, 0.0) + value
    out: Dict[Tuple[int, int], float] = {}
    for key, value in engines.items():
        out[key[:2]] = max(out.get(key[:2], 0.0), value)
    return out


def _eng(luid_low, pid, eng=0, phys=0, luid_high=0, engtype="3D"):
    return f"pid_{pid}_luid_0x{luid_high:08X}_0x{luid_low:08X}_phys_{phys}_eng_{eng}_engtype_{engtype}"


def _inst(luid_low):
    return f"luid_0x00000000_0x{luid_low:08X}_phys_0"


def _big_array(seed: int, processes: int = 300, engines: int = 12) -> Dict[Any, Any]:
    """A big machine's array: three adapters (one with a high LUID part and a second phys), many
    processes on many engines, plus every value / name the parser must skip or keep apart."""
    rng = random.Random(seed)
    adapters = [(0, IG_LUID, 0), (0, NV_LUID, 0), (0x1, 0x2F3E, 1)]
    out: Dict[Any, Any] = {}
    for pid in range(1000, 1000 + processes):
        for eng in range(engines):
            hi, lo, phys = adapters[(pid + eng) % len(adapters)]
            value = rng.choice([0.0, 0.0, -0.0, rng.random() * 40.0, rng.uniform(0, 3) * 1e-3])
            out[_eng(lo, pid, eng, phys, hi, engtype=rng.choice(["3D", "Copy", "VideoDecode", ""]))] = value
    out.update({
        "pid_1_luid_0x00000000_0x0000AB01_phys_0_eng_0_engtype_3D".lower(): 5.0,  # case differs: same engine
        "unparseable_instance": 33.0,
        "_Total": 12.0,
        7: 3.0,                                       # a non-str key: its own engine
        _eng(NV_LUID, 9, 1): float("nan"),
        _eng(NV_LUID, 10, 1): float("inf"),
        _eng(NV_LUID, 11, 1): -4.0,
        _eng(NV_LUID, 12, 1): True,
        _eng(NV_LUID, 13, 1): "45",
        _eng(NV_LUID, 14, 1): None,
    })
    return out


SMALL_ARRAYS = [
    {},
    {_eng(IG_LUID, 10): 25.0, _eng(NV_LUID, 11): 0.0},
    {_eng(NV_LUID, 1): 44.0, _eng(NV_LUID, 2): 34.0, _eng(NV_LUID, 3): 13.0},       # #328: summed
    {"unparseable_instance": 70.0, _eng(NV_LUID, 1): 5.0},                            # unparsed is busiest
    {"a": 1.0, "b": 2.0},                                                             # unparsed only
    {_eng(NV_LUID, 1): -0.0, "x": -0.0},
    {_eng(NV_LUID, 1, eng=0): 45.0, _eng(NV_LUID, 2, eng=1): 50.0, _eng(IG_LUID, 3): 150.0},
]


@pytest.mark.parametrize("samples", SMALL_ARRAYS + [_big_array(s) for s in range(4)])
def test_one_parse_reads_exactly_what_the_old_per_use_parses_read(samples):
    parsed = parse_gpu_engine_utilization(samples)
    old_per = _old_per_adapter(samples)
    assert parsed.per_adapter == old_per
    assert per_adapter_engine_utilization(samples) == old_per
    luids = list(old_per) + [(0, 0x1234), [0, NV_LUID]]       # an absent LUID; a list-typed LUID
    for luid in [None] + luids:
        expected = _old_aggregate(samples, luid)
        assert parsed.busiest(luid) == expected
        assert aggregate_gpu_engine_utilization(samples, luid) == expected
        # Bit-identical, not just equal (0.0 vs -0.0 included).
        assert math.copysign(1.0, parsed.busiest(luid)) == math.copysign(1.0, expected)


# --- the poll: one parse, shared ------------------------------------------------------------------

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


def _hybrid(monkeypatch):
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: [UHD, RTX, BRD])
    monkeypatch.setattr(power_utils, "ac_line_status", lambda: 1)
    monkeypatch.setattr(power_utils, "battery_presence", lambda: True)
    monkeypatch.setattr(power_utils, "platform_is_mobile", lambda: True)
    t = StatsMonitorThread(interval=1.0)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter, t._gpu_vram_counter, t._gpu_shared_counter = 1, 2, 3
    t._nvidia_smi_path = "C:/fake/nvidia-smi.exe"
    t._run_child = MagicMock(return_value=SMI)
    t._wmi_ohm = False
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    t._init_power_query = MagicMock()
    t._power_query = None
    t._gpu_adapters = [UHD, RTX, BRD]
    t.config = dict(t.config or {}, gpu_adapter="")
    return t


def _tick(t, clock, util, temp=True, power=True):
    dedicated = {_inst(IG_LUID): 100 * MIB, _inst(NV_LUID): 300 * MIB}
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=[util, dedicated, {}]), \
         patch.object(mt.psutil, "virtual_memory", return_value=types.SimpleNamespace(total=32 * GIB)), \
         patch.object(StatsMonitorThread, "_init_ohm_wmi"):
        r = t._poll_gpu_hybrid(include_temp=temp, include_power=power)
    clock.t += 1.0
    return r


class _CountingPattern:
    """ENGINE_INSTANCE_PATTERN, counting the instance names it is asked to match."""

    def __init__(self, pattern):
        self.pattern, self.calls = pattern, 0

    def match(self, text):
        self.calls += 1
        return self.pattern.match(text)


def test_a_hybrid_poll_and_its_hover_list_parse_the_array_once(q_app, monkeypatch, clock):
    """Readout + Optimus gate + hover list: one pass over the array (was three)."""
    t = _hybrid(monkeypatch)
    util = {_eng(IG_LUID, 10): 5.0, _eng(NV_LUID, 11): 40.0, _eng(NV_LUID, 12, eng=2): 7.0}
    _tick(t, clock, util)                       # warm-up: the first sample's window has no start
    counter = _CountingPattern(constants.gpu.ENGINE_INSTANCE_PATTERN)
    with patch.object(constants.gpu, "ENGINE_INSTANCE_PATTERN", counter), \
         patch.object(StatsMonitorThread, "_nvidia_awake", autospec=True,
                      side_effect=StatsMonitorThread._nvidia_awake) as awake:
        r = _tick(t, clock, util)
        cards = t._gpu_cards(True, True)
    assert awake.call_count == 1                # the gate was judged on this poll...
    assert counter.calls == len(util)           # ...from the same single parse
    assert r.util == 40.0
    by_luid = {c.luid: c.util for c in cards}
    assert by_luid == {(0, NV_LUID): 40.0, (0, IG_LUID): 5.0}


# --- the Optimus gate is not judged when nothing could use it -------------------------------------

IGPU_BUSY = {_eng(IG_LUID, 10): 25.0, _eng(NV_LUID, 11): 0.0}
DGPU_BUSY = {_eng(IG_LUID, 10): 5.0, _eng(NV_LUID, 11): 40.0}


def test_no_gpu_temperature_or_power_skips_the_optimus_gate(q_app, monkeypatch, clock):
    t = _hybrid(monkeypatch)
    with patch.object(StatsMonitorThread, "_nvidia_awake", autospec=True,
                      side_effect=StatsMonitorThread._nvidia_awake) as awake:
        for util in [DGPU_BUSY, IGPU_BUSY] * 5:
            r = _tick(t, clock, util, temp=False, power=False)
            assert (r.temp, r.power) == (None, None)
        assert awake.call_count == 0
        assert t._run_child.call_count == 0     # and nvidia-smi never ran, as before
        # Either one on: judged on every poll again, and a busy dGPU is read.
        _tick(t, clock, DGPU_BUSY, temp=True, power=False)
        assert awake.call_count == 1
        r = _tick(t, clock, DGPU_BUSY, temp=True, power=False)
        assert r.temp == 52.0 and r.power is None
        _tick(t, clock, IGPU_BUSY, temp=False, power=True)
        assert awake.call_count == 3


def test_no_gpu_temperature_or_power_keeps_the_poll_result_identical(q_app, monkeypatch, clock):
    """Skipping the gate changes no value the poll returns or the hover list shows."""
    t = _hybrid(monkeypatch)
    results = [(_tick(t, clock, u, temp=False, power=False), t._gpu_cards(False, False))
               for u in [DGPU_BUSY, IGPU_BUSY, DGPU_BUSY]]
    assert [r.util for r, _ in results] == [40.0, 25.0, 40.0]
    for r, cards in results:
        assert (r.temp, r.power, r.vram_total) == (None, None, 8 * 1024.0)   # DXGI's total, no nvidia-smi
        assert all(c.temp is None and c.power is None for c in cards)


# --- the LHM/OHM WMI probe backs off --------------------------------------------------------------

class _FakeCom:
    def __init__(self):
        self.calls = 0
        self.available = False
        self.obj = MagicMock()
        self.obj.ExecQuery.return_value = [object(), object()]

    def GetObject(self, moniker):
        self.calls += 1
        if self.available and "LibreHardwareMonitor" in moniker:
            return self.obj
        raise OSError(f"no namespace {moniker}")


def _wmi_thread(monkeypatch, com):
    monkeypatch.setattr(mt, "_com_client", lambda: com)
    t = StatsMonitorThread(interval=1.0)
    t.logger = MagicMock()
    t._ohm_outage.logger = MagicMock()
    return t


GRACE = constants.lhm.NOT_DETECTED_GRACE_SEC
BACKOFF = constants.lhm.WMI_PROBE_BACKOFF_SEC


def test_a_failed_wmi_probe_waits_the_backoff_once_nothing_was_ever_found(q_app, monkeypatch, clock):
    """The steady state of every NVIDIA desktop without LHM: one probe per backoff window."""
    com = _FakeCom()
    t = _wmi_thread(monkeypatch, com)
    t._init_ohm_wmi()                                     # the first probe starts the grace
    clock.t += GRACE
    t._init_ohm_wmi()
    assert com.calls == 4 and t._wmi_ohm is None          # both namespaces, twice, none there
    for _ in range(3):                                    # CPU temp, CPU power, GPU: same poll
        t._init_ohm_wmi()
    clock.t += BACKOFF - 0.5
    t._init_ohm_wmi()
    assert com.calls == 4                                 # nothing re-probed inside the backoff
    clock.t += 0.5
    com.available = True                                  # LHM started meanwhile: picked up
    t._init_ohm_wmi()
    assert com.calls == 5 and t._wmi_ohm is com.obj


def test_the_start_up_grace_probes_every_poll(q_app, monkeypatch, clock):
    """LHM's WMI coming up after NetSpeedTray at login (or initialising with 0 sensors): every poll
    of the first NOT_DETECTED_GRACE_SEC probes, so it is found on the poll it appears, as before."""
    com = _FakeCom()
    t = _wmi_thread(monkeypatch, com)
    polls = 0
    while polls < GRACE:
        t._init_ohm_wmi()
        clock.t += 1.0
        polls += 1
    assert com.calls == 2 * polls                         # every poll probed both namespaces
    com.available = True
    clock.t -= 1.0                                        # still inside the grace
    t._init_ohm_wmi()
    assert t._wmi_ohm is com.obj


def test_a_lost_working_connection_is_re_probed_every_poll(q_app, monkeypatch, clock):
    """A one-poll hiccup: the drop's own re-probe fails on the same transient, and the next poll's
    succeeds - no 30 s of blank temperatures (main recovered on the next poll; so does this)."""
    com = _FakeCom()
    com.available = True
    t = _wmi_thread(monkeypatch, com)
    t._init_ohm_wmi()
    assert t._wmi_ohm is com.obj
    clock.t += GRACE + 60.0                               # long past the start-up grace
    # The connection drops (a failed sensor query does this) and the transient is still there.
    t._wmi_ohm = None
    t._ohm_outage.failed("CPU temperature query failed: RPC")
    com.available = False
    for _ in range(3):
        t._init_ohm_wmi()
        clock.t += 1.0
    assert com.calls == 1 + 3 * 2                         # no backoff while the source is lost
    com.available = True
    t._init_ohm_wmi()
    assert t._wmi_ohm is com.obj and not t._ohm_outage.lost


def test_a_settings_change_clears_the_probe_backoff(q_app, monkeypatch, clock):
    t = _wmi_thread(monkeypatch, _FakeCom())
    t._lhm_http = MagicMock()
    t._ohm_retry_at = clock.t + 1000.0
    t._ohm_first_probe_at = clock.t - 1000.0
    t._hw_queries_dirty = True
    t._is_running = True

    def stop_after_one_tick(*_a, **_k):
        t._is_running = False
        return {}

    with patch.object(mt.psutil, "net_io_counters", side_effect=stop_after_one_tick), \
         patch.object(t, "_init_com"):
        t.run()
    assert t._ohm_retry_at == 0.0                         # probed on the next poll
    assert t._ohm_first_probe_at is None                  # with a fresh grace


def test_the_backoff_constant_is_validated():
    assert constants.lhm.WMI_PROBE_BACKOFF_SEC > 0
    with patch.object(constants.lhm, "WMI_PROBE_BACKOFF_SEC", 0):
        with pytest.raises(ValueError):
            constants.lhm.validate()
