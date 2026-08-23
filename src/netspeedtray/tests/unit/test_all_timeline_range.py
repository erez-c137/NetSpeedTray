"""
The "All" period has to span the data that exists, not a decade.

`HistoryPeriodConstants.get_start_time` falls back to `now - 10 years` for TIMELINE_ALL when it is
not told the earliest row in the database:

    if earliest_db:
         return earliest_db
    return now - timedelta(days=365*10)

That fallback is reasonable in isolation. What was not reasonable is that the Monitor's graph only
looked the earliest row up for **SYSTEM UPTIME**, so "All" always took the fallback: an axis running
from roughly 2016 with every real sample crushed into a sliver at the right edge. The one period
whose entire job is to show everything was the only one that showed nothing.

The Overview tab had always asked for both periods; `graph_host` had drifted from it.
"""

from datetime import datetime, timedelta

from netspeedtray import constants

hp = constants.data.history_period


def test_all_starts_at_the_earliest_row_when_told():
    now = datetime(2026, 8, 23, 12, 0, 0)
    earliest = datetime(2026, 6, 27, 20, 0, 0)
    assert hp.get_start_time("TIMELINE_ALL", now, earliest_db=earliest) == earliest


def test_all_without_the_earliest_row_spans_a_decade():
    """Pins the fallback that made this visible - and why it must not be the normal path."""
    now = datetime(2026, 8, 23, 12, 0, 0)
    start = hp.get_start_time("TIMELINE_ALL", now, earliest_db=None)
    assert start is not None
    assert (now - start).days >= 365 * 9, "the fallback is what produced the ~2016 axis"


def test_graph_host_requests_the_earliest_row_for_all():
    """The regression itself: the lookup must be gated on ALL as well as SYSTEM UPTIME.

    Asserted against the source because the alternative is standing up a Monitor window, a main
    widget and a live database to observe one boolean.
    """
    import inspect
    from netspeedtray.views.monitor import graph_host

    src = inspect.getsource(graph_host.GraphHost._time_range)
    assert "TIMELINE_ALL" in src, (
        "graph_host._time_range no longer asks for the earliest row on the All period - the axis "
        "will fall back to a ten-year span")
    assert "TIMELINE_SYSTEM_UPTIME" in src, "the uptime period must keep working too"


def test_the_lookup_latches_on_the_attempt_not_the_result():
    """An empty database returns None legitimately; retrying would be a UI-thread DB call per tick."""
    import inspect
    from netspeedtray.views.monitor import graph_host

    src = inspect.getsource(graph_host.GraphHost._time_range)
    assert "_earliest_db_fetched" in src, (
        "the lookup is gated on a cached VALUE rather than on whether it was attempted, so an empty "
        "database would re-query on every refresh and realtime tick")


def test_other_periods_are_unaffected():
    now = datetime(2026, 8, 23, 12, 0, 0)
    assert hp.get_start_time("TIMELINE_WEEK", now) == now - timedelta(days=7)
    assert hp.get_start_time("TIMELINE_MONTH", now) == now - timedelta(days=30)
    assert hp.get_start_time("TIMELINE_24_HOURS", now) == now - timedelta(days=1)
