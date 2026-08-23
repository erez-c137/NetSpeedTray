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

**2.1.4: the two quirks previously xfailed here are now FIXED**, because the
2.2.0 beta cycle depends on them. `_parse_version` pads to a fixed shape,
`(major, minor, patch, is_final, stage_rank, stage_number)`, which makes
"1.3" == "1.3.0", keeps a final release above its own pre-releases, and orders
successive pre-releases so `is_newer` can actually advance between betas.
Before this, every "2.2.0-beta.N" parsed to (2, 2) and compared EQUAL, so a
beta tester was stranded on whichever build they installed first.
"""
import pytest

from netspeedtray.core.update_checker import _parse_version, is_newer, select_release_assets


# --- _parse_version: normalization -------------------------------------------

# Shape is (major, minor, patch, is_final, stage_rank, stage_number).
# is_final=1 for a real release, 0 for a pre-release.
@pytest.mark.parametrize("raw, expected", [
    ("1.3.1", (1, 3, 1, 1, 0, 0)),
    ("v1.3.1", (1, 3, 1, 1, 0, 0)),     # lowercase v stripped
    ("V1.3.1", (1, 3, 1, 1, 0, 0)),     # uppercase V stripped
    ("vv1.0", (1, 0, 0, 1, 0, 0)),      # lstrip removes *all* leading v/V chars
    (" 1.2.3 ", (1, 2, 3, 1, 0, 0)),    # surrounding whitespace stripped
    ("1.3.10", (1, 3, 10, 1, 0, 0)),    # multi-digit component kept as int 10
    ("2", (2, 0, 0, 1, 0, 0)),          # single component padded to a full release
    ("0.0.0", (0, 0, 0, 1, 0, 0)),
    ("1.4.0+build7", (1, 4, 0, 1, 0, 0)),   # build metadata is not precedence
])
def test_parse_version_normalizes(raw, expected):
    assert _parse_version(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("", ()),                          # empty -> empty tuple, no crash
    ("v", ()),                         # only the prefix
    ("   ", ()),                       # whitespace only
    ("abc", ()),                       # no numeric leading component
    ("1.3.x", (1, 3, 0, 1, 0, 0)),     # stops at first non-int, then pads
    ("1..2", (1, 0, 0, 1, 0, 0)),      # empty middle component stops parsing
])
def test_parse_version_malformed_is_graceful(raw, expected):
    # The contract is: never raise, return a (possibly empty) int tuple.
    assert _parse_version(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("1.3-beta", (1, 3, 0, 0, 1, 0)),      # suffix no longer eats the component
    ("1.3.1-rc2", (1, 3, 1, 0, 2, 2)),     # digits glued to the stage name
    ("2.2.0-beta.1", (2, 2, 0, 0, 1, 1)),
    ("2.2.0-alpha", (2, 2, 0, 0, 0, 0)),
    ("2.2.0-rc.1", (2, 2, 0, 0, 2, 1)),
    ("2.2.0-nightly", (2, 2, 0, 0, 3, 0)),  # unknown stage: above rc, below final
])
def test_parse_version_prerelease_shape(raw, expected):
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


# --- release / pre-release ordering (fixed in 2.1.4) -------------------------

def test_trailing_zero_components_treated_as_equal():
    # "1.3" and "1.3.0" are the same release; neither is newer. Previously the
    # shorter tuple compared as older, so "1.3.0" looked like an update over "1.3".
    assert is_newer("1.3.0", "1.3") is False
    assert is_newer("1.3", "1.3.0") is False


def test_final_release_outranks_its_own_prereleases():
    assert is_newer("1.4.0", "1.4.0-beta") is True
    assert is_newer("1.4.0", "1.4.0-rc.9") is True
    assert is_newer("1.4.0-beta", "1.4.0") is False
    assert is_newer("1.4.0-rc.9", "1.4.0") is False


def test_prerelease_of_higher_version_beats_lower_final():
    # A 2.2.0 beta IS newer than 2.1.4 - this is what lets a tester opt in.
    assert is_newer("1.5.0-beta", "1.4.0") is True
    assert is_newer("2.2.0-beta.1", "2.1.4") is True
    assert is_newer("2.1.4", "2.2.0-beta.1") is False


def test_successive_prereleases_are_ordered():
    """The 2.2.0 beta cycle depends on this.

    Previously every '2.2.0-beta.N' truncated to (2, 2) and compared EQUAL, so a
    beta tester could never be offered the next beta - they were stranded on
    whichever build they installed first.
    """
    assert is_newer("1.4.0-beta2", "1.4.0-beta1") is True
    assert is_newer("2.2.0-beta.2", "2.2.0-beta.1") is True
    assert is_newer("2.2.0-beta.10", "2.2.0-beta.9") is True   # numeric, not lexical
    assert is_newer("2.2.0-beta.1", "2.2.0-beta.2") is False


def test_prerelease_stages_are_ordered():
    assert is_newer("2.2.0-beta.1", "2.2.0-alpha.9") is True
    assert is_newer("2.2.0-rc.1", "2.2.0-beta.9") is True
    # An unrecognised stage sorts above rc but still below the final release,
    # so an unexpected tag can never look newer than a real one.
    assert is_newer("2.2.0-nightly", "2.2.0-rc.1") is True
    assert is_newer("2.2.0", "2.2.0-nightly") is True


# --- release-asset selection (installer + portable) --------------------------

def test_select_release_assets_picks_installer_and_portable():
    """Both URLs must be surfaced so the updater can pick the portable ZIP for a portable run (#195)."""
    assets = [
        {"name": "checksums.txt", "browser_download_url": "https://x/sums"},
        {"name": "NetSpeedTray-2.1.0-x64-Setup.exe", "browser_download_url": "https://x/setup"},
        {"name": "NetSpeedTray-Portable-2.1.0.zip", "browser_download_url": "https://x/portable"},
    ]
    installer, portable = select_release_assets(assets)
    assert installer == "https://x/setup"
    assert portable == "https://x/portable"


def test_select_release_assets_missing_are_empty():
    installer, portable = select_release_assets([{"name": "notes.md", "browser_download_url": "https://x/n"}])
    assert installer == ""
    assert portable == ""

