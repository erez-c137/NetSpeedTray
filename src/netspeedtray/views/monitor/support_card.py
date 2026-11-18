"""
The one-time "rate / star / support" card in the Monitor (2.2.2 D8). The decision is
core/support_prompt (pure); this owns the moment and the card.

- **The moment:** the Monitor window being OPENED (built), at least SUPPORT_PROMPT_AFTER_DAYS after
  ``first_seen_at``. Never on launch: not in the app's first SUPPORT_PROMPT_MIN_UPTIME_S, nor in the
  sign-in rush. Never over a fullscreen app, never with a modal dialog up, never while another card of
  ours (the first-run tip / "what's new", an update offer, a notice, a data-cap alert) or the
  "running" panel is up. A blocked open is a "not this time": the card is owed to the next open.
- **The card:** inline, at the top of the Monitor under the tabs (so it is there whichever tab the
  Monitor reopens on) - a Windows 11 InfoBar: a heart glyph, "Enjoying NetSpeedTray? It's free and
  always will be.", and four equal-weight buttons: Rate it on the Microsoft Store / Star it on GitHub /
  Support it / No thanks. No accent button - "No thanks" weighs the same as the asks.
- **Once, ever:** any button marks it done and removes it; so does being on screen for
  SUPPORT_PROMPT_SEEN_MS (closing the Monitor after that still counts). Nothing records WHICH button
  (zero telemetry), nothing is sent anywhere, and nothing in the app changes either way.
- **Links:** the Store's review page through the ``ms-windows-store:`` protocol (its web page when the
  Store app is missing or the protocol fails), the GitHub repo (star), and the app's own Support dialog
  (the channels it already offers - this card adds none). All through helpers.open_external.
- **Off by policy:** DisableTips (DEPLOYING.md), re-read at every open.
- Keyboard: every button is a Tab stop, Enter / Space press it, the focus ring shows for keyboard focus
  only. Narrator reads the card's name ("Enjoying NetSpeedTray?") and description. High Contrast draws
  in the theme's system colours (the Flyout's palette); right-to-left languages mirror the row.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.core import support_prompt as logic
from netspeedtray.utils import styles as su

logger = logging.getLogger("NetSpeedTray.SupportCard")

ACTION_RATE, ACTION_STAR, ACTION_SUPPORT, ACTION_NO_THANKS = "rate", "star", "support", "no_thanks"
_GLYPH_HEART = 0xEB51            # Segoe Fluent "HeartFill" - the Support dialog's Sponsors glyph
_BUTTON_MIN_H, _BUTTON_MIN_W, _GAP = 32, 96, 8


def _tr(i18n, key: str, default: str) -> str:
    from netspeedtray.utils.helpers import tr
    return tr(i18n, key, default)


def _alive_visible(obj) -> bool:
    if obj is None:
        return False
    try:
        from PyQt6 import sip
        if sip.isdeleted(obj):
            return False
    except Exception:
        pass
    try:
        return bool(obj.isVisible())
    except Exception:
        return False


def _seconds_since_sign_in() -> Optional[float]:
    try:
        from netspeedtray.core.shutdown_channel import session_seconds_since_logon
        return session_seconds_since_logon()
    except Exception:
        return None


def _policy_allows() -> bool:
    from netspeedtray.core.startup_notices import tips_disabled_by_policy
    return not tips_disabled_by_policy()


def save_config(main_widget: Any, updates: dict) -> None:
    """Save app-state bookkeeping through the widget (no repaint), as StartupNotices does."""
    config = getattr(main_widget, "config", None)
    if config is None:
        return
    changed = {k: v for k, v in (updates or {}).items() if config.get(k, object()) != v}
    if not changed:
        return
    config.update(changed)
    controller = getattr(main_widget, "config_controller", None)
    try:
        if controller is not None:
            controller.update_config(changed, apply_and_repaint=False)
        else:
            main_widget.update_config(changed)
    except Exception as e:
        logger.error("Could not save the support card's state: %s", e)


def ensure_first_seen(main_widget: Any, now: Optional[datetime] = None) -> None:
    """Start the D8 clock on the first run of 2.2.2+ (a no-op once it runs)."""
    config = getattr(main_widget, "config", None)
    if config is not None:
        save_config(main_widget, logic.first_seen_updates(config, now))


def blocked_reason(main_widget: Any) -> Optional[str]:
    """Why this Monitor open may not carry the card, or None. Each one is a 'not this time'."""
    w = main_widget
    start = getattr(w, "session_start_time", None)
    if isinstance(start, datetime):
        if (datetime.now() - start).total_seconds() < constants.app.SUPPORT_PROMPT_MIN_UPTIME_S:
            return "the app only just started"
    since = _seconds_since_sign_in()
    if since is not None and 0 <= since < constants.app.SIGN_IN_DUPLICATE_WINDOW_S:
        return "the sign-in rush"
    handler = getattr(w, "system_event_handler", None)
    try:
        if handler is not None and handler.is_fullscreen_active():
            return "a fullscreen app"
    except Exception:
        pass
    latch = getattr(w, "_fs_latch", None)
    if getattr(latch, "covered", False) is True:
        return "a fullscreen app"
    try:
        from PyQt6.QtWidgets import QApplication
        if QApplication.activeModalWidget() is not None:
            return "a modal dialog"
    except Exception:
        pass
    notices = getattr(w, "_startup_notices", None)
    live = getattr(notices, "_live_card", None)
    if callable(live):
        try:
            if _alive_visible(live()):
                return "the first-run tip or what's new is up"
        except Exception:
            pass
    for name in ("_update_flyout", "_notice_flyout", "_usage_alert_flyout", "_running_panel"):
        if _alive_visible(getattr(w, name, None)):
            return "another card of ours is up"
    return None


def should_offer(main_widget: Any, now: Optional[datetime] = None) -> bool:
    """The whole decision for one Monitor open (the clock, once-ever, the policy, the moment)."""
    config = getattr(main_widget, "config", None)
    if config is None:
        return False
    ensure_first_seen(main_widget, now)
    if not logic.due(config, prompts_allowed=_policy_allows(), now=now):
        return False
    reason = blocked_reason(main_widget)
    if reason:
        logger.info("Support card owed, but not on this Monitor open: %s.", reason)
        return False
    return True


def open_store_review(opener: Optional[Callable[[str], Any]] = None) -> None:
    """The Store app's review page, or the Store's web page when there is no Store app or the
    protocol launch fails."""
    if opener is None:
        from netspeedtray.utils.helpers import open_external as opener
    if logic.store_protocol_registered() and opener(logic.store_review_url()) is not False:
        return
    opener(logic.store_web_url())


class SupportCard(QFrame):
    """The inline InfoBar. ``answered(action)`` when a button is pressed (then it is removed);
    ``seen`` once it has been on screen for SUPPORT_PROMPT_SEEN_MS."""

    answered = pyqtSignal(str)
    seen = pyqtSignal()

    def __init__(self, i18n, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from netspeedtray.views.flyout import _card_colors
        self.setObjectName("supportCard")
        self._c = c = _card_colors()
        title = _tr(i18n, "SUPPORT_PROMPT_TITLE", "Enjoying NetSpeedTray?")
        body = _tr(i18n, "SUPPORT_PROMPT_BODY", "It's free and always will be.")
        self.setAccessibleName(title)
        self.setAccessibleDescription(body)
        self.setStyleSheet(
            f"QFrame#supportCard {{ background: {c['subtle_fill']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: {tokens.RADIUS_CARD}px; }}")

        outer = QHBoxLayout(self)       # mirrored automatically in a right-to-left language
        outer.setContentsMargins(tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_M)
        outer.setSpacing(12)

        icon = QLabel(chr(_GLYPH_HEART), self)
        icon.setAccessibleName("")
        icon.setStyleSheet(f"color: {c['accent']}; background: transparent;"
                           f" font-family: '{su.icon_font_family()}'; font-size: 16px;")
        icon.setFixedWidth(20)
        outer.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._title = QLabel(title, self)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setWordWrap(True)
        self._title.setFont(su.font(tokens.TYPE_BODY_STRONG))
        self._title.setStyleSheet(f"color: {c['text_primary']}; background: transparent; border: none;")
        col.addWidget(self._title)
        self._body = QLabel(body, self)
        self._body.setTextFormat(Qt.TextFormat.PlainText)
        self._body.setWordWrap(True)
        self._body.setFont(su.font(tokens.TYPE_BODY))
        self._body.setStyleSheet(f"color: {c['text_secondary']}; background: transparent; border: none;")
        col.addWidget(self._body)
        col.addSpacing(10)

        specs = (
            (ACTION_RATE, "SUPPORT_PROMPT_RATE_BUTTON", "Rate it on the Microsoft Store"),
            (ACTION_STAR, "SUPPORT_PROMPT_STAR_BUTTON", "Star it on GitHub"),
            (ACTION_SUPPORT, "SUPPORT_PROMPT_SUPPORT_BUTTON", "Support it"),
            (ACTION_NO_THANKS, "SUPPORT_PROMPT_NO_THANKS_BUTTON", "No thanks"),
        )
        self.buttons = {}
        for action, key, default in specs:
            btn = self._make_button(_tr(i18n, key, default))
            btn.clicked.connect(lambda _=False, a=action: self._answer(a))
            self.buttons[action] = btn
        order = [self.buttons[a] for a, _k, _d in specs]
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_GAP)
        self._grid.setVerticalSpacing(_GAP)
        self._columns = 0
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addLayout(self._grid)
        row.addStretch(1)
        col.addLayout(row)
        outer.addLayout(col, 1)
        self._reflow(4)

        self._seen_timer = QTimer(self)
        self._seen_timer.setSingleShot(True)
        self._seen_timer.setInterval(constants.app.SUPPORT_PROMPT_SEEN_MS)
        self._seen_timer.timeout.connect(self._on_seen)
        self._seen_done = False
        self._answered = False

    # --- buttons and layout ------------------------------------------------------------------

    def _make_button(self, text: str) -> QPushButton:
        """The Flyout's neutral button: equal weight for all four (no accent fill)."""
        from netspeedtray.views.flyout import _FlyoutButton   # keyboard-only focus ring
        c = self._c
        btn = _FlyoutButton(text, self)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFont(su.font(tokens.TYPE_BODY))
        btn.setMinimumHeight(_BUTTON_MIN_H)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setAutoDefault(False)
        normal = (f"background-color: {c['card_bg']}; color: {c['text_primary']};"
                  f" border: 1px solid {c['card_stroke']}; padding: 6px 13px 7px;")
        focus = f"border: 2px solid {c['focus']}; padding: 5px 12px 6px;"
        hover = "" if c.get("high_contrast") else f"QPushButton:hover {{ border-color: {c['accent']}; }}"
        btn.setStyleSheet(
            f"QPushButton {{ {normal} border-radius: {tokens.RADIUS_CONTROL}px; }}"
            f"{hover}"
            f"QPushButton[kbfocus=\"true\"] {{ {focus} }}")
        return btn

    def _button_width(self, btn: QPushButton) -> int:
        return max(_BUTTON_MIN_W, btn.fontMetrics().horizontalAdvance(btn.text()) + 2 * (13 + 2) + 4)

    def _columns_for(self, avail: int) -> int:
        widths = [self._button_width(b) for b in self.buttons.values()]
        if sum(widths) + _GAP * 3 <= avail:
            return 4
        if 2 * max(widths) + _GAP <= avail:
            return 2
        return 1

    def _reflow(self, columns: int) -> None:
        """Four in a row when they fit; else a 2 x 2 grid of equal buttons; else one column."""
        if columns == self._columns:
            return
        self._columns = columns
        for btn in self.buttons.values():
            self._grid.removeWidget(btn)
        widths = [self._button_width(b) for b in self.buttons.values()]
        for i, btn in enumerate(self.buttons.values()):
            btn.setMinimumWidth(widths[i] if columns == 4 else max(widths))
            self._grid.addWidget(btn, i // columns, i % columns)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        m = self.contentsMargins()
        lay = self.layout().contentsMargins()
        avail = self.width() - m.left() - m.right() - lay.left() - lay.right() - 20 - 12
        self._reflow(self._columns_for(avail))

    def minimumSizeHint(self) -> QSize:   # never forces the Monitor wider than its own minimum
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 300), hint.height())

    # --- keyboard ----------------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = self.focusWidget()
            if isinstance(focused, QPushButton) and focused in self.buttons.values():
                focused.click()
                return
        super().keyPressEvent(event)

    # --- seen / answered ---------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._seen_done:
            self._seen_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._seen_timer.stop()     # closed / minimized before it was really seen: not counted

    def _on_seen(self) -> None:
        if not self._seen_done and self.isVisible():
            self._seen_done = True
            self.seen.emit()

    def _answer(self, action: str) -> None:
        if self._answered:
            return
        self._answered = True
        self._seen_timer.stop()
        self.answered.emit(action)
        self.hide()
        self.deleteLater()


class SupportPrompt:
    """Wires one SupportCard into one Monitor window (``attach`` decides; returns the card or None)."""

    def __init__(self, window: QWidget, main_widget: Any, i18n,
                 opener: Optional[Callable[[str], Any]] = None) -> None:
        self._window = window
        self._w = main_widget
        self._i18n = i18n
        self._opener = opener
        self.card: Optional[SupportCard] = None

    def attach(self, host_layout, index: int, *, now: Optional[datetime] = None) -> Optional[SupportCard]:
        try:
            if not should_offer(self._w, now):
                return None
            card = SupportCard(self._i18n, self._window)
            card.answered.connect(self._on_answered)
            card.seen.connect(self._mark_done)
            wrap = QWidget(self._window)
            wrap.setObjectName("supportCardHost")
            lay = QVBoxLayout(wrap)
            m = constants.layout.MONITOR_BODY_MARGIN
            lay.setContentsMargins(m, 4, m, 4)
            lay.addWidget(card)
            card.destroyed.connect(wrap.deleteLater)
            host_layout.insertWidget(index, wrap)
            self.card = card
            logger.info("Support card shown in the Monitor (once, ever).")
            return card
        except Exception as e:
            logger.error("Could not show the support card: %s", e, exc_info=True)
            return None

    def _mark_done(self) -> None:
        save_config(self._w, logic.shown_updates())

    def _open(self, url: str) -> Any:
        if self._opener is not None:
            return self._opener(url)
        from netspeedtray.utils.helpers import open_external
        return open_external(url)

    def _on_answered(self, action: str) -> None:
        # Done forever, whichever button. Which one is never recorded (zero telemetry).
        self._mark_done()
        self.card = None
        try:
            if action == ACTION_RATE:
                open_store_review(self._open)
            elif action == ACTION_STAR:
                self._open(logic.github_repo_url())
            elif action == ACTION_SUPPORT:
                QTimer.singleShot(0, self._open_support_dialog)
        except Exception as e:
            logger.error("Support card action failed: %s", e)

    def _open_support_dialog(self) -> None:
        """The app's own Support dialog, over the Monitor (the channels it offers today)."""
        try:
            from netspeedtray.views.support_dialog import SupportDialog
            icon = None
            try:
                icon = self._w.windowIcon()
            except Exception:
                pass
            SupportDialog(self._i18n, self._window, app_icon=icon).exec()
        except Exception as e:
            logger.error("Could not open the Support dialog: %s", e)
