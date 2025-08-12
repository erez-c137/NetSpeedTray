# src/netspeedtray/utils/components.py

"""
Custom Qt Widget Components for NetSpeedTray.

This module provides custom UI widgets like Win11Toggle and Win11Slider,
designed to offer a look and feel consistent with Windows 11 modern UI elements.
These components are used throughout the application's settings interfaces
to provide a cohesive user experience.
"""

import logging
from typing import Optional, Final
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QCheckBox, QLabel, QSlider, 
    QSizePolicy, QStyleOption
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtGui import QFont, QPaintEvent, QPainter 
from netspeedtray.utils.styles import toggle_style, slider_style
from netspeedtray import constants


logger = logging.getLogger("NetSpeedTray.Components")


class Win11Toggle(QWidget):
    """
    A custom toggle switch widget. Intended to be compact.
    If label_text is provided, it's shown to the left of the switch.
    For aligned switches in a list, parent layout should handle descriptive labels
    and use this widget with an empty label_text.
    """
    toggled = pyqtSignal(bool)

    _OUTER_TRACK_WIDTH: Final[int] = constants.ui.visuals.TOGGLE_TRACK_WIDTH
    _OUTER_TRACK_HEIGHT: Final[int] = constants.ui.visuals.TOGGLE_TRACK_HEIGHT
    _THUMB_DIAMETER: Final[int] = constants.ui.visuals.TOGGLE_THUMB_DIAMETER
    
    _THUMB_TRAVEL_PADDING: Final[int] = (_OUTER_TRACK_HEIGHT - _THUMB_DIAMETER) // 2
    
    _START_X_POS: Final[int] = _THUMB_TRAVEL_PADDING
    _END_X_POS: Final[int] = _OUTER_TRACK_WIDTH - _THUMB_DIAMETER - _THUMB_TRAVEL_PADDING

    def __init__(self, label_text: str = "", initial_state: bool = False, 
                 parent: Optional[QWidget] = None, label_text_color: Optional[str] = None) -> None:
        super().__init__(parent)
        self._is_checked: bool = initial_state
        self.label_text: str = label_text 
        self._label_widget: Optional[QLabel] = None
        self._label_text_color: Optional[str] = label_text_color

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Explicitly set border and outline to none for the QWidget container
        self.setStyleSheet("background-color: transparent; border: none; outline: none;") 
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._setup_ui() 
        
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(self._is_checked)
        self.checkbox.blockSignals(False)
        self._update_thumb_position(animate=False)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)

    def _create_label_widget(self, text: str) -> QLabel:
        label = QLabel(text, self)
        font = QFont("Segoe UI Variable", 10)
        label.setFont(font)
        label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Ensure internal labels also have no border/outline
        label_qss = "background:transparent; border:none; outline: none;"
        if self._label_text_color:
            label_qss += f" color: {self._label_text_color};"
        label.setStyleSheet(label_qss)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        return label

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0) 
        layout.setSpacing(12)

        if self.label_text.strip():
            self._label_widget = self._create_label_widget(self.label_text)
            layout.addWidget(self._label_widget)

        self.toggle_visual_container = QWidget(self) 
        self.toggle_visual_container.setFixedSize(self._OUTER_TRACK_WIDTH, self._OUTER_TRACK_HEIGHT)
        self.toggle_visual_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Explicitly set border and outline to none for the internal visual container
        self.toggle_visual_container.setStyleSheet("background:transparent; border: none; outline: none;") 

        self.checkbox = QCheckBox(self.toggle_visual_container)
        self.checkbox.setFixedSize(self._OUTER_TRACK_WIDTH, self._OUTER_TRACK_HEIGHT)
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        # toggle_style handles ::indicator. Ensure QCheckBox base has no outline.
        checkbox_qss = toggle_style(self._OUTER_TRACK_WIDTH, self._OUTER_TRACK_HEIGHT) 
        checkbox_qss += " QCheckBox { background-color: transparent; border: none; outline: none; padding: 0px; margin: 0px; }"
        self.checkbox.setStyleSheet(checkbox_qss)

        self.thumb = QWidget(self.toggle_visual_container)
        self.thumb.setFixedSize(self._THUMB_DIAMETER, self._THUMB_DIAMETER)
        # Thumb style should not include an outline by default, only its own border
        self.thumb.setStyleSheet(f"""
            QWidget {{
                background-color: white; 
                border-radius: {self._THUMB_DIAMETER // 2}px; 
                border: 1px solid #B0B0B0; 
                outline: none;
            }}
        """)
        self.thumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.thumb_animation = QPropertyAnimation(self.thumb, b"pos", self)
        self.thumb_animation.setDuration(120) 
        self.thumb_animation.setEasingCurve(QEasingCurve.Type.InOutSine)

        self.checkbox.toggled.connect(self._on_checkbox_toggled)
        
        layout.addWidget(self.toggle_visual_container)
        
        self.setLayout(layout)
        self._update_minimum_height_and_adjust_size()

    def _update_minimum_height_and_adjust_size(self) -> None:
        content_min_h = self._OUTER_TRACK_HEIGHT
        if self._label_widget and self._label_widget.isVisible():
            content_min_h = max(content_min_h, self._label_widget.sizeHint().height())
        
        margins = self.layout().contentsMargins()
        self.setMinimumHeight(content_min_h + margins.top() + margins.bottom())
        self.adjustSize()

    def _update_thumb_position(self, animate: bool = True) -> None:
        target_x = self._END_X_POS if self._is_checked else self._START_X_POS
        target_y_pos = self._THUMB_TRAVEL_PADDING 
        end_pos = QPoint(target_x, target_y_pos)
        current_pos = self.thumb.pos()

        if current_pos == end_pos and not self.thumb_animation.state() == QPropertyAnimation.State.Running:
            return
        self.thumb_animation.stop() 
        if animate:
            self.thumb_animation.setStartValue(current_pos)
            self.thumb_animation.setEndValue(end_pos)
            self.thumb_animation.start()
        else:
            self.thumb.move(end_pos)

    def _on_checkbox_toggled(self, checked: bool) -> None:
        if self._is_checked == checked: return
        self._is_checked = checked
        self._update_thumb_position(animate=True) 
        self.toggled.emit(self._is_checked)

    def isChecked(self) -> bool: return self._is_checked

    def setChecked(self, checked: bool) -> None:
        if self._is_checked == checked:
            self._update_thumb_position(animate=False); return
        self._is_checked = checked
        self.checkbox.blockSignals(True); self.checkbox.setChecked(self._is_checked); self.checkbox.blockSignals(False)
        self._update_thumb_position(animate=False) 
        self.toggled.emit(self._is_checked)

    def setText(self, text: str) -> None:
        self.label_text = text
        current_layout = self.layout()
        if not isinstance(current_layout, QHBoxLayout): 
            logger.error("Win11Toggle: Layout is not QHBoxLayout or not set."); return

        has_internal_label = bool(text.strip())

        if self._label_widget:
            if has_internal_label:
                self._label_widget.setText(text)
                self._label_widget.setVisible(True)
            else: 
                self._label_widget.setVisible(False)
        elif has_internal_label: 
            self._label_widget = self._create_label_widget(text)
            current_layout.insertWidget(0, self._label_widget) 
        
        self._update_minimum_height_and_adjust_size()

    def setLabelTextColor(self, color_hex: str) -> None:
        self._label_text_color = color_hex 
        if self._label_widget:
            label_qss = "background:transparent; border:none; outline: none;" # Ensure outline:none
            if self._label_text_color:
                label_qss += f" color: {self._label_text_color};"
            self._label_widget.setStyleSheet(label_qss)

    def sizeHint(self) -> QSize:
        current_layout = self.layout()
        if not current_layout: return QSize(self._OUTER_TRACK_WIDTH, self._OUTER_TRACK_HEIGHT)

        margins = current_layout.contentsMargins()
        spacing = current_layout.spacing()

        content_width = self._OUTER_TRACK_WIDTH
        content_height = self._OUTER_TRACK_HEIGHT

        if self._label_widget and self._label_widget.isVisible():
            label_sh = self._label_widget.sizeHint()
            content_width += label_sh.width() + spacing
            content_height = max(content_height, label_sh.height())
        
        total_width = content_width + margins.left() + margins.right()
        total_height = content_height + margins.top() + margins.bottom()
        
        return QSize(total_width, total_height)

class Win11Slider(QWidget):
    """
    A custom slider widget styled to resemble Windows 11's sliders.
    It consists of a QSlider for the sliding mechanism and a QLabel
    to display the current value or associated text.
    """
    valueChanged = pyqtSignal(int)
    sliderReleased = pyqtSignal()
    
    def __init__(self, min_value: int = 0, max_value: int = 100, value: int = 0, 
                 page_step: int = 1, has_ticks: bool = False, 
                 parent: Optional[QWidget] = None, value_label_text_color: Optional[str] = None) -> None:
        super().__init__(parent)
        self._value_label: Optional[QLabel] = None
        self._value_label_text_color: Optional[str] = value_label_text_color
        
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Explicitly set border and outline to none for the QWidget container
        self.setStyleSheet("background:transparent; border: none; outline: none;")
        self._setup_ui(min_value, max_value, value, page_step, has_ticks)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)

    def _setup_ui(self, min_val: int, max_val: int, initial_val: int, page_step: int, has_ticks: bool) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0) 
        layout.setSpacing(8)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimumWidth(100) 
        self.slider.setMaximumWidth(160) 
        
        # Apply base styling to QSlider itself to remove default border/outline, then add specific styles
        qslider_base_style = "QSlider { border: none; outline: none; background: transparent; }"
        self.slider.setStyleSheet(qslider_base_style + slider_style())
        
        self.slider.setRange(min_val, max_val)
        self.slider.setValue(initial_val)
        self.slider.setPageStep(page_step) 
        self.slider.setSingleStep(1)

        if has_ticks:
            self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            tick_interval = max(1, (max_val - min_val) // 10 if (max_val - min_val) > 0 else 1)
            if (max_val - min_val) > 0 and (max_val - min_val) / max(1, tick_interval) > 15:
                tick_interval = max(1, (max_val - min_val) // 15)
            self.slider.setTickInterval(tick_interval)

        self._value_label = QLabel(self) 
        font = QFont("Segoe UI Variable", 10) 
        self._value_label.setFont(font)
        self._value_label.setMinimumWidth(70) 
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._value_label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._value_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        # Ensure value label also has no border/outline
        label_qss = "background:transparent; border:none; outline: none;"
        if self._value_label_text_color: 
            label_qss += f" color: {self._value_label_text_color};"
        self._value_label.setStyleSheet(label_qss)
        self.setValueText(str(initial_val)) 

        layout.addWidget(self.slider)
        layout.addWidget(self._value_label)
        self.setLayout(layout)

        self.slider.valueChanged.connect(self._on_internal_slider_value_changed)
        self.slider.sliderReleased.connect(self._on_internal_slider_released)

    def _on_internal_slider_value_changed(self, value: int) -> None: self.valueChanged.emit(value)
    def _on_internal_slider_released(self) -> None: self.sliderReleased.emit()

    def setRange(self, min_val: int, max_val: int) -> None:
        self.slider.setRange(min_val, max_val)
        if self.slider.tickPosition() != QSlider.TickPosition.NoTicks:
            tick_interval = max(1, (max_val - min_val) // 10 if (max_val - min_val) > 0 else 1)
            if (max_val - min_val) > 0 and (max_val - min_val) / max(1, tick_interval) > 15:
                tick_interval = max(1, (max_val - min_val) // 15)
            self.slider.setTickInterval(tick_interval)

    def setValue(self, value: int) -> None: self.slider.setValue(value)
    def setSingleStep(self, step: int) -> None: self.slider.setSingleStep(step)
    def setPageStep(self, step: int) -> None: self.slider.setPageStep(step)
    def setTickInterval(self, ti: int) -> None: self.slider.setTickInterval(ti)
    def setTickPosition(self, position: QSlider.TickPosition) -> None: self.slider.setTickPosition(position)
    def value(self) -> int: return self.slider.value()

    def setValueText(self, text: str) -> None:
        if self._value_label: self._value_label.setText(text)
        else: logger.error("Win11Slider: _value_label not initialized when trying to set text.")

    def setValueLabelTextColor(self, color_hex: str) -> None:
        self._value_label_text_color = color_hex 
        if self._value_label:
            label_qss = "background:transparent; border:none; outline: none;" # Ensure outline:none
            if self._value_label_text_color:
                label_qss += f" color: {self._value_label_text_color};"
            self._value_label.setStyleSheet(label_qss)

    def sizeHint(self) -> QSize:
        slider_sh = self.slider.sizeHint()
        label_sh = self._value_label.sizeHint() if self._value_label else QSize(70, 20) 
        
        current_layout = self.layout()
        if not isinstance(current_layout, QHBoxLayout): 
            return QSize(slider_sh.width() + 70 + 8, max(slider_sh.height(), 20, 28))

        margins = current_layout.contentsMargins()
        spacing = current_layout.spacing()
        
        width = slider_sh.width() + spacing + label_sh.width() + margins.left() + margins.right()
        height = max(slider_sh.height(), label_sh.height()) + margins.top() + margins.bottom()
        
        return QSize(width, max(height, 28))