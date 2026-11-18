"""
A small frameless callout ("flyout") anchored near a point on screen.

A calm, non-modal card next to the widget: data-cap alerts, one-time notices, the automatic
update offer, and (2.2.2) the first-run tip and the one-time "what's new" after an update
(views/widget/startup_notices.py). It does NOT use a system-tray icon (NetSpeedTray
deliberately has none).

Frameless + translucent + always-on-top + shown without stealing focus. Auto-dismisses
after a timeout (skipped when the OS / config asks to reduce motion is irrelevant here -
it's a timeout, not an animation). Built on the design tokens so it matches the app.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens


class Flyout(QWidget):
    """A frameless, auto-dismissing callout. Call show_at(point) to display it.

    ``secondary_text``: a second, neutral button that just closes the card ("Got it") - beside the
    accent ``action_text`` one. ``close_text``: the accessible name of the X (a screen reader would
    otherwise read the icon glyph). ``focusable``: the card may take the keyboard once the user
    clicks into it (Tab between the buttons, Enter / Space presses, Esc closes) - still shown without
    activating, so it never takes the keyboard from the app the user is typing in. Off by default:
    an alert that can appear at any time stays a pure no-focus window (WS_EX_NOACTIVATE)."""
    dismissed = pyqtSignal()
    action_clicked = pyqtSignal()
    secondary_clicked = pyqtSignal()

    def __init__(self, title: str, message: str, action_text: Optional[str] = None,
                 auto_dismiss_ms: int = 12000, parent: Optional[QWidget] = None, *,
                 secondary_text: Optional[str] = None, close_text: Optional[str] = None,
                 focusable: bool = False) -> None:
        # WindowDoesNotAcceptFocus (native WS_EX_NOACTIVATE): neither showing it nor clicking its
        # buttons takes the keyboard from the app the user is typing in - it can appear at any time
        # (a data-cap alert, the side-bar no-room notice, rc.2 W4). WA_ShowWithoutActivating alone
        # only covers the show - which is what a ``focusable`` card (the first-run tip) wants.
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint)
        if not focusable:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)  # never steal focus
        # Single-use, self-dismissing toast: free it on close instead of letting the closed-but-hidden
        # parentless widget linger held by the caller's reference until the next alert replaces it (#20).
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        if parent is None:
            # Qt destroys it (on close, on the GUI thread) - never Python's GC (utils/qt_lifetime).
            from netspeedtray.utils.qt_lifetime import hand_to_qt
            hand_to_qt(self)
        self._auto_dismiss_ms = auto_dismiss_ms
        self._focusable = focusable
        # Narrator / UI Automation: the window is named by its title and described by its text.
        self.setAccessibleName(title)
        self.setAccessibleDescription(message)

        c = _card_colors()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)  # room so the card's shadow/border isn't clipped

        card = QFrame()
        card.setObjectName("flyoutCard")
        card.setStyleSheet(
            f"QFrame#flyoutCard {{ background-color: {c['card_bg']}; "
            f"border: 1px solid {c['card_stroke']}; border-radius: {tokens.RADIUS_CARD}px; }}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_M)
        cl.setSpacing(tokens.SPACE_S)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_lbl = QLabel(title)
        title_lbl.setTextFormat(Qt.TextFormat.PlainText)
        title_lbl.setFont(su.font(tokens.TYPE_BODY_STRONG))
        title_lbl.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        header.addWidget(title_lbl, 1)
        close_btn = QPushButton(chr(0xE8BB))   # Segoe Fluent "ChromeClose"
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if close_text:
            close_btn.setAccessibleName(close_text)
            close_btn.setToolTip(close_text)
        if not focusable:
            close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.setStyleSheet(
            f"QPushButton {{ color: {c['text_secondary']}; background: transparent; border: none;"
            f" font-family: '{su.icon_font_family()}'; font-size: 11px; }}"   # one family (no fallback walk)
            f"QPushButton:hover {{ color: {c['text_primary']}; }}"
            f"QPushButton:focus {{ color: {c['text_primary']}; border: 1px solid {c['focus']};"
            f" border-radius: {tokens.RADIUS_CONTROL}px; }}"
        )
        close_btn.clicked.connect(self._dismiss)
        header.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        cl.addLayout(header)
        self._close_btn = close_btn

        msg = QLabel(message)
        msg.setTextFormat(Qt.TextFormat.PlainText)   # translated text, never parsed as HTML
        msg.setWordWrap(True)
        msg.setMinimumWidth(280)
        msg.setMaximumWidth(360)
        msg.setFont(su.font(tokens.TYPE_BODY))
        msg.setStyleSheet(f"color: {c['text_secondary']}; background: transparent;")
        cl.addWidget(msg)
        self._message_label = msg

        self._action_btn: Optional[QPushButton] = None
        self._secondary_btn: Optional[QPushButton] = None
        if action_text or secondary_text:
            row = QHBoxLayout()   # mirrored automatically in a right-to-left language
            row.addStretch(1)
            focus_css = (f"QPushButton:focus {{ border: 2px solid {c['focus']}; }}" if focusable else "")
            if action_text:
                act = QPushButton(action_text)
                act.setCursor(Qt.CursorShape.PointingHandCursor)
                act.setFont(su.font(tokens.TYPE_BODY))
                act.setStyleSheet(
                    f"QPushButton {{ background-color: {c['accent']}; color: {c['accent_text']}; border: none; "
                    f"border-radius: {tokens.RADIUS_CONTROL}px; padding: 4px 14px; }}" + focus_css
                )
                act.clicked.connect(self._on_action)
                row.addWidget(act)
                self._action_btn = act
            if secondary_text:
                sec = QPushButton(secondary_text)
                sec.setCursor(Qt.CursorShape.PointingHandCursor)
                sec.setFont(su.font(tokens.TYPE_BODY))
                sec.setStyleSheet(
                    f"QPushButton {{ background-color: {c['subtle_fill']}; color: {c['text_primary']}; "
                    f"border: 1px solid {c['card_stroke']}; border-radius: {tokens.RADIUS_CONTROL}px; "
                    f"padding: 4px 14px; }}" + focus_css
                )
                sec.clicked.connect(self._on_secondary)
                row.addWidget(sec)
                self._secondary_btn = sec
            for btn in (self._action_btn, self._secondary_btn):
                if btn is not None and not focusable:
                    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            cl.addLayout(row)
        outer.addWidget(card)
        if focusable:
            # Tab order: the answer first, the X last (set once all three share this window).
            chain = [b for b in (self._action_btn, self._secondary_btn, close_btn) if b is not None]
            for a, b in zip(chain, chain[1:]):
                QWidget.setTabOrder(a, b)
            chain[0].setFocus()   # where the keyboard lands when the card is activated (not the X)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)

    def show_at(self, point: QPoint) -> None:
        """Show the flyout with its bottom-right near `point` (e.g. the widget), nudged
        on-screen. Starts the auto-dismiss timer."""
        self.adjustSize()
        sz = self.size()
        x = point.x() - sz.width()
        y = point.y() - sz.height()
        # Kept inside the work area of the screen holding `point`, not clamped at 0: a monitor left
        # of / above the primary has negative virtual coordinates (0 flung the flyout onto the
        # primary), and on a left / top taskbar 0 sat it over the taskbar.
        screen = QGuiApplication.screenAt(point) or self._nearest_screen(point)
        if screen is not None:
            area = screen.availableGeometry()
            if x < area.left():       # no room to the left of the anchor: open to its right
                x = point.x()
            if y < area.top():        # no room above it: open below
                y = point.y()
            x = max(area.left(), min(x, area.right() + 1 - sz.width()))
            y = max(area.top(), min(y, area.bottom() + 1 - sz.height()))
        self.move(x, y)
        self.show()
        if self._auto_dismiss_ms > 0:
            self._timer.start(self._auto_dismiss_ms)

    @staticmethod
    def _nearest_screen(point: QPoint):
        """The screen whose geometry lies closest to `point`, for an anchor screenAt() misses: with
        mixed-DPI monitors the virtual desktop has gaps between screens, and the widget's corner can
        sit in one. Not the primary - that is the jump to the wrong monitor show_at avoids."""
        def distance(screen) -> int:
            g = screen.geometry()
            dx = max(g.left() - point.x(), 0, point.x() - g.right())
            dy = max(g.top() - point.y(), 0, point.y() - g.bottom())
            return dx * dx + dy * dy
        screens = QGuiApplication.screens()
        return min(screens, key=distance) if screens else QGuiApplication.primaryScreen()

    def keyPressEvent(self, event) -> None:
        """A focusable card: Esc closes it; Enter presses the focused button (Space already does)."""
        key = event.key()
        if self._focusable and key == Qt.Key.Key_Escape:
            self._dismiss()
            return
        if self._focusable and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = self.focusWidget()
            if isinstance(focused, QPushButton):
                focused.click()
                return
        super().keyPressEvent(event)

    def _on_action(self) -> None:
        self.action_clicked.emit()
        self._dismiss()

    def _on_secondary(self) -> None:
        self.secondary_clicked.emit()
        self._dismiss()

    def _dismiss(self) -> None:
        self._timer.stop()
        self.dismissed.emit()
        self.close()


def _card_colors() -> dict:
    """The card's colours: the app's theme tokens - or, under a Windows contrast theme, the theme's
    own system colours (window, window text, highlight), as Windows expects an app to draw then."""
    from netspeedtray.utils.taskbar_appearance import read_high_contrast_ui_colors
    hc = read_high_contrast_ui_colors()
    if hc:
        return {"card_bg": hc["background"], "card_stroke": hc["text"], "subtle_fill": hc["background"],
                "text_primary": hc["text"], "text_secondary": hc["text"],
                "accent": hc["highlight"], "accent_text": hc["highlight_text"], "focus": hc["text"]}
    c = dict(su.semantic_colors())
    c["accent"] = su.get_accent_color().name()
    c["accent_text"] = "white"
    c["focus"] = c["text_primary"]
    return c
