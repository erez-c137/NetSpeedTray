"""
#236 (balciseri, 2026-11-08): Ryzen 7 9800X3D ("AMD Radeon(TM) Graphics") + RTX 5070, temperatures
from LibreHardwareMonitor. The widget read "3% (38°C, 17.5W)": the NVIDIA's utilization and power,
but the APU's "GPU VR SoC" (a voltage-regulator sensor) as the temperature. NVIDIA said 26 °C.

Temperature, power and utilization for "the GPU" must describe ONE adapter. The fixture below is
that machine's LHM tree (as data.json, and as LHM 0.9.4's WMI rows), sensor for sensor.
"""
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.core import gpu_adapters
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.lhm_http import LhmGpu, LhmSnapshot, parse_snapshot
from netspeedtray.core.monitor_thread import StatsMonitorThread

MIB = 1024.0 * 1024.0
GIB = 1024 ** 3
APU_LUID, RTX_LUID = 0xD9C1, 0xAB01
APU = AdapterInfo("AMD Radeon(TM) Graphics", 0x1002, (0, APU_LUID), 512 * 2 ** 20, int(15 * GIB), False)
RTX = AdapterInfo("NVIDIA GeForce RTX 5070", 0x10DE, (0, RTX_LUID), 12 * GIB, int(15 * GIB), False)
BRD = AdapterInfo("Microsoft Basic Render Driver", 0x1414, (0, 0xDD11), 0, int(15 * GIB), True)


def _eng(luid_low, pid, eng=0):
    return f"pid_{pid}_luid_0x00000000_0x{luid_low:08X}_phys_0_eng_{eng}_engtype_3D"


def _sensor(text, sensor_id, sensor_type, value):
    return {"Text": text, "SensorId": sensor_id, "Type": sensor_type, "Value": value,
            "Min": "", "Max": "", "ImageURL": "images/transparent.png"}


def _group(text, *children):
    return {"Text": text, "Min": "", "Value": "", "Max": "", "Children": list(children)}


def balciseri_data_json(nvidia_temps=True):
    """The screenshot's LHM tree (Italian locale: decimal commas), AMD node listed first."""
    nvidia_t = [
        _sensor("GPU Core", "/gpu-nvidia/0/temperature/0", "Temperature", "26,0 °C"),
        _sensor("GPU Hot Spot", "/gpu-nvidia/0/temperature/2", "Temperature", "27,0 °C"),
        _sensor("GPU Memory Junction", "/gpu-nvidia/0/temperature/3", "Temperature", "255,0 °C"),
    ] if nvidia_temps else []
    return {"Text": "Sensor", "Children": [{"Text": "PC_CASA", "Children": [
        {"Text": "AMD Ryzen 7 9800X3D", "HardwareId": "/amdcpu/0", "Children": [
            _group("Temperatures", _sensor("Core (Tctl/Tdie)", "/amdcpu/0/temperature/2",
                                           "Temperature", "39,0 °C"))]},
        {"Text": "AMD Radeon(TM) Graphics", "HardwareId": "/gpu-amd/0", "Children": [
            _group("Powers",
                   _sensor("GPU Core", "/gpu-amd/0/power/0", "Power", "6,0 W"),
                   _sensor("GPU SoC", "/gpu-amd/0/power/1", "Power", "14,0 W")),
            _group("Temperatures",
                   _sensor("GPU VR SoC", "/gpu-amd/0/temperature/4", "Temperature", "38,0 °C")),
            _group("Load", _sensor("GPU Core", "/gpu-amd/0/load/0", "Load", "0,0 %"))]},
        {"Text": "NVIDIA GeForce RTX 5070", "HardwareId": "/gpu-nvidia/0", "Children": [
            _group("Powers", _sensor("GPU Package", "/gpu-nvidia/0/power/0", "Power", "17,5 W")),
            _group("Temperatures", *nvidia_t),
            _group("Load", _sensor("GPU Core", "/gpu-nvidia/0/load/0", "Load", "2,0 %"))]},
    ]}]}


def _wmi_row(name, identifier, value):
    return types.SimpleNamespace(Name=name, Identifier=identifier, Value=value)


# LHM 0.9.4's WMI provider lists its rows in the order sensors went live - not grouped by card.
# This order reproduces the report exactly with the old "first 'gpu' row of each type" rule.
WMI_TEMPS = [
    _wmi_row("GPU VR SoC", "/gpu-amd/0/temperature/4", 38.0),
    _wmi_row("GPU Core", "/gpu-nvidia/0/temperature/0", 26.0),
    _wmi_row("GPU Hot Spot", "/gpu-nvidia/0/temperature/2", 27.0),
    _wmi_row("GPU Memory Junction", "/gpu-nvidia/0/temperature/3", 255.0),
]
WMI_POWERS = [
    _wmi_row("GPU Package", "/gpu-nvidia/0/power/0", 17.5),
    _wmi_row("GPU Core", "/gpu-amd/0/power/0", 6.0),
    _wmi_row("GPU SoC", "/gpu-amd/0/power/1", 14.0),
]


def _wmi(temps=WMI_TEMPS, powers=WMI_POWERS):
    wmi = MagicMock()
    wmi.ExecQuery.side_effect = lambda q: list(temps if "Temperature" in q else powers if "Power" in q else [])
    return wmi


@pytest.fixture
def thread(q_app, monkeypatch):
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: None)
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter, t._gpu_vram_counter, t._gpu_shared_counter = 1, 2, 3
    t._nvidia_smi_path = None
    t._wmi_ohm = False
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    t._init_power_query = MagicMock()
    t._power_query = None
    t._gpu_adapters = [APU, RTX, BRD]
    t.config = dict(t.config or {}, gpu_adapter="")
    return t


def _poll(t, util):
    dedicated = {f"luid_0x00000000_0x{APU_LUID:08X}_phys_0": 300 * MIB,
                 f"luid_0x00000000_0x{RTX_LUID:08X}_phys_0": 1200 * MIB}
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=[util, dedicated, {}]), \
         patch("netspeedtray.core.monitor_thread.psutil.virtual_memory",
               return_value=types.SimpleNamespace(total=32 * GIB)), \
         patch.object(StatsMonitorThread, "_init_ohm_wmi"):
        return t._poll_gpu_hybrid(include_temp=True, include_power=True)


NVIDIA_BUSIER = {_eng(RTX_LUID, 10): 3.0, _eng(APU_LUID, 20): 0.0}


# --- the parser: one card's die temperature, never a VR or memory sensor -------------------------

def test_data_json_groups_each_card_and_skips_vr_and_junk():
    snap = parse_snapshot(balciseri_data_json())
    by_id = {g.hardware_id: g for g in snap.gpus}
    nv, amd = by_id["/gpu-nvidia/0"], by_id["/gpu-amd/0"]
    assert (nv.temperature, nv.temperature_sensor, nv.power) == (26.0, "GPU Core", 17.5)
    # The APU's only temperature is a voltage regulator: not the GPU's temperature.
    assert amd.temperature is None
    assert amd.power == 6.0
    assert snap.preferred_gpu is nv


def test_memory_junction_never_stands_in_for_the_die():
    tree = balciseri_data_json(nvidia_temps=False)
    nv_node = tree["Children"][0]["Children"][2]
    nv_node["Children"][1]["Children"] = [
        _sensor("GPU Memory Junction", "/gpu-nvidia/0/temperature/3", "Temperature", "255,0 °C"),
        _sensor("GPU Memory Junction", "/gpu-nvidia/0/temperature/4", "Temperature", "84,0 °C")]
    nv = parse_snapshot(tree).gpus[0]
    assert nv.hardware_id == "/gpu-nvidia/0"
    assert nv.temperature is None and nv.power == 17.5


def test_hot_spot_when_there_is_no_core_sensor():
    tree = balciseri_data_json(nvidia_temps=False)
    tree["Children"][0]["Children"][2]["Children"][1]["Children"] = [
        _sensor("GPU Memory Junction", "/gpu-nvidia/0/temperature/3", "Temperature", "90,0 °C"),
        _sensor("GPU Hot Spot", "/gpu-nvidia/0/temperature/2", "Temperature", "27,0 °C")]
    assert parse_snapshot(tree).gpus[0].temperature == 27.0


# --- Automatic on the hybrid machine --------------------------------------------------------------

def test_automatic_lhm_web_server_reads_the_nvidia_for_all_three(thread):
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    r = _poll(thread, NVIDIA_BUSIER)
    assert (r.util, r.temp, r.power) == (3.0, 26.0, 17.5)


def test_automatic_wmi_fallback_reads_one_card_not_the_first_row_of_each_type(thread):
    """LHM 0.9.4 over WMI (no web server): the report itself - 38 °C was the APU's VR SoC."""
    thread._wmi_ohm = _wmi()
    r = _poll(thread, NVIDIA_BUSIER)
    assert (r.util, r.temp, r.power) == (3.0, 26.0, 17.5)


def test_wmi_fallback_without_dxgi_still_keeps_temp_and_power_on_one_card(thread):
    thread._gpu_adapters = None
    thread._wmi_ohm = _wmi()
    r = _poll(thread, NVIDIA_BUSIER)
    assert (r.temp, r.power) == (26.0, 17.5)


def test_web_server_card_without_a_temp_is_not_completed_from_another_card(thread):
    """LHM's NVIDIA group has power but no temperature: WMI must not lend it the APU's 38 °C."""
    thread._gpu_adapters = None
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json(nvidia_temps=False))
    thread._wmi_ohm = _wmi(temps=[WMI_TEMPS[0]])
    r = _poll(thread, NVIDIA_BUSIER)
    assert r.power == 17.5
    assert r.temp is None


def test_automatic_follows_a_clearly_busier_igpu_with_its_own_sensors(thread):
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    r = _poll(thread, {_eng(RTX_LUID, 10): 2.0, _eng(APU_LUID, 20): 40.0})
    assert r.util == 40.0
    assert r.temp is None        # the APU has no die sensor in LHM - not the RTX's 26 °C
    assert r.power == 6.0        # the APU's, not the RTX's 17.5 W


def test_automatic_does_not_flip_cards_on_idle_noise(thread):
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    first = _poll(thread, {})                                                     # idle: the dGPU
    noise = _poll(thread, {_eng(RTX_LUID, 10): 2.0, _eng(APU_LUID, 20): 8.0})     # within the margin
    assert (first.temp, first.power) == (26.0, 17.5)
    assert (noise.util, noise.temp, noise.power) == (2.0, 26.0, 17.5)
    busy = _poll(thread, {_eng(RTX_LUID, 10): 2.0, _eng(APU_LUID, 20): 40.0})
    back = _poll(thread, {_eng(RTX_LUID, 10): 60.0, _eng(APU_LUID, 20): 40.0})
    assert busy.power == 6.0 and (back.util, back.temp, back.power) == (60.0, 26.0, 17.5)


# --- a picked GPU --------------------------------------------------------------------------------

def test_picked_apu_never_reads_its_vr_sensor_as_the_gpu_temperature(thread):
    thread.config = dict(thread.config, gpu_adapter=APU.name)
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    r = _poll(thread, NVIDIA_BUSIER)
    assert (r.util, r.temp, r.power) == (0.0, None, 6.0)


def test_picked_rtx_reads_the_rtx(thread):
    thread.config = dict(thread.config, gpu_adapter=RTX.name)
    thread._wmi_ohm = _wmi()
    r = _poll(thread, NVIDIA_BUSIER)
    assert (r.util, r.temp, r.power) == (3.0, 26.0, 17.5)


# --- one GPU: Automatic is untouched --------------------------------------------------------------

def test_single_gpu_automatic_is_the_plain_busiest_engine(thread):
    thread._gpu_adapters = [RTX, BRD]
    thread._lhm_http.get_snapshot.return_value = LhmSnapshot(
        gpus=(LhmGpu("/gpu-nvidia/0", "NVIDIA GeForce RTX 5070", 26.0, "GPU Core", 17.5, "GPU Package"),))
    r = _poll(thread, {_eng(RTX_LUID, 10): 30.0, "unparseable_instance": 45.0})
    assert (r.util, r.temp, r.power) == (45.0, 26.0, 17.5)
    assert thread._auto_gpu_luid is None
