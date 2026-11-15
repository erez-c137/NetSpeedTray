"""
2.2.1 GPU follow-ups to the Optimus gate (#500/#532/#537).

H4 - the first utilization sample after a GPU PDH query (re)build (Settings save, the Monitor window
flipping GPU collection for a VRAM-only user, resume) covers only the moment since the priming
collection: a near-zero window whose value is noise. That poll holds the readout (no 'gpu' key: the
taskbar keeps its last value, nothing is recorded) and the Optimus gate does not count it.

H5 - on a hybrid laptop the idle NVIDIA dGPU's temperature / power read a bare "—" (the gate stops
reading them so it can sleep). The hover card now says "Idle" beside that GPU - only when the gate
judged it idle (this poll, nvidia-smi healthy) AND its readings are blank because of that: never for a
missing sensor, never next to a live LHM reading. "Idle", not "Asleep": the gate only knows there was
no engine activity within the hold. The taskbar widget is unchanged.
"""
import subprocess
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import monitor_thread as mt
from netspeedtray.core.lhm_http import LhmGpu, LhmSnapshot
from netspeedtray.core.monitor_thread import GpuCard, StatsMonitorThread
from netspeedtray.tests.unit.test_optimus_nvidia_smi_wake import (  # noqa: F401 (clock: a fixture)
    BRD, DGPU_BUSY, IGPU_BUSY, NV_LUID, IG_LUID, RTX, UHD, _thread, _tick, clock)
from netspeedtray.tests.unit.test_hover_all_gpus_236 import ALL, _hover_fake
from netspeedtray.tests.unit.test_ram_vram_independent import _ticks, thread  # noqa: F401 (fixture)

EN = I18nStrings("en_US")


def _rebuild(t):
    """Drop the query the way run() does on a settings change / resume; the next poll rebuilds it."""
    with patch("win32pdh.CloseQuery"):
        t._cleanup_gpu_query()
    t._gpu_query_retry_after = 0.0


def _tick_rebuilding(t, clock, util, **kw):
    with patch("win32pdh.OpenQuery", return_value=321), \
         patch("win32pdh.AddCounter", side_effect=lambda q, path: {"GPU Engine": 1, "Dedicated": 2}.get(
             next((k for k in ("GPU Engine", "Dedicated") if k in path), ""), 3)):
        return _tick(t, clock, util, **kw)


# --- H4: no first-sample blip after a rebuild --------------------------------------------------

def test_the_first_sample_after_a_rebuild_is_held_then_readings_resume(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], False, monkeypatch, mobile=False)   # a desktop: no gate involved
    assert _tick(t, clock, DGPU_BUSY).util == 40.0
    _rebuild(t)
    held = _tick_rebuilding(t, clock, DGPU_BUSY)
    assert t._gpu_query == 321
    assert held.util is None                       # not a reading: the readout keeps its last value
    assert held.vram_used is not None              # memory is an instantaneous counter: still read
    assert _tick(t, clock, DGPU_BUSY).util == 40.0
    assert _tick(t, clock, IGPU_BUSY).util == 25.0


def test_vram_only_rebuilds_hold_nothing_and_a_forced_gpu_holds_one_tick(thread):
    """The real loop (review of #542): (a) VRAM alone, its query invalidated between ticks - memory on
    every tick including the rebuild one, never a GPU reading, never a pending hold; (b) the Monitor
    window then forces GPU usage - the rebuilt query's first tick sends no 'gpu', the next one does,
    memory on both."""
    settling = []

    def between(i):
        settling.append(thread._gpu_util_settling)
        if i == 1:
            thread.invalidate_hardware_queries()
        if i == 3:
            thread._force_hardware_collection = True
    got, pdh, _m = _ticks(thread, {"monitor_vram_enabled": True}, n=5, between=between)
    assert pdh.opened == 3                         # start, the invalidation, the forced GPU usage
    for tick in got:
        assert tick["vram_used"] == pytest.approx(3000 / 1024)
    assert ["gpu" in tick for tick in got[:3]] == [False, False, False]
    assert settling[:3] == [False, False, False]
    assert "gpu" not in got[3] and settling[3] is False    # the held tick, then cleared
    assert got[4]["gpu"] == 0.0


def test_no_engine_counter_means_nothing_to_hold(q_app, monkeypatch, clock):
    """Utilization wanted but the \\GPU Engine counter cannot be added: no hold is pending, and the
    first poll reports 0.0 (no counter, as before) - not None."""
    t = _thread([UHD, RTX, BRD], False, monkeypatch, mobile=False)
    _rebuild(t)
    t._gpu_util_wanted = True

    def add_counter(q, path):
        if "GPU Engine" in path:
            raise RuntimeError("PDH_CSTATUS_NO_OBJECT")
        return 2 if "Dedicated" in path else 3
    with patch("win32pdh.OpenQuery", return_value=321), patch("win32pdh.AddCounter", side_effect=add_counter), \
         patch("win32pdh.CollectQueryData"):
        assert t._init_gpu_query()
    assert t._gpu_util_counter is None and t._gpu_util_settling is False
    dedicated = {f"luid_0x00000000_0x{NV_LUID:08X}_phys_0": 100 * 1024.0 * 1024.0}
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=[dedicated, {}]), \
         patch.object(mt.psutil, "virtual_memory", return_value=types.SimpleNamespace(total=32 * 1024 ** 3)), \
         patch.object(StatsMonitorThread, "_init_ohm_wmi"):
        assert t._poll_gpu_hybrid(include_temp=False, include_power=False).util == 0.0


def test_the_gate_does_not_count_the_settling_sample(q_app, monkeypatch, clock):
    """The settling window is a sliver: activity in it is not "the dGPU woke up", and no nvidia-smi
    call is made on it - the next, full sample decides."""
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    for _ in range(40):
        _tick(t, clock, IGPU_BUSY)                 # idle, past any hold
    assert t._run_child.call_count == 0
    _rebuild(t)
    _tick_rebuilding(t, clock, DGPU_BUSY)
    assert t._run_child.call_count == 0 and t._nvidia_active_at is None
    r = _tick(t, clock, DGPU_BUSY)                 # a real sample with the dGPU busy: read
    assert t._run_child.call_count == 1 and r.temp == 52.0


def _one_run_tick(thread, poll_result, cards=()):
    thread.config = {"monitor_gpu_enabled": True, "show_hardware_temps": True}
    thread._is_running = True
    got = {}

    def emit(stats):
        got.update(stats)
        thread._is_running = False

    thread.stats_ready = types.SimpleNamespace(emit=emit)
    with patch("netspeedtray.core.monitor_thread.is_rdp_session", return_value=False), \
         patch.object(thread, "_init_com"), patch.object(thread, "_cleanup_com"), \
         patch.object(thread, "_poll_gpu_hybrid", MagicMock(return_value=poll_result)), \
         patch.object(thread, "_collect_power_stats"), \
         patch.object(thread, "_gpu_cards", MagicMock(return_value=cards)):
        thread.run()
    return got


TWO = (GpuCard((0, NV_LUID), "RTX", False, 3.0), GpuCard((0, IG_LUID), "UHD", True, 0.0))


def test_a_held_poll_sends_no_gpu_reading_no_verdict_and_keeps_the_list(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    got = _one_run_tick(t, mt.GpuPollResult(None, 2048.0, 8192.0, None, None, present=True, idle=True), TWO)
    assert "gpu" not in got and "gpu_cards" not in got
    assert "gpu_idle" not in got                   # its verdict comes from the same sliver of a sample
    assert got["vram_used"] == 2.0 and got["gpu_temp"] is None
    got = _one_run_tick(t, mt.GpuPollResult(40.0, 2048.0, 8192.0, None, None, present=True, idle=True), TWO)
    assert got["gpu"] == 40.0 and got["gpu_cards"] == TWO and got["gpu_idle"] is True


# --- H5: the hover card says when the dGPU is idle -------------------------------------------------

def _cards(t):
    return {c.luid[1]: c for c in t._gpu_cards(True, True)}


def test_listed_dgpu_is_idle_only_while_the_gate_blanks_it(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    for _ in range(3):
        _tick(t, clock, IGPU_BUSY)
    cards = _cards(t)
    assert cards[NV_LUID].idle is True and (cards[NV_LUID].temp, cards[NV_LUID].power) == (None, None)
    assert cards[IG_LUID].idle is None             # the gate says nothing about the iGPU
    for _ in range(2):
        _tick(t, clock, DGPU_BUSY)
    cards = _cards(t)
    assert cards[NV_LUID].idle is False and cards[NV_LUID].temp == 52.0


LHM_RTX = LhmSnapshot(gpus=(LhmGpu("/gpu-nvidia/0", RTX.name, 45.0, "t", 8.0, "p"),))


@pytest.mark.parametrize("picked", ["", RTX.name], ids=["automatic", "picked"])
def test_never_idle_next_to_a_live_lhm_reading(q_app, monkeypatch, clock, picked):
    """Review of #542: the gate only stops nvidia-smi - LHM still reads the dGPU, and "Idle" must not
    sit beside its 45 °C / 8 W."""
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    t.config["gpu_adapter"] = picked
    t._lhm_http.get_snapshot.return_value = LHM_RTX
    for _ in range(3):
        r = _tick(t, clock, IGPU_BUSY)
    assert t._gpu_sensor_asleep is True            # the gate has it idle...
    assert (r.temp, r.power) == (45.0, 8.0)        # ...but LHM reads it
    card = _cards(t)[NV_LUID]
    assert (card.temp, card.power) == (45.0, 8.0)
    assert card.idle is False                      # judged, so the row keeps its (hidden) tag's room
    if picked:
        assert r.idle is False


def test_a_failing_nvidia_smi_is_not_called_idle(q_app, monkeypatch, clock):
    """Its "—" are a failing sensor, not the gate: no tag."""
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    t._run_child.side_effect = subprocess.CalledProcessError(9, "nvidia-smi")
    for _ in range(2):
        _tick(t, clock, DGPU_BUSY)                 # awake: nvidia-smi asked, and failing
    assert t._nvidia_fail_streak > 0
    for _ in range(60):
        _tick(t, clock, IGPU_BUSY)
    assert t._nvidia_gate_open is False
    assert _cards(t)[NV_LUID].idle is False


def _blind_tick(t, clock):
    """A poll whose engine-utilization array read fails (util_arr None: the gate's blind path)."""
    dedicated = {f"luid_0x00000000_0x{l:08X}_phys_0": 100 * 1024.0 * 1024.0 for l in (IG_LUID, NV_LUID)}
    with patch("win32pdh.CollectQueryData"), \
         patch("win32pdh.GetFormattedCounterArray", side_effect=[RuntimeError("PDH"), dedicated, {}]), \
         patch.object(mt.psutil, "virtual_memory", return_value=types.SimpleNamespace(total=32 * 1024 ** 3)), \
         patch.object(StatsMonitorThread, "_init_ohm_wmi"):
        r = t._poll_gpu_hybrid(include_temp=True, include_power=True)
    clock.t += 1.0
    return r


def test_one_blind_poll_keeps_the_verdict_so_an_open_card_is_not_rebuilt(q_app, monkeypatch, clock):
    """Review of #542: a one-poll PDH array failure used to drop the verdict (key gone -> rebuild ->
    key back -> rebuild, the width changing twice). It is kept for ONE such poll."""
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    t._run_child = MagicMock(side_effect=AssertionError("no nvidia-smi in this test"))
    t._nvidia_retry_at = 10 ** 9                   # keep nvidia-smi out of the fail-open blind poll
    for _ in range(3):
        _tick(t, clock, IGPU_BUSY)
    assert _cards(t)[NV_LUID].idle is True
    _blind_tick(t, clock)
    assert _cards(t)[NV_LUID].idle is True         # kept over the one blind poll
    _tick(t, clock, IGPU_BUSY)
    assert _cards(t)[NV_LUID].idle is True
    _blind_tick(t, clock)
    _blind_tick(t, clock)                          # a lasting failure: no longer judged
    assert _cards(t)[NV_LUID].idle is None


def test_desktops_and_temperature_power_off_never_judge_it(q_app, monkeypatch, clock):
    desk = _thread([UHD, RTX, BRD], False, monkeypatch, mobile=False)
    _tick(desk, clock, IGPU_BUSY)
    assert {c.idle for c in desk._gpu_cards(True, True)} == {None}
    off = _thread([UHD, RTX, BRD], True, monkeypatch)
    for _ in range(3):
        _tick(off, clock, IGPU_BUSY, temp=False, power=False)
    assert {c.idle for c in off._gpu_cards(False, False)} == {None}


def test_a_picked_dgpu_carries_it_on_the_poll_result_automatic_never(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    t.config["gpu_adapter"] = RTX.name
    assert _tick(t, clock, IGPU_BUSY).idle is True
    _tick(t, clock, DGPU_BUSY)
    assert _tick(t, clock, DGPU_BUSY).idle is False
    t.config["gpu_adapter"] = UHD.name             # the iGPU picked: nothing is judged for it
    assert _tick(t, clock, IGPU_BUSY).idle is None
    auto = _thread([UHD, RTX, BRD], True, monkeypatch)
    assert _tick(auto, clock, IGPU_BUSY).idle is None


def test_the_poll_sends_the_verdict_and_the_controller_hands_it_to_the_view(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    got = _one_run_tick(t, mt.GpuPollResult(0.0, None, None, None, None, present=True, idle=True))
    assert got["gpu_idle"] is True
    from netspeedtray.core.controller import StatsController
    view = types.SimpleNamespace(gpu_idle=None)
    ctl = types.SimpleNamespace(view=view, _handle_network_counters=MagicMock(), config={},
                                widget_state=None, network_identity_updated=MagicMock())
    for name in ("cpu_usage_updated", "gpu_usage_updated", "cpu_temp_updated", "gpu_temp_updated",
                 "cpu_power_updated", "gpu_power_updated", "ram_info_updated", "vram_info_updated"):
        setattr(ctl, name, MagicMock())
    StatsController.handle_stats(ctl, {"gpu_idle": True, "gpu_present": True})
    assert view.gpu_idle is True
    StatsController.handle_stats(ctl, {"network": {}})       # a tick without it: kept
    assert view.gpu_idle is True


# --- the rows and the card ---------------------------------------------------------------------------

def test_rows_carry_it_only_with_temperature_or_power_on(q_app):
    from netspeedtray.views.widget.main import hover_gpu_rows
    cards = (TWO[0]._replace(idle=True), TWO[1])
    rows = hover_gpu_rows(dict(show_hardware_temps=True), cards)
    assert rows[0]["idle"] is True and "idle" not in rows[1]
    assert hover_gpu_rows(dict(show_hardware_power=True), (TWO[0]._replace(idle=False),))[0]["idle"] is False
    assert "idle" not in hover_gpu_rows(dict(monitor_vram_enabled=True), cards)[0]


def _tag_rows(card):
    return {k: not lbl.isHidden() for k, lbl in card._idle_tags.items()}


def test_card_shows_idle_beside_the_listed_dgpu_and_never_changes_width(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    idle = (TWO[0]._replace(idle=True, temp=None, power=None), TWO[1])
    card = UsageFlyout(EN, None, None, hardware=_hover_fake(idle, **ALL)._hover_hardware_snapshot())
    try:
        assert _tag_rows(card) == {("gpus", (0, NV_LUID)): True}
        tag = card._idle_tags[("gpus", (0, NV_LUID))]
        assert tag.text() == EN.GPU_IDLE_LABEL == "Idle"
        assert tag.toolTip() == EN.GPU_IDLE_TOOLTIP and "idle" in tag.toolTip()
        card.adjustSize()
        width, cell = card.sizeHint().width(), tag.parentWidget().sizeHint().width()
        awake = (TWO[0]._replace(idle=False, temp=61.0, power=40.0), TWO[1])
        assert card.update_hardware(_hover_fake(awake, **ALL)._hover_hardware_snapshot()) is True
        assert _tag_rows(card) == {("gpus", (0, NV_LUID)): False}
        card.adjustSize()
        # Its room is kept: the name cell - and so the card - never jumps on wake / idle.
        assert tag.parentWidget().sizeHint().width() == cell and card.sizeHint().width() == width
        assert card.update_hardware(_hover_fake(idle, **ALL)._hover_hardware_snapshot()) is True
        assert _tag_rows(card) == {("gpus", (0, NV_LUID)): True}
        # The card stops being judged (temperature / power off elsewhere, the iGPU picked): rebuilt.
        assert card.update_hardware(_hover_fake(TWO, **ALL)._hover_hardware_snapshot()) is False
    finally:
        card.deleteLater()


def test_no_tag_on_a_card_that_cannot_idle(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware=_hover_fake(TWO, **ALL)._hover_hardware_snapshot())
    try:
        assert card._idle_tags == {}
        from PyQt6.QtWidgets import QLabel
        assert EN.GPU_IDLE_LABEL not in [lbl.text() for lbl in card.findChildren(QLabel)]
    finally:
        card.deleteLater()


def _single_snapshot(idle, temps=True):
    cfg = dict(monitor_gpu_enabled=True, show_hardware_temps=temps, hardware_in_hover_card=True,
               widget_display_mode="side_by_side")
    fake = _hover_fake((), **cfg)
    fake.gpu_idle = idle
    return fake._hover_hardware_snapshot()


def test_single_gpu_row_says_idle_for_a_picked_idle_dgpu(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    snap = _single_snapshot(True)
    assert snap["gpu"]["idle"] is True
    assert "idle" not in _single_snapshot(None)["gpu"]
    assert "idle" not in _single_snapshot(True, temps=False)["gpu"]
    card = UsageFlyout(EN, None, None, hardware=snap)
    try:
        assert _tag_rows(card) == {("gpu",): True}
        assert card.update_hardware(_single_snapshot(False)) is True
        assert _tag_rows(card) == {("gpu",): False}
        assert card.update_hardware(_single_snapshot(None)) is False   # no longer judged: rebuilt
    finally:
        card.deleteLater()
