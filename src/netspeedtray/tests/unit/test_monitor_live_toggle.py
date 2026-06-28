"""
Live/Pause for the Monitor graph. The shared GraphHost owns the one canonical "is live" flag (so the
Network + Hardware tabs stay in sync); the LiveToggle pill is a thin view bound to it.

Pausing must NEVER load matplotlib (it's the cheap path), and resuming must refresh once so the frozen
view jumps to "now". The pill must reflect a state change no matter which surface caused it.
"""
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _cfg(**over):
    c = dict(constants.config.defaults.DEFAULT_CONFIG)
    c.update(over)
    return c


def _host(live=True):
    from netspeedtray.views.monitor.graph_host import GraphHost
    mw = MagicMock()
    cfg = _cfg(live_update=live)
    mw.config = cfg
    mw.config_manager = MagicMock()
    return GraphHost(mw, cfg, I18nStrings("en_US")), cfg


# --- GraphHost contract ---------------------------------------------------------

def test_pause_persists_emits_and_never_loads_matplotlib(q_app):
    h, cfg = _host(live=True)
    assert h.is_live is True
    seen = []
    h.live_changed.connect(seen.append)

    h.set_live(False)
    assert h.is_live is False
    assert cfg["live_update"] is False        # persisted through the config handler
    assert seen == [False]
    assert h._loaded is False                  # the cheap path: pausing imports nothing

    h.set_live(False)                          # idempotent — no second emit
    assert seen == [False]


def test_resume_starts_realtime_and_catches_up(q_app):
    h, cfg = _host(live=False)
    calls = []
    h.start_realtime = lambda: calls.append("start")          # stub out the matplotlib-loading path
    h.update_graph = lambda show_loading=True: calls.append(("update", show_loading))
    seen = []
    h.live_changed.connect(seen.append)

    h.set_live(True)
    assert h.is_live is True
    assert cfg["live_update"] is True
    assert "start" in calls
    assert ("update", False) in calls          # jump to "now" instead of waiting a tick
    assert seen == [True]


# --- LiveToggle view binding ----------------------------------------------------

class _FakeHost(QObject):
    """Minimal stand-in exposing just the surface LiveToggle binds to."""
    live_changed = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._live = True
        self.calls = []

    @property
    def is_live(self) -> bool:
        return self._live

    def set_live(self, v) -> None:
        v = bool(v)
        if v == self._live:
            return
        self._live = v
        self.calls.append(v)
        self.live_changed.emit(v)


def test_pill_click_pauses_and_rerenders(q_app):
    from netspeedtray.views.monitor.live_toggle import LiveToggle
    host = _FakeHost()
    pill = LiveToggle(host, I18nStrings("en_US"))
    assert "Live" in pill.text()

    pill.click()                               # user presses -> freeze
    assert host.calls == [False]
    assert "Paused" in pill.text()             # re-rendered from the host's live_changed


def test_pill_syncs_to_external_change(q_app):
    from netspeedtray.views.monitor.live_toggle import LiveToggle
    host = _FakeHost()
    pill = LiveToggle(host, I18nStrings("en_US"))
    pill.click()                               # now paused
    assert "Paused" in pill.text()

    host.set_live(True)                        # the OTHER tab's pill resumed -> this one must follow
    assert "Live" in pill.text()
