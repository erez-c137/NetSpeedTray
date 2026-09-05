"""
The in-app updater's progress dialog must not cancel the update by being closed.

Found on release day of 2.1.5 (#296, and the same symptom in #260 for 2.1.3 -> 2.1.4): the download
ran to 100%, the progress window closed, and then nothing - no installer, no browser, no log line.
The cause is one line of ordering plus one Qt fact. `_on_verified` calls `_close_progress()` and
THEN checks `self._user_cancelled`; `_close_progress()` calls `QProgressDialog.close()`; and Qt's
QProgressDialog emits `canceled()` from its close event, not only from the Cancel button. So closing
the dialog ran `_on_cancel`, set the flag, and the next line silently deleted the verified installer
and finished. Every failure path logs; this one was the single silent path, which is why two bug
reports and one release went by without a trace in any log.

These tests drive `SecureUpdater` with a REAL QProgressDialog wired exactly as `start()` wires it,
because a mocked dialog cannot emit the signal that caused the bug.
"""
import os

import pytest
from PyQt6.QtWidgets import QProgressDialog, QWidget

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core import update_installer as ui


@pytest.fixture
def updater(q_app, tmp_path):
    parent = QWidget()
    u = ui.SecureUpdater(parent, "https://example.invalid/Setup.exe", "https://example.invalid/release",
                         I18nStrings("en_US"), latest_version="9.9.9")
    # The private download folder + the "verified" installer inside it, as after a real download.
    u._tmpdir = str(tmp_path / "dl")
    os.makedirs(u._tmpdir)
    u._dest = os.path.join(u._tmpdir, "NetSpeedTray-Setup.exe")
    with open(u._dest, "wb") as f:
        f.write(b"MZ" + b"\0" * 64)
    # The progress dialog exactly as start() creates and wires it.
    u._progress = QProgressDialog("Downloading", "Cancel", 0, 100, parent)
    u._progress.setAutoClose(False)
    u._progress.setAutoReset(False)
    u._progress.canceled.connect(u._on_cancel)
    u._progress.show()
    q_app.processEvents()
    u._active = True
    yield u
    parent.deleteLater()


def test_closing_the_progress_dialog_is_not_a_user_cancel(updater, q_app):
    updater._on_progress(100)
    updater._close_progress()
    q_app.processEvents()
    assert updater._user_cancelled is False


def test_a_verified_installer_is_launched_not_discarded(updater, monkeypatch, q_app):
    launched = []
    monkeypatch.setattr(ui, "launch_installer", lambda path, hwnd=0: launched.append(path))
    quit_requested = []
    updater.launching.connect(lambda: quit_requested.append(True))

    updater._on_progress(100)
    updater._on_verified(updater._dest, True, "ok")
    q_app.processEvents()

    assert launched == [updater._dest], "the verified installer must be launched"
    assert quit_requested, "the app must be told to quit for the installer"
    assert os.path.isfile(updater._dest), "the download must not be deleted on the success path"


def test_a_real_cancel_click_still_cancels(updater, monkeypatch, q_app):
    launched = []
    monkeypatch.setattr(ui, "launch_installer", lambda path, hwnd=0: launched.append(path))
    updater._progress.canceled.emit()     # what the Cancel button does (cancel() is the slot; the button emits)
    q_app.processEvents()
    assert updater._user_cancelled is True
    updater._on_verified(updater._dest, True, "ok")
    q_app.processEvents()
    assert launched == []
    assert not os.path.exists(updater._tmpdir), "a cancelled download is cleaned up"


def test_portable_staged_path_is_not_cancelled_by_the_dialog_closing(updater, monkeypatch, q_app):
    handed_off = []
    monkeypatch.setattr(ui.SecureUpdater, "_try_hands_off", lambda self, ready, app_dir: handed_off.append(ready) or True)
    monkeypatch.setattr(ui, "is_portable_install", lambda: True, raising=False)
    monkeypatch.setattr(ui.sys, "executable", os.path.join(updater._tmpdir, "NetSpeedTray.exe"), raising=False)
    updater._portable = True
    updater._on_progress(100)
    updater._on_staged(os.path.join(updater._tmpdir, "NetSpeedTray-9.9.9"))
    q_app.processEvents()
    assert updater._user_cancelled is False
    assert handed_off, "the staged portable update must proceed to the hands-off swap"


# ----------------------------------------------------------------------------- the elevated launch

def test_launch_runs_the_installer_elevated_and_silent(monkeypatch):
    """The installer is started through ShellExecuteEx(runas) with the Store/winget switches, so the
    UAC prompt is the app's own and the install is hands-off, ending in the installer relaunching us."""
    calls = []
    monkeypatch.setattr(ui, "_shell_execute_runas", lambda path, params, hwnd=0: calls.append((path, params, hwnd)))
    ui.launch_installer(r"C:\dl\Setup.exe", hwnd=42)
    assert calls == [(r"C:\dl\Setup.exe", "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART", 42)]


def test_a_declined_uac_prompt_keeps_the_app_and_says_so(updater, monkeypatch, q_app):
    """Declining UAC used to leave the user with no app and no message. Now: no quit, the download
    is dropped, and the existing fallback (message + release page) tells them what happened."""
    def declined(path, params, hwnd=0):
        raise ui.UpdateElevationDeclined("the UAC prompt was declined")
    monkeypatch.setattr(ui, "_shell_execute_runas", declined)
    fallbacks = []
    monkeypatch.setattr(updater, "_fallback", lambda reason: fallbacks.append(reason))
    quit_requested = []
    updater.launching.connect(lambda: quit_requested.append(True))

    updater._on_progress(100)
    updater._on_verified(updater._dest, True, "ok")
    q_app.processEvents()

    assert quit_requested == [], "the app must keep running when the prompt is declined"
    assert fallbacks and "declined" in fallbacks[0]
    assert not os.path.exists(updater._tmpdir), "the download is dropped; nothing is left half-done"
