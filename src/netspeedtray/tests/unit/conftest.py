
import pytest
from PyQt6.QtWidgets import QApplication

@pytest.fixture(scope="session")
def q_app():
    """Provides a QApplication instance for the test session."""
    return QApplication.instance() or QApplication([])
