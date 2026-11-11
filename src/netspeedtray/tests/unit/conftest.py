import os

# Run Qt tests headless and deterministically. Must be set before any QApplication
# is created (pytest-qt's qtbot fixture and the q_app fixture below). `setdefault`
# lets a developer override it locally (e.g. to actually see a dialog) without
# editing this file.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")  # pin DPI for render determinism

# The real %APPDATA% / %LOCALAPPDATA% are redirected for the whole session in tests/conftest.py
# (one level up, so it also runs before the integration tests import anything).

import pytest
from PyQt6.QtWidgets import QApplication

# ONE QApplication for the whole process, created here and held by this module until exit.
# Many test modules declare their own ``q_app``/``qt_app`` fixture (``QApplication.instance() or
# QApplication([])``), several module-scoped. Whichever of them ran first in a process used to CREATE
# the app, and was then its only owner: at that module's teardown the wrapper was released, the C++
# QApplication was destroyed, and the next Qt test made a second one. After that recreate, PyQt6 no
# longer releases the wrappers of C++-created children (a scroll area's viewport, a combo's line
# edit, ...) when their Python-created parent is destroyed, so those stale wrappers stay in sip's
# address map. A later object allocated at a reused address (a QBoxLayout) was then handed back by
# findChildren(QWidget) as the old QWidget wrapper, and name_controls' accessibleName() read it as a
# widget: an access violation or "Negative size passed to PyUnicode_New" (PR #505, parallel order).
# The product creates exactly one QApplication (monitor.py) and never hits this.
_SESSION_APP = QApplication.instance() or QApplication([])


@pytest.fixture(scope="session")
def q_app():
    """Provides a QApplication instance for the test session.

    pytest-qt's own ``qtbot``/``qapp`` fixtures reuse ``QApplication.instance()``,
    so this coexists with them (both return the same singleton).
    """
    return QApplication.instance() or _SESSION_APP


def dispose_widget(widget) -> None:
    """Destroy a test's top-level widget NOW, on the C++ side, instead of leaving it to Python's
    cyclic GC. A parentless page or dialog with signal/lambda reference cycles was otherwise
    collected at a random point in some LATER test, which crashed the run with an access violation
    (depending on test order). Safe to call twice."""
    from PyQt6 import sip
    if widget is None or sip.isdeleted(widget):
        return
    try:
        widget.close()
    except RuntimeError:
        pass
    if not sip.isdeleted(widget):
        sip.delete(widget)


@pytest.fixture
def qt_owned(q_app):
    """``qt_owned(widget)`` -> widget, destroyed deterministically at teardown (dispose_widget)."""
    owned = []

    def own(widget):
        owned.append(widget)
        return widget
    yield own
    for w in reversed(owned):
        dispose_widget(w)
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


@pytest.fixture(autouse=True)
def _isolate_update_marker(tmp_path, monkeypatch):
    """No test may read or write the real %APPDATA%/NetSpeedTray/staged_update.json or stage into
    the real %LOCALAPPDATA%/NetSpeedTray/updates (#260): ``apply_update`` records its outcome
    there, and an unisolated CLI test once wrote a stray entry into the maintainer's live marker.
    Tests that need the marker override ``_marker_path`` themselves; this is the floor."""
    from netspeedtray.core import update_applier
    root = tmp_path / "_isolated_update_state"
    monkeypatch.setattr(update_applier, "_marker_path",
                        lambda: str(root / "appdata" / update_applier._STAGED_MARKER_NAME))
    monkeypatch.setattr(update_applier, "staging_root",
                        lambda: str(root / "localappdata" / "NetSpeedTray" / "updates"))
    # Recorded staged folders are only deleted from a staging location; in tests that is the
    # test's own Downloads stand-in (or tmp_path itself) - never the real Downloads.
    monkeypatch.setattr(update_applier, "_downloads_dirs", raising=False, value=
                        lambda: [str(tmp_path / "Downloads"), str(tmp_path)])


@pytest.fixture(autouse=True)
def _no_real_shell_launch(monkeypatch):
    """No test may open a real browser or Explorer window on the machine running the suite:
    ``os.startfile`` (behind ``helpers.open_external``, the guided-copy folder reveal and
    ``webbrowser.open`` on Windows) is a no-op here. A test that checks a launch fakes it itself."""
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", lambda *a, **k: None)


class _NoLhmWebServer:
    """Stand-in for LhmHttpClient: LibreHardwareMonitor's web server is not running."""

    def __init__(self, *args, **kwargs):
        from netspeedtray import constants
        self.calls = 0
        self.host = constants.lhm.HTTP_HOST
        self.port = constants.lhm.HTTP_PORT
        self.status = constants.lhm.STATUS_NOT_FOUND

    def get_snapshot(self):
        self.calls += 1
        return None

    def reset_backoff(self):
        pass

    def configure(self, port, username="", password=""):
        self.port = port


@pytest.fixture(autouse=True)
def _no_real_lhm_web_server(monkeypatch):
    """No StatsMonitorThread under test may read a real LibreHardwareMonitor on the developer's
    machine (127.0.0.1:8085, #187): a live LHM would answer ahead of the WMI/ACPI sources a test
    pins, and its absence costs a connect attempt. Tests of the HTTP source itself use
    netspeedtray.core.lhm_http directly, against servers they start; tests that want an LHM
    reading set ``thread._lhm_http`` themselves."""
    from netspeedtray.core import monitor_thread
    monkeypatch.setattr(monitor_thread, "LhmHttpClient", _NoLhmWebServer)


@pytest.fixture(autouse=True)
def _backups_not_quitting(monkeypatch):
    """A widget ``cleanup()`` under test calls ``history_backup.cancel_all`` (the app is quitting), which
    refuses every later backup for the rest of the process - right for the app, wrong for the next
    test (#334). Each test starts with backups allowed."""
    from netspeedtray.core.db import history_backup
    monkeypatch.setattr(history_backup, "_cancelled", False)


@pytest.fixture(autouse=True)
def _pin_windows_theme(monkeypatch):
    """Pin the Windows (taskbar) theme the colour scheme resolves against, so no test depends on the
    machine running it (on a light-mode CI runner the factory white text once turned black and failed
    exact assertions that pass on a dark-mode dev PC, PR #409).

    Dark by default; NST_TEST_WINDOWS_THEME=light runs the suite under a light taskbar, to prove a test
    doesn't depend on it. The cached flag is cleared, so the first resolve reads the pinned value. A
    test that needs a specific theme patches color_scheme itself (or passes ``light=``), which overrides
    this floor."""
    import os
    from netspeedtray.utils import color_scheme
    light = os.environ.get("NST_TEST_WINDOWS_THEME", "dark").strip().lower() == "light"
    monkeypatch.setattr(color_scheme, "read_system_uses_light_theme", lambda: 1 if light else 0)
    monkeypatch.setattr(color_scheme, "_shell_light", None)
    # ...and no High Contrast / accent taskbar (legibility "L"): a dev PC with an accent bar must not
    # change what the suite paints. Tests of those states patch read_taskbar_appearance themselves.
    from netspeedtray.utils.taskbar_appearance import PLAIN
    monkeypatch.setattr(color_scheme, "read_taskbar_appearance", lambda shell_light=False: PLAIN)
    monkeypatch.setattr(color_scheme, "_appearance", None)


@pytest.fixture(autouse=True)
def _fresh_per_taskbar_layout_cache(monkeypatch):
    """The per-HWND layout cache and background-probe queue (#373) are process-wide; tests reuse the
    same fake HWNDs, so each test starts (and ends) with both empty - one test's landed layout must
    never answer for another's taskbar. And no test starts a real background UIA probe thread by
    placing a widget on a mocked tray-less bar (PositionManager._refresh_trayless_bar_layout): it
    would flip the layout worker's ``running`` flag under tests that drive the worker by hand. Tests of
    the background probe import the real function at module level (before this patch applies)."""
    from netspeedtray.utils import taskbar_utils
    monkeypatch.setattr(taskbar_utils, "request_background_layout_probe", lambda _hwnd: None)
    # The Widgets toggle (TaskbarDa) is pinned ON: layout reads drop the Widgets box while it is off
    # (#473), and no test may depend on the machine's setting. Tests of the toggle patch it themselves.
    monkeypatch.setattr(taskbar_utils, "_widgets_toggle_on", lambda: True, raising=False)
    monkeypatch.setattr(taskbar_utils, "_widgets_flip", {"on": None, "gen": 0, "on_at": None},
                        raising=False)
    taskbar_utils._layout_by_hwnd.clear()
    del taskbar_utils._layout_background[:]
    getattr(taskbar_utils, "_layout_superseded", {}).clear()
    yield
    taskbar_utils._layout_by_hwnd.clear()
    del taskbar_utils._layout_background[:]
