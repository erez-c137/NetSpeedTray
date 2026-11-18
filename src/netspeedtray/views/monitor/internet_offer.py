"""
"Also watch the internet" - the one-click, inline opt-in to the internet check (2.2.2 D5).

With the internet check off (the default, and it stays off), every health figure in the Monitor comes
from pings to the user's own router, and says so ("Router: steady"). Beside it, a NoticeLink offers the
internet check; clicking it opens an inline confirm - never a dialog chain - that says in one line
exactly what leaves the PC and who sees it, with [Turn on] and [Not now]:

  * Turn on  -> ``latency_public_enabled`` through the normal config path (saved, applied live: the
               running probe adds the internet ping on its next cycle). Reversible in Settings.
  * Not now  -> the confirm closes and the offer is gone for the rest of the session; after
               ``INTERNET_OFFER_MAX_DECLINES`` of them it is never offered again (Settings still has it).

Nothing is turned on without the Turn on click. The Overview strip and the Check connection sheet share
this module, so the two always say the same thing and a "Not now" in one holds in the other.
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.utils import styles as su

logger = logging.getLogger("NetSpeedTray.InternetOffer")

# The session-wide "Not now" (an attribute on the main widget, so the Overview and the sheet agree).
_SNOOZE_ATTR = "internet_offer_snoozed"
# Unicode first-strong isolate: "1.1.1.1 (Cloudflare)" stays one left-to-right run inside a
# right-to-left sentence (Hebrew) - without it the closing parenthesis flips to the wrong end.
_FSI, _PDI = "⁨", "⁩"


def _tr(i18n, key: str, default: str) -> str:
    from netspeedtray.utils.helpers import tr
    return tr(i18n, key, default)


def provider_for_host(host: Any) -> Optional[str]:
    """Who runs ``host`` when it is a well-known public test server (1.1.1.1 -> "Cloudflare"), else
    None. IP addresses are compared in canonical form, names case-insensitively."""
    h = str(host or "").strip().rstrip(".").lower()
    if not h:
        return None
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        h = ipaddress.ip_address(h).compressed
    except ValueError:
        pass
    return constants.network.latency.ANCHOR_PROVIDERS.get(h)


def configured_host(config: Dict[str, Any]) -> str:
    """The internet test server the probe would ping (latency_probe's own fallback)."""
    return str(config.get("latency_public_host", "1.1.1.1") or "1.1.1.1").strip() or "1.1.1.1"


def offer_target(host: Any) -> str:
    """The test server as the confirm names it: "1.1.1.1 (Cloudflare)", or just the host when it is
    not a known one - as one isolated left-to-right run."""
    h = str(host or "").strip()
    provider = provider_for_host(h)
    run = f"{h} ({provider})" if provider else h
    return f"{_FSI}{run}{_PDI}"


def confirm_text(i18n, host: Any) -> str:
    """The one plain line on what leaves the PC, to whom, how often."""
    lat = constants.network.latency
    return _tr(i18n, "INTERNET_CONFIRM_TEXT",
               "Sends {count} tiny pings every {seconds} seconds to {target}, which sees your IP address. "
               "Nothing else leaves your PC.").format(
        count=lat.BURST_SIZE, seconds=f"{lat.OUTAGE_CYCLE_SEC:g}", target=offer_target(host))


class InternetOffer:
    """The offer's state and its two outcomes, for one main widget (the app's config owner).

    ``available()`` - offer it now? Only while the internet check is off, the latency probe is on (the
    router line it sits beside exists), the main widget can turn it on, and the user has not declined it
    this session or ``INTERNET_OFFER_MAX_DECLINES`` times in all."""

    def __init__(self, main_widget, config: Dict[str, Any]) -> None:
        self._mw = main_widget
        self._config = config

    def available(self) -> bool:
        cfg = self._config
        if bool(cfg.get("latency_public_enabled", False)) or not bool(cfg.get("latency_enabled", True)):
            return False
        if getattr(self._mw, _SNOOZE_ATTR, False) is True:
            return False
        try:
            declines = int(cfg.get("internet_offer_declines", 0) or 0)
        except (TypeError, ValueError):
            declines = 0
        if declines >= constants.network.latency.INTERNET_OFFER_MAX_DECLINES:
            return False
        return callable(getattr(self._mw, "turn_on_internet_check", None))

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
        """The Not now click: gone for this session, and one step closer to never offered again."""
        try:
            setattr(self._mw, _SNOOZE_ATTR, True)
        except Exception:
            pass
        try:
            count = int(self._config.get("internet_offer_declines", 0) or 0) + 1
        except (TypeError, ValueError):
            count = 1
        try:
            self._mw.config_controller.update_config({"internet_offer_declines": count},
                                                     apply_and_repaint=False)
        except Exception as e:
            self._config["internet_offer_declines"] = count   # at least this session remembers
            logger.debug("Could not save the internet offer's Not now: %s", e)


def offer_for(main_widget, config: Dict[str, Any]) -> Optional[InternetOffer]:
    """An InternetOffer when ``main_widget`` can turn the internet check on, else None."""
    if main_widget is None or not callable(getattr(main_widget, "turn_on_internet_check", None)):
        return None
    return InternetOffer(main_widget, config)


def _confirm_colors() -> dict:
    """{"bg", "stroke", "text", "accent", "accent_text", "focus", "hc"} for the confirm panel. High
    Contrast: the theme's own colours (window, window text, highlight), never the app's tokens."""
    from netspeedtray.utils.taskbar_appearance import read_high_contrast_link_colors
    hc = read_high_contrast_link_colors()
    if hc:
        return {"bg": hc["background"], "stroke": hc["text"], "text": hc["text"],
                "accent": hc["focus"], "accent_text": hc["background"], "focus": hc["link"], "hc": True}
    from netspeedtray.utils.components import notice_link_colors
    c = su.semantic_colors()
    return {"bg": c["card_bg"], "stroke": c["card_stroke"], "text": c["text_primary"],
            "accent": None, "accent_text": None, "focus": notice_link_colors(c["card_bg"])["focus"],
            "hc": False}


class _ConfirmButton(QPushButton):
    """A confirm action: Tab-reachable (no focus ring left behind by a mouse click), Enter and Space
    fire it, and it is never a dialog's default button (Enter on it never presses "Check again")."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)

    def keyPressEvent(self, event) -> None:
        from netspeedtray.utils.components import _enter_activates
        if not _enter_activates(self, event):
            super().keyPressEvent(event)


class InternetCheckConfirm(QFrame):
    """The inline confirm: one line on what leaves the PC, then [Turn on] [Not now]. After Turn on it
    stays as a short "is on" note (``show_done``) until the host hides it. Emits ``turn_on_clicked`` /
    ``not_now_clicked``; the host (with an InternetOffer) does the rest."""

    turn_on_clicked = pyqtSignal()
    not_now_clicked = pyqtSignal()

    _THEME_EVENTS = frozenset((QEvent.Type.PaletteChange.value, QEvent.Type.StyleChange.value,
                               QEvent.Type.ApplicationPaletteChange.value, 210))   # 210: ThemeChange

    def __init__(self, i18n, host: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("internetConfirm")
        self._i18n = i18n
        self._theming = False
        self._colors: dict = {}
        self.setAccessibleName(_tr(i18n, "INTERNET_WATCH_OFFER", "Also watch the internet"))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(tokens.SPACE_M, tokens.SPACE_S, tokens.SPACE_M, tokens.SPACE_S)
        lay.setSpacing(tokens.SPACE_S)
        self._text = QLabel()
        self._text.setObjectName("internetConfirmText")
        self._text.setWordWrap(True)
        self._text.setFont(su.font(tokens.TYPE_CAPTION))
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lay.addWidget(self._text)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(tokens.SPACE_S)
        self._on_btn = _ConfirmButton(_tr(i18n, "INTERNET_CONFIRM_TURN_ON", "Turn on"))
        self._on_btn.setObjectName("internetConfirmOn")
        self._off_btn = _ConfirmButton(_tr(i18n, "INTERNET_CONFIRM_NOT_NOW", "Not now"))
        self._off_btn.setObjectName("internetConfirmNotNow")
        for b in (self._on_btn, self._off_btn):
            b.setFont(su.font(tokens.TYPE_CAPTION))
            su.apply_text_face(b, b.text())
        self._on_btn.clicked.connect(self.turn_on_clicked.emit)
        self._off_btn.clicked.connect(self.not_now_clicked.emit)
        row.addWidget(self._on_btn)
        row.addWidget(self._off_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self._done = False
        self.set_host(host)
        self.refresh_theme()

    # -- content --------------------------------------------------------------------------------------
    def text(self) -> str:
        return self._text.accessibleName() or self._text.text()

    def set_host(self, host: str) -> None:
        if self._done:
            return
        line = confirm_text(self._i18n, host)
        su.set_ui_text(self._text, line)
        self._text.setAccessibleName(line)
        for b in (self._on_btn, self._off_btn):
            b.setAccessibleDescription(line)

    def turn_on_button(self) -> QPushButton:
        return self._on_btn

    def not_now_button(self) -> QPushButton:
        return self._off_btn

    def is_done(self) -> bool:
        return self._done

    def show_done(self) -> None:
        """After Turn on: the buttons go, a short note says it is on (and where to turn it off)."""
        self._done = True
        line = _tr(self._i18n, "INTERNET_CONFIRM_DONE",
                   "The internet check is on. You can turn it off in Settings.")
        su.set_ui_text(self._text, line)
        self._text.setAccessibleName(line)
        self._on_btn.hide()
        self._off_btn.hide()
        self.setAccessibleName(line)

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
        c = _confirm_colors()
        if c == self._colors:
            return
        self._colors = c
        ring = tokens.FOCUS_RING_WIDTH
        r = tokens.RADIUS_CONTROL
        pad = f"padding: {tokens.SPACE_XS}px {tokens.SPACE_M}px;"
        if c["hc"]:
            on_btn = (f"QPushButton#internetConfirmOn {{ background: {c['accent']}; color: {c['accent_text']};"
                      f" border: {ring}px solid {c['accent']}; border-radius: {r}px; {pad} }}")
            off_btn = (f"QPushButton#internetConfirmNotNow {{ background: {c['bg']}; color: {c['text']};"
                       f" border: {ring}px solid {c['text']}; border-radius: {r}px; {pad} }}")
        else:
            # The app's Fluent buttons (accent + standard), sized for a caption line.
            on_btn = su.button_style(accent=True).replace("QPushButton", "QPushButton#internetConfirmOn")
            off_btn = su.button_style(accent=False).replace("QPushButton", "QPushButton#internetConfirmNotNow")
        focus = (f" QPushButton#internetConfirmOn:focus, QPushButton#internetConfirmNotNow:focus"
                 f" {{ border: {ring}px solid {c['focus']}; }}")
        self._theming = True
        try:
            self.setStyleSheet(
                f"QFrame#internetConfirm {{ background-color: {c['bg']}; border: 1px solid {c['stroke']};"
                f" border-radius: {r}px; }}"
                f" QLabel#internetConfirmText {{ color: {c['text']}; background: transparent; border: none; }}"
                + on_btn + off_btn + focus)
        finally:
            self._theming = False


def wire_offer(link, confirm: InternetCheckConfirm, offer: Optional[InternetOffer],
               on_changed: Optional[Callable[[], None]] = None) -> None:
    """Connect a NoticeLink (the offer) and its confirm: the link opens the confirm (moving keyboard focus
    into it when the link was keyboard-activated), Turn on / Not now run the offer and close it.
    ``on_changed`` runs after either outcome so the host can re-lay itself out."""

    def _open() -> None:
        if offer is None or not offer.available():
            return
        keyboard = link.button().hasFocus()   # the link takes Tab focus only: focused = keyboard
        confirm.set_host(offer.host())
        link.hide()
        confirm.show()
        if keyboard:
            confirm.focus_first()
        if on_changed:
            on_changed()

    def _on() -> None:
        if offer is not None and offer.turn_on():
            confirm.show_done()
        else:
            confirm.hide()
        link.hide()
        if on_changed:
            on_changed()

    def _not_now() -> None:
        if offer is not None:
            offer.decline()
        confirm.hide()
        link.hide()
        if on_changed:
            on_changed()

    link.set_on_click(_open)
    confirm.turn_on_clicked.connect(_on)
    confirm.not_now_clicked.connect(_not_now)
