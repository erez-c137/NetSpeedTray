"""
Custom Qt Widget Components for NetSpeedTray.

This module provides custom UI widgets like Win11Toggle and Win11Slider,
designed to offer a look and feel consistent with Windows 11 modern UI elements.
These components are used throughout the application's settings interfaces
to provide a cohesive user experience.
"""

import logging
import re
from typing import Optional, Final
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QCheckBox, QLabel, QSlider,
    QSizePolicy, QStyleOption, QLineEdit, QFrame, QToolButton, QButtonGroup,
    QPushButton, QColorDialog, QComboBox,
    QApplication, QScrollArea, QAbstractSlider, QAbstractSpinBox
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QPropertyAnimation, QEasingCurve, QPoint, QTimer, QObject, QEvent
from PyQt6.QtGui import QFont, QPaintEvent, QPainter, QColor
from netspeedtray.utils.styles import (
    toggle_style, slider_style, font as token_font, semantic_colors, get_accent_color,
    timeline_pills_style, prefers_reduced_motion, is_dark_mode,
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


_WHEEL_GUARD = _WheelGuard()   # single shared, stateless instance


def install_wheel_guard(root: QWidget) -> None:
    """Install the wheel guard on every scroll-sensitive control under `root`.

    Call once on a page/dialog (e.g. after building a settings page): scrolling the page then
    scrolls it, instead of changing the slider/combo/spin box the cursor passes over. Covers the
    custom Win11 controls too, since Win11ComboBox is a QComboBox and Win11Slider wraps a QSlider.
    """
    for cls in (QComboBox, QAbstractSpinBox, QAbstractSlider):
        for w in root.findChildren(cls):
            w.installEventFilter(_WHEEL_GUARD)
            # Drop wheel-focus so a scroll can't first focus the control and then change it.
            if w.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class Win11ComboBox(QComboBox):
    """A drop-in QComboBox whose pop-up list is guaranteed opaque.

    Qt 6 gives every combo pop-up a *translucent* container window (``WA_TranslucentBackground``, used
    for the native rounded drop-shadow). Under our dark theme the styled item-view didn't actually
    cover that container on screen, so the open list rendered see-through over whatever it overlapped -
    unreadable. We force both the container window and its viewport opaque each time the pop-up opens
    (the container is created lazily on the first show and then reused). Colours still come from the
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
        label = QLabel(text, self)
        # Segoe UI Variable is Windows 11 only; fall back to Segoe UI on Win10
        # to avoid Qt picking an unintended serif fallback (#149).
        font = QFont()
        font.setFamilies(["Segoe UI Variable", "Segoe UI"])
        font.setPointSize(10)
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
        self.checkbox.setStyleSheet(checkbox_qss)

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
            animate = False   # honour Windows "Animation effects" off - snap instead of slide
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

        # Win11 family with Win10 fallback (#149).
        label_font = QFont()
        label_font.setFamilies(["Segoe UI Variable", "Segoe UI"])
        label_font.setPointSize(10)
        
        if self._editable:
            self._value_input = QLineEdit(self) 
            self._value_input.setFont(label_font)
            self._value_input.setMinimumWidth(70) 
            self._value_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._value_input.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self._value_input.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            
            self._update_input_style()
            self._value_input.setText(f"{initial_val}{self._suffix}")
            
            layout.addWidget(self._value_input)
            self._value_input.editingFinished.connect(self._on_input_editing_finished)
        else:
            self._value_label = QLabel(f"{initial_val}{self._suffix}", self)
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


    def _on_internal_slider_value_changed(self, value: int) -> None:
        if self._auto_update_label:
            self.setValueText(f"{value}{self._suffix}")
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
                     self.setValueText(f"{new_val}{self._suffix}")
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


class CollapsibleSection(QWidget):
    """
    A collapsible card section with a clickable header and expandable content area.
    Used to reduce visual density on busy settings pages.
    """
    toggled = pyqtSignal(bool)

    def __init__(self, title: str, expanded: bool = True, parent: Optional[QWidget] = None):
        super().__init__(parent)
        from netspeedtray.utils.styles import collapsible_section_style

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(constants.layout.COLLAPSIBLE_SECTION_SPACING)

        # --- Header ---
        self._header = QWidget()
        self._header.setObjectName("collapsibleHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setFixedHeight(constants.layout.COLLAPSIBLE_HEADER_HEIGHT)
        self._header.setStyleSheet(collapsible_section_style())
        self._header.mousePressEvent = self._on_header_clicked

        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(8, 0, 8, 0)
        header_layout.setSpacing(6)

        self._chevron = QLabel()
        self._chevron.setObjectName("sectionChevron")
        self._chevron.setFixedWidth(16)
        header_layout.addWidget(self._chevron)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("sectionTitle")
        header_layout.addWidget(self._title_label, stretch=1)

        outer_layout.addWidget(self._header)

        # --- Content ---
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(10)
        outer_layout.addWidget(self._content)

        # --- Initial state ---
        self._expanded = expanded
        self._update_state()

    def _on_header_clicked(self, event) -> None:
        self._expanded = not self._expanded
        self._update_state()
        self.toggled.emit(self._expanded)

    def _update_state(self) -> None:
        self._content.setVisible(self._expanded)
        self._chevron.setText("\u25BE" if self._expanded else "\u25B8")

    def setExpanded(self, expanded: bool) -> None:
        if self._expanded != expanded:
            self._expanded = expanded
            self._update_state()
            self.toggled.emit(self._expanded)

    def isExpanded(self) -> bool:
        return self._expanded

    def contentLayout(self) -> QVBoxLayout:
        """Returns the layout to add child widgets to."""
        return self._content_layout


# =============================================================================
# Design-system primitives (Fluent settings vocabulary). These consume the
# tokens in constants/styles.py via utils/styles.font()/semantic_colors() so the
# Settings dialog and the future Monitor window share one visual language.
# =============================================================================

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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, tokens.SPACE_M, 16, tokens.SPACE_M)
        layout.setSpacing(tokens.SPACE_M)

        if icon:
            glyph = QLabel(icon)
            glyph.setFixedWidth(22)
            glyph.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
            layout.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        self._title_label = QLabel(title)
        self._title_label.setFont(token_font(tokens.TYPE_BODY_STRONG))
        self._title_label.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        text_col.addWidget(self._title_label)
        if description:
            self._desc_label = QLabel(description)
            self._desc_label.setFont(token_font(tokens.TYPE_CAPTION))
            self._desc_label.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
            self._desc_label.setWordWrap(True)
            text_col.addWidget(self._desc_label)
        layout.addLayout(text_col, 1)

        if control is not None:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)

    def control(self) -> Optional[QWidget]:
        return self._control


class SettingExpander(QWidget):
    """
    A Fluent settings expander: a header row (title + optional muted description +
    optional master toggle) over a collapsible content area of child cards. With
    ``header_toggle=True`` a Win11Toggle in the header both controls the feature and
    drives the body's visibility - the native Win11 Settings disclosure idiom.
    """
    toggled = pyqtSignal(bool)          # master-toggle state (only when header_toggle)
    expandedChanged = pyqtSignal(bool)

    def __init__(self, title: str, description: str = "", header_toggle: bool = False,
                 initial_on: bool = False, expanded: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingExpander")
        self._has_toggle = header_toggle
        c = semantic_colors()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(tokens.SPACE_XS)

        self._header = QFrame()
        self._header.setObjectName("expanderHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setStyleSheet(
            f"QFrame#expanderHeader {{ background-color: {c['card_bg']}; "
            f"border: 1px solid {c['card_stroke']}; border-radius: {tokens.RADIUS_CARD}px; }}"
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
        text_col.addWidget(title_label)
        if description:
            d = QLabel(description)
            d.setFont(token_font(tokens.TYPE_CAPTION))
            d.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
            d.setWordWrap(True)
            text_col.addWidget(d)
        hl.addLayout(text_col, 1)

        self._toggle: Optional["Win11Toggle"] = None
        if header_toggle:
            self._toggle = Win11Toggle(initial_state=initial_on)
            self._toggle.toggled.connect(self._on_toggle)
            hl.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignVCenter)

        self._chevron = QLabel()
        self._chevron.setFixedWidth(16)
        # Native Win11 disclosure chevron (Segoe Fluent Icons), not a Unicode triangle.
        self._chevron.setStyleSheet(
            f"color: {c['text_secondary']}; background: transparent;"
            f" font-family: 'Segoe Fluent Icons','Segoe MDL2 Assets'; font-size: 12px;")
        hl.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)

        self._header.mousePressEvent = self._on_header_clicked
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

    def _on_header_clicked(self, event) -> None:
        if self._has_toggle and self._toggle is not None:
            self._toggle.setChecked(not self._toggle.isChecked())  # fires _on_toggle
        else:
            self.setExpanded(not self._expanded)

    def _sync(self) -> None:
        self._content.setVisible(self._expanded)
        # E70D ChevronDown (open) / E76C ChevronRight (closed).
        self._chevron.setText(chr(0xE70D) if self._expanded else chr(0xE76C))

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


class Win11Segmented(QWidget):
    """
    A Win11 segmented control: an exclusive row of joined pill buttons (the native
    idiom for a small mutually-exclusive choice - decimals 0/1/2, alignment L/C/R,
    update-rate Smart/1s/2s/…). Emits ``valueChanged(value)`` with the option's value.
    """
    valueChanged = pyqtSignal(object)

    def __init__(self, options, parent: Optional[QWidget] = None) -> None:
        """options: an iterable of (label, value) pairs."""
        super().__init__(parent)
        self._value = None
        self._items = []  # (value, button) in order; matched by equality, not repr()
        options = list(options)
        n = len(options)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        for i, (label, value) in enumerate(options):
            btn = QToolButton()
            btn.setText(str(label))
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if n == 1:
                btn.setObjectName("segOnly")
            elif i == 0:
                btn.setObjectName("segFirst")
            elif i == n - 1:
                btn.setObjectName("segLast")
            else:
                btn.setObjectName("segMid")
            btn.clicked.connect(lambda _checked=False, v=value: self.setValue(v))
            self._group.addButton(btn)
            layout.addWidget(btn)
            self._items.append((value, btn))
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
                font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
            }}
            QToolButton:hover {{ background-color: {c['subtle_fill']}; }}
            QToolButton:checked {{ background-color: {accent}; color: white; border-color: {accent}; }}
            QToolButton#segFirst {{ border-top-left-radius: {r}px; border-bottom-left-radius: {r}px; }}
            QToolButton#segLast  {{ border-top-right-radius: {r}px; border-bottom-right-radius: {r}px; }}
            QToolButton#segOnly  {{ border-radius: {r}px; }}
        """)

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
        self._seg = Win11Segmented(options)
        self._seg.valueChanged.connect(self._on_segment)
        for value, btn in self._seg._items:
            if value != self._CUSTOM:
                name = next((n for (n, _u, _d) in self._presets if n == value), value)
                key = "ARROW_PRESET_" + str(name).upper().replace(" ", "_")
                btn.setToolTip(self._tr(key, name))
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
        crow.addWidget(QLabel(self._tr("ARROW_CUSTOM_UP_LABEL", "Up")))
        crow.addWidget(self._up_edit)
        crow.addSpacing(tokens.SPACE_M)
        crow.addWidget(QLabel(self._tr("ARROW_CUSTOM_DOWN_LABEL", "Down")))
        crow.addWidget(self._down_edit)
        crow.addStretch(0)
        self._custom_row.setVisible(False)
        root.addWidget(self._custom_row)   # was orphaned after `return` in _tr (dead code) - the custom
        #                                    arrow row never entered the layout, so "Custom" did nothing.

    def _tr(self, key: str, default: str) -> str:
        """Graceful i18n lookup: the picker may be constructed without an i18n object."""
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default

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
