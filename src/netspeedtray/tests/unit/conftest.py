import os

# Run Qt tests headless and deterministically. Must be set before any QApplication
# is created (pytest-qt's qtbot fixture and the q_app fixture below). `setdefault`
# lets a developer override it locally (e.g. to actually see a dialog) without
# editing this file.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "1")  # pin DPI for render determinism

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def q_app():
    """Provides a QApplication instance for the test session.

    pytest-qt's own ``qtbot``/``qapp`` fixtures reuse ``QApplication.instance()``,
    so this coexists with them (both return the same singleton).
    """
    return QApplication.instance() or QApplication([])
