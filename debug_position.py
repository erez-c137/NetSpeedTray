"""Debug: check tray area position vs taskbar rect."""
import sys, math
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

from netspeedtray.utils.taskbar_utils import get_taskbar_info

tb = get_taskbar_info()
dpi = tb.dpi_scale

print(f"=== Taskbar ===")
print(f"Rect (physical): {tb.rect}")
print(f"Rect (logical): top={tb.rect[1]/dpi:.0f}, bottom={tb.rect[3]/dpi:.0f}, height={( tb.rect[3]-tb.rect[1])/dpi:.0f}")

tray_rect = tb.get_tray_rect()
if tray_rect:
    print(f"\n=== Tray Notification Area ===")
    print(f"Rect (physical): {tray_rect}")
    print(f"Rect (logical): top={tray_rect[1]/dpi:.0f}, bottom={tray_rect[3]/dpi:.0f}, height={(tray_rect[3]-tray_rect[1])/dpi:.0f}")
    
    tray_center_log = (tray_rect[1] + tray_rect[3]) / 2.0 / dpi
    print(f"Tray center (logical): {tray_center_log:.1f}")
    
    tb_center_log = (tb.rect[1] + tb.rect[3]) / 2.0 / dpi
    print(f"Taskbar center (logical): {tb_center_log:.1f}")
    print(f"Offset between centers: {tray_center_log - tb_center_log:.1f}px")
else:
    print("No tray rect found!")

if tb.tasklist_rect:
    print(f"\n=== Tasklist Area ===")
    print(f"Rect (physical): {tb.tasklist_rect}")
    print(f"Rect (logical): top={tb.tasklist_rect[1]/dpi:.0f}, bottom={tb.tasklist_rect[3]/dpi:.0f}")

print(f"\n=== Recommended Fix ===")
if tray_rect:
    tray_h = (tray_rect[3] - tray_rect[1]) / dpi
    print(f"Use tray height ({tray_h:.0f}px) instead of taskbar height ({(tb.rect[3]-tb.rect[1])/dpi:.0f}px)")
    print(f"Center widget on tray center ({tray_center_log:.1f}) instead of taskbar center ({tb_center_log:.1f})")
