"""
Pause actually freezes the live readout (the bug fix). The display is signal-driven from the
monitor thread, so pausing has to gate the update_* slots on `is_paused` — otherwise the numbers
keep moving. Exercised with a lightweight fake `self` (no live QWidget), the pattern used across
the widget tests.
"""
import types

from netspeedtray.views.widget.main import NetworkSpeedWidget


def _fake():
    f = types.SimpleNamespace()
    f.is_paused = False
    f.upload_speed = 0.0
    f.download_speed = 0.0
    f.cpu_usage = 0.0
    f.gpu_usage = 0.0
    f.config = {"widget_display_mode": "cycle"}
    f.update = lambda: None
    return f


def test_paused_freezes_network_readout():
    f = _fake()
    NetworkSpeedWidget.update_display_speeds(f, 10.0, 20.0)
    assert (f.upload_speed, f.download_speed) == (10.0, 20.0)

    f.is_paused = True
    NetworkSpeedWidget.update_display_speeds(f, 999.0, 888.0)  # ignored while paused
    assert (f.upload_speed, f.download_speed) == (10.0, 20.0)  # last numbers kept on screen


def test_paused_freezes_cpu_gpu_readout():
    f = _fake()
    f.is_paused = True
    NetworkSpeedWidget.update_cpu_usage(f, 55.0)
    NetworkSpeedWidget.update_gpu_usage(f, 77.0)
    assert f.cpu_usage == 0.0 and f.gpu_usage == 0.0  # frozen


def test_resume_restores_updates():
    f = _fake()
    f.is_paused = True
    NetworkSpeedWidget.update_display_speeds(f, 5.0, 6.0)
    assert (f.upload_speed, f.download_speed) == (0.0, 0.0)  # paused -> ignored

    f.is_paused = False
    NetworkSpeedWidget.update_display_speeds(f, 5.0, 6.0)
    assert (f.upload_speed, f.download_speed) == (5.0, 6.0)  # live again
