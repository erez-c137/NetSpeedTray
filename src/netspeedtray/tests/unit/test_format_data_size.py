"""
Tests for `format_data_size` (helpers.py) - the shared decimal (base-1000) byte
formatter behind the data-cap usage card and five Monitor surfaces. It was
previously untested despite being load-bearing for the cap accounting (the
"used" glance and the cap it's compared against both route through it).

The headline case is the tier-promotion rounding bug: a value just under a
boundary (e.g. 999_999 B) scales to 999.999 KB, which rounds to 1000.0 - so it
must read "1.0 MB", not "1000.0 KB".
"""
import pytest

from netspeedtray.utils.helpers import format_data_size


class _I18n:
    BYTES_UNIT = "B"
    KB_UNIT = "KB"
    MB_UNIT = "MB"
    GB_UNIT = "GB"
    TB_UNIT = "TB"
    PB_UNIT = "PB"


I = _I18n()


@pytest.mark.parametrize("data_bytes, expected", [
    (0,                (0.0, "B")),
    (1,                (1.0, "B")),
    (500,              (500.0, "B")),
    (999,              (999.0, "B")),
    (1_000,            (1.0, "KB")),
    (1_500,            (1.5, "KB")),
    (1_500_000,        (1.5, "MB")),
    (1_000_000_000,    (1.0, "GB")),
    # The rounding-promotion bug: just under a boundary must promote a tier,
    # never render as "1000.x <smaller-unit>".
    (999_999,          (1.0, "MB")),
    (999_999_999,      (1.0, "GB")),
    (999_999_999_999,  (1.0, "TB")),
    # ...but a value that genuinely rounds DOWN must stay in its tier.
    (999_994,          (999.99, "KB")),
    (999_940,          (999.94, "KB")),
])
def test_boundaries_and_promotion(data_bytes, expected):
    assert format_data_size(data_bytes, I) == expected


def test_negative_clamps_to_zero():
    assert format_data_size(-5, I) == (0.0, "B")
    assert format_data_size(-999_999_999, I) == (0.0, "B")


def test_precision_argument():
    # 1_536_000 B == 1.536 MB
    assert format_data_size(1_536_000, I, precision=0) == (2.0, "MB")   # rounds 1.536 -> 2
    assert format_data_size(1_536_000, I, precision=1) == (1.5, "MB")
    assert format_data_size(1_536_000, I, precision=3) == (1.536, "MB")


def test_promotion_respects_precision_zero():
    # precision=0: 999_999 B -> 999.999 KB -> round -> 1000 -> promote -> 1 MB.
    assert format_data_size(999_999, I, precision=0) == (1.0, "MB")


def test_never_promotes_past_top_unit():
    # A value beyond PB must stay in PB (the top unit), not overflow the label list.
    huge = 5_000 * 1000 ** 5   # 5000 PB in bytes
    value, unit = format_data_size(huge, I)
    assert unit == "PB"
    assert value >= 1000.0     # legitimately large in PB, not falsely promoted


def test_type_error_on_non_number():
    with pytest.raises(TypeError):
        format_data_size("100", I)
    with pytest.raises(TypeError):
        format_data_size(None, I)
