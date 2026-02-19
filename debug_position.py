"""Debug: shows widget boundaries with a red border to visualize positioning."""
import sys, math
sys.path.insert(0, "src")

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics
from PyQt6.QtCore import Qt, QTimer
app = QApplication(sys.argv)

from netspeedtray.utils.taskbar_utils import get_taskbar_info
from netspeedtray import constants

tb = get_taskbar_info()
dpi = tb.dpi_scale
tb_height_phys = tb.rect[3] - tb.rect[1]
tb_height_log = tb_height_phys / dpi

print(f"=== Taskbar ===")
print(f"Rect (physical): {tb.rect}")
print(f"DPI: {dpi}")
print(f"Height physical: {tb_height_phys}, logical: {tb_height_log}")

widget_w = 150
widget_h = math.ceil(tb_height_log)
widget_y = round((tb.rect[1] + tb.rect[3]) / 2.0 / dpi - widget_h / 2.0)
widget_x = round(tb.rect[2] / dpi) - widget_w - 400

print(f"Widget: {widget_w}x{widget_h} at ({widget_x}, {widget_y})")

class DebugWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(widget_w, widget_h)
        self.move(widget_x, widget_y)
    
    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        
        print(f"\n=== paintEvent ===")
        print(f"Widget size in paint: {w}x{h}")
        print(f"Device pixel ratio: {self.devicePixelRatio()}")
        
        # Semi-transparent background
        p.fillRect(0, 0, w, h, QColor(40, 40, 40, 200))
        
        # Red border to show widget bounds
        pen = QPen(QColor(255, 0, 0), 2)
        p.setPen(pen)
        p.drawRect(1, 1, w-2, h-2)
        
        # Draw centered text like the real widget
        font = QFont(constants.config.defaults.DEFAULT_FONT_FAMILY, 
                     constants.config.defaults.DEFAULT_FONT_SIZE, 63)
        p.setFont(font)
        metrics = QFontMetrics(font)
        
        line_height = metrics.height()
        ascent = metrics.ascent()
        total_text_height = line_height * 2
        top_y = int((h - total_text_height) / 2 + ascent)
        bottom_y = top_y + line_height
        
        print(f"Font metrics - height: {line_height}, ascent: {ascent}")
        print(f"Total text height: {total_text_height}")
        print(f"top_y: {top_y}, bottom_y: {bottom_y}")
        print(f"Visual top: {top_y - ascent}, Visual bottom: {bottom_y - ascent + line_height}")
        print(f"Padding - top: {top_y - ascent}, bottom: {h - (bottom_y - ascent + line_height)}")
        
        # Green horizontal line at widget center
        p.setPen(QPen(QColor(0, 255, 0), 1))
        p.drawLine(0, h // 2, w, h // 2)
        
        # Draw speed text
        p.setPen(QColor(200, 200, 200))
        p.drawText(8, top_y, "↑ 65.1 MB/s")
        p.drawText(8, bottom_y, "↓ 0.1 MB/s")
        
        p.end()

widget = DebugWidget()
widget.show()

# Auto-close after 10 seconds
QTimer.singleShot(10000, app.quit)
print("\nWidget visible for 10 seconds. Check alignment vs taskbar...")
app.exec()
