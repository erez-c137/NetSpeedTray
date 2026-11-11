"""The suite keeps ONE QApplication for the whole process (conftest._SESSION_APP).

A module-scoped ``q_app`` fixture that happened to run first used to create the app and own it alone;
its teardown destroyed the C++ QApplication and the next test made a second one. After that, PyQt6
leaves the wrappers of C++-created children (a scroll area's viewport, ...) in sip's address map when
their Python-created parent is destroyed, and findChildren later hands one back for an unrelated
object at the reused address - the access violation in name_controls' accessibleName() (PR #505).
"""
import gc
import os
import subprocess
import sys
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QScrollArea, QWidget

from netspeedtray.tests.unit import conftest

UNIT = Path(__file__).resolve().parent
REPO = UNIT.parents[3]


def test_a_destroyed_parent_releases_its_cpp_created_child_wrappers(q_app):
    host = QWidget()
    scroll = QScrollArea(host)
    scroll.viewport().setAutoFillBackground(False)   # a wrapper for the C++-created viewport
    sip.delete(host)                                  # C++ side gone, the scroll wrapper still alive
    assert sip.isdeleted(scroll)
    held = [type(r).__name__ for r in gc.get_referents(scroll) if isinstance(r, QObject)]
    assert held == [], f"stale child wrappers still registered with sip: {held}"


def test_the_session_application_is_the_only_one(q_app):
    assert QApplication.instance() is conftest._SESSION_APP


def test_a_module_q_app_fixture_running_first_does_not_destroy_the_application():
    """test_app_overlap_yield.py's module-scoped autouse q_app, first in the process, then the probe
    above: without the session-held app, the probe sees stale child wrappers (every time)."""
    # Any test in a module with a module-scoped q_app fixture works; renaming this one fails loudly.
    first = "src/netspeedtray/tests/unit/test_app_overlap_yield.py::test_owner_centred_bar_group_is_start_to_last_app"
    probe = ("src/netspeedtray/tests/unit/test_qapplication_lifetime.py::"
             "test_a_destroyed_parent_releases_its_cpp_created_child_wrappers")
    same_app = "src/netspeedtray/tests/unit/test_qapplication_lifetime.py::test_the_session_application_is_the_only_one"
    env = dict(os.environ)
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("PYTEST_ADDOPTS", None)   # e.g. a developer's -n auto would clash with -p no:xdist
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", first, probe, same_app],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
