"""
A locale file is only reachable if it is also registered, and only usable if a few of its values
respect where they get painted.

`LANGUAGE_MAP` in `constants/i18n.py` is the source of truth for both the Settings picker and
`resolve_language()`. A locale JSON with no entry there is dead weight: it never appears in the
dropdown, and auto-detect falls through to en_US. That is exactly what happened to Turkish in #249 -
a complete, correct `tr_TR.json` that the app could not reach, and which every existing test passed
because parity only ever compared *keys between files*.

The value checks below cover the other two things a new-locale PR gets wrong that CI could not see:
a decimal separator that does not match the language, and a `DEFAULT_TEXT` long enough to overflow
the taskbar widget.
"""

import json
from pathlib import Path

import pytest

from netspeedtray.constants.i18n import I18nStrings

_LOCALES = Path(__file__).parents[3] / "netspeedtray" / "constants" / "locales"


def _locale_files():
    return sorted(_LOCALES.glob("*.json"))


@pytest.mark.parametrize("path", _locale_files(), ids=lambda p: p.stem)
def test_every_locale_file_is_registered(path):
    """A shipped JSON with no LANGUAGE_MAP entry is unreachable from the UI."""
    assert path.stem in I18nStrings.LANGUAGE_MAP, (
        f"{path.stem}.json exists but is not in LANGUAGE_MAP, so it cannot be selected in Settings "
        f"and resolve_language('{path.stem}') returns en_US. Add it to constants/i18n.py."
    )


@pytest.mark.parametrize("code", sorted(I18nStrings.LANGUAGE_MAP))
def test_every_registered_language_has_a_file(code):
    """The reverse: an entry with no file offers the user a language that loads as English."""
    assert (_LOCALES / f"{code}.json").exists(), (
        f"LANGUAGE_MAP lists {code} but {code}.json is missing; selecting it silently falls back."
    )


@pytest.mark.parametrize("code", sorted(I18nStrings.LANGUAGE_MAP))
def test_registered_language_resolves_to_itself(code):
    """Guards the alias/prefix logic: no shipped locale may resolve to a different one.

    The prefix scan that maps de_AT -> de_DE is order-dependent, so adding a second locale for an
    existing language (pt_BR next to a pt_PT, say) can silently capture the wrong file.
    """
    assert I18nStrings.resolve_language(code) == code


# The decimal mark each language actually uses, per CLDR. This is a property of the language, not a
# preference, so it is pinned rather than merely range-checked: "." is a perfectly valid *value* while
# being wrong for Turkish, which is how tr_TR shipped with the English separator in #249. A new locale
# has to be added here, which is the point - it forces the question to be answered.
_EXPECTED_DECIMAL_SEPARATOR = {
    "en_US": ".", "ko_KR": ".", "ja_JP": ".", "zh_CN": ".", "zh_TW": ".", "he_IL": ".",
    "de_DE": ",", "es_ES": ",", "fr_FR": ",", "nl_NL": ",", "pl_PL": ",", "ru_RU": ",",
    "sl_SI": ",", "tr_TR": ",",
}


@pytest.mark.parametrize("path", _locale_files(), ids=lambda p: p.stem)
def test_decimal_separator_matches_the_language(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    sep = data.get("DECIMAL_SEPARATOR")
    assert sep in (".", ","), (
        f"{path.stem}: DECIMAL_SEPARATOR is {sep!r}; it is substituted into formatted speeds and "
        f"must be exactly '.' or ','."
    )
    expected = _EXPECTED_DECIMAL_SEPARATOR.get(path.stem)
    assert expected is not None, (
        f"{path.stem} is a new locale: add its decimal mark to _EXPECTED_DECIMAL_SEPARATOR."
    )
    assert sep == expected, (
        f"{path.stem}: DECIMAL_SEPARATOR is {sep!r}, but this language writes decimals with "
        f"{expected!r} (CLDR). Speeds would render as '12.5' where users expect '12,5'."
    )


@pytest.mark.parametrize("path", _locale_files(), ids=lambda p: p.stem)
def test_default_text_stays_short_enough_for_the_widget(path):
    """DEFAULT_TEXT is painted on the taskbar widget itself.

    `widget_renderer._fmt_hw_percent` returns it when a hardware reading is unavailable, into a slot
    laid out for the reference string - so a spelled-out phrase ('Mevcut degil', 12 chars) overflows
    where 'N/A' fits. Every locale abbreviates; the cap is deliberately loose to leave room for
    scripts that need more glyphs, and only catches the sentence-instead-of-abbreviation mistake.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("DEFAULT_TEXT", "")
    assert 0 < len(value) <= 6, (
        f"{path.stem}: DEFAULT_TEXT is {value!r} ({len(value)} chars). It is drawn on the taskbar "
        f"widget in a slot sized for 'N/A' - use the language's abbreviation, not a phrase."
    )
