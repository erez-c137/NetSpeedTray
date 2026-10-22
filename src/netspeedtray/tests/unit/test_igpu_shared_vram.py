"""
An integrated GPU's shared memory is shown as its "VRAM" (owner decision, 2026-10-22).

PDH's `\\GPU Adapter Memory(*)\\Dedicated Usage` is 0 on an Intel iGPU, so an iGPU-only machine
showed a meaningless "VRAM 0.0 GB" (or nothing). The iGPU's real footprint is its Shared Usage -
~680 MiB on the owner's Intel UHD 770 when this was written. Rules pinned here:

- Whether the machine has real dedicated memory is a STATIC adapter fact (DXGI
  DedicatedVideoMemory >= DEDICATED_VIDEO_MEMORY_MIN_MIB on a non-software adapter), read at query
  init - never a usage reading, which misreads a dGPU that is idle at startup and flips mid-session.
- nvidia-smi present, a failed dedicated read, or a Basic-Render-only machine: today's readout.
- Otherwise the real adapter (by LUID) using the most shared memory, against its DXGI
  SharedSystemMemory budget, with ``vram_is_shared`` set.
- No DXGI: the usage latch + a 1 MiB floor, against half of physical RAM (fallback).
- The taskbar keeps "VRAM"; the hover card and the Monitor say "VRAM (shared)"; the metrics
  endpoint labels the series kind="shared".
"""
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import gpu_adapters
from netspeedtray.core.gpu_adapters import AdapterInfo
from netspeedtray.core.monitor_thread import StatsMonitorThread

MIB = 1024.0 * 1024.0
GIB = 1024 ** 3
GIB_RAM = 32 * GIB                # a 32 GiB machine -> 16384 MiB fallback shared budget
UTIL = {"pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 5.0}

# PDH instance names and the matching DXGI adapters.
IGPU_I, DGPU_I, BRD_I = "luid_0x00000000_0x0000D9C1_phys_0", "luid_0x00000000_0x0000AB01_phys_0", \
    "luid_0x00000000_0x0000DD11_phys_0"
INTEL_UHD = AdapterInfo("Intel(R) UHD Graphics 770", 0x8086, (0, 0xD9C1), 128 * 2 ** 20,
                        int(7.839 * GIB), False)
BASIC_RENDER = AdapterInfo("Microsoft Basic Render Driver", 0x1414, (0, 0xDD11), 0, int(7.839 * GIB), True)
ARC_DGPU = AdapterInfo("Intel(R) Arc(TM) A770M", 0x8086, (0, 0xAB01), 16 * GIB, int(7.839 * GIB), False)
RADEON_DGPU = AdapterInfo("AMD Radeon RX 6600", 0x1002, (0, 0xAB01), 8 * GIB, int(7.839 * GIB), False)
AMD_APU = AdapterInfo("AMD Radeon(TM) Graphics", 0x1002, (0, 0xD9C1), 512 * 2 ** 20, int(7.0 * GIB), False)


@pytest.fixture
def thread(q_app, monkeypatch):
    # The G4 re-read must never reach the real DXGI from a unit test: these fixtures set the facts.
    monkeypatch.setattr(gpu_adapters, "enumerate_adapters", lambda: None)
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter = 1
    t._gpu_vram_counter = 2
    t._gpu_shared_counter = 3
    t._nvidia_smi_path = None
    t._wmi_ohm = False
    t._gpu_adapters = None            # fallback unless a test sets the DXGI facts
    return t


def _poll(thread, dedicated, shared):
    """One poll. `shared=None` means the Shared Usage counter could not be added; an Exception
    instance as `dedicated` or `shared` makes that read raise."""
    if shared is None:
        thread._gpu_shared_counter = None
        arrays = [UTIL, dedicated]
    else:
        arrays = [UTIL, dedicated, shared]
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=arrays), \
         patch("netspeedtray.core.monitor_thread.psutil.virtual_memory",
               return_value=types.SimpleNamespace(total=GIB_RAM)):
        return thread._poll_gpu_hybrid(include_temp=False, include_power=False)


# --- DXGI static facts ------------------------------------------------------------------------------

def test_igpu_only_reports_shared_usage_against_the_dxgi_budget(thread):
    """The owner's PC: Intel UHD 770 (DXGI 128 MiB stolen) + Basic Render, both 0 dedicated usage."""
    thread._gpu_adapters = [INTEL_UHD, BASIC_RENDER]
    r = _poll(thread, {IGPU_I: 0.0, BRD_I: 0.0}, {IGPU_I: 682 * MIB, BRD_I: 0.0})
    assert r.vram_is_shared is True
    assert r.vram_used == pytest.approx(682.0)
    assert r.vram_total == pytest.approx(7.839 * 1024, rel=1e-6)   # SharedSystemMemory, not RAM/2


@pytest.mark.parametrize("dgpu", [ARC_DGPU, RADEON_DGPU], ids=["intel+arc", "amd+radeon"])
def test_idle_non_nvidia_dgpu_at_startup_never_reads_as_shared_nor_flips(thread, dgpu):
    """M1: a hybrid laptop / idle eGPU / desktop with the monitor on the motherboard. The dGPU reads
    0 dedicated at startup; the static fact keeps the dedicated readout, then and after it wakes."""
    thread._gpu_adapters = [INTEL_UHD, dgpu]
    idle = _poll(thread, {IGPU_I: 0.0, DGPU_I: 0.0}, {IGPU_I: 600 * MIB, DGPU_I: 2 * MIB})
    assert idle.vram_is_shared is False and idle.vram_used == 0.0
    busy = _poll(thread, {IGPU_I: 0.0, DGPU_I: 3 * 1024 * MIB}, {IGPU_I: 600 * MIB, DGPU_I: 2 * MIB})
    assert busy.vram_is_shared is False and busy.vram_used == pytest.approx(3072.0)
    idle_again = _poll(thread, {IGPU_I: 0.0, DGPU_I: 0.0}, {IGPU_I: 600 * MIB, DGPU_I: 2 * MIB})
    assert idle_again.vram_is_shared is False


def test_igpu_only_never_flips_even_when_its_dedicated_usage_spikes(thread):
    """The usage latch is fallback-only: with DXGI facts a usage reading cannot change the mode."""
    thread._gpu_adapters = [INTEL_UHD, BASIC_RENDER]
    assert _poll(thread, {IGPU_I: 300 * MIB}, {IGPU_I: 700 * MIB}).vram_is_shared is True
    assert _poll(thread, {IGPU_I: 0.0}, {IGPU_I: 700 * MIB}).vram_is_shared is True


def test_small_stolen_chunk_is_not_dedicated_memory(thread):
    small = AdapterInfo("Intel(R) HD Graphics", 0x8086, (0, 0xD9C1), 256 * 2 ** 20, int(4 * GIB), False)
    thread._gpu_adapters = [small]
    r = _poll(thread, {IGPU_I: 0.0}, {IGPU_I: 900 * MIB})
    assert r.vram_is_shared is True and r.vram_used == pytest.approx(900.0)


def test_apu_carve_out_at_the_threshold_keeps_the_dedicated_readout(thread):
    """AMD APU, 512 MiB UMA frame buffer: real carve-out usage, today's behavior."""
    thread._gpu_adapters = [AMD_APU]
    r = _poll(thread, {IGPU_I: 400 * MIB}, {IGPU_I: 900 * MIB})
    assert r.vram_is_shared is False and r.vram_used == pytest.approx(400.0)


def test_basic_render_only_keeps_todays_hidden_vram(thread):
    """M2: VMs, servers, broken GPU drivers - no fake "iGPU 0.0/8.0G"."""
    thread._gpu_adapters = [BASIC_RENDER]
    r = _poll(thread, {BRD_I: 0.0}, {BRD_I: 0.0})
    assert r.vram_is_shared is False and r.vram_used == 0.0 and r.vram_total is None


def test_shared_reading_of_an_unknown_luid_is_not_used(thread):
    thread._gpu_adapters = [INTEL_UHD]
    r = _poll(thread, {"luid_0x0_0xFFFF_phys_0": 0.0}, {"luid_0x0_0xFFFF_phys_0": 500 * MIB})
    assert r.vram_is_shared is False


def test_dxgi_budget_missing_falls_back_to_half_of_ram(thread):
    thread._gpu_adapters = [INTEL_UHD._replace(shared_system_bytes=0)]
    r = _poll(thread, {IGPU_I: 0.0}, {IGPU_I: 500 * MIB})
    assert r.vram_is_shared is True and r.vram_total == pytest.approx(16384.0)


def test_dedicated_read_throwing_never_falls_into_shared_mode(thread):
    thread._gpu_adapters = [INTEL_UHD]
    r = _poll(thread, RuntimeError("PDH_CSTATUS_INVALID_DATA"), {IGPU_I: 500 * MIB})
    assert r.vram_is_shared is False and r.vram_used is None
    thread._gpu_adapters = None
    r = _poll(thread, RuntimeError("PDH_CSTATUS_INVALID_DATA"), {IGPU_I: 500 * MIB})
    assert r.vram_is_shared is False and r.vram_used is None


def test_an_nvidia_driver_keeps_the_dedicated_path(thread):
    thread._gpu_adapters = [INTEL_UHD]
    thread._nvidia_smi_path = r"C:\Windows\System32\nvidia-smi.exe"
    thread._nvidia_cache_vram_total = 8192.0
    thread._nvidia_last_poll = float("inf")         # don't actually run nvidia-smi
    r = _poll(thread, {IGPU_I: 0.0}, {IGPU_I: 500 * MIB})
    assert r.vram_is_shared is False
    assert r.vram_used == 0.0 and r.vram_total == 8192.0


def test_query_init_reads_the_static_facts_and_a_rebuild_rereads_them(thread):
    thread._gpu_query = None
    with patch.object(gpu_adapters, "enumerate_adapters", side_effect=[[INTEL_UHD], [INTEL_UHD, ARC_DGPU]]), \
         patch("win32pdh.OpenQuery", return_value=77), patch("win32pdh.AddCounter", return_value=5), \
         patch("win32pdh.CollectQueryData"), patch("win32pdh.CloseQuery"):
        assert thread._init_gpu_query() is True
        assert thread._gpu_adapters == [INTEL_UHD]
        thread._cleanup_gpu_query()                # resume / eGPU plugged in
        assert thread._init_gpu_query() is True
        assert thread._gpu_adapters == [INTEL_UHD, ARC_DGPU]


def test_pdh_luid_parsing_and_software_filter():
    assert gpu_adapters.pdh_instance_luid(IGPU_I) == (0, 0xD9C1)
    assert gpu_adapters.pdh_instance_luid("pid_4_luid_0x00000001_0x0000ABCD_phys_0_eng_0") == (1, 0xABCD)
    assert gpu_adapters.pdh_instance_luid("garbage") is None
    assert gpu_adapters.hardware_adapters([INTEL_UHD, BASIC_RENDER]) == [INTEL_UHD]
    assert gpu_adapters.hardware_adapters([BASIC_RENDER._replace(software=False)]) == []   # vendor 0x1414


def test_enumerate_adapters_is_none_off_windows():
    with patch.object(gpu_adapters.sys, "platform", "linux"):
        assert gpu_adapters.enumerate_adapters() is None


# --- no DXGI: fallback --------------------------------------------------------------------------------

def test_fallback_discrete_gpu_is_unchanged(thread):
    r = _poll(thread, {"luid_0x0_0xA_phys_0": 2048 * MIB, "luid_0x0_0xB_phys_0": 0.0},
              {"luid_0x0_0xA_phys_0": 300 * MIB, "luid_0x0_0xB_phys_0": 700 * MIB})
    assert r.vram_used == 2048.0          # dedicated, summed across adapters, as before
    assert r.vram_total is None           # no nvidia-smi -> no total, as before
    assert r.vram_is_shared is False


def test_fallback_igpu_reports_shared_against_half_of_ram(thread):
    r = _poll(thread, {IGPU_I: 0.0, BRD_I: 0.0}, {IGPU_I: 682 * MIB, BRD_I: 0.0})
    assert r.vram_is_shared is True
    assert r.vram_used == pytest.approx(682.0)
    assert r.vram_total == pytest.approx(16384.0)


def test_fallback_tiny_carve_out_counts_as_no_dedicated_memory(thread):
    small = constants.gpu.DEDICATED_USAGE_FALLBACK_MIB / 2
    r = _poll(thread, {"a": small * MIB}, {"a": 900 * MIB})
    assert r.vram_is_shared is True


def test_fallback_basic_render_only_keeps_todays_hidden_vram(thread):
    """M2 without DXGI: 0 shared is no iGPU."""
    r = _poll(thread, {BRD_I: 0.0}, {BRD_I: 0.0})
    assert r.vram_is_shared is False and r.vram_used == 0.0 and r.vram_total is None


def test_fallback_dedicated_usage_once_seen_is_latched(thread):
    at = constants.gpu.DEDICATED_USAGE_FALLBACK_MIB
    assert _poll(thread, {"a": at * MIB}, {"a": 10 * MIB, "b": 600 * MIB}).vram_is_shared is False
    r = _poll(thread, {"a": 0.0}, {"a": 10 * MIB, "b": 600 * MIB})
    assert r.vram_is_shared is False and r.vram_used == 0.0
    with patch("win32pdh.CloseQuery"):
        thread._cleanup_gpu_query()                 # a resume / settings rebuild keeps the latch
    assert thread._gpu_dedicated_seen is True


def test_missing_shared_counter_keeps_todays_behavior(thread):
    r = _poll(thread, {"a": 0.0}, None)
    assert r.vram_is_shared is False
    assert r.vram_used == 0.0 and r.vram_total is None


def test_shared_counter_with_no_instances_keeps_todays_behavior(thread):
    r = _poll(thread, {"a": 0.0}, {})
    assert r.vram_is_shared is False
    assert r.vram_used == 0.0


def test_shared_read_error_keeps_todays_behavior(thread):
    thread._gpu_adapters = [INTEL_UHD]
    r = _poll(thread, {IGPU_I: 0.0}, RuntimeError("PDH_CSTATUS_INVALID_DATA"))
    assert r.vram_is_shared is False
    assert r.vram_used == 0.0


# --- controller ---------------------------------------------------------------------------------------

def test_controller_flags_the_view_before_emitting(q_app):
    from netspeedtray.core.controller import StatsController
    ctl = StatsController({"record_hardware_history": False}, MagicMock())
    ctl.view = types.SimpleNamespace(vram_is_shared=False)
    seen = []
    ctl.vram_info_updated.connect(lambda u, t: seen.append((u, t, ctl.view.vram_is_shared)))
    ctl.handle_stats({"gpu": 5.0, "vram_used": 0.67, "vram_total": 16.0, "vram_is_shared": True})
    assert seen == [(0.67, 16.0, True)]
    ctl.handle_stats({"gpu": 5.0, "vram_used": 3.0, "vram_total": 8.0})     # discrete: no key
    assert seen[-1] == (3.0, 8.0, False)


# --- display ----------------------------------------------------------------------------------------

EN = I18nStrings("en_US")


def test_every_locale_has_the_shared_label():
    import json
    from pathlib import Path
    folder = Path(constants.__file__).resolve().parent / "locales"
    for f in folder.glob("*.json"):
        value = json.loads(f.read_text(encoding="utf-8"))["VRAM_SHARED_LABEL"]
        assert "VRAM" in value, f.name


def test_hover_card_says_shared(q_app):
    from PyQt6.QtWidgets import QLabel
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware={"gpu": {"usage": 5.0}, "vram": (0.67, 16.0),
                                                  "vram_shared": True})
    try:
        texts = [lbl.text() for lbl in card.findChildren(QLabel)]
        assert "VRAM (shared)" in texts and "0.7 / 16.0 GB" in texts, texts
    finally:
        card.deleteLater()


def test_hover_card_discrete_label_unchanged(q_app):
    from PyQt6.QtWidgets import QLabel
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware={"gpu": {"usage": 5.0}, "vram": (3.1, 8.0)})
    try:
        texts = [lbl.text() for lbl in card.findChildren(QLabel)]
        assert "VRAM" in texts and "VRAM (shared)" not in texts
    finally:
        card.deleteLater()


def test_hover_snapshot_carries_the_flag(q_app):
    from netspeedtray.views.widget.main import NetworkSpeedWidget as W
    from netspeedtray.utils.widget_renderer import RenderConfig  # noqa: F401 (import side effects)
    cfg = {"show_usage_on_hover": False, "show_hover_tips": False, "hardware_in_hover_card": True,
           "widget_display_mode": "side_by_side", "monitor_cpu_enabled": True,
           "monitor_gpu_enabled": True, "monitor_vram_enabled": True}
    fake = types.SimpleNamespace(
        config=cfg, _cached_layout_mode="horizontal", layout_manager=types.SimpleNamespace(_side_fit=None),
        cpu_usage=1.0, gpu_usage=2.0, cpu_temp=None, gpu_temp=None, cpu_power=None, gpu_power=None,
        ram_used=None, ram_total=None, vram_used=0.67, vram_total=16.0, vram_is_shared=True,
        network_identity=None, logger=MagicMock())
    for name in ("_side_bar_fit", "_hover_hardware_wanted", "_hover_hardware_snapshot",
                 "_identity_presentation", "_identity_ssid_text"):
        setattr(fake, name, types.MethodType(getattr(W, name), fake))
    snap = fake._hover_hardware_snapshot()
    assert snap["vram"] == (0.67, 16.0) and snap["vram_shared"] is True
    fake.vram_is_shared = False
    assert "vram_shared" not in fake._hover_hardware_snapshot()


def test_monitor_telemetry_tile_says_shared_and_shows_a_small_reading(q_app):
    from netspeedtray.views.monitor.hardware.telemetry import TelemetryStrip
    s = TelemetryStrip({}, EN)
    w = types.SimpleNamespace(cpu_usage=1.0, gpu_usage=2.0, gpu_present=True, cpu_temp=None,
                              gpu_temp=None, cpu_power=None, gpu_power=None, ram_used=8.0,
                              ram_total=32.0, vram_used=0.03, vram_total=16.0, vram_is_shared=True)
    s.update_from(w)
    assert not s._vram.isHidden()
    assert s._vram._caption.text() == "VRAM (shared)"
    assert s._vram._value.text() == "0.0 / 16.0 GB"
    w.vram_is_shared, w.vram_used, w.vram_total = False, 3.1, 8.0
    s.update_from(w)
    assert s._vram._caption.text() == "VRAM"
    assert s._vram._value.text() == "3.1 / 8.0 GB"


def test_monitor_overview_vram_tile_says_shared(q_app):
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    mw = _MW()
    mw.vram_used, mw.vram_total, mw.vram_is_shared = 0.03, 16.0, True
    ov = OverviewTab(mw, _cfg(), EN)
    ov.show()
    q_app.processEvents()
    try:
        assert ov._tiles["vram"].isVisibleTo(ov)               # a small shared reading is still real
        assert ov._tiles["vram"]._label.text() == "VRAM (shared)"
        assert ov._tiles["gpu"]._label.text() == "iGPU"
        assert ov._tiles["vram"]._value.text() == "0%"         # percent of the shared budget
    finally:
        ov.hide()
        ov.teardown()


def test_hover_card_relabels_the_vram_row_on_a_mode_change(q_app):
    from PyQt6.QtWidgets import QLabel
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware={"gpu": {"usage": 5.0}, "vram": (0.67, 7.8),
                                                  "vram_shared": True})
    try:
        card.update_hardware({"gpu": {"usage": 5.0}, "vram": (3.1, 8.0)})
        texts = [lbl.text() for lbl in card.findChildren(QLabel)]
        assert "VRAM" in texts and "VRAM (shared)" not in texts and "3.1 / 8.0 GB" in texts, texts
        card.update_hardware({"gpu": {"usage": 5.0}, "vram": (0.67, 7.8), "vram_shared": True})
        assert "VRAM (shared)" in [lbl.text() for lbl in card.findChildren(QLabel)]
    finally:
        card.deleteLater()


def test_monitor_overview_vram_detail_sheet_label(q_app):
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    mw = _MW()
    mw.vram_is_shared = True
    ov = OverviewTab(mw, _cfg(), EN)
    try:
        assert ov._detail_subjects("vram")[0]["label"] == "VRAM (shared)"
        mw.vram_is_shared = False
        assert ov._detail_subjects("vram")[0]["label"] == "VRAM"
    finally:
        ov.teardown()


def test_monitor_overview_vram_series_resets_on_a_mode_change(q_app):
    from netspeedtray.tests.unit.test_overview_tiles import _MW, _cfg
    from netspeedtray.views.monitor.overview.tab import OverviewTab
    mw = _MW()
    mw.vram_used, mw.vram_total, mw.vram_is_shared = 0.8, 8.0, True
    ov = OverviewTab(mw, _cfg(), EN)
    ov.show()
    q_app.processEvents()
    try:
        assert list(ov._vram_series) and ov._vram_series[-1] == pytest.approx(10.0)
        mw.vram_used, mw.vram_is_shared = 4.0, False
        ov._render()
        assert list(ov._vram_series) == [pytest.approx(50.0)]
    finally:
        ov.hide()
        ov.teardown()


# --- metrics endpoint (L4) ----------------------------------------------------------------------------

def _scrape(snap):
    from netspeedtray.core.metrics_endpoint import render_metrics
    return render_metrics(snap, scrape_time=1.0)


def test_metrics_label_dedicated_memory_kind_dedicated():
    from netspeedtray.core.metrics_endpoint import MetricsSnapshot
    text = _scrape(MetricsSnapshot(version="1", vram_used_bytes=2e9, vram_total_bytes=8e9))
    assert 'netspeedtray_gpu_memory_used_bytes{kind="dedicated"} 2000000000' in text
    assert 'netspeedtray_gpu_memory_total_bytes{kind="dedicated"} 8000000000' in text


def test_metrics_label_igpu_memory_kind_shared():
    from netspeedtray.core.metrics_snapshot import build_snapshot
    widget = MagicMock(vram_used=0.5, vram_total=8.0, vram_is_shared=True, gpu_usage=3.0,
                       config={"monitor_gpu_enabled": True, "monitor_vram_enabled": True})
    snap = build_snapshot(widget)
    assert snap.vram_kind == "shared"
    text = _scrape(snap)
    assert 'netspeedtray_gpu_memory_used_bytes{kind="shared"} 536870912' in text
    assert 'netspeedtray_gpu_memory_total_bytes{kind="shared"}' in text
    widget.vram_is_shared = False
    assert build_snapshot(widget).vram_kind == "dedicated"
