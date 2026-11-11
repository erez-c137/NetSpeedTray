"""
Bengali digits (2.2.1, asked by the bn_BD translator on #477): "Bengali digits in fixed strings while
retaining Western digits for live numerical values".

- Fixed text - a number that is part of a label or a sentence - is written in Bengali digits: the
  retention ladder ("২ বছর", next to the locale file's own "১ বছর"), Usage history's month and year
  rows (next to the day rows' dates, which Qt already writes as ৩/১১/২০২৬) and its billing-day note.
- Live values - speeds, data amounts, percentages - stay Western in every language, so the widget's
  width (sized from the "888.8" reference strings) never changes.
- Every other language is untouched.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import helpers

BN = I18nStrings("bn_BD")
EN = I18nStrings("en_US")
BENGALI_DIGIT = re.compile("[০-৯]")
WESTERN_DIGIT = re.compile("[0-9]")
LOCALES = Path(constants.i18n.get_locales_path())


def test_fixed_digits_maps_only_the_fixed_text_languages():
    assert helpers.fixed_digits(2026, BN) == "২০২৬"
    assert helpers.fixed_digits("1-28", BN) == "১-২৮"
    for lang in I18nStrings.LANGUAGE_MAP:
        if lang not in I18nStrings.FIXED_TEXT_DIGITS:
            assert helpers.fixed_digits(2026, I18nStrings(lang)) == "2026", lang


def test_fixed_text_digit_tables_are_ten_digits_in_order():
    import unicodedata
    for lang, digits in I18nStrings.FIXED_TEXT_DIGITS.items():
        assert lang in I18nStrings.LANGUAGE_MAP
        assert [unicodedata.digit(d) for d in digits] == list(range(10)), lang


@pytest.mark.parametrize("days", sorted(constants.data.retention.DAYS_MAP.values()))
def test_retention_ladder_in_bengali_digits(days):
    """The ladder's "1 year" comes from the locale file ("১ বছর"): the other steps match it now
    ("২ বছর", not "2 বছর")."""
    label = helpers.format_retention_label(days, BN)
    assert not WESTERN_DIGIT.search(label), label
    if days < constants.data.retention.UNLIMITED_DAYS:
        assert BENGALI_DIGIT.search(label), label


def test_retention_ladder_unchanged_in_english():
    assert helpers.format_retention_label(730, EN) == "2 years"
    assert helpers.format_retention_label(90, EN) == "3 months"


@pytest.mark.parametrize("lang", ["bn_BD", "en_US", "de_DE"])
def test_live_values_stay_western(lang):
    """Speeds and amounts are live values: Western digits in every language, and the widget's width
    reference strings never change."""
    i18n = I18nStrings(lang)
    for text in (helpers.format_speed(1_234_567.0, i18n), helpers.format_decimal(12.5, i18n, 1),
                 helpers.get_reference_value_string(False, 1)):
        assert not BENGALI_DIGIT.search(text) and WESTERN_DIGIT.search(text), (lang, text)


def test_bengali_locale_writes_its_fixed_numbers_in_bengali_digits():
    """The locale file's own fixed text (TIMELINE_12_HOURS = "১২ ঘণ্টা") is Bengali digits; the
    Western digits left are technical names the translator kept as written: versions, ports and
    addresses, IPv4/IPv6, and the Wi-Fi band names the widget draws (2.4G / 5G / 6G)."""
    data = json.loads((LOCALES / "bn_BD.json").read_text(encoding="utf-8"))
    technical = re.compile(r"v\d+(\.\d+)+|\b\d+\.\d+\.\d+\.\d+\b|IPv[46]|\b2\.4 ?G(Hz)?|\b[56]G\b"
                           r"|NetSpeedTray \d\.\d|\b\d\.\d\b")
    for key, text in data.items():
        rest = technical.sub("", re.sub(r"\{[^}]*\}", "", text))
        assert not WESTERN_DIGIT.search(rest), f"{key}: {text}"


# --------------------------------------------------------------------------- Usage history

TODAY = date.today()


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return TODAY


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    import netspeedtray.views.monitor.usage_history_sheet as sheet_mod
    monkeypatch.setattr(sheet_mod, "date", _FrozenDate)


class _WS:
    def get_metered_daily_volume(self, start, end):
        from netspeedtray.core.db.history_reader import DayVolume
        return [DayVolume(TODAY - timedelta(days=i), 0, 1e9, 5e9, False) for i in reversed(range(40))]


def _labels(qt_owned, lang, view, config=None):
    from netspeedtray.views.monitor.usage_history_sheet import UsageHistorySheet
    s = qt_owned(UsageHistorySheet(_WS(), config or {}, I18nStrings(lang)))
    s.set_days(_WS().get_metered_daily_volume(None, None))
    s._on_view_changed(view)
    rows = [s._table.item(r, 0).text() for r in range(s._table.rowCount())]
    amounts = [s._table.item(r, 1).text() for r in range(s._table.rowCount())]
    return s, rows, amounts


@pytest.mark.parametrize("view", ["days", "months", "years"])
def test_usage_history_row_labels_in_bengali_digits(qt_owned, view):
    """Day rows' dates come from Qt's Bengali locale (৩/১১/২০২৬); month and year rows match them now
    ("নভেম্বর ২০২৬", not "নভেম্বর 2026"). The amounts are live values: Western."""
    _s, rows, amounts = _labels(qt_owned, "bn_BD", view)
    assert rows
    for label in rows:
        assert BENGALI_DIGIT.search(label) and not WESTERN_DIGIT.search(label), label
    for text in amounts:
        assert WESTERN_DIGIT.search(text) and not BENGALI_DIGIT.search(text), text


def test_usage_history_unchanged_in_english(qt_owned):
    _s, rows, _amounts = _labels(qt_owned, "en_US", "months")
    assert rows[0] == f"{TODAY.strftime('%B')} {TODAY.year}"
    _s, rows, _amounts = _labels(qt_owned, "en_US", "years")
    assert rows[0] == str(TODAY.year)


def test_billing_day_note_in_bengali_digits(qt_owned):
    cfg = {"data_cap_enabled": True, "data_cap_gb": 100, "data_cap_reset_day": 15}
    s, _rows, _amounts = _labels(qt_owned, "bn_BD", "months", cfg)
    assert "১৫" in s._period_note.text() and "15" not in s._period_note.text()
