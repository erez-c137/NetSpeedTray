"""
System / user text in its own face (utils/styles.text_face and friends, rc.7): the face is taken BACK
when the text no longer needs it, and text no known face can have (emoji) is never probed.

The face checks need the real Windows fonts (offscreen, text_face is None by design), so the main cases
run in a clean subprocess on the windows platform, in an English UI.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3]

_PROBE = r"""
import json, os, sys, psutil
from PyQt6.QtWidgets import QApplication, QLabel
app = QApplication(sys.argv[:1])
from netspeedtray import constants
from netspeedtray.utils import styles as su
su.apply_ui_language(app, constants.i18n.get_i18n("en_US").language)
from netspeedtray.utils.components import Win11ComboBox


def files():
    return sorted({os.path.basename(m.path) for m in psutil.Process().memory_maps()
                   if m.path.lower().endswith((".ttf", ".ttc", ".otf"))})


def state(w):
    app.processEvents(); w.ensurePolished()   # its style sheet applied: font() is what it draws in
    return {"family": w.font().family(), "marked": "nst-text-face" in (w.styleSheet() or "")}


from netspeedtray.utils.font_runs import resolve_family
out = {"ui": su.ui_family(), "yahei": resolve_family("Microsoft YaHei UI") is not None}
# First, in a process that has probed nothing yet: an emoji Wi-Fi name maps no font file (the UI face
# drawn first, so it is not counted).
plain = QLabel("Ethernet"); plain.adjustSize(); plain.grab()
before = files()
out["emoji_face"] = su.text_face("Home🏠")
out["emoji_new_files"] = sorted(set(files()) - set(before))
su._windows_language = "zh_CN"                                 # then: a Chinese Windows
label = QLabel()
su.set_text_in_face(label, "以太网"); out["label_cjk"] = state(label)
su.set_text_in_face(label, "Ethernet"); out["label_ascii"] = state(label)
# The editable host box with no style sheet above it (the box's font then flows into its line edit).
combo = Win11ComboBox(); combo.setEditable(True); combo.addItems(["1.1.1.1", "8.8.8.8"])
su.keep_combo_face(combo)
su.set_combo_text_in_face(combo, "例子.测试")
out["combo_cjk"] = state(combo); out["edit_cjk"] = state(combo.lineEdit())
su.set_combo_text_in_face(combo, "1.1.1.1")
out["combo_ascii"] = state(combo); out["edit_ascii"] = state(combo.lineEdit())
combo.lineEdit().setText("例子.测试"); combo.lineEdit().setText("8.8.8.8")   # typed, then typed back
out["edit_typed_back"] = state(combo.lineEdit())
print(json.dumps(out), flush=True)
os._exit(0)
"""


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows fonts (DirectWrite)")
def test_face_is_taken_back_and_emoji_is_never_probed(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8",
               APPDATA=str(tmp_path))
    res = subprocess.run([sys.executable, "-c", _PROBE], env=env, capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert res.returncode == 0, res.stderr[-2000:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    if not out["yahei"]:
        pytest.skip("Microsoft YaHei UI is not installed here")
    ui = out["ui"]
    assert out["label_cjk"]["marked"] and out["label_cjk"]["family"] != ui
    assert out["label_ascii"] == {"family": ui, "marked": False}, out["label_ascii"]
    assert out["edit_cjk"]["marked"] and out["edit_cjk"]["family"] != ui
    # Back to an ASCII host: the line edit's own font is the UI face again - not the face the box had
    # when the line edit's font was first saved.
    assert out["edit_ascii"] == {"family": ui, "marked": False}, out["edit_ascii"]
    assert out["combo_ascii"] == {"family": ui, "marked": False}, out["combo_ascii"]
    assert out["edit_typed_back"] == {"family": ui, "marked": False}, out["edit_typed_back"]
    assert out["emoji_face"] is None
    assert out["emoji_new_files"] == [], f"emoji probing mapped font files: {out['emoji_new_files']}"


def test_astral_text_is_never_probed(monkeypatch):
    """An emoji (outside the BMP) can't be asked of GDI; probing it would cache a QRawFont per face."""
    from netspeedtray.utils import styles as su
    from netspeedtray.utils import font_runs

    def boom(*a, **k):
        raise AssertionError("text_face probed a face for astral text")

    monkeypatch.setattr(font_runs, "family_covers", boom)
    monkeypatch.setattr(su, "face_for_text", boom)
    assert su.text_face("Home\U0001F3E0") is None
    assert su.text_face("x\U0001F600 y") is None
