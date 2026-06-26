"""
Unit tests for cycle-mode interaction on NetworkSpeedWidget.

`_step_cycle` and `wheelEvent` are exercised as plain functions with a lightweight
fake `self`, so the full (expensive) widget never has to be constructed. This keeps
the index math and the scroll dispatch under test without a live QWidget.
"""
import types
from unittest.mock import MagicMock

from netspeedtray.views.widget.main import NetworkSpeedWidget


def _fake_widget(display_mode="cycle", cpu=True, gpu=True, index=0, timer_active=True):
    fake = types.SimpleNamespace()
    fake.config = {
        "widget_display_mode": display_mode,
        "monitor_cpu_enabled": cpu,
        "monitor_gpu_enabled": gpu,
    }
    fake._cycle_index = index
    fake._current_cycle_mode = "network_only"
    fake.update = MagicMock()
    fake._cycle_timer = MagicMock()
    fake._cycle_timer.isActive.return_value = timer_active
    return fake


def _fake_event(dy):
    ev = MagicMock()
    ev.angleDelta.return_value.y.return_value = dy
    return ev


# --- _step_cycle: the index math (network -> cpu -> gpu, wrapping) -------------

def test_step_cycle_advances_forward():
    fake = _fake_widget(index=0)
    NetworkSpeedWidget._step_cycle(fake, 1)
    assert fake._cycle_index == 1
    assert fake._current_cycle_mode == "cpu_only"
    fake.update.assert_called_once()


def test_step_cycle_wraps_forward_to_start():
    fake = _fake_widget(index=2)  # gpu_only -> wraps back to network_only
    NetworkSpeedWidget._step_cycle(fake, 1)
    assert fake._cycle_index == 0
    assert fake._current_cycle_mode == "network_only"


def test_step_cycle_backward_wraps_to_end():
    fake = _fake_widget(index=0)
    NetworkSpeedWidget._step_cycle(fake, -1)
    assert fake._cycle_index == 2
    assert fake._current_cycle_mode == "gpu_only"


def test_step_cycle_skips_disabled_metrics():
    fake = _fake_widget(cpu=True, gpu=False, index=0)  # only network + cpu
    NetworkSpeedWidget._step_cycle(fake, 1)
    assert fake._current_cycle_mode == "cpu_only"
    NetworkSpeedWidget._step_cycle(fake, 1)
    assert fake._current_cycle_mode == "network_only"  # wraps over the 2 modes


def test_step_cycle_single_metric_is_noop():
    fake = _fake_widget(cpu=False, gpu=False, index=0)  # network only
    NetworkSpeedWidget._step_cycle(fake, 1)
    assert fake._cycle_index == 0
    assert fake._current_cycle_mode == "network_only"


# --- wheelEvent: dispatch + auto-timer reset ----------------------------------

def test_wheel_scroll_down_steps_forward_and_resets_timer():
    fake = _fake_widget(timer_active=True)
    fake._step_cycle = MagicMock()
    ev = _fake_event(-120)  # scroll down
    NetworkSpeedWidget.wheelEvent(fake, ev)
    fake._step_cycle.assert_called_once_with(1)
    fake._cycle_timer.start.assert_called_once()
    ev.accept.assert_called_once()


def test_wheel_scroll_up_steps_backward():
    fake = _fake_widget()
    fake._step_cycle = MagicMock()
    ev = _fake_event(120)  # scroll up
    NetworkSpeedWidget.wheelEvent(fake, ev)
    fake._step_cycle.assert_called_once_with(-1)


def test_wheel_zero_delta_is_ignored():
    fake = _fake_widget()
    fake._step_cycle = MagicMock()
    ev = _fake_event(0)
    NetworkSpeedWidget.wheelEvent(fake, ev)
    fake._step_cycle.assert_not_called()
    ev.ignore.assert_called_once()


def test_wheel_does_not_restart_inactive_timer():
    fake = _fake_widget(timer_active=False)
    fake._step_cycle = MagicMock()
    ev = _fake_event(-120)
    NetworkSpeedWidget.wheelEvent(fake, ev)
    fake._cycle_timer.start.assert_not_called()
    ev.accept.assert_called_once()
