"""
The log has to stay readable, because it is the only evidence a bug report carries.

A support bundle for #260 arrived with **137,497 of its 139,638 lines - 98.5% - from one call**:
`get_primary_interface_name()` logging a WARNING every second while the reporter's Wi-Fi had no
route. That flood rotated away every genuinely useful entry, so the actual question (did the
portable update stage correctly?) was unanswerable from a bundle the reporter had gone to the
trouble of attaching.

Two independent faults produced it, and both are pinned here:

1. `StatsController._update_primary_interface_name` throttled the expensive lookup to once per
   15 s - but only `if self.primary_interface is not None`. With no route the lookup returns None,
   so the guard fell through and the blocking UDP connect ran on *every* poll. The cache stopped
   working in exactly the situation it existed for, which is also a real performance bug on the
   GUI-adjacent path, not just a logging one.
2. `get_primary_interface_name()` logged a warning per failed attempt. Having no route is a normal
   recurring *state* (offline, VPN reconnecting, resuming from sleep), not an event.
"""

import logging
import socket
import time
from unittest.mock import MagicMock, patch

import pytest

from netspeedtray.utils import network_utils as nu


@pytest.fixture(autouse=True)
def _reset_latch():
    """The unreachable latch is module state - each test starts clean."""
    nu._unreachable_since = None
    yield
    nu._unreachable_since = None


class _UnreachableSocket:
    """A socket whose connect() fails the way Windows fails with no route."""
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def settimeout(self, *a): pass
    def connect(self, *a): raise OSError("[WinError 10051] unreachable network")


def _run_offline(times):
    with patch("socket.socket", lambda *a, **k: _UnreachableSocket()):
        for _ in range(times):
            assert nu.get_primary_interface_name() is None


def test_repeated_failures_log_one_warning(caplog):
    """100 failed attempts must produce ONE warning, not 100."""
    with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.NetworkUtils"):
        _run_offline(100)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, f"expected 1 warning for 100 failures, got {len(warnings)}"


def test_repeats_drop_to_debug(caplog):
    """The repeats still exist for anyone who turns DEBUG on - they are just not in the file log."""
    with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.NetworkUtils"):
        _run_offline(5)
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG and "unreachable" in r.message]
    assert len(debugs) == 4, f"expected 4 DEBUG repeats after the first warning, got {len(debugs)}"


def test_at_info_level_a_long_outage_is_one_line(caplog):
    """The real-world check: at the file handler's INFO level, an outage costs a single line."""
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.NetworkUtils"):
        _run_offline(500)
    assert len(caplog.records) == 1, (
        f"500 failed attempts wrote {len(caplog.records)} lines at INFO; #260's bundle had 137,497"
    )


def test_recovery_is_logged_once_and_rearms(caplog):
    """Coming back logs once, and a later outage warns again rather than staying silent forever."""
    with caplog.at_level(logging.INFO, logger="NetSpeedTray.NetworkUtils"):
        _run_offline(3)
        nu._note_reachable()
        _run_offline(3)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(warns) == 2, "the second outage must warn again"
    assert len(infos) == 1 and "reachable again" in infos[0].message


def test_recovery_without_a_preceding_failure_is_silent(caplog):
    """The happy path must not log anything - it runs on every poll."""
    with caplog.at_level(logging.DEBUG, logger="NetSpeedTray.NetworkUtils"):
        nu._note_reachable()
    assert not [r for r in caplog.records if "reachable again" in r.message]


def test_the_lookup_is_throttled_even_when_it_fails():
    """The regression that caused the flood.

    A failing lookup must still arm the 15s cache. Previously the guard also required
    `primary_interface is not None`, so a None result meant the expensive blocking call ran on
    every single poll.
    """
    from netspeedtray.core.controller import StatsController

    c = StatsController.__new__(StatsController)
    c.logger = logging.getLogger("test")
    c.primary_interface = None
    c.last_primary_check_time = 0.0

    with patch("netspeedtray.core.controller.get_primary_interface_name", return_value=None) as look:
        c._update_primary_interface_name()          # first call: must run
        assert look.call_count == 1
        for _ in range(60):                          # a minute of polls, still offline
            c._update_primary_interface_name()
        assert look.call_count == 1, (
            f"the failing lookup ran {look.call_count} times in 60 polls - the throttle is not "
            f"applying when it returns None"
        )


def test_the_throttle_still_expires():
    """Throttling must not become 'never look again' - a NIC change has to be picked up."""
    from netspeedtray.core.controller import StatsController

    c = StatsController.__new__(StatsController)
    c.logger = logging.getLogger("test")
    c.primary_interface = None
    c.last_primary_check_time = 0.0

    with patch("netspeedtray.core.controller.get_primary_interface_name", return_value=None) as look:
        c._update_primary_interface_name()
        assert look.call_count == 1
        # Pretend the refresh window has passed.
        c.last_primary_check_time = time.monotonic() - (StatsController._PRIMARY_REFRESH_SEC + 1)
        c._update_primary_interface_name()
        assert look.call_count == 2, "the lookup must run again once the refresh window elapses"
