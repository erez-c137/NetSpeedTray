"""
The one-time "rate / star / support" card (2.2.2 D8) - the pure decision, no Qt.

House rule 3 (ROADMAP): at most one one-time moment, ever, dismissable forever. This is that moment.

- **The clock** is ``first_seen_at``, a UTC timestamp written by the first run of 2.2.2 or newer - for
  everyone, upgraders included. It is never back-filled from history rows: an upgrader with years of
  history counts from their first 2.2.2 run, so nobody gets the card on upgrade day.
- **The moment** is the user OPENING the Monitor at least ``SUPPORT_PROMPT_AFTER_DAYS`` after that -
  engagement, never a launch, never a pop-up. The card sits inline in the Monitor window.
- **Once, ever.** ``support_prompt_shown`` turns True when any of its buttons is pressed, or once the
  card has been on screen for ``SUPPORT_PROMPT_SEEN_MS`` while the Monitor was the active,
  un-minimized window (a user who closes the Monitor without answering has still seen it). A Monitor closed before that owes it to the next open.
- **Off by policy:** the same ``DisableTips`` policy as the first-run tip and "what's new" (DEPLOYING.md).
  Nothing is marked while the policy is on - it simply never shows.
- **Rate only where it works:** the Store accepts a rating only from a user who has the app in their
  Store library, so "Rate it in the Store" shows only on a Store install (installed_from_store);
  everyone else gets "Star on GitHub" alone.
- **A sane clock:** below SUPPORT_PROMPT_CLOCK_FLOOR nothing is written or due, and first_seen_at is
  never moved once written, so setting the clock back never shortens the wait.
- **Zero telemetry:** nothing records which button was pressed, and nothing is sent anywhere; a link
  opens only when clicked. Nothing in the app changes whichever button it was.
- Settings > Reset all keeps both keys (dialog.RESET_ALL_KEEPS).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional

from netspeedtray import constants

FIRST_SEEN_KEY = "first_seen_at"
SHOWN_KEY = "support_prompt_shown"
_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_ts(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime(_FORMAT)


def parse_ts(value: Any) -> Optional[datetime]:
    """'2026-11-18T09:30:00Z' -> an aware UTC datetime; None for anything else."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clock_floor() -> datetime:
    """No real "now" is earlier than this (the feature did not exist before). A clock below it is
    wrong (a dead CMOS battery, a VM restored from an old snapshot): nothing is written and nothing
    is due, so a backward clock can never shorten the 30 days."""
    return parse_ts(constants.app.SUPPORT_PROMPT_CLOCK_FLOOR)


def first_seen_updates(config: Mapping[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """The config change that starts the clock: ``{first_seen_at: now}`` when it is missing or
    unreadable, else nothing. It is never moved once written - not to an earlier "now" (a clock set
    back would shorten the wait), and not to a later one. A clock below the floor writes nothing."""
    now = now or now_utc()
    if now < clock_floor():
        return {}
    if parse_ts(config.get(FIRST_SEEN_KEY)) is None:
        return {FIRST_SEEN_KEY: format_ts(now)}
    return {}


def days_since_first_seen(config: Mapping[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    seen = parse_ts(config.get(FIRST_SEEN_KEY))
    if seen is None:
        return None
    return ((now or now_utc()) - seen).total_seconds() / 86400.0


def due(config: Mapping[str, Any], *, prompts_allowed: bool, now: Optional[datetime] = None) -> bool:
    """The card is owed at this Monitor open: never shown, not turned off by policy, the clock is
    sane (not below the floor), and at least SUPPORT_PROMPT_AFTER_DAYS since first_seen_at. The
    moment-of-open gates (another card up, a fullscreen app, the first minutes after launch) are the
    caller's."""
    now = now or now_utc()
    if not prompts_allowed or config.get(SHOWN_KEY, False) or now < clock_floor():
        return False
    days = days_since_first_seen(config, now)
    return days is not None and days >= constants.app.SUPPORT_PROMPT_AFTER_DAYS


def shown_updates() -> Dict[str, Any]:
    """The config change that marks the card done, forever."""
    return {SHOWN_KEY: True}


def store_review_url() -> str:
    return constants.app.STORE_REVIEW_URL_TEMPLATE.format(product_id=constants.app.STORE_PRODUCT_ID)


def store_web_url() -> str:
    return constants.app.STORE_WEB_URL_TEMPLATE.format(product_id=constants.app.STORE_PRODUCT_ID)


def github_repo_url() -> str:
    return f"https://github.com/{constants.app.GITHUB_OWNER}/{constants.app.GITHUB_REPO}"


def store_protocol_registered() -> bool:
    """The ``ms-windows-store:`` link has a handler (the Store app is installed). Some Windows
    editions (LTSC, Server, a removed Store) have none - then the Store's web page opens instead.
    Read-only; any failure reads as "not registered" (the web page always works). Where Group Policy
    turned the Store off, the handler is still registered and opens the Store's "blocked" page -
    acceptable: managed fleets turn this card off with DisableTips."""
    try:
        import winreg
    except ImportError:   # not Windows
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "ms-windows-store", 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, "URL Protocol")
        return True
    except OSError:
        return False


def installed_from_store() -> bool:
    r"""This copy was installed by the Microsoft Store: Setup ran with ``/STORE`` and recorded
    ``InstalledFrom = "Store"`` under ``HKLM\SOFTWARE\NetSpeedTray`` (HKCU is read too). Only such a
    user can rate: the Store lets a user rate only apps "that you have downloaded and are in your
    library", so a Setup.exe / winget / portable user is never sent to a page that refuses them.
    Activates only once the Store payload's installerParameters carry /STORE. Read-only; any failure
    reads as False (Star is still offered)."""
    try:
        import winreg
    except ImportError:   # not Windows
        return False
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, constants.app.INSTALL_SOURCE_REGISTRY_KEY, 0, winreg.KEY_READ) as key:
                value, value_type = winreg.QueryValueEx(key, constants.app.INSTALL_SOURCE_VALUE)
        except OSError:
            continue
        if value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and \
                str(value).strip().lower() == constants.app.INSTALL_SOURCE_STORE.lower():
            return True
    return False
