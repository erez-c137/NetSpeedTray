"""
Regression tests for bugs fixed in v1.3.3.

Each test pins a specific defect so it cannot silently come back:
  * #131 — Auto-Cycling crash from a `constants.renderer.renderer.X` typo.
  * #154 — 10GbE NICs showing a constant 0 because real samples above the
           (unreliable) psutil per-NIC link speed were silently dropped.
  * #153 — Color/threshold edits not applying until restart because
           update_config() never rebuilt the cached pens.
"""
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray import constants
from netspeedtray.core.controller import StatsController
from netspeedtray.core.widget_state import WidgetState


class _MockNetIO:
    """Minimal stand-in for psutil._common.snetio."""
    def __init__(self, bytes_sent: int, bytes_recv: int):
        self.bytes_sent = bytes_sent
        self.bytes_recv = bytes_recv


@pytest.fixture
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller() -> StatsController:
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    return StatsController(config=config, widget_state=MagicMock(spec=WidgetState))


# --- #131: Auto-Cycling crash ------------------------------------------------

def test_cycle_interval_constant_resolves():
    """The constant the cycle timer reads must exist and be an int."""
    assert isinstance(constants.renderer.CYCLE_INTERVAL_MS, int)


def test_main_does_not_reintroduce_double_renderer_typo():
    """
    main.py must not reference `constants.renderer.renderer.*` — that typo raised
    AttributeError on every startup once 'cycle' display mode was saved (#131).
    """
    import netspeedtray
    main_path = os.path.join(
        os.path.dirname(netspeedtray.__file__), "views", "widget", "main.py"
    )
    src = open(main_path, encoding="utf-8").read()
    assert "constants.renderer.renderer" not in src
    assert "constants.renderer.CYCLE_INTERVAL_MS" in src


# --- #154: high-speed samples on multi-gigabit NICs --------------------------

def test_high_speed_sample_not_dropped_on_10gbe(controller):
    """
    A ~8 Gbit/s sample must be reported, not silently dropped. Previously the
    per-interface psutil link speed (often wrong/half on 10GbE) capped samples
    and dropped everything above it, yielding a constant 0.
    """
    controller.config["update_rate"] = 1.0
    controller.config["interface_mode"] = "selected"
    controller.config["selected_interfaces"] = ["Ethernet"]

    t0 = time.monotonic()
    controller.last_check_time = t0
    controller.last_interface_counters = {"Ethernet": _MockNetIO(bytes_sent=0, bytes_recv=0)}

    # 1e9 bytes over 1s == 8 Gbit/s of download.
    second = {"Ethernet": _MockNetIO(bytes_sent=0, bytes_recv=1_000_000_000)}
    mock_view = MagicMock()
    controller.set_view(mock_view)

    with patch("time.monotonic", return_value=t0 + 1.0):
        controller._handle_network_counters(second)

    mock_view.update_display_speeds.assert_called_once()
    down_mbps = mock_view.update_display_speeds.call_args[0][1]
    # 1e9 B/s * 8 / 1e6 == 8000 Mbps. Must not be dropped to 0.
    assert down_mbps == pytest.approx(8000.0, rel=0.01)


def test_absurd_speed_above_absolute_ceiling_still_dropped(controller):
    """The absolute MAX_REASONABLE_SPEED_BPS ceiling must still reject glitches."""
    controller.config["update_rate"] = 1.0
    controller.config["interface_mode"] = "selected"
    controller.config["selected_interfaces"] = ["Ethernet"]

    t0 = time.monotonic()
    controller.last_check_time = t0
    controller.last_interface_counters = {"Ethernet": _MockNetIO(bytes_sent=0, bytes_recv=0)}

    over_ceiling = constants.network.interface.MAX_REASONABLE_SPEED_BPS * 4
    second = {"Ethernet": _MockNetIO(bytes_sent=0, bytes_recv=over_ceiling)}
    mock_view = MagicMock()
    controller.set_view(mock_view)

    with patch("time.monotonic", return_value=t0 + 1.0):
        controller._handle_network_counters(second)

    down_mbps = mock_view.update_display_speeds.call_args[0][1]
    assert down_mbps == pytest.approx(0.0)


# --- #153: live-apply of color/threshold edits -------------------------------

def test_update_config_rebuilds_pen_cache(q_app):
    """
    update_config() must rebuild the cached pens so color edits apply live.
    Previously it re-derived self.*_color but left self._cached_pens stale, so
    the paint loop kept the old colors until the next restart.
    """
    from netspeedtray.utils.widget_renderer import WidgetRenderer

    cfg = constants.config.defaults.DEFAULT_CONFIG.copy()
    i18n = MagicMock()
    i18n.DECIMAL_SEPARATOR = "."
    renderer = WidgetRenderer(cfg, i18n)

    new_cfg = dict(cfg)
    new_cfg["high_speed_color"] = "#abcdef"
    new_cfg["low_speed_color"] = "#123456"
    renderer.update_config(new_cfg)

    assert renderer._cached_pens["high"].color().name() == "#abcdef"
    assert renderer._cached_pens["low"].color().name() == "#123456"
