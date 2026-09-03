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


def test_non_finite_input_renders_zero_not_inf_or_nan(en):
    """#14: NaN/inf from a bad caller must clamp to zero, never the literal 'inf'/'nan' in the UI."""
    for bad in (float("inf"), float("-inf"), float("nan")):
        s = format_speed(bad, en, force_mega_unit=True)
        assert "inf" not in s.lower() and "nan" not in s.lower(), s
        v, _u = format_speed(bad, en, split_unit=True)
        assert v == "0"


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


# --- unit-boundary rounding: the unit is chosen from the ROUNDED value --------
# (2.1.5 item 11a: 999.95..999.999 in any unit used to render "1000.0 <unit>" -
# four integer digits in a slot sized for three - instead of promoting.)

def test_rounding_boundary_promotes_kilo_to_mega(en):
    # 124_999 B/s = 999.992 Kbit/s -> rounds to 1000.0 at dp=1 -> must promote
    assert format_speed(124_999, en, split_unit=True) == ("1.0", en.MBITS_LABEL)


def test_rounding_boundary_promotes_mega_to_giga(en):
    # 124_999_999 B/s = 999.999992 Mbit/s
    assert format_speed(124_999_999, en, split_unit=True) == ("1.0", en.GBITS_LABEL)


def test_rounding_boundary_promotes_base_to_kilo(en):
    # 124.95 B/s = 999.6 bit/s -> "%.0f" in the base unit would render "1000"
    assert format_speed(124.95, en, split_unit=True) == ("1.0", en.KBITS_LABEL)


def test_just_below_rounding_boundary_stays(en):
    # 124_990 B/s = 999.92 Kbit/s -> 999.9 at dp=1: no promotion
    assert format_speed(124_990, en, split_unit=True) == ("999.9", en.KBITS_LABEL)


def test_rounding_boundary_at_dp0(en):
    # dp=0: 999.6 Kbit rounds to "1000" -> promote; 999.2 stays "999"
    assert format_speed(124_950, en, decimal_places=0, split_unit=True) == ("1", en.MBITS_LABEL)
    assert format_speed(124_900, en, decimal_places=0, split_unit=True) == ("999", en.KBITS_LABEL)


def test_rounding_boundary_binary_divisor(en):
    # bytes_binary: 1_048_575 B/s = 1023.999 KiB/s -> 1024.0 at dp=1 -> promote to MiB/s
    assert format_speed(1_048_575, en, unit_type="bytes_binary", split_unit=True) == ("1.0", en.MIBPS_LABEL)


def test_top_of_scale_giga_still_renders_1000(en):
    # ~1000 Gbit/s: giga is the top unit (no tera label), so "1000.0" is the honest render.
    assert format_speed(124_999_999_000, en, split_unit=True) == ("1000.0", en.GBITS_LABEL)


# --- D4: force_mega promotes to giga at the divisor (no widget widening) ------

def test_force_mega_promotes_to_giga_at_10gbe(en):
    # 1.25 GB/s = 10_000 Mbit/s: was "10000.0 Mbps" (overflows the '8888.8' slot); now giga.
    assert format_speed(1_250_000_000, en, force_mega_unit=True, split_unit=True) == ("10.0", en.GBITS_LABEL)


def test_force_mega_giga_promotion_at_rounded_boundary(en):
    # 999.999992 Mbit rounds to 1000.0 at dp=1 -> promotes
    assert format_speed(124_999_999, en, force_mega_unit=True, split_unit=True) == ("1.0", en.GBITS_LABEL)


def test_force_mega_stays_mega_below_boundary(en):
    assert format_speed(124_900_000, en, force_mega_unit=True, split_unit=True) == ("999.2", en.MBITS_LABEL)


def test_force_mega_giga_promotion_bytes_mode(en):
    # 1500 MB/s -> 1.5 GB/s (bytes modes promote too)
    assert format_speed(1_500_000_000, en, unit_type="bytes_decimal", force_mega_unit=True,
                        split_unit=True) == ("1.5", en.GBPS_LABEL)


def test_force_mega_giga_promotion_binary(en):
    # 8192 Mibit/s -> 8.0 Gibit/s (binary boundary is 1024, not 1000)
    assert format_speed(1_073_741_824, en, unit_type="bits_binary", force_mega_unit=True,
                        split_unit=True) == ("8.0", en.GIBITS_LABEL)


# --- adaptive decimal floor in force_mega mode (2.1.5 item 11c) ---------------
# At shipped defaults (always_mbps, dp=1) traffic under 6,250 B/s rendered "0.0 Mbps".
# When the value rounds to zero but is >= 0.001 of the mega unit (1 kbps), extend the
# decimals just enough to show the first significant digit, capped at 3.

def test_adaptive_floor_shows_idle_traffic(en):
    # 3000 B/s = 0.024 Mbit/s (Discord idle): was "0.0 Mbps"
    assert format_speed(3_000, en, force_mega_unit=True, split_unit=True) == ("0.02", en.MBITS_LABEL)


def test_adaptive_floor_extends_to_three_decimals(en):
    # 500 B/s = 0.004 Mbit/s
    assert format_speed(500, en, force_mega_unit=True, split_unit=True) == ("0.004", en.MBITS_LABEL)


def test_adaptive_floor_at_exact_1kbps(en):
    # 125 B/s = exactly 0.001 Mbit/s - the floor itself is shown
    assert format_speed(125, en, force_mega_unit=True, split_unit=True) == ("0.001", en.MBITS_LABEL)


def test_below_floor_renders_plain_zero(en):
    # 124 B/s = 0.000992 Mbit/s - below the 1 kbps floor: plain zero at configured precision
    assert format_speed(124, en, force_mega_unit=True, split_unit=True) == ("0.0", en.MBITS_LABEL)


def test_adaptive_floor_with_dp2_config(en):
    assert format_speed(500, en, force_mega_unit=True, decimal_places=2,
                        split_unit=True) == ("0.004", en.MBITS_LABEL)


def test_adaptive_floor_dp0_is_width_capped(en):
    # dp=0's reference is "8888" (4 chars): the borrow may never outgrow it. "0.02" fits;
    # "0.004" would not, so a dp=0 config keeps plain zero for values needing 3 decimals.
    assert format_speed(3_000, en, force_mega_unit=True, decimal_places=0,
                        split_unit=True) == ("0.02", en.MBITS_LABEL)
    assert format_speed(500, en, force_mega_unit=True, decimal_places=0,
                        split_unit=True) == ("0", en.MBITS_LABEL)


def test_adaptive_floor_never_fires_on_true_zero(en):
    assert format_speed(0, en, force_mega_unit=True, split_unit=True) == ("0.0", en.MBITS_LABEL)


def test_auto_mode_untouched_at_low_traffic(en):
    # auto never rendered zero for nonzero traffic; it must stay exactly as before
    assert format_speed(3_000, en, split_unit=True) == ("24.0", en.KBITS_LABEL)
    assert format_speed(1, en, split_unit=True) == ("8", en.BITS_LABEL)


def test_adaptive_floor_respects_locale_separator(de):
    assert format_speed(3_000, de, force_mega_unit=True, split_unit=True) == ("0,02", de.MBITS_LABEL)
    assert format_speed(500, de, force_mega_unit=True, split_unit=True) == ("0,004", de.MBITS_LABEL)


# --- width invariants: nothing new outgrows the reference string --------------

def test_new_renderings_fit_reference_width(en):
    ref_fm = get_reference_value_string(True, 1, "bits_decimal")     # "8888.8"
    for speed in (3_000, 500, 125, 124, 1_250_000_000, 124_999_999):
        val, _ = format_speed(speed, en, force_mega_unit=True, split_unit=True)
        assert len(val) <= len(ref_fm), (speed, val)
    ref_auto = get_reference_value_string(False, 1, "bits_decimal")  # "888.8"
    for speed in (124_999, 124_999_999, 124.95):
        val, _ = format_speed(speed, en, split_unit=True)
        assert len(val) <= len(ref_auto), (speed, val)


def test_fixed_width_pads_adaptive_and_promoted_values(en):
    # fixed_width rjust against the UNCHANGED reference string keeps behaving.
    ref = get_reference_value_string(True, 1, "bits_decimal")        # "8888.8" (6 chars)
    val, _ = format_speed(500, en, force_mega_unit=True, fixed_width=True, split_unit=True)
    assert val == "0.004".rjust(len(ref))
    val2, _ = format_speed(1_250_000_000, en, force_mega_unit=True, fixed_width=True, split_unit=True)
    assert val2 == "10.0".rjust(len(ref))


def test_fixed_width_longer_than_reference_is_not_padded_or_truncated(en):
    # "1000.0" at the top of the giga scale (6 chars) exceeds the auto reference "888.8"
    # (5 chars): rjust must leave it alone - current behaviour, preserved.
    val, unit = format_speed(124_999_999_000, en, fixed_width=True, split_unit=True)
    assert val == "1000.0" and unit == en.GBITS_LABEL
