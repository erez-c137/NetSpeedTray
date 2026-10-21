"""
The first-run gesture hint in the widget's hover card - the only onboarding a new install gets. It
used to promise "graphs, app activity, and CPU/GPU stats" on right-click, none of which the 2.2 menu
has (Settings / Monitor / Pause / Updates / Support / Exit), and a double-click that is configurable.
Now it names the menu's real entries and the double-click action actually configured.
"""
import pytest

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.widget.main import hover_hint_text

D = constants.config.defaults
EN = I18nStrings("en_US")


@pytest.mark.parametrize("action, label_key", [
    (D.CLICK_ACTION_OPEN_MONITOR, "CLICK_ACTION_OPEN_MONITOR_LABEL"),
    (D.CLICK_ACTION_SETTINGS, "CLICK_ACTION_SETTINGS_LABEL"),
    (D.CLICK_ACTION_PAUSE, "CLICK_ACTION_PAUSE_LABEL"),
])
def test_hint_names_the_configured_double_click_action(action, label_key):
    text = hover_hint_text(EN, {"double_click_action": action})
    lines = text.split("\n")
    assert lines[0] == EN.WIDGET_HOVER_TOOLTIP
    assert lines[1] == f"Double-click: {getattr(EN, label_key)}"


def test_hint_drops_the_double_click_line_when_it_does_nothing():
    assert hover_hint_text(EN, {"double_click_action": D.CLICK_ACTION_NONE}) == EN.WIDGET_HOVER_TOOLTIP


def test_default_config_hint_matches_the_real_menu():
    text = hover_hint_text(EN, dict(D.DEFAULT_CONFIG))
    assert text == "Right-click for Settings, the Monitor, and more\nDouble-click: Open Monitor"
    for gone in ("graphs", "app activity", "CPU/GPU"):   # 1.x/2.0 menu items the 2.2 menu lacks
        assert gone not in text


@pytest.mark.parametrize("code", sorted(I18nStrings.LANGUAGE_MAP))
def test_hint_formats_in_every_locale(code):
    i18n = I18nStrings(code)
    text = hover_hint_text(i18n, {"double_click_action": D.CLICK_ACTION_OPEN_MONITOR})
    assert "{" not in text and i18n.CLICK_ACTION_OPEN_MONITOR_LABEL in text
