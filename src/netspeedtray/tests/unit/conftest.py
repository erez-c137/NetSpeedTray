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


@pytest.fixture(scope="session")
def q_app():
    """Provides a QApplication instance for the test session.

    pytest-qt's own ``qtbot``/``qapp`` fixtures reuse ``QApplication.instance()``,
    so this coexists with them (both return the same singleton).
    """
    return QApplication.instance() or QApplication([])


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
