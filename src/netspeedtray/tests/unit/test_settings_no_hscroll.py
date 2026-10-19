"""
No Settings page may scroll sideways, in any language, at any window width the dialog allows.

Long translations used to force a horizontal scrollbar: SettingCard titles never wrapped and sat
beside fixed-width controls (a 260 px slider), the 7-segment arrow picker and the two Reset buttons
were one rigid row. Cards now wrap their title and drop the control under it when narrow, the arrow
picker wraps onto two rows, the Reset buttons stack, and the dialog's minimum width is the widest
page's floor (and the footer's). The sidebar widens to its longest page name (de_DE scrolled too).

The page check runs in a subprocess because it needs real Windows font metrics: the suite's offscreen
platform has no font directory, so its fallback font measures text roughly twice as wide as Segoe UI,
and pointing the whole suite at C:\\Windows\\Fonts changes other tests' rendering.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from PyQt6.QtWidgets import QPushButton, QSlider, QWidget

from netspeedtray.utils.components import ReflowRow, SettingCard, Win11Segmented

SRC_DIR = Path(__file__).resolve().parents[3]          # .../src
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# en_US is the baseline, de_DE the reported case, hu_HU the widest on main (its Hardware page ran
# 176 px past a 640 px window; it_IT 169, es_ES 156 - all 16 locales were swept by hand).
LOCALES = ["en_US", "de_DE", "hu_HU"]

_PROBE = textwrap.dedent(r"""
    import json, sys
    from unittest.mock import MagicMock
    from PyQt6.QtCore import QPoint
    from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
    app = QApplication(sys.argv)
    for n in ("critical", "information", "warning"):
        setattr(QMessageBox, n, staticmethod(lambda *a, **k: None))
    from netspeedtray import constants
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.views.settings.dialog import SettingsDialog

    def overflow(scroll):
        vp = scroll.viewport()
        worst = 0
        if scroll.horizontalScrollBar().maximum() > 0:
            worst = scroll.horizontalScrollBar().maximum()
        for w in scroll.widget().findChildren(QWidget):
            if w.isVisibleTo(scroll.widget()) and w.width() > 0:
                worst = max(worst, w.mapTo(vp, QPoint(w.width(), 0)).x() - vp.width())
        return worst

    out = []
    for lang in sys.argv[1].split(","):
        cfg = dict(constants.config.defaults.DEFAULT_CONFIG)
        cfg["language"] = lang
        # The Integrations rows (port, URL + Copy, status) only show while the endpoint is on.
        cfg["metrics_endpoint_enabled"] = True
        parent = MagicMock(); parent.config = {}; parent.config_manager = MagicMock()
        dlg = SettingsDialog(main_widget=parent, config=cfg, version="0", i18n=I18nStrings(lang),
                             available_interfaces=["Ethernet", "Wi-Fi"], is_startup_enabled=False)
        # ...and the longest status line it can show (a port-in-use error).
        from netspeedtray.core.metrics_endpoint import EndpointStatus
        dlg.advanced_page.set_metrics_endpoint_status(EndpointStatus(
            constants.network.metrics.STATE_ERROR, constants.network.metrics.DEFAULT_PORT, "in_use"))
        assert dlg.advanced_page._metrics_url_card.isVisibleTo(dlg.advanced_page)
        dlg.show()
        for label, width in (("default", dlg.width()), ("minimum", dlg.minimumWidth())):
            dlg.resize(width, dlg.height())
            for _ in range(5):
                app.processEvents()
            if dlg.sidebar.horizontalScrollBar().maximum() > 0:
                out.append(f"{lang} {label} {dlg.width()}px sidebar scrolls sideways")
            for b in (dlg.export_log_button, dlg.cancel_button, dlg.save_button):
                if b.width() < b.sizeHint().width():
                    out.append(f"{lang} {label} {dlg.width()}px footer button clipped: {b.text()!r}")
            for i in range(dlg.stack.count()):
                dlg.navigate_to_page(i)
                for _ in range(5):
                    app.processEvents()
                px = overflow(dlg.stack.widget(i))
                if px > 1:
                    out.append(f"{lang} {label} {dlg.width()}px page {i} "
                               f"({type(dlg.stack.widget(i).widget()).__name__}): {px}px past the edge")
        dlg.close()
        # Delete each dialog while the QApplication is alive: left to interpreter teardown, the
        # orphaned dialogs crashed the child at exit (0xC0000005) about 1 run in 4, and the
        # unflushed RESULT line went down with it - an empty, flaky "probe failed" on CI.
        dlg.deleteLater()
        for _ in range(5):
            app.processEvents()
    print("RESULT" + json.dumps(out), flush=True)
""")


@pytest.mark.skipif(sys.platform != "win32" or not FONT_DIR.is_dir(),
                    reason="needs the Windows font directory for real text metrics")
def test_no_settings_page_scrolls_sideways(tmp_path):
    env = dict(os.environ)
    env.update({
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QPA_FONTDIR": str(FONT_DIR),
        "QT_SCALE_FACTOR": "1",
        "APPDATA": str(tmp_path),          # never the real %APPDATA%\NetSpeedTray
        "PYTHONPATH": str(SRC_DIR),
        "PYTHONIOENCODING": "utf-8",
    })
    proc = subprocess.run([sys.executable, "-c", _PROBE, ",".join(LOCALES)], env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")]
    assert lines, (f"probe failed (returncode {proc.returncode}):\n"
                   f"{proc.stdout[-2000:]}\n{proc.stderr[-4000:]}")
    offenders = json.loads(lines[-1][len("RESULT"):])
    assert offenders == [], "Settings pages that scroll sideways:\n" + "\n".join(offenders)


# --- the primitives that make it so (in-process; widths are relative, so any font works) ---------

def _wide_control(width: int) -> QWidget:
    s = QSlider()
    s.setMinimumWidth(width)
    return s


def _hosted(qtbot, widget: QWidget) -> QWidget:
    """Parent the widget in a plain host: a top-level window's layout pins it to the side-by-side
    minimum, but inside a page (here: a layout-less host) only minimumSizeHint applies."""
    host = QWidget()
    qtbot.addWidget(host)
    widget.setParent(host)
    host.resize(1200, 300)
    host.show()
    widget._test_host = host   # keep the host (and so the widget) alive for the test
    return widget


def test_setting_card_stacks_its_control_when_narrow(qtbot):
    card = _hosted(qtbot, SettingCard("Aktualisierungsintervall der Hardwarestatistiken",
                                      control=_wide_control(260)))
    floor = card.minimumSizeHint().width()
    # The floor is the stacked width (widest single piece), not title + control side by side.
    assert floor < card._side_by_side_width()
    assert floor >= 260

    card.resize(floor, 120)
    qtbot.waitUntil(card.isStacked)
    assert card._title_label.wordWrap()

    card.resize(card._side_by_side_width() + 40, 120)
    qtbot.waitUntil(lambda: not card.isStacked())


def test_setting_card_without_control_never_stacks(qtbot):
    card = _hosted(qtbot, SettingCard("Title only"))
    card.resize(60, 60)
    assert not card.isStacked()


def test_wrapping_segmented_breaks_onto_two_rows(qtbot):
    seg = _hosted(qtbot, Win11Segmented([(f"Option {i}", i) for i in range(7)], wrap=True))
    one_row = seg._row_width(1)
    assert seg.minimumSizeHint().width() < one_row
    seg.resize(seg.minimumSizeHint().width(), 80)
    qtbot.waitUntil(lambda: seg._rows == 2)
    seg.resize(one_row + 10, 80)
    qtbot.waitUntil(lambda: seg._rows == 1)
    seg.setValue(6)                       # still one exclusive group across rows
    assert seg.value() == 6


def test_plain_segmented_does_not_wrap(qtbot):
    seg = Win11Segmented([(f"Option {i}", i) for i in range(7)])
    qtbot.addWidget(seg)
    assert seg.minimumSizeHint().width() >= sum(b.sizeHint().width() for _v, b in seg._items)


def test_reflow_row_stacks_buttons_when_narrow(qtbot):
    a, b = QPushButton("Reset this page to defaults"), QPushButton("Reset everything to defaults")
    row = _hosted(qtbot, ReflowRow([a, b]))
    assert row.minimumSizeHint().width() < row._one_row_width()
    row.resize(row.minimumSizeHint().width(), 80)
    qtbot.waitUntil(row.isStacked)
    row.resize(row._one_row_width() + 20, 80)
    qtbot.waitUntil(lambda: not row.isStacked())
