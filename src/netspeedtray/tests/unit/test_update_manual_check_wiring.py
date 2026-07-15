"""
Regression: a manual "Check for updates" must not double-wire the update dialog.

The bug: `update_available` is wired to `_on_update_available` once at widget
construction (persistent). `check_for_updates` (the tray-menu path) *also* connected a
second, identical handler (`_on_update_available_manual`), so a manual check fired both
and the dialog appeared twice - the first started the download, the second "popped back
up" over it and raced the install/quit so the installer never visibly ran.

This drives the REAL `NetworkSpeedWidget.check_for_updates` against a minimal fake `self`
(no heavy widget construction) with mock signals, and asserts the manual path does NOT
add a second `update_available` receiver - it only wires the manual-only up-to-date /
failed messages that the silent automatic check omits.
"""
import types
from unittest.mock import MagicMock

from netspeedtray.views.widget.main import NetworkSpeedWidget


def _fake_widget():
    return types.SimpleNamespace(
        update_checker=MagicMock(),
        _on_up_to_date_manual=lambda: None,
        _on_check_failed_manual=lambda e: None,
    )


def test_manual_check_does_not_reconnect_update_available():
    fake = _fake_widget()
    uc = fake.update_checker

    NetworkSpeedWidget.check_for_updates(fake)

    # The regression: the manual path must NOT re-connect update_available (that second,
    # identical handler is what showed the dialog twice). The persistent connection made
    # once at construction is the only one.
    uc.update_available.connect.assert_not_called()
    # It DOES wire the manual-only messages, and kicks off the check.
    uc.up_to_date.connect.assert_called_once()
    uc.check_failed.connect.assert_called_once()
    uc.check_now.assert_called_once()


def test_manual_check_no_op_without_checker():
    """Guard: no update_checker (feature off / not yet built) is a safe no-op."""
    fake = types.SimpleNamespace(update_checker=None)
    NetworkSpeedWidget.check_for_updates(fake)  # must not raise
