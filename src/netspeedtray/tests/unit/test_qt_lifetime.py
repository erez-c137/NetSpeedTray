"""
Parentless top-level windows the app drops must be destroyed by Qt on the GUI thread, never by
Python's cyclic GC - which runs on whatever thread allocates (stats QThread, metrics server,
janitor) and would delete a QWidget off the GUI thread (utils/qt_lifetime.py).
"""
import gc
import inspect
import threading
import weakref
from types import SimpleNamespace

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QPushButton, QWidget

from netspeedtray.utils.qt_lifetime import hand_to_qt


def _gc_on_a_worker_thread() -> None:
    t = threading.Thread(target=gc.collect, name="nst-test-worker")
    t.start()
    t.join(30)


def _run_deferred_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


class _Cyclic(QWidget):
    """A window whose wrapper is in a reference cycle - the common shape (self.x = self, a child
    holding a bound method of its window, ...)."""
    deleted_on = None

    def __init__(self):
        super().__init__()
        self.me = self
        self.button = QPushButton("x", self)

    def __del__(self):
        type(self).deleted_on = threading.current_thread().name


def test_without_the_handover_the_gc_destroys_a_window_on_a_worker_thread(q_app):
    """The hazard itself (control case)."""
    _Cyclic.deleted_on = None
    w = _Cyclic()
    w.show()
    del w
    _gc_on_a_worker_thread()
    assert _Cyclic.deleted_on == "nst-test-worker"


def test_a_handed_over_window_survives_the_gc_and_dies_by_qt(q_app):
    from netspeedtray.utils.qt_lifetime import kept_count
    before = kept_count()
    _Cyclic.deleted_on = None
    w = _Cyclic()
    w.show()
    hand_to_qt(w)
    assert kept_count() == before + 1
    ref = weakref.ref(w)
    button = w.button
    w.hide()
    w.deleteLater()
    del w
    _gc_on_a_worker_thread()
    assert _Cyclic.deleted_on is None and ref() is not None and not sip.isdeleted(button)
    _run_deferred_deletes()
    assert sip.isdeleted(button), "Qt destroyed it on the GUI thread"
    assert kept_count() == before, "the registry lets go once Qt has destroyed it"


def test_a_dropped_alert_flyout_is_only_destroyed_by_its_own_close(q_app):
    from netspeedtray.views.flyout import Flyout
    f = Flyout("t", "m", auto_dismiss_ms=0)
    f.show()
    probe = f.findChildren(QWidget)[0]
    del f
    _gc_on_a_worker_thread()
    assert not sip.isdeleted(probe), "a Python-owned flyout would have died on the worker thread"
    probe.window().close()                       # WA_DeleteOnClose
    _run_deferred_deletes()
    assert sip.isdeleted(probe)


def test_the_hover_card_is_handed_to_qt_before_it_is_dropped(q_app):
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.usage_flyout import UsageFlyout
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    card = UsageFlyout(I18nStrings("en_US"))
    card.show()
    probe = card.findChildren(QWidget)[0]
    stub = SimpleNamespace(_hover_card=card)
    del card
    NetworkSpeedWidget._hide_usage_hover_card(stub)
    _gc_on_a_worker_thread()
    assert stub._hover_card is None and not sip.isdeleted(probe)
    _run_deferred_deletes()
    assert sip.isdeleted(probe)


def test_replacing_an_alert_closes_the_previous_one(q_app):
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    import logging
    stub = SimpleNamespace(_usage_alert_flyout=None, logger=logging.getLogger("t"),
                           frameGeometry=lambda: __import__("PyQt6.QtCore", fromlist=["QRect"]).QRect(
                               100, 100, 50, 20))
    NetworkSpeedWidget._show_usage_alert(stub, "a", "1", auto_dismiss_ms=0)
    first = stub._usage_alert_flyout
    NetworkSpeedWidget._show_usage_alert(stub, "b", "2", auto_dismiss_ms=0)
    _run_deferred_deletes()
    assert sip.isdeleted(first) and not sip.isdeleted(stub._usage_alert_flyout)
    stub._usage_alert_flyout.close()
    _run_deferred_deletes()


def test_the_monitor_window_hands_itself_to_qt_when_parentless():
    from netspeedtray.views.monitor.window import MonitorWindow
    src = inspect.getsource(MonitorWindow.__init__)
    assert "WA_DeleteOnClose" in src and "hand_to_qt(self)" in src
