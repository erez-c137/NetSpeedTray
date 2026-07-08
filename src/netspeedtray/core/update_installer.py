"""
Secure one-click update: download the signed installer, verify it, run it.

Flow (any failure falls back to opening the GitHub release page in the browser, i.e.
the old behavior - so the worst case is never worse than before):

    download installer_url -> %TEMP%  (HTTPS, with a progress dialog)
      -> signature_verifier.verify_file()  (WinVerifyTrust + SignPath pin, fail-closed)
        -> launch the installer + quit the app   (Inno's AppMutex lets it replace us)

Both the download AND the (potentially network-blocking) signature verification run on
the worker thread, so the UI never freezes. The download host doesn't have to be
trusted: the Authenticode + publisher-pin gate is what authorizes execution.

Portable builds can't be updated by an installer (it targets Program Files, not the folder the user
unzipped), so in portable mode the same worker runs a guided flow instead (#195). A PyInstaller onedir
ZIP is many files, not one signed binary, so Authenticode on the bootstrap EXE alone would NOT vouch
for the _internal/ payload that actually runs. Instead the whole ZIP's SHA-256 is checked against the
release's published checksums.txt (fetched over HTTPS) - i.e. the download is exactly as trustworthy as
fetching that ZIP yourself from the release page. On a match it's extracted and staged in the user's
Downloads for them to copy over their folder. Settings live in %APPDATA%, so a folder replace never
touches them. (Installer-grade signing of the portable bundle is a tracked hardening follow-up.)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from typing import Callable, Optional

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from netspeedtray import constants
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


def _safe_extract(zip_path: str, dest_dir: str) -> None:
    """
    Extract ``zip_path`` into ``dest_dir``, refusing any member that would escape it (zip-slip).

    The download host is untrusted - the Authenticode gate on the *extracted* EXE is what authorizes
    the update - so a hostile archive must not be able to write a single byte outside the private temp
    directory during extraction.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest_root = os.path.realpath(dest_dir)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            target = os.path.realpath(os.path.join(dest_dir, member))
            if target != dest_root and not target.startswith(dest_root + os.sep):
                raise RuntimeError(f"unsafe path in archive: {member!r}")
        zf.extractall(dest_dir)


def _locate_portable_exe(root: str) -> str:
    """Return the path to the bundled ``<APP_NAME>.exe`` inside an extracted portable tree (raises if
    absent - a portable archive without the app EXE is not something we hand to the user)."""
    wanted = f"{constants.app.APP_NAME}.exe".lower()
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower() == wanted:
                return os.path.join(dirpath, name)
    raise RuntimeError(f"no {constants.app.APP_NAME}.exe in the portable archive")


def _downloads_dir() -> str:
    """The user's Downloads folder if it exists, else their home directory - a persistent, findable
    place to stage the verified new version (the temp dir gets swept on the next launch)."""
    home = os.path.expanduser("~")
    downloads = os.path.join(home, "Downloads")
    return downloads if os.path.isdir(downloads) else home


def _sha256_file(path: str) -> str:
    """Streaming SHA-256 of a file, as lowercase hex."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _fetch_checksums(url: str) -> str:
    """Download the release's checksums.txt over HTTPS (small; capped)."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(1_000_000).decode("utf-8", "replace")


def _expected_hash_for(checksums_text: str, filename: str) -> Optional[str]:
    """
    Return the lowercase SHA-256 listed for ``filename`` in a checksums.txt, or None if absent.

    The release publishes ``<HASH> <FILENAME>`` lines (uppercase hex from PowerShell Get-FileHash);
    match the filename case-insensitively and normalize the hash to lowercase.
    """
    fn = filename.lower()
    for line in checksums_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        token, name = parts[0].lower(), " ".join(parts[1:]).lower()
        if name == fn and len(token) == 64 and all(c in "0123456789abcdef" for c in token):
            return token
    return None


def _unique_dir(path: str) -> str:
    """
    Return a directory path that does not currently exist, so a subsequent ``shutil.move`` renames the
    source *to* it rather than nesting *inside* a surviving directory.

    Tries to clear ``path`` first (a stale staging folder from a previous run); if a locked file leaves
    it partly present, falls back to ``path-2``, ``path-3``, ... so we never move into a mixed folder.
    """
    if not os.path.exists(path):
        return path
    shutil.rmtree(path, ignore_errors=True)
    if not os.path.exists(path):
        return path
    i = 2
    while os.path.exists(f"{path}-{i}"):
        i += 1
    return f"{path}-{i}"


class _DownloadWorker(QObject):
    """
    Downloads, THEN verifies - ALL heavy I/O on the worker thread so the UI never blocks or freezes.

    Installer mode: the downloaded Setup.exe is Authenticode-verified (publisher-pinned) and the emitted
    path is that EXE; the caller launches it.

    Portable mode: a PyInstaller onedir ZIP is not one signed file, so instead of Authenticode the whole
    ZIP's SHA-256 is checked against the release's published checksums.txt - i.e. the download is exactly
    as trustworthy as fetching that ZIP yourself from the release page (#195). On a match the archive is
    extracted and the app folder is staged (moved) to its final Downloads location *here*, off the UI
    thread, and ``staged`` carries that final path; the caller only reveals it and shows instructions.
    """
    progress = pyqtSignal(int)
    verified = pyqtSignal(str, bool, str)  # installer path, trusted, reason
    staged = pyqtSignal(str)               # portable: final staged folder in Downloads
    failed = pyqtSignal(str)

    def __init__(self, url: str, dest: str, *, portable: bool = False,
                 extract_dir: Optional[str] = None, checksums_url: str = "",
                 expected_name: str = "", ready_target: str = "") -> None:
        super().__init__()
        self._url = url
        self._dest = dest
        self._portable = portable
        self._extract_dir = extract_dir
        self._checksums_url = checksums_url
        self._expected_name = expected_name
        self._ready_target = ready_target
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
        try:
            if self._portable:
                self._run_portable()
            else:
                # Verify on THIS (worker) thread - WinVerifyTrust can block on revocation I/O.
                result = verify_file(self._dest)
                self.verified.emit(self._dest, result.trusted, result.reason)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    def _run_portable(self) -> None:
        """Checksum-verify the whole ZIP against the release, then extract + stage. Runs on the worker
        thread; any problem raises and run() routes it to the browser fallback."""
        self.progress.emit(-1)  # flip the dialog to a busy indicator during verify/extract/stage
        if not self._checksums_url or not self._expected_name or not self._ready_target:
            raise RuntimeError("missing checksums reference for the portable update")
        checksums = _fetch_checksums(self._checksums_url)
        expected = _expected_hash_for(checksums, self._expected_name)
        if not expected:
            raise RuntimeError("no published checksum for the portable build")
        actual = _sha256_file(self._dest)
        if actual != expected:
            raise RuntimeError("checksum mismatch - the download may be corrupt or tampered")
        _safe_extract(self._dest, self._extract_dir or "")
        app_folder = os.path.dirname(_locate_portable_exe(self._extract_dir or ""))
        ready = _unique_dir(self._ready_target)
        shutil.move(app_folder, ready)   # move the verified tree out of the temp dir, off the UI thread
        self.staged.emit(ready)


class SecureUpdater(QObject):
    """
    Orchestrates download -> verify -> launch with a progress dialog and a browser
    fallback. Parented to the widget; self-destructs (deleteLater) when it finishes.

    Emits ``launching`` right before the verified installer starts, so the caller quits.
    """
    launching = pyqtSignal()

    def __init__(self, parent_widget: QWidget, installer_url: str, release_url: str, i18n,
                 *, portable: bool = False, portable_url: str = "", latest_version: str = "") -> None:
        super().__init__(parent_widget)
        self._parent = parent_widget
        self._installer_url = installer_url
        self._portable = portable
        self._portable_url = portable_url
        self._latest_version = latest_version
        self._release_url = release_url
        self.i18n = i18n
        self._thread: Optional[QThread] = None
        self._worker: Optional[_DownloadWorker] = None
        self._dest: Optional[str] = None
        self._tmpdir: Optional[str] = None
        self._extract_dir: Optional[str] = None
        self._progress: Optional[QProgressDialog] = None
        self._active = False
        self._user_cancelled = False

    def is_running(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:  # in-flight guard: no concurrent downloads
            return
        url = self._portable_url if self._portable else self._installer_url
        if not url:
            self._fallback("no portable asset in the release" if self._portable
                           else "no installer asset in the release")
            return
        try:
            # Download into a private per-run directory. mkdtemp creates it 0700
            # (owner-only) instead of the shared %TEMP% root, so the verified file
            # can't be swapped out from under us between verification and launch
            # (TOCTOU hardening - H5).
            sweep_stale_update_dirs()   # clear any orphaned dir from a previous successful update first
            self._tmpdir = tempfile.mkdtemp(prefix="NetSpeedTray-update-")
            if self._portable:
                self._dest = os.path.join(self._tmpdir, "NetSpeedTray-Portable.zip")
                self._extract_dir = os.path.join(self._tmpdir, "extracted")
            else:
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

        checksums_url = expected_name = ready_target = ""
        if self._portable:
            # checksums.txt is published next to the portable ZIP in the same release-download folder;
            # the expected filename is the ZIP's own basename as listed there.
            base, _, fname = self._portable_url.rpartition("/")
            checksums_url = f"{base}/checksums.txt" if base else ""
            expected_name = fname
            name = (f"{constants.app.APP_NAME}-{self._latest_version}"
                    if self._latest_version else f"{constants.app.APP_NAME}-update")
            ready_target = os.path.join(_downloads_dir(), name)

        self._thread = QThread(self)
        self._worker = _DownloadWorker(url, self._dest, portable=self._portable,
                                       extract_dir=self._extract_dir, checksums_url=checksums_url,
                                       expected_name=expected_name, ready_target=ready_target)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.verified.connect(self._on_verified)
        self._worker.staged.connect(self._on_staged)
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

    def _on_staged(self, ready: str) -> None:
        """
        Portable update: the ZIP's SHA-256 matched the release's checksums.txt and the new version was
        extracted + staged to ``ready`` (in Downloads) on the worker thread. Reveal it and tell the user
        to copy it over their current folder. Settings live in ``%APPDATA%``, not the app folder, so
        replacing the folder never touches them (#195).
        """
        self._teardown_thread()
        self._close_progress()
        self._cleanup_file()   # drop the temp zip + now-empty extract dir; `ready` is outside it
        if self._user_cancelled:
            # Staging already finished before the cancel landed; don't leave a surprise folder behind.
            try:
                shutil.rmtree(ready, ignore_errors=True)
            except Exception:
                pass
            self._finish()
            return
        try:
            os.startfile(ready)   # type: ignore[attr-defined]  # reveal in Explorer (Windows)
        except Exception:
            pass
        try:
            app_dir = os.path.dirname(os.path.abspath(sys.executable))
        except Exception:
            app_dir = ""
        try:
            title = getattr(self.i18n, "UPDATE_PORTABLE_READY_TITLE", "Update ready to install")
            msg = getattr(
                self.i18n, "UPDATE_PORTABLE_READY_MESSAGE",
                "NetSpeedTray {version} is ready in the folder that just opened:\n{ready}\n\n"
                "To finish updating: close NetSpeedTray, then copy everything from that folder into "
                "your current folder:\n{app_dir}\n(replacing the old files). Your settings are kept.")
            QMessageBox.information(
                self._parent, title,
                msg.format(version=self._latest_version or "", ready=ready, app_dir=app_dir))
        except Exception:
            pass
        self._finish()

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
