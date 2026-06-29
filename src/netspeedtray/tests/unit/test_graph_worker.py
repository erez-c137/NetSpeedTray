from datetime import datetime
from unittest.mock import MagicMock

from netspeedtray.views.graph.worker import GraphDataWorker
from netspeedtray.views.graph.request import DataRequest


def _session_net_request(interface_name, seq):
    return DataRequest(
        start_time=datetime(2026, 1, 1, 0, 0, 0),
        end_time=datetime(2026, 1, 1, 1, 0, 0),
        interface_name=interface_name,
        is_session_view=True,
        sequence_id=seq,
        stat_type="network",
    )


def test_session_all_interfaces_uses_in_memory_aggregate(q_app):
    """Session view with no NIC filter keeps the fast all-interfaces in-memory path."""
    ws = MagicMock()
    ws.get_aggregated_speed_history.return_value = []
    GraphDataWorker(ws).process_data(_session_net_request(None, 1))
    ws.get_aggregated_speed_history.assert_called_once()
    ws.get_speed_history.assert_not_called()


def test_session_specific_nic_reads_per_interface_from_db(q_app):
    """Session view scoped to ONE NIC must read that interface from the DB (the in-memory aggregate is
    all-interfaces only), not silently show the aggregate — the 2.0 NIC-filter fix."""
    ws = MagicMock()
    ws.get_speed_history.return_value = []
    ws.get_total_bandwidth_for_period.return_value = (0.0, 0.0)
    GraphDataWorker(ws).process_data(_session_net_request("Ethernet", 1))
    ws.get_speed_history.assert_called_once()
    assert ws.get_speed_history.call_args.kwargs.get("interface_name") == "Ethernet"
    ws.get_aggregated_speed_history.assert_not_called()


def test_preserve_global_peaks_keeps_upload_and_download_extrema():
    data = []
    for i in range(100):
        up = float(i)
        down = float(100 - i)
        if i == 73:
            down = 1000.0
        data.append((float(i), up, down))

    sampled = data[::10]
    result = GraphDataWorker._preserve_global_peaks(data, sampled)

    assert (99.0, 99.0, 1.0) in result
    assert (73.0, 73.0, 1000.0) in result
    assert result == sorted(result, key=lambda point: point[0])
