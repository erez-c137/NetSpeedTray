"""
The "NetSpeedTray is running" panel (2.2.2 D3) - what launching the app from Start shows while it runs.

NetSpeedTray has no tray icon, so before 2.2.2 a launch from Start while it ran only brought the widget
back - the most common way people "open" an app showed nothing they could use. Now a second launch that
is the USER asking (shutdown_channel.activation_kind: not a duplicate autostart at sign-in, not the
updater's relaunch) opens this small window after the widget is back (widget.handle_activate_request).

What it is: a normal, focusable window (a taskbar button, Alt+Tab, Narrator) - never a tool window and
never always-on-top. App name + version, one live status line (the widget's own speeds and, when the
latency probe has data, the connection in the Overview's words), and the app's doors - Settings,
Monitor, Check connection, Check for updates - plus a quiet "Quit NetSpeedTray". Every door is the
widget's existing action, called after the panel closes; nothing here duplicates their logic.
Keyboard: the first door has focus, Tab walks the doors top to bottom, Enter/Space press the focused
one, Esc closes. High Contrast draws in the system colours; RTL follows the application.

It is the first version of 2.3's About window (C3/C7: details, support rows, the keyboard route to every
menu action) - sections are stacked in ``_root`` so that window can add its own between the status
card and the doors without redoing this one.
"""
from __future__ import annotations

import ctypes
import logging
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QCursor, QGuiApplication, QIcon
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

# Segoe Fluent Icons glyphs - the right-click menu's where the menu has the same item.
_GLYPH_SETTINGS = 0xE713
_GLYPH_MONITOR = 0xE9D9
_GLYPH_CONNECTION = 0xE774   # globe: the router and, when on, the internet
_GLYPH_UPDATES = 0xE895

_STATUS_REFRESH_MS = 1000
_MIN_WIDTH = 380
_ICON_PX = 40
# LRI/PDI keep one speed ("88.6 Mbps") whole in an RTL line; a leading RLM makes that line RTL.
_LRI, _PDI, _RLM = "\u2066", "\u2069", "\u200f"


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
    """SetForegroundWindow on the panel's HWND. Qt's activateWindow asks for the same, but only a
    process Windows allows may take the foreground - the second launch allowed us
    (AllowSetForegroundWindow), so this is the call that uses that right before it lapses."""
    try:
        ctypes.windll.user32.SetForegroundWindow(int(widget.winId()))
    except Exception as e:
        logger.debug("SetForegroundWindow failed: %s", e)


# --- helpers the widget uses ------------------------------------------------------------------------

def screen_for_new_window():
    """The screen under the pointer - where the user just clicked Start or a pin, also on a
    secondary-monitor taskbar - else the primary screen."""
    try:
        screen = QGuiApplication.screenAt(QCursor.pos())
    except Exception:
        screen = None
    return screen or QGuiApplication.primaryScreen()


def bring_to_front(panel: QWidget) -> None:
    """Show (restored if minimized), raise and focus ``panel``."""
    if panel.isMinimized():
        panel.showNormal()
    panel.show()
    panel.raise_()
    panel.activateWindow()
    _force_foreground(panel)


def keep_until_destroyed(owner, attr: str, panel: QWidget) -> None:
    """``owner.<attr> = panel`` until Qt destroys the panel (it deletes itself on close); the strong
    reference keeps Python's GC from destroying it on another thread (utils/qt_lifetime). The hook
    clears ``attr`` only while it still names THIS panel."""
    from netspeedtray.utils.qt_lifetime import hand_to_qt
    hand_to_qt(panel)
    setattr(owner, attr, panel)
    key = id(panel)

    def _gone(*_args) -> None:
        current = getattr(owner, attr, None)
        if current is not None and id(current) == key:
            setattr(owner, attr, None)

    panel.destroyed.connect(_gone)


def _palette() -> Dict[str, str]:
    """The panel's colours: the app's Fluent tokens (utils/styles.semantic_colors), or in High Contrast
    the system colours - an app must draw in them there, and focus/hover use the Highlight pair."""
    on, hc_text, hc_bg = read_high_contrast()
    if on:
        text = hc_text or _sys_color_hex(_COLOR_WINDOWTEXT)
        bg = hc_bg or _sys_color_hex(_COLOR_WINDOW)
        hi = _sys_color_hex(_COLOR_HIGHLIGHT)
        return {"hc": "1", "bg": bg, "card": bg, "stroke": text, "text": text, "sub": text,
                "btn_bg": _sys_color_hex(_COLOR_BTNFACE), "btn_text": _sys_color_hex(_COLOR_BTNTEXT),
                "hover_bg": hi, "hover_text": _sys_color_hex(_COLOR_HIGHLIGHTTEXT), "focus": hi,
                "icon": text}
    c = su.semantic_colors()
    dark = su.is_dark_mode()
    st = tokens
    return {"hc": "", "bg": st.DIALOG_CONTENT_BG_DARK if dark else st.DIALOG_CONTENT_BG_LIGHT,
            "card": c["card_bg"], "stroke": c["card_stroke"], "text": c["text_primary"],
            "sub": c["text_secondary"], "btn_bg": c["card_bg"], "btn_text": c["text_primary"],
            "hover_bg": c["subtle_fill"], "hover_text": c["text_primary"], "focus": c["accent"],
            "icon": c["text_primary"]}


def _style_sheet(p: Dict[str, str]) -> str:
    r_card, r_ctl = tokens.RADIUS_CARD, tokens.RADIUS_CONTROL
    return (
        f"QDialog#RunningPanel {{ background: {p['bg']}; }}"
        f" QLabel {{ color: {p['text']}; background: transparent; }}"
        f" QLabel#Secondary {{ color: {p['sub']}; }}"
        f" QFrame#StatusCard {{ background: {p['card']}; border: 1px solid {p['stroke']};"
        f" border-radius: {r_card}px; }}"
        f" QPushButton#Door {{ text-align: left; padding: 9px 12px; border-radius: {r_card - 2}px;"
        f" background: {p['btn_bg']}; color: {p['btn_text']}; border: 1px solid {p['stroke']}; }}"
        f" QPushButton#Door:hover {{ background: {p['hover_bg']}; color: {p['hover_text']}; }}"
        f" QPushButton#Door:focus {{ border: 2px solid {p['focus']}; padding: 8px 11px; }}"
        f" QPushButton#Quiet {{ background: transparent; color: {p['sub']}; padding: 6px 10px;"
        f" border: 1px solid transparent; border-radius: {r_ctl}px; }}"
        f" QPushButton#Quiet:hover {{ background: {p['hover_bg']}; color: {p['hover_text']}; }}"
        f" QPushButton#Quiet:focus {{ border: 2px solid {p['focus']}; padding: 5px 9px; }}")


class RunningPanel(QDialog):
    """The panel. ``main_widget`` is the taskbar widget: its live state is read for the status line and
    its own actions are the doors (show_settings, open_monitor_window, open_connection_check,
    check_for_updates, fully_exit_application)."""

    def __init__(self, main_widget, i18n, version: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._mw = main_widget
        self.i18n = i18n
        self._shown_once = False
        self.setObjectName("RunningPanel")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(APP_NAME)
        self.setAccessibleName(APP_NAME)
        self.setAccessibleDescription(self._tr("RUNNING_PANEL_RUNNING", "Running on your taskbar"))
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setLayoutDirection(QApplication.layoutDirection())
        self.setMinimumWidth(_MIN_WIDTH)
        self._p = _palette()
        self.setStyleSheet(_style_sheet(self._p))

        root = QVBoxLayout(self)
        root.setContentsMargins(tokens.SPACE_L + 4, tokens.SPACE_L, tokens.SPACE_L + 4, tokens.SPACE_M + 4)
        root.setSpacing(tokens.SPACE_M + 2)
        self._root = root

        root.addLayout(self._build_header(version))
        root.addWidget(self._build_status_card())
        root.addLayout(self._build_doors())
        root.addLayout(self._build_footer())

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(_STATUS_REFRESH_MS)
        self._status_timer.timeout.connect(self.refresh_status)
        self.refresh_status()

    # --- building ---------------------------------------------------------------------------------

    def _tr(self, key: str, fallback: str) -> str:
        value = getattr(self.i18n, key, None)
        return value if isinstance(value, str) and value else fallback

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

    def _rtl(self) -> bool:
        return self.layoutDirection() == Qt.LayoutDirection.RightToLeft

    def _build_header(self, version: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(tokens.SPACE_M)
        icon = QApplication.windowIcon()
        if icon is not None and not icon.isNull():
            pic = QLabel(self)
            pic.setPixmap(icon.pixmap(QSize(_ICON_PX, _ICON_PX)))
            pic.setAccessibleName(APP_NAME)
            row.addWidget(pic, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title_label = self._label(APP_NAME, tokens.TYPE_TITLE)
        ver = self._tr("RUNNING_PANEL_VERSION", "Version {version}")
        try:
            ver = ver.format(version=version)
        except Exception:
            ver = f"{ver} {version}"
        self.version_label = self._label(ver, tokens.TYPE_CAPTION, secondary=True)
        col.addWidget(self.title_label)
        col.addWidget(self.version_label)
        row.addLayout(col, 1)
        return row

    def _build_status_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("StatusCard")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(tokens.SPACE_M + 2, tokens.SPACE_M, tokens.SPACE_M + 2, tokens.SPACE_M)
        lay.setSpacing(tokens.SPACE_XS)
        self.running_label = self._label(self._tr("RUNNING_PANEL_RUNNING", "Running on your taskbar"),
                                         tokens.TYPE_BODY_STRONG)
        self.status_label = self._label("", tokens.TYPE_BODY)
        self.hint_label = self._label(self._tr(
            "RUNNING_PANEL_HINT",
            "Hover the speeds on the taskbar for details, or right-click them for the menu."),
            tokens.TYPE_CAPTION, secondary=True, wrap=True)
        for w in (self.running_label, self.status_label, self.hint_label):
            lay.addWidget(w)
        return card

    def _door(self, text: str, glyph: int, action: Callable[[], None]) -> QPushButton:
        btn = QPushButton("  " + text, self)   # the gap after the glyph (the start side in RTL too)
        btn.setObjectName("Door")
        btn.setFont(su.font(tokens.TYPE_BODY))
        btn.setIcon(su.fluent_icon(glyph, 16, self._p["icon"]))
        btn.setIconSize(QSize(16, 16))
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setAutoDefault(True)          # Enter presses the focused door
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setAccessibleName(text)
        btn.clicked.connect(lambda _checked=False, a=action: self._run(a))
        return btn

    def _build_doors(self) -> QVBoxLayout:
        mw = self._mw
        col = QVBoxLayout()
        col.setSpacing(tokens.SPACE_XS + 2)
        self.settings_button = self._door(self._tr("SETTINGS_WINDOW_TITLE", "Settings"),
                                          _GLYPH_SETTINGS, lambda: mw.show_settings())
        self.monitor_button = self._door(self._tr("MONITOR_WINDOW_TITLE", "Monitor"),
                                         _GLYPH_MONITOR, lambda: mw.open_monitor_window())
        self.check_connection_button = self._door(self._tr("CHECK_CONN_ACTION", "Check connection"),
                                                  _GLYPH_CONNECTION, lambda: mw.open_connection_check())
        self.updates_button = self._door(self._tr("RUNNING_PANEL_CHECK_UPDATES", "Check for updates"),
                                         _GLYPH_UPDATES, lambda: mw.check_for_updates())
        # IT's DisableUpdateCheck policy hides it, as it hides the menu item (U1).
        self.updates_button.setVisible(not _update_check_disabled())
        doors = (self.settings_button, self.monitor_button, self.check_connection_button,
                 self.updates_button)
        for d in doors:
            col.addWidget(d)
        return col

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, tokens.SPACE_XS, 0, 0)
        text = self._tr("RUNNING_PANEL_QUIT", "Quit NetSpeedTray")
        self.quit_button = QPushButton(text, self)
        self.quit_button.setObjectName("Quiet")
        self.quit_button.setFont(su.font(tokens.TYPE_CAPTION))
        self.quit_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.quit_button.setAutoDefault(True)
        self.quit_button.setAccessibleName(text)
        mw = self._mw
        self.quit_button.clicked.connect(lambda _checked=False: self._run(lambda: mw.fully_exit_application()))
        row.addWidget(self.quit_button, 0)
        row.addStretch(1)
        order = [self.settings_button, self.monitor_button, self.check_connection_button,
                 self.updates_button, self.quit_button]
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)
        return row

    # --- behaviour --------------------------------------------------------------------------------

    def _run(self, action: Callable[[], None]) -> None:
        """Close, then run the widget's action on the next loop turn (outside this click, and after the
        panel is gone, so the window it opens takes the foreground the panel had)."""
        self.close()

        def _go() -> None:
            try:
                action()
            except Exception as e:
                logger.error("A 'running' panel action failed: %s", e, exc_info=True)

        QTimer.singleShot(0, _go)

    def status_text(self) -> str:
        """The live line: the down / up speeds with their arrows, then the connection when the latency
        probe has something to say - "Router: Good (3 ms)" - in the Overview's words. "Paused" while paused."""
        mw = self._mw
        if bool(getattr(mw, "is_paused", False)):
            return self._tr("MONITOR_PAUSED", "Paused")
        rtl = self._rtl()
        parts = [self._speeds_text(rtl)]
        conn = self._connection_text()
        if conn:
            parts.append(conn)
        line = "  ·  ".join(p for p in parts if p)
        # RTL: the line opens with Latin text, so Qt would lay it out left to right - a right-to-left
        # mark first makes it a right-to-left paragraph (the speeds stay whole in their isolates).
        return _RLM + line if rtl else line

    def _speeds_text(self, rtl: bool) -> str:
        from netspeedtray.utils.helpers import format_speed
        mw = self._mw
        cfg = getattr(mw, "config", None) or {}
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

        down = f"\u2193 {fmt(getattr(mw, 'download_speed', 0.0))}"
        up = f"\u2191 {fmt(getattr(mw, 'upload_speed', 0.0))}"
        if rtl:
            down, up = f"{_LRI}{down}{_PDI}", f"{_LRI}{up}{_PDI}"
        return f"{down}   {up}"

    def _connection_text(self) -> str:
        from netspeedtray.core.latency_probe import HOP_ANCHOR, HOP_GATEWAY, headline_hop, latency_verdict
        mw = self._mw
        hop = getattr(mw, "latency_headline_hop", None)
        if hop not in (HOP_ANCHOR, HOP_GATEWAY):
            cfg = getattr(mw, "config", None) or {}
            hop = headline_hop(bool(cfg.get("latency_public_enabled", False)))
            known = False
        else:
            known = True
        label = (self._tr("LATENCY_LABEL", "Internet") if hop == HOP_ANCHOR
                 else self._tr("CHECK_CONN_ROUTER", "Router"))
        if getattr(mw, "latency_link_lost", False) is True:
            return f"{label}: {self._tr('LATENCY_NO_CONNECTION', 'No connection')}"
        if not known:
            return ""   # no probe data yet (or the probe is off)
        ms = getattr(mw, "latency_headline_ms", None)
        loss = getattr(mw, "latency_loss", None)
        if ms is None and not loss:
            return ""
        verdict = latency_verdict(ms, loss)
        keys = {"good": ("LATENCY_GOOD", "Good"), "ok": ("LATENCY_OK", "OK"), "slow": ("LATENCY_SLOW", "Slow")}
        key, fallback = keys.get(verdict, keys["ok"])
        out = f"{label}: {self._tr(key, fallback)}"
        if ms is not None:
            ms_text = f"{float(ms):.0f} ms"
            out += f" ({_LRI}{ms_text}{_PDI})" if self._rtl() else f" ({ms_text})"
        return out

    def refresh_status(self) -> None:
        try:
            text = self.status_text()
        except Exception as e:
            logger.debug("Status line failed: %s", e)
            return
        if text != self.status_label.text():
            su.set_ui_text(self.status_label, text)
            self.status_label.setAccessibleName(text.replace(_LRI, "").replace(_PDI, "").replace(_RLM, ""))

    def place_on_screen(self, screen) -> None:
        """Centre on ``screen``'s work area (DPI is Qt's: these are logical pixels)."""
        if screen is None:
            return
        self.adjustSize()
        area = screen.availableGeometry()
        size = self.sizeHint().expandedTo(self.minimumSizeHint())
        x = area.x() + max(0, (area.width() - size.width()) // 2)
        y = area.y() + max(0, (area.height() - size.height()) // 2)
        self.move(x, y)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        try:
            from netspeedtray.utils.dwm import apply_win11_chrome
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode() and not self._p["hc"])
        except Exception:
            pass
        self.refresh_status()
        self._status_timer.start()
        if not self._shown_once:
            self._shown_once = True
            self.settings_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._status_timer.stop()
        super().hideEvent(event)
