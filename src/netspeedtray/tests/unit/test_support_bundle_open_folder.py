"""
After saving a support bundle the next step is attaching it to an issue, but the success box offered
nothing to do. It now has an "Open folder" button that opens Explorer with the zip selected (the same
absolute-path explorer launch the stats export uses). Nothing here writes outside a tmp dir or opens
a real window or Explorer.
"""
import logging
import os
import subprocess

import pytest
from PyQt6.QtWidgets import QMessageBox, QWidget

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.utils import helpers
from netspeedtray.views.settings import dialog as D

EN = I18nStrings("en_US")


class _Host(QWidget):
    """Just enough of SettingsDialog for export_support_bundle (a real QWidget for the box parent)."""
    def __init__(self):
        super().__init__()
        self.i18n = EN
        self.logger = logging.getLogger("test")
        self.config = {}


def _run(monkeypatch, tmp_path, click_label):
    zip_path = tmp_path / "bundle.zip"
    monkeypatch.setattr(D.QFileDialog, "getSaveFileName", staticmethod(lambda *a: (str(zip_path), "")))
    monkeypatch.setattr(D, "build_support_bundle", lambda destination_zip, config: destination_zip)
    revealed = []
    monkeypatch.setattr(D, "reveal_in_explorer", revealed.append)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)          # never shown
    monkeypatch.setattr(QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if b.text().replace("&", "") == click_label))
    D.SettingsDialog.export_support_bundle(_Host())
    return zip_path, revealed


def test_open_folder_reveals_the_bundle(q_app, monkeypatch, tmp_path):
    zip_path, revealed = _run(monkeypatch, tmp_path, EN.STATS_DETAIL_OPEN_FOLDER)
    assert revealed == [str(zip_path)]


def test_ok_does_not_open_explorer(q_app, monkeypatch, tmp_path):
    _zip, revealed = _run(monkeypatch, tmp_path, "OK")
    assert revealed == []


@pytest.mark.parametrize("path", [
    r"C:/Users/x/Desktop/b.zip",
    r"C:\Users\x\My Files\b.zip",
    r"C:\Users\x\a,b\NetSpeedTray_Support,1.zip",   # Explorer splits /select, on commas
])
def test_reveal_launches_explorer_by_absolute_path_with_the_path_quoted(monkeypatch, path):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda args: calls.append(args))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    helpers.reveal_in_explorer(path)
    assert calls == ['"C:\\Windows\\explorer.exe" /select,"' + os.path.normpath(path) + '"']


def test_reveal_never_raises(monkeypatch):
    def boom(_args):
        raise OSError("no explorer")
    monkeypatch.setattr(subprocess, "Popen", boom)
    helpers.reveal_in_explorer("x.zip")
