"""
Unit tests for UpdateChecker.should_check (the 24h interval gate) and _on_result
(notify / skip-version logic) - the same diagnostics-family code as the v1.3.2
updater regression, previously untested at the instance level. These drive the
methods directly (no network, no QThread); _on_result's outcome is checked via
mock slots connected to its signals (direct connections fire synchronously).
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from netspeedtray.core.update_checker import UpdateChecker, _CheckWorker, select_release_assets


def _iso_hours_ago(h: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


# --- should_check ------------------------------------------------------------

def test_should_check_false_when_disabled():
    assert UpdateChecker({"check_for_updates": False}).should_check() is False


def test_should_check_true_when_never_checked():
    assert UpdateChecker({}).should_check() is True


def test_should_check_true_after_interval():
    assert UpdateChecker({"last_update_check": _iso_hours_ago(25)}).should_check() is True


def test_should_check_false_within_interval():
    assert UpdateChecker({"last_update_check": _iso_hours_ago(1)}).should_check() is False


def test_should_check_true_on_malformed_timestamp():
    # A garbage / non-ISO timestamp must not crash - fall back to checking.
    assert UpdateChecker({"last_update_check": "not-a-date"}).should_check() is True


# --- _on_result --------------------------------------------------------------

def _spies(uc):
    avail, uptodate = MagicMock(), MagicMock()
    uc.update_available.connect(avail)
    uc.up_to_date.connect(uptodate)
    return avail, uptodate


def test_on_result_newer_notifies():
    uc = UpdateChecker({})
    avail, uptodate = _spies(uc)
    uc._on_result("99.0.0", "http://example/release")  # far newer than the current version
    avail.assert_called_once()
    uptodate.assert_not_called()


def test_on_result_skipped_version_suppressed_with_v_normalization():
    uc = UpdateChecker({"skipped_version": "99.0.0"})
    avail, uptodate = _spies(uc)
    uc._on_result("v99.0.0", "http://example/release")  # leading 'v' stripped to match skip
    avail.assert_not_called()
    uptodate.assert_called_once()


def test_on_result_not_newer_is_up_to_date():
    uc = UpdateChecker({})
    avail, uptodate = _spies(uc)
    uc._on_result("0.0.1", "http://example/release")  # older than the current version
    avail.assert_not_called()
    uptodate.assert_called_once()


# --- _CheckWorker fetch (mocked HTTP) ---------------------------------------
# The GitHub fetch was the v1.3.2 regression surface and was previously untested.
# run() is called directly (not via start()), so direct-connected slots fire in-thread.

_URLOPEN = "netspeedtray.core.update_checker.urllib.request.urlopen"


def _fake_resp(payload: bytes):
    """A context-manager stand-in for urlopen()'s return value."""
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = payload
    cm.__exit__.return_value = False
    return cm


def test_worker_emits_finished_with_body_and_assets():
    worker = _CheckWorker()
    finished, failed = MagicMock(), MagicMock()
    worker.finished.connect(finished)
    worker.failed.connect(failed)
    payload = json.dumps({
        "tag_name": "v2.0.0", "html_url": "http://example/rel", "body": "## What's new\n- stuff",
        "assets": [
            {"name": "NetSpeedTray-2.0.0-x64-Setup.exe", "browser_download_url": "http://dl/setup.exe"},
            {"name": "NetSpeedTray-Portable-2.0.0.zip", "browser_download_url": "http://dl/portable.zip"},
        ],
    }).encode()
    with patch(_URLOPEN, return_value=_fake_resp(payload)):
        worker.run()
    finished.assert_called_once_with(
        "v2.0.0", "http://example/rel", "## What's new\n- stuff",
        "http://dl/setup.exe", "http://dl/portable.zip",
    )
    failed.assert_not_called()


def test_select_release_assets_picks_installer_and_portable():
    assets = [
        {"name": "NetSpeedTray-2.0.0-x64-Setup.exe", "browser_download_url": "u1"},
        {"name": "NetSpeedTray-Portable-2.0.0.zip", "browser_download_url": "u2"},
        {"name": "checksums.txt", "browser_download_url": "u3"},
    ]
    assert select_release_assets(assets) == ("u1", "u2")
    assert select_release_assets([]) == ("", "")


def test_worker_emits_failed_when_no_tag():
    worker = _CheckWorker()
    finished, failed = MagicMock(), MagicMock()
    worker.finished.connect(finished)
    worker.failed.connect(failed)
    payload = json.dumps({"html_url": "x"}).encode()  # no tag_name
    with patch(_URLOPEN, return_value=_fake_resp(payload)):
        worker.run()
    finished.assert_not_called()
    failed.assert_called_once()


def test_worker_emits_failed_on_network_error():
    worker = _CheckWorker()
    failed = MagicMock()
    worker.failed.connect(failed)
    with patch(_URLOPEN, side_effect=OSError("boom")):
        worker.run()
    failed.assert_called_once()
    assert "boom" in failed.call_args[0][0]
