"""
Which one-time notice, if any, a launch owes the user (2.2.2 D1 and D2) - the pure decision, no Qt.

- **First-run tip (D1):** a brand-new install (config ``first_run_ever`` still True) is taught the three
  gestures once. ``first_run_ever`` stays True until the tip has really shown, so a tip deferred by a
  hidden widget, a fullscreen app or the sign-in rush is owed again next launch - deferred, not lost.
- **What's new (D2):** the first launch of a newer *final* release than ``last_seen_version`` shows one
  sentence (the release's own ``WHATS_NEW_<major>_<minor>_<patch>`` string) once.

Rules, all in :func:`plan_startup_notice`:

- Never both in one launch: a fresh install is the tip's run (and records the version), and a launch
  that owes the tip never shows "what's new".
- Not on a relaunch of the same version, nor on a downgrade: ``last_seen_version`` only ever moves up
  (the updater's own ordering, ``update_checker._parse_version``), so 2.2.3 -> 2.2.2 -> 2.2.3 shows
  2.2.3's notice at most once.
- **Prereleases never show it; they record their version.** A tester who ran 2.2.2-beta.1 has
  ``last_seen_version`` = "2.2.2-beta.1", and the final 2.2.2 orders above every prerelease of it, so
  the tester sees 2.2.2's notice exactly once - on the final. Beta-to-beta and beta relaunches show
  nothing, and the notice's text is the final's (it may change during the betas).
- A release with no ``WHATS_NEW_*`` string shows nothing (and records the version).
- The IT policy ``DisableTips`` (HKLM/HKCU ``SOFTWARE\\Policies\\NetSpeedTray``) shows neither: the
  versions are still recorded, and the tip is used up, so lifting the policy later never surfaces a
  stale "welcome" or "what's new".
- A config written by 2.2.1 or older has no ``last_seen_version``: an upgrader, owed 2.2.2's notice.
  A deleted / unreadable config (all defaults) reads as a fresh install: the tip once more, no
  "what's new". Settings > Reset all keeps both keys (dialog.RESET_ALL_KEEPS).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

from netspeedtray import constants
from netspeedtray.core.update_checker import _parse_version, policy_value_set

NOTICE_TIP = "first_run_tip"
NOTICE_WHATS_NEW = "whats_new"
WHATS_NEW_KEY_PREFIX = "WHATS_NEW_"


@dataclass(frozen=True)
class StartupPlan:
    """``notice``: NOTICE_TIP, NOTICE_WHATS_NEW or None. ``updates``: config changes to save now (at
    startup, whether or not a notice ever shows); a notice's own "seen" mark is saved only once it
    really shows (:func:`shown_updates`)."""
    notice: Optional[str] = None
    updates: Dict[str, Any] = field(default_factory=dict)


def tips_disabled_by_policy() -> bool:
    """IT turned the one-time tips off (``DisableTips``, a nonzero DWORD; DEPLOYING.md)."""
    return policy_value_set(constants.app.POLICY_DISABLE_TIPS_VALUE)


def release_core(version: str) -> Optional[str]:
    """'2.2.2-beta.1' -> '2.2.2'; None when unparseable."""
    parsed = _parse_version(version or "")
    if not parsed:
        return None
    return ".".join(str(n) for n in parsed[:3])


def is_prerelease(version: str) -> bool:
    parsed = _parse_version(version or "")
    return bool(parsed) and parsed[3] == 0


def whats_new_key(version: str) -> Optional[str]:
    """The i18n key holding this release's one sentence: '2.2.2' -> 'WHATS_NEW_2_2_2'."""
    core = release_core(version)
    return WHATS_NEW_KEY_PREFIX + core.replace(".", "_") if core else None


def display_version(version: str) -> str:
    return (version or "").lstrip("vV").strip()


def release_page_url(version: str) -> str:
    """The GitHub release page of exactly this version (the tags are 'v' + __version__)."""
    return constants.app.RELEASE_TAG_URL_TEMPLATE.format(
        owner=constants.app.GITHUB_OWNER, repo=constants.app.GITHUB_REPO, version=display_version(version))


def later_version(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """The higher of two versions by the updater's ordering (a final outranks its prereleases)."""
    if not a:
        return b
    if not b:
        return a
    return b if _parse_version(b) > _parse_version(a) else a


def plan_startup_notice(config: Mapping[str, Any], version: str, *,
                        has_string: Callable[[str], bool], prompts_allowed: bool) -> StartupPlan:
    """What this launch owes. ``has_string(key)``: the key exists (in en_US - the fallback every
    language reads). ``prompts_allowed``: not tips_disabled_by_policy()."""
    prev = config.get("last_seen_version")
    seen = later_version(prev, version)

    if config.get("first_run_ever", True):
        updates: Dict[str, Any] = {"first_run_v2_seen": True}   # a new user has no 2.0 "before"
        if seen != prev:
            updates["last_seen_version"] = seen
        if not prompts_allowed or config.get("first_run_tip_shown", False):
            updates["first_run_ever"] = False                    # used up: never surfaces later
            return StartupPlan(None, updates)
        return StartupPlan(NOTICE_TIP, updates)

    if not config.get("first_run_v2_seen", False):
        # A 1.x upgrade: this launch is the 2.0 welcome dialog's (main._maybe_show_welcome records the
        # version after it). Never two notices in one launch.
        return StartupPlan(None, {})

    if prev and _parse_version(version) <= _parse_version(prev):
        return StartupPlan(None, {})                             # a relaunch or a downgrade

    key = whats_new_key(version)
    if is_prerelease(version) or not prompts_allowed or not key or not has_string(key):
        return StartupPlan(None, {"last_seen_version": seen})
    return StartupPlan(NOTICE_WHATS_NEW, {})


def shown_updates(notice: str, config: Mapping[str, Any], version: str) -> Dict[str, Any]:
    """The config changes that mark ``notice`` as shown (saved the moment it really showed)."""
    if notice == NOTICE_TIP:
        return {"first_run_ever": False, "first_run_tip_shown": True}
    if notice == NOTICE_WHATS_NEW:
        return {"last_seen_version": later_version(config.get("last_seen_version"), version)}
    return {}
