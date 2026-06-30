"""
Unit tests for core.update_checker version comparison/parsing.

The v1.3.2 updater regression makes the version-compare path high-value: a
lexical (string) compare would say "1.3.9" > "1.3.10" and either spam or miss
updates. These tests pin the REAL behavior of `_parse_version` and `is_newer`:

  - numeric (tuple-of-ints) comparison, so 1.3.10 > 1.3.9
  - a leading 'v'/'V' is stripped on either side
  - equal versions => not newer
  - malformed / empty / partial version strings parse gracefully (no crash);
    `_parse_version` stops at the first non-integer component

Only the pure comparison/parsing is exercised here - no network, no Qt threads,
no UpdateChecker instance (that would need real HTTP + a QThread).

Known quirks that are intentional and should NOT be "fixed" in a patch are
xfailed below with a clear note (zero-padding asymmetry: "1.3" vs "1.3.0",
and pre-release tags being truncated rather than ordered).
"""
import pytest

from netspeedtray.core.update_checker import _parse_version, is_newer


# --- _parse_version: normalization -------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("1.3.1", (1, 3, 1)),
    ("v1.3.1", (1, 3, 1)),     # lowercase v stripped
    ("V1.3.1", (1, 3, 1)),     # uppercase V stripped
    ("vv1.0", (1, 0)),         # lstrip removes *all* leading v/V chars
    (" 1.2.3 ", (1, 2, 3)),    # surrounding whitespace stripped
    ("1.3.10", (1, 3, 10)),    # multi-digit component kept as int 10, not "10"
    ("2", (2,)),               # single component
    ("0.0.0", (0, 0, 0)),
])
def test_parse_version_normalizes(raw, expected):
    assert _parse_version(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("", ()),                  # empty -> empty tuple, no crash
    ("v", ()),                 # only the prefix
    ("   ", ()),               # whitespace only
    ("abc", ()),               # no numeric leading component
    ("1.3.x", (1, 3)),         # stops at first non-int component
    # A pre-release suffix is attached to the LAST numeric component, so the
    # whole component (e.g. "3-beta") fails int() and parsing stops *before* it.
    ("1.3-beta", (1,)),        # "3-beta" is non-int -> only (1,) survives
    ("1.3.1-rc2", (1, 3)),     # "1-rc2" is non-int -> stops, (1, 3)
    ("1..2", (1,)),            # empty middle component is non-int -> stops at it
])
def test_parse_version_malformed_is_graceful(raw, expected):
    # The contract is: never raise, return a (possibly short/empty) int tuple.
    assert _parse_version(raw) == expected


# --- is_newer: numeric, not lexical ------------------------------------------

def test_is_newer_numeric_not_lexical():
    # The whole point: 1.3.10 must beat 1.3.9 even though "1.3.10" < "1.3.9"
    # as plain strings.
    assert is_newer("1.3.10", "1.3.9") is True
    assert is_newer("1.3.9", "1.3.10") is False
    # And the lexical trap one more place: 1.10.0 > 1.9.0
    assert is_newer("1.10.0", "1.9.0") is True
    assert is_newer("1.9.0", "1.10.0") is False


@pytest.mark.parametrize("latest, current, expected", [
    ("1.3.4", "1.3.3", True),     # patch bump
    ("1.4.0", "1.3.9", True),     # minor bump beats higher patch
    ("2.0.0", "1.9.9", True),     # major bump
    ("1.3.3", "1.3.4", False),    # older latest
    ("1.3.3", "2.0.0", False),    # much older latest
])
def test_is_newer_ordering(latest, current, expected):
    assert is_newer(latest, current) is expected


def test_is_newer_equal_is_not_newer():
    # Strictly newer: equal versions must return False (no self-update prompt).
    assert is_newer("1.3.3", "1.3.3") is False
    assert is_newer("0.0.0", "0.0.0") is False


def test_is_newer_v_prefix_stripped_either_side():
    # 'v' on latest, current, both, or neither - all equivalent.
    assert is_newer("v1.3.4", "1.3.3") is True
    assert is_newer("1.3.4", "v1.3.3") is True
    assert is_newer("v1.3.4", "v1.3.3") is True
    # And a 'v' prefix must not make an equal version look newer.
    assert is_newer("v1.3.3", "1.3.3") is False
    assert is_newer("1.3.3", "v1.3.3") is False


def test_is_newer_malformed_does_not_crash():
    # Graceful: empty/garbage current or latest must not raise.
    assert is_newer("1.3.4", "") is True     # () < (1,3,4)
    assert is_newer("", "1.3.4") is False     # () is not > anything
    assert is_newer("", "") is False
    assert is_newer("garbage", "1.3.4") is False  # () vs (1,3,4)
    assert is_newer("1.3.4", "garbage") is True   # (1,3,4) > ()


# --- known quirks: pin as xfail, do NOT change in a patch --------------------

@pytest.mark.xfail(
    reason="Zero-padding asymmetry: tuple compare makes (1,3) < (1,3,0), so "
           "is_newer('1.3.0','1.3') is True but is_newer('1.3','1.3.0') is "
           "False. '1.3' and '1.3.0' are the same release; treating one as "
           "newer is arguably wrong. Intentional/known - not a patch fix.",
    strict=True,
)
def test_trailing_zero_components_treated_as_equal():
    # If this ever starts passing, the normalization changed (good!) - update.
    assert is_newer("1.3.0", "1.3") is False
    assert is_newer("1.3", "1.3.0") is False


def test_trailing_zero_asymmetry_actual_behavior():
    # Documents the CURRENT (arguably-wrong) behavior so a regression is visible.
    assert is_newer("1.3.0", "1.3") is True    # longer tuple wins
    assert is_newer("1.3", "1.3.0") is False


def test_prerelease_suffix_truncates_last_component():
    # A '-beta'/'-rc' suffix is glued to the LAST numeric component, so that
    # whole component fails int() and parsing stops *before* it. The suffixed
    # component is lost entirely, not just its text part:
    #   "1.4.0-beta" -> ["1","4","0-beta"] -> int("0-beta") fails -> (1, 4)
    assert _parse_version("1.4.0-beta") == (1, 4)
    assert _parse_version("1.4.0") == (1, 4, 0)


def test_prerelease_compares_older_than_final_by_accident():
    # Because the suffixed component is dropped, the pre-release tuple is SHORTER
    # and therefore compares as older than the final release. This yields the
    # arguably-desirable result (final > beta) but only as a side effect of
    # truncation, not real semver ordering. Pinned so the accident is visible.
    assert is_newer("1.4.0", "1.4.0-beta") is True       # (1,4,0) > (1,4)
    assert is_newer("1.4.0-beta", "1.4.0") is False
    # The flip side of the same accident: a beta of a HIGHER version can look
    # equal-or-older than a lower final because its last component vanished.
    # "1.5.0-beta" -> (1,5) which is still > (1,4,0), so this one is fine:
    assert is_newer("1.5.0-beta", "1.4.0") is True


@pytest.mark.xfail(
    reason="No real semver pre-release ordering: '1.4.0-beta1' and "
           "'1.4.0-beta2' both truncate to (1,4), so successive betas compare "
           "EQUAL and is_newer can never advance between them. Proper "
           "pre-release ordering is out of scope for a patch.",
    strict=True,
)
def test_successive_prereleases_are_ordered():
    assert is_newer("1.4.0-beta2", "1.4.0-beta1") is True
