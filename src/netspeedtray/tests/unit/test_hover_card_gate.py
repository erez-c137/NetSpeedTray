"""Regression: the hover card must arm when ONLY the battery section is enabled.

enterEvent's arm gate used to check just show_usage_on_hover / show_hover_tips, so
a user running the battery card alone (usage rows and tips off, power_display_enabled
on) never saw the card at all - the arm timer never started.
"""
from netspeedtray.views.widget.main import NetworkSpeedWidget


def _config(**overrides):
    config = {"show_usage_on_hover": True, "show_hover_tips": True,
              "power_display_enabled": True}
    config.update(overrides)
    return config


def test_arms_for_battery_only():
    """usage rows off + tips off + battery toggle on -> card arms (the reported case)."""
    assert NetworkSpeedWidget._hover_card_enabled(_config(show_usage_on_hover=False,
                                                          show_hover_tips=False))


def test_arms_with_defaults():
    assert NetworkSpeedWidget._hover_card_enabled({})


def test_stays_off_when_every_section_disabled():
    assert not NetworkSpeedWidget._hover_card_enabled(_config(show_usage_on_hover=False,
                                                              show_hover_tips=False,
                                                              power_display_enabled=False))
