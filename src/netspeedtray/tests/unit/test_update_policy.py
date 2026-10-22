"""
U1 (rc.2): IT's machine-wide policy to turn update checks off.

``HKLM\\SOFTWARE\\Policies\\NetSpeedTray`` ``DisableUpdateCheck`` (DWORD, nonzero = off), or the same
under HKCU. When set: no automatic check, no manual check (menu item hidden, check refused), and the
Settings toggle is locked with "Managed by your organization" - while the user's own setting is kept.

The registry is faked: the real HKLM Policies key needs admin to write, and a test must never touch
the machine's policy. The fake stands in for exactly the two winreg calls the reader makes.
"""
import types
import winreg
from unittest.mock import MagicMock

import pytest

from netspeedtray import constants
from netspeedtray.core import update_checker as uc


class _FakeKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def policy(monkeypatch):
    """policy(hklm={...}, hkcu={...}) installs a fake registry; a hive passed as None has no key."""
    def _install(hklm=None, hkcu=None):
        hives = {winreg.HKEY_LOCAL_MACHINE: hklm, winreg.HKEY_CURRENT_USER: hkcu}
        opened = []

        def open_key(hive, path, reserved=0, access=winreg.KEY_READ):
            opened.append((hive, path, access))
            assert access == winreg.KEY_READ, "the policy must be read-only"
            if path != constants.app.POLICY_REGISTRY_KEY or hives.get(hive) is None:
                raise FileNotFoundError(2, "no key")
            return _FakeKey(hives[hive])

        def query(key, name):
            if name not in key.values:
                raise FileNotFoundError(2, "no value")
            return key.values[name]

        monkeypatch.setattr(winreg, "OpenKey", open_key)
        monkeypatch.setattr(winreg, "QueryValueEx", query)
        return opened
    return _install


_NAME = "DisableUpdateCheck"


def test_constants_name_the_documented_key():
    assert constants.app.POLICY_REGISTRY_KEY == r"SOFTWARE\Policies\NetSpeedTray"
    assert constants.app.POLICY_DISABLE_UPDATE_CHECK_VALUE == _NAME


def test_hklm_dword_one_disables(policy):
    policy(hklm={_NAME: (1, winreg.REG_DWORD)})
    assert uc.update_check_disabled_by_policy() is True


def test_hkcu_policy_also_disables(policy):
    policy(hkcu={_NAME: (1, winreg.REG_DWORD)})
    assert uc.update_check_disabled_by_policy() is True


@pytest.mark.parametrize("hklm", [
    None,                                        # no key at all (every unmanaged machine)
    {},                                          # key without the value
    {_NAME: (0, winreg.REG_DWORD)},              # explicitly allowed
    {_NAME: ("1", winreg.REG_SZ)},               # wrong type is not a policy
])
def test_anything_else_is_no_policy(policy, hklm):
    policy(hklm=hklm)
    assert uc.update_check_disabled_by_policy() is False


def test_a_wrong_type_is_ignored_with_a_warning_once(policy, caplog):
    """REG_SZ "1" (a hand-written script) is not a policy - but IT must be told why it does nothing."""
    import logging
    uc._POLICY_TYPE_WARNED.clear()
    policy(hklm={_NAME: ("1", winreg.REG_SZ)})
    with caplog.at_level(logging.WARNING, logger=uc.logger.name):
        assert uc.update_check_disabled_by_policy() is False
        assert uc.update_check_disabled_by_policy() is False   # runs on every menu open
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and _NAME in r.getMessage()]
    assert len(warnings) == 1 and "REG_DWORD" in warnings[0].getMessage()


def test_hives_are_or_ed_hkcu_zero_cannot_re_enable(policy):
    """Disable-only: HKCU 0 does not override HKLM 1 (nor the other way round)."""
    policy(hklm={_NAME: (1, winreg.REG_DWORD)}, hkcu={_NAME: (0, winreg.REG_DWORD)})
    assert uc.update_check_disabled_by_policy() is True
    policy(hklm={_NAME: (0, winreg.REG_DWORD)}, hkcu={_NAME: (1, winreg.REG_DWORD)})
    assert uc.update_check_disabled_by_policy() is True


def test_a_read_error_is_no_policy(monkeypatch):
    def denied(*a, **k):
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(winreg, "OpenKey", denied)
    assert uc.update_check_disabled_by_policy() is False


# --------------------------------------------------------------------- the checker obeys it

def test_should_check_is_false_under_policy_even_when_the_user_allows_it(policy):
    policy(hklm={_NAME: (1, winreg.REG_DWORD)})
    checker = uc.UpdateChecker({"check_for_updates": True, "last_update_check": None})
    assert checker.should_check() is False


def test_should_check_unchanged_without_policy(policy):
    policy()
    assert uc.UpdateChecker({"check_for_updates": True}).should_check() is True


def test_check_now_never_starts_a_request_under_policy(q_app, policy, monkeypatch):
    policy(hklm={_NAME: (1, winreg.REG_DWORD)})
    worker_cls = MagicMock()
    monkeypatch.setattr(uc, "_CheckWorker", worker_cls)
    uc.UpdateChecker({}).check_now()
    worker_cls.assert_not_called()


def test_manual_menu_check_is_a_no_op_under_policy(policy):
    """The manual check obeys the policy (the per-user toggle does not): the machine must never
    contact GitHub. No one-shot replies are wired either."""
    from netspeedtray.views.widget.main import NetworkSpeedWidget
    policy(hklm={_NAME: (1, winreg.REG_DWORD)})
    fake = types.SimpleNamespace(update_checker=MagicMock(), _on_up_to_date_manual=lambda: None,
                                 _on_check_failed_manual=lambda e: None)
    NetworkSpeedWidget.check_for_updates(fake)
    fake.update_checker.check_now.assert_not_called()
    fake.update_checker.up_to_date.connect.assert_not_called()


# ------------------------------------------------------------------------- tray menu item

def _menu_fake(update_action):
    return types.SimpleNamespace(widget=types.SimpleNamespace(config={}), pause_action=None,
                                 reset_position_action=None, pause_separator=None,
                                 update_action=update_action, logger=MagicMock())


@pytest.mark.parametrize("hklm, visible", [({_NAME: (1, winreg.REG_DWORD)}, False), (None, True)])
def test_menu_item_hidden_only_under_policy(policy, hklm, visible):
    from netspeedtray.core.tray_manager import TrayIconManager
    policy(hklm=hklm)
    action = MagicMock()
    fake = _menu_fake(action)
    TrayIconManager._refresh_dynamic_items(fake)
    action.setVisible.assert_called_once_with(visible)
    fake.logger.error.assert_not_called()


# ----------------------------------------------------------------------- Settings toggle

@pytest.fixture
def general_page(q_app):
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.pages.general import GeneralPage

    def _build():
        return GeneralPage(I18nStrings("en_US"), MagicMock())
    return _build


def _shown_labels(widget):
    """Texts of the labels that would be on screen (isHidden: the page itself is never shown)."""
    from PyQt6.QtWidgets import QLabel
    return [lbl.text() for lbl in widget.findChildren(QLabel) if not lbl.isHidden()]


def test_settings_toggle_is_locked_and_explained_under_policy(policy, general_page):
    policy(hklm={_NAME: (1, winreg.REG_DWORD)})
    page = general_page()
    page.load_settings({"check_for_updates": True}, is_startup_enabled=False)
    assert not page.check_for_updates.isEnabled()
    assert not page.check_for_updates.isChecked(), "a locked toggle shows what actually happens: off"
    assert "Managed by your organization" in _shown_labels(page)
    assert page.check_for_updates.checkbox.accessibleDescription() == "Managed by your organization"
    # The user's own choice is written back untouched, so lifting the policy restores it.
    assert page.get_settings()["check_for_updates"] is True
    page.load_settings({"check_for_updates": False}, is_startup_enabled=False)
    assert page.get_settings()["check_for_updates"] is False


def test_settings_toggle_is_normal_without_policy(policy, general_page):
    policy()
    page = general_page()
    page.load_settings({"check_for_updates": True}, is_startup_enabled=False)
    assert page.check_for_updates.isEnabled()
    assert page.check_for_updates.isChecked()
    assert "Managed by your organization" not in _shown_labels(page)
    assert page.check_for_updates.checkbox.accessibleDescription() == ""


def test_the_cached_page_follows_the_policy_on_every_load(policy, general_page):
    """The Settings dialog is cached: load_settings (each re-open, Reset all) must re-read the
    policy, so removing it unlocks the toggle - and adding it locks it - without a restart."""
    policy(hklm={_NAME: (1, winreg.REG_DWORD)})
    page = general_page()
    page.load_settings({"check_for_updates": True}, is_startup_enabled=False)
    assert not page.check_for_updates.isEnabled()

    policy()                                                     # IT removes the policy
    page.load_settings({"check_for_updates": True}, is_startup_enabled=False)
    assert page.check_for_updates.isEnabled()
    assert page.check_for_updates.isChecked(), "the user's own setting comes back"
    assert page.check_for_updates.graphicsEffect() is None, "no longer dimmed"
    assert "Managed by your organization" not in _shown_labels(page)
    page.check_for_updates.setChecked(False)
    assert page.get_settings()["check_for_updates"] is False, "and the toggle is live again"

    policy(hklm={_NAME: (1, winreg.REG_DWORD)})                  # and pushes it again
    page.load_settings({"check_for_updates": True}, is_startup_enabled=False)
    assert not page.check_for_updates.isEnabled()
    assert "Managed by your organization" in _shown_labels(page)
