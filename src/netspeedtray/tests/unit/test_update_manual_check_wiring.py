"""
Regression: a manual "Check for updates" must not double-wire the update dialog, nor leave
one-shot replies behind for the next automatic check.

Bug 1: `update_available` is wired to `_on_update_available` once at widget construction
(persistent). `check_for_updates` (the tray-menu path) *also* connected a second, identical
handler, so a manual check fired both and the dialog appeared twice - the first started the
download, the second "popped back up" over it and raced the install/quit.

Bug 2 (#508 review): the manual path connected `up_to_date` / `check_failed` as SingleShot
connections. When the manual check FOUND an update, neither fired, so they stayed connected and
the next AUTOMATIC check consumed them - a surprise "Update check failed" / "You're up to date"
box. Now all three signals are wired once, permanently, and the boxes show only when the result
answers the menu item (`_manual_update_check`).

Drives the REAL `NetworkSpeedWidget.check_for_updates` against a minimal fake `self` (no heavy
widget construction) with mock signals.
"""
import types
from unittest.mock import MagicMock

from netspeedtray.views.widget.main import NetworkSpeedWidget


def _fake_widget():
    return types.SimpleNamespace(
        update_checker=MagicMock(),
        _manual_update_check=False,
    )


def test_manual_check_connects_nothing_and_arms_the_manual_flag():
    fake = _fake_widget()
    uc = fake.update_checker

    NetworkSpeedWidget.check_for_updates(fake)

    # Everything is wired once at construction; the manual path connects NOTHING.
    uc.update_available.connect.assert_not_called()
    uc.up_to_date.connect.assert_not_called()
    uc.check_failed.connect.assert_not_called()
    assert fake._manual_update_check is True
    uc.check_now.assert_called_once()


def test_manual_check_no_op_without_checker():
    """Guard: no update_checker (feature off / not yet built) is a safe no-op."""
    fake = types.SimpleNamespace(update_checker=None)
    NetworkSpeedWidget.check_for_updates(fake)  # must not raise
