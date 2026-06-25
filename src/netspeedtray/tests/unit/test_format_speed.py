"""
Unit tests for helpers.format_speed.

format_speed takes a speed in BYTES/sec and renders it for the widget. It is on
the hot path and used everywhere, but had no direct coverage. These tests pin the
behavior that matters: bits-vs-bytes scaling, the base-unit %.0f vs scaled
decimal_places formatting, decimal/binary divisors, force_mega, locale decimal
separator, and the clamp/type-guard edges.

Unit strings are asserted via the real I18nStrings attributes (not hardcoded), so
the tests track the locale data rather than a particular English label.
"""
import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils.helpers import format_speed, get_reference_value_string

# 1 Mbit/s == 1_000_000 bits/s == 125_000 bytes/s.
MBIT_IN_BYTES = 125_000


@pytest.fixture(scope="module")
def en():
    return I18nStrings("en_US")


@pytest.fixture(scope="module")
def de():
    return I18nStrings("de_DE")


# --- bits, decimal (default) -------------------------------------------------

def test_bits_decimal_scaling(en):
    # 1 Mbit/s
    assert format_speed(MBIT_IN_BYTES, en, split_unit=True) == ("1.0", en.MBITS_LABEL)
    # 1 Kbit/s  (125 B/s * 8 = 1000 bit/s)
    assert format_speed(125, en, split_unit=True) == ("1.0", en.KBITS_LABEL)
    # 2 Gbit/s  (250 MB/s * 8 = 2e9 bit/s)
    assert format_speed(250_000_000, en, split_unit=True) == ("2.0", en.GBITS_LABEL)


def test_base_unit_uses_no_decimals(en):
    # 50 B/s * 8 = 400 bit/s -> base unit, formatted with %.0f regardless of decimal_places
    assert format_speed(50, en, split_unit=True) == ("400", en.BITS_LABEL)
    assert format_speed(50, en, decimal_places=3, split_unit=True) == ("400", en.BITS_LABEL)


def test_zero_and_negative_clamp_to_base_zero(en):
    # MINIMUM_DISPLAY_SPEED is 0.0, so the historical sub-zero branch is dead:
    # zero and negative both render as "0" in the base unit, never a kilo unit.
    assert format_speed(0, en, split_unit=True) == ("0", en.BITS_LABEL)
    assert format_speed(-9999, en, split_unit=True) == ("0", en.BITS_LABEL)


# --- bytes, decimal ----------------------------------------------------------

def test_bytes_decimal_scaling(en):
    assert format_speed(1_000_000, en, unit_type="bytes_decimal", split_unit=True) == ("1.0", en.MBPS_LABEL)
    assert format_speed(1_000, en, unit_type="bytes_decimal", split_unit=True) == ("1.0", en.KBPS_LABEL)
    # base bytes uses %.0f
    assert format_speed(500, en, unit_type="bytes_decimal", split_unit=True) == ("500", en.BPS_LABEL)


# --- binary units ------------------------------------------------------------

def test_bits_binary_uses_binary_divisor(en):
    # 131072 B/s * 8 = 1_048_576 bit/s == exactly 1 Mibit/s (MEBI divisor)
    assert format_speed(131072, en, unit_type="bits_binary", split_unit=True) == ("1.0", en.MIBITS_LABEL)


# --- decimal_places + force_mega ---------------------------------------------

def test_decimal_places_on_scaled_unit(en):
    assert format_speed(MBIT_IN_BYTES, en, decimal_places=2, split_unit=True) == ("1.00", en.MBITS_LABEL)
    assert format_speed(MBIT_IN_BYTES, en, decimal_places=0, split_unit=True) == ("1", en.MBITS_LABEL)


def test_force_mega_unit_always_mega(en):
    # Forces the mega unit even when the value would otherwise scale to kilo/base.
    assert format_speed(MBIT_IN_BYTES, en, force_mega_unit=True, split_unit=True) == ("1.0", en.MBITS_LABEL)
    # 0.1 Mbit/s stays in Mbit/s rather than dropping to Kbit/s
    assert format_speed(12_500, en, force_mega_unit=True, split_unit=True) == ("0.1", en.MBITS_LABEL)


# --- output shape ------------------------------------------------------------

def test_split_unit_false_joins_value_and_unit(en):
    assert format_speed(MBIT_IN_BYTES, en) == f"1.0 {en.MBITS_LABEL}"


def test_fixed_width_right_justifies_value(en):
    val, _ = format_speed(MBIT_IN_BYTES, en, fixed_width=True, split_unit=True)
    ref = get_reference_value_string(False, 1, "bits_decimal")  # "888.8"
    assert val.strip() == "1.0"
    assert len(val) == len(ref)  # right-justified to the reference width, not just >=


def test_bytes_binary_uses_mebi_divisor(en):
    # 1 MiB/s == 1_048_576 bytes/s (MEBI divisor)
    assert format_speed(1_048_576, en, unit_type="bytes_binary", split_unit=True) == ("1.0", en.MIBPS_LABEL)


def test_bytes_decimal_giga_scale(en):
    # 2 GB/s == 2_000_000_000 bytes/s (decimal GIGA)
    assert format_speed(2_000_000_000, en, unit_type="bytes_decimal", split_unit=True) == ("2.0", en.GBPS_LABEL)


def test_force_mega_unit_with_zero(en):
    # force_mega keeps the mega unit and decimal formatting even at zero speed.
    assert format_speed(0, en, force_mega_unit=True, split_unit=True) == ("0.0", en.MBITS_LABEL)


# --- locale decimal separator ------------------------------------------------

def test_locale_decimal_separator(de):
    # de_DE uses ',' as the decimal separator and localized unit labels.
    assert de.DECIMAL_SEPARATOR == ","
    assert format_speed(MBIT_IN_BYTES, de, split_unit=True) == ("1,0", de.MBITS_LABEL)
    # base unit has no decimal to translate
    assert format_speed(50, de, split_unit=True) == ("400", de.BITS_LABEL)


# --- type guard --------------------------------------------------------------

@pytest.mark.parametrize("bad", ["abc", None, [1], {}])
def test_non_number_raises_typeerror(en, bad):
    with pytest.raises(TypeError):
        format_speed(bad, en)
