"""
Pause actually freezes the live readout (the bug fix). The display is signal-driven from the
monitor thread, so pausing has to gate the update_* slots on `is_paused` - otherwise the numbers
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


def test_paused_freezes_all_hardware_detail_slots():
    """Every update_* slot must freeze - temp/power/RAM/VRAM included (the partial-freeze fix)."""
    f = _fake()
    f.cpu_temp = f.gpu_temp = f.cpu_power = f.gpu_power = None
    f.ram_used = f.ram_total = f.vram_used = f.vram_total = 0.0
    f.is_paused = True
    NetworkSpeedWidget.update_cpu_temp(f, 70.0)
    NetworkSpeedWidget.update_gpu_temp(f, 65.0)
    NetworkSpeedWidget.update_cpu_power(f, 45.0)
    NetworkSpeedWidget.update_gpu_power(f, 120.0)
    NetworkSpeedWidget.update_ram_info(f, 8.0, 16.0)
    NetworkSpeedWidget.update_vram_info(f, 4.0, 8.0)
    assert f.cpu_temp is None and f.gpu_temp is None
    assert f.cpu_power is None and f.gpu_power is None
    assert f.ram_used == 0.0 and f.vram_used == 0.0  # all on their last values


def test_resume_restores_updates():
    f = _fake()
    f.is_paused = True
    NetworkSpeedWidget.update_display_speeds(f, 5.0, 6.0)
    assert (f.upload_speed, f.download_speed) == (0.0, 0.0)  # paused -> ignored

    f.is_paused = False
    NetworkSpeedWidget.update_display_speeds(f, 5.0, 6.0)
    assert (f.upload_speed, f.download_speed) == (5.0, 6.0)  # live again


def _graph_fake():
    """A fake whose history keeps recording, as the real widget_state does while paused."""
    f = types.SimpleNamespace(is_paused=False, _paused_histories=None, update=lambda: None,
                              logger=types.SimpleNamespace(info=lambda *a: None, debug=lambda *a: None))
    f.widget_state = types.SimpleNamespace(net=[1], cpu_history=[10], gpu_history=[20])
    f.widget_state.get_aggregated_speed_history = lambda: list(f.widget_state.net)
    # Pre-fix there is no snapshot helper: fall back to the live read the paint path used to make.
    live = getattr(NetworkSpeedWidget, "_live_graph_histories", None)
    f._live_graph_histories = (lambda: live(f)) if live else (lambda: (
        list(f.widget_state.net), list(f.widget_state.cpu_history), list(f.widget_state.gpu_history)))
    return f


def test_paused_freezes_the_mini_graph_too():
    """The numbers froze but any repaint (the Auto-Cycle tick, a hover) drew the live history: the
    graph scrolled on under frozen numbers. pause() snapshots it; resume() goes live again."""
    f = _graph_fake()
    graph = getattr(NetworkSpeedWidget, "_graph_histories", lambda self: self._live_graph_histories())
    NetworkSpeedWidget.pause(f)
    f.widget_state.net.append(2)
    f.widget_state.cpu_history.append(11)
    f.widget_state.gpu_history.append(21)
    assert graph(f) == ([1], [10], [20])
    NetworkSpeedWidget.resume(f)
    assert graph(f) == ([1, 2], [10, 11], [20, 21])
