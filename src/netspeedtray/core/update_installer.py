"""
Secure one-click update: download the signed installer, verify it, run it.

Flow (any failure falls back to opening the GitHub release page in the browser, i.e.
the old behavior — so the worst case is never worse than before):

    download installer_url -> %TEMP%  (HTTPS, with a progress dialog)
      -> signature_verifier.verify_file()  (WinVerifyTrust + SignPath pin, fail-closed)
        -> launch the installer + quit the app   (Inno's AppMutex lets it replace us)

Both the download AND the (potentially network-blocking) signature verification run on
the worker thread, so the UI never freezes. The download host doesn't have to be
trusted: the Authenticode + publisher-pin gate is what authorizes execution.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import urllib.request
from typing import Callable, Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from netspeedtray.utils.signature_verifier import verify_file

logger = logging.getLogger("NetSpeedTray.UpdateInstaller")

_USER_AGENT = "NetSpeedTray-Updater"
_CHUNK = 64 * 1024
# Hard ceiling so a redirected/hostile download can't fill the disk before the
# signature gate runs (the real installer is well under this).
_MAX_BYTES = 250 * 1024 * 1024


def download_to(url: str, dest: str,
                progress_cb: Optional[Callable[[int], None]] = None,
                is_cancelled: Optional[Callable[[], bool]] = None) -> None:
    """
    Download `url` to `dest` over HTTPS, streaming in chunks. Calls progress_cb(pct)
    (0-100, or -1 when the size is unknown) and aborts if is_cancelled() turns True.
    Raises on any network/IO error, on cancellation, or if the size exceeds _MAX_BYTES.
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
            if read > _MAX_BYTES:
                raise RuntimeError("download exceeded maximum allowed size")
            if progress_cb is not None:
                progress_cb(int(read * 100 / total) if total > 0 else -1)


def sweep_stale_update_dirs() -> None:
    """Remove leftover ``NetSpeedTray-update-*`` temp directories from past in-app updates. The success
    path can't delete its own dir (the installer runs from it), so it orphans a ~10-30 MB Setup.exe;
    this clears any that are no longer in use (#19). Safe to call at startup and before a new download
    (a dir whose installer is still running is locked and simply skipped)."""
    import glob
    import shutil
    try:
        for d in glob.glob(os.path.join(tempfile.gettempdir(), "NetSpeedTray-update-*")):
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


def launch_installer(path: str) -> None:
    """Launch the (already-verified) installer. The app must then quit so Inno's
    AppMutex check passes and it can replace the running files."""
    subprocess.Popen([path], close_fds=True)


class _DownloadWorker(QObject):
    """Downloads, THEN verifies — both on the worker thread so the UI never blocks."""
    progress = pyqtSignal(int)
    verified = pyqtSignal(str, bool, str)  # path, trusted, reason
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
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
            return
        if self._cancelled:
            self.failed.emit("cancelled")
            return
        # Verify on THIS (worker) thread — WinVerifyTrust can block on revocation I/O.
        result = verify_file(self._dest)
        self.verified.emit(self._dest, result.trusted, result.reason)


class SecureUpdater(QObject):
    """
    Orchestrates download -> verify -> launch with a progress dialog and a browser
    fallback. Parented to the widget; self-destructs (deleteLater) when it finishes.

    Emits ``launching`` right before the verified installer starts, so the caller quits.
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
        self._tmpdir: Optional[str] = None
        self._progress: Optional[QProgressDialog] = None
        self._active = False
        self._user_cancelled = False

    def is_running(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:  # in-flight guard: no concurrent downloads
            return
        if not self._installer_url:
            self._fallback("no installer asset in the release")
            return
        try:
            # Download into a private per-run directory. mkdtemp creates it 0700
            # (owner-only) instead of the shared %TEMP% root, so the verified file
            # can't be swapped out from under us between verification and launch
            # (TOCTOU hardening — H5).
            sweep_stale_update_dirs()   # clear any orphaned dir from a previous successful update first
            self._tmpdir = tempfile.mkdtemp(prefix="NetSpeedTray-update-")
            self._dest = os.path.join(self._tmpdir, "NetSpeedTray-Setup.exe")
        except Exception as e:
            self._fallback(f"could not create a temp directory: {e}")
            return

        self._active = True
        title = getattr(self.i18n, "UPDATE_DOWNLOADING_TITLE", "Downloading update")
        cancel = getattr(self.i18n, "CANCEL_BUTTON", "Cancel")
        self._progress = QProgressDialog(title, cancel, 0, 100, self._parent)
        self._progress.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
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
        self._worker.verified.connect(self._on_verified)
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
        self._user_cancelled = True
        if self._worker is not None:
            self._worker.cancel()

    def _on_verified(self, path: str, trusted: bool, reason: str) -> None:
        self._teardown_thread()
        self._close_progress()
        if self._user_cancelled:
            self._cleanup_file()
            self._finish()
            return
        if not trusted:
            logger.warning("Downloaded update failed verification: %s", reason)
            self._cleanup_file()
            self._fallback(f"signature check failed: {reason}")
            return
        try:
            launch_installer(path)
            self.launching.emit()
            self._finish()
        except Exception as e:  # noqa: BLE001
            logger.error("Could not launch installer: %s", e, exc_info=True)
            self._cleanup_file()
            self._fallback(f"could not start the installer: {e}")

    def _on_failed(self, reason: str) -> None:
        self._teardown_thread()
        self._close_progress()
        self._cleanup_file()
        if reason == "cancelled":
            self._finish()
            return
        self._fallback(reason)

    # --- helpers -------------------------------------------------------------
    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
        self._worker = None

    def _close_progress(self) -> None:
        if self._progress is not None:
            self._progress.close()  # WA_DeleteOnClose -> destroyed
            self._progress = None

    def _cleanup_file(self) -> None:
        # Remove the whole private download directory (and the installer in it).
        if self._tmpdir and os.path.isdir(self._tmpdir):
            import shutil
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        elif self._dest and os.path.isfile(self._dest):
            try:
                os.remove(self._dest)
            except OSError:
                pass

    def _finish(self) -> None:
        """Terminal cleanup: mark idle and release this one-shot updater."""
        self._active = False
        self.deleteLater()

    def _fallback(self, reason: str) -> None:
        """Open the release page in the browser and tell the user why (non-fatal)."""
        logger.info("Falling back to the browser for update: %s", reason)
        self._close_progress()
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
        self._finish()
