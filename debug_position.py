import sys
sys.path.insert(0, "src")
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from netspeedtray.utils.taskbar_utils import get_taskbar_info

tb = get_taskbar_info()
dpi = tb.dpi_scale
screen = tb.get_screen()

print(f"DPI: {dpi}")
print(f"Screen geometry: {screen.geometry()}")
print(f"Screen available geometry: {screen.availableGeometry()}")
print(f"Taskbar rect (physical): {tb.rect}")
print(f"Taskbar rect (logical): top={tb.rect[1]/dpi:.0f} bottom={tb.rect[3]/dpi:.0f}")
print(f"Work area (physical): {tb.work_area}")

avail = screen.availableGeometry()
visible_taskbar_top = avail.bottom() + 1
visible_taskbar_height = screen.geometry().bottom() + 1 - visible_taskbar_top
print(f"\nVisible taskbar top (logical): {visible_taskbar_top}")
print(f"Visible taskbar height (logical): {visible_taskbar_height}")
print(f"Shell_TrayWnd top (logical): {tb.rect[1]/dpi:.0f}")
print(f"Invisible overhead: {visible_taskbar_top - tb.rect[1]/dpi:.0f}px")
