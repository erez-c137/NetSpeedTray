"""Session-wide test isolation for every test under src/netspeedtray/tests (unit AND integration).

No test may write the maintainer's real %APPDATA%/NetSpeedTray (config, history, the update records)
or %LOCALAPPDATA%/NetSpeedTray. Redirected here - the top of the test tree - so it runs before ANY
test module is imported: get_app_data_path() caches its first answer, and CI runs `pytest` from the
repo root, which collects tests/integration before tests/unit (a unit-level conftest was too late,
#402). utils/styles.py once wrote its chevron PNG cache there from every Settings test, and an
unisolated apply_update test wrote a stray update marker (#260 review). Per-test fixtures still
narrow this further where a test needs to.
"""
import os
import sqlite3 as _sqlite3
import tempfile as _tempfile

import pytest

_SESSION_PROFILE = _tempfile.mkdtemp(prefix="nst-test-profile-")
os.environ["APPDATA"] = os.path.join(_SESSION_PROFILE, "Roaming")
os.environ["LOCALAPPDATA"] = os.path.join(_SESSION_PROFILE, "Local")


# --- SQLite without fsync, in THIS process only ---------------------------------------------------
# Most of the suite's wall time was fsync, not work: commits and wal_checkpoint(TRUNCATE) on tiny temp
# databases (a 62 s test spent 95% of it flushing). `synchronous` only decides what survives a power
# cut or an OS crash; nothing a test does in-process can observe it - every other handle reads the
# same OS page cache. So every connection opened in the pytest process runs with synchronous=OFF:
#   - sqlite3.connect is wrapped (every raw connection a test or the code under test opens);
#   - DatabaseWorker and the frozen 2.1.x LegacyV7Worker set NORMAL right after connecting, so their
#     _initialize_connection is wrapped to set OFF again afterwards.
# Child processes are untouched: the crash-durability tests (tests/integration/test_*_kill.py, the
# TerminateProcess children elsewhere) run their writers in fresh interpreters that never load this
# file, so they keep the real pragmas and real fsync. The v7->v8 migrator still forces FULL on its own
# build connection (migrate_v8.py) - left as is. No test asserts the pragma's value.
# NST_TEST_REAL_FSYNC=1 turns all of this off (to time or debug a run with the product's pragmas).
_REAL_FSYNC = os.environ.get("NST_TEST_REAL_FSYNC") == "1"


def _fsync_off(conn) -> None:
    try:
        conn.execute("PRAGMA synchronous = OFF")
    except Exception:
        pass    # a closed / unusable connection: the code under test reports that itself


if not _REAL_FSYNC and not getattr(_sqlite3.connect, "_nst_fsync_off", False):
    _orig_connect = _sqlite3.connect

    def _connect_fsync_off(*args, **kwargs):
        conn = _orig_connect(*args, **kwargs)
        _fsync_off(conn)
        return conn

    _connect_fsync_off._nst_fsync_off = True
    _sqlite3.connect = _connect_fsync_off


def _wrap_initialize_connection(cls) -> None:
    orig = cls._initialize_connection
    if getattr(orig, "_nst_fsync_off", False):
        return

    def _initialize_connection(self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        conn = getattr(self, "conn", None)
        if conn is not None:
            _fsync_off(conn)
        return result

    _initialize_connection._nst_fsync_off = True
    cls._initialize_connection = _initialize_connection


def pytest_configure(config):
    if not _REAL_FSYNC:
        # Imported here, not at the top: the APPDATA redirect above must run before any app module.
        from netspeedtray.core.database import DatabaseWorker
        from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
        _wrap_initialize_connection(DatabaseWorker)
        _wrap_initialize_connection(LegacyV7Worker)
    # `-n N` alone means --dist load, which would run the native-desktop tests next to each other.
    # Upgrade it to loadgroup, which keeps every test marked native_desktop on ONE worker, one after
    # the other (they show real windows; the hover-card test reads the foreground). Any other --dist
    # chosen explicitly is left alone.
    if getattr(config.option, "dist", "no") == "load":
        config.option.dist = "loadgroup"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    if not config.pluginmanager.hasplugin("xdist"):
        return
    for item in items:
        if item.get_closest_marker("native_desktop") is not None:
            item.add_marker(pytest.mark.xdist_group("native_desktop"))
