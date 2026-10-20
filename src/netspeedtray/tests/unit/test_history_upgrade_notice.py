"""
After a SUCCESSFUL v7 -> v8 history upgrade the user gets one calm flyout, once: the history was
upgraded, totals for days before it are estimates that can read lower than 2.1 showed (2.1 over-counted
quiet minutes), and the data-cap count is unaffected. A fresh install, a file that is already v8 and a
refused (deferred) upgrade say nothing; the "shown" flag is persisted in config.
"""
import types
from unittest.mock import MagicMock

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.database import DatabaseWorker
from netspeedtray.tests.unit.test_db_switchover import _Fault, _make_v7, _unstarted_state, DB
from netspeedtray.views.widget.main import NetworkSpeedWidget


def _prepare(db):
    w = DatabaseWorker(db)
    got = []
    w.history_upgraded.connect(lambda: got.append(1))
    w._prepare_storage()
    return w, got


def test_a_successful_v7_upgrade_is_announced_once(tmp_path, q_app):
    db = _make_v7(tmp_path)
    w, got = _prepare(db)
    try:
        assert w.reader_version == 8 and not w._schema_incompatible
        assert got == [1]
    finally:
        w._close_connection()
    w2, got2 = _prepare(db)        # the next launch: already v8, nothing to announce
    try:
        assert got2 == []
    finally:
        w2._close_connection()


def test_a_fresh_install_is_not_announced(tmp_path, q_app):
    w, got = _prepare(tmp_path / DB)
    try:
        assert w.reader_version == 8 and got == []
    finally:
        w._close_connection()


def test_a_refused_upgrade_is_not_announced(tmp_path, q_app):
    db = _make_v7(tmp_path)
    w = DatabaseWorker(db)
    got, deferred = [], []
    w.history_upgraded.connect(lambda: got.append(1))
    w.migration_deferred.connect(deferred.append)
    with _Fault.no_space():
        w._prepare_storage()
    assert deferred and got == []


def test_widget_state_reemits_the_upgrade(tmp_path, q_app):
    s = _unstarted_state(tmp_path)
    try:
        got = []
        s.db_history_upgraded.connect(lambda: got.append(1))
        s.db_worker.history_upgraded.emit()
        assert got == [1]
    finally:
        s.cleanup()


def _fake_widget(config):
    return types.SimpleNamespace(i18n=I18nStrings("en_US"), config=config, logger=MagicMock(),
                                 _show_usage_alert=MagicMock(), config_controller=MagicMock())


def test_the_widget_shows_the_notice_and_persists_the_flag(q_app):
    f = _fake_widget({})
    NetworkSpeedWidget._on_db_history_upgraded(f)
    args = f._show_usage_alert.call_args
    assert args.args[:2] == (f.i18n.DB_UPGRADE_DONE_FLYOUT_TITLE, f.i18n.DB_UPGRADE_DONE_FLYOUT_BODY)
    f.config_controller.update_config.assert_called_once()
    assert f.config_controller.update_config.call_args.args[0] == {"history_upgrade_notice_shown": True}
    body = f.i18n.DB_UPGRADE_DONE_FLYOUT_BODY
    assert "estimate" in body and "data-cap" in body


def test_the_notice_is_never_shown_twice(q_app):
    f = _fake_widget({"history_upgrade_notice_shown": True})
    NetworkSpeedWidget._on_db_history_upgraded(f)
    f._show_usage_alert.assert_not_called()
    f.config_controller.update_config.assert_not_called()
