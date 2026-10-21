"""
Locale checks added by the 2.2 release-candidate checkup (I1, I2): number separators and keyboard
accelerators, in every shipped locale.
"""
import json
import re
from pathlib import Path

import pytest

LOCALES = Path(__file__).resolve().parents[2] / "constants" / "locales"
FILES = sorted(LOCALES.glob("*.json"))

# Strings that sit side by side in ONE menu / button row, so their &-accelerators must differ.
# The tray menu shows Pause OR Resume (the same slot), so each is checked against the rest separately.
_MENU_COMMON = ["SETTINGS_MENU_ITEM", "SHOW_MONITOR_MENU_ITEM", "CHECK_FOR_UPDATES_MENU_ITEM",
                "SUPPORT_MENU_ITEM", "EXIT_MENU_ITEM"]
GROUPS = {
    "tray menu (paused off)": _MENU_COMMON + ["PAUSE_MENU_ITEM"],
    "tray menu (paused on)": _MENU_COMMON + ["RESUME_MENU_ITEM"],
    "Settings footer": ["SAVE_BUTTON", "CANCEL_BUTTON", "EXPORT_SUPPORT_BUNDLE_BUTTON"],
}


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _mnemonic(text):
    m = re.search(r"&([^&\s])", (text or "").replace("&&", ""))
    return m.group(1).lower() if m else None


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_thousands_and_decimal_separators_differ(path):
    """I1: tr_TR used "," for both, so 1,234 read as one-point-two-three-four."""
    d = _load(path)
    dec = d.get("DECIMAL_SEPARATOR")
    th = d.get("THOUSANDS_SEPARATOR")
    assert dec and th is not None, "both separators must be present"
    assert dec != th, f"{path.stem}: DECIMAL_SEPARATOR == THOUSANDS_SEPARATOR == {dec!r}"


def test_turkish_memory_header_is_translated():
    """I3: the Monitor's Memory section header was still English in tr_TR."""
    assert _load(LOCALES / "tr_TR.json")["MEMORY_HEADER"] != _load(LOCALES / "en_US.json")["MEMORY_HEADER"]


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_no_accelerator_collisions_within_a_menu_or_button_row(path):
    """I2: hu_HU Save "&Mentés" and Cancel "&Mégse" both answered Alt+M."""
    d = _load(path)
    problems = []
    for group, keys in GROUPS.items():
        seen = {}
        for k in keys:
            c = _mnemonic(d.get(k, ""))
            if c is not None:
                seen.setdefault(c, []).append(k)
        problems += [f"{group}: Alt+{c} -> {ks}" for c, ks in seen.items() if len(ks) > 1]
    assert not problems, f"{path.stem}: " + "; ".join(problems)
