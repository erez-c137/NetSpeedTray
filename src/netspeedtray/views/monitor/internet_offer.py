"""
"Also check your internet" - the one-click opt-in to the internet check (2.2.2 D5).

With the internet check off (the default, and it stays off), every health figure in the Monitor comes
from pings to the user's own router, and says so ("Router: steady"). Beside it, a NoticeLink offers the
internet check. On the Overview it opens a small Fluent TeachingTip-style popup anchored to the link
(``InternetOfferTip``); in the Check connection sheet an inline, compact confirm. Both hold the same
``InternetCheckConfirm``: a title, one body line saying exactly what leaves the PC and who sees it, and
[Turn on] [Not now]:

  * Turn on  -> ``latency_public_enabled`` through the main widget (saved, applied live: the running
               probe adds the internet ping on its next cycle). Reversible in Settings.
  * Not now  -> the offer is gone for the rest of the session; after ``INTERNET_OFFER_MAX_DECLINES`` of
               them it is never offered again (the last one reads "No thanks"). Settings keeps the switch.
  * Esc / a click outside the popup only closes it - that is not a decline.

Nothing is turned on without the Turn on click. The body text is set when the confirm OPENS, never at
construction: a host name in another script must not make Qt load faces for a confirm nobody opened.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from typing import Any, Dict, Optional

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils import styles as su

logger = logging.getLogger("NetSpeedTray.InternetOffer")

# The session-wide "Not now" (an attribute on the main widget, so the Overview and the sheet agree).
_SNOOZE_ATTR = "internet_offer_snoozed"
# Right-to-left UIs only: LRE ... PDF keeps "1.1.1.1 (Cloudflare)" one left-to-right run inside a Hebrew
# sentence (the closing parenthesis would flip otherwise) - the embedding units.py uses. Never the
# FSI/PDI isolates: Segoe UI Variable lacks them, and a missing glyph sends Qt through every installed
# family (852 font files, ~38 MB, in every language). Left-to-right UIs get no control characters.
_LRE, _PDF = "‪", "‬"
_IPV4_HEADER, _ICMP_HEADER = 20, 8       # bytes per echo at the IP layer, besides the payload
_SECONDS_PER_DAY = 86400


def _tr(i18n, key: str, default: str) -> str:
    from netspeedtray.utils.helpers import tr
    return tr(i18n, key, default)


def _rtl(i18n) -> bool:
    return getattr(i18n, "is_rtl", False) is True


def _canonical(host: Any) -> str:
    h = str(host or "").strip().rstrip(".").lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        return ipaddress.ip_address(h).compressed
    except ValueError:
        return h


def is_ip_address(host: Any) -> bool:
    try:
        ipaddress.ip_address(_canonical(host))
        return True
    except ValueError:
        return False


def provider_for_host(host: Any) -> Optional[str]:
    """Who runs ``host`` when it is a well-known public test server (1.1.1.1 -> "Cloudflare"), else
    None. IP addresses are compared in canonical form, names case-insensitively."""
    h = _canonical(host)
    return constants.network.latency.ANCHOR_PROVIDERS.get(h) if h else None


def configured_host(config: Dict[str, Any]) -> str:
    """The internet test server the probe would ping (latency_probe's own fallback)."""
    return str(config.get("latency_public_host", "1.1.1.1") or "1.1.1.1").strip() or "1.1.1.1"


def host_label(host: Any) -> str:
    """The test server as a list shows it: "1.1.1.1 (Cloudflare)", or the host alone."""
    h = str(host or "").strip()
    provider = provider_for_host(h)
    return f"{h} ({provider})" if provider else h


def host_from_label(text: Any) -> str:
    """``host_label``'s inverse: "1.1.1.1 (Cloudflare)" -> "1.1.1.1"; anything else as typed."""
    t = str(text or "").strip()
    m = re.fullmatch(r"(\S+) \((.+)\)", t)
    if m and provider_for_host(m.group(1)) == m.group(2):
        return m.group(1)
    return t


def offer_target(host: Any, rtl: bool = False) -> str:
    """The test server as a sentence names it - "1.1.1.1 (Cloudflare)" - embedded LTR in an RTL UI."""
    run = host_label(host)
    return f"{_LRE}{run}{_PDF}" if rtl else run


def mb_per_day(host: Any = None) -> int:
    """The internet check's traffic in a day of running, at the IP layer, both ways: each cycle sends
    BURST_SIZE echoes of the probe's fixed payload and gets as many replies (IPv4 - the probe pings
    over IPv4 only). 4 x 2 x 40 B every 3 s = ~9.2 MB; rounded to the nearest MB, never down to 0."""
    lat = constants.network.latency
    per_echo = _IPV4_HEADER + _ICMP_HEADER + len(lat.ECHO_PAYLOAD)
    per_day = lat.BURST_SIZE * 2 * per_echo * _SECONDS_PER_DAY / lat.OUTAGE_CYCLE_SEC
    return max(1, round(per_day / 1_000_000))


def confirm_body(i18n, host: Any) -> str:
    """The one plain line on what leaves the PC, to whom, how often. A host NAME is looked up every
    cycle, so that variant says the DNS server sees the name - "nothing else" stays true."""
    lat = constants.network.latency
    rtl = _rtl(i18n)
    who = provider_for_host(host) or str(host or "").strip()
    if rtl and not provider_for_host(host):
        who = f"{_LRE}{who}{_PDF}"
    values = dict(target=offer_target(host, rtl), count=lat.BURST_SIZE, seconds=f"{lat.OUTAGE_CYCLE_SEC:g}",
                  mb=mb_per_day(host), who=who)
    if is_ip_address(host):
        return _tr(i18n, "INTERNET_CONFIRM_BODY",
                   "Pings {target} {count} times every {seconds} seconds, about {mb} MB a day. {who} sees "
                   "your IP address, as any website does. Nothing else leaves your PC.").format(**values)
    return _tr(i18n, "INTERNET_CONFIRM_BODY_NAME",
               "Pings {target} {count} times every {seconds} seconds, about {mb} MB a day, and looks up its "
               "address each time. {who} sees your IP address, and your DNS server sees the name. Nothing "
               "else leaves your PC.").format(**values)


def include_once_target(host: Any) -> str:
    """The Check connection tick's "(pings 1.1.1.1, Cloudflare)" part."""
    h = str(host or "").strip()
    provider = provider_for_host(h)
    return f"{h}, {provider}" if provider else h


class InternetOffer:
    """The offer's state and its two outcomes, for one main widget (the app's config owner).

    ``available()`` - offer it now? Only while the internet check is off, the latency probe is on (the
    router line it sits beside exists), the main widget can turn it on, and the user has not declined it
    this session or ``INTERNET_OFFER_MAX_DECLINES`` times in all."""

    def __init__(self, main_widget, config: Dict[str, Any]) -> None:
        self._mw = main_widget
        self._config = config

    def _declines(self) -> int:
        try:
            return int(self._config.get("internet_offer_declines", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def available(self) -> bool:
        cfg = self._config
        if bool(cfg.get("latency_public_enabled", False)) or not bool(cfg.get("latency_enabled", True)):
            return False
        if getattr(self._mw, _SNOOZE_ATTR, False) is True:
            return False
        if self._declines() >= constants.network.latency.INTERNET_OFFER_MAX_DECLINES:
            return False
        return callable(getattr(self._mw, "turn_on_internet_check", None))

    def ends_offer(self) -> bool:
        """This "Not now" is the last one: the button says "No thanks"."""
        return self._declines() + 1 >= constants.network.latency.INTERNET_OFFER_MAX_DECLINES

    def host(self) -> str:
        return configured_host(self._config)

    def turn_on(self) -> bool:
        """The Turn on click: the main widget sets ``latency_public_enabled`` (saved + applied live).
        True when it took."""
        try:
            self._mw.turn_on_internet_check()
        except Exception as e:
            logger.error("Could not turn the internet check on: %s", e, exc_info=True)
            return False
        return bool(self._config.get("latency_public_enabled", False))

    def decline(self) -> None:
        """The Not now click: gone for this session, and one step closer to never offered again. Saved
        through the main widget's ``save_config_keys`` - which keeps an open Settings dialog in step, so
        neither its Cancel nor its Save brings the offer back, and never writes Settings' unsaved edits."""
        try:
            setattr(self._mw, _SNOOZE_ATTR, True)
        except Exception:
            pass
        updates = {"internet_offer_declines": self._declines() + 1}
        try:
            save = getattr(self._mw, "save_config_keys", None)
            if callable(save):
                save(updates)
            else:
                self._mw.config_controller.update_config(updates, apply_and_repaint=False)
        except Exception as e:
            logger.debug("Could not save the internet offer's Not now: %s", e)
        self._config.update(updates)   # at least this session remembers


def offer_for(main_widget, config: Dict[str, Any]) -> Optional[InternetOffer]:
    """An InternetOffer when ``main_widget`` can turn the internet check on, else None."""
    if main_widget is None or not callable(getattr(main_widget, "turn_on_internet_check", None)):
        return None
    return InternetOffer(main_widget, config)


def confirm_colors() -> dict:
    """{"bg", "stroke", "text", "sub", "fill", "accent", "accent_text", "focus", "hc"}. High Contrast:
    the theme's own colours (window, window text, highlight), never the app's tokens. The focus ring
    is the text colour, Highlight in High Contrast (combined review U3)."""
    from netspeedtray.utils.taskbar_appearance import read_high_contrast_link_colors
    hc = read_high_contrast_link_colors()
    if hc:
        return {"bg": hc["background"], "stroke": hc["text"], "text": hc["text"], "sub": hc["text"],
                "fill": hc["background"], "accent": hc["focus"], "accent_text": hc["background"],
                "focus": hc["focus"], "hc": True}
    c = su.semantic_colors()
    return {"bg": c["card_bg"], "stroke": c["card_stroke"], "text": c["text_primary"],
            "sub": c["text_secondary"], "fill": c["subtle_fill"], "accent": None, "accent_text": None,
            "focus": c["text_primary"], "hc": False}


class _ConfirmButton(QPushButton):
    """A confirm action: Tab-reachable (no focus ring left behind by a mouse click), Enter and Space
    fire it, and it is never a dialog's default button (Enter on it never presses "Check again")."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(tokens.INTERNET_CONFIRM_BUTTON_HEIGHT)   # Fluent: 32 px, every theme
        self.setAccessibleName(text)

    def keyPressEvent(self, event) -> None:
        from netspeedtray.utils.components import _enter_activates
        if not _enter_activates(self, event):
            super().keyPressEvent(event)


class InternetCheckConfirm(QFrame):
    """The confirm: a title, one body line on what leaves the PC, then [Turn on] [Not now] at the end
    of the row. ``framed``: its own card (the sheet's inline confirm); unframed inside the popup, which
    paints the card. ``show_done`` turns it into a short "is on" line (the sheet). Emits
    ``turn_on_clicked`` / ``not_now_clicked``; the host (with an InternetOffer) does the rest."""

    turn_on_clicked = pyqtSignal()
    not_now_clicked = pyqtSignal()

    _THEME_EVENTS = frozenset((QEvent.Type.PaletteChange.value, QEvent.Type.StyleChange.value,
                               QEvent.Type.ApplicationPaletteChange.value, 210))   # 210: ThemeChange

    def __init__(self, i18n, *, framed: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("internetConfirm")
        self._i18n = i18n
        self._framed = framed
        self._theming = False
        self._colors: dict = {}
        self._done = False
        title = _tr(i18n, "INTERNET_CONFIRM_TITLE", "Also check your internet?")
        self.setAccessibleName(title)

        lay = QVBoxLayout(self)
        if framed:   # the sheet's compact inline card
            lay.setContentsMargins(tokens.SPACE_M, tokens.SPACE_S, tokens.SPACE_M, tokens.SPACE_S)
        else:        # the popup's own padding is its card's
            lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.SPACE_XS)
        self._title = QLabel()
        self._title.setObjectName("internetConfirmTitle")
        self._title.setWordWrap(True)
        self._title.setFont(su.font(tokens.TYPE_BODY_STRONG))
        su.set_ui_text(self._title, title)
        lay.addWidget(self._title)
        self._text = QLabel()
        self._text.setObjectName("internetConfirmText")
        self._text.setWordWrap(True)
        self._text.setFont(su.font(tokens.TYPE_CAPTION if framed else tokens.TYPE_BODY))
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lay.addWidget(self._text)
        lay.addSpacing(tokens.SPACE_S)

        row = QHBoxLayout()   # mirrored automatically in a right-to-left UI
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(tokens.SPACE_S)
        row.addStretch(1)     # buttons at the row's end (Fluent dialog / TeachingTip order)
        self._on_btn = _ConfirmButton(_tr(i18n, "INTERNET_CONFIRM_TURN_ON", "Turn on"))
        self._on_btn.setObjectName("internetConfirmOn")
        self._off_btn = _ConfirmButton(_tr(i18n, "INTERNET_CONFIRM_NOT_NOW", "Not now"))
        self._off_btn.setObjectName("internetConfirmNotNow")
        for b in (self._on_btn, self._off_btn):
            b.setFont(su.font(tokens.TYPE_BODY))
            su.apply_text_face(b, b.text())
        self._on_btn.clicked.connect(self.turn_on_clicked.emit)
        self._off_btn.clicked.connect(self.not_now_clicked.emit)
        row.addWidget(self._on_btn)
        row.addWidget(self._off_btn)
        lay.addLayout(row)
        self.refresh_theme()

    # -- content --------------------------------------------------------------------------------------
    def title(self) -> str:
        return self._title.accessibleName() or self._title.text()

    def text(self) -> str:
        return self._text.accessibleName() or self._text.text()

    def set_host(self, host: str) -> None:
        """Fill the body for ``host`` - called when the confirm opens (lazily: see the module doc)."""
        line = confirm_body(self._i18n, host)
        su.set_text_in_face(self._text, line)   # a typed host in any script: the one face that has it all
        self._text.setAccessibleName(line)
        self.setAccessibleDescription(line)
        for b in (self._on_btn, self._off_btn):
            b.setAccessibleDescription(line)

    def set_ends_offer(self, last: bool) -> None:
        """The last "Not now" ends the offer for good: it says so ("No thanks")."""
        text = (_tr(self._i18n, "INTERNET_CONFIRM_NO_THANKS", "No thanks") if last
                else _tr(self._i18n, "INTERNET_CONFIRM_NOT_NOW", "Not now"))
        self._off_btn.setText(text)
        self._off_btn.setAccessibleName(text)
        su.apply_text_face(self._off_btn, text)

    def turn_on_button(self) -> QPushButton:
        return self._on_btn

    def not_now_button(self) -> QPushButton:
        return self._off_btn

    def is_done(self) -> bool:
        return self._done

    def show_done(self) -> None:
        """After Turn on (the sheet): title and buttons go, one quiet line says it is on and where to
        turn it off - no box."""
        self._done = True
        line = (_tr(self._i18n, "INTERNET_CONFIRM_DONE", "The internet check is on.") + " "
                + _tr(self._i18n, "INTERNET_CHECK_OFF_HINT", "Turn it off any time in Settings."))
        su.set_ui_text(self._text, line)
        self._text.setAccessibleName(line)
        self._title.hide()
        self._on_btn.hide()
        self._off_btn.hide()
        self.setAccessibleName(line)
        self.setAccessibleDescription("")
        self._colors = {}
        self.refresh_theme()

    def focus_first(self) -> None:
        """Keyboard users land on Turn on when the confirm opens from a keyboard-activated link."""
        self._on_btn.setFocus(Qt.FocusReason.TabFocusReason)

    # -- theme ----------------------------------------------------------------------------------------
    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type().value in self._THEME_EVENTS and not self._theming:
            self.refresh_theme()

    def colors(self) -> dict:
        return dict(self._colors)

    def refresh_theme(self) -> None:
        c = confirm_colors()
        if c == self._colors:
            return
        self._colors = c
        ring = tokens.FOCUS_RING_WIDTH
        r = tokens.RADIUS_CONTROL
        pad = f"padding: 0px {tokens.SPACE_M}px; min-height: {tokens.INTERNET_CONFIRM_BUTTON_HEIGHT - 2 * ring}px;"
        if c["hc"]:
            # Both buttons the same box (2 px border) so they are the same height. The primary is
            # filled with Highlight, as the first-run tip's (combined review U7); its focus ring is the
            # window text, since a Highlight ring would vanish on a Highlight fill.
            on_btn = (f"QPushButton#internetConfirmOn {{ background: {c['accent']}; color: {c['accent_text']};"
                      f" border: {ring}px solid {c['accent']}; border-radius: {r}px; {pad} }}")
            off_btn = (f"QPushButton#internetConfirmNotNow {{ background: {c['bg']}; color: {c['text']};"
                       f" border: {ring}px solid {c['text']}; border-radius: {r}px; {pad} }}")
            focus = (f" QPushButton#internetConfirmOn:focus {{ border: {ring}px solid {c['text']}; }}"
                     f" QPushButton#internetConfirmNotNow:focus {{ border: {ring}px solid {c['focus']}; }}")
        else:
            # One accent (Turn on) and the Fluent standard button the first-run tip uses too (subtle
            # fill, 1 px card stroke - combined review U7); the 2 px ring takes its extra pixel from the
            # padding, never the text.
            on_btn = su.button_style(accent=True).replace("QPushButton", "QPushButton#internetConfirmOn")
            off_btn = (f"QPushButton#internetConfirmNotNow {{ background: {c['fill']}; color: {c['text']};"
                       f" border: 1px solid {c['stroke']}; border-radius: {r}px;"
                       f" padding: 1px {tokens.SPACE_M + 1}px;"
                       f" min-height: {tokens.INTERNET_CONFIRM_BUTTON_HEIGHT - 2 * ring}px; }}")
            focus = (f" QPushButton#internetConfirmOn:focus {{ border: {ring}px solid {c['focus']}; }}"
                     f" QPushButton#internetConfirmNotNow:focus {{ border: {ring}px solid {c['focus']};"
                     f" padding: 0px {tokens.SPACE_M}px; }}")
        boxed = self._framed and not self._done
        frame = (f"QFrame#internetConfirm {{ background-color: {c['bg']}; border: 1px solid {c['stroke']};"
                 f" border-radius: {tokens.RADIUS_CARD}px; }}" if boxed
                 else "QFrame#internetConfirm { background: transparent; border: none; }")
        text_color = c["sub"] if self._done else c["text"]
        self._theming = True
        try:
            self.setStyleSheet(
                frame
                + f" QLabel#internetConfirmTitle {{ color: {c['text']}; background: transparent; border: none; }}"
                + f" QLabel#internetConfirmText {{ color: {text_color}; background: transparent; border: none; }}"
                + on_btn + off_btn + focus)
        finally:
            self._theming = False
        if self._done:
            self.layout().setContentsMargins(0, 0, 0, 0)


class InternetOfferTip(QWidget):
    """The Overview's confirm as a Fluent TeachingTip: a ~360 px card with a beak pointing at the link,
    shown as a popup over the page (nothing in the page moves). Esc or a click outside closes it
    (``dismissed`` - NOT a decline); Turn on / Not now are the confirm's.

    A local tip on purpose: #592's Flyout (the first-run TeachingTip) is not on this branch yet; once
    both are in, this should share its tail painting / placement code."""

    dismissed = pyqtSignal()

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("internetOfferTip")
        self._closing_for_action = False
        self._beak_side = "top"           # the card side the beak is on ("top": the tip is below the link)
        self._beak_x = 0.0
        m = tokens.INTERNET_TIP_MARGIN
        self._lay = QVBoxLayout(self)
        self.confirm = InternetCheckConfirm(i18n, framed=False)
        self._lay.addWidget(self.confirm)
        self._set_margins()
        self.setFixedWidth(tokens.INTERNET_TIP_WIDTH + 2 * m)
        self.setAccessibleName(self.confirm.title())
        self.confirm.turn_on_clicked.connect(self._mark_action)
        self.confirm.not_now_clicked.connect(self._mark_action)

    def _set_margins(self) -> None:
        m, b = tokens.INTERNET_TIP_MARGIN, tokens.INTERNET_TIP_BEAK_H
        ph, pv = tokens.INTERNET_TIP_PAD_H, tokens.INTERNET_TIP_PAD_V
        top = m + pv + (b if self._beak_side == "top" else 0)
        bottom = m + pv + (b if self._beak_side == "bottom" else 0)
        self._lay.setContentsMargins(m + ph, top, m + ph, bottom)

    def _mark_action(self) -> None:
        self._closing_for_action = True

    def beak_side(self) -> str:
        return self._beak_side

    def show_for(self, anchor: QWidget, focus: bool = False) -> None:
        """Place the tip under ``anchor`` (above it when the screen has no room below), its beak at
        the anchor's centre, inside the anchor's screen; show it, Turn on focused for keyboard users."""
        self._closing_for_action = False
        self.adjustSize()
        g = QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
        screen = QGuiApplication.screenAt(g.center()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect(0, 0, 10 ** 5, 10 ** 5)
        m, gap = tokens.INTERNET_TIP_MARGIN, tokens.INTERNET_TIP_GAP
        self._beak_side = "top"
        self._set_margins()
        self.adjustSize()
        h, w = self.sizeHint().height(), self.width()
        y = g.bottom() + gap - m
        if y + h > avail.bottom() and g.top() - gap - h + m >= avail.top():
            self._beak_side = "bottom"
            self._set_margins()
            self.adjustSize()
            h = self.sizeHint().height()
            y = g.top() - gap - h + m
        x = g.center().x() - w // 2
        x = max(avail.left(), min(x, avail.right() - w + 1))
        self.resize(w, h)
        self.move(x, y)
        inset = m + tokens.RADIUS_CARD + tokens.INTERNET_TIP_BEAK_W
        self._beak_x = float(max(inset, min(g.center().x() - x, w - inset)))
        self.show()
        self.raise_()
        if focus:
            self.confirm.focus_first()

    def close_for_action(self) -> None:
        self._closing_for_action = True
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if not self._closing_for_action:
            self.dismissed.emit()   # Esc / a click outside: closed, not declined
        self._closing_for_action = False

    def paintEvent(self, event) -> None:
        c = self.confirm.colors() or confirm_colors()
        m, bh, bw = tokens.INTERNET_TIP_MARGIN, tokens.INTERNET_TIP_BEAK_H, tokens.INTERNET_TIP_BEAK_W
        r = float(tokens.RADIUS_CARD)
        body = QRectF(self.rect()).adjusted(m + 0.5, m + 0.5, -m - 0.5, -m - 0.5)
        if self._beak_side == "top":
            body.setTop(body.top() + bh)
        else:
            body.setBottom(body.bottom() - bh)
        path = QPainterPath()
        path.addRoundedRect(body, r, r)
        beak = QPainterPath()
        x = self._beak_x
        if self._beak_side == "top":
            base = body.top()
            beak.moveTo(QPointF(x - bw, base + 1))
            beak.lineTo(QPointF(x, base - bh))
            beak.lineTo(QPointF(x + bw, base + 1))
        else:
            base = body.bottom()
            beak.moveTo(QPointF(x - bw, base - 1))
            beak.lineTo(QPointF(x, base + bh))
            beak.lineTo(QPointF(x + bw, base - 1))
        beak.closeSubpath()
        shape = path.united(beak)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not c.get("hc"):   # a soft Fluent shadow; a contrast theme gets its plain stroke only
            for i, alpha in ((3, 10), (2, 18), (1, 26)):
                p.setPen(QPen(QColor(0, 0, 0, alpha), 2 * i))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(shape)
        p.setPen(QPen(QColor(c["stroke"]), 1))
        p.setBrush(QColor(c["bg"]))
        p.drawPath(shape)
        p.end()
