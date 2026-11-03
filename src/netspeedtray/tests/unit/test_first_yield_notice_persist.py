"""The first-yield notice (#24) is marked shown only once its flyout really showed.

It saved ``app_overlap_notice_shown`` before the deferred show, so a flyout that failed to show
used up the once-ever notice for good (rc.4 review; the same fix #472 made for the second-launch
notice).
"""
import types
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from netspeedtray.views.widget import main as main_mod

W = main_mod.NetworkSpeedWidget


@pytest.fixture(scope="module", autouse=True)
def q_app():
    return QApplication.instance() or QApplication([])


def _stub(shows: bool):
    i18n = types.SimpleNamespace(SIDE_BAR_NO_ROOM_TITLE="t", APP_OVERLAP_YIELD_BODY="b",
                                 HIDE_WHEN_APPS_COVER_LABEL="S", SETTINGS_WINDOW_TITLE="W",
                                 WIDGET_SETTINGS_GROUP="P")
    stub = types.SimpleNamespace(config={}, logger=MagicMock(), update_config=MagicMock(), i18n=i18n,
                                 _show_notice_flyout=MagicMock(return_value=shows))
    stub._app_overlap_text = lambda t: W._app_overlap_text(stub, t)
    stub._show_app_overlap_notice = lambda t, b: W._show_app_overlap_notice(stub, t, b)
    return stub


def test_a_failed_show_does_not_use_up_the_notice():
    stub = _stub(shows=False)
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
        W._maybe_show_app_overlap_notice(stub)
    stub.update_config.assert_not_called()
    assert stub._app_overlap_notice_shown is False
    # The next yield tries again - and once it shows, the flag is saved.
    stub._show_notice_flyout.return_value = True
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: fn()):
        W._maybe_show_app_overlap_notice(stub)
    assert stub._show_notice_flyout.call_count == 2
    stub.update_config.assert_called_once_with({"app_overlap_notice_shown": True})


def test_nothing_is_saved_before_the_deferred_show_runs():
    stub = _stub(shows=True)
    queued = []
    with patch.object(main_mod.QTimer, "singleShot", side_effect=lambda ms, fn: queued.append(fn)):
        W._maybe_show_app_overlap_notice(stub)
        W._maybe_show_app_overlap_notice(stub)          # the session latch: one queued, not two
    assert len(queued) == 1
    stub.update_config.assert_not_called()
    queued[0]()
    stub.update_config.assert_called_once_with({"app_overlap_notice_shown": True})
