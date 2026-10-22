"""
rc.2 batch G-HW: G4 (eGPU hot-plug re-reads the DXGI adapter list), G5 (RAM-only / VRAM-only
configs collect their data), G6 (greyed Settings > Hardware cards say why), F4 (#236 GPU picker,
small form) and F8 (°C / °F display unit; closes the v1.4.0 promise, O6).
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtGui import QFont, QFontMetrics

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import gpu_adapters
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.lhm_http import LhmGpu, LhmSnapshot
from netspeedtray.core.monitor_thread import (StatsMonitorThread, aggregate_gpu_engine_utilization,
                                              memory_collection_gates)
from netspeedtray.utils import helpers

EN = I18nStrings("en_US")
MIB = 1024.0 * 1024.0
GIB = 1024 ** 3

IGPU_I = "luid_0x00000000_0x0000D9C1_phys_0"
DGPU_I = "luid_0x00000000_0x0000AB01_phys_0"
INTEL_UHD = AdapterInfo("Intel(R) UHD Graphics 770", 0x8086, (0, 0xD9C1), 128 * 2 ** 20, int(8 * GIB), False)
BASIC_RENDER = AdapterInfo("Microsoft Basic Render Driver", 0x1414, (0, 0xDD11), 0, int(8 * GIB), True)
RADEON = AdapterInfo("AMD Radeon RX 6600", 0x1002, (0, 0xAB01), 8 * GIB, int(8 * GIB), False)
RTX = AdapterInfo("NVIDIA GeForce RTX 4070", 0x10DE, (0, 0xAB01), 12 * GIB, int(8 * GIB), False)


def _eng(luid_low: int, pid: int, eng: int = 0) -> str:
    return f"pid_{pid}_luid_0x00000000_0x{luid_low:08X}_phys_0_eng_{eng}_engtype_3D"


@pytest.fixture
def thread(q_app, monkeypatch):
    # Never the real DXGI from a unit test (review L3); tests that exercise G4 patch it themselves.
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: None)
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter = 1
    t._gpu_vram_counter = 2
    t._gpu_shared_counter = 3
    t._nvidia_smi_path = None
    t._wmi_ohm = False
    t._lhm_http = MagicMock()
    t._lhm_http.get_snapshot.return_value = None
    return t


def _poll(thread, util, dedicated, shared, include_temp=False, include_power=False):
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=[util, dedicated, shared]), \
         patch("netspeedtray.core.monitor_thread.psutil.virtual_memory",
               return_value=types.SimpleNamespace(total=32 * GIB)), \
         patch.object(StatsMonitorThread, "_init_ohm_wmi"):
        return thread._poll_gpu_hybrid(include_temp=include_temp, include_power=include_power)


# --- G4: an eGPU hot-plugged without a display change ------------------------------------------

def test_hot_plugged_egpu_is_picked_up_without_a_query_rebuild(thread):
    thread._gpu_adapters = [INTEL_UHD, BASIC_RENDER]
    before = _poll(thread, {}, {IGPU_I: 0.0}, {IGPU_I: 600 * MIB})
    assert before.vram_is_shared is True
    with patch.object(gpu_adapters, "enumerate_adapters",
                      return_value=[INTEL_UHD, BASIC_RENDER, RADEON]) as enum:
        after = _poll(thread, {}, {IGPU_I: 0.0, DGPU_I: 2048 * MIB}, {IGPU_I: 600 * MIB, DGPU_I: 2 * MIB})
        again = _poll(thread, {}, {IGPU_I: 0.0, DGPU_I: 2048 * MIB}, {IGPU_I: 600 * MIB, DGPU_I: 2 * MIB})
    assert after.vram_is_shared is False and after.vram_used == pytest.approx(2048.0)
    assert again.vram_is_shared is False
    assert enum.call_count == 1          # once per change of the PDH adapter set, not per poll


def test_unplugged_egpu_drops_its_stale_dedicated_verdict(thread):
    thread._gpu_adapters = [INTEL_UHD, RADEON]
    with patch.object(gpu_adapters, "enumerate_adapters", return_value=[INTEL_UHD, BASIC_RENDER]):
        r = _poll(thread, {}, {IGPU_I: 0.0}, {IGPU_I: 700 * MIB})
    assert r.vram_is_shared is True and r.vram_used == pytest.approx(700.0)


def test_matching_adapter_set_never_re_enumerates(thread):
    thread._gpu_adapters = [INTEL_UHD, BASIC_RENDER]
    with patch.object(gpu_adapters, "enumerate_adapters") as enum:
        for _ in range(3):
            _poll(thread, {}, {IGPU_I: 0.0}, {IGPU_I: 600 * MIB})
    enum.assert_not_called()


# --- G5: RAM / VRAM have their own switches -----------------------------------------------------

@pytest.mark.parametrize("cfg, expected", [
    ({"monitor_ram_enabled": True}, (True, False)),
    ({"monitor_vram_enabled": True}, (False, True)),
    ({"monitor_cpu_enabled": True}, (True, False)),
    ({}, (False, False)),
])
def test_memory_gates_follow_their_own_switches(cfg, expected):
    assert memory_collection_gates(cfg, False) == expected
    assert memory_collection_gates(cfg, True) == (True, True)


def _one_tick(thread, config):
    thread.config = config
    thread._is_running = True
    got = {}

    def emit(stats):
        got.update(stats)
        thread._is_running = False

    thread.stats_ready = types.SimpleNamespace(emit=emit)
    gpu = MagicMock(return_value=types.SimpleNamespace(
        util=40.0, vram_used=2048.0, vram_total=8192.0, temp=60.0, power=30.0,
        vram_is_shared=False, present=True))
    with patch("netspeedtray.core.monitor_thread.is_rdp_session", return_value=False), \
         patch.object(thread, "_init_com"), patch.object(thread, "_cleanup_com"), \
         patch.object(thread, "_poll_gpu_hybrid", gpu), \
         patch.object(thread, "_collect_power_stats"), \
         patch.object(thread, "_poll_cpu_temperature", return_value=50.0) as cpu_temp:
        thread.run()
    return got, gpu, cpu_temp


def test_ram_only_config_collects_ram_and_nothing_else(thread):
    stats, gpu, cpu_temp = _one_tick(thread, {"monitor_ram_enabled": True, "show_hardware_temps": True})
    assert stats["ram_used"] > 0 and stats["ram_total"] > 0
    assert "cpu" not in stats and "cpu_temp" not in stats
    gpu.assert_not_called()
    cpu_temp.assert_not_called()


def test_vram_only_config_collects_vram_without_temps_or_util(thread):
    stats, gpu, _ = _one_tick(thread, {"monitor_vram_enabled": True, "show_hardware_temps": True})
    assert stats["vram_used"] == pytest.approx(2.0)
    assert "gpu" not in stats and "gpu_temp" not in stats and "ram_used" not in stats
    gpu.assert_called_once_with(include_temp=False, include_power=False)


def test_controller_forwards_ram_and_vram_without_cpu_or_gpu(q_app):
    from netspeedtray.core.controller import StatsController
    ctl = StatsController({"record_hardware_history": True}, MagicMock())
    ram, vram = [], []
    ctl.ram_info_updated.connect(lambda u, t: ram.append((u, t)))
    ctl.vram_info_updated.connect(lambda u, t: vram.append((u, t)))
    ctl.handle_stats({"ram_used": 4.0, "ram_total": 16.0})
    ctl.handle_stats({"vram_used": 2.0, "vram_total": 8.0, "gpu_present": True})
    assert ram == [(4.0, 16.0)] and vram == [(2.0, 8.0)]


# --- F4: GPU picker -----------------------------------------------------------------------------

def test_engine_aggregation_filters_by_luid_and_auto_is_unchanged():
    samples = {_eng(0xD9C1, 1): 30.0, _eng(0xD9C1, 2): 20.0, _eng(0xAB01, 3): 70.0, "garbage": 90.0}
    assert aggregate_gpu_engine_utilization(samples) == 90.0
    assert aggregate_gpu_engine_utilization(samples, (0, 0xD9C1)) == 50.0
    assert aggregate_gpu_engine_utilization(samples, (0, 0xAB01)) == 70.0


def test_picked_igpu_reads_its_own_load_and_shared_memory_on_an_nvidia_box(thread):
    thread._gpu_adapters = [INTEL_UHD, RTX]
    thread._nvidia_smi_path = "nvidia-smi.exe"
    thread._nvidia_cache_vram_total = 12288.0
    thread._nvidia_last_poll = float("inf")    # never spawn
    thread.config = {"gpu_adapter": "intel(r) uhd graphics 770 "}
    util = {_eng(0xD9C1, 1): 12.0, _eng(0xAB01, 2): 80.0}
    r = _poll(thread, util, {IGPU_I: 0.0, DGPU_I: 3000 * MIB}, {IGPU_I: 640 * MIB, DGPU_I: 5 * MIB})
    assert r.util == pytest.approx(12.0)
    assert r.vram_is_shared is True and r.vram_used == pytest.approx(640.0)
    assert r.vram_total == pytest.approx(8 * 1024)


def test_picked_dgpu_reads_its_own_dedicated_memory_and_dxgi_total(thread):
    thread._gpu_adapters = [INTEL_UHD, RADEON]
    thread.config = {"gpu_adapter": "AMD Radeon RX 6600"}
    r = _poll(thread, {_eng(0xD9C1, 1): 60.0, _eng(0xAB01, 2): 25.0},
              {IGPU_I: 100 * MIB, DGPU_I: 3000 * MIB}, {IGPU_I: 640 * MIB, DGPU_I: 5 * MIB})
    assert r.util == pytest.approx(25.0)
    assert r.vram_is_shared is False and r.vram_used == pytest.approx(3000.0)
    assert r.vram_total == pytest.approx(8 * 1024)


def test_picked_gpu_takes_temperature_only_from_its_own_maker(thread):
    thread._gpu_adapters = [INTEL_UHD, RTX]
    thread._nvidia_smi_path = "nvidia-smi.exe"
    thread._nvidia_cache_temp = 71.0
    thread._nvidia_cache_power = 150.0
    thread._nvidia_cache_vram_total = 12288.0
    thread._nvidia_last_poll = float("inf")
    thread._lhm_http.get_snapshot.return_value = LhmSnapshot(gpus=(
        LhmGpu("/gpu-nvidia/0", "NVIDIA GeForce RTX 4070", temperature=70.0, power=140.0),))
    thread.config = {"gpu_adapter": INTEL_UHD.name}
    r = _poll(thread, {}, {IGPU_I: 0.0}, {IGPU_I: 600 * MIB}, include_temp=True, include_power=True)
    assert r.temp is None and r.power is None          # N/A, never the RTX's reading
    thread.config = {"gpu_adapter": RTX.name}
    r = _poll(thread, {}, {IGPU_I: 0.0, DGPU_I: 10 * MIB}, {IGPU_I: 600 * MIB},
              include_temp=True, include_power=True)
    assert r.temp == 70.0 and r.power == 140.0


def test_lhm_gpu_for_prefers_the_matching_name():
    snap = LhmSnapshot(gpus=(LhmGpu("/gpu-amd/0", "AMD Radeon(TM) Graphics", temperature=50.0),
                             LhmGpu("/gpu-amd/1", "AMD Radeon RX 6600", temperature=65.0)))
    assert snap.gpu_for(0x1002, "AMD Radeon RX 6600").temperature == 65.0
    assert snap.gpu_for(0x10DE, "anything") is None


def test_missing_pick_falls_back_to_automatic(thread):
    thread._gpu_adapters = [INTEL_UHD, BASIC_RENDER]
    thread.config = {"gpu_adapter": "Unplugged eGPU"}
    r = _poll(thread, {_eng(0xD9C1, 1): 33.0}, {IGPU_I: 0.0}, {IGPU_I: 600 * MIB})
    assert r.util == pytest.approx(33.0) and r.vram_is_shared is True


# --- Settings > Hardware: picker, unit, throttle, G6 tooltips ----------------------------------

@pytest.fixture
def page(qt_owned):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    return qt_owned(HardwarePage(EN, lambda: None))


def test_picker_hidden_with_one_gpu_and_listed_with_two(page):
    with patch.object(gpu_adapters, "enumerate_adapters", return_value=[INTEL_UHD, BASIC_RENDER]):
        page.load_settings({})
    assert page._gpu_adapter_card.isHidden()
    assert page.get_settings()["gpu_adapter"] == ""
    with patch.object(gpu_adapters, "enumerate_adapters", return_value=[INTEL_UHD, RADEON, BASIC_RENDER]):
        page.load_settings({"gpu_adapter": RADEON.name})
    assert not page._gpu_adapter_card.isHidden()
    assert [page.gpu_adapter.itemText(i) for i in range(page.gpu_adapter.count())] == \
        [EN.GPU_ADAPTER_AUTO, INTEL_UHD.name, RADEON.name]
    assert page.get_settings()["gpu_adapter"] == RADEON.name


def test_a_saved_pick_for_an_absent_gpu_survives_a_save(page):
    with patch.object(gpu_adapters, "enumerate_adapters", return_value=[INTEL_UHD]):
        page.load_settings({"gpu_adapter": "eGPU X"})
    assert not page._gpu_adapter_card.isHidden()
    assert page.get_settings()["gpu_adapter"] == "eGPU X"


def test_throttle_slider_shows_fahrenheit_and_stores_celsius(page):
    page.load_settings({"temperature_unit": "fahrenheit", "throttle_temp_c": 90})
    assert page.throttle_temp.value() == 194
    s = page.get_settings()
    assert s["temperature_unit"] == "fahrenheit" and s["throttle_temp_c"] == 90
    page.temperature_unit.setCurrentIndex(page.temperature_unit.findData("celsius"))
    assert page.throttle_temp.value() == 90
    assert page.get_settings()["throttle_temp_c"] == 90
    page.load_settings({"temperature_unit": "fahrenheit", "throttle_temp_c": 0})
    assert page.get_settings()["throttle_temp_c"] == 0


def test_greyed_cards_say_why(page):
    page.load_settings({"monitor_cpu_enabled": False, "monitor_gpu_enabled": False,
                        "monitor_ram_enabled": True, "monitor_vram_enabled": True})
    assert page._ram_card.toolTip() == EN.HARDWARE_NEEDS_CPU_TOOLTIP
    assert page._vram_card.toolTip() == EN.HARDWARE_NEEDS_GPU_TOOLTIP
    assert page._temps_card.toolTip().startswith(EN.RECORD_HARDWARE_HISTORY_INACTIVE_SUB)
    assert EN.SHOW_HARDWARE_TEMPS_TOOLTIP in page._temps_card.toolTip()
    assert EN.SHOW_HARDWARE_POWER_TOOLTIP in page._power_card.toolTip()
    page.monitor_cpu.setChecked(True)
    assert page._ram_card.toolTip() == ""
    assert page._temps_card.toolTip() == EN.SHOW_HARDWARE_TEMPS_TOOLTIP


@pytest.mark.parametrize("cpu, gpu, ram, vram, reason", [
    (False, False, False, False, "SHOW_MEMORY_LABELS_INACTIVE_TOOLTIP"),   # no memory readout on
    (True, True, False, False, "SHOW_MEMORY_LABELS_INACTIVE_TOOLTIP"),
    (False, True, True, False, "HARDWARE_NEEDS_CPU_TOOLTIP"),              # RAM on, CPU off (L5)
    (True, False, False, True, "HARDWARE_NEEDS_GPU_TOOLTIP"),              # VRAM on, GPU off
    (False, False, True, True, "RECORD_HARDWARE_HISTORY_INACTIVE_SUB"),    # both hosts off
])
def test_memory_labels_tooltip_names_the_actual_reason(page, cpu, gpu, ram, vram, reason):
    page.load_settings({"monitor_cpu_enabled": cpu, "monitor_gpu_enabled": gpu,
                        "monitor_ram_enabled": ram, "monitor_vram_enabled": vram})
    assert not page._mem_labels_card.isEnabled()
    assert page._mem_labels_card.toolTip() == getattr(EN, reason)


def test_fahrenheit_throttle_floor_is_the_celsius_off_stop(page):
    """L6: the °F scale starts at 32 °F (= 0 °C, "Off"), so no low °F stop saves as 1 °C."""
    page.load_settings({"temperature_unit": "fahrenheit", "throttle_temp_c": 0})
    assert page.throttle_temp.slider.minimum() == 32 and page.throttle_temp.value() == 32
    assert page.get_settings()["throttle_temp_c"] == 0
    page.throttle_temp.setValue(33)
    assert page.get_settings()["throttle_temp_c"] == 1
    page.throttle_temp.setValue(266)
    assert page.get_settings()["throttle_temp_c"] == 130


# --- review M1 / L1 / L2 / L6 -------------------------------------------------------------------

ARC = AdapterInfo("Intel(R) Arc(TM) A770", 0x8086, (0, 0xAB01), 16 * GIB, int(8 * GIB), False)


def test_picked_arc_never_reports_the_igpus_rapl_power(thread):
    """M1: RAPL PP1 is the Intel iGPU's plane - a picked Arc must not show it (reproduced 4.2 W)."""
    thread._gpu_adapters = [INTEL_UHD, ARC]
    thread._power_query = 7
    thread._power_pp1_counter = 8
    thread._init_power_query = MagicMock()
    with patch("win32pdh.GetFormattedCounterValue", return_value=(0, 4200.0)):
        thread.config = {"gpu_adapter": ARC.name}
        arc = _poll(thread, {}, {IGPU_I: 0.0, DGPU_I: 10 * MIB}, {IGPU_I: 600 * MIB}, include_power=True)
        thread.config = {"gpu_adapter": INTEL_UHD.name}
        uhd = _poll(thread, {}, {IGPU_I: 0.0, DGPU_I: 10 * MIB}, {IGPU_I: 600 * MIB}, include_power=True)
    assert arc.power is None
    assert uhd.power == pytest.approx(4.2)


def test_lhm_same_maker_pair_is_told_apart_by_integrated_or_na():
    """L1: an Arc pick whose name LHM spells differently gets the discrete group, not the iGPU's."""
    snap = LhmSnapshot(gpus=(LhmGpu("/gpu-intel-integrated/0", "Intel(R) UHD Graphics 770", temperature=45.0),
                             LhmGpu("/gpu-intel/0", "Intel Arc A770 Graphics", temperature=66.0)))
    assert snap.gpu_for(0x8086, ARC.name, integrated=False).temperature == 66.0
    assert snap.gpu_for(0x8086, INTEL_UHD.name, integrated=True).temperature == 45.0
    twins = LhmSnapshot(gpus=(LhmGpu("/gpu-nvidia/0", "RTX A", temperature=50.0),
                              LhmGpu("/gpu-nvidia/1", "RTX B", temperature=70.0)))
    assert twins.gpu_for(0x10DE, "NVIDIA GeForce RTX 4070", integrated=False) is None   # can't tell: N/A


def test_wmi_same_maker_pair_reads_only_its_own_side(thread):
    thread._gpu_adapters = [INTEL_UHD, ARC]
    assert thread._gpu_sensor_matches("/gpu-intel/0/temperature/0", ARC)
    assert not thread._gpu_sensor_matches("/gpu-intel-integrated/0/temperature/0", ARC)
    assert thread._gpu_sensor_matches("/gpu-intel-integrated/0/temperature/0", INTEL_UHD)
    thread._gpu_adapters = [AdapterInfo("AMD Radeon(TM) Graphics", 0x1002, (0, 0xD9C1), 512 * 2 ** 20, GIB, False),
                            RADEON]
    assert not thread._gpu_sensor_matches("/gpu-amd/0/temperature/0", RADEON)   # can't tell: N/A


def test_picked_nvidia_card_reads_its_own_nvidia_smi_line(thread):
    """L1/L6: the picked card's line by name - not the first line - and nvidia-smi's total."""
    rtx2 = AdapterInfo("NVIDIA GeForce RTX 3060", 0x10DE, (0, 0xEE01), 12 * GIB, int(8 * GIB), False)
    thread._gpu_adapters = [RTX, rtx2]
    thread._nvidia_smi_path = "nvidia-smi.exe"
    thread._run_child = MagicMock(return_value="NVIDIA GeForce RTX 3060, 55, 12288, 90.0\n"
                                                "NVIDIA GeForce RTX 4070, 64, 12282, 110.5\n")
    thread.config = {"gpu_adapter": RTX.name}
    r = _poll(thread, {}, {DGPU_I: 3000 * MIB}, {DGPU_I: 5 * MIB}, include_temp=True, include_power=True)
    assert (r.temp, r.power, r.vram_total) == (64.0, 110.5, 12282.0)
    calls = thread._run_child.call_count
    _poll(thread, {}, {DGPU_I: 3000 * MIB}, {DGPU_I: 5 * MIB}, include_temp=True, include_power=True)
    assert thread._run_child.call_count == calls      # cached on the slow sub-cadence


def test_ambiguous_nvidia_smi_line_reads_na_but_keeps_the_dxgi_total(thread):
    twin = AdapterInfo("NVIDIA GeForce RTX 4070", 0x10DE, (0, 0xEE01), 12 * GIB, int(8 * GIB), False)
    thread._gpu_adapters = [RTX, twin]
    thread._nvidia_smi_path = "nvidia-smi.exe"
    thread._run_child = MagicMock(return_value="NVIDIA GeForce RTX 4070, 50, 12282, 90.0\n"
                                                "NVIDIA GeForce RTX 4070, 70, 12282, 200.0\n")
    thread.config = {"gpu_adapter": RTX.name}
    r = _poll(thread, {}, {DGPU_I: 3000 * MIB}, {DGPU_I: 5 * MIB}, include_temp=True, include_power=True)
    assert r.temp is None and r.power is None
    assert r.vram_total == pytest.approx(12 * 1024)


def test_g4_retries_an_unresolved_set_a_bounded_number_of_times(thread):
    """L2: a fresh read that still misses the LUID is retried later (PDH can lead DXGI on a hot-plug),
    at most ADAPTER_RECHECK_MAX times, never on every poll."""
    g = constants.gpu
    thread._gpu_adapters = [INTEL_UHD, BASIC_RENDER]
    clock = [1000.0]
    reads = [[INTEL_UHD, BASIC_RENDER], [INTEL_UHD, BASIC_RENDER, RADEON]]
    with patch("netspeedtray.core.monitor_thread.time.monotonic", side_effect=lambda: clock[0]), \
         patch.object(gpu_adapters, "enumerate_adapters", side_effect=lambda: reads.pop(0) if reads
                      else [INTEL_UHD, BASIC_RENDER, RADEON]) as enum:
        pdh = {IGPU_I: 0.0, DGPU_I: 2048 * MIB}
        first = _poll(thread, {}, pdh, {IGPU_I: 600 * MIB})
        assert enum.call_count == 1 and first.vram_is_shared is True     # DXGI not caught up yet
        _poll(thread, {}, pdh, {IGPU_I: 600 * MIB})
        assert enum.call_count == 1                                     # not every poll
        clock[0] += g.ADAPTER_RECHECK_SEC
        second = _poll(thread, {}, pdh, {IGPU_I: 600 * MIB})
        assert enum.call_count == 2 and second.vram_is_shared is False  # resolved on the retry
        clock[0] += 10 * g.ADAPTER_RECHECK_SEC
        _poll(thread, {}, pdh, {IGPU_I: 600 * MIB})
        assert enum.call_count == 2                                     # resolved: checked for good


def test_g4_gives_up_on_a_luid_dxgi_never_lists(thread):
    g = constants.gpu
    thread._gpu_adapters = [INTEL_UHD, BASIC_RENDER]
    clock = [1000.0]
    ghost = "luid_0x00000000_0x0000EEEE_phys_0"
    with patch("netspeedtray.core.monitor_thread.time.monotonic", side_effect=lambda: clock[0]), \
         patch.object(gpu_adapters, "enumerate_adapters", return_value=[INTEL_UHD, BASIC_RENDER]) as enum:
        for _ in range(g.ADAPTER_RECHECK_MAX * 3):
            _poll(thread, {}, {IGPU_I: 0.0, ghost: 0.0}, {IGPU_I: 600 * MIB})
            clock[0] += g.ADAPTER_RECHECK_SEC
    assert enum.call_count == g.ADAPTER_RECHECK_MAX


# --- F8: the display unit -----------------------------------------------------------------------

def test_format_temperature():
    assert helpers.format_temperature(40.0, "fahrenheit") == "104°F"
    assert helpers.format_temperature(27.9, "celsius") == "28°C"
    assert helpers.format_temperature(27.9, "kelvin?") == "28°C"
    assert helpers.unit_to_celsius(helpers.celsius_to_unit(87.0, "fahrenheit"), "fahrenheit") == pytest.approx(87.0)


def test_widget_suffix_uses_the_unit(q_app):
    from netspeedtray.utils.widget_renderer import RenderConfig, WidgetRenderer
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, temperature_unit="fahrenheit")
    r = WidgetRenderer(cfg, EN)
    assert RenderConfig.from_dict(cfg).temperature_unit == "fahrenheit"
    assert r._build_hw_suffix(40.0, 7.8, True, True, "fahrenheit") == "(104°F, 7.8W)"
    assert r._build_hw_suffix(40.0, None, True, False) == "(40°C)"


@pytest.mark.parametrize("family", ["Segoe UI", "Arial", "Consolas", "Times New Roman"])
def test_fahrenheit_reference_fits_every_reading(q_app, family):
    """The widget is sized once from the reference: every plausible reading (0-150 °C) must fit."""
    fm = QFontMetrics(QFont(family, 9))
    ref = fm.horizontalAdvance(helpers.hw_suffix_reference(True, True, "fahrenheit"))
    widest = max(fm.horizontalAdvance(f"({helpers.format_temperature(c, 'fahrenheit')}, 250.0W)")
                 for c in range(0, 150))
    assert ref >= widest


def test_hover_card_formats_in_the_unit(q_app):
    from PyQt6.QtWidgets import QLabel
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware={"cpu": {"usage": 37.0, "temp": 40.0},
                                                "temp_unit": "fahrenheit"})
    try:
        assert "104°F" in [lbl.text() for lbl in card.findChildren(QLabel)]
    finally:
        card.deleteLater()


def test_hover_snapshot_carries_the_unit_only_when_not_celsius(q_app):
    from netspeedtray.tests.unit.test_hover_card_hardware import HW, _hover_fake
    f = _hover_fake(hardware_in_hover_card=True, widget_display_mode="side_by_side",
                    temperature_unit="fahrenheit", **HW)
    assert f._hover_hardware_snapshot()["temp_unit"] == "fahrenheit"
    c = _hover_fake(hardware_in_hover_card=True, widget_display_mode="side_by_side", **HW)
    assert "temp_unit" not in c._hover_hardware_snapshot()


def test_monitor_formatters_use_the_unit():
    from netspeedtray.views.monitor.stats_detail import StatsDetailSheet
    fake = types.SimpleNamespace(_config={"temperature_unit": "fahrenheit"}, _fmt_speed=str)
    fmt = StatsDetailSheet._formatter(fake, "hw", "°C")
    assert fmt(100.0) == "212°F" and fmt(None) == "-"
    from netspeedtray.views.monitor.hardware.telemetry import TelemetryStrip
    strip = types.SimpleNamespace(_config={"temperature_unit": "fahrenheit"})
    assert TelemetryStrip._proc_text(strip, 18.0, 40.0, None) == "18%  ·  104°F"


def test_config_validates_the_unit():
    schema = constants.config.defaults.VALIDATION_SCHEMA
    assert schema["temperature_unit"]["choices"] == ["celsius", "fahrenheit"]
    assert constants.config.defaults.DEFAULT_CONFIG["temperature_unit"] == "celsius"
    assert constants.config.defaults.DEFAULT_CONFIG["gpu_adapter"] == ""
