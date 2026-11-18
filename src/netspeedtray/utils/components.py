"""
Custom Qt Widget Components for NetSpeedTray.

This module provides custom UI widgets like Win11Toggle and Win11Slider,
designed to offer a look and feel consistent with Windows 11 modern UI elements.
These components are used throughout the application's settings interfaces
to provide a cohesive user experience.
"""

import logging
import re
from typing import Callable, Optional, Final
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QBoxLayout, QCheckBox, QLabel, QSlider,
    QSizePolicy, QStyle, QStyleOption, QLineEdit, QFrame, QToolButton, QButtonGroup,
    QPushButton, QColorDialog, QComboBox,
    QApplication, QScrollArea, QAbstractSlider, QAbstractSpinBox, QAbstractButton
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QPropertyAnimation, QEasingCurve, QPoint, QTimer, QObject, QEvent
from PyQt6.QtGui import QFont, QPaintEvent, QPainter, QColor, QIcon
from netspeedtray.utils.styles import (
    toggle_style, slider_style, font as token_font, semantic_colors, get_accent_color,
    prefers_reduced_motion, is_dark_mode, ui_font, ui_family_css, icon_font_family, symbol_face, set_ui_text, face_for_text, set_text_in_face, apply_text_face,
)
from netspeedtray.constants.styles import styles as tokens
from netspeedtray import constants


logger = logging.getLogger("NetSpeedTray.Components")


class _WheelGuard(QObject):
    """Stops the mouse wheel from changing a scroll-sensitive control the cursor merely hovers.

    Sliders, spin boxes and combo boxes normally eat the wheel to change their value, so scrolling
    a long settings page silently nudges whatever you happen to scroll past. This filter forwards the
    wheel to the enclosing QScrollArea (so the page scrolls) whenever the control is NOT focused. A
    control the user has clicked/tabbed into keeps normal wheel behavior.
    """
    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(obj, QWidget) and not obj.hasFocus():
            sa = obj.parentWidget()
            while sa is not None and not isinstance(sa, QScrollArea):
                sa = sa.parentWidget()
            if isinstance(sa, QScrollArea):
                QApplication.sendEvent(sa.verticalScrollBar(), event)
            return True   # never let an unfocused control consume the wheel itself
        return False


_WHEEL_GUARD = None   # single shared, stateless instance - created on first use


def _wheel_guard() -> _WheelGuard:
    """The shared guard, (re)created when missing. A module-level QObject made at import dies with
    the QApplication that owned it; anything that later builds a settings page in the same process
    (a second QApplication in the test run) then crashed in installEventFilter."""
    global _WHEEL_GUARD
    from PyQt6 import sip
    if _WHEEL_GUARD is None or sip.isdeleted(_WHEEL_GUARD):
        _WHEEL_GUARD = _WheelGuard()
    return _WHEEL_GUARD


def install_wheel_guard(root: QWidget) -> None:
    """Install the wheel guard on every scroll-sensitive control under `root`.

    Call once on a page/dialog (e.g. after building a settings page): scrolling the page then
    scrolls it, instead of changing the slider/combo/spin box the cursor passes over. Covers the
    custom Win11 controls too, since Win11ComboBox is a QComboBox and Win11Slider wraps a QSlider.
    """
    guard = _wheel_guard()
    for cls in (QComboBox, QAbstractSpinBox, QAbstractSlider):
        for w in root.findChildren(cls):
            w.installEventFilter(guard)
            # Drop wheel-focus so a scroll can't first focus the control and then change it.
            if w.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class Win11ComboBox(QComboBox):
    """A drop-in QComboBox whose pop-up list is guaranteed opaque.

    Qt 6 gives every combo pop-up a *translucent* container window (``WA_TranslucentBackground``, used
    for the native rounded drop-shadow). Under our dark theme the styled item-view didn't actually
    cover that container on screen, so the open list rendered see-through over whatever it overlapped -
    unreadable. We force both the container window and its viewport opaque each time the pop-up opens
    (the container is created lazily on the first show and then reused). Colors still come from the
    QSS (``QComboBox QAbstractItemView``); this only removes the transparency. Use everywhere a
    settings combo is needed, exactly like a plain QComboBox."""

    def showPopup(self) -> None:
        super().showPopup()
        # The container is only fully realised after this event-loop turn, so apply once now (handles a
        # reused, already-realised container) and once deferred (handles the very first open).
        self._force_opaque_popup()
        QTimer.singleShot(0, self._force_opaque_popup)

    def _force_opaque_popup(self) -> None:
        try:
            win = self.view().window()
            win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
            win.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
            vp = self.view().viewport()
            vp.setAutoFillBackground(True)
            bg = tokens.COMBOBOX_BG_DARK if is_dark_mode() else tokens.COMBOBOX_BG_LIGHT
            pal = vp.palette()
            pal.setColor(vp.backgroundRole(), QColor(bg))
            vp.setPalette(pal)
            win.update()
        except Exception:
            pass  # styling only; never block the pop-up


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
        # No text in the constructor: an adapter name (以太网) laid out in the UI face first makes Qt
        # search every installed family. The text goes in last, in its own face (set_text_in_face).
        label = QLabel(self)
        # The UI face (#149: never an unintended serif fallback), ONE family - a family list loads every
        # installed font family (utils/styles.ui_family). Small cut: what this label drew in until 2.2.0.
        label.setFont(ui_font(point_size=10, optical="Small"))
        label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Ensure internal labels also have no border/outline
        label_qss = "background:transparent; border:none; outline: none;"
        if self._label_text_color:
            label_qss += f" color: {self._label_text_color};"
        label.setStyleSheet(label_qss)
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        set_text_in_face(label, text)
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
        self.toggle_visual_container.setStyleSheet("background:transparent; border: none; outline: none;") 

        # A layout is created for the container. This is the crucial fix.
        # It ensures all child widgets are properly managed by Qt's layout system.
        container_layout = QHBoxLayout(self.toggle_visual_container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        self.checkbox = QCheckBox() # No parent needed here, the layout will set it.
        self.checkbox.setFixedSize(self._OUTER_TRACK_WIDTH, self._OUTER_TRACK_HEIGHT)
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        checkbox_qss = toggle_style(self._OUTER_TRACK_WIDTH, self._OUTER_TRACK_HEIGHT) 
        checkbox_qss += " QCheckBox { background-color: transparent; border: none; outline: none; padding: 0px; margin: 0px; }"
        checkbox_qss += self._focus_ring_qss()
        self.checkbox.setStyleSheet(checkbox_qss)
        self.checkbox.installEventFilter(self)
        if self.label_text.strip():
            # The visible label is a separate QLabel; the focusable checkbox carries the name.
            self.checkbox.setAccessibleName(self.label_text.strip())

        # The checkbox is now added to the container's layout.
        container_layout.addWidget(self.checkbox)

        self.thumb = QWidget(self.toggle_visual_container)
        self.thumb.setFixedSize(self._THUMB_DIAMETER, self._THUMB_DIAMETER)
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
        self.thumb.raise_() # Ensure thumb is always drawn on top of the checkbox track
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
        if animate and prefers_reduced_motion():
            animate = False   # honor Windows "Animation effects" off - snap instead of slide
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


    # --- keyboard focus visual (2.2.2 D4) -------------------------------------------------------------
    # The track's sheet says outline:none, so a switch reached with Tab - or landed on by a Settings deep
    # link - showed no focus at all. Space flips it (Windows' switch key); Enter is left to the dialog's
    # default button, as everywhere else in Windows.
    def _focus_ring_qss(self) -> str:
        ring = tokens.FOCUS_RING_WIDTH
        w = max(0, self._OUTER_TRACK_WIDTH - 2 * ring)
        h = max(0, self._OUTER_TRACK_HEIGHT - 2 * ring)
        c = semantic_colors()
        return (f' QCheckBox[keyboardFocus="true"]::indicator {{ border: {ring}px solid {c["accent"]};'
                f' width: {w}px; height: {h}px; }}'
                # on: the track IS accent - the ring is drawn in the text colour so it still shows
                f' QCheckBox[keyboardFocus="true"]::indicator:checked {{ border: {ring}px solid {c["text_primary"]};'
                f' width: {w}px; height: {h}px; }}')

    def _set_keyboard_focus(self, on: bool) -> None:
        if bool(self.checkbox.property("keyboardFocus")) == on:
            return
        self.checkbox.setProperty("keyboardFocus", on)
        st = self.checkbox.style()
        st.unpolish(self.checkbox)
        st.polish(self.checkbox)
        self.checkbox.update()

    _KEYBOARD_REASONS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                         Qt.FocusReason.ShortcutFocusReason, Qt.FocusReason.OtherFocusReason)
    # Focus lost or regained with the window (Alt+Tab away and back) keeps the ring as it was.
    _WINDOW_REASONS = (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason)

    def eventFilter(self, obj, event) -> bool:
        if obj is getattr(self, "checkbox", None):
            kind = event.type()
            if kind == QEvent.Type.FocusIn:
                # Tab, Shift+Tab and a deep link's focus show the ring; a mouse click doesn't
                if event.reason() in self._KEYBOARD_REASONS:
                    self._set_keyboard_focus(True)
                elif event.reason() == Qt.FocusReason.MouseFocusReason:
                    self._set_keyboard_focus(False)
            elif kind == QEvent.Type.FocusOut and event.reason() not in self._WINDOW_REASONS:
                self._set_keyboard_focus(False)
        return super().eventFilter(obj, event)

    def show_keyboard_focus(self) -> None:
        """Show the focus ring now - a Settings deep link focuses the switch before its window is
        active, so Qt sends no FocusIn until the window activates (and then as an activation)."""
        self._set_keyboard_focus(True)

    def keyboardFocusShown(self) -> bool:
        return bool(self.checkbox.property("keyboardFocus"))

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
                set_text_in_face(self._label_widget, text)
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
            apply_text_face(self._label_widget, self._label_widget.text())   # the new sheet dropped its face


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
    It consists of a QSlider for the sliding mechanism and a label/input
    to display and edit the current value.
    """
    valueChanged = pyqtSignal(int)
    sliderReleased = pyqtSignal()


    def __init__(self, min_value: int = 0, max_value: int = 100, value: int = 0, 
                 page_step: int = 1, has_ticks: bool = False, 
                 parent: Optional[QWidget] = None, value_label_text_color: Optional[str] = None,
                 editable: bool = True, suffix: str = "", auto_update_label: bool = True) -> None:
        super().__init__(parent)
        self._value_input: Optional[QLineEdit] = None
        self._value_label: Optional[QLabel] = None
        self._value_label_text_color: Optional[str] = value_label_text_color
        self._editable: bool = editable
        self._suffix: str = suffix
        self._special_value_text: str = ""
        self._auto_update_label: bool = auto_update_label

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
            # Safe tick interval calculation
            rng = max_val - min_val
            if rng > 0:
                tick_interval = max(1, rng // 10)
                if rng / tick_interval > 15:
                    tick_interval = max(1, rng // 15)
            else:
                tick_interval = 1
            self.slider.setTickInterval(tick_interval)

        layout.addWidget(self.slider)

        # The UI face, one family (#149; utils/styles.ui_family). Small cut: as until 2.2.0.
        label_font = ui_font(point_size=10, optical="Small")
        
        if self._editable:
            self._value_input = QLineEdit(self) 
            self._value_input.setFont(label_font)
            self._value_input.setMinimumWidth(70) 
            self._value_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._value_input.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self._value_input.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            
            self._update_input_style()
            self._value_input.setText(self._format_value(initial_val, min_val))
            
            layout.addWidget(self._value_input)
            self._value_input.editingFinished.connect(self._on_input_editing_finished)
        else:
            self._value_label = QLabel(self._format_value(initial_val, min_val), self)
            self._value_label.setFont(label_font)
            # Allow label to grow if needed, but keeping a minimum prevents jumping
            self._value_label.setMinimumWidth(50) 
            self._value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._value_label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            
            # Style for the label
            label_style = "QLabel { background: transparent; border: none; padding-left: 2px; }"
            if self._value_label_text_color:
                label_style += f" color: {self._value_label_text_color};"
            self._value_label.setStyleSheet(label_style)
            
            layout.addWidget(self._value_label)

        self.setLayout(layout)

        self.slider.valueChanged.connect(self._on_internal_slider_value_changed)
        self.slider.sliderReleased.connect(self._on_internal_slider_released)


    def _update_input_style(self) -> None:
        """Updates the QLineEdit style to look transparent but usable."""
        if not self._value_input: return
        
        base_style = """
            QLineEdit {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                outline: none;
                padding-left: 2px;
            }
            QLineEdit:hover {
                border: 1px solid rgba(120, 120, 120, 0.5);
                background: rgba(120, 120, 120, 0.1);
            }
            QLineEdit:focus {
                border: 1px solid #0078D4;
                background: rgba(32, 32, 32, 0.2);
            }
        """
        if self._value_label_text_color: 
            base_style += f" QLineEdit {{ color: {self._value_label_text_color}; }}"
        
        self._value_input.setStyleSheet(base_style)


    def _format_value(self, value: int, minimum: Optional[int] = None) -> str:
        """``value`` + suffix, or the special-value text at the minimum (QSpinBox semantics)."""
        floor = self.slider.minimum() if minimum is None else minimum
        if self._special_value_text and value == floor:
            return self._special_value_text
        return f"{value}{self._suffix}"

    def setSuffix(self, suffix: str) -> None:
        """Change the unit shown after the number (e.g. "°C" -> "°F" when the temperature unit flips)."""
        self._suffix = suffix or ""
        if self._auto_update_label:
            self.setValueText(self._format_value(self.slider.value()))

    def setSpecialValueText(self, text: str) -> None:
        """Show ``text`` instead of the number at the minimum - e.g. "Off" for a 0 that disables the
        feature, rather than a misleading "0°C". Same contract as QAbstractSpinBox.setSpecialValueText."""
        self._special_value_text = text or ""
        if self._auto_update_label:
            self.setValueText(self._format_value(self.slider.value()))

    def _on_internal_slider_value_changed(self, value: int) -> None:
        if self._auto_update_label:
            self.setValueText(self._format_value(value))
        self.valueChanged.emit(value)


    def _on_internal_slider_released(self) -> None: self.sliderReleased.emit()


    def _on_input_editing_finished(self) -> None:
        """Parses input text and updates the slider."""
        if not self._value_input: return
        text = self._value_input.text()
        # Find the first integer in the text
        match = re.search(r"[-+]?\d+", text)
        if match:
            try:
                new_val = int(match.group())
                # Clamp to range
                new_val = max(self.slider.minimum(), min(self.slider.maximum(), new_val))
                
                # Update slider (this will trigger valueChanged -> parent update -> setValueText)
                if new_val != self.slider.value():
                    self.slider.setValue(new_val)
                else:
                     # Even if value is same, format it back (e.g. user typed "50" -> "50%")
                     self.setValueText(self._format_value(new_val))
            except ValueError:
                pass 


    def setRange(self, min_val: int, max_val: int) -> None:
        self.slider.setRange(min_val, max_val)
        if self.slider.tickPosition() != QSlider.TickPosition.NoTicks:
            rng = max_val - min_val
            tick_interval = max(1, rng // 10) if rng > 0 else 1
            if rng > 0 and rng / tick_interval > 15:
                tick_interval = max(1, rng // 15)
            self.slider.setTickInterval(tick_interval)


    def setValue(self, value: int) -> None: self.slider.setValue(value)


    def setSingleStep(self, step: int) -> None: self.slider.setSingleStep(step)


    def setPageStep(self, step: int) -> None: self.slider.setPageStep(step)


    def setTickInterval(self, ti: int) -> None: self.slider.setTickInterval(ti)


    def setTickPosition(self, position: QSlider.TickPosition) -> None: self.slider.setTickPosition(position)


    def value(self) -> int: return self.slider.value()


    def setValueText(self, text: str) -> None:
        if self._value_input:
            if not self._value_input.hasFocus():
                self._value_input.setText(text)
                self._value_input.setCursorPosition(0)
        elif self._value_label:
            self._value_label.setText(text)


    def setValueLabelTextColor(self, color_hex: str) -> None:
        self._value_label_text_color = color_hex 
        if self._value_input:
            self._update_input_style()
        elif self._value_label:
            label_style = "QLabel { background: transparent; border: none; padding-left: 2px; }"
            if self._value_label_text_color:
                label_style += f" color: {self._value_label_text_color};"
            self._value_label.setStyleSheet(label_style)


    def sizeHint(self) -> QSize:
        slider_sh = self.slider.sizeHint()
        if self._value_input:
             label_sh = self._value_input.sizeHint()
        elif self._value_label:
             label_sh = self._value_label.sizeHint()
        else:
             label_sh = QSize(50, 20)
        
        current_layout = self.layout()
        if not isinstance(current_layout, QHBoxLayout): 
            return QSize(slider_sh.width() + 70 + 8, max(slider_sh.height(), 20, 28))

        margins = current_layout.contentsMargins()
        spacing = current_layout.spacing()
        
        width = slider_sh.width() + spacing + label_sh.width() + margins.left() + margins.right()
        height = max(slider_sh.height(), label_sh.height()) + margins.top() + margins.bottom()
        
        return QSize(width, max(height, 28))


# =============================================================================
# Design-system primitives (Fluent settings vocabulary). These consume the
# tokens in constants/styles.py via utils/styles.font()/semantic_colors() so the
# Settings dialog and the future Monitor window share one visual language.
# =============================================================================

def name_controls(control: Optional[QWidget], name: str, description: str = "") -> list:
    """Give ``control`` - and every keyboard-focusable widget inside it that has no name of its own -
    the card's title as its accessible name (and the description as its accessible description).

    A setting's title is a separate QLabel, so without this a screen reader landing on the toggle,
    slider or spin box announced only "check box" / "slider". A button that shows its own text (Copy,
    a segment of a segmented control) is already named by that text and is left alone, as is any
    widget that was named explicitly. Returns the widgets it named."""
    if control is None:
        return []
    name = str(name or "").strip().rstrip(":：").strip()
    description = str(description or "").strip()
    if not name:
        return []
    named = []
    for w in [control] + control.findChildren(QWidget):
        if isinstance(w, QAbstractButton) and w.text().strip():
            continue
        if w.accessibleName():
            continue
        if w is not control and not (w.focusPolicy().value & Qt.FocusPolicy.TabFocus.value):
            continue
        w.setAccessibleName(name)
        if description:
            w.setAccessibleDescription(description)
        named.append(w)
    return named


class SettingCard(QFrame):
    """
    A Fluent "setting card" row: a title with an optional muted description on the
    left, an optional leading glyph, and a control docked to the right. The single
    reusable atom the new Settings pages are built from.
    """

    def __init__(self, title: str, description: str = "", control: Optional[QWidget] = None,
                 icon: Optional[str] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingCard")
        self._control = control
        c = semantic_colors()
        self.setStyleSheet(
            f"QFrame#settingCard {{ background-color: {c['card_bg']}; "
            f"border: 1px solid {c['card_stroke']}; border-radius: {tokens.RADIUS_CARD}px; }}"
        )
        # Win11 Settings card proportions: a ~58px floor height with roomy 16px side padding, so a
        # bare-toggle row and a slider/combo row read as the same generously-sized card (the old 48px /
        # 12-8 padding read cramped next to the native Settings app).
        self.setMinimumHeight(58)

        # A box layout whose direction flips with the card's width: title | control side by side when
        # there's room, the control under the title when there isn't (see _reflow). The title wraps, so
        # a long translation grows the card taller instead of forcing the page to scroll sideways.
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        layout.setContentsMargins(16, tokens.SPACE_M, 16, tokens.SPACE_M)
        layout.setSpacing(tokens.SPACE_M)
        self._row = layout

        # The leading glyph and the title share one sub-row so a stacked card keeps the glyph beside it.
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(tokens.SPACE_M)
        self._glyph: Optional[QLabel] = None
        if icon:
            glyph = QLabel(icon)
            glyph.setFixedWidth(22)
            glyph.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
            head.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
            self._glyph = glyph

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        self._title_label = QLabel(title)
        self._title_label.setFont(token_font(tokens.TYPE_BODY_STRONG))
        self._title_label.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        self._title_label.setWordWrap(True)
        text_col.addWidget(self._title_label)
        if description:
            self._desc_label = QLabel()
            set_ui_text(self._desc_label, description)
            self._desc_label.setFont(token_font(tokens.TYPE_CAPTION))
            self._desc_label.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
            self._desc_label.setWordWrap(True)
            text_col.addWidget(self._desc_label)
        head.addLayout(text_col, 1)
        layout.addLayout(head, 1)

        if control is not None:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        # The title is a separate QLabel, so a screen reader landing on the control heard nothing.
        self._named = name_controls(control, title, description)

    def control(self) -> Optional[QWidget]:
        return self._control

    def setDescriptionVisible(self, visible: bool) -> None:
        """Show or hide the description line - and drop it from the control's accessible description
        while hidden, so a screen reader never announces text that is not on screen."""
        desc = getattr(self, "_desc_label", None)
        if desc is None:
            return
        desc.setVisible(visible)
        for w in self._named:
            w.setAccessibleDescription(desc.text() if visible else "")

    def isStacked(self) -> bool:
        """True while the control sits under the title (the card is too narrow for one row)."""
        return self._row.direction() == QBoxLayout.Direction.TopToBottom

    def _control_min_width(self) -> int:
        ctl = self._control
        if ctl is None or ctl.isHidden():
            return 0
        return ctl.minimumWidth() or ctl.minimumSizeHint().width()

    def _head_min_width(self, side_by_side: bool) -> int:
        """Narrowest the glyph + title column may get. Beside the control the title keeps its one-line
        width (capped at SETTING_CARD_MIN_TITLE_WIDTH) so it isn't wrapped a word per line; stacked it
        only needs its longest word."""
        lbl = self._title_label
        need = lbl.minimumSizeHint().width()   # a word-wrapped QLabel's floor: its longest word
        if side_by_side:
            one_line = lbl.fontMetrics().horizontalAdvance(lbl.text()) + 2
            need = max(need, min(one_line, constants.layout.SETTING_CARD_MIN_TITLE_WIDTH))
        desc = getattr(self, "_desc_label", None)
        if desc is not None and not desc.isHidden():
            need = max(need, desc.minimumSizeHint().width())
            if side_by_side:
                # The same rule for the description: beside the control it keeps a readable column
                # (or its one-line width, if shorter). A long live status (the metrics endpoint's
                # "port in use") once wrapped 8-10 lines in a 60-100 px column at the minimum width.
                one_line = desc.fontMetrics().horizontalAdvance(desc.text().strip()) + 2
                need = max(need, min(one_line, constants.layout.SETTING_CARD_MIN_DESC_WIDTH))
        if self._glyph is not None:
            need += self._glyph.width() + tokens.SPACE_M
        return need

    def _side_by_side_width(self) -> int:
        """The card width below which title + control no longer fit on one row."""
        m = self._row.contentsMargins()
        ctl = self._control_min_width()
        one_row = getattr(self._control, "_one_row_width", None)
        if ctl and callable(one_row):
            # A ReflowRow beside the title lays out on one row (it only stacks under a stacked
            # title), so that - not its stacked floor - is what the side-by-side layout needs.
            ctl = max(ctl, one_row())
        gap = self._row.spacing() if ctl else 0
        return m.left() + m.right() + self._head_min_width(True) + gap + ctl

    def minimumSizeHint(self) -> QSize:
        # The STACKED floor: a card can always reflow down to its widest single piece, so that - not
        # title + control side by side - is what the page (and its scroll area) must make room for.
        base = super().minimumSizeHint()
        m = self._row.contentsMargins()
        w = m.left() + m.right() + max(self._head_min_width(False), self._control_min_width())
        return QSize(w, base.height())

    def _reflow(self, width: int) -> None:
        if self._control is None:
            return
        stacked = width < self._side_by_side_width()
        if stacked == self.isStacked():
            return
        if stacked:
            self._row.setDirection(QBoxLayout.Direction.TopToBottom)
            self._row.setAlignment(self._control, Qt.AlignmentFlag.AlignLeft)
        else:
            self._row.setDirection(QBoxLayout.Direction.LeftToRight)
            self._row.setAlignment(self._control, Qt.AlignmentFlag.AlignVCenter)
        self.updateGeometry()

    def resizeEvent(self, event) -> None:
        self._reflow(event.size().width())
        super().resizeEvent(event)

    def set_description(self, text: str, user_text: bool = False) -> None:
        """Swap the muted description (only for a card built with one). ``user_text``: it holds text
        the user typed (a host name in any script) - drawn in the one face that has all of it."""
        label = getattr(self, "_desc_label", None)
        if label is not None:
            if user_text:
                from netspeedtray.utils.styles import set_text_in_face
                set_text_in_face(label, text)
            else:
                set_ui_text(label, text)   # e.g. LHM status "Options → ...": arrows not in every UI face
        for w in self._named:
            w.setAccessibleDescription(str(text or "").strip())
        if self._control is not None:
            self._reflow(self.width())   # a longer description may need the stacked layout


class ReflowRow(QWidget):
    """A left-packed row of widgets (e.g. action buttons) that stacks them vertically when the row is
    too narrow to hold them side by side, so a long translation never forces a sideways scroll."""

    def __init__(self, widgets, spacing: int = tokens.SPACE_S, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._widgets = list(widgets)
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(spacing)
        for w in self._widgets:
            self._box.addWidget(w, 0, Qt.AlignmentFlag.AlignLeft)
        self._box.addStretch(1)

    @staticmethod
    def _min_w(w: QWidget) -> int:
        return max(w.minimumWidth(), w.minimumSizeHint().width(), w.sizeHint().width())

    def _one_row_width(self) -> int:
        shown = [w for w in self._widgets if not w.isHidden()]
        return sum(self._min_w(w) for w in shown) + self._box.spacing() * max(0, len(shown) - 1)

    def isStacked(self) -> bool:
        return self._box.direction() == QBoxLayout.Direction.TopToBottom

    def minimumSizeHint(self) -> QSize:
        base = super().minimumSizeHint()
        widest = max((self._min_w(w) for w in self._widgets if not w.isHidden()), default=0)
        return QSize(widest, base.height())

    def resizeEvent(self, event) -> None:
        stacked = event.size().width() < self._one_row_width()
        if stacked != self.isStacked():
            self._box.setDirection(QBoxLayout.Direction.TopToBottom if stacked
                                   else QBoxLayout.Direction.LeftToRight)
            self.updateGeometry()
        super().resizeEvent(event)


class _ExpanderHeader(QAbstractButton):
    """The header row of a SettingExpander WITHOUT a header toggle - the disclosure control itself.
    A button, so UI Automation exposes a Button (with Invoke) and the keyboard can drive it: a Tab
    stop that Space (Qt's button default) and Enter open and close. (With a header toggle the toggle
    is the control and the header stays a plain frame, so the tree has no redundant second button.)

    Not checkable on purpose: Qt reports a checkable button to UI Automation as a check box ("not
    checked"), and PyQt6 has no QAccessible binding to expose a real expanded/collapsed state - so
    the state rides in the accessible name instead ("Advertised plan speed, collapsed")."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Tab only: a mouse click toggles without leaving a focus ring behind.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        # A button defaults to a fixed height; the header grows with a wrapped title like a frame does.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Toggle on the first press only, but swallow the auto-repeats too: an ignored repeat goes
            # up to the dialog, which "presses" its default Save button - holding Enter on a header
            # used to save and close Settings (#432 review).
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        # QAbstractButton paints nothing itself: draw the stylesheet box (card fill, stroke, focus ring).
        opt = QStyleOption()
        opt.initFrom(self)
        painter = QPainter(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)


class SettingExpander(QWidget):
    """
    A Fluent settings expander: a header row (title + optional muted description +
    optional master toggle) over a collapsible content area of child cards. With
    ``header_toggle=True`` a Win11Toggle in the header both controls the feature and
    drives the body's visibility - the native Win11 Settings disclosure idiom.
    Without one, the header itself opens and closes the body, from the mouse or the keyboard.
    """
    toggled = pyqtSignal(bool)          # master-toggle state (only when header_toggle)
    expandedChanged = pyqtSignal(bool)

    def __init__(self, title: str, description: str = "", header_toggle: bool = False,
                 initial_on: bool = False, expanded: bool = False,
                 parent: Optional[QWidget] = None, i18n=None) -> None:
        super().__init__(parent)
        self.setObjectName("settingExpander")
        self._has_toggle = header_toggle
        self._i18n = i18n
        self._title = str(title or "").strip().rstrip(":：").strip()
        c = semantic_colors()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(tokens.SPACE_XS)

        if header_toggle:
            self._header = QFrame()
            self._header.mousePressEvent = self._on_header_clicked   # the toggle is the keyboard path
        else:
            self._header = _ExpanderHeader()
            self._header.clicked.connect(self._on_header_clicked)   # mouse, Space, Enter
        self._header.setObjectName("expanderHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        # The focus ring follows the app's single focus style (2px accent, as on the inputs).
        self._header.setStyleSheet(
            f"#expanderHeader {{ background-color: {c['card_bg']}; "
            f"border: 1px solid {c['card_stroke']}; border-radius: {tokens.RADIUS_CARD}px; }}"
            f" #expanderHeader:focus {{ border: 2px solid {c['accent']}; }}"
        )
        self._header.setMinimumHeight(58)   # match SettingCard's Win11 row height
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(16, tokens.SPACE_M, 16, tokens.SPACE_M)
        hl.setSpacing(tokens.SPACE_M)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title_label = QLabel(title)
        title_label.setFont(token_font(tokens.TYPE_BODY_STRONG))
        title_label.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        title_label.setWordWrap(True)   # a long translation wraps instead of widening the whole page
        text_col.addWidget(title_label)
        self._title_label = title_label
        self._desc_label: Optional[QLabel] = None
        if description:
            d = QLabel(description)
            d.setFont(token_font(tokens.TYPE_CAPTION))
            d.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
            d.setWordWrap(True)
            text_col.addWidget(d)
            self._desc_label = d
        hl.addLayout(text_col, 1)

        self._toggle: Optional["Win11Toggle"] = None
        self._named: list = []
        if header_toggle:
            self._toggle = Win11Toggle(initial_state=initial_on)
            self._toggle.toggled.connect(self._on_toggle)
            hl.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignVCenter)
            # The header toggle is the expander's only focusable control: it carries the title.
            self._named = name_controls(self._toggle, title, description)

        self._chevron = QLabel()
        self._chevron.setFixedWidth(16)
        # Native Win11 disclosure chevron (Segoe Fluent Icons), not a Unicode triangle.
        self._chevron.setStyleSheet(
            f"color: {c['text_secondary']}; background: transparent;"
            f" font-family: '{icon_font_family()}'; font-size: 12px;")   # one family (no fallback walk)
        hl.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)

        if description and not header_toggle:
            self._header.setAccessibleDescription(str(description).strip())
        outer.addWidget(self._header)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(tokens.SPACE_L, tokens.SPACE_XS, tokens.SPACE_XS, tokens.SPACE_XS)
        self._content_layout.setSpacing(tokens.SPACE_XS)
        outer.addWidget(self._content)

        self._expanded = initial_on if header_toggle else expanded
        self._sync()

    def _on_toggle(self, on: bool) -> None:
        self.toggled.emit(on)
        self.setExpanded(on)

    def _on_header_clicked(self, *_args) -> None:
        if self._has_toggle and self._toggle is not None:
            self._toggle.setChecked(not self._toggle.isChecked())  # fires _on_toggle
        else:
            self.setExpanded(not self._expanded)

    def _tr(self, key: str, default: str) -> str:
        from netspeedtray.utils.helpers import tr
        return tr(self._i18n, key, default)

    def _sync(self) -> None:
        self._content.setVisible(self._expanded)
        # E70D ChevronDown (open) / E76C ChevronRight (closed).
        self._chevron.setText(chr(0xE70D) if self._expanded else chr(0xE76C))
        # The header's name carries the state a screen reader can't otherwise get (see _ExpanderHeader).
        # With a header toggle, the toggle's own checked state already says it.
        if self._has_toggle:
            return
        if self._expanded:
            name = self._tr("EXPANDER_A11Y_EXPANDED", "{title}, expanded")
        else:
            name = self._tr("EXPANDER_A11Y_COLLAPSED", "{title}, collapsed")
        try:
            name = name.format(title=self._title)
        except (KeyError, IndexError, ValueError):
            name = self._title
        if self._header.accessibleName() != name:
            self._header.setAccessibleName(name)

    def setExpanded(self, on: bool) -> None:
        if self._expanded != on:
            self._expanded = on
            self._sync()
            self.expandedChanged.emit(on)

    def isExpanded(self) -> bool:
        return self._expanded

    def isChecked(self) -> bool:
        return self._toggle.isChecked() if self._toggle else self._expanded

    def setChecked(self, on: bool) -> None:
        if self._toggle is not None:
            self._toggle.setChecked(on)
            self.setExpanded(on)
        else:
            self.setExpanded(on)

    def contentLayout(self) -> QVBoxLayout:
        return self._content_layout

    def hasHeaderToggle(self) -> bool:
        """True when the header carries the feature's on/off toggle (the body shows only while it is on)."""
        return self._has_toggle

    def header(self) -> QWidget:
        """The header row - all that is on screen while the expander is collapsed."""
        return self._header

    def set_description(self, text: str) -> None:
        """Swap the muted header description (only for an expander built with one)."""
        if self._desc_label is not None:
            set_ui_text(self._desc_label, text)
        for w in self._named + ([] if self._has_toggle else [self._header]):
            w.setAccessibleDescription(str(text or "").strip())


class Win11Segmented(QWidget):
    """
    A Win11 segmented control: an exclusive row of joined pill buttons (the native
    idiom for a small mutually-exclusive choice - decimals 0/1/2, alignment L/C/R,
    update-rate 1s/2s/5s/10s). Emits ``valueChanged(value)`` with the option's value.
    """
    valueChanged = pyqtSignal(object)

    def __init__(self, options, parent: Optional[QWidget] = None, wrap: bool = False) -> None:
        """options: an iterable of (label, value) pairs. ``wrap=True`` lets a long row (e.g. the
        seven arrow styles) break onto two rows when its parent is too narrow for one, instead of
        forcing a sideways scroll - QToolButton segments hard-clip, so they can't shrink."""
        super().__init__(parent)
        self._value = None
        self._items = []  # (value, button) in order; matched by equality, not repr()
        self._wrap = wrap
        self._rows = 1
        options = list(options)
        n = len(options)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        if wrap:
            layout = QVBoxLayout(self)
            layout.setSpacing(tokens.SPACE_XS)
            self._row_layouts = []
            for _ in range(2):
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(0)
                row.addStretch(1)                  # rows stay left-packed
                layout.addLayout(row)
                self._row_layouts.append(row)
        else:
            layout = QHBoxLayout(self)
            layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        self._layout = layout

        for i, (label, value) in enumerate(options):
            btn = QToolButton()
            btn.setText(str(label))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setObjectName(self._segment_name(i, n))
            btn.clicked.connect(lambda _checked=False, v=value: self.setValue(v))
            self._group.addButton(btn)
            if not wrap:
                layout.addWidget(btn)
            self._items.append((value, btn))
        if wrap:
            self._place(1)
        else:
            layout.addStretch(0)

        accent = get_accent_color().name()
        c = semantic_colors()
        r = tokens.RADIUS_CONTROL
        self.setStyleSheet(f"""
            QToolButton {{
                background-color: {c['card_bg']};
                color: {c['text_primary']};
                border: 1px solid {c['card_stroke']};
                border-radius: 0px;
                padding: 7px 12px;
                {ui_family_css()}
            }}
            QToolButton:hover {{ background-color: {c['subtle_fill']}; }}
            QToolButton:checked {{ background-color: {accent}; color: white; border-color: {accent}; }}
            QToolButton#segFirst {{ border-top-left-radius: {r}px; border-bottom-left-radius: {r}px; }}
            QToolButton#segLast  {{ border-top-right-radius: {r}px; border-bottom-right-radius: {r}px; }}
            QToolButton#segOnly  {{ border-radius: {r}px; }}
        """)

    @staticmethod
    def _segment_name(i: int, n: int) -> str:
        """Object name picking the rounded ends: segOnly / segFirst / segLast / segMid."""
        if n == 1:
            return "segOnly"
        if i == 0:
            return "segFirst"
        if i == n - 1:
            return "segLast"
        return "segMid"

    def _per_row(self, rows: int) -> int:
        return -(-len(self._items) // rows)   # ceil

    def _place(self, rows: int) -> None:
        """(wrap mode) Lay the segments out on ``rows`` rows, each row its own rounded strip."""
        self._rows = rows
        per_row = self._per_row(rows)
        for row in self._row_layouts:
            for _v, btn in self._items:
                row.removeWidget(btn)
        for i, (_v, btn) in enumerate(self._items):
            r, col = divmod(i, per_row)
            in_row = min(per_row, len(self._items) - r * per_row)
            name = self._segment_name(col, in_row)
            if btn.objectName() != name:
                btn.setObjectName(name)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            self._row_layouts[r].insertWidget(col, btn)   # before the row's trailing stretch

    def _row_width(self, rows: int) -> int:
        """Width the segments need when laid out on ``rows`` rows."""
        per_row = self._per_row(rows)
        widths = [btn.sizeHint().width() for _v, btn in self._items]
        return max(sum(widths[i:i + per_row]) for i in range(0, len(widths), per_row))

    def minimumSizeHint(self) -> QSize:
        base = super().minimumSizeHint()
        if not self._wrap or len(self._items) < 2:
            return base
        return QSize(self._row_width(2), base.height())

    def sizeHint(self) -> QSize:
        # (wrap mode) Prefer one row: a layout that doesn't stretch the control (a stacked SettingCard
        # aligns it left) sizes it by this, and a 2-row hint would keep it on two rows for good.
        base = super().sizeHint()
        if not self._wrap or len(self._items) < 2:
            return base
        return QSize(self._row_width(1), base.height())

    def _one_row_width(self) -> int:
        """(wrap mode) What SettingCard needs beside the title: the one-row width, so the card puts the
        control under the title (on one row, if that fits) before squeezing it onto two rows."""
        return self._row_width(1) if self._wrap and len(self._items) > 1 else self.minimumSizeHint().width()

    def resizeEvent(self, event) -> None:
        if self._wrap and len(self._items) > 1:
            rows = 1 if event.size().width() >= self._row_width(1) else 2
            if rows != self._rows:
                self._place(rows)
                self.updateGeometry()
        super().resizeEvent(event)

    def _find(self, value):
        for v, btn in self._items:
            if v == value:
                return btn
        return None

    def setValue(self, value) -> None:
        btn = self._find(value)
        if btn is None:
            return  # reject values with no matching option: no desync, no phantom emit
        changed = value != self._value
        self._value = value
        if not btn.isChecked():
            btn.setChecked(True)
        if changed:
            self.valueChanged.emit(value)

    def value(self):
        return self._value


class ColorField(QWidget):
    """
    A color input: a swatch button (opens QColorDialog) plus a validated #RRGGBB
    field. Consolidates the four hand-rolled swatch+lineedit copies. Emits
    ``colorChanged(hex)`` only on a real change.
    """
    colorChanged = pyqtSignal(str)

    _HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")

    def __init__(self, initial_hex: str = "#FFFFFF", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hex = self._normalize(initial_hex) or "#FFFFFF"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.SPACE_S)

        self._swatch = QPushButton()
        self._swatch.setFixedSize(30, 24)
        self._swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._swatch.clicked.connect(self._pick)

        self._edit = QLineEdit(self._hex)
        self._edit.setMaxLength(7)
        self._edit.setFixedWidth(84)
        self._edit.editingFinished.connect(self._on_edit_finished)

        layout.addWidget(self._swatch)
        layout.addWidget(self._edit)
        layout.addStretch(0)
        self._apply_swatch()

    def _normalize(self, h: Optional[str]) -> Optional[str]:
        if not h:
            return None
        h = h.strip()
        if self._HEX_RE.match(h):
            return ("#" + h.lstrip("#")).upper()
        return None

    def _apply_swatch(self) -> None:
        c = semantic_colors()
        self._swatch.setStyleSheet(
            f"QPushButton {{ background-color: {self._hex}; border: 1px solid {c['card_stroke']}; "
            f"border-radius: {tokens.RADIUS_CONTROL}px; }}"
        )

    def _pick(self) -> None:
        col = QColorDialog.getColor(QColor(self._hex), self)
        if col.isValid():
            self.setColor(col.name())

    def _on_edit_finished(self) -> None:
        n = self._normalize(self._edit.text())
        if n:
            self.setColor(n)
        else:
            self._edit.setText(self._hex)  # revert invalid input

    def setColor(self, hex_str: str) -> None:
        n = self._normalize(hex_str)
        if n is None:
            return
        changed = n != self._hex
        self._hex = n
        self._edit.blockSignals(True)
        self._edit.setText(self._hex)
        self._edit.blockSignals(False)
        self._apply_swatch()
        if changed:
            self.colorChanged.emit(self._hex)

    def color(self) -> str:
        return self._hex


class ArrowStylePicker(QWidget):
    """
    Picks the up/down arrow glyph style from the curated presets (Classic/Solid/Compact/
    Outline/Double; see ``constants.arrows.ARROW_PRESETS``) plus a Custom option for any glyph.

    Each segment's label *is* the glyph pair, so the choice is visual. "Classic" maps to the
    empty config value (``arrow_*_symbol == ""``) so the renderer falls back to the native,
    locale-aware default arrow - i.e. Classic always follows the OS language. Emits ``changed``.
    """
    changed = pyqtSignal()
    _CUSTOM = "__custom__"

    def __init__(self, i18n=None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._i18n = i18n
        from netspeedtray.constants.arrows import ARROW_PRESETS
        self._presets = list(ARROW_PRESETS)  # [(name, up, down)]

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(tokens.SPACE_S)

        options = [(f"{up} {down}", name) for (name, up, down) in self._presets]
        options.append((self._tr("ARROW_STYLE_CUSTOM", "Custom"), self._CUSTOM))
        self._seg = Win11Segmented(options, wrap=True)   # 7 segments: two rows in a narrow window
        self._seg.valueChanged.connect(self._on_segment)
        for value, btn in self._seg._items:
            if value != self._CUSTOM:
                name = next((n for (n, _u, _d) in self._presets if n == value), value)
                key = "ARROW_PRESET_" + str(name).upper().replace(" ", "_")
                btn.setToolTip(self._tr(key, name))
                btn.setAccessibleName(self._tr(key, name))   # the visible text is just two glyphs
                # ▴ ▾ / △ ▽ / ⇑ ⇓ are not in the UI face: draw the pair in the known face the widget
                # draws it in (utils/font_runs), so Qt never searches every installed family for them.
                face = symbol_face(btn.text())
                if face:
                    btn.setStyleSheet("font-family: '%s';" % face)
        root.addWidget(self._seg)

        self._custom_row = QWidget()
        crow = QHBoxLayout(self._custom_row)
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(tokens.SPACE_S)
        self._up_edit = QLineEdit()
        self._up_edit.setMaxLength(4)
        self._up_edit.setFixedWidth(56)
        self._up_edit.setPlaceholderText("↑")
        self._down_edit = QLineEdit()
        self._down_edit.setMaxLength(4)
        self._down_edit.setFixedWidth(56)
        self._down_edit.setPlaceholderText("↓")
        self._up_edit.textChanged.connect(lambda _t: self.changed.emit())
        self._down_edit.textChanged.connect(lambda _t: self.changed.emit())
        for text, edit in ((self._tr("ARROW_CUSTOM_UP_LABEL", "Up"), self._up_edit),
                           (self._tr("ARROW_CUSTOM_DOWN_LABEL", "Down"), self._down_edit)):
            lbl = QLabel(text)
            lbl.setBuddy(edit)
            edit.setAccessibleName(text)
            if edit is self._down_edit:
                crow.addSpacing(tokens.SPACE_M)
            crow.addWidget(lbl)
            crow.addWidget(edit)
        crow.addStretch(0)
        # The placeholders ↑ ↓ (and the custom arrows typed here) are not in every UI face (Bengali's
        # Nirmala UI): these two fields draw in a known face that has them.
        arrow_face = face_for_text("↑↓")
        if arrow_face:
            for edit in (self._up_edit, self._down_edit):
                f = QFont(edit.font())
                f.setFamily(arrow_face)
                edit.setFont(f)
        self._custom_row.setVisible(False)
        root.addWidget(self._custom_row)   # was orphaned after `return` in _tr (dead code) - the custom
        #                                    arrow row never entered the layout, so "Custom" did nothing.

    def _tr(self, key: str, default: str) -> str:
        """Graceful i18n lookup: the picker may be constructed without an i18n object."""
        from netspeedtray.utils.helpers import tr
        return tr(self._i18n, key, default)

    def _on_segment(self, value) -> None:
        self._custom_row.setVisible(value == self._CUSTOM)
        self.changed.emit()

    def set_values(self, up: str, down: str) -> None:
        """Select the preset matching (up, down), or Custom (filling the fields)."""
        up = up or ""
        down = down or ""
        if not up and not down:
            self._seg.setValue("Classic")
            self._custom_row.setVisible(False)
            return
        for (name, pu, pd) in self._presets:
            if up == pu and down == pd:
                self._seg.setValue(name)
                self._custom_row.setVisible(False)
                return
        self._up_edit.blockSignals(True)
        self._down_edit.blockSignals(True)
        self._up_edit.setText(up)
        self._down_edit.setText(down)
        self._up_edit.blockSignals(False)
        self._down_edit.blockSignals(False)
        self._seg.setValue(self._CUSTOM)
        self._custom_row.setVisible(True)

    def get_values(self) -> dict:
        """Returns {'arrow_up_symbol', 'arrow_down_symbol'} for the current selection."""
        val = self._seg.value()
        if val == self._CUSTOM:
            return {"arrow_up_symbol": self._up_edit.text(), "arrow_down_symbol": self._down_edit.text()}
        for (name, up, down) in self._presets:
            if name == val:
                # Classic == native default → store empty so it tracks the OS language.
                if name == "Classic":
                    return {"arrow_up_symbol": "", "arrow_down_symbol": ""}
                return {"arrow_up_symbol": up, "arrow_down_symbol": down}
        return {"arrow_up_symbol": "", "arrow_down_symbol": ""}


# --- NoticeLink: a moment-of-need prompt with a real action (2.2.2 D4) --------------------------------

def default_notice_surface() -> str:
    """The surface a NoticeLink sits on when its host names none: a setting card."""
    return tokens.CARD_BG_DARK if is_dark_mode() else tokens.CARD_BG_LIGHT


def notice_link_colors(surface: Optional[str] = None) -> dict:
    """The colours a NoticeLink paints with: ``{"text", "link", "focus", "background", "underline"}``.

    High Contrast wins: the theme's own window text / hyperlink / highlight colours on its own window
    colour (the app's Light/Dark surfaces are not what a High Contrast user picked, so the notice
    paints its own background then), and the link is always underlined - colour alone must not carry
    "this is clickable". Otherwise the app's tokens: secondary text, and the Windows accent nudged
    until it reads at MIN_TEXT_CONTRAST on ``surface`` - the colour the notice really sits on (the
    default blue is 4.45:1 on the Check connection sheet's #f3f3f3, under AA)."""
    from netspeedtray.utils.taskbar_appearance import read_high_contrast_link_colors
    hc = read_high_contrast_link_colors()
    if hc:
        return {"text": hc["text"], "link": hc["link"], "focus": hc["focus"],
                "background": hc["background"], "underline": True}
    c = semantic_colors()
    surface = surface or default_notice_surface()
    link = readable_on(c["accent"], surface)
    return {"text": readable_on(c["text_secondary"], surface), "link": link, "focus": link,
            "background": None, "underline": False}


def readable_on(color_hex: str, surface_hex: str, minimum: Optional[float] = None) -> str:
    """``color_hex``, lightened (on a dark surface) or darkened (on a light one) in small steps until
    it reaches ``minimum`` contrast against ``surface_hex`` - the hue is kept, only the lightness moves."""
    from netspeedtray.utils.color_scheme import contrast_ratio
    if minimum is None:
        minimum = tokens.MIN_TEXT_CONTRAST
    color = QColor(color_hex)
    surface = QColor(surface_hex)
    if not color.isValid() or not surface.isValid():
        return color_hex
    target = QColor(255, 255, 255) if surface.lightness() < 128 else QColor(0, 0, 0)
    steps = tokens.CONTRAST_BLEND_STEPS
    for step in range(0, steps + 1):
        t = step / steps
        mixed = QColor(round(color.red() + (target.red() - color.red()) * t),
                       round(color.green() + (target.green() - color.green()) * t),
                       round(color.blue() + (target.blue() - color.blue()) * t))
        if contrast_ratio(mixed.name(), surface.name()) >= minimum:
            return mixed.name()
    return target.name()


def _enter_activates(button: QAbstractButton, event) -> bool:
    """Enter / Return on a focused button-like control: activate it once per press and accept the event,
    auto-repeats too. Passed up, an ignored Enter reaches the dialog, which presses its default button -
    holding Enter on an expander header used to save and close Settings (#432 review). True if handled."""
    if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        return False
    if not event.isAutoRepeat():
        button.click()
    event.accept()
    return True


class _NoticeLinkButton(QPushButton):
    """A NoticeLink's action: a real push button - UI Automation sees a Button with Invoke, Tab reaches
    it, Space (Qt's default) and Enter fire it - drawn as a Fluent HyperlinkButton. Tab focus only (as
    _ExpanderHeader): a mouse click leaves no focus ring behind. Never a dialog's default button: Enter
    on it fires IT, never the dialog's Save / Check again."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("noticeLinkAction")
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(tokens.NOTICE_LINK_MIN_HEIGHT)

    def keyPressEvent(self, event) -> None:
        if not _enter_activates(self, event):
            super().keyPressEvent(event)


class NoticeLink(QFrame):
    """A short notice with one link-style action: "Hardware monitoring is off.  Turn on in Settings >".
    The pattern for every moment-of-need prompt that used to only SAY where to go (2.2.2 D4), and for
    the Monitor's other in-card links ("Set a monthly limit", "Usage by day").

    ``on_click`` is any callable and can be swapped later (``set_on_click``) - Settings deep links pass
    ``lambda: main_widget.open_settings(page, anchor)``; an inline confirm (D5) or a help sheet (D6)
    pass their own. ``activated`` is emitted first, on every activation, for listeners that only need
    to know it happened.

    Accessibility: the action's accessible name is its text, its description the notice text, so a
    screen reader landing on the button hears both. Keyboard: Tab reaches the button, Enter and Space
    activate it. Look: a Fluent HyperlinkButton - accent text, no underline (High Contrast: always
    underlined), a subtle fill on hover and press, a 2 px accent focus ring (the app's house style,
    on purpose - not Fluent's two-tone ring), a trailing Segoe Fluent Icons chevron (a pixmap, so no
    font fallback search - the Unicode arrow made Qt load every installed font for Bengali). Colours
    are contrast-checked against ``surface`` (a hex or a callable returning one - what the host
    paints behind the notice; default a setting card) and re-read on every palette / style / theme
    change. RTL: the layout mirrors and the chevron points the reading way.

    ``stacked`` puts the action under the text instead of after it; ``centered`` centres both (an
    empty-state overlay on a chart), else both start-align. ``text`` may be empty: the action alone.
    """

    activated = pyqtSignal()

    def __init__(self, text: str, action_text: str, on_click: Optional[Callable[[], None]] = None,
                 *, stacked: bool = False, centered: bool = False, font_token: tuple = tokens.TYPE_CAPTION,
                 surface=None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("noticeLink")
        self._on_click = on_click
        self._action_text = str(action_text or "")
        self._surface = surface
        self._stacked = stacked
        self._centered = centered
        self._colors: dict = {}
        self._theming = False   # refresh_theme's own setStyleSheet sends a StyleChange - not a theme change

        direction = QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        lay = QBoxLayout(direction, self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.SPACE_XS if stacked else tokens.SPACE_S)
        self._layout = lay

        self._label = QLabel()
        self._label.setObjectName("noticeLinkText")
        self._label.setWordWrap(True)
        self._label.setFont(token_font(font_token))
        if centered:
            self._label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._button = _NoticeLinkButton()
        self._button.setFont(token_font(font_token))
        self._button.clicked.connect(self._fire)

        if stacked:
            # The label spans the full width (an alignment flag here would shrink it to its unwrapped
            # size hint and clip the wrapped lines); its own text alignment does the centring. The
            # button keeps its padding off the text's edge: pulled back by it when start-aligned, so
            # the link's words line up with the notice's.
            align = Qt.AlignmentFlag.AlignHCenter if centered else Qt.AlignmentFlag.AlignLeading
            lay.addWidget(self._label)
            lay.addWidget(self._button, 0, align)
        else:
            lay.addWidget(self._label, 1)
            lay.addWidget(self._button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_text(text)
        self._sync_action_text()
        self.refresh_theme()

    # -- content --------------------------------------------------------------------------------------
    def text(self) -> str:
        return self._text

    def action_text(self) -> str:
        return self._action_text

    def button(self) -> QPushButton:
        return self._button

    def label(self) -> QLabel:
        return self._label

    def set_text(self, text: str) -> None:
        self._text = str(text or "")
        set_ui_text(self._label, self._text)
        self._label.setVisible(bool(self._text))
        self._button.setAccessibleDescription(self._text)

    def set_action_text(self, action_text: str) -> None:
        self._action_text = str(action_text or "")
        self._sync_action_text()

    def set_on_click(self, on_click: Optional[Callable[[], None]]) -> None:
        """Swap what the action does (D5: a Settings deep link becomes an inline confirm)."""
        self._on_click = on_click

    def click(self) -> None:
        """Activate the action as a user would (tests, and a host forwarding a click)."""
        self._button.click()

    def _fire(self) -> None:
        self.activated.emit()
        cb = self._on_click
        if cb is None:
            return
        try:
            cb()
        except Exception as e:
            logger.error("NoticeLink action '%s' failed: %s", self._action_text, e, exc_info=True)

    def _sync_action_text(self) -> None:
        self._button.setText(self._action_text)
        apply_text_face(self._button, self._action_text)
        self._button.setAccessibleName(self._action_text)
        # QPushButton draws its icon before the text; the chevron trails it, so the button lays out in
        # the opposite direction of the notice (the action text is one run - its order doesn't change).
        rtl = self.isRightToLeft()
        self._button.setLayoutDirection(Qt.LayoutDirection.LeftToRight if rtl else Qt.LayoutDirection.RightToLeft)
        if self._stacked and not self._centered:
            # A box layout resolves AlignLeading with the ITEM's own direction - the button's, flipped
            # above - so the start edge is given absolutely, from the notice's direction.
            side = Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft
            self._layout.setAlignment(self._button, Qt.AlignmentFlag.AlignAbsolute | side)
        self._sync_chevron()

    def _sync_chevron(self) -> None:
        link = self._colors.get("link")
        if not link or not self._action_text:
            self._button.setIcon(QIcon())
            return
        from netspeedtray.utils.styles import fluent_icon
        cp = tokens.CHEVRON_LEFT_CODEPOINT if self.isRightToLeft() else tokens.CHEVRON_RIGHT_CODEPOINT
        px = tokens.NOTICE_LINK_CHEVRON_PX
        self._button.setIcon(fluent_icon(cp, px, link))
        self._button.setIconSize(QSize(px, px))

    def chevron_codepoint(self) -> Optional[int]:
        """The chevron the action shows (tests): ChevronRight, or ChevronLeft in RTL; None without one."""
        if self._button.icon().isNull():
            return None
        return tokens.CHEVRON_LEFT_CODEPOINT if self.isRightToLeft() else tokens.CHEVRON_RIGHT_CODEPOINT

    # Palette / style changes, and QEvent::ThemeChange (210 - a Windows light/dark or High Contrast
    # switch; PyQt6's QEvent.Type has no name for it), by value.
    _THEME_EVENTS = frozenset((QEvent.Type.PaletteChange.value, QEvent.Type.StyleChange.value,
                               QEvent.Type.ApplicationPaletteChange.value, 210))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        kind = event.type()
        if kind == QEvent.Type.LayoutDirectionChange:
            self._sync_action_text()
        elif kind.value in self._THEME_EVENTS and not self._theming:
            self.refresh_theme()

    # -- theme ----------------------------------------------------------------------------------------
    def _surface_hex(self) -> Optional[str]:
        s = self._surface
        try:
            return s() if callable(s) else s
        except Exception:
            return None

    def refresh_theme(self) -> None:
        """(Re)apply the colours: Light/Dark tokens, or High Contrast's own (see notice_link_colors)."""
        c = notice_link_colors(self._surface_hex())
        if c == self._colors:
            return
        self._colors = c
        bg = c["background"]
        hc = bg is not None
        ring = tokens.FOCUS_RING_WIDTH
        dark = is_dark_mode()
        hover_fill = "transparent" if hc else (tokens.SUBTLE_FILL_DARK if dark else tokens.SUBTLE_FILL_LIGHT)
        pressed_fill = "transparent" if hc else (tokens.NOTICE_LINK_PRESSED_FILL_DARK if dark
                                                 else tokens.NOTICE_LINK_PRESSED_FILL_LIGHT)
        pressed = QColor(c["link"])
        if not hc:
            pressed.setAlphaF(tokens.NOTICE_LINK_PRESSED_TEXT_ALPHA)
        pressed_text = f"rgba({pressed.red()}, {pressed.green()}, {pressed.blue()}, {pressed.alphaF():.2f})"
        under = "underline" if c["underline"] else "none"
        frame_bg = (f"QFrame#noticeLink {{ background-color: {bg}; border-radius: {tokens.RADIUS_CONTROL}px; }}"
                    if bg else "QFrame#noticeLink { background: transparent; }")
        self._theming = True
        try:
            self.setStyleSheet(
                frame_bg
                + f" QLabel#noticeLinkText {{ color: {c['text']}; background: transparent; }}"
                + f" QPushButton#noticeLinkAction {{ color: {c['link']}; background: transparent;"
                  f" border: {ring}px solid transparent; border-radius: {tokens.RADIUS_CONTROL}px;"
                  f" padding: {tokens.NOTICE_LINK_PAD_V}px {tokens.NOTICE_LINK_PAD_H}px;"
                  f" text-decoration: {under}; }}"
                + f" QPushButton#noticeLinkAction:hover {{ background: {hover_fill}; }}"
                + f" QPushButton#noticeLinkAction:pressed {{ background: {pressed_fill}; color: {pressed_text}; }}"
                + f" QPushButton#noticeLinkAction:focus {{ border: {ring}px solid {c['focus']}; }}"
            )
        finally:
            self._theming = False
        if bg:
            self._layout.setContentsMargins(tokens.SPACE_S, tokens.SPACE_XS, tokens.SPACE_S, tokens.SPACE_XS)
        else:
            self._layout.setContentsMargins(0, 0, 0, 0)
        self._sync_chevron()

    def colors(self) -> dict:
        """The colours last applied (tests, and a host that wants to match them)."""
        return dict(self._colors)


class _AnchorHighlight(QWidget):
    """Where a Settings deep link landed: an accent tint with a hairline accent stroke over the card -
    deliberately NOT a focus ring (the focused switch shows its own). See flash_highlight."""

    _DISMISS_EVENTS = (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.Wheel)

    def __init__(self, target: QWidget, color: str, cap_ms: int) -> None:
        super().__init__(target)
        self.setObjectName("anchorHighlight")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._color = QColor(color)
        self._opacity = 1.0
        self._cap_ms = int(cap_ms)
        self._armed = False          # the cap timer starts at the first paint, not at open_page
        self._dismissed = False
        self._fade = None
        self._cap = QTimer(self)
        self._cap.setSingleShot(True)
        self._cap.timeout.connect(self.dismiss)
        self.setGeometry(target.rect())
        target.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)   # the user's first click / key / scroll in this window
        self.raise_()

    def is_armed(self) -> bool:
        return self._armed

    def is_dismissed(self) -> bool:
        return self._dismissed

    def _release_app_filter(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        kind = event.type()
        if obj is self.parentWidget() and kind == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        elif (kind in self._DISMISS_EVENTS and not self._dismissed and isinstance(obj, QWidget)
              and obj.window() is self.window()):
            self.dismiss()
        return False

    def dismiss(self) -> None:
        """Fade out (Reduce motion: just go)."""
        if self._dismissed:
            return
        self._dismissed = True
        self._cap.stop()
        self._release_app_filter()
        if prefers_reduced_motion():
            self.hide()
            self.deleteLater()
            return
        from PyQt6.QtCore import QVariantAnimation
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(constants.timers.SETTINGS_ANCHOR_FADE_MS)
        anim.valueChanged.connect(self._set_opacity)
        anim.finished.connect(self.deleteLater)
        self._fade = anim
        anim.start()

    def _set_opacity(self, value) -> None:
        self._opacity = float(value)
        self.update()

    def paintEvent(self, event) -> None:
        if not self._armed:
            self._armed = True
            self._cap.start(self._cap_ms)
        from PyQt6.QtGui import QPen
        stroke = tokens.ANCHOR_HIGHLIGHT_STROKE
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(self._opacity)
        fill = QColor(self._color)
        fill.setAlphaF(tokens.ANCHOR_HIGHLIGHT_FILL_ALPHA)
        pen = QPen(self._color)
        pen.setWidth(stroke)
        p.setPen(pen)
        p.setBrush(fill)
        inset = stroke / 2.0
        from PyQt6.QtCore import QRectF
        p.drawRoundedRect(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset),
                          tokens.RADIUS_CARD, tokens.RADIUS_CARD)


def flash_highlight(target: QWidget, cap_ms: Optional[int] = None) -> Optional[QWidget]:
    """Tint ``target`` (a setting card) - where a deep link landed. It stays until the user's first
    click, key press or scroll in that window, or ``cap_ms`` after the dialog first paints it, then fades
    out (Reduce motion: removed at once). The colour is the (readable) accent; High Contrast: the
    theme's highlight colour. Returns the highlight widget."""
    if target is None:
        return None
    if cap_ms is None:
        cap_ms = constants.timers.SETTINGS_ANCHOR_HIGHLIGHT_MS
    for old in target.findChildren(QWidget, "anchorHighlight"):
        old.dismiss() if isinstance(old, _AnchorHighlight) else old.deleteLater()
    overlay = _AnchorHighlight(target, notice_link_colors()["focus"], cap_ms)
    overlay.show()
    return overlay
