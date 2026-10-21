"""
Unit tests for the StatsController class.

These tests verify the correctness of the network speed calculation,
interface filtering, and aggregation logic.
"""

import pytest
from unittest.mock import patch, MagicMock
import time

from netspeedtray import constants
from netspeedtray.core.controller import StatsController
from netspeedtray.core.widget_state import WidgetState


# A helper class to simulate psutil's snetio counter objects
class MockNetIO:
    """A simple mock for psutil._common.snetio."""
    def __init__(self, bytes_sent: int, bytes_recv: int):
        self.bytes_sent = bytes_sent
        self.bytes_recv = bytes_recv


@pytest.fixture
def mock_config() -> dict:
    """Provides a default configuration dictionary for tests."""
    config = constants.config.defaults.DEFAULT_CONFIG.copy()
    # Let's add a specific exclusion for one of our tests
    config["excluded_interfaces"].append("test_exclude")
    return config


@pytest.fixture
def mock_widget_state() -> MagicMock:
    """Provides a MagicMock of the WidgetState."""
    return MagicMock(spec=WidgetState)


@pytest.fixture
def controller_instance(mock_config: dict, mock_widget_state: MagicMock) -> StatsController:
    """Provides a fresh, configured NetworkController instance for each test."""
    return StatsController(config=mock_config, widget_state=mock_widget_state)


def test_update_speeds_calculates_and_emits_correctly(controller_instance, mock_widget_state):
    """
    Tests the core logic of update_speeds, ensuring it calculates speeds,
    respects exclusions, and sends correct data to the data and view layers.
    """
    # ARRANGE
    controller = controller_instance
    
    # Manually initialize the controller's internal state to simulate
    # that a "priming read" has already occurred. This is the key fix.
    controller.last_check_time = time.monotonic()
    controller.last_interface_counters = {
        "Wi-Fi": MockNetIO(bytes_sent=1000, bytes_recv=2000),
        "Ethernet": MockNetIO(bytes_sent=500, bytes_recv=800),
        "test_exclude": MockNetIO(bytes_sent=9999, bytes_recv=9999) 
    }
    controller.primary_interface = None # Ensure it uses the fallback aggregation logic

    # EXPLICITLY set the mode for this test case
    controller.config["interface_mode"] = "all_physical"

    # Define the network counters for the second read
    second_counters = {
        "Wi-Fi": MockNetIO(bytes_sent=3000, bytes_recv=6000),    # Diff: 2000 sent, 4000 recv
        "Ethernet": MockNetIO(bytes_sent=1100, bytes_recv=1800),  # Diff: 600 sent, 1000 recv
        "test_exclude": MockNetIO(bytes_sent=9999, bytes_recv=9999)
    }
    
    mock_view = MagicMock()
    controller.set_view(mock_view)

    # ACT
    # Simulate the passage of 2 seconds and the new psutil data
    with patch('time.monotonic', return_value=controller.last_check_time + 2.0):
        controller._handle_network_counters(second_counters)

    # ASSERT
    mock_widget_state.add_speed_data.assert_called_once()
    mock_view.update_display_speeds.assert_called_once()
    display_args = mock_view.update_display_speeds.call_args[0]
    
    # Total Upload B/s = (2000 + 600) / 2s = 1300
    # Total Upload Mbps = (1300 * 8) / 1,000,000 = 0.0104
    assert display_args[0] == pytest.approx(0.0104)
    # Total Download B/s = (4000 + 1000) / 2s = 2500
    # Total Download Mbps = (2500 * 8) / 1,000,000 = 0.02
    assert display_args[1] == pytest.approx(0.02)


def test_update_speeds_handles_resume_from_sleep(controller_instance, mock_widget_state):
    """
    Tests that a long time delta re-primes counters and emits zero speed.
    """
    # ARRANGE
    controller = controller_instance
    initial_counters = { "Wi-Fi": MockNetIO(bytes_sent=1000, bytes_recv=2000) }
    controller._handle_network_counters(initial_counters)

    second_counters = { "Wi-Fi": MockNetIO(bytes_sent=1500, bytes_recv=2500) }
    mock_view = MagicMock()
    controller.set_view(mock_view)
    
    # Store the time *before* the long sleep
    time_before_sleep = controller.last_check_time
    
    # ACT
    with patch('time.monotonic', return_value=time_before_sleep + 600.0):
        controller._handle_network_counters(second_counters)

    # ASSERT
    mock_widget_state.add_speed_data.assert_not_called()
    mock_view.update_display_speeds.assert_called_once_with(0.0, 0.0)
    # Assert the time has been updated to the new baseline
    assert controller.last_check_time == time_before_sleep + 600.0


def test_odometer_counts_bytes_even_when_all_rates_exceed_ceiling(controller_instance, mock_widget_state):
    """#13: a counter jump whose RATE exceeds MAX_REASONABLE_SPEED_BPS is display-dropped (continue),
    but the REAL bytes must still reach the data-cap odometer (add_usage_bytes), not be lost with the
    display. Previously the odometer feed was gated on the (now-empty) display data."""
    controller = controller_instance
    controller.config["interface_mode"] = "all_physical"
    controller.last_check_time = time.monotonic()
    controller.last_interface_counters = {"Wi-Fi": MockNetIO(bytes_sent=0, bytes_recv=0)}
    controller.primary_interface = None
    controller.set_view(MagicMock())
    # #13's real mechanism: the previous sample was HANDLED late, so this one arrives 1 s after it
    # although the counters were SAMPLED 3 s apart. (A literal 30 GiB in 1 s of sampling would be
    # 240 Gbps - a counter glitch, which the odometer's sanity cap now clips; see
    # test_odometer_accuracy.py.)
    controller._last_sample_time = (controller.last_check_time - 2.0, 10_000.0)

    huge = 30 * 1024**3  # 30 GiB over a 1 s handling span -> ~30 GB/s, above the ceiling -> display-dropped
    second = {"Wi-Fi": MockNetIO(bytes_sent=huge, bytes_recv=0)}
    with patch('time.monotonic', return_value=controller.last_check_time + 1.0):
        controller._handle_network_counters(second, sample_time=(controller.last_check_time + 1.0, 10_003.0))

    mock_widget_state.add_speed_data.assert_not_called()       # over-ceiling rate -> dropped from display
    mock_widget_state.add_usage_bytes.assert_called_once()     # ...but the real bytes WERE counted
    up_bytes, down_bytes = mock_widget_state.add_usage_bytes.call_args[0]
    assert up_bytes == huge and down_bytes == 0


def test_spike_filter_lets_sustained_ramp_through_after_one_poll(controller_instance):
    """#5: a one-off counter glitch is still capped on its first over-threshold poll, but a SUSTAINED
    jump passes through on the SECOND poll instead of being pinned at 2x baseline while the rolling
    average slowly catches up."""
    from collections import deque
    controller = controller_instance
    controller.config["interface_mode"] = "all_physical"
    controller.primary_interface = None
    view = MagicMock(); controller.set_view(view)
    name = "Ethernet"
    controller.recent_speeds[name] = deque([(2_000_000, 2_000_000)] * 10, maxlen=20)  # 2 MB/s baseline
    t0 = time.monotonic()
    controller.last_check_time = t0
    controller.last_interface_counters = {name: MockNetIO(bytes_sent=0, bytes_recv=0)}

    # Poll 1 of a sustained ramp to ~12 MB/s -> capped (first over-threshold poll looks like a glitch)
    with patch('time.monotonic', return_value=t0 + 1.0):
        controller._handle_network_counters({name: MockNetIO(bytes_sent=12_000_000, bytes_recv=12_000_000)})
    up1 = view.update_display_speeds.call_args[0][0]   # Mbps
    assert up1 < 50          # ~32 Mbps (2x baseline), NOT the real ~96 Mbps

    # Poll 2 of the SAME ramp -> not capped, the real value passes through
    with patch('time.monotonic', return_value=t0 + 2.0):
        controller._handle_network_counters({name: MockNetIO(bytes_sent=24_000_000, bytes_recv=24_000_000)})
    up2 = view.update_display_speeds.call_args[0][0]
    assert up2 > 80          # real ~96 Mbps now passes through


def test_aggregate_for_display_select_specific_mode(controller_instance):
    """
    Tests that the _aggregate_for_display method correctly sums only the
    user-selected interfaces when the monitoring mode is 'selected'.
    """
    # ARRANGE
    controller = controller_instance
    
    # Configure the controller to be in "selected" mode
    controller.config["interface_mode"] = "selected"
    controller.config["selected_interfaces"] = ["Wi-Fi", "VPN"]

    per_interface_speeds = {
        "Wi-Fi": (1000.0, 2000.0),
        "Ethernet": (5000.0, 8000.0), # Should be ignored
        "VPN": (100.0, 150.0),
        "Bluetooth": (5.0, 10.0)      # Should be ignored
    }
    
    # ACT
    agg_upload_bps, agg_download_bps = controller._aggregate_for_display(per_interface_speeds)
    
    # ASSERT
    # Expected Upload = 1000.0 (Wi-Fi) + 100.0 (VPN) = 1100.0 B/s
    assert agg_upload_bps == pytest.approx(1100.0)
    
    # Expected Download = 2000.0 (Wi-Fi) + 150.0 (VPN) = 2150.0 B/s
    assert agg_download_bps == pytest.approx(2150.0)


def test_update_speeds_handles_short_lag_spike(controller_instance, mock_widget_state):
    """
    Tests that a short but abnormal time delta (e.g. 4s on a 1s interval)
    is correctly identified as a lag spike and does not record a speed.
    This validates the fix for the phantom speed issue.
    """
    # ARRANGE
    controller = controller_instance
    controller.config["update_rate"] = 1.0
    
    # --- Manually prime the controller state ---
    # This simulates the application having been running normally before the lag spike.
    time_before_lag = time.monotonic()
    controller.last_check_time = time_before_lag
    controller.last_interface_counters = { "Wi-Fi": MockNetIO(bytes_sent=1000, bytes_recv=2000) }
    
    second_counters = { "Wi-Fi": MockNetIO(bytes_sent=10000, bytes_recv=20000) } # Large delta
    mock_view = MagicMock()
    controller.set_view(mock_view)
    
    # ACT: Simulate a 12-second lag, which is > max(10s, 1s * 5.0)
    with patch('time.monotonic', return_value=time_before_lag + 12.0):
        controller._handle_network_counters(second_counters)

    # ASSERT
    # With the new logic, no speed should be calculated or stored
    mock_widget_state.add_speed_data.assert_not_called()
    # The view should be reset to zero
    mock_view.update_display_speeds.assert_called_once_with(0.0, 0.0)
    # The baseline time should be updated to the new time
    assert controller.last_check_time == time_before_lag + 12.0


def test_temp_power_none_is_forwarded_to_clear_stale(controller_instance):
    """
    A dropped sensor (key present in stats, value None) must be forwarded to the
    view so a stale reading can be cleared to "(N/A)", instead of being swallowed
    and leaving the last good value frozen on screen.
    """
    controller = controller_instance
    mock_view = MagicMock()
    controller.set_view(mock_view)

    controller.handle_stats(
        {"cpu": 50.0, "cpu_temp": 45.0, "gpu": 30.0, "gpu_power": None}
    )

    mock_view.update_cpu_temp.assert_called_once_with(45.0)
    mock_view.update_gpu_power.assert_called_once_with(None)  # None forwarded, not dropped


def test_absent_temp_power_key_emits_nothing(controller_instance):
    """When a metric's key is absent (feature disabled), no signal is emitted for it."""
    controller = controller_instance
    mock_view = MagicMock()
    controller.set_view(mock_view)

    controller.handle_stats({"cpu": 50.0})  # no temp/power keys at all

    mock_view.update_cpu_temp.assert_not_called()
    mock_view.update_cpu_power.assert_not_called()
    mock_view.update_gpu_temp.assert_not_called()
    mock_view.update_gpu_power.assert_not_called()


# --- all_physical uses the shared adapter classifier (2.2 scope C, accuracy audit finding 5) -------

def _classifier_for(table):
    from netspeedtray.utils.interface_classifier import AdapterInfo, InterfaceClassifier
    rows = {n: AdapterInfo(n, d, t, hw) for n, (d, t, hw) in table.items()}
    return InterfaceClassifier(loader=lambda: rows)


_DEV_MACHINE = {   # this machine's live adapters, GetIfTable2 2026-10-19 (+ NordLynx)
    "Wi-Fi 3": ("Realtek 8851BU Wireless LAN WiFi 6 USB NIC", 71, True),
    "Ethernet": ("Intel(R) Ethernet Connection (17) I219-LM", 6, True),
    "Tailscale": ("Tailscale Tunnel", 53, False),
    "vEthernet (WSL (Hyper-V firewall))": ("Hyper-V Virtual Ethernet Adapter #2", 6, False),
    "Ethernet 3": ("VirtualBox Host-Only Ethernet Adapter", 6, False),
    "NordLynx": ("NordLynx Tunnel", 53, False),
    "Loopback Pseudo-Interface 1": ("Software Loopback Interface 1", 24, False),
}


def _run_all_physical(mock_config, mock_widget_state, table, first, second):
    controller = StatsController(config=mock_config, widget_state=mock_widget_state,
                                 interface_classifier=_classifier_for(table))
    controller.config["interface_mode"] = "all_physical"
    controller.primary_interface = None
    view = MagicMock()
    controller.set_view(view)
    controller.last_check_time = time.monotonic()
    controller.last_interface_counters = {n: MockNetIO(*v) for n, v in first.items()}
    with patch('time.monotonic', return_value=controller.last_check_time + 1.0):
        controller._handle_network_counters({n: MockNetIO(*v) for n, v in second.items()})
    return view.update_display_speeds.call_args[0], mock_widget_state.add_usage_bytes.call_args[0]


def test_all_physical_excludes_vpns_and_virtual_adapters(mock_config, mock_widget_state):
    """A full-tunnel VPN's bytes ride the Wi-Fi too; the 2.1 keyword list let Tailscale, NordLynx,
    vEthernet and a VirtualBox 'Ethernet 3' through, double-counting them (display AND odometer)."""
    names = list(_DEV_MACHINE)
    first = {n: (0, 0) for n in names}
    second = {n: (0, 0) for n in names}
    second["Wi-Fi 3"] = (125_000, 250_000)          # 1 / 2 Mbps over 1 s
    for n in ("Tailscale", "NordLynx", "vEthernet (WSL (Hyper-V firewall))", "Ethernet 3"):
        second[n] = (100_000, 200_000)              # each would add 0.8 / 1.6 Mbps
    display, usage = _run_all_physical(mock_config, mock_widget_state, _DEV_MACHINE, first, second)
    assert display[0] == pytest.approx(1.0)
    assert display[1] == pytest.approx(2.0)
    assert tuple(usage) == (125_000, 250_000)


def test_all_physical_with_no_physical_adapter_counts_the_rest(mock_config, mock_widget_state):
    """Inside a VM whose NIC the OS doesn't flag as hardware, the mode must not read 0 forever:
    with no physical adapter at all it sums every non-pseudo interface."""
    table = {"Ethernet 8": ("Acme Network Adapter", 6, False),
             "Loopback Pseudo-Interface 1": ("Software Loopback Interface 1", 24, False)}
    first = {"Ethernet 8": (0, 0), "Loopback Pseudo-Interface 1": (0, 0)}
    second = {"Ethernet 8": (125_000, 125_000), "Loopback Pseudo-Interface 1": (500_000, 500_000)}
    display, usage = _run_all_physical(mock_config, mock_widget_state, table, first, second)
    assert display[0] == pytest.approx(1.0)
    assert tuple(usage) == (125_000, 125_000)


def test_all_physical_fallback_never_reintroduces_a_vpn(mock_config, mock_widget_state):
    """No physical adapter (a VM NIC the OS doesn't flag) while a WireGuard tunnel and Tailscale are
    up: the fallback must sum the NIC only - the tunnels' bytes already ride it."""
    table = {"Ethernet 8": ("Acme Network Adapter", 6, False),
             "home": ("WireGuard Tunnel", 53, False),
             "Tailscale": ("Tailscale Tunnel", 53, False)}
    first = {n: (0, 0) for n in table}
    second = {"Ethernet 8": (125_000, 125_000), "home": (100_000, 100_000), "Tailscale": (100_000, 100_000)}
    display, usage = _run_all_physical(mock_config, mock_widget_state, table, first, second)
    assert display[0] == pytest.approx(1.0)
    assert tuple(usage) == (125_000, 125_000)


def test_available_interfaces_lists_vpns_but_not_pseudo(mock_config, mock_widget_state):
    """The picker now SHOWS VPNs (grouped as virtual) - selected mode can pick a tunnel - but still
    hides loopback / Teredo like 2.1 did."""
    controller = StatsController(config=mock_config, widget_state=mock_widget_state,
                                 interface_classifier=_classifier_for(_DEV_MACHINE))
    counters = {n: MockNetIO(0, 0) for n in _DEV_MACHINE}
    with patch("psutil.net_io_counters", return_value=counters):
        got = controller.get_available_interfaces()
    assert "Tailscale" in got and "NordLynx" in got and "Wi-Fi 3" in got
    assert "Loopback Pseudo-Interface 1" not in got

def test_current_primary_interface_resolves_in_any_mode(mock_config, mock_widget_state):
    """The picker marks the routing interface even when the mode isn't auto (where the per-poll
    lookup never runs)."""
    controller = StatsController(config=mock_config, widget_state=mock_widget_state)
    controller.config["interface_mode"] = "all_physical"
    with patch("netspeedtray.core.controller.get_primary_interface_name", return_value="Wi-Fi 3") as g:
        assert controller.current_primary_interface() == "Wi-Fi 3"
        assert controller.current_primary_interface() == "Wi-Fi 3"
    assert g.call_count == 1   # the 15 s cache applies
