"""
HardwareFeed — owns the per-process CPU/RAM/GPU sampler for the Monitor's Hardware tab.

Same lifecycle shape as the Network tab's AppActivityFeed: a worker on its own QThread, polled only
while the Hardware tab is visible, degrading to an 'unavailable' signal under RDP (where psutil
performance data is unreliable and there's no GPU) without spawning a thread. On teardown it releases
the worker's PDH GPU query on the worker thread, then deletes the worker on its own loop.

Matplotlib-free: imports only Qt + the psutil/PDH worker + rdp_utils (lazy).
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from netspeedtray.views.monitor.hardware.worker import HardwareActivityWorker


class HardwareFeed(QObject):
    """Lifecycle wrapper around HardwareActivityWorker: poll while shown, idle while hidden."""

    payload_ready = pyqtSignal(object)
    unavailable = pyqtSignal(str)
    _request_sample = pyqtSignal()

    POLL_MS = 2000

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.logger = logging.getLogger("NetSpeedTray.HardwareFeed")
        self._thread: Optional[QThread] = None
        self._worker: Optional[HardwareActivityWorker] = None
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
        self._worker = HardwareActivityWorker()
        self._worker.moveToThread(self._thread)
        self._worker.data_ready.connect(self.payload_ready)
        self._worker.error.connect(lambda m: self.logger.debug("hardware feed worker error: %s", m))
        self._request_sample.connect(self._worker.sample)
        # On thread finish (during quit), release the PDH query then delete the worker — both on the
        # worker's own loop, so neither is lost to a stopped event loop.
        self._thread.finished.connect(self._worker.close_gpu_query)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def start(self) -> None:
        self._ensure_worker()
        if self._rdp:
            return
        if not self._timer.isActive():
            self._timer.start()
        self._request_sample.emit()

    def stop(self) -> None:
        self._timer.stop()

    def teardown(self) -> None:
        self.stop()
        try:
            if self._worker is not None:
                self._worker.data_ready.disconnect(self.payload_ready)
                self._request_sample.disconnect(self._worker.sample)
        except Exception:
            pass
        try:
            if self._thread is not None:
                self._thread.quit()
                if not self._thread.wait(800):
                    self._thread.wait()
        except Exception:
            pass
        try:
            if self._thread is not None:
                self._thread.deleteLater()
        except Exception:
            pass
        self._worker = None
        self._thread = None
