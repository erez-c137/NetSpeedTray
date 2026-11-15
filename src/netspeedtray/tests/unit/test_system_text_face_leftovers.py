"""
The rc.7 system-text faces (#495), the review's leftovers (2.2.1):
  - the Monitor's app-row tooltip in a Hebrew / Bengali UI around a Chinese process name;
  - the Settings GPU picker with a Chinese GPU name: its popup in a Hebrew / Bengali UI, and its width;
  - a Wi-Fi name with an emoji in the hover card;
  - the Hebrew Monitor's dropdown buttons, whose label ran over the chevron.

Each of the first three loaded EVERY installed font family (~850 font files: Qt's fallback search) - in
a clean process on the real Windows platform, as test_font_files_per_language measures it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3]
EVERY_FAMILY = 40   # fewer mapped font files than this: Qt did not search every family (~850)

_PROBE = r"""
import json, os, sys, psutil
from PyQt6.QtWidgets import QApplication, QLabel
from PyQt6.QtCore import Qt
app = QApplication(sys.argv[:1])
lang, stage = sys.argv[1], sys.argv[2]
from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.font_runs import resolve_family
i18n = constants.i18n.get_i18n(lang)
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl else Qt.LayoutDirection.LeftToRight)
su.apply_ui_language(app, i18n.language)
su._windows_language = "zh_CN"                                 # a Chinese Windows


def files():
    app.processEvents()
    return sorted({os.path.basename(m.path) for m in psutil.Process().memory_maps()
                   if m.path.lower().endswith((".ttf", ".ttc", ".otf"))})


out = {"faces": {f: resolve_family(f) is not None for f in ("Microsoft YaHei UI", "Segoe UI Emoji", su.ui_family())}}
# The UI face, drawn first (not counted).
from netspeedtray.views.usage_flyout import UsageFlyout
plain = UsageFlyout(i18n, hardware={"cpu": {"usage": 5.0}, "identity": "Home"})
plain.adjustSize(); plain.grab()
before = files()

if stage == "emoji":   # a Wi-Fi name with an emoji in the hover card
    card = UsageFlyout(i18n, hardware={"cpu": {"usage": 5.0}, "identity": "Home\U0001F3E0 ❤️"})
    card.adjustSize(); card.grab()
    label = card._hw_values[("identity", "text")]
    out["emoji_new"] = sorted(set(files()) - set(before))
    out["emoji_name"] = label.accessibleName()
    card.update_hardware({"cpu": {"usage": 5.0}, "identity": "Office"})
    out["emoji_back"] = [label.text(), label.textFormat() == Qt.TextFormat.RichText, label.accessibleName()]

if stage == "tooltip":   # the app row's tooltip (drawn as QToolTip draws it: a label in the app font)
    from netspeedtray.views.monitor.network.app_list import AppRow
    row = AppRow(i18n)
    proc = "微信.exe"
    row.update_row({"identity_key": proc, "display_name": proc, "conn_count": 3, "established_count": 2,
                    "host_count": 1, "pids": [42]}, 5)
    tip = QLabel(row.toolTip()); tip.adjustSize(); tip.grab()
    out["tooltip"] = files()

if stage == "combo":   # the GPU picker with a Chinese GPU name picked, its list opened; then reloaded
    from netspeedtray.utils.components import Win11ComboBox
    combo = Win11ComboBox(); combo.setMinimumWidth(200)
    combo.addItem(i18n.GPU_ADAPTER_AUTO, "")
    for name in ("Intel(R) UHD Graphics 770", "Microsoft 基本显示适配器"):
        su.add_combo_item_in_face(combo, name, name)
    combo.setCurrentIndex(2)
    su.keep_combo_face(combo)
    combo.show(); app.processEvents()
    combo.showPopup(); app.processEvents(); combo.view().window().grab()
    out["popup"] = files()
    combo.hidePopup(); app.processEvents()
    out["cjk_width"] = combo.width()
    policy = combo.sizeAdjustPolicy()
    combo.clear()
    combo.addItem(i18n.GPU_ADAPTER_AUTO, "")
    su.add_combo_item_in_face(combo, "Intel(R) UHD Graphics 770", "x")
    combo.setCurrentIndex(1)
    su.keep_combo_face(combo)
    out["reload_policy"] = [policy.name, combo.sizeAdjustPolicy().name, combo.minimumWidth()]
print(json.dumps(out), flush=True)
os._exit(0)
"""


LANGS = ["bn_BD", "he_IL", "en_US"]


def _probe(lang: str, stage: str, appdata) -> dict:
    """One stage in its own clean process: a stage that loads every family must not fail the next."""
    if not sys.platform.startswith("win"):
        pytest.skip("Windows fonts (DirectWrite)")
    env = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8",
               APPDATA=str(appdata))
    res = subprocess.run([sys.executable, "-c", _PROBE, lang, stage], env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=180)
    assert res.returncode == 0, res.stderr[-2000:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    missing = sorted(f for f, ok in out["faces"].items() if not ok)
    if missing:
        pytest.skip(f"not installed here: {missing}")
    return out


@pytest.mark.native_desktop
@pytest.mark.parametrize("lang", LANGS)
def test_an_emoji_wifi_name_loads_only_the_emoji_face(lang, tmp_path):
    out = _probe(lang, "emoji", tmp_path)
    assert out["emoji_new"] == ["seguiemj.ttf"], f"{lang}: {len(out['emoji_new'])} font files mapped"
    assert out["emoji_name"] == "Home\U0001F3E0 ❤️"   # screen readers get the words, not markup
    # A name without an emoji afterwards: plain text again.
    assert out["emoji_back"] == ["Office", False, ""]


@pytest.mark.native_desktop
@pytest.mark.parametrize("lang", LANGS)
def test_the_app_row_tooltip_draws_the_process_name_in_its_face(lang, tmp_path):
    out = _probe(lang, "tooltip", tmp_path)
    assert len(out["tooltip"]) < EVERY_FAMILY, f"{lang}: {len(out['tooltip'])} font files - every family"


@pytest.mark.native_desktop
@pytest.mark.parametrize("lang", LANGS)
def test_the_gpu_picker_with_a_chinese_name(lang, tmp_path):
    out = _probe(lang, "combo", tmp_path)
    assert len(out["popup"]) < EVERY_FAMILY, f"{lang}: {len(out['popup'])} font files - every family"
    # As wide as its widest item needs, not a character count (267 px for these items until 2.2.1).
    assert out["cjk_width"] == 200, out["cjk_width"]
    # Reloaded without a Chinese name: the box's own sizing again.
    assert out["reload_policy"] == ["AdjustToMinimumContentsLengthWithIcon", "AdjustToContentsOnFirstShow", 200]


def test_offscreen_tooltip_is_the_plain_sentence():
    """No face can be seen offscreen: the tooltip is the formatted sentence, as before."""
    from netspeedtray.utils import styles as su
    tip = su.tooltip_with_text_in_face("{name} - {conn} connections", "name", name="chrome.exe", conn=3)
    assert tip == "chrome.exe - 3 connections"


def test_tooltip_spans_only_the_system_text(monkeypatch):
    """A sentence no one face has: only the process name is a span in its face, the rest escaped text in
    the application face."""
    from netspeedtray.utils import styles as su
    monkeypatch.setattr(su, "text_face",
                        lambda t: "Microsoft YaHei UI" if t == "微信.exe" else None)
    tip = su.tooltip_with_text_in_face("{name} - {conn} חיבורים <b>", "name", name="微信.exe", conn=3)
    assert tip == ("<span style=\"white-space:pre-wrap;\"><span style=\"font-family:'Microsoft YaHei UI';\">"
                   "微信.exe</span> - 3 חיבורים &lt;b&gt;</span>")


@pytest.fixture
def rtl_app():
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    old = app.layoutDirection()
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    yield app
    app.setLayoutDirection(old)


def test_dropdown_button_padding_is_mirrored_right_to_left(rtl_app):
    """Qt mirrors the chevron (subcontrol-position) in a right-to-left UI but not QSS padding: the label
    covered the chevron - the Hebrew Monitor's adapter button showed no dropdown arrow."""
    from PyQt6.QtCore import Qt
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.utils import styles as su
    from netspeedtray.views.monitor.network.header import NetworkHeader
    from netspeedtray.views.monitor.timeline_selector import TimelineSelector
    assert su.menu_button_padding() == "padding: 4px 12px 4px 32px;"
    i18n = I18nStrings("he_IL")
    head = NetworkHeader(i18n, "TIMELINE_24_HOURS")
    for btn in (head._iface_btn, TimelineSelector(i18n)._btn):
        assert "padding: 4px 12px 4px 32px;" in btn.styleSheet()
    rtl_app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    assert su.menu_button_padding() == "padding: 4px 32px 4px 12px;"
