"""
Unit tests for GraphLogic - the reusable graph-engine math the Monitor's GraphHost drives.

(The old GraphWindow's `_update_stats_bar` overlay tests were retired with the standalone window in
2.0; the Monitor surfaces stats in its tab header + Stats-detail sheet instead, and the worker's data
processing is covered by test_graph_worker.py.)
"""
import pytest

from netspeedtray.views.graph.logic import GraphLogic


def test_calculate_stats_preserves_real_peaks():
    """Peak stats should reflect true maxima from the timeline data."""
    history_data = [
        (1.0, 10.0, 20.0),
        (2.0, 20.0, 30.0),
        (3.0, 50_000_000.0, 100_000_000.0),
    ]

    stats = GraphLogic.calculate_stats(history_data)

    assert stats["max_up"] == pytest.approx(400.0)
    assert stats["max_down"] == pytest.approx(800.0)
