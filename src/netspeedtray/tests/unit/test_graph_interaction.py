import logging

import numpy as np

from netspeedtray.views.graph.interaction import GraphInteractionHandler


def test_update_data_cache_preserves_bytes_per_second_values():
    """
    Regression test for tooltip unit inflation:
    interaction cache must keep raw bytes/sec values.
    """
    handler = GraphInteractionHandler.__new__(GraphInteractionHandler)
    handler.logger = logging.getLogger(__name__)
    handler._graph_x_cache = None
    handler._graph_data_ts_raw = None
    handler._graph_data_ups = None
    handler._graph_data_downs = None
    handler._blit_background = object()

    timestamps = np.array([1000.0, 1001.0, 1002.0], dtype=float)
    x_coords = np.array([0.0, 0.1, 0.2], dtype=float)
    upload_bytes_sec = np.array([12.5, 20.0, 30.0], dtype=float)
    download_bytes_sec = np.array([100.0, 120.0, 140.0], dtype=float)

    handler.update_data_cache(timestamps, upload_bytes_sec, download_bytes_sec, x_coords=x_coords)

    np.testing.assert_allclose(handler._graph_data_ups, upload_bytes_sec)
    np.testing.assert_allclose(handler._graph_data_downs, download_bytes_sec)
    np.testing.assert_allclose(handler._graph_x_cache, x_coords)
