"""
Update Checker — checks for new releases via the GitHub Releases API.

Runs in a background thread to avoid blocking the UI. Emits Qt signals
when a result is available.
"""
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from netspeedtray import constants

logger = logging.getLogger(f"{constants.app.APP_NAME}.UpdateChecker")

RELEASES_URL = f"https://api.github.com/repos/{constants.app.GITHUB_OWNER}/{constants.app.GITHUB_REPO}/releases/latest"
CHECK_INTERVAL_HOURS = 24


def _parse_version(version_str: str) -> Tuple[int, ...]:
    """Parse 'v1.3.1' or '1.3.1' into a comparable tuple of ints."""
    cleaned = version_str.lstrip("vV").strip()
    parts = []
    for part in cleaned.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            break
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """Return True if latest version is strictly newer than current."""
    return _parse_version(latest) > _parse_version(current)


class _CheckWorker(QThread):
    """Background thread that hits the GitHub API."""
    finished = pyqtSignal(str, str)  # (latest_version, release_url)
    failed = pyqtSignal(str)         # error message

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                RELEASES_URL,
                headers={"Accept": "application/vnd.github.v3+json",
                         "User-Agent": f"NetSpeedTray/{constants.app.VERSION}"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            tag = data.get("tag_name", "")
            html_url = data.get("html_url", "")
            if tag:
                self.finished.emit(tag, html_url)
            else:
                self.failed.emit("No tag_name in response")
        except Exception as e:
            self.failed.emit(str(e))


class UpdateChecker(QObject):
    """
    Manages update checks. Owns the worker thread and emits signals
    that the UI layer can connect to.

    Signals:
        update_available(latest_version: str, release_url: str)
        up_to_date()
        check_failed(error: str)
    """
    update_available = pyqtSignal(str, str)  # (latest_version, release_url)
    up_to_date = pyqtSignal()
    check_failed = pyqtSignal(str)

    def __init__(self, config: dict, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.config = config
        self._worker: Optional[_CheckWorker] = None

    def should_check(self) -> bool:
        """Return True if enough time has passed since the last check."""
        if not self.config.get("check_for_updates", True):
            return False

        last_check = self.config.get("last_update_check")
        if not last_check:
            return True

        try:
            last_dt = datetime.fromisoformat(last_check)
            elapsed = datetime.now(timezone.utc) - last_dt
            return elapsed.total_seconds() > CHECK_INTERVAL_HOURS * 3600
        except (ValueError, TypeError):
            return True

    def check_now(self) -> None:
        """Start an async update check. Results arrive via signals."""
        if self._worker is not None and self._worker.isRunning():
            logger.debug("Update check already in progress, skipping.")
            return

        logger.info("Checking for updates...")
        self._worker = _CheckWorker(self)
        self._worker.finished.connect(self._on_result)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_result(self, latest_version: str, release_url: str) -> None:
        """Handle a successful API response."""
        self.config["last_update_check"] = datetime.now(timezone.utc).isoformat()
        current = constants.app.VERSION
        skipped = self.config.get("skipped_version")

        if is_newer(latest_version, current):
            # Don't notify if the user chose to skip this version
            if skipped and latest_version.lstrip("vV") == skipped.lstrip("vV"):
                logger.info("Update %s available but skipped by user.", latest_version)
                self.up_to_date.emit()
            else:
                logger.info("Update available: %s (current: %s)", latest_version, current)
                self.update_available.emit(latest_version, release_url)
        else:
            logger.info("Up to date (current: %s, latest: %s).", current, latest_version)
            self.up_to_date.emit()

    def _on_failed(self, error: str) -> None:
        """Handle a failed check."""
        logger.warning("Update check failed: %s", error)
        self.check_failed.emit(error)
