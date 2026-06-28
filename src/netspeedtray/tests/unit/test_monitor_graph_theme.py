"""
The Monitor's embedded graph must theme from the OS apps theme (su.is_dark_mode), like the rest of the
Monitor — NOT from config['dark_mode'], which is never synced to the OS and so stays at its default.

Regression for the audit High: a default-True config['dark_mode'] rendered a near-black graph inside a
light-mode Monitor (split theme) on every light-mode PC.
"""
from unittest.mock import MagicMock

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.monitor import graph_host as GH


@pytest.fixture(scope="session")
def q_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_hw_styles_follow_os_theme_not_config(q_app, monkeypatch):
    cfg = {"dark_mode": True}                       # stale config insists on dark...
    host = GH.GraphHost(MagicMock(), cfg, I18nStrings("en_US"))
    monkeypatch.setattr(GH.su, "is_dark_mode", lambda: False)   # ...but the OS is in light mode
    styles = host._hw_styles()
    # The RAM line colour is derived from the theme; light mode -> #388E3C, dark -> #4CAF50.
    assert styles["ram"][0] == "#388E3C", "graph theme ignored the OS (light) theme and used config"

    monkeypatch.setattr(GH.su, "is_dark_mode", lambda: True)
    assert host._hw_styles()["ram"][0] == "#4CAF50"
