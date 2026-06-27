"""
The retention ladder + its shared label (Settings page and the graph slider both use
helpers.format_retention_label, so they can't drift). Locks the curated steps the user signed
off on: floor = 1 month, default = 1 year, plus "Keep everything".
"""
import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import helpers

_i18n = I18nStrings("en_US")
_UNLIMITED = constants.data.retention.UNLIMITED_DAYS


@pytest.mark.parametrize("days,expected", [
    (31, "1 month"),
    (90, "3 months"),
    (180, "6 months"),
    (365, "1 year"),
    (730, "2 years"),
    (1825, "5 years"),
    (_UNLIMITED, "Keep everything"),
])
def test_retention_label(days, expected):
    assert helpers.format_retention_label(days, _i18n) == expected


def test_ladder_is_exactly_the_agreed_set():
    assert list(constants.data.retention.DAYS_MAP.values()) == [31, 90, 180, 365, 730, 1825, _UNLIMITED]


def test_floor_is_one_month_so_this_month_is_always_whole():
    # The shortest retention must cover a full calendar month (up to 31 days) so the hover card's
    # "This month" total is never truncated by pruning.
    assert min(constants.data.retention.DAYS_MAP.values()) == 31


def test_default_is_one_year():
    assert constants.config.defaults.DEFAULT_KEEP_DATA_DAYS == 365


def test_keep_everything_is_a_real_choice_within_max():
    r = constants.data.retention
    assert _UNLIMITED in r.DAYS_MAP.values()
    assert _UNLIMITED <= r.MAX_RETENTION_DAYS  # validate() would have rejected it otherwise


def test_keep_everything_pruning_never_deletes():
    # The sentinel must be far enough in the future that a now-minus-retention cutoff predates
    # any plausible data, so "keep everything" really keeps everything.
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=_UNLIMITED)
    assert cutoff.year < 2000
