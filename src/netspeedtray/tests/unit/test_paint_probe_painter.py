"""The right-align probe in widget_paint._draw_content must end its QPainter even when a draw raises.

Qt aborts the whole process when a QImage is freed while a QPainter is still active on it, so a probe
draw that raised (caught by the probe's own ``except``) used to leave ``mp`` active and kill the app a
moment later - in the live widget, a mirror or the Settings preview, in side_by_side / cycle mode on a
top/bottom taskbar. The abort can't be caught in-process, so the scenario runs in a child interpreter.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]

SCRIPT = textwrap.dedent("""
    import gc, sys
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QImage, QPainter, QColor
    app = QApplication(sys.argv)
    from netspeedtray import constants
    from netspeedtray.constants.i18n import I18nStrings
    from netspeedtray.tests.color_helpers import themed
    from netspeedtray.utils import widget_paint
    from netspeedtray.utils.widget_paint import WidgetMetrics, font_from_config, render_widget
    from netspeedtray.utils.widget_renderer import WidgetRenderer

    real = widget_paint._draw_foreground
    calls = {"n": 0}
    def flaky(painter, *a, **k):
        calls["n"] += 1
        if calls["n"] % 2 == 1:          # every probe draw raises; the real draw after it works
            raise RuntimeError("probe draw failed")
        return real(painter, *a, **k)
    widget_paint._draw_foreground = flaky

    cfg = themed(dict(constants.config.defaults.DEFAULT_CONFIG, widget_display_mode="side_by_side",
                      graph_enabled=False))
    for _ in range(50):
        renderer = WidgetRenderer(cfg, I18nStrings("en_US"))
        img = QImage(240, 48, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        try:
            render_widget(p, QRect(0, 0, 240, 48), renderer, renderer.config,
                          WidgetMetrics(upload_mbps=1.0, download_mbps=2.0), font=font_from_config(cfg))
        finally:
            p.end()
        gc.collect()
    assert calls["n"] >= 50, calls
    print("survived")
""")


def test_a_probe_draw_that_raises_does_not_abort_the_process():
    env = dict(os.environ, PYTHONPATH=str(SRC), QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-c", SCRIPT], env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0 and "survived" in proc.stdout, (proc.returncode, proc.stdout[-2000:],
                                                                 proc.stderr[-2000:])
