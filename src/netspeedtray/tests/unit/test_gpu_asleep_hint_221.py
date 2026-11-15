"""
2.2.1 GPU follow-ups to the Optimus gate (#500/#532/#537).

H4 - the first utilization sample after a GPU PDH query (re)build (Settings save, the Monitor window
flipping GPU collection for a VRAM-only user, resume) covers only the moment since the priming
collection: a near-zero window whose value is noise. That poll holds the readout (no 'gpu' key: the
taskbar keeps its last value, nothing is recorded) and the Optimus gate does not count it.

H5 - on a hybrid laptop the sleeping NVIDIA dGPU's temperature / power read a bare "—". The hover card
now says "Asleep" beside that GPU - only when the gate KNOWS it is asleep (judged this poll, nvidia-smi
healthy), never for a missing sensor. The taskbar widget is unchanged.
"""
import subprocess
import types
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import monitor_thread as mt
from netspeedtray.core.monitor_thread import GpuCard, StatsMonitorThread
from netspeedtray.tests.unit.test_optimus_nvidia_smi_wake import (  # noqa: F401 (clock: a fixture)
    BRD, DGPU_BUSY, IGPU_BUSY, NV_LUID, IG_LUID, RTX, UHD, _thread, _tick, clock)
from netspeedtray.tests.unit.test_hover_all_gpus_236 import ALL, _hover_fake

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


def test_a_vram_only_query_has_no_utilization_to_hold(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], False, monkeypatch, mobile=False)
    _rebuild(t)
    t._gpu_util_wanted = False
    with patch("win32pdh.OpenQuery", return_value=321), patch("win32pdh.AddCounter", return_value=5),          patch("win32pdh.CollectQueryData"):
        assert t._init_gpu_query()
    assert t._gpu_util_settling is False


def test_the_gate_does_not_count_the_settling_sample(q_app, monkeypatch, clock):
    """The settling window is a sliver: activity in it is not "the dGPU woke up", and no nvidia-smi
    call is made on it - the next, full sample decides."""
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    for _ in range(40):
        _tick(t, clock, IGPU_BUSY)                 # asleep, past any hold
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


def test_a_held_poll_sends_no_gpu_reading_and_keeps_the_list(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    got = _one_run_tick(t, mt.GpuPollResult(None, 2048.0, 8192.0, None, None, present=True), TWO)
    assert "gpu" not in got and "gpu_cards" not in got
    assert got["vram_used"] == 2.0 and got["gpu_temp"] is None
    got = _one_run_tick(t, mt.GpuPollResult(40.0, 2048.0, 8192.0, None, None, present=True), TWO)
    assert got["gpu"] == 40.0 and got["gpu_cards"] == TWO


# --- H5: the hover card says when the dGPU is asleep ---------------------------------------------

def _cards(t):
    return {c.luid[1]: c for c in t._gpu_cards(True, True)}


def test_listed_dgpu_is_asleep_only_while_the_gate_has_it_asleep(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    for _ in range(3):
        _tick(t, clock, IGPU_BUSY)
    cards = _cards(t)
    assert cards[NV_LUID].asleep is True and (cards[NV_LUID].temp, cards[NV_LUID].power) == (None, None)
    assert cards[IG_LUID].asleep is None           # the gate says nothing about the iGPU
    for _ in range(2):
        _tick(t, clock, DGPU_BUSY)
    cards = _cards(t)
    assert cards[NV_LUID].asleep is False and cards[NV_LUID].temp == 52.0


def test_a_failing_nvidia_smi_is_not_called_asleep(q_app, monkeypatch, clock):
    """Its "—" are a failing sensor, not the gate: no hint."""
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    t._run_child.side_effect = subprocess.CalledProcessError(9, "nvidia-smi")
    for _ in range(2):
        _tick(t, clock, DGPU_BUSY)                 # awake: nvidia-smi asked, and failing
    assert t._nvidia_fail_streak > 0
    for _ in range(60):
        _tick(t, clock, IGPU_BUSY)
    assert t._nvidia_gate_open is False
    assert _cards(t)[NV_LUID].asleep is False


def test_desktops_and_temperature_power_off_never_judge_it(q_app, monkeypatch, clock):
    desk = _thread([UHD, RTX, BRD], False, monkeypatch, mobile=False)
    _tick(desk, clock, IGPU_BUSY)
    assert {c.asleep for c in desk._gpu_cards(True, True)} == {None}
    off = _thread([UHD, RTX, BRD], True, monkeypatch)
    for _ in range(3):
        _tick(off, clock, IGPU_BUSY, temp=False, power=False)
    assert {c.asleep for c in off._gpu_cards(False, False)} == {None}


def test_a_picked_dgpu_carries_it_on_the_poll_result_automatic_never(q_app, monkeypatch, clock):
    t = _thread([UHD, RTX, BRD], True, monkeypatch)
    t.config["gpu_adapter"] = RTX.name
    assert _tick(t, clock, IGPU_BUSY).asleep is True
    _tick(t, clock, DGPU_BUSY)
    assert _tick(t, clock, DGPU_BUSY).asleep is False
    t.config["gpu_adapter"] = UHD.name             # the iGPU picked: nothing is judged for it
    assert _tick(t, clock, IGPU_BUSY).asleep is None
    auto = _thread([UHD, RTX, BRD], True, monkeypatch)
    assert _tick(auto, clock, IGPU_BUSY).asleep is None


def test_the_poll_sends_the_verdict_and_the_controller_hands_it_to_the_view(q_app):
    t = StatsMonitorThread(interval=0.1)
    t.logger = MagicMock()
    got = _one_run_tick(t, mt.GpuPollResult(0.0, None, None, None, None, present=True, asleep=True))
    assert got["gpu_asleep"] is True
    from netspeedtray.core.controller import StatsController
    view = types.SimpleNamespace(gpu_asleep=None)
    ctl = types.SimpleNamespace(view=view, _handle_network_counters=MagicMock(), config={},
                                widget_state=None, network_identity_updated=MagicMock())
    for name in ("cpu_usage_updated", "gpu_usage_updated", "cpu_temp_updated", "gpu_temp_updated",
                 "cpu_power_updated", "gpu_power_updated", "ram_info_updated", "vram_info_updated"):
        setattr(ctl, name, MagicMock())
    StatsController.handle_stats(ctl, {"gpu_asleep": True, "gpu_present": True})
    assert view.gpu_asleep is True
    StatsController.handle_stats(ctl, {"network": {}})       # a tick without it: kept
    assert view.gpu_asleep is True


# --- the rows and the card ---------------------------------------------------------------------------

def test_rows_carry_it_only_with_temperature_or_power_on(q_app):
    from netspeedtray.views.widget.main import hover_gpu_rows
    cards = (TWO[0]._replace(asleep=True), TWO[1])
    rows = hover_gpu_rows(dict(show_hardware_temps=True), cards)
    assert rows[0]["asleep"] is True and "asleep" not in rows[1]
    assert hover_gpu_rows(dict(show_hardware_power=True), (TWO[0]._replace(asleep=False),))[0]["asleep"] is False
    assert "asleep" not in hover_gpu_rows(dict(monitor_vram_enabled=True), cards)[0]


def _hint_rows(card):
    return {k: not lbl.isHidden() for k, lbl in card._asleep_hints.items()}


def test_card_shows_asleep_beside_the_listed_dgpu_and_never_changes_width(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    asleep = (TWO[0]._replace(asleep=True, temp=None, power=None), TWO[1])
    card = UsageFlyout(EN, None, None, hardware=_hover_fake(asleep, **ALL)._hover_hardware_snapshot())
    try:
        assert _hint_rows(card) == {("gpus", (0, NV_LUID)): True}
        assert card._asleep_hints[("gpus", (0, NV_LUID))].text() == EN.GPU_ASLEEP_LABEL == "Asleep"
        hint = card._asleep_hints[("gpus", (0, NV_LUID))]
        card.adjustSize()
        width, cell = card.sizeHint().width(), hint.parentWidget().sizeHint().width()
        awake = (TWO[0]._replace(asleep=False, temp=61.0, power=40.0), TWO[1])
        assert card.update_hardware(_hover_fake(awake, **ALL)._hover_hardware_snapshot()) is True
        assert _hint_rows(card) == {("gpus", (0, NV_LUID)): False}
        card.adjustSize()
        # Its room is kept: the name cell - and so the card - never jumps on wake / sleep.
        assert hint.parentWidget().sizeHint().width() == cell and card.sizeHint().width() == width
        assert card.update_hardware(_hover_fake(asleep, **ALL)._hover_hardware_snapshot()) is True
        assert _hint_rows(card) == {("gpus", (0, NV_LUID)): True}
        # The card stops being judged (temperature / power off elsewhere, the iGPU picked): rebuilt.
        assert card.update_hardware(_hover_fake(TWO, **ALL)._hover_hardware_snapshot()) is False
    finally:
        card.deleteLater()


def test_no_hint_on_a_card_that_cannot_sleep(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, None, None, hardware=_hover_fake(TWO, **ALL)._hover_hardware_snapshot())
    try:
        assert card._asleep_hints == {}
        from PyQt6.QtWidgets import QLabel
        assert EN.GPU_ASLEEP_LABEL not in [lbl.text() for lbl in card.findChildren(QLabel)]
    finally:
        card.deleteLater()


def _single_snapshot(asleep, temps=True):
    cfg = dict(monitor_gpu_enabled=True, show_hardware_temps=temps, hardware_in_hover_card=True,
               widget_display_mode="side_by_side")
    fake = _hover_fake((), **cfg)
    fake.gpu_asleep = asleep
    return fake._hover_hardware_snapshot()


def test_single_gpu_row_says_asleep_for_a_picked_sleeping_dgpu(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    snap = _single_snapshot(True)
    assert snap["gpu"]["asleep"] is True
    assert "asleep" not in _single_snapshot(None)["gpu"]
    assert "asleep" not in _single_snapshot(True, temps=False)["gpu"]
    card = UsageFlyout(EN, None, None, hardware=snap)
    try:
        assert _hint_rows(card) == {("gpu",): True}
        assert card.update_hardware(_single_snapshot(False)) is True
        assert _hint_rows(card) == {("gpu",): False}
        assert card.update_hardware(_single_snapshot(None)) is False   # no longer judged: rebuilt
    finally:
        card.deleteLater()
