"""
An integrated GPU's shared memory is shown as its "VRAM" (owner decision, 2026-10-22).

PDH's `\\GPU Adapter Memory(*)\\Dedicated Usage` is 0 on an Intel iGPU, so an iGPU-only machine
showed a meaningless "VRAM 0.0 GB" (or nothing). The iGPU's real footprint is its Shared Usage -
682 MiB on the owner's Intel UHD 770 when this was written. Rules pinned here:

- Any adapter with dedicated usage at or above IGPU_DEDICATED_THRESHOLD_MIB (latched), or an
  NVIDIA driver (nvidia-smi), keeps the dedicated readout exactly as before.
- Otherwise the busiest adapter's Shared Usage is reported, against half of physical RAM (Windows'
  shared GPU memory budget; PDH has no limit counter), with ``vram_is_shared`` set.
- No Shared Usage counter / no instances: today's behavior.
- The taskbar keeps "VRAM"; the hover card and the Monitor say "VRAM (shared)".
"""
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.monitor_thread import StatsMonitorThread

MIB = 1024.0 * 1024.0
GIB_RAM = 32 * 1024 ** 3          # a 32 GiB machine -> 16384 MiB shared budget
UTIL = {"pid_1_luid_0x0_0xA_phys_0_eng_0_engtype_3D": 5.0}


@pytest.fixture
def thread(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    t._gpu_query = 123
    t._gpu_util_counter = 1
    t._gpu_vram_counter = 2
    t._gpu_shared_counter = 3
    t._nvidia_smi_path = None
    t._wmi_ohm = False
    return t


def _poll(thread, dedicated, shared):
    """One poll. `shared=None` means the Shared Usage counter could not be added."""
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


# --- collection -------------------------------------------------------------------------------------

def test_discrete_gpu_is_unchanged(thread):
    r = _poll(thread, {"luid_0x0_0xA_phys_0": 2048 * MIB, "luid_0x0_0xB_phys_0": 0.0},
              {"luid_0x0_0xA_phys_0": 300 * MIB, "luid_0x0_0xB_phys_0": 700 * MIB})
    assert r.vram_used == 2048.0          # dedicated, summed across adapters, as before
    assert r.vram_total is None           # no nvidia-smi -> no total, as before
    assert r.vram_is_shared is False


def test_igpu_only_reports_shared_usage_against_half_of_ram(thread):
    """The owner's PC: Intel UHD 770 + a second adapter, both 0 dedicated, 682 MiB shared."""
    r = _poll(thread, {"luid_0x0_0xD9C1_phys_0": 0.0, "luid_0x0_0xDD11_phys_0": 0.0},
              {"luid_0x0_0xD9C1_phys_0": 682 * MIB, "luid_0x0_0xDD11_phys_0": 0.0})
    assert r.vram_is_shared is True
    assert r.vram_used == pytest.approx(682.0)
    assert r.vram_total == pytest.approx(16384.0)   # 32 GiB * SHARED_BUDGET_FRACTION_OF_RAM


def test_tiny_carve_out_counts_as_no_dedicated_memory(thread):
    small = constants.gpu.IGPU_DEDICATED_THRESHOLD_MIB / 2
    r = _poll(thread, {"luid_0x0_0xA_phys_0": small * MIB}, {"luid_0x0_0xA_phys_0": 900 * MIB})
    assert r.vram_is_shared is True
    assert r.vram_used == pytest.approx(900.0)


def test_carve_out_at_the_threshold_is_dedicated(thread):
    at = constants.gpu.IGPU_DEDICATED_THRESHOLD_MIB
    r = _poll(thread, {"luid_0x0_0xA_phys_0": at * MIB}, {"luid_0x0_0xA_phys_0": 900 * MIB})
    assert r.vram_is_shared is False
    assert r.vram_used == pytest.approx(at)


def test_missing_shared_counter_keeps_todays_behavior(thread):
    r = _poll(thread, {"luid_0x0_0xA_phys_0": 0.0}, None)
    assert r.vram_is_shared is False
    assert r.vram_used == 0.0 and r.vram_total is None


def test_shared_counter_with_no_instances_keeps_todays_behavior(thread):
    r = _poll(thread, {"luid_0x0_0xA_phys_0": 0.0}, {})
    assert r.vram_is_shared is False
    assert r.vram_used == 0.0


def test_shared_read_error_keeps_todays_behavior(thread):
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray",
               side_effect=[UTIL, {"luid_0x0_0xA_phys_0": 0.0}, RuntimeError("PDH_CSTATUS_INVALID_DATA")]):
        r = thread._poll_gpu_hybrid(include_temp=False, include_power=False)
    assert r.vram_is_shared is False
    assert r.vram_used == 0.0


def test_dedicated_memory_once_seen_is_latched(thread):
    """A hybrid laptop's dGPU idles to ~0 dedicated - the readout must not swap to the iGPU's."""
    assert _poll(thread, {"a": 1024 * MIB}, {"a": 10 * MIB, "b": 600 * MIB}).vram_is_shared is False
    r = _poll(thread, {"a": 0.0}, {"a": 10 * MIB, "b": 600 * MIB})
    assert r.vram_is_shared is False and r.vram_used == 0.0
    with patch("win32pdh.CloseQuery"):
        thread._cleanup_gpu_query()                 # a resume / settings rebuild keeps the latch
    assert thread._gpu_dedicated_seen is True


def test_an_nvidia_driver_keeps_the_dedicated_path(thread):
    thread._nvidia_smi_path = r"C:\Windows\System32\nvidia-smi.exe"
    thread._nvidia_cache_vram_total = 8192.0
    thread._nvidia_last_poll = float("inf")         # don't actually run nvidia-smi
    r = _poll(thread, {"a": 0.0}, {"a": 500 * MIB})
    assert r.vram_is_shared is False
    assert r.vram_used == 0.0 and r.vram_total == 8192.0


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
