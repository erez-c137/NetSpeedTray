"""
The one-time "rate / star / support" card (2.2.2 D8) - the pure decision, no Qt.

House rule 3 (ROADMAP): at most one one-time moment, ever, dismissable forever. This is that moment.

- **The clock** is ``first_seen_at``, a UTC timestamp written by the first run of 2.2.2 or newer - for
  everyone, upgraders included. It is never back-filled from history rows: an upgrader with years of
  history counts from their first 2.2.2 run, so nobody gets the card on upgrade day.
- **The moment** is the user OPENING the Monitor at least ``SUPPORT_PROMPT_AFTER_DAYS`` after that -
  engagement, never a launch, never a pop-up. The card sits inline in the Monitor window.
- **Once, ever.** ``support_prompt_shown`` turns True when any of its buttons is pressed, or once the
  card has been on screen for ``SUPPORT_PROMPT_SEEN_MS`` (a user who closes the Monitor without
  answering has still seen it). A Monitor closed before that owes it to the next open.
- **Off by policy:** the same ``DisableTips`` policy as the first-run tip and "what's new" (DEPLOYING.md).
  Nothing is marked while the policy is on - it simply never shows.
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


def first_seen_updates(config: Mapping[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """The config change that starts the clock: ``{first_seen_at: now}`` when it is missing or
    unreadable, else nothing. Also restarts it when it lies more than a day in the FUTURE (a clock
    that was wrong and got fixed), or the card could never come."""
    now = now or now_utc()
    seen = parse_ts(config.get(FIRST_SEEN_KEY))
    if seen is None or seen > now + timedelta(days=1):
        return {FIRST_SEEN_KEY: format_ts(now)}
    return {}


def days_since_first_seen(config: Mapping[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    seen = parse_ts(config.get(FIRST_SEEN_KEY))
    if seen is None:
        return None
    return ((now or now_utc()) - seen).total_seconds() / 86400.0


def due(config: Mapping[str, Any], *, prompts_allowed: bool, now: Optional[datetime] = None) -> bool:
    """The card is owed at this Monitor open: never shown, not turned off by policy, and at least
    SUPPORT_PROMPT_AFTER_DAYS since first_seen_at. The moment-of-open gates (another card up, a
    fullscreen app, the first minutes after launch) are the caller's."""
    if not prompts_allowed or config.get(SHOWN_KEY, False):
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
    Read-only; any failure reads as "not registered" (the web page always works)."""
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
