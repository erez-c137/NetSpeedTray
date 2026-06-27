"""
AppActivityFeed — owns the per-app connection sampler for the Monitor's Network tab.

Reuses the App Activity window's AppActivityWorker BYTE-FOR-BYTE (a psutil connection sampler on its
own QThread) and presents it as a simple start/stop feed: it polls only while the Network tab is
visible and emits the worker's honest payload (live connections per app — Windows can't attribute
bytes to a process without admin/ETW, so this is counts + hosts, never estimated speed).

Graph-free + matplotlib-free: imports only Qt + the psutil worker, so it can't trip the Monitor's
import firewall.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from netspeedtray.views.app_activity.worker import AppActivityWorker


class AppActivityFeed(QObject):
    """Lifecycle wrapper around AppActivityWorker: poll while shown, idle while hidden."""

    payload_ready = pyqtSignal(object)   #: the worker's payload dict (rows + summary counts)
    unavailable = pyqtSignal(str)        #: emitted once when sampling can't run (e.g. "rdp")
    _request_sample = pyqtSignal()

    POLL_MS = 2000  # connections change slowly; 2s keeps the list calm and psutil cost low

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.logger = logging.getLogger("NetSpeedTray.AppActivityFeed")
        self._thread: Optional[QThread] = None
        self._worker: Optional[AppActivityWorker] = None
        self._rdp = False
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._request_sample)

    def _ensure_worker(self) -> None:
        if self._worker is not None or self._rdp:
            return
        from netspeedtray.utils.rdp_utils import is_rdp_session
        if is_rdp_session():
            self._rdp = True
            self.unavailable.emit("rdp")
            return
        self._thread = QThread()
        self._worker = AppActivityWorker()
        self._worker.moveToThread(self._thread)
        self._worker.data_ready.connect(self.payload_ready)
        self._worker.error.connect(lambda m: self.logger.debug("app feed worker error: %s", m))
        self._request_sample.connect(self._worker.sample)
        # Delete the worker on ITS OWN loop while the thread is still draining its event queue during
        # quit() — a deleteLater() posted after the loop has stopped would never be processed (the
        # worker, and its psutil name cache, would leak on every Monitor open/close).
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def start(self) -> None:
        self._ensure_worker()
        if self._rdp:
            return
        if not self._timer.isActive():
            self._timer.start()
        self._request_sample.emit()   # paint immediately, don't wait a full POLL_MS

    def stop(self) -> None:
        self._timer.stop()

    def teardown(self) -> None:
        """Stop polling, finish the worker thread, then release it. Idempotent."""
        self.stop()
        try:
            if self._worker is not None:
                self._worker.data_ready.disconnect(self.payload_ready)
                self._request_sample.disconnect(self._worker.sample)
        except Exception:
            pass
        try:
            if self._thread is not None:
                # quit() lets the loop process the finished->worker.deleteLater above before it stops.
                self._thread.quit()
                if not self._thread.wait(800):
                    self._thread.wait()
        except Exception:
            pass
        try:
            if self._thread is not None:
                self._thread.deleteLater()   # the worker was deleted on its own loop via finished
        except Exception:
            pass
        self._worker = None
        self._thread = None
