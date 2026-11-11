"""
#236 follow-up (2.2.1): the hover card lists every GPU on a multi-GPU PC.

The taskbar keeps one GPU (the picker / Automatic, unchanged - test_gpu_automatic_golden.py is the
contract). The hover card lists each GPU with its own usage, temperature, power and memory, built from
what the poll already read: no new GpuPollResult field, no extra PDH array read, never nvidia-smi.
Design of record: Docs/releases/v2.2.1/design-236-multi-gpu-display.md.
"""
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import gpu_adapters
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.lhm_http import LhmGpu, LhmSnapshot, parse_snapshot
from netspeedtray.core.monitor_thread import (GpuCard, StatsMonitorThread, aggregate_gpu_engine_utilization,
                                              per_adapter_engine_utilization)
from netspeedtray.tests.unit.test_multi_gpu_sensors_236 import (
    APU, APU_LUID, BRD, GIB, MIB, NVIDIA_BUSIER, RTX, RTX_LUID, _eng, balciseri_data_json)

EN = I18nStrings("en_US")
# The 9800X3D's iGPU as an integrated adapter (less dedicated memory than DEDICATED_VIDEO_MEMORY_MIN_MIB).
APU_IG = APU._replace(dedicated_video_bytes=256 * 2 ** 20)


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
    t._gpu_adapters = [APU_IG, RTX, BRD]
    t.config = dict(t.config or {}, gpu_adapter="")
    return t


def _poll(t, util=NVIDIA_BUSIER, shared=None, dedicated=None):
    """One poll with EXACTLY the three PDH array reads the golden test mocks (a fourth would raise)."""
    dedicated = dedicated if dedicated is not None else {
        f"luid_0x00000000_0x{APU_LUID:08X}_phys_0": 300 * MIB,
        f"luid_0x00000000_0x{RTX_LUID:08X}_phys_0": 1200 * MIB}
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=[util, dedicated, shared or {}]), \
         patch("netspeedtray.core.monitor_thread.psutil.virtual_memory",
               return_value=types.SimpleNamespace(total=32 * GIB)), \
         patch.object(StatsMonitorThread, "_init_ohm_wmi"):
        return t._poll_gpu_hybrid(include_temp=True, include_power=True)


# --- 1. the per-card build ------------------------------------------------------------------------

def test_balciseri_machine_lists_each_card_with_its_own_readings(thread):
    """The #236 machine: RTX 3 % / 26 °C / 17.5 W / 1.17 of 12 GiB; APU 0 % / 38 °C (its own VR SoC,
    its only sensor) / 6 W - each from its OWN sensor group, discrete first."""
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    shared = {f"luid_0x00000000_0x{APU_LUID:08X}_phys_0": 900 * MIB}
    r = _poll(thread, shared=shared)
    assert (r.util, r.temp, r.power) == (3.0, 26.0, 17.5)    # the taskbar's Automatic: unchanged
    rtx, apu = thread._gpu_cards(include_temp=True, include_power=True)
    assert (rtx.name, rtx.integrated, rtx.util, rtx.temp, rtx.power) == (
        "NVIDIA GeForce RTX 5070", False, 3.0, 26.0, 17.5)
    assert rtx.vram_used_gib == pytest.approx(1200 / 1024) and rtx.vram_total_gib == pytest.approx(12.0)
    assert not rtx.vram_shared
    assert (apu.name, apu.integrated, apu.util, apu.temp, apu.power) == (
        "AMD Radeon Graphics", True, 0.0, 38.0, 6.0)
    # An iGPU's memory is its shared usage against its shared budget, like the picked-iGPU readout.
    assert apu.vram_shared and apu.vram_used_gib == pytest.approx(900 / 1024)
    assert apu.vram_total_gib == pytest.approx(15.0)
    assert not rtx.picked and not apu.picked                   # Automatic: no card is "the taskbar's"


def test_an_igpu_without_a_shared_reading_shows_no_memory(thread):
    """No Shared Usage for the iGPU: "—", never its BIOS carve-out as dedicated "VRAM 0.3 / 0.3 GB"."""
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    _poll(thread, shared={})
    _rtx, apu = thread._gpu_cards(True, True)
    assert (apu.vram_used_gib, apu.vram_total_gib, apu.vram_shared) == (None, None, True)


def test_switches_off_leave_temperature_and_power_out(thread):
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    _poll(thread)
    cards = thread._gpu_cards(include_temp=False, include_power=False)
    assert [(c.temp, c.power) for c in cards] == [(None, None), (None, None)]


def test_picked_card_is_marked_and_reads_exactly_the_taskbar(thread):
    thread.config = dict(thread.config, gpu_adapter=APU.name)
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    r = _poll(thread)
    rtx, apu = thread._gpu_cards(include_temp=True, include_power=True)
    assert apu.picked and not rtx.picked
    assert (apu.util, apu.temp, apu.power) == (r.util, r.temp, r.power) == (0.0, 38.0, 6.0)
    assert (rtx.temp, rtx.power) == (26.0, 17.5)


def test_one_gpu_lists_nothing(thread):
    thread._gpu_adapters = [RTX, BRD]
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    _poll(thread)
    assert thread._gpu_cards(True, True) == ()


def test_the_poll_reads_no_more_pdh_arrays_and_returns_the_same_tuple(thread):
    """Golden-test constraint: the list adds no GpuPollResult field and no PDH read (``_poll`` hands
    out exactly three arrays and would fail on a fourth)."""
    thread._lhm_http.get_snapshot.return_value = parse_snapshot(balciseri_data_json())
    r = _poll(thread)
    assert type(r)._fields == ("util", "vram_used", "vram_total", "temp", "power", "vram_is_shared", "present")
    with patch("win32pdh.GetFormattedCounterArray", side_effect=AssertionError("PDH read")):
        assert len(thread._gpu_cards(True, True)) == 2


# --- 2. per-LUID utilization ----------------------------------------------------------------------

@pytest.mark.parametrize("samples", [
    {_eng(APU_LUID, 20): 9.0, _eng(RTX_LUID, 10): 0.0},
    {_eng(APU_LUID, 20): 45.0, _eng(RTX_LUID, 10): 30.0, _eng(RTX_LUID, 11): 30.0},   # one engine, summed
    {_eng(APU_LUID, 20, eng=0): 45.0, _eng(APU_LUID, 21, eng=1): 50.0, _eng(RTX_LUID, 10): 12.5},
    {_eng(RTX_LUID, 10): 150.0, _eng(APU_LUID, 1): -3.0, _eng(APU_LUID, 2): float("nan"),
     _eng(APU_LUID, 3): True, _eng(APU_LUID, 4): "x"},
    {},
])
def test_each_card_reads_what_picking_it_would_and_the_busiest_is_automatic(samples):
    per = per_adapter_engine_utilization(samples)
    assert max(per.values(), default=0.0) == aggregate_gpu_engine_utilization(samples)
    for luid in ((0, APU_LUID), (0, RTX_LUID)):
        assert per.get(luid, 0.0) == aggregate_gpu_engine_utilization(samples, luid)


def test_unparseable_instances_belong_to_no_card():
    assert per_adapter_engine_utilization({"unparseable_instance": 70.0, _eng(RTX_LUID, 1): 5.0}) == {
        (0, RTX_LUID): 5.0}


# --- 3. names and identical cards -----------------------------------------------------------------

def test_display_names_drop_trademarks_and_number_duplicates():
    assert gpu_adapters.display_name("AMD Radeon(TM) Graphics") == "AMD Radeon Graphics"
    assert gpu_adapters.display_name("Intel(R)  UHD Graphics 770") == "Intel UHD Graphics 770"
    assert gpu_adapters.display_name("Intel® Arc™ A770") == "Intel Arc A770"
    assert gpu_adapters.numbered_display_names(
        ["NVIDIA GeForce RTX 4090", "Intel(R) UHD Graphics 770", "NVIDIA GeForce RTX 4090"]) == [
        "NVIDIA GeForce RTX 4090 #1", "Intel UHD Graphics 770", "NVIDIA GeForce RTX 4090 #2"]


def test_two_identical_cards_are_numbered_and_never_borrow_each_others_sensors(thread):
    a = AdapterInfo("NVIDIA GeForce RTX 4090", 0x10DE, (0, RTX_LUID), 24 * GIB, int(15 * GIB), False)
    b = a._replace(luid=(0, APU_LUID))
    thread._gpu_adapters = [a, b, BRD]
    thread._lhm_http.get_snapshot.return_value = LhmSnapshot(gpus=(
        LhmGpu("/gpu-nvidia/0", "NVIDIA GeForce RTX 4090", 40.0, "GPU Core", 100.0, "GPU Package"),
        LhmGpu("/gpu-nvidia/1", "NVIDIA GeForce RTX 4090", 45.0, "GPU Core", 120.0, "GPU Package")))
    _poll(thread)
    one, two = thread._gpu_cards(True, True)
    assert (one.name, two.name) == ("NVIDIA GeForce RTX 4090 #1", "NVIDIA GeForce RTX 4090 #2")
    assert (one.temp, one.power, two.temp, two.power) == (None, None, None, None)
    assert (one.util, two.util) == (3.0, 0.0)     # utilization is per LUID: never ambiguous


# --- 4. never nvidia-smi --------------------------------------------------------------------------

def test_the_list_never_runs_nvidia_smi_and_reuses_only_a_fresh_cache_of_the_one_nvidia_card(thread):
    thread._nvidia_smi_path = "nvidia-smi.exe"
    thread._nvidia_cache_temp, thread._nvidia_cache_power, thread._nvidia_cache_vram_total = 61.0, 150.5, 12282.0
    thread._nvidia_last_poll = time.monotonic()      # the Automatic poll's own sub-cadence just ran
    thread._run_child = MagicMock(return_value="61, 12282, 150.5\n")
    _poll(thread)
    thread._run_child.reset_mock()
    thread._run_child.side_effect = AssertionError("the GPU list started nvidia-smi")
    rtx, apu = thread._gpu_cards(True, True)
    for _ in range(5):
        thread._gpu_cards(True, True)
    thread._run_child.assert_not_called()
    assert (rtx.temp, rtx.power) == (61.0, 150.5)
    assert rtx.vram_total_gib == pytest.approx(12282 / 1024)
    assert (apu.temp, apu.power) == (None, None)      # nvidia-smi's line is never another card's
    # A stale cache (nvidia-smi has not run for a while) is not shown as a live reading.
    thread._nvidia_last_poll = time.monotonic() - 10 * thread._NVIDIA_POLL_INTERVAL_SEC
    rtx, _apu = thread._gpu_cards(True, True)
    assert (rtx.temp, rtx.power) == (None, None)
    thread._run_child.assert_not_called()


def test_two_nvidia_cards_never_use_the_first_line_cache(thread):
    other = AdapterInfo("NVIDIA GeForce RTX 3060", 0x10DE, (0, APU_LUID), 12 * GIB, int(15 * GIB), False)
    thread._gpu_adapters = [RTX, other, BRD]
    thread._nvidia_cache_temp, thread._nvidia_cache_power = 61.0, 150.5
    _poll(thread)
    thread._nvidia_last_poll = time.monotonic()
    assert [(c.temp, c.power) for c in thread._gpu_cards(True, True)] == [(None, None), (None, None)]


# --- 5. the thread emits it only when it should -----------------------------------------------------

def _one_tick(thread, config, cards):
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
         patch.object(thread, "_gpu_cards", MagicMock(return_value=cards)) as build:
        thread.run()
    return got, build


TWO = (GpuCard((0, RTX_LUID), "RTX", False, 3.0), GpuCard((0, APU_LUID), "APU", True, 0.0))


@pytest.mark.parametrize("config, cards, listed", [
    ({"monitor_gpu_enabled": True}, TWO, True),                            # default: on
    ({"monitor_gpu_enabled": True, "hover_list_all_gpus": False}, TWO, False),
    ({"monitor_gpu_enabled": True}, TWO[:1], False),                       # one GPU: no list
    ({"monitor_vram_enabled": True}, TWO, False),                          # VRAM-only poll: no GPU line
    ({}, TWO, False),                                                      # nothing collected
])
def test_gpu_cards_ride_the_stats_only_with_gpu_monitoring_and_two_cards(thread, config, cards, listed):
    """Every tick says what the list is: the cards, or () to clear it."""
    got, build = _one_tick(thread, config, cards)
    assert got["gpu_cards"] == (TWO if listed else ())
    if not config.get("monitor_gpu_enabled") or config.get("hover_list_all_gpus") is False:
        build.assert_not_called()


def test_a_failed_gpu_poll_sends_no_list_so_the_card_keeps_the_last_one(thread):
    """A transient PDH/driver error must not blink the list out of an open card (review #4)."""
    thread.config = {"monitor_gpu_enabled": True}
    thread._is_running = True
    got = {}

    def emit(stats):
        got.update(stats)
        thread._is_running = False

    thread.stats_ready = types.SimpleNamespace(emit=emit)
    with patch("netspeedtray.core.monitor_thread.is_rdp_session", return_value=False), \
         patch.object(thread, "_init_com"), patch.object(thread, "_cleanup_com"), \
         patch.object(thread, "_poll_gpu_hybrid", side_effect=RuntimeError("PDH_CSTATUS_INVALID_DATA")), \
         patch.object(thread, "_collect_power_stats"), \
         patch.object(thread, "_gpu_cards", MagicMock(return_value=TWO)):
        thread.run()
    assert "gpu_cards" not in got


# --- 6. config + controller -----------------------------------------------------------------------

def test_config_default_and_schema(tmp_path):
    from netspeedtray.utils.config import ConfigManager
    d = constants.config.defaults
    assert d.DEFAULT_HOVER_LIST_ALL_GPUS is True
    assert d.DEFAULT_CONFIG["hover_list_all_gpus"] is True
    assert d.VALIDATION_SCHEMA["hover_list_all_gpus"] == {"type": bool, "default": True}
    cm = ConfigManager(tmp_path / "c.json")
    with patch.object(cm.logger, "warning"):
        assert cm._validate_config({"hover_list_all_gpus": "no"})["hover_list_all_gpus"] is True
    assert cm._validate_config({"hover_list_all_gpus": False})["hover_list_all_gpus"] is False


def test_controller_hands_the_cards_to_the_view_and_never_records_them(q_app, tmp_path):
    from PyQt6.QtCore import QThread
    from netspeedtray.core.controller import StatsController
    from netspeedtray.core.widget_state import WidgetState
    cfg = dict(constants.config.defaults.DEFAULT_CONFIG, monitor_gpu_enabled=True)
    with patch.object(QThread, "start", lambda self: None), \
            patch("netspeedtray.core.widget_state.get_app_data_path", return_value=tmp_path):
        ws = WidgetState(cfg.copy())
    try:
        view = types.SimpleNamespace(gpu_cards=())
        ctl = StatsController(cfg, ws)
        ctl.view = view
        base = {"gpu": 20.0, "gpu_present": True, "vram_used": 1.0, "vram_total": 12.0}
        ctl.handle_stats(dict(base))
        without = [(t, v) for _ts, t, v in ws._hw_batch]
        ws._hw_batch.clear()
        ctl.handle_stats(dict(base, gpu_cards=TWO))
        assert view.gpu_cards == TWO
        assert [(t, v) for _ts, t, v in ws._hw_batch] == without     # nothing more persisted
        ctl.handle_stats(dict(base))                                   # a failed GPU poll: kept
        assert view.gpu_cards == TWO
        ctl.handle_stats({"network": {}})                              # a tick without GPU keys: kept
        assert view.gpu_cards == TWO
        ctl.handle_stats(dict(base, gpu_cards=()))                     # one GPU / list off: cleared
        assert view.gpu_cards == ()
    finally:
        ws.cleanup()


# --- 7. the widget's hover gates and snapshot -------------------------------------------------------

def _hover_fake(cards=TWO, layout="horizontal", **config):
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    cfg = {"show_usage_on_hover": False, "show_hover_tips": False, **config}
    fake = types.SimpleNamespace(
        config=cfg, _cached_layout_mode=layout, layout_manager=types.SimpleNamespace(_side_fit=None),
        cpu_usage=37.0, gpu_usage=52.0, cpu_temp=58.0, gpu_temp=64.0, cpu_power=35.0, gpu_power=120.0,
        ram_used=9.2, ram_total=16.0, vram_used=3.1, vram_total=8.0, vram_is_shared=False,
        network_identity=None, gpu_cards=cards, logger=MagicMock(), _hover_card=None,
        _hover_card_pinned=False, _HOVER_HW_REFRESH_SEC=W._HOVER_HW_REFRESH_SEC)
    for name in ("_side_bar_fit", "_hover_hardware_wanted", "_hover_hardware_snapshot",
                 "_identity_presentation", "_identity_ssid_text", "_refresh_hover_hardware"):
        setattr(fake, name, types.MethodType(getattr(W, name), fake))
    return fake


CARDS = (GpuCard((0, RTX_LUID), "NVIDIA GeForce RTX 5070", False, 3.0, 1.17, 12.0, False, 26.0, 17.5),
         GpuCard((0, APU_LUID), "AMD Radeon Graphics", True, 0.0, 0.88, 15.0, True, 38.0, 6.0))
GPU_ON = dict(monitor_gpu_enabled=True, monitor_vram_enabled=True, show_hardware_temps=True,
              show_hardware_power=True)
# ...with the hover card on (the usage rows) and a taskbar that paints the GPU.
ALL = dict(GPU_ON, show_usage_on_hover=True, widget_display_mode="side_by_side")


@pytest.mark.parametrize("switch", ["show_usage_on_hover", "show_hover_tips", "power_display_enabled"])
def test_bottom_bar_lists_the_gpus_in_a_card_the_user_already_gets(q_app, switch):
    on = _hover_fake(CARDS, **{**GPU_ON, switch: True})
    assert on._hover_hardware_wanted()
    assert on._hover_hardware_snapshot() == {"gpus": [
        {"key": (0, RTX_LUID), "name": "NVIDIA GeForce RTX 5070", "usage": 3.0, "on_taskbar": False,
         "temp": 26.0, "power": 17.5, "vram": (1.17, 12.0)},
        {"key": (0, APU_LUID), "name": "AMD Radeon Graphics", "usage": 0.0, "on_taskbar": False,
         "temp": 38.0, "power": 6.0, "vram": (0.88, 15.0), "vram_shared": True}]}


def test_the_list_never_opens_a_card_the_user_turned_off(q_app):
    """Review #1 (decided): usage, tips and battery all off on a bottom bar = no card, list or not."""
    from PyQt6.QtCore import QPointF, QTimer
    from PyQt6.QtGui import QEnterEvent
    from PyQt6.QtWidgets import QWidget
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    off = _hover_fake(CARDS, **GPU_ON)
    assert not off._hover_hardware_wanted()
    assert off._hover_hardware_snapshot() is None

    class _Armable(NetworkSpeedWidget):
        def __init__(self):
            QWidget.__init__(self)
            self.config = {"show_usage_on_hover": False, "show_hover_tips": False, **GPU_ON}
            self.gpu_cards = CARDS
            self._cached_layout_mode = "horizontal"
            self.layout_manager = types.SimpleNamespace(_side_fit=None)
            self._hover_card = None
            self._hover_card_timer = QTimer()
            self._hover_card_timer.setSingleShot(True)
            self.logger = MagicMock()

    w = _Armable()
    try:
        w.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
        assert not w._hover_card_timer.isActive()
    finally:
        w._hover_card_timer.stop()
        w.deleteLater()


def test_a_side_bar_hiding_the_hardware_still_lists_them_with_the_card_off(q_app):
    fake = _hover_fake(CARDS, layout="vertical", **GPU_ON)
    fake.layout_manager._side_fit = types.SimpleNamespace(hides_hardware=True, hides_identity=False)
    snap = fake._hover_hardware_snapshot()
    assert fake._hover_hardware_wanted() and [g["name"] for g in snap["gpus"]] == [c.name for c in CARDS]
    assert not any(g["on_taskbar"] for g in snap["gpus"])


@pytest.mark.parametrize("cards, config", [
    (CARDS, dict(ALL, hover_list_all_gpus=False)),        # switched off
    (CARDS[:1], ALL),                                      # one GPU
    ((), ALL),
    (CARDS, dict(ALL, monitor_gpu_enabled=False)),         # GPU monitoring off
])
def test_gate_stays_shut_without_the_switch_two_cards_and_gpu_monitoring(q_app, cards, config):
    fake = _hover_fake(cards, **config)
    assert not fake._hover_hardware_wanted()
    assert fake._hover_hardware_snapshot() is None


def test_list_replaces_the_single_gpu_and_vram_rows_in_a_hardware_card(q_app):
    fake = _hover_fake(CARDS, hardware_in_hover_card=True, monitor_cpu_enabled=True,
                       monitor_ram_enabled=True, widget_display_mode="side_by_side", **GPU_ON)
    snap = fake._hover_hardware_snapshot()
    assert set(snap) == {"cpu", "ram", "gpus"}
    fake.config["hover_list_all_gpus"] = False
    assert set(fake._hover_hardware_snapshot()) == {"cpu", "ram", "gpu", "vram"}


@pytest.mark.parametrize("mode, marked", [
    (dict(widget_display_mode="side_by_side"), True),
    (dict(widget_display_mode="gpu_only"), True),
    (dict(widget_display_mode="cycle"), True),             # Auto-Cycle paints the GPU in one phase
    (dict(widget_display_mode="network_only"), False),     # nothing on the bar to point at
    (dict(widget_display_mode="cpu_only", monitor_cpu_enabled=True), False),
    (dict(widget_display_mode="side_by_side", widget_display_order=["network", "cpu", "none"],
          monitor_cpu_enabled=True), False),               # a side_by_side order without a GPU slot
    (dict(widget_display_mode="side_by_side", hardware_in_hover_card=True), False),   # moved to the card
])
def test_marker_only_for_a_picked_card_the_taskbar_actually_paints(q_app, mode, marked):
    picked = (CARDS[0], CARDS[1]._replace(picked=True))
    snap = _hover_fake(picked, monitor_gpu_enabled=True, show_usage_on_hover=True,
                       **mode)._hover_hardware_snapshot()
    assert [g["on_taskbar"] for g in snap["gpus"]] == [False, marked]
    assert all(set(g) == {"key", "name", "usage", "on_taskbar"} for g in snap["gpus"])


def test_a_card_set_change_rebuilds_the_open_card(q_app):
    fake = _hover_fake(CARDS, **ALL)
    card = MagicMock(has_hardware=True)
    card.update_hardware.return_value = False
    fake._hover_card, fake._hover_hw_refreshed = card, 0.0
    fake._show_usage_hover_card = MagicMock()
    fake._hide_usage_hover_card = MagicMock()
    fake._show_usage_hover_card.side_effect = lambda **k: setattr(fake, "_hover_card", MagicMock())
    fake._refresh_hover_hardware()
    fake._show_usage_hover_card.assert_called_once_with(pinned=False, rebuild_of=card)
    fake._hide_usage_hover_card.assert_not_called()


def test_a_rebuild_that_bails_closes_the_frozen_card(q_app):
    """Review #3: menu open / a drag / a fullscreen app - the rebuild does nothing; the old card,
    whose rows can no longer update, must not stay up."""
    fake = _hover_fake(CARDS, **ALL)
    card = MagicMock(has_hardware=True)
    card.update_hardware.return_value = False
    fake._hover_card, fake._hover_hw_refreshed = card, 0.0
    fake._show_usage_hover_card = MagicMock()          # bails: self._hover_card stays the old card
    fake._hide_usage_hover_card = MagicMock()
    fake._refresh_hover_hardware()
    fake._hide_usage_hover_card.assert_called_once()


def test_a_rebuild_keeps_the_pin_hint_without_counting_a_show(q_app):
    """A rebuild is the same card for the user: its footer stays as it was and the pin-hint counter
    (a config write) is not bumped once per rebuild."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    fake = types.SimpleNamespace(
        _is_context_menu_visible=False, _dragging=False, isVisible=lambda: True,
        config={"show_usage_on_hover": False},
        i18n=EN, logger=MagicMock(), _hover_card=None, _hover_hardware_wanted=lambda: False,
        _hover_hint_text=lambda: "hint", _pin_hint_text=MagicMock(return_value="counted"),
        _hover_card_edge=lambda: None, frameGeometry=lambda: None, screen=lambda: None,
        _retire_pin_hint=MagicMock())
    with patch("netspeedtray.views.usage_flyout.UsageFlyout") as flyout:
        for old_hint, expected in ((True, EN.HOVER_CARD_PIN_HINT), (False, None)):
            W._show_usage_hover_card(fake, rebuild_of=MagicMock(pin_hint_visible=old_hint))
            assert flyout.call_args.kwargs["pin_hint"] == expected
        fake._pin_hint_text.assert_not_called()
        W._show_usage_hover_card(fake)                      # a fresh hover still counts
        fake._pin_hint_text.assert_called_once()


@pytest.mark.parametrize("pinned", [False, True])
def test_a_card_opened_before_the_list_arrived_gains_it(q_app, pinned):
    """Review (2.2.1 rebase): a bottom-bar card opened before the first GPU poll (or pinned while GPU
    monitoring was switched on) had no hardware rows, so the 1 Hz refresh skipped it and the list
    never appeared until the next hover. It is rebuilt with the list, keeping its pin; a rebuild
    that bails leaves it up (no hardware rows to freeze)."""
    fake = _hover_fake(CARDS, **ALL)
    card = MagicMock(has_hardware=False)
    fake._hover_card, fake._hover_hw_refreshed, fake._hover_card_pinned = card, 0.0, pinned
    fake._show_usage_hover_card = MagicMock()          # bails
    fake._hide_usage_hover_card = MagicMock()
    fake._refresh_hover_hardware()
    fake._show_usage_hover_card.assert_called_once_with(pinned=pinned, rebuild_of=card)
    fake._hide_usage_hover_card.assert_not_called()
    # No list to gain (one GPU): nothing happens to a card without hardware.
    fake._show_usage_hover_card.reset_mock()
    fake.gpu_cards, fake._hover_hw_refreshed = CARDS[:1], 0.0
    fake._refresh_hover_hardware()
    fake._show_usage_hover_card.assert_not_called()


def test_a_list_that_goes_away_keeps_the_users_card(q_app):
    """Review (2.2.1 rebase): the list was a pinned bottom-bar card's only hardware and GPU
    monitoring is switched off (or an eGPU unplugged): the card the user pinned for its usage rows is
    rebuilt without the list, not closed. Closed only if the rebuild has nothing left to show."""
    fake = _hover_fake((), **ALL)
    card = MagicMock(has_hardware=True, lists_gpus=True)
    fake._hover_card, fake._hover_hw_refreshed, fake._hover_card_pinned = card, 0.0, True
    fake._show_usage_hover_card = MagicMock(side_effect=lambda **k: setattr(fake, "_hover_card", MagicMock()))
    fake._hide_usage_hover_card = MagicMock()
    fake._refresh_hover_hardware()
    fake._show_usage_hover_card.assert_called_once_with(pinned=True, rebuild_of=card)
    fake._hide_usage_hover_card.assert_not_called()
    # Nothing else to show: the rebuild bails, and the frozen card closes.
    fake._hover_card, fake._hover_hw_refreshed = card, 0.0
    fake._show_usage_hover_card = MagicMock()
    fake._refresh_hover_hardware()
    fake._hide_usage_hover_card.assert_called_once()
    # A side-bar hardware card (not only the list) still closes as before.
    other = MagicMock(has_hardware=True, lists_gpus=False)
    fake._hover_card, fake._hover_hw_refreshed = other, 0.0
    fake._show_usage_hover_card = MagicMock()
    fake._hide_usage_hover_card = MagicMock()
    fake._refresh_hover_hardware()
    fake._show_usage_hover_card.assert_not_called()
    fake._hide_usage_hover_card.assert_called_once()


def test_lists_gpus_only_for_a_card_whose_hardware_is_the_list(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    only = UsageFlyout(EN, None, None, hardware=_snap())
    mixed = UsageFlyout(EN, None, None, hardware=dict(_snap(), cpu={"usage": 5.0}))
    plain = UsageFlyout(EN, None, None, hardware={"cpu": {"usage": 5.0}})
    try:
        assert only.lists_gpus is True
        assert mixed.lists_gpus is False and plain.lists_gpus is False
    finally:
        for c in (only, mixed, plain):
            c.deleteLater()


# --- 8. the card ---------------------------------------------------------------------------------

def _texts(card):
    """Every label's text - a GPU name by its full name (its accessible name: offscreen, the test
    fonts are wide enough to elide even a short one)."""
    from PyQt6.QtWidgets import QLabel
    return [lbl.text() for lbl in card.findChildren(QLabel)] + [
        v.accessibleName() for k, v in card._hw_values.items() if k[0] == "gpus" and k[2] == "name"]


def _snap(cards=CARDS, **config):
    return _hover_fake(cards, **dict(ALL, **config))._hover_hardware_snapshot()


def test_card_lists_each_gpu_with_its_rows(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware=_snap())
    try:
        texts = _texts(card)
        for t in ("NVIDIA GeForce RTX 5070", "AMD Radeon Graphics", "3%", "0%", "26°C", "38°C",
                  "17.5 W", "6.0 W", "1.2 / 12.0 GB", "0.9 / 15.0 GB", "VRAM", "VRAM (shared)"):
            assert t in texts, (t, texts)
        assert "GPU" not in texts and EN.GPU_ON_TASKBAR_MARKER not in texts
        assert card.has_hardware
    finally:
        card.deleteLater()


def test_marker_shows_on_the_picked_card(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware=_snap((CARDS[0]._replace(picked=True), CARDS[1])))
    try:
        assert _texts(card).count(EN.GPU_ON_TASKBAR_MARKER) == 1
    finally:
        card.deleteLater()


def test_long_names_are_elided_and_live_updates_never_change_the_width(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    long = CARDS[0]._replace(name="NVIDIA GeForce RTX 5090 Laptop GPU with an Absurdly Long Driver Name")
    card = UsageFlyout(EN, None, None, hardware=_snap((long, CARDS[1])))
    try:
        name_lbl = card._hw_values[("gpus", (0, RTX_LUID), "name")]
        assert name_lbl.text().endswith("…") and name_lbl.text() != long.name
        assert name_lbl.sizeHint().width() <= UsageFlyout._GPU_NAME_MAX_W + 8
        card.adjustSize()
        width = card.sizeHint().width()
        busy = (long._replace(util=100.0, temp=99.0, power=250.0, vram_used_gib=99.9, vram_total_gib=99.9),
                CARDS[1]._replace(util=None, temp=None, power=None, vram_used_gib=None))
        assert card.update_hardware(_snap(busy)) is True
        assert "100%" in _texts(card) and "—" in _texts(card)
        card.adjustSize()
        assert card.sizeHint().width() == width
    finally:
        card.deleteLater()


def test_a_different_card_set_asks_for_a_rebuild(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware=_snap())
    try:
        third = GpuCard((0, 0xBEEF), "Intel Arc A770", False, 1.0)
        assert card.update_hardware(_snap(CARDS + (third,))) is False
        assert card.update_hardware(_snap((CARDS[0]._replace(picked=True), CARDS[1]))) is False
        assert card.update_hardware(_snap()) is True
    finally:
        card.deleteLater()


def test_a_han_adapter_name_is_drawn_in_its_own_face(q_app, monkeypatch):
    """Windows' text goes through styles.apply_text_face (no font fallback list, rc.7): offscreen,
    text_face is None by design, so it is faked here - the label must take the face it names."""
    from netspeedtray.utils import styles as su
    from netspeedtray.views.usage_flyout import UsageFlyout
    monkeypatch.setattr(su, "text_face", lambda text: "Microsoft YaHei UI" if "显卡" in str(text) else None)
    han = CARDS[1]._replace(name="显卡 GPU")
    card = UsageFlyout(EN, None, None, hardware=_snap((CARDS[0], han)))
    try:
        name_lbl = card._hw_values[("gpus", (0, APU_LUID), "name")]
        assert name_lbl.text() == "显卡 GPU"
        assert "Microsoft YaHei UI" in name_lbl.styleSheet()
        assert name_lbl.font().family() == "Microsoft YaHei UI"
        latin = card._hw_values[("gpus", (0, RTX_LUID), "name")]
        assert "YaHei" not in latin.styleSheet()
    finally:
        card.deleteLater()


# --- 9. Settings ----------------------------------------------------------------------------------

@pytest.fixture
def page(q_app, monkeypatch):
    from netspeedtray.views.settings.pages.hardware import HardwarePage
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: [APU_IG, RTX, BRD])
    p = HardwarePage(EN, lambda: None)
    yield p
    p.deleteLater()


def test_settings_toggle_round_trips_beside_the_picker(page, monkeypatch):
    page.load_settings({"monitor_gpu_enabled": True})
    assert page.get_settings()["hover_list_all_gpus"] is True
    assert page._hover_gpus_card.isVisibleTo(page) and page._hover_gpus_card.isEnabled()
    page.load_settings({"monitor_gpu_enabled": True, "hover_list_all_gpus": False})
    assert page.get_settings()["hover_list_all_gpus"] is False
    page.monitor_gpu.setChecked(False)
    assert not page._hover_gpus_card.isEnabled()
    # Two identical cards: one name in the picker (hidden), but the list has two rows to show.
    twin = RTX._replace(luid=(0, APU_LUID))
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: [RTX, twin, BRD])
    page.load_settings({"monitor_gpu_enabled": True})
    assert not page._gpu_adapter_card.isVisibleTo(page) and page._hover_gpus_card.isVisibleTo(page)
    # One GPU: hidden - even with a saved pick that keeps the picker listed (no Settings change at all).
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: [RTX, BRD])
    page.load_settings({"monitor_gpu_enabled": True})
    assert not page._hover_gpus_card.isVisibleTo(page)
    page.load_settings({"monitor_gpu_enabled": True, "gpu_adapter": "AMD Radeon(TM) Graphics"})
    assert page._gpu_adapter_card.isVisibleTo(page) and not page._hover_gpus_card.isVisibleTo(page)


# --- owner decision 2026-11-11: one source of truth for the GPU power Automatic shows ---------------

UHD_IG = AdapterInfo("Intel(R) UHD Graphics 770", 0x8086, (0, APU_LUID), 128 * 2 ** 20, int(15 * GIB), False)


def _poll_with_rapl(t, watts):
    t._power_query, t._power_pp1_counter = 5, 7
    with patch("win32pdh.GetFormattedCounterValue", return_value=(0, watts * 1000.0)):
        return _poll(t)


def test_the_igpu_row_shows_the_rapl_power_the_taskbar_shows(thread):
    """Automatic with no sensor group: the taskbar's GPU power is Intel RAPL PP1 - the iGPU's own power
    plane - so the iGPU's row shows that same figure, not "—"; the RTX row gets none of it."""
    thread._gpu_adapters = [UHD_IG, RTX, BRD]
    r = _poll_with_rapl(thread, 6.5)
    assert r.power == 6.5
    rtx, uhd = thread._gpu_cards(include_temp=True, include_power=True)
    assert uhd.name == "Intel UHD Graphics 770" and uhd.power == r.power == 6.5
    assert rtx.power is None
    assert thread._gpu_cards(include_temp=True, include_power=False)[1].power is None


def test_rapl_power_is_not_pinned_on_a_card_it_is_not_from(thread):
    # An AMD APU beside the RTX: RAPL PP1 is Intel's plane - no Intel iGPU, no row gets it.
    thread._gpu_adapters = [APU_IG, RTX, BRD]
    _poll_with_rapl(thread, 6.5)
    assert [c.power for c in thread._gpu_cards(True, True)] == [None, None]
    # A picked card reads exactly the taskbar (no RAPL for a picked RTX).
    thread._gpu_adapters = [UHD_IG, RTX, BRD]
    thread.config = dict(thread.config, gpu_adapter=RTX.name)
    r = _poll_with_rapl(thread, 6.5)
    rtx, uhd = thread._gpu_cards(True, True)
    assert r.power is None and (rtx.power, uhd.power) == (None, None)


# --- live test (twins, 2026-11-11): the duplicate number is never elided away ----------------------

def test_two_identical_long_names_keep_their_numbers_visible(q_app, monkeypatch):
    """Live twins scenario: both "NVIDIA GeForce RTX 4090 #1/#2" rendered "NVIDIA GeForce RTX 409…" -
    the number was elided away. Only the name is elided now; the number always shows, same budget."""
    from netspeedtray.views.usage_flyout import UsageFlyout
    names = gpu_adapters.numbered_display_names(["NVIDIA GeForce RTX 4090 Laptop GPU Founders Edition"] * 2)
    twins = (CARDS[0]._replace(luid=(0, 1), name=names[0]), CARDS[0]._replace(luid=(0, 2), name=names[1]))
    card = UsageFlyout(EN, None, None, hardware=_snap(twins))
    try:
        one, two = (card._hw_values[("gpus", (0, k), "name")] for k in (1, 2))
        assert one.text().endswith("… #1") and two.text().endswith("… #2")
        assert one.text() != two.text()
        assert one.sizeHint().width() <= UsageFlyout._GPU_NAME_MAX_W + 8
        assert one.accessibleName() == names[0]
    finally:
        card.deleteLater()

    # A Han name keeps its own face on the same path, number kept too.
    from netspeedtray.utils import styles as su
    monkeypatch.setattr(su, "text_face", lambda text: "Microsoft YaHei UI" if "显卡" in str(text) else None)
    han = gpu_adapters.numbered_display_names(["显卡" * 30] * 2)
    card = UsageFlyout(EN, None, None, hardware=_snap((CARDS[0]._replace(luid=(0, 1), name=han[0]),
                                                       CARDS[0]._replace(luid=(0, 2), name=han[1]))))
    try:
        lbl = card._hw_values[("gpus", (0, 2), "name")]
        assert lbl.text().endswith("… #2") and lbl.font().family() == "Microsoft YaHei UI"
    finally:
        card.deleteLater()


def test_set_elided_text_in_face_keeps_a_suffix_only_when_it_ends_the_text(q_app):
    from PyQt6.QtWidgets import QLabel
    from netspeedtray.utils import styles as su
    lbl = QLabel()
    su.set_elided_text_in_face(lbl, "Short #1", 500, keep_suffix=" #1")
    assert lbl.text() == "Short #1"                                  # fits: untouched
    su.set_elided_text_in_face(lbl, "A very long adapter name " * 5, 80, keep_suffix=" #1")
    assert lbl.text().endswith("…")                                   # suffix not at the end: plain elide
