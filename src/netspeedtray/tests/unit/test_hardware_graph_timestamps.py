"""
The Monitor's Hardware graph must accept the timestamps its own data layer produces.

`WidgetState.get_hardware_history()` is typed `List[Tuple[datetime, float]]` and really does return
datetimes, but the hardware renderers built their arrays with `np.array(rows, dtype=float)`, which
cannot convert a datetime. Every hardware series therefore raised

    TypeError: float() argument must be a string or a real number, not 'datetime.datetime'

inside `GraphRenderer.render()`. `GraphHost._on_data_ready` catches everything and logs it, so the
Hardware tab just drew empty 0.0-1.0 axes - no error surfaced to the user. It shipped that way in
2.1.0, 2.1.1 and 2.1.2 before a smoke test caught the log line.

The speed-history path yields epoch floats instead, and both reach the same renderers, so the
coercion has to accept either.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from netspeedtray.views.graph.renderer import GraphRenderer


_BASE = datetime(2026, 8, 22, 2, 0, 0)


def _datetime_series(n=5):
    """Exactly what get_hardware_history() returns."""
    return [(_BASE + timedelta(seconds=i), float(i * 10)) for i in range(n)]


def _epoch_series(n=5):
    """What the speed-history path yields."""
    return [((_BASE + timedelta(seconds=i)).timestamp(), float(i * 10)) for i in range(n)]


def test_datetime_rows_are_accepted():
    """The regression: this raised TypeError before the fix."""
    arr = GraphRenderer._rows_as_floats(_datetime_series())
    assert arr.shape == (5, 2)
    assert arr.dtype == np.float64
    assert arr[0, 0] == pytest.approx(_BASE.timestamp())
    assert list(arr[:, 1]) == [0.0, 10.0, 20.0, 30.0, 40.0]


def test_epoch_rows_still_work():
    """The other caller must not regress."""
    arr = GraphRenderer._rows_as_floats(_epoch_series())
    assert arr.shape == (5, 2)
    assert arr[0, 0] == pytest.approx(_BASE.timestamp())


def test_both_shapes_agree():
    """A datetime series and the same instants as epochs must produce identical arrays."""
    np.testing.assert_allclose(
        GraphRenderer._rows_as_floats(_datetime_series()),
        GraphRenderer._rows_as_floats(_epoch_series()),
    )


def test_three_column_rows_survive():
    """The overview network series is (ts, up, down), not (ts, value)."""
    rows = [(_BASE + timedelta(seconds=i), float(i), float(i * 2)) for i in range(3)]
    arr = GraphRenderer._rows_as_floats(rows)
    assert arr.shape == (3, 3)
    assert list(arr[:, 2]) == [0.0, 2.0, 4.0]


def test_empty_series_is_empty_not_an_error():
    """Callers guard with `if not series` / `len(x) > 0`; an empty input must stay falsy, not raise."""
    arr = GraphRenderer._rows_as_floats([])
    assert len(arr) == 0


def test_raw_numpy_conversion_still_fails_on_datetimes():
    """Pins WHY the helper exists.

    If a future refactor drops back to `np.array(rows, dtype=float)`, this documents the failure it
    reintroduces - the bug was invisible precisely because the exception was swallowed downstream.
    """
    with pytest.raises(TypeError):
        np.array(_datetime_series(), dtype=float)
