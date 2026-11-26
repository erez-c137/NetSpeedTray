"""Temperature onboarding dialog - defaults, action capture, and the dismiss-forever opt-out."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.temp_onboarding_dialog import TempOnboardingDialog


@pytest.fixture
def dlg(qt_owned):
    return qt_owned(TempOnboardingDialog(I18nStrings("en_US")))


def test_default_action_is_dismiss(dlg):
    assert dlg.action == TempOnboardingDialog.ACTION_DISMISS


def test_get_lhm_link_sets_action_and_opens_the_releases_page(q_app, qt_owned):
    from netspeedtray.views.temp_onboarding_dialog import LHM_RELEASES_URL
    opened = []
    dlg = qt_owned(TempOnboardingDialog(I18nStrings("en_US"), open_url=opened.append))
    dlg.get_lhm_link.click()
    assert dlg.action == TempOnboardingDialog.ACTION_GET_LHM
    assert opened == [LHM_RELEASES_URL]


def test_dont_remind_defaults_off_and_toggles(dlg):
    assert dlg.dismissed_forever() is False
    dlg.dont_remind.setChecked(True)
    assert dlg.dismissed_forever() is True


def test_releases_url_is_official_lhm(dlg):
    from netspeedtray.views.temp_onboarding_dialog import LHM_RELEASES_URL
    assert LHM_RELEASES_URL.startswith("https://github.com/LibreHardwareMonitor/")


def test_releases_url_is_the_current_release(dlg):
    """v0.9.5+ is read over LHM's web server (#187), so the button no longer pins v0.9.4."""
    from netspeedtray.views.temp_onboarding_dialog import LHM_RELEASES_URL
    assert LHM_RELEASES_URL.endswith("/releases/latest")


def test_step_two_names_the_lhm_menu_item(dlg):
    """2.2.2 D6: the web server is a numbered step - LHM's Options → Remote Web Server → Run. On LHM's
    own default port there is no port to set; another port adds the Interface / Port sentence."""
    text = dlg.steps[1].text()
    assert "Options → Remote Web Server → Run" in text
    assert "{port}" not in text and "Interface / Port" not in text


def _clipped_labels(dlg, q_app):
    """Every visible word-wrapped label shorter than its text needs at its width."""
    from PyQt6.QtWidgets import QLabel
    q_app.processEvents()
    return [(lbl.text()[:40], lbl.height(), lbl.heightForWidth(lbl.width()))
            for lbl in dlg.findChildren(QLabel)
            if lbl.isVisible() and lbl.wordWrap() and lbl.height() < lbl.heightForWidth(lbl.width())]


@pytest.mark.parametrize("lang", ["en_US", "de_DE", "hu_HU", "he_IL"])
def test_every_step_stays_fully_visible_as_the_steps_tick(q_app, qt_owned, lang):
    """VM live test (2.2.2 D6): when both steps ticked, "Temperatures are on." appeared and the window
    kept its height - the layout squeezed the wrapped step labels and step 1 lost its last line. Every
    wrapped label must get its full height-for-width in every state, and with More details open."""
    from PyQt6.QtCore import Qt
    from netspeedtray import constants
    L = constants.lhm
    state = {"s": None}
    dlg = qt_owned(TempOnboardingDialog(I18nStrings(lang), show_remind=False,
                                         status_provider=lambda: state["s"]))
    if lang == "he_IL":
        dlg.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    dlg.show()
    assert _clipped_labels(dlg, q_app) == [], "unticked"
    for status in (L.STATUS_SERVER_OFF, L.STATUS_OK, None, L.STATUS_OK):
        state["s"] = status
        dlg.refresh_status()
        assert _clipped_labels(dlg, q_app) == [], status
    assert dlg.connected_label.isVisible()
    dlg.details.setExpanded(True)
    assert _clipped_labels(dlg, q_app) == [], "More details open"
    open_height = dlg.height()
    dlg.details.setExpanded(False)
    assert _clipped_labels(dlg, q_app) == [], "More details closed"
    assert dlg.height() < open_height   # closing More details gives the room back


SRC_DIR = Path(__file__).resolve().parents[3]
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

_CLIP_PROBE = r"""
import json, sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel
app = QApplication(sys.argv)
from netspeedtray import constants
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.views.temp_onboarding_dialog import TempOnboardingDialog
L = constants.lhm
rows = []
for lang in ("en_US", "de_DE", "hu_HU", "he_IL"):
    state = {"s": None}
    dlg = TempOnboardingDialog(I18nStrings(lang), show_remind=False, status_provider=lambda: state["s"])
    if lang == "he_IL":
        dlg.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    dlg.show()
    for tag, status in (("unticked", None), ("step 1 ticked", L.STATUS_SERVER_OFF),
                        ("both ticked", L.STATUS_OK)):
        state["s"] = status
        dlg.refresh_status()
        app.processEvents()
        for lbl in dlg.findChildren(QLabel):
            if lbl.isVisible() and lbl.wordWrap() and lbl.height() < lbl.heightForWidth(lbl.width()):
                rows.append([lang, tag, lbl.text()[:40], lbl.height(), lbl.heightForWidth(lbl.width())])
    rows.append(["#seen", lang])
    dlg.close()
    dlg.deleteLater()
print("RESULT" + json.dumps(rows), flush=True)
"""


@pytest.mark.skipif(sys.platform != "win32" or not FONT_DIR.is_dir(),
                    reason="needs the Windows font directory for real text metrics")
@pytest.mark.parametrize("scale", ["1", "1.5"])
def test_steps_keep_their_text_with_real_fonts(tmp_path, scale):
    """The VM case itself, with Windows font metrics (a subprocess): at 100% the dialog opened whole,
    then "Temperatures are on." appearing cut step 1's last line. 150% too. A large offscreen screen
    (``configfile``, relative: the platform argument splits on ':') so no screen size caps the window."""
    (tmp_path / "screen.json").write_text(json.dumps({"screens": [
        {"name": "probe", "x": 0, "y": 0, "width": 2880, "height": 1620, "logicalDpi": 96,
         "logicalBaseDpi": 96, "dpr": 1}]}), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen:configfile=screen.json", QT_QPA_FONTDIR=str(FONT_DIR),
               QT_SCALE_FACTOR=scale, APPDATA=str(tmp_path), LOCALAPPDATA=str(tmp_path),
               PYTHONPATH=str(SRC_DIR), PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-c", _CLIP_PROBE], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, f"probe failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}"
    rows = json.loads(lines[-1][len("RESULT"):])
    assert sum(1 for r in rows if r[0] == "#seen") == 4, rows
    assert [r for r in rows if r[0] != "#seen"] == []
