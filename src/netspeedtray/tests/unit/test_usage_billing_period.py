"""
The hover card's "This month" row sat right above the data-cap row, but the two counted different
spans: the calendar month vs the billing period (reset day). With a reset day of 15 they disagreed
side by side. With a cap set and a reset day other than the 1st, the row now covers the billing
period (start taken from the odometer, which owns it) and reads "This Period"; the Overview usage
tile follows, including its avg/day + projected. Otherwise it stays the calendar month.
"""
import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QLabel

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.widget.main import NetworkSpeedWidget

EN = I18nStrings("en_US")


class _WS:
    def __init__(self, period_key):
        self.period_key = period_key
        self.starts = []

    def get_usage_period_key(self):
        return self.period_key

    def get_history_volume_for_period(self, start, end):
        self.starts.append(start)
        return SimpleNamespace(up_bytes=1.0, down_bytes=2.0, reconstructed=False)


class _W:
    """Just enough of NetworkSpeedWidget to run the real usage-totals code."""
    _HOVER_USAGE_TTL_SEC = NetworkSpeedWidget._HOVER_USAGE_TTL_SEC
    _hover_usage_totals = NetworkSpeedWidget._hover_usage_totals
    _usage_billing_period_start = NetworkSpeedWidget._usage_billing_period_start

    def __init__(self, config, period_key):
        self.config = config
        self.widget_state = _WS(period_key)
        self.logger = logging.getLogger("test")
        self._hover_usage_cache = None
        self._hover_usage_cache_ts = 0.0


def _month_start():
    return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def test_month_row_follows_the_billing_period_when_a_cap_is_set():
    start = date.today() - timedelta(days=3)
    if start.day == 1:
        start -= timedelta(days=1)   # keep it off the 1st, the calendar-month case
    w = _W({"data_cap_enabled": True, "data_cap_gb": 500, "data_cap_reset_day": start.day},
           start.isoformat())
    w._hover_usage_totals()
    period = datetime(start.year, start.month, start.day)
    assert w.widget_state.starts[1] == period          # [0] is today, [1] the "month" row
    assert w._hover_usage_period_start == period


@pytest.mark.parametrize("config", [
    {"data_cap_enabled": False, "data_cap_gb": 500, "data_cap_reset_day": 15},   # no cap: calendar
    {"data_cap_enabled": True, "data_cap_gb": 0, "data_cap_reset_day": 15},
])
def test_month_row_stays_the_calendar_month_without_a_cap(config):
    w = _W(config, (date.today() - timedelta(days=3)).isoformat())
    w._hover_usage_totals()
    assert w.widget_state.starts[1] == _month_start()
    assert w._hover_usage_period_start is None


def test_reset_day_one_is_just_the_month():
    w = _W({"data_cap_enabled": True, "data_cap_gb": 500, "data_cap_reset_day": 1},
           _month_start().date().isoformat())
    w._hover_usage_totals()
    assert w.widget_state.starts[1] == _month_start()
    assert w._hover_usage_period_start is None


def test_flyout_labels_the_row_this_period(q_app):
    from netspeedtray.views.usage_flyout import UsageFlyout
    card = UsageFlyout(EN, (1, 2), (3, 4), month_label=EN.USAGE_THIS_PERIOD_LABEL)
    texts = [lbl.text() for lbl in card.findChildren(QLabel)]
    assert "This Period" in texts and EN.USAGE_THIS_MONTH_LABEL not in texts


def test_overview_tile_uses_the_period_for_label_and_average(q_app):
    from netspeedtray.views.monitor.overview.tiles import UsageTile
    tile = UsageTile(EN)
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=4)
    tile.set((0.0, 0.0), (0.0, 5.0e9), None, period_start=start)    # 5 GB over 5 period days
    assert tile._month_name.text() == "This Period"
    assert tile._avgday[1].text().startswith("↓ 1.0 GB")
    tile.set((0.0, 0.0), (0.0, 5.0e9), None)
    assert tile._month_name.text() == EN.USAGE_THIS_MONTH_LABEL
