"""
A small frameless callout ("flyout") anchored near a point on screen.

A calm, non-modal card next to the widget: data-cap alerts, one-time notices, the automatic
update offer, and (2.2.2) the first-run tip and the one-time "what's new" after an update
(views/widget/startup_notices.py). It does NOT use a system-tray icon (NetSpeedTray
deliberately has none).

Frameless + translucent + always-on-top + shown without stealing focus. Auto-dismisses
after a timeout unless built with ``auto_dismiss_ms=0``. The window paints its own card: a soft
shadow in the transparent margin, the rounded body and - when shown with ``show_for(...,
tail=True)`` - a small tail pointing at the widget (a Windows 11 TeachingTip). Built on the design
tokens so it matches the app; under a contrast theme it draws in the theme's system colours.
"""
from __future__ import annotations

import ctypes
from typing import Optional

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QSizePolicy,
)

from netspeedtray.utils import styles as su
from netspeedtray.constants.styles import styles as tokens

# The window's transparent margin around the card body: room for the soft shadow and the tail.
_MARGIN = 10
_TAIL_W, _TAIL_H = 12, 6          # the TeachingTip tail: 12 px wide at the body, 6 px deep
_BODY_GAP = 10                    # body edge to the widget (the tail's tip sits 4 px from it)
_BUTTON_MIN_H = 32
_BUTTON_MIN_W = 96
_SPI_GETSCREENREADER = 0x0046
_WM_ACTIVATE = 0x0006
_WA_INACTIVE = 0


def screen_reader_running() -> bool:
    """True while a screen reader (Narrator, NVDA, JAWS) says it is running (SPI_GETSCREENREADER).
    Any failure reads as False."""
    try:
        flag = ctypes.c_int(0)
        if ctypes.windll.user32.SystemParametersInfoW(_SPI_GETSCREENREADER, 0, ctypes.byref(flag), 0):
            return bool(flag.value)
    except Exception:
        pass
    return False


def restore_foreground(own_hwnd: int, prev_hwnd: int, user32=None) -> bool:
    """Give the foreground back to ``prev_hwnd`` - only while the card (``own_hwnd``) still holds it
    and ``prev_hwnd`` is a live, visible window. Closing the card then never changes which window has
    the keyboard. True when it was handed back."""
    if not own_hwnd or not prev_hwnd or own_hwnd == prev_hwnd:
        return False
    try:
        u = user32 if user32 is not None else ctypes.windll.user32
        if int(u.GetForegroundWindow() or 0) != int(own_hwnd):
            return False
        if not u.IsWindow(prev_hwnd) or not u.IsWindowVisible(prev_hwnd):
            return False
        return bool(u.SetForegroundWindow(prev_hwnd))
    except Exception:
        return False


class _FlyoutButton(QPushButton):
    """A card button whose focus ring is drawn for KEYBOARD focus only (Tab / Shift+Tab / a shortcut),
    like Windows: the ``kbfocus`` property drives the ring in the style sheet."""

    _KEYBOARD_REASONS = (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                         Qt.FocusReason.ShortcutFocusReason)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._set_keyboard_focus(event.reason() in self._KEYBOARD_REASONS)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._set_keyboard_focus(False)

    def mousePressEvent(self, event) -> None:
        self._set_keyboard_focus(False)
        super().mousePressEvent(event)

    def _set_keyboard_focus(self, on: bool) -> None:
        if bool(self.property("kbfocus")) == on:
            return
        self.setProperty("kbfocus", on)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()


class Flyout(QWidget):
    """A frameless, auto-dismissing callout. Call show_at(point) or show_for(widget_geo, ...).

    ``secondary_text``: a second button that just closes the card ("Got it"). With it, the card has
    no X (its own close button is the way out; Esc still works). ``action_style``: "accent" (the
    default) or "neutral" for a calmer card. ``close_text``: the accessible name of the X.
    ``focusable``: the card may take the keyboard once activated (Tab between the buttons, Enter /
    Space presses, Esc closes) - still shown without activating unless ``show_for(activate=True)``
    (a running screen reader), so it never takes the keyboard from the app the user is typing in.
    Off by default: an alert that can appear at any time stays a pure no-focus window."""
    dismissed = pyqtSignal()
    action_clicked = pyqtSignal()
    secondary_clicked = pyqtSignal()

    def __init__(self, title: str, message: str, action_text: Optional[str] = None,
                 auto_dismiss_ms: int = 12000, parent: Optional[QWidget] = None, *,
                 secondary_text: Optional[str] = None, close_text: Optional[str] = None,
                 focusable: bool = False, action_style: str = "accent") -> None:
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
        self._dismiss_emitted = False
        self._prev_foreground = 0          # the window this card took the foreground from, if it did
        self._tail_side: Optional[str] = None   # "top" / "bottom" / "left" / "right" of the body
        self._tail_at = 0                  # the tail's centre along that side, window coordinates
        self._anchor = None                # (avail, edge, bounds, tail) of the last show_for
        # Narrator / UI Automation: the window is named by its title and described by its text.
        self.setAccessibleName(title)
        self.setAccessibleDescription(message)

        c = self._colors = _card_colors()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_MARGIN, _MARGIN, _MARGIN, _MARGIN)

        card = QFrame()
        card.setObjectName("flyoutCard")
        card.setStyleSheet("QFrame#flyoutCard { background: transparent; border: none; }")  # painted below
        cl = QVBoxLayout(card)
        cl.setContentsMargins(tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_M)
        cl.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title_lbl = QLabel(title)
        title_lbl.setTextFormat(Qt.TextFormat.PlainText)
        title_lbl.setWordWrap(True)
        title_lbl.setFont(su.font(tokens.TYPE_BODY_STRONG))
        title_lbl.setStyleSheet(f"color: {c['text_primary']}; background: transparent;")
        header.addWidget(title_lbl, 1)
        self._close_btn: Optional[QPushButton] = None
        if not secondary_text:
            # A card with its own "Got it" needs no X: one way out, and one less Tab stop.
            close_btn = _FlyoutButton(chr(0xE8BB))   # Segoe Fluent "ChromeClose"
            close_btn.setFixedSize(20, 20)
            close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if close_text:
                close_btn.setAccessibleName(close_text)
                close_btn.setToolTip(close_text)
            if not focusable:
                close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            close_btn.setStyleSheet(
                f"QPushButton {{ color: {c['text_secondary']}; background: transparent;"
                f" border: 1px solid transparent; border-radius: {tokens.RADIUS_CONTROL}px;"
                f" font-family: '{su.icon_font_family()}'; font-size: 11px; }}"   # one family (no fallback walk)
                f"QPushButton:hover {{ color: {c['text_primary']}; }}"
                f"QPushButton[kbfocus=\"true\"] {{ color: {c['text_primary']}; border-color: {c['focus']}; }}"
            )
            close_btn.clicked.connect(self._dismiss)
            header.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
            self._close_btn = close_btn
        cl.addLayout(header)
        cl.addSpacing(4)                     # title -> body

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
            cl.addSpacing(12)                # body -> buttons
            row = QHBoxLayout()              # mirrored automatically in a right-to-left language
            row.setSpacing(8)
            two = bool(action_text and secondary_text)
            if not two:
                row.addStretch(1)
            if action_text:
                # Under a contrast theme the primary is filled with Highlight, as the internet confirm's
                # (combined review U7); its focus ring is then the window text (a Highlight ring would
                # vanish on it). Every other button keeps the Highlight ring.
                accent = action_style == "accent"
                act = self._make_button(action_text, accent=accent, fill=two)
                act.clicked.connect(self._on_action)
                row.addWidget(act, 1 if two else 0)
                self._action_btn = act
            if secondary_text:
                sec = self._make_button(secondary_text, accent=False, fill=two)
                sec.clicked.connect(self._on_secondary)
                row.addWidget(sec, 1 if two else 0)
                self._secondary_btn = sec
            for btn in (self._action_btn, self._secondary_btn):
                if btn is not None and not focusable:
                    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if two:
                # Equal widths, each wide enough for the longer label (a stretch split alone squeezed
                # "Open Monitor" under its own padding).
                need = max(self._label_width(b) for b in (self._action_btn, self._secondary_btn))
                for b in (self._action_btn, self._secondary_btn):
                    b.setMinimumWidth(max(_BUTTON_MIN_W, need))
            cl.addLayout(row)

        outer.addWidget(card)
        if focusable:
            # Tab order: the answer first, the X (if any) last - set once all share this window.
            chain = [b for b in (self._action_btn, self._secondary_btn, self._close_btn) if b is not None]
            for a, b in zip(chain, chain[1:]):
                QWidget.setTabOrder(a, b)
            if chain:
                chain[0].setFocus()   # where the keyboard lands when the card is activated (not the X)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dismiss)

    def _make_button(self, text: str, *, accent: bool, fill: bool) -> QPushButton:
        """A TeachingTip button: 32 px tall, 5/12/6 padding. The focus ring only changes a border's
        colour (never its width - no text shift or clipping), and only for keyboard focus."""
        c = self._colors
        btn = _FlyoutButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFont(su.font(tokens.TYPE_BODY))
        btn.setMinimumHeight(_BUTTON_MIN_H)
        btn.setMinimumWidth(_BUTTON_MIN_W)
        if fill:
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        radius = tokens.RADIUS_CONTROL
        if accent:
            normal = (f"background-color: {c['accent']}; color: {c['accent_text']};"
                      f" border: 2px solid transparent; padding: 5px 12px 6px;")
            focus = f"border-color: {c['text_primary'] if c.get('high_contrast') else c['focus']};"
        else:
            # A 1 px stroke at rest; the 2 px ring takes the extra pixel from the padding, not the text.
            normal = (f"background-color: {c['subtle_fill']}; color: {c['text_primary']};"
                      f" border: 1px solid {c['card_stroke']}; padding: 6px 13px 7px;")
            focus = f"border: 2px solid {c['focus']}; padding: 5px 12px 6px;"
        btn.setStyleSheet(
            f"QPushButton {{ {normal} border-radius: {radius}px; }}"
            f"QPushButton[kbfocus=\"true\"] {{ {focus} }}"
        )
        return btn

    @staticmethod
    def _label_width(btn: QPushButton) -> int:
        """The width a card button needs: its text plus the 12 px padding and the 2 px border, each side."""
        return btn.fontMetrics().horizontalAdvance(btn.text()) + 2 * (12 + 2) + 4

    # --- showing ---------------------------------------------------------------------------------

    def show_at(self, point: QPoint) -> None:
        """Show the flyout with its bottom-right near `point` (e.g. the widget), nudged
        on-screen. Starts the auto-dismiss timer."""
        self._tail_side = None
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
        self._present(False)

    def show_for(self, widget_geo: QRect, avail: QRect, edge: Optional[object] = None,
                 screen=None, *, tail: bool = False, activate: bool = False) -> None:
        """Show the card beside the widget, the way the hover card is placed (UsageFlyout.placement):
        above it on a bottom taskbar, below on a top one, beside it on a side bar, flipping when there
        is no room, never over the widget. ``tail``: a 12x6 tail on the body's widget-facing side,
        pointing at the widget's centre - clamped along that side at a screen edge (the card is
        clamped into the work area, the tail keeps pointing). ``activate``: show WITH activation (a
        screen reader is running, so Narrator reads it); otherwise it never takes the keyboard."""
        if screen is not None:
            try:
                self.winId()
                handle = self.windowHandle()
                if handle is not None:
                    handle.setScreen(screen)
            except Exception:
                pass
        bounds = None
        try:
            bounds = screen.geometry() if screen is not None else None
        except Exception:
            bounds = None
        self._anchor = (avail, edge, bounds, tail)
        self.adjustSize()
        self._place(widget_geo)
        self._present(activate)

    def reanchor(self, widget_geo: QRect) -> None:
        """The widget moved: follow it (only a card shown with show_for)."""
        if self._anchor is None:
            return
        self._place(widget_geo)

    def _place(self, widget_geo: QRect) -> None:
        from netspeedtray.views.usage_flyout import UsageFlyout, _GAP_ABOVE
        avail, edge, bounds, tail = self._anchor
        # placement() keeps _GAP_ABOVE between the widget and the WINDOW; this window has a
        # _MARGIN-wide transparent rim, so hand it the widget shrunk by the difference: the body then
        # sits _BODY_GAP from the real widget, and the window edge just touches it (never over it).
        k = max(0, _GAP_ABOVE + _MARGIN - _BODY_GAP)
        kx = min(k, max(0, (widget_geo.width() - 2) // 2))
        ky = min(k, max(0, (widget_geo.height() - 2) // 2))
        target = widget_geo.adjusted(kx, ky, -kx, -ky)
        rect = UsageFlyout.placement(target, avail, self.size(), edge, bounds=bounds)
        self.move(rect.x(), rect.y())
        self._tail_side = self._facing_side(rect, widget_geo) if tail else None
        if self._tail_side in ("top", "bottom"):
            along = widget_geo.center().x() - rect.x()
            lo = _MARGIN + tokens.RADIUS_CARD + _TAIL_W // 2
            hi = rect.width() - _MARGIN - tokens.RADIUS_CARD - _TAIL_W // 2
        elif self._tail_side in ("left", "right"):
            along = widget_geo.center().y() - rect.y()
            lo = _MARGIN + tokens.RADIUS_CARD + _TAIL_W // 2
            hi = rect.height() - _MARGIN - tokens.RADIUS_CARD - _TAIL_W // 2
        else:
            along, lo, hi = 0, 0, 0
        self._tail_at = max(lo, min(along, hi)) if hi >= lo else (lo + hi) // 2
        self.update()

    @staticmethod
    def _facing_side(rect: QRect, widget_geo: QRect) -> Optional[str]:
        """The body side that faces the widget, or None when the card could not avoid it."""
        if rect.y() + rect.height() <= widget_geo.y():
            return "bottom"
        if rect.y() >= widget_geo.y() + widget_geo.height():
            return "top"
        if rect.x() + rect.width() <= widget_geo.x():
            return "right"
        if rect.x() >= widget_geo.x() + widget_geo.width():
            return "left"
        return None

    def tail(self):
        """(side, centre along it) of the tail, or None (tests, screenshots)."""
        return (self._tail_side, self._tail_at) if self._tail_side else None

    def _present(self, activate: bool) -> None:
        if activate and self._focusable:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            self.show()
            self.raise_()
            self.activateWindow()
        else:
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

    # --- painting --------------------------------------------------------------------------------

    def _body_path(self) -> QPainterPath:
        body = QRectF(self.rect()).adjusted(_MARGIN + 0.5, _MARGIN + 0.5, -_MARGIN - 0.5, -_MARGIN - 0.5)
        path = QPainterPath()
        path.addRoundedRect(body, tokens.RADIUS_CARD, tokens.RADIUS_CARD)
        side, at, half = self._tail_side, float(self._tail_at), _TAIL_W / 2
        if side:
            if side == "bottom":
                pts = [(at - half, body.bottom() - 1), (at, body.bottom() + _TAIL_H), (at + half, body.bottom() - 1)]
            elif side == "top":
                pts = [(at - half, body.top() + 1), (at, body.top() - _TAIL_H), (at + half, body.top() + 1)]
            elif side == "right":
                pts = [(body.right() - 1, at - half), (body.right() + _TAIL_H, at), (body.right() - 1, at + half)]
            else:
                pts = [(body.left() + 1, at - half), (body.left() - _TAIL_H, at), (body.left() + 1, at + half)]
            tail = QPainterPath()
            tail.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
            tail.closeSubpath()
            path = path.united(tail)
        return path

    def paintEvent(self, event) -> None:
        c = self._colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = self._body_path()
        if not c.get("high_contrast"):
            # A soft Fluent-style shadow in the transparent rim: a few widening, fading strokes of the
            # card's own outline, nudged down. Cheap (no graphics effect on the buttons' subtree).
            dark = su.is_dark_mode()
            p.translate(0, 2)
            for width, alpha in ((8, 6 if not dark else 14), (5, 9 if not dark else 20), (2, 14 if not dark else 30)):
                p.setPen(QPen(QColor(0, 0, 0, alpha), width, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            p.translate(0, -2)
        p.setPen(QPen(QColor(c["card_stroke"]), 1))
        p.setBrush(QColor(c["card_bg"]))
        p.drawPath(path)
        p.end()

    # --- input and closing -----------------------------------------------------------------------

    def nativeEvent(self, event_type, message):
        """Remember the window this card took the foreground from (WM_ACTIVATE's lParam), so closing
        the card hands the keyboard back to it (restore_foreground).

        CRITICAL (2.2.2 live test F1): observe only and return ``(False, 0)``. Never return
        ``super().nativeEvent(...)`` - under PyQt 6.10 it gives ``(False, None)``, and that ``None``
        handed back as the native result access-violates in QtCore.pyd during the window's creation
        (every card crashed the app on its first show). Never call ``winId()`` here either: this runs
        while the native window is being created; the message's own hwnd is the card's window."""
        try:
            raw = event_type.data() if hasattr(event_type, "data") else bytes(event_type)
            if raw == b"windows_generic_MSG" and message:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == _WM_ACTIVATE and (msg.wParam & 0xFFFF) != _WA_INACTIVE:
                    prev = int(msg.lParam or 0)
                    if prev and prev != int(msg.hWnd or 0):
                        self._prev_foreground = prev
        except Exception:
            pass
        return False, 0

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

    def closeEvent(self, event) -> None:
        """Every way out - a button, Esc, Alt+F4, a system close, a replacement - reports ``dismissed``
        exactly once, and gives the foreground back if the card had taken it."""
        self._timer.stop()
        try:
            own = int(self.internalWinId())       # sip.voidptr: int() works, truthiness does not
        except Exception:
            own = 0
        try:
            restore_foreground(own, self._prev_foreground)
        except Exception:
            pass
        if not self._dismiss_emitted:
            self._dismiss_emitted = True
            self.dismissed.emit()
        super().closeEvent(event)

    def _on_action(self) -> None:
        self.action_clicked.emit()
        self._dismiss()

    def _on_secondary(self) -> None:
        self.secondary_clicked.emit()
        self._dismiss()

    def _dismiss(self) -> None:
        self._timer.stop()
        if not self._dismiss_emitted:
            self._dismiss_emitted = True
            self.dismissed.emit()
        self.close()


def _card_colors() -> dict:
    """The card's colours: the app's theme tokens - or, under a Windows contrast theme, the theme's
    own system colours (window, window text, highlight), as Windows expects an app to draw then. The
    keyboard focus ring is the text colour (black on light, white on dark), Highlight in HC."""
    from netspeedtray.utils.taskbar_appearance import read_high_contrast_ui_colors
    hc = read_high_contrast_ui_colors()
    if hc:
        return {"card_bg": hc["background"], "card_stroke": hc["text"], "subtle_fill": hc["background"],
                "text_primary": hc["text"], "text_secondary": hc["text"],
                "accent": hc["highlight"], "accent_text": hc["highlight_text"], "focus": hc["highlight"],
                "high_contrast": True}
    c = dict(su.semantic_colors())
    c["accent"] = su.get_accent_color().name()
    c["accent_text"] = "white"
    c["focus"] = c["text_primary"]
    return c
