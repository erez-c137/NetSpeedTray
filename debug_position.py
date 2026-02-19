import sys, math
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

from netspeedtray.utils.taskbar_utils import get_taskbar_info

tb = get_taskbar_info()
dpi = tb.dpi_scale

print(f"Taskbar HWND: {tb.hwnd}")
print(f"Taskbar rect (physical): {tb.rect}")
print(f"DPI scale: {dpi}")
print(f"Taskbar top (logical): {tb.rect[1] / dpi:.2f}")
print(f"Taskbar bottom (logical): {tb.rect[3] / dpi:.2f}")
print(f"Taskbar height (physical): {tb.rect[3] - tb.rect[1]}")
print(f"Taskbar height (logical): {(tb.rect[3] - tb.rect[1]) / dpi:.2f}")
print(f"Taskbar height (round): {round((tb.rect[3] - tb.rect[1]) / dpi)}")
print(f"Widget height (ceil): {math.ceil((tb.rect[3] - tb.rect[1]) / dpi)}")

wh = math.ceil((tb.rect[3] - tb.rect[1]) / dpi)
center = (tb.rect[1] + tb.rect[3]) / 2.0 / dpi
old_y = round(tb.rect[1] / dpi) + (round((tb.rect[3] - tb.rect[1]) / dpi) - wh) // 2
new_y = round(center - wh / 2.0)

print(f"")
print(f"Old Y position: {old_y}")
print(f"New Y position: {new_y}")
print(f"Difference: {new_y - old_y}")

for screen in app.screens():
    print(f"\nScreen: {screen.name()}")
    print(f"  Geometry: {screen.geometry()}")
    print(f"  DPR: {screen.devicePixelRatio()}")
