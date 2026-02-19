import sys, math
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QFontMetrics
app = QApplication(sys.argv)

from netspeedtray.utils.taskbar_utils import get_taskbar_info, is_small_taskbar
from netspeedtray import constants

tb = get_taskbar_info()
dpi = tb.dpi_scale
tb_height_phys = tb.rect[3] - tb.rect[1]
tb_height_log = tb_height_phys / dpi

print(f"=== Display Info ===")
for screen in app.screens():
    print(f"Screen: {screen.name()}")
    print(f"  Geometry: {screen.geometry()}")
    print(f"  DPR: {screen.devicePixelRatio()}")

print(f"\n=== Taskbar Info ===")
print(f"HWND: {tb.hwnd}")
print(f"Rect (physical): {tb.rect}")
print(f"DPI scale: {dpi}")
print(f"Height physical: {tb_height_phys}")
print(f"Height logical: {tb_height_log}")
print(f"Is small taskbar: {is_small_taskbar(tb)}")

print(f"\n=== Widget Sizing ===")
widget_height = math.ceil(tb_height_log)
print(f"Widget height (ceil): {widget_height}")

# Simulate font metrics
font = QFont(constants.config.defaults.DEFAULT_FONT_FAMILY, constants.config.defaults.DEFAULT_FONT_SIZE, 63)
metrics = QFontMetrics(font)
line_height = metrics.height()
ascent = metrics.ascent()
total_text_height = line_height * 2
top_y = int((widget_height - total_text_height) / 2 + ascent)
bottom_y = top_y + line_height

print(f"Font: {font.family()} @ {font.pointSize()}pt")
print(f"Line height: {line_height}")
print(f"Ascent: {ascent}")
print(f"Total text height: {total_text_height}")
print(f"top_y (first line baseline): {top_y}")
print(f"bottom_y (second line baseline): {bottom_y}")
print(f"Text block top: {top_y - ascent}")
print(f"Text block bottom: {bottom_y - ascent + line_height}")
print(f"Vertical padding top: {top_y - ascent}")
print(f"Vertical padding bottom: {widget_height - (bottom_y - ascent + line_height)}")

print(f"\n=== Position ===")
y_pos = round((tb.rect[1] + tb.rect[3]) / 2.0 / dpi - widget_height / 2.0)
print(f"Widget Y: {y_pos}")
print(f"Taskbar top (logical): {tb.rect[1] / dpi}")
print(f"Widget bottom: {y_pos + widget_height}")
print(f"Taskbar bottom (logical): {tb.rect[3] / dpi}")
