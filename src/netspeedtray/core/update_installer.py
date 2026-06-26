"""
Secure one-click update: download the signed installer, verify it, run it.

Flow (any failure falls back to opening the GitHub release page in the browser, i.e.
the old P1 behavior — so the worst case is never worse than before):

    download installer_url -> %TEMP%  (HTTPS, with a progress dialog)
      -> signature_verifier.verify_file()  (WinVerifyTrust + SignPath pin, fail-closed)
        -> launch the installer + quit the app   (Inno's AppMutex lets it replace us)

The download host doesn't have to be trusted: the Authenticode + publisher-pin gate
is what authorizes execution, so a redirected/tampered download is caught there.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import urllib.request
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from netspeedtray.utils.signature_verifier import verify_file

logger = logging.getLogger("NetSpeedTray.UpdateInstaller")

_USER_AGENT = "NetSpeedTray-Updater"
_CHUNK = 64 * 1024


def download_to(url: str, dest: str,
                progress_cb: Optional[Callable[[int], None]] = None,
                is_cancelled: Optional[Callable[[], bool]] = None) -> None:
    """
    Download `url` to `dest` over HTTPS, streaming in chunks. Calls progress_cb(pct)
    (0-100, or -1 when the size is unknown) and aborts if is_cancelled() turns True.
    Raises on any network/IO error or on cancellation.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        read = 0
        while True:
            if is_cancelled is not None and is_cancelled():
                raise RuntimeError("cancelled")
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            out.write(chunk)
            read += len(chunk)
            if progress_cb is not None:
                progress_cb(int(read * 100 / total) if total > 0 else -1)


def launch_installer(path: str) -> None:
    """Launch the (already-verified) installer. The app must then quit so Inno's
    AppMutex check passes and it can replace the running files."""
    subprocess.Popen([path], close_fds=True)


class _DownloadWorker(QObject):
    """Runs download_to on a QThread."""
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, url: str, dest: str) -> None:
        super().__init__()
        self._url = url
        self._dest = dest
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            download_to(self._url, self._dest,
                        progress_cb=lambda p: self.progress.emit(p),
                        is_cancelled=lambda: self._cancelled)
            self.finished.emit(self._dest)
        except Exception as e:  # noqa: BLE001 - report everything to the orchestrator
            self.failed.emit(str(e))


class SecureUpdater(QObject):
    """
    Orchestrates the download -> verify -> launch flow with a progress dialog and a
    browser fallback. Keep a reference alive while it runs (the parent widget holds it).

    Emits ``launching`` right before the verified installer is started, so the caller
    can quit the app.
    """
    launching = pyqtSignal()

    def __init__(self, parent_widget: QWidget, installer_url: str, release_url: str, i18n) -> None:
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._installer_url = installer_url
        self._release_url = release_url
        self.i18n = i18n
        self._thread: Optional[QThread] = None
        self._worker: Optional[_DownloadWorker] = None
        self._dest: Optional[str] = None
        self._progress: Optional[QProgressDialog] = None

    def start(self) -> None:
        if not self._installer_url:
            self._fallback("no installer asset in the release")
            return
        try:
            fd, self._dest = tempfile.mkstemp(suffix="-NetSpeedTray-Setup.exe")
            os.close(fd)
        except Exception as e:
            self._fallback(f"could not create a temp file: {e}")
            return

        title = getattr(self.i18n, "UPDATE_DOWNLOADING_TITLE", "Downloading update")
        cancel = getattr(self.i18n, "CANCEL_BUTTON", "Cancel")
        self._progress = QProgressDialog(title, cancel, 0, 100, self._parent)
        self._progress.setWindowTitle(title)
        self._progress.setMinimumWidth(360)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)
        self._progress.canceled.connect(self._on_cancel)

        self._thread = QThread(self)
        self._worker = _DownloadWorker(self._installer_url, self._dest)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_downloaded)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()
        self._progress.show()

    # --- worker callbacks ----------------------------------------------------
    def _on_progress(self, pct: int) -> None:
        if self._progress is None:
            return
        if pct < 0:
            self._progress.setRange(0, 0)  # indeterminate when size unknown
        else:
            self._progress.setValue(pct)

    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _on_downloaded(self, path: str) -> None:
        self._teardown_thread()
        result = verify_file(path)
        if not result.trusted:
            logger.warning("Downloaded update failed verification: %s", result.reason)
            self._cleanup_file()
            self._fallback(f"signature check failed: {result.reason}")
            return
        if self._progress is not None:
            self._progress.close()
        try:
            launch_installer(path)
            self.launching.emit()
        except Exception as e:  # noqa: BLE001
            logger.error("Could not launch installer: %s", e, exc_info=True)
            self._cleanup_file()
            self._fallback(f"could not start the installer: {e}")

    def _on_failed(self, reason: str) -> None:
        self._teardown_thread()
        self._cleanup_file()
        if self._progress is not None:
            self._progress.close()
        if reason == "cancelled":
            return  # user cancelled; do nothing
        self._fallback(reason)

    # --- helpers -------------------------------------------------------------
    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
        self._worker = None

    def _cleanup_file(self) -> None:
        if self._dest and os.path.isfile(self._dest):
            try:
                os.remove(self._dest)
            except OSError:
                pass

    def _fallback(self, reason: str) -> None:
        """Open the release page in the browser and tell the user why (non-fatal)."""
        logger.info("Falling back to the browser for update: %s", reason)
        try:
            msg = getattr(self.i18n, "UPDATE_FALLBACK_MESSAGE",
                          "Couldn't complete the in-app update. Opening the download page instead.")
            QMessageBox.information(self._parent, getattr(self.i18n, "UPDATE_AVAILABLE_TITLE", "Update"), msg)
        except Exception:
            pass
        try:
            import webbrowser
            webbrowser.open(self._release_url)
        except Exception:
            pass
