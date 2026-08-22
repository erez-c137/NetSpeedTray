"""
Tray-menu keyboard accelerators must be unique within a menu, in every locale.

An `&` in a menu string marks the Alt-key accelerator. When two items in the SAME menu claim the
same letter, Qt stops activating either one: Alt+P cycles the highlight between them instead of
choosing, so the shortcut silently degrades into a no-op that needs Enter to finish.

This went unnoticed in 11 of 13 locales - including `en_US`, where "&Pause" and "Support this
&Project" both claimed Alt+P. The cause is systematic rather than careless: the word for "project"
begins with P in most European languages, so `SUPPORT_MENU_ITEM` collided with Pause (or, in
French, with "&Paramètres") almost everywhere it was translated.

Adding a locale or rewording a menu item is exactly when this regresses, which is why it is a test
and not a one-time cleanup.
"""

import json
import re
from pathlib import Path

import pytest

_LOCALES = Path(__file__).parents[3] / "netspeedtray" / "constants" / "locales"

# Items built into the one tray context menu by core/tray_manager.py.
_TRAY_MENU_KEYS = [
    "SETTINGS_MENU_ITEM",
    "SHOW_MONITOR_MENU_ITEM",
    "PAUSE_MENU_ITEM",
    "RESUME_MENU_ITEM",
    "CHECK_FOR_UPDATES_MENU_ITEM",
    "SUPPORT_MENU_ITEM",
    "EXIT_MENU_ITEM",
]

# Pause and Resume are the same QAction, relabeled - they can never be on screen together
# (tray_manager.update_pause_action), so they are allowed to share a letter.
_MUTUALLY_EXCLUSIVE = [{"PAUSE_MENU_ITEM", "RESUME_MENU_ITEM"}]

_ACCEL = re.compile(r"&([^\s&])")


def _locale_files():
    return sorted(_LOCALES.glob("*.json"))


def _accelerators(data):
    """key -> accelerator letter (upper-cased), for keys that declare one."""
    out = {}
    for key in _TRAY_MENU_KEYS:
        value = data.get(key)
        if not isinstance(value, str):
            continue
        m = _ACCEL.search(value)
        if m:
            out[key] = m.group(1).upper()
    return out


@pytest.mark.parametrize("path", _locale_files(), ids=lambda p: p.stem)
def test_tray_menu_accelerators_are_unique(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    accels = _accelerators(data)

    by_letter = {}
    for key, letter in accels.items():
        by_letter.setdefault(letter, set()).add(key)

    clashes = {
        letter: keys for letter, keys in by_letter.items()
        if len(keys) > 1 and not any(keys <= pair for pair in _MUTUALLY_EXCLUSIVE)
    }

    assert not clashes, (
        f"{path.stem}: two tray-menu items claim the same Alt key - "
        + "; ".join(f"Alt+{letter}: " + ", ".join(f"{k}={data[k]!r}" for k in sorted(keys))
                    for letter, keys in clashes.items())
    )


@pytest.mark.parametrize("path", _locale_files(), ids=lambda p: p.stem)
def test_accelerator_letter_appears_in_the_label(path):
    """An accelerator has to be a letter the user can actually see underlined.

    `&` immediately before a space, or at the end of a string, marks nothing and silently drops the
    shortcut.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for key in _TRAY_MENU_KEYS:
        value = data.get(key)
        if not isinstance(value, str) or "&" not in value:
            continue
        m = _ACCEL.search(value)
        if not m:
            problems.append(f"{key}={value!r} has a stray '&' marking no letter")
            continue
        if m.group(1) not in value.replace("&", ""):
            problems.append(f"{key}={value!r} marks {m.group(1)!r}, which is not in the label")
    assert not problems, f"{path.stem}: " + "; ".join(problems)


def test_english_is_covered_too():
    """en_US is a locale like any other here - its own Pause/Project clash is what made this
    a source-language bug rather than a translation one."""
    assert (_LOCALES / "en_US.json").exists()
    accels = _accelerators(json.loads((_LOCALES / "en_US.json").read_text(encoding="utf-8")))
    assert len(set(accels.values())) == len(accels)
