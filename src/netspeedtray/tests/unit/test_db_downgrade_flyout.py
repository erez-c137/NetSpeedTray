"""
The downgrade guard is no longer log-only (v2.1.5, action-plan item 1).

test_db_downgrade_guard.py proves the guard *holds* - reads work, every write is
refused. These tests pin the new *surface* on top of it: a rolled-back install used to
look perfectly healthy while recording nothing, forever, because the refusal lived only
in the log. Now:

- ``DatabaseWorker.schema_incompatible`` is emitted when the guard trips, carrying
  (file_version, supported_version, newest_backup_full_path or "").
- The refusal ERROR names the newest sibling ``speed_history.db.bak.v*`` by FULL path
  and no longer advises discarding the history as the first option.
- ``WidgetState`` re-emits the signal (``db_schema_incompatible``) so delivery is queued
  onto the GUI thread once the event loop is up.
- ``NetworkSpeedWidget._on_db_schema_incompatible`` shows the generic flyout with the
  two new i18n strings (the MONITOR_ERROR_FLYOUT pattern at main.py's _on_monitor_error).

Signal-level assertions only - no live GUI is spawned (the lightweight fake-`self`
pattern used across the widget tests, e.g. test_pause_gate.py).
"""

import logging
import os
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QThread

from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.core.database import DatabaseWorker


def _make_newer_db(tmp_path: Path) -> tuple[Path, int]:
    """
    Build a real schema at this build's version, then stamp a NEWER version into
    metadata - the exact on-disk state a rollback leaves behind.
    """
    db_path = tmp_path / "speed_history.db"
    worker = DatabaseWorker(db_path)
    worker._initialize_connection()
    worker._check_and_create_schema()
    newer_version = DatabaseWorker._DB_VERSION + 1
    worker.conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('db_version', ?)",
        (str(newer_version),),
    )
    worker.conn.commit()
    worker._close_connection()
    return db_path, newer_version


def _plant_backups(tmp_path: Path) -> Path:
    """Create an older and a newer pre-migration backup sibling; returns the newest."""
    old_bak = tmp_path / "speed_history.db.bak.v7_20260101_000000"
    old_bak.write_bytes(b"old backup")
    stale = time.time() - 3600
    os.utime(old_bak, (stale, stale))
    new_bak = tmp_path / "speed_history.db.bak.v7_20260801_120000"
    new_bak.write_bytes(b"new backup")
    return new_bak


def _open_guarded(db_path: Path) -> tuple[DatabaseWorker, list]:
    """Open the DB the way run() does (synchronously, no thread), capturing the signal."""
    worker = DatabaseWorker(db_path)
    received: list = []
    worker.schema_incompatible.connect(lambda *args: received.append(args))
    worker._initialize_connection()
    worker._check_and_create_schema()
    return worker, received


class TestDowngradeSignal:
    """DatabaseWorker.schema_incompatible carries what the UI message needs."""

    def test_emitted_with_newest_backup_full_path(self, tmp_path, q_app):
        db_path, newer_version = _make_newer_db(tmp_path)
        newest_bak = _plant_backups(tmp_path)

        worker, received = _open_guarded(db_path)
        try:
            assert worker._schema_incompatible is True
            assert received == [
                (newer_version, DatabaseWorker._DB_VERSION, str(newest_bak))
            ], "Signal must name the NEWEST backup by full path"
        finally:
            worker._close_connection()

    def test_emitted_with_empty_path_when_no_backup_exists(self, tmp_path, q_app):
        db_path, newer_version = _make_newer_db(tmp_path)

        worker, received = _open_guarded(db_path)
        try:
            assert worker._schema_incompatible is True
            assert received == [(newer_version, DatabaseWorker._DB_VERSION, "")]
        finally:
            worker._close_connection()

    def test_not_emitted_on_healthy_open(self, tmp_path, q_app):
        db_path = tmp_path / "speed_history.db"
        worker, received = _open_guarded(db_path)
        try:
            assert worker._schema_incompatible is False
            assert received == []
        finally:
            worker._close_connection()


class TestDowngradeErrorMessage:
    """The refusal ERROR points the user AT their data, not away from it."""

    def test_names_newest_backup_and_drops_discard_advice(self, tmp_path, q_app, caplog):
        db_path, _ = _make_newer_db(tmp_path)
        newest_bak = _plant_backups(tmp_path)

        with caplog.at_level(logging.ERROR):
            worker, _ = _open_guarded(db_path)
        worker._close_connection()

        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        refusal = next((m for m in errors if "READ-ONLY" in m), None)
        assert refusal is not None, "The downgrade refusal ERROR was not logged"
        assert str(newest_bak) in refusal, "ERROR must name the newest backup by full path"
        assert "aside" not in refusal, (
            "With a verified backup sitting in the same folder, the message must not "
            "advise discarding the history"
        )

    def test_without_backup_discard_is_not_the_first_option(self, tmp_path, q_app, caplog):
        db_path, _ = _make_newer_db(tmp_path)

        with caplog.at_level(logging.ERROR):
            worker, _ = _open_guarded(db_path)
        worker._close_connection()

        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        refusal = next((m for m in errors if "READ-ONLY" in m), None)
        assert refusal is not None
        # Upgrading (which keeps the data) must be advised before any mention of
        # starting a fresh history.
        assert "Upgrade" in refusal
        if "aside" in refusal:
            assert refusal.index("Upgrade") < refusal.index("aside")


class TestWidgetStateReemit:
    """WidgetState re-emits so the GUI thread gets a queued delivery it can't miss."""

    def test_reemits_schema_incompatible(self, tmp_path, q_app):
        from netspeedtray.core.widget_state import WidgetState

        config = constants.config.defaults.DEFAULT_CONFIG.copy()
        with patch.object(QThread, "start", lambda self: None):
            with patch(
                "netspeedtray.core.widget_state.get_app_data_path",
                return_value=tmp_path,
            ):
                state = WidgetState(config)
        try:
            received = []
            state.db_schema_incompatible.connect(
                lambda file_v, ours, bak: received.append((file_v, ours, bak))
            )
            bak = str(tmp_path / "speed_history.db.bak.v7_20260801_120000")
            state.db_worker.schema_incompatible.emit(8, 7, bak)
            assert received == [(8, 7, bak)]
        finally:
            state.cleanup()


class TestFlyoutHandler:
    """The widget surfaces the guard through the existing generic flyout notifier."""

    def test_handler_shows_the_two_new_strings(self, q_app):
        from netspeedtray.views.widget.main import NetworkSpeedWidget

        f = types.SimpleNamespace()
        f.i18n = I18nStrings("en_US")
        f._show_usage_alert = MagicMock()
        f.logger = MagicMock()

        NetworkSpeedWidget._on_db_schema_incompatible(
            f, 8, 7, r"C:\Users\x\AppData\Roaming\NetSpeedTray\speed_history.db.bak.v7_1"
        )

        f._show_usage_alert.assert_called_once_with(
            f.i18n.DB_READONLY_FLYOUT_TITLE, f.i18n.DB_READONLY_FLYOUT_BODY
        )

    def test_i18n_keys_exist_in_english(self):
        i18n = I18nStrings("en_US")
        assert i18n.DB_READONLY_FLYOUT_TITLE.strip()
        assert i18n.DB_READONLY_FLYOUT_BODY.strip()
        # Distinct from the monitor-degraded notice this pattern was copied from.
        assert i18n.DB_READONLY_FLYOUT_BODY != i18n.MONITOR_ERROR_FLYOUT_BODY


class TestBackupVersionFilter:
    """Review C5: the refusal message must never point at a backup this build cannot read."""

    def test_newest_backup_prefers_a_version_this_build_can_read(self, tmp_path):
        worker = DatabaseWorker(tmp_path / "speed_history.db")
        v_ok = DatabaseWorker._DB_VERSION
        readable = tmp_path / f"speed_history.db.bak.v{v_ok}_20260101_000000"
        readable.write_bytes(b"readable")
        stale = time.time() - 3600
        os.utime(readable, (stale, stale))
        unreadable = tmp_path / f"speed_history.db.bak.v{v_ok + 1}_20260801_000000"
        unreadable.write_bytes(b"written by a newer schema")   # newest by mtime

        assert worker._find_newest_backup() == readable

    def test_only_unreadable_backups_means_no_backup(self, tmp_path):
        worker = DatabaseWorker(tmp_path / "speed_history.db")
        v_bad = DatabaseWorker._DB_VERSION + 1
        (tmp_path / f"speed_history.db.bak.v{v_bad}_20260801_000000").write_bytes(b"v8 only")

        assert worker._find_newest_backup() is None, (
            "naming a backup this build cannot open sends the user into a dead-end restore")
