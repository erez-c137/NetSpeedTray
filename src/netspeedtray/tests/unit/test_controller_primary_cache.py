"""H1 regression: the primary-interface routing lookup must be cached, not run every poll."""
import types
import logging

from netspeedtray.core import controller as ctrl_mod
from netspeedtray.core.controller import StatsController


def _fake():
    s = types.SimpleNamespace()
    s.primary_interface = None
    s.last_primary_check_time = 0.0
    s.logger = logging.getLogger("test.ctrl")
    s._PRIMARY_REFRESH_SEC = StatsController._PRIMARY_REFRESH_SEC
    return s


def test_primary_lookup_is_cached_within_window(monkeypatch):
    calls = []
    monkeypatch.setattr(ctrl_mod, "get_primary_interface_name",
                        lambda: (calls.append(1), "Ethernet")[1])
    s = _fake()
    StatsController._update_primary_interface_name(s)  # first call resolves
    StatsController._update_primary_interface_name(s)  # within window -> cached
    StatsController._update_primary_interface_name(s)
    assert len(calls) == 1
    assert s.primary_interface == "Ethernet"


def test_primary_lookup_refreshes_after_window(monkeypatch):
    calls = []
    monkeypatch.setattr(ctrl_mod, "get_primary_interface_name",
                        lambda: (calls.append(1), "Ethernet")[1])
    s = _fake()
    StatsController._update_primary_interface_name(s)
    # Simulate the refresh window having elapsed.
    s.last_primary_check_time -= (StatsController._PRIMARY_REFRESH_SEC + 1)
    StatsController._update_primary_interface_name(s)
    assert len(calls) == 2
