"""
The "NetSpeedTray is running" panel (2.2.2 D3) - what launching the app from Start shows while it runs.

NetSpeedTray has no tray icon, so before 2.2.2 a launch from Start while it ran only brought the widget
back - the most common way people "open" an app showed nothing they could use. Now a second launch that
is the USER asking (shutdown_channel.activation_kind: not a duplicate autostart at sign-in, not the
updater's relaunch) opens this small window after the widget is back (widget.handle_activate_request).

What it is: a normal, focusable window (a taskbar button, Alt+Tab, Narrator) - never a tool window and
never always-on-top - with a fixed size (it can't be dragged into odd shapes). Header: the app name,
"Version 2.2.2", a Beta badge on a prerelease, and an inline "Check for updates" link whose result
shows in place ("Checking…" -> "You're up to date" / "2.2.3 is available - Update"); that check keeps
the panel open. A status card says what is true right now (running / hidden for a full-screen app /
stepped aside for app buttons / paused, with Resume), the live speeds and, when the latency probe has
something to say, the connection in the Overview's words. Then three navigation rows - Monitor,
Settings, Check connection - and a quiet "Quit NetSpeedTray". Every row is the widget's existing action:
the action runs first and the panel closes right after (no activation flash).

Keyboard: the first row has focus, Tab / Up / Down move, Enter / Space press, Esc closes. The Fluent
focus ring (2 px in the text colour, 1 px gap outside the row's border; Highlight in High Contrast) is
drawn only once the keyboard has been used, and its space is always reserved, so nothing shifts. High
Contrast draws in the system colours (icons re-tint to HighlightText on hover); RTL follows the app.

It is the first version of 2.3's About window (C3/C7): sections are stacked in ``_root``, so About can
add details and support rows between the status card and the rows without redoing this one.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils import styles as su
from netspeedtray.utils.taskbar_appearance import read_high_contrast

logger = logging.getLogger("NetSpeedTray.RunningPanel")

APP_NAME = "NetSpeedTray"   # a name, never translated (the window title, the heading)

# GetSysColor indexes for High Contrast (winuser.h).
_COLOR_WINDOW = 5
_COLOR_WINDOWTEXT = 8
_COLOR_HIGHLIGHT = 13
_COLOR_HIGHLIGHTTEXT = 14
_COLOR_BTNFACE = 15
_COLOR_BTNTEXT = 18
_COLOR_HOTLIGHT = 26

# Segoe Fluent Icons / MDL2 glyphs - the right-click menu's, where the menu has the same item.
_GLYPH_SETTINGS = 0xE713
_GLYPH_MONITOR = 0xE9D9
_GLYPH_CONNECTION = 0xE774       # globe: the router and, when on, the internet
_GLYPH_CHEVRON_LTR = 0xE76C      # ChevronRight
_GLYPH_CHEVRON_RTL = 0xE76B      # ChevronLeft

_STATUS_REFRESH_MS = 1000
_MIN_WIDTH = 380
_ICON_PX = 40
_ROW_HEIGHT = 40                 # the visible row (Fluent navigation card)
_RING = 3                        # reserved around every focusable control: 2 px ring + 1 px gap
_ROW_ICON = 16
_CHEVRON_PX = 12
# LRM marks frame one speed ("88.6 Mbps") so it stays whole in an RTL line; a leading RLM makes that
# line RTL. Not the bidi isolates (U+2066 / U+2069): Segoe UI has no glyph for them, and one missing
# glyph makes Qt walk its fallback chain - every installed font family (utils/styles.bidi_isolate).
_LRI = _PDI = "‎"
_RLM = "‏"
_DOWN, _UP = "↓", "↑"
_KEYBOARD_PROP = "nst_keyboard"


# --- seams (patched in tests) ----------------------------------------------------------------------

def _sys_color_hex(index: int) -> str:
    get = ctypes.windll.user32.GetSysColor
    get.restype = ctypes.c_uint32
    v = int(get(index))
    return f"#{v & 0xFF:02X}{(v >> 8) & 0xFF:02X}{(v >> 16) & 0xFF:02X}"


def _update_check_disabled() -> bool:
    try:
        from netspeedtray.core.update_checker import update_check_disabled_by_policy
        return bool(update_check_disabled_by_policy())
    except Exception:
        return False


def _force_foreground(widget: QWidget) -> None:
    """SetForegroundWindow on the window's HWND. Qt's activateWindow asks for the same, but only a
    process Windows allows may take the foreground - the second launch allowed us
    (AllowSetForegroundWindow), so this is the call that uses that right before it lapses."""
    try:
        ctypes.windll.user32.SetForegroundWindow(int(widget.winId()))
    except Exception as e:
        logger.debug("SetForegroundWindow failed: %s", e)


# --- helpers the widget uses ------------------------------------------------------------------------

def screen_for_new_window(lparam: int = 0, widget: Optional[QWidget] = None):
    """Where the panel opens: the monitor the shell launched the second process on (its STARTUPINFO
    hint, carried in the activate lParam - a Start menu or a pin on a secondary taskbar); else the
    widget's monitor (a keyboard launch has no pointer worth following); else the primary screen."""
    try:
        from netspeedtray.core import shutdown_channel as sc
        point = sc.unpack_point(lparam)
        if point is not None:
            device = sc.monitor_device_at(*point)
            for screen in QGuiApplication.screens():
                if device and screen.name() == device:
                    return screen
    except Exception as e:
        logger.debug("Launch monitor hint unusable: %s", e)
    try:
        screen = widget.screen() if widget is not None and hasattr(widget, "screen") else None
    except Exception:
        screen = None
    return screen or QGuiApplication.primaryScreen()


def bring_to_front(window: QWidget) -> None:
    """Show (restored if minimized), raise and focus ``window``."""
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()
    _force_foreground(window)


def keep_until_destroyed(owner, attr: str, panel: "RunningPanel") -> None:
    """``owner.<attr> = panel`` until the panel closes; the strong reference (utils/qt_lifetime) keeps
    Python's GC from destroying it on another thread until Qt does (it deletes itself on close). The
    attribute is cleared when the panel starts closing - a closing panel is never re-raised - and only
    while it still names THIS panel."""
    from netspeedtray.utils.qt_lifetime import hand_to_qt
    hand_to_qt(panel)
    setattr(owner, attr, panel)
    key = id(panel)

    def _gone(*_args) -> None:
        current = getattr(owner, attr, None)
        if current is not None and id(current) == key:
            setattr(owner, attr, None)

    panel.on_closing = _gone
    panel.destroyed.connect(_gone)


def _strip_mnemonic(text: str) -> str:
    try:
        from netspeedtray.views.settings.pages.general import strip_mnemonic
        return strip_mnemonic(text)
    except Exception:
        return str(text or "").replace("&", "")


def _palette() -> Dict[str, str]:
    """The panel's colours: the app's Fluent tokens (utils/styles.semantic_colors), or in High Contrast
    the system colours - an app must draw in them there; focus and hover use the Highlight pair."""
    on, hc_text, hc_bg = read_high_contrast()
    if on:
        text = hc_text or _sys_color_hex(_COLOR_WINDOWTEXT)
        bg = hc_bg or _sys_color_hex(_COLOR_WINDOW)
        hi = _sys_color_hex(_COLOR_HIGHLIGHT)
        return {"hc": "1", "bg": bg, "card": bg, "stroke": text, "text": text, "sub": text,
                "btn_bg": _sys_color_hex(_COLOR_BTNFACE), "btn_text": _sys_color_hex(_COLOR_BTNTEXT),
                "hover_bg": hi, "hover_text": _sys_color_hex(_COLOR_HIGHLIGHTTEXT), "pressed_bg": hi,
                "focus": hi, "icon": text, "link": _sys_color_hex(_COLOR_HOTLIGHT), "badge": text}
    c = su.semantic_colors()
    dark = su.is_dark_mode()
    accent = QColor(c["accent"])
    link = accent.lighter(150).name() if dark else accent.darker(115).name()
    return {"hc": "", "bg": tokens.DIALOG_CONTENT_BG_DARK if dark else tokens.DIALOG_CONTENT_BG_LIGHT,
            "card": c["card_bg"], "stroke": c["card_stroke"], "text": c["text_primary"],
            "sub": c["text_secondary"], "btn_bg": c["card_bg"], "btn_text": c["text_primary"],
            "hover_bg": c["subtle_fill"], "hover_text": c["text_primary"],
            "pressed_bg": "rgba(255, 255, 255, 0.03)" if dark else "rgba(0, 0, 0, 0.02)",
            "focus": c["text_primary"], "icon": c["text_primary"], "link": link, "badge": link}


def _style_sheet(p: Dict[str, str], rtl: bool) -> str:
    r_card, r_ctl = tokens.RADIUS_CARD, tokens.RADIUS_CONTROL
    lead = 12 + _ROW_ICON + 12          # the glyph sits in the leading padding (drawn by _Control)
    trail = 12 + _CHEVRON_PX + 10       # the chevron sits in the trailing padding
    pad_l, pad_r = (trail, lead) if rtl else (lead, trail)
    return (
        f"QDialog#RunningPanel {{ background: {p['bg']}; }}"
        f" QLabel {{ color: {p['text']}; background: transparent; }}"
        f" QLabel#Secondary {{ color: {p['sub']}; }}"
        f" QLabel#Badge {{ color: {p['badge']}; border: 1px solid {p['badge']}; border-radius: {r_ctl}px;"
        f" padding: 0px 5px; }}"
        f" QFrame#StatusCard {{ background: {p['card']}; border: 1px solid {p['stroke']};"
        f" border-radius: {r_card}px; }}"
        f" QPushButton#Row {{ text-align: left; margin: {_RING}px; padding: 0px {pad_r}px 0px {pad_l}px;"
        f" border-radius: {r_card - 2}px; background: {p['btn_bg']}; color: {p['btn_text']};"
        f" border: 1px solid {p['stroke']}; }}"
        f" QPushButton#Row:hover {{ background: {p['hover_bg']}; color: {p['hover_text']}; }}"
        f" QPushButton#Row:pressed {{ background: {p['pressed_bg']}; color: {p['sub'] if not p['hc'] else p['hover_text']}; }}"
        f" QPushButton#Quiet {{ background: transparent; color: {p['sub']}; margin: {_RING}px;"
        f" padding: 4px 8px; border: none; border-radius: {r_ctl}px; }}"
        f" QPushButton#Quiet:hover {{ background: {p['hover_bg']}; color: {p['hover_text']}; }}"
        f" QPushButton#Quiet:pressed {{ background: {p['pressed_bg']}; }}"
        f" QPushButton#Link {{ background: transparent; color: {p['link']}; margin: {_RING}px;"
        f" padding: 0px 2px; border: none; text-align: left; }}"
        f" QPushButton#Link:hover {{ text-decoration: underline; }}"
        f" QPushButton#Link:pressed {{ color: {p['sub']}; }}")


class _Control(QPushButton):
    """A focusable control of the panel: a navigation row (leading glyph + trailing chevron), a link or
    the quiet Quit. Paints what style sheets can't: the glyph (re-tinted on hover in High Contrast), the
    chevron (mirrored in RTL), and the Fluent focus ring once the keyboard has been used."""

    def __init__(self, text: str, kind: str, panel: "RunningPanel", glyph: Optional[int] = None) -> None:
        super().__init__(text, panel)
        self._panel = panel
        self._glyph = glyph
        self.setObjectName(kind)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoDefault(True)          # Enter presses the focused control
        self.setAccessibleName(text)
        self.setFont(su.font(tokens.TYPE_BODY if kind == "Row" else tokens.TYPE_CAPTION))
        if kind == "Row":
            self.setFixedHeight(_ROW_HEIGHT + 2 * _RING)
            p = panel.palette_tokens
            self._icon_normal = su.fluent_icon(glyph, _ROW_ICON, p["icon"]) if glyph else None
            self._icon_hover = (su.fluent_icon(glyph, _ROW_ICON, p["hover_text"])
                                if glyph and p["hc"] else self._icon_normal)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        p = self._panel.palette_tokens
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self.objectName() == "Row":
                inner = self.rect().adjusted(_RING, _RING, -_RING, -_RING)
                icon = self._icon_hover if self.underMouse() else self._icon_normal
                if icon is not None:
                    x = inner.right() - 12 - _ROW_ICON + 1 if rtl else inner.left() + 12
                    y = inner.top() + (inner.height() - _ROW_ICON) // 2
                    icon.paint(painter, x, y, _ROW_ICON, _ROW_ICON)
                f = QFont(su.icon_font_family())
                f.setPixelSize(_CHEVRON_PX)
                painter.setFont(f)
                hover_hc = p["hc"] and self.underMouse()
                painter.setPen(QColor(p["hover_text"] if hover_hc else p["sub"]))
                cw = _CHEVRON_PX + 2
                cx = inner.left() + 12 if rtl else inner.right() - 12 - cw + 1
                painter.drawText(QRectF(cx, inner.top(), cw, inner.height()), Qt.AlignmentFlag.AlignCenter,
                                 chr(_GLYPH_CHEVRON_RTL if rtl else _GLYPH_CHEVRON_LTR))
            if self.hasFocus() and self._panel.keyboard_mode:
                pen = QPen(QColor(p["focus"]))
                pen.setWidthF(2.0)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                radius = (tokens.RADIUS_CARD - 2 if self.objectName() == "Row" else tokens.RADIUS_CONTROL) + _RING - 1
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)
        finally:
            painter.end()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        self.update()


class RunningPanel(QDialog):
    """The panel. ``main_widget`` is the taskbar widget: its live state is read for the status card and
    its own actions are the rows (open_monitor_window, show_settings, open_connection_check), the
    update link (check_for_updates(reply_to=...), _open_update_dialog_from_offer), Resume (resume) and
    Quit (fully_exit_application)."""

    def __init__(self, main_widget, i18n, version: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._mw = main_widget
        self.i18n = i18n
        self._version = str(version)
        self._shown_once = False
        self._fixed_width = 0
        self._offer = None
        self._update_state = "idle"
        self.closing = False   # set the moment a row starts closing it (support_card.blocked_reason)
        self.on_closing: Optional[Callable[[], None]] = None
        self.setObjectName("RunningPanel")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(APP_NAME)
        self.setAccessibleName(APP_NAME)
        self.setAccessibleDescription(self._tr("RUNNING_PANEL_RUNNING", "Running on your taskbar"))
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setLayoutDirection(QApplication.layoutDirection())
        self.setProperty(_KEYBOARD_PROP, False)
        self.palette_tokens = _palette()
        self.setStyleSheet(_style_sheet(self.palette_tokens, self._rtl()))

        # Spacing on the 4/8/12/16/24 ramp. Controls carry _RING px of reserved focus-ring space on
        # every side, which the margins and spacings below subtract so the visible gaps stay on it.
        root = QVBoxLayout(self)
        root.setContentsMargins(24 - _RING, 24, 24 - _RING, 16 - _RING)
        root.setSpacing(0)
        self._root = root

        root.addLayout(self._build_header())
        root.addSpacing(16)
        card_row = QHBoxLayout()
        card_row.setContentsMargins(_RING, 0, _RING, 0)
        card_row.addWidget(self._build_status_card())
        root.addLayout(card_row)
        root.addSpacing(16 - _RING)
        root.addLayout(self._build_rows())
        root.addSpacing(16 - 2 * _RING)
        root.addLayout(self._build_footer())

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(_STATUS_REFRESH_MS)
        self._status_timer.timeout.connect(self.refresh_status)
        self.refresh_status()

    # --- building ---------------------------------------------------------------------------------

    def _tr(self, key: str, fallback: str) -> str:
        value = getattr(self.i18n, key, None)
        return value if isinstance(value, str) and value else fallback

    def _rtl(self) -> bool:
        return self.layoutDirection() == Qt.LayoutDirection.RightToLeft

    @property
    def keyboard_mode(self) -> bool:
        return bool(self.property(_KEYBOARD_PROP))

    def _label(self, text: str, token: tuple, secondary: bool = False, wrap: bool = False) -> QLabel:
        label = QLabel(self)
        label.setFont(su.font(token))
        su.set_ui_text(label, text)
        if secondary:
            label.setObjectName("Secondary")
        label.setWordWrap(wrap)
        if self._rtl():
            # A label of Latin text ("NetSpeedTray", the speeds) takes its alignment from its own text
            # and would sit at the left of a right-to-left window: pin every label to the start side.
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute
                               | Qt.AlignmentFlag.AlignVCenter)
        return label

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(_RING, 0, _RING, 0)
        row.setSpacing(tokens.SPACE_M)
        icon = QApplication.windowIcon()
        if icon is not None and not icon.isNull():
            pic = QLabel(self)
            pic.setPixmap(icon.pixmap(QSize(_ICON_PX, _ICON_PX)))
            pic.setAccessibleName(APP_NAME)
            row.addWidget(pic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title_label = self._label(APP_NAME, tokens.TYPE_TITLE)
        col.addWidget(self.title_label)

        line = QHBoxLayout()
        line.setSpacing(tokens.SPACE_XS + 2)
        ver = self._tr("RUNNING_PANEL_VERSION", "Version {version}")
        try:
            ver = ver.format(version=self._version)
        except Exception:
            ver = f"{ver} {self._version}"
        self.version_label = self._label(ver, tokens.TYPE_CAPTION, secondary=True)
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        line.addWidget(self.version_label)
        self.beta_badge = self._label(self._tr("RUNNING_PANEL_BETA", "Beta"), tokens.TYPE_CAPTION)
        self.beta_badge.setObjectName("Badge")
        self.beta_badge.setVisible("-" in self._version)   # a prerelease: 2.2.2-beta.1, -rc.2
        line.addWidget(self.beta_badge)
        self.update_sep = self._label("·", tokens.TYPE_CAPTION, secondary=True)
        line.addWidget(self.update_sep)
        self.update_link = _Control(self._tr("RUNNING_PANEL_CHECK_UPDATES", "Check for updates"),
                                    "Link", self)
        self.update_link.clicked.connect(self._check_for_updates)
        line.addWidget(self.update_link)
        self.update_state_label = self._label("", tokens.TYPE_CAPTION, secondary=True)
        line.addWidget(self.update_state_label)
        self.update_now_link = _Control(self._tr("RUNNING_PANEL_UPDATE_NOW", "Update"), "Link", self)
        self.update_now_link.clicked.connect(self._open_update_offer)
        line.addWidget(self.update_now_link)
        line.addStretch(1)
        col.addLayout(line)
        row.addLayout(col, 1)
        # IT's DisableUpdateCheck policy hides the check, as it hides the menu item (U1).
        self._updates_allowed = not _update_check_disabled()
        self._set_update_state("idle")
        return row

    def _build_status_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("StatusCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(tokens.SPACE_XS)
        self.running_label = self._label(self._tr("RUNNING_PANEL_RUNNING", "Running on your taskbar"),
                                         tokens.TYPE_BODY_STRONG, wrap=True)
        self.speeds_label = self._label("", tokens.TYPE_BODY)
        self.speeds_label.setMinimumWidth(self._speeds_reference_width())
        self.connection_label = self._label("", tokens.TYPE_BODY, wrap=True)
        self.paused_label = self._label(self._tr("RUNNING_PANEL_PAUSED_LINE", "Speeds are paused."),
                                        tokens.TYPE_BODY, wrap=True)
        resume = _strip_mnemonic(self._tr("RESUME_MENU_ITEM", "Resume"))
        self.resume_link = _Control(resume, "Link", self)
        self.resume_link.clicked.connect(self._resume)
        paused_row = QHBoxLayout()
        paused_row.setContentsMargins(0, 0, 0, 0)
        paused_row.addWidget(self.paused_label, 1)
        paused_row.addWidget(self.resume_link, 0)
        self.hint_label = self._label(self._tr(
            "RUNNING_PANEL_HINT",
            "Point to the speeds on the taskbar for details. Right-click them for more options."),
            tokens.TYPE_CAPTION, secondary=True, wrap=True)
        lay.addWidget(self.running_label)
        lay.addWidget(self.speeds_label)
        lay.addWidget(self.connection_label)
        lay.addLayout(paused_row)
        lay.addSpacing(tokens.SPACE_XS)
        lay.addWidget(self.hint_label)
        self.status_card = card
        return card

    def _speeds_reference_width(self) -> int:
        """The widest the speeds line can get with the widget's unit settings, from stable reference
        values (CLAUDE.md: size from "888.8"-style strings, never from live values)."""
        # Measured the way set_ui_text draws it: the arrows the UI face lacks (Nirmala UI has no ↓ ↑)
        # in the known face that has them - a plain QFontMetrics would make Qt search every family.
        body = su.font(tokens.TYPE_BODY)
        widest = 0
        for exp in (3, 6, 9):
            mbps = 888.8 * (10 ** exp) / 1_000_000
            text = self._speeds_text(mbps, mbps, rtl=False)
            widest = max(widest, su.ui_text_advance(body, text))
        return widest + 4

    def _build_rows(self) -> QVBoxLayout:
        mw = self._mw
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4 - 2 * _RING)   # 4 px between the visible rows
        self.monitor_button = _Control(self._tr("MONITOR_WINDOW_TITLE", "Monitor"), "Row", self,
                                       _GLYPH_MONITOR)
        self.monitor_button.clicked.connect(lambda: self._run(lambda: mw.open_monitor_window(), answers_tip=True))
        self.settings_button = _Control(self._tr("SETTINGS_WINDOW_TITLE", "Settings"), "Row", self,
                                        _GLYPH_SETTINGS)
        self.settings_button.clicked.connect(lambda: self._run(lambda: mw.show_settings(), answers_tip=True))
        self.check_connection_button = _Control(self._tr("CHECK_CONN_ACTION", "Check connection"), "Row",
                                                self, _GLYPH_CONNECTION)
        self.check_connection_button.clicked.connect(lambda: self._run(lambda: mw.open_connection_check()))
        self.rows: List[_Control] = [self.monitor_button, self.settings_button, self.check_connection_button]
        for r in self.rows:
            col.addWidget(r)
            r.installEventFilter(self)
        return col

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        mw = self._mw
        self.quit_button = _Control(self._tr("RUNNING_PANEL_QUIT", "Quit NetSpeedTray"), "Quiet", self)
        self.quit_button.clicked.connect(lambda: self._run(lambda: mw.fully_exit_application()))
        try:
            startup = getattr(mw, "is_startup_enabled", None)
            if callable(startup) and startup():
                self.quit_button.setToolTip(self._tr(
                    "RUNNING_PANEL_QUIT_TIP", "Stops NetSpeedTray. It starts again when you sign in."))
        except Exception:
            pass
        row.addWidget(self.quit_button, 0)
        row.addStretch(1)
        order = [self.rows[0], self.rows[1], self.rows[2], self.quit_button,
                 self.update_link, self.update_now_link, self.resume_link]
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)
        return row

    # --- keyboard ---------------------------------------------------------------------------------

    def _enter_keyboard_mode(self) -> None:
        if not self.keyboard_mode:
            self.setProperty(_KEYBOARD_PROP, True)
            for w in self.findChildren(_Control):
                w.update()

    def focusNextPrevChild(self, forward: bool) -> bool:  # type: ignore[override]
        self._enter_keyboard_mode()
        return super().focusNextPrevChild(forward)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.KeyPress and obj in self.rows:
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self._enter_keyboard_mode()
                visible = [r for r in self.rows if r.isVisible()]
                i = visible.index(obj) if obj in visible else 0
                j = i - 1 if key == Qt.Key.Key_Up else i + 1
                if 0 <= j < len(visible):
                    visible[j].setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Up, Qt.Key.Key_Down,
                           Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._enter_keyboard_mode()
        super().keyPressEvent(event)

    # --- behaviour --------------------------------------------------------------------------------

    def _quitting(self) -> bool:
        check = getattr(self._mw, "_app_is_quitting", None)
        try:
            return bool(check()) if callable(check) else False
        except Exception:
            return False

    def _run(self, action: Callable[[], None], answers_tip: bool = False) -> None:
        """A navigation row: the widget's action first, then the panel closes - both on the next loop
        turns, in that order, so the window the action opens is up before the panel goes (no
        activation flash, nothing landing behind) and a modal one (Check connection) doesn't hold the
        panel open behind it. Nothing at all once the app is quitting. The panel counts as closing
        from now on (``closing``). ``answers_tip``: the Monitor and Settings rows answer the first-run
        tip (combined review C8) - it never comes back over the window they open."""
        if self._quitting():
            return
        self.closing = True
        if answers_tip:
            notices = getattr(self._mw, "_startup_notices", None)
            answer = getattr(notices, "answer_tip", None)
            if callable(answer):
                try:
                    answer("the running panel's Monitor / Settings row")
                except Exception as e:
                    logger.debug("Could not answer the first-run tip: %s", e)

        def _go() -> None:
            try:
                action()
            except Exception as e:
                logger.error("A 'running' panel action failed: %s", e, exc_info=True)

        QTimer.singleShot(0, _go)
        QTimer.singleShot(0, self.close)

    def _resume(self) -> None:
        try:
            self._mw.resume()
        except Exception as e:
            logger.error("Resume from the 'running' panel failed: %s", e, exc_info=True)
        self.refresh_status()

    def _check_for_updates(self) -> None:
        """The inline check: the panel stays open and shows the result in the header."""
        if self._quitting():
            return
        self._set_update_state("checking")
        try:
            self._mw.check_for_updates(reply_to=self._on_update_reply)
        except Exception as e:
            logger.error("Update check from the 'running' panel failed: %s", e, exc_info=True)
            self._set_update_state("failed")

    def _on_update_reply(self, kind: str, *args) -> bool:
        """The widget's manual-check result, inline. False (the widget then shows its usual reply)
        when the panel is no longer on screen."""
        from PyQt6 import sip
        if sip.isdeleted(self) or not self.isVisible():
            return False
        if kind == "available" and len(args) >= 2:
            self._offer = args[1]
            self._set_update_state("available", str(args[0]))
        elif kind in ("up_to_date", "failed"):
            self._set_update_state(kind)
        else:
            self._set_update_state("idle")
        return True

    def _open_update_offer(self) -> None:
        offer = self._offer
        if offer is None:
            return
        mw = self._mw
        self._run(lambda: mw._open_update_dialog_from_offer(offer))

    def _set_update_state(self, state: str, version: str = "") -> None:
        self._update_state = state
        allowed = self._updates_allowed
        texts = {
            "checking": self._tr("RUNNING_PANEL_CHECKING", "Checking…"),
            "up_to_date": self._tr("RUNNING_PANEL_UP_TO_DATE", "You're up to date"),
            "failed": self._tr("RUNNING_PANEL_CHECK_FAILED", "Couldn't check for updates"),
        }
        if state == "available":
            text = self._tr("RUNNING_PANEL_UPDATE_AVAILABLE", "{version} is available")
            try:
                text = text.format(version=version)
            except Exception:
                text = f"{version} {text}"
        else:
            text = texts.get(state, "")
        su.set_ui_text(self.update_state_label, text)
        self.update_state_label.setVisible(allowed and bool(text))
        self.update_link.setVisible(allowed and state in ("idle", "failed"))
        self.update_now_link.setVisible(allowed and state == "available")
        self.update_sep.setVisible(allowed)
        self._refit()

    # --- status -----------------------------------------------------------------------------------

    def _widget_state(self) -> str:
        """What is true about the widget right now: paused, hidden for a full-screen app, stepped aside
        for the taskbar's app buttons (#24), otherwise hidden, or running."""
        mw = self._mw
        if bool(getattr(mw, "is_paused", False)):
            return "paused"
        visible = getattr(mw, "isVisible", None)
        try:
            shown = bool(visible()) if callable(visible) else True
        except Exception:
            shown = True
        if shown:
            return "running"
        try:
            handler = getattr(mw, "system_event_handler", None)
            if handler is not None and handler.is_fullscreen_active():
                return "fullscreen"
        except Exception:
            pass
        try:
            pm = getattr(mw, "position_manager", None)
            if pm is not None and pm.is_yielding_to_apps() is True:
                return "yielded"
        except Exception:
            pass
        return "hidden"

    def _title_for(self, state: str) -> str:
        return {
            "paused": _strip_mnemonic(self._tr("MONITOR_PAUSED", "Paused")),
            "fullscreen": self._tr("RUNNING_PANEL_HIDDEN_FULLSCREEN",
                                   "Running - hidden while a full-screen app is open"),
            "yielded": self._tr("RUNNING_PANEL_HIDDEN_YIELD",
                                "Running - stepped aside for your taskbar's app buttons"),
            "hidden": self._tr("RUNNING_PANEL_HIDDEN", "Running - the widget is hidden right now"),
        }.get(state, self._tr("RUNNING_PANEL_RUNNING", "Running on your taskbar"))

    def _speeds_text(self, down_mbps, up_mbps, rtl: bool) -> str:
        from netspeedtray.utils.helpers import format_speed
        cfg = getattr(self._mw, "config", None) or {}
        d = constants.config.defaults
        units = constants.network.units

        def fmt(mbps) -> str:
            try:
                bps = float(mbps or 0.0) * units.MEGA_DIVISOR / units.BITS_PER_BYTE
            except (TypeError, ValueError):
                bps = 0.0
            return format_speed(
                bps, self.i18n,
                force_mega_unit=(cfg.get("speed_display_mode", d.DEFAULT_SPEED_DISPLAY_MODE) == "always_mbps"),
                decimal_places=int(cfg.get("decimal_places", d.DEFAULT_DECIMAL_PLACES)),
                unit_type=str(cfg.get("unit_type", d.DEFAULT_UNIT_TYPE)),
                short_labels=bool(cfg.get("short_unit_labels", d.DEFAULT_SHORT_UNIT_LABELS)))

        down, up = f"{_DOWN} {fmt(down_mbps)}", f"{_UP} {fmt(up_mbps)}"
        if rtl:
            return f"{_RLM}{_LRI}{down}{_PDI}   {_LRI}{up}{_PDI}"
        return f"{down}   {up}"

    def _connection_text(self) -> str:
        """The connection in the Overview's words - "Router: Good · 3 ms" - or "" when the latency
        probe has nothing to say (off, or no sample yet)."""
        from netspeedtray.core.latency_probe import HOP_ANCHOR, HOP_GATEWAY, headline_hop, latency_verdict
        mw = self._mw
        hop = getattr(mw, "latency_headline_hop", None)
        known = hop in (HOP_ANCHOR, HOP_GATEWAY)
        if not known:
            cfg = getattr(mw, "config", None) or {}
            hop = headline_hop(bool(cfg.get("latency_public_enabled", False)))
        label = (self._tr("LATENCY_LABEL", "Internet") if hop == HOP_ANCHOR
                 else self._tr("CHECK_CONN_ROUTER", "Router"))
        rtl = self._rtl()
        lead = _RLM if rtl else ""
        if getattr(mw, "latency_link_lost", False) is True:
            return f"{lead}{label}: {self._tr('LATENCY_NO_CONNECTION', 'No connection')}"
        if not known:
            return ""
        ms = getattr(mw, "latency_headline_ms", None)
        loss = getattr(mw, "latency_loss", None)
        if ms is None and not loss:
            return ""
        verdict = latency_verdict(ms, loss)
        keys = {"good": ("LATENCY_GOOD", "Good"), "ok": ("LATENCY_OK", "OK"), "slow": ("LATENCY_SLOW", "Slow")}
        key, fallback = keys.get(verdict, keys["ok"])
        out = f"{lead}{label}: {self._tr(key, fallback)}"
        if ms is not None:
            ms_text = f"{float(ms):.0f} ms"
            out += f" · {_LRI}{ms_text}{_PDI}" if rtl else f" · {ms_text}"
        return out

    def status_text(self) -> str:
        """The card's live lines as one string (tests, Narrator's description)."""
        parts = [self.running_label.text()]
        for lbl in (self.speeds_label, self.connection_label, self.paused_label):
            if not lbl.isHidden() and lbl.text():
                parts.append(lbl.text())
        return "\n".join(p.replace(_LRI, "").replace(_PDI, "").replace(_RLM, "") for p in parts)

    def refresh_status(self) -> None:
        try:
            state = self._widget_state()
            mw = self._mw
            paused = state == "paused"
            self._set_label(self.running_label, self._title_for(state))
            speeds = "" if paused else self._speeds_text(getattr(mw, "download_speed", 0.0),
                                                         getattr(mw, "upload_speed", 0.0), self._rtl())
            conn = "" if paused else self._connection_text()
            self._set_label(self.speeds_label, speeds)
            self._set_label(self.connection_label, conn)
            changed = (self.speeds_label.isHidden() != paused or self.connection_label.isHidden() != (not conn)
                       or self.paused_label.isHidden() != (not paused))
            self.speeds_label.setHidden(paused)
            self.connection_label.setHidden(not conn)
            self.paused_label.setHidden(not paused)
            self.resume_link.setHidden(not (paused and callable(getattr(mw, "resume", None))))
            self.setAccessibleDescription(self.status_text())
            if changed:
                self._refit()
        except Exception as e:
            logger.debug("Status refresh failed: %s", e)

    def _set_label(self, label: QLabel, text: str) -> None:
        if text != label.text():
            su.set_ui_text(label, text)
            label.setAccessibleName(text.replace(_LRI, "").replace(_PDI, "").replace(_RLM, ""))

    # --- size and placement -----------------------------------------------------------------------

    def _refit(self) -> None:
        """Fixed size: the width chosen at the first layout, the height the content needs at it."""
        if not self._fixed_width:
            return
        lay = self.layout()
        h = lay.totalHeightForWidth(self._fixed_width) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.setFixedSize(self._fixed_width, max(h, lay.totalMinimumSize().height()))

    def _fix_size(self) -> None:
        if self._fixed_width:
            return
        self._fixed_width = max(_MIN_WIDTH, self.layout().totalMinimumSize().width(),
                                min(self.sizeHint().width(), _MIN_WIDTH + 120))
        self._refit()

    def place_on_screen(self, screen) -> None:
        """Centre on ``screen``'s work area (DPI is Qt's: these are logical pixels)."""
        self._fix_size()
        if screen is None:
            return
        area = screen.availableGeometry()
        size = self.size()
        x = area.x() + max(0, (area.width() - size.width()) // 2)
        y = area.y() + max(0, (area.height() - size.height()) // 2)
        self.move(x, y)

    # --- window events ----------------------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        self._fix_size()
        super().showEvent(event)
        try:
            from netspeedtray.utils.dwm import apply_win11_chrome
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode() and not self.palette_tokens["hc"])
        except Exception:
            pass
        self.refresh_status()
        self._status_timer.start()
        if not self._shown_once:
            self._shown_once = True
            self.rows[0].setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._status_timer.stop()
        super().hideEvent(event)

    def _release(self) -> None:
        """Closing (the X, Alt+F4, Esc, a row, quit): stop the clock and let the owner forget this panel
        at once, so a launch meanwhile builds a new one instead of re-raising a closing one."""
        self._status_timer.stop()
        self.closing = True
        hook, self.on_closing = self.on_closing, None
        if hook is not None:
            try:
                hook()
            except Exception:
                pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._release()
        super().closeEvent(event)

    def done(self, result: int) -> None:  # type: ignore[override]
        self._release()   # Esc (reject) ends here without a closeEvent
        super().done(result)
