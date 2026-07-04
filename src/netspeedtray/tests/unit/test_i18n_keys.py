"""
Dead-i18n-key detector.

`I18nStrings` (constants/i18n.py) loads user-facing strings from per-language JSON
files, with ``en_US.json`` as the single source of truth: ``__getattr__`` looks a key
up in the active language, falls back to en_US, and raises ``AttributeError`` if the
key is missing from *both*. So any key referenced in code that does NOT exist in
en_US.json is a live bug: depending on the access path it surfaces to the user either
as raw key text (``getattr(..., KEY, default)`` / ``_tr(KEY, default)`` returning the
key/default) or as a crash (bare ``self.i18n.KEY`` raising AttributeError).

This test statically scans every ``*.py`` under ``src/netspeedtray/`` for the literal
key-reference patterns the codebase actually uses:

  * attribute access:        ``i18n.SOME_KEY`` / ``self.i18n.SOME_KEY``
  * the App-Activity helper:  ``self._tr("SOME_KEY", ...)``
  * defensive lookups:        ``getattr(<obj>, "SOME_KEY", ...)``

...and asserts every referenced key exists in en_US.json. Only literal
UPPER_SNAKE_CASE identifiers are matched; dynamically-built keys (f-strings like
``f"ORDER_POSITION_{i+1}"`` or variable keys like ``getattr(self.i18n, period_key)``)
contain ``{``/lowercase and are intentionally NOT matched, since their validity can't
be proven statically.

Genuinely-dead keys must NOT be deleted from this test's perspective; if found they
should be reported to a human. Keys that are intentionally defined-but-not-yet-wired,
or referenced-here-but-defined-elsewhere, go in ``ALLOWLIST`` with a reason.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

from netspeedtray.constants import i18n as i18n_module


# --- locations ---------------------------------------------------------------

# constants/i18n.py lives at .../netspeedtray/constants/i18n.py
PACKAGE_ROOT = Path(i18n_module.__file__).resolve().parent.parent  # .../netspeedtray
EN_US_JSON = PACKAGE_ROOT / "constants" / "locales" / "en_US.json"


# --- keys that are intentionally exempt --------------------------------------
#
# Each entry MUST have a reason. These are keys that a literal scan would flag but
# which are NOT bugs. Keep this list tight; do not use it to paper over real
# dead references.
ALLOWLIST: Dict[str, str] = {
    # Reserved for a planned tray pause/resume action. The menu strings already
    # exist in en_US.json but the feature wiring is intentionally deferred, so a
    # future code reference shouldn't be a surprise. Listed here defensively so
    # the test stays green whether or not the wiring exists yet.
    "PAUSE_MENU_ITEM": "Reserved for planned tray pause/resume action.",
    "RESUME_MENU_ITEM": "Reserved for planned tray pause/resume action.",
}


# --- KNOWN, GENUINELY-DEAD keys (real bugs) ----------------------------------
#
# Unlike ALLOWLIST (keys that exist in en_US.json and are merely deferred), these
# are keys referenced in code that do NOT exist in any locale -- i.e. real, latent
# bugs that crash or leak raw text when their code path runs. Per the test policy we
# do NOT silently delete/ignore them and we do NOT fix the production code from the
# test; we pin them as known-dead via an xfail-style check and REPORT them so a human
# can fix the source (restore the key, or correct the reference) and then remove the
# entry here. `value` is the human-readable bug note.
# Currently empty: the one known dead key (SPEED_GRAPH_TITLE - a latent crash in
# views/graph/window.py) was fixed to use the existing SPEED_GRAPH_TAB_LABEL. Add an
# entry here (key -> note) only to track a NEW dead reference that can't be fixed
# immediately; test_known_dead_keys_are_consistent guards the entries.
KNOWN_DEAD_KEYS: Dict[str, str] = {}


# --- regexes -----------------------------------------------------------------
#
# A real key is UPPER_SNAKE_CASE: starts with an uppercase letter, then only
# uppercase letters / digits / underscores. This pattern deliberately excludes
# anything containing lowercase or '{' (i.e. f-string fragments and dynamic keys).
_KEY = r"[A-Z][A-Z0-9_]*"

# 1) Attribute access: `<word>.i18n.KEY` or just `i18n.KEY`.
#    We anchor on the literal token `i18n.` so we don't grab unrelated dotted names.
_RE_ATTR = re.compile(r"\bi18n\.(" + _KEY + r")\b")

# 2) self._tr("KEY", ...)  /  self._tr('KEY', ...)
_RE_TR = re.compile(r"\b_tr\(\s*['\"](" + _KEY + r")['\"]")

# 3) getattr(<anything>, "KEY", ...)  /  getattr(<anything>, 'KEY', ...)
#    Restricted to i18n receivers so we don't pick up unrelated getattr calls.
_RE_GETATTR = re.compile(
    r"getattr\(\s*[^,]*i18n[^,]*,\s*['\"](" + _KEY + r")['\"]"
)

# 4) hasattr(self.i18n, "KEY")  -- general.py guards update-mode labels this way.
_RE_HASATTR = re.compile(
    r"hasattr\(\s*[^,]*i18n[^,]*,\s*['\"](" + _KEY + r")['\"]"
)


def _load_en_us_keys() -> Set[str]:
    with EN_US_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return set(data.keys())


def _iter_source_files() -> List[Path]:
    """All .py files under the package, excluding the test tree itself."""
    files: List[Path] = []
    for p in PACKAGE_ROOT.rglob("*.py"):
        parts = p.parts
        if "tests" in parts:
            continue
        files.append(p)
    return files


def _collect_references() -> List[Tuple[str, Path, int]]:
    """
    Returns (key, file, lineno) for every literal i18n key reference found in the
    (non-test) source tree.
    """
    refs: List[Tuple[str, Path, int]] = []
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for rx in (_RE_ATTR, _RE_TR, _RE_GETATTR, _RE_HASATTR):
                for m in rx.finditer(line):
                    refs.append((m.group(1), path, lineno))
    return refs


# These are not translation keys -- they are dotted attribute/module names that
# happen to sit after the literal token `i18n.` in source (e.g. `i18n_module.__file__`
# style names never appear, but guard against accidental constant-namespace hits).
# Empirically the scan only yields real keys, but we keep this guard explicit and
# deterministic. Anything here is excluded from the "must exist in en_US" check.
_NON_KEY_TOKENS: Set[str] = {
    # `self.i18n.LANGUAGE_MAP` (general.py) is a genuine *class attribute* on
    # I18nStrings -- the {code: native-name} dict used to populate the language
    # picker -- not a JSON translation key. It resolves via normal attribute access
    # (never reaching __getattr__), so it is correct as-is and must not be treated
    # as a missing locale key.
    "LANGUAGE_MAP",
}


@pytest.fixture(scope="module")
def en_us_keys() -> Set[str]:
    keys = _load_en_us_keys()
    assert keys, "en_US.json loaded no keys -- source of truth is empty/broken."
    return keys


@pytest.fixture(scope="module")
def references() -> List[Tuple[str, Path, int]]:
    refs = _collect_references()
    # Sanity: the scan must actually find references, otherwise the regexes are
    # broken and the whole test is silently vacuous.
    assert len(refs) > 50, (
        f"Only {len(refs)} i18n references found; the scanner is likely broken."
    )
    return refs


# --- the actual contract -----------------------------------------------------

def test_en_us_is_loadable_and_nonempty(en_us_keys: Set[str]) -> None:
    # en_US must contain the well-known keys the app relies on; a smoke check that
    # we are reading the right file with json.load.
    for sentinel in ("SETTINGS_WINDOW_TITLE", "MBITS_LABEL", "DECIMAL_SEPARATOR"):
        assert sentinel in en_us_keys


def test_allowlisted_keys_are_real_and_have_reasons(en_us_keys: Set[str]) -> None:
    # Guard the allowlist itself: every entry must (a) have a non-empty reason and
    # (b) actually exist in en_US.json. An allowlisted key that ISN'T in en_US would
    # mask a real dead reference, so fail loudly.
    for key, reason in ALLOWLIST.items():
        assert reason.strip(), f"ALLOWLIST entry {key!r} has an empty reason."
        assert key in en_us_keys, (
            f"ALLOWLIST entry {key!r} is not present in en_US.json. Remove it from "
            f"the allowlist or restore the key -- allowlisting a missing key hides bugs."
        )


def test_scanner_finds_known_reference_shapes(
    references: List[Tuple[str, Path, int]],
) -> None:
    # Pin that each of the access shapes is exercised by the real codebase, so a
    # future refactor that breaks one regex is noticed.
    found_keys = {key for key, _, _ in references}
    # `i18n.ERROR_TITLE` style attribute access (used widely, e.g. error dialogs).
    assert "ERROR_TITLE" in found_keys
    # `_tr("APP_ACTIVITY_...")` style (the Monitor's per-app connection list).
    assert any(k.startswith("APP_ACTIVITY_") for k in found_keys)
    # `getattr(self.i18n, "DISPLAY_FORMAT_GROUP", ...)` style (settings pages).
    assert "DISPLAY_FORMAT_GROUP" in found_keys


def test_known_dead_keys_are_consistent(
    en_us_keys: Set[str], references: List[Tuple[str, Path, int]]
) -> None:
    # Guard KNOWN_DEAD_KEYS: each entry must (a) have a note, (b) actually be
    # referenced somewhere in the source (else it's stale and should be removed),
    # and (c) genuinely be absent from en_US.json (else the "bug" is already fixed
    # and the entry must be removed so the real check covers it again).
    referenced = {key for key, _, _ in references}
    for key, note in KNOWN_DEAD_KEYS.items():
        assert note.strip(), f"KNOWN_DEAD_KEYS entry {key!r} has an empty note."
        assert key in referenced, (
            f"KNOWN_DEAD_KEYS entry {key!r} is no longer referenced in code; remove "
            f"the stale entry."
        )
        assert key not in en_us_keys, (
            f"KNOWN_DEAD_KEYS entry {key!r} now EXISTS in en_US.json -- the bug is "
            f"fixed. Remove it from KNOWN_DEAD_KEYS so the live check guards it."
        )


def test_no_dead_i18n_references(
    en_us_keys: Set[str], references: List[Tuple[str, Path, int]]
) -> None:
    """
    Every literal i18n key referenced in src/netspeedtray/ must exist in en_US.json
    (or be explicitly allowlisted). A miss is a user-visible bug: AttributeError for
    bare attribute access, or leaked raw key/default text for getattr/_tr lookups.
    """
    missing: Dict[str, List[str]] = {}
    for key, path, lineno in references:
        if key in _NON_KEY_TOKENS:
            continue
        if key in ALLOWLIST:
            continue
        if key in KNOWN_DEAD_KEYS:
            # Pinned separately by test_known_dead_keys_still_dead (xfail). Excluded
            # here so the suite stays green while the bug is tracked, not hidden.
            continue
        if key not in en_us_keys:
            loc = f"{path.relative_to(PACKAGE_ROOT).as_posix()}:{lineno}"
            missing.setdefault(key, []).append(loc)

    if missing:
        lines = []
        for key in sorted(missing):
            locs = ", ".join(sorted(set(missing[key])))
            lines.append(f"  {key}  ->  referenced at: {locs}")
        report = "\n".join(lines)
        pytest.fail(
            "Dead i18n key reference(s): key(s) used in code but missing from "
            f"en_US.json (and not allowlisted):\n{report}\n\n"
            "Fix: restore the key in en_US.json (+ all locales), update the code to "
            "the correct key, or -- if intentionally reserved -- add it to ALLOWLIST "
            "with a reason."
        )


def test_hebrew_rtl_registration():
    """he_IL is a registered, RTL locale; other locales are LTR (v2.1 item 6 foundation)."""
    from netspeedtray.constants.i18n import I18nStrings
    assert "he_IL" in I18nStrings.LANGUAGE_MAP
    assert "he_IL" in I18nStrings.RTL_LANGUAGES
    assert I18nStrings("he_IL").is_rtl is True
    assert I18nStrings("en_US").is_rtl is False
    assert I18nStrings("ru_RU").is_rtl is False
