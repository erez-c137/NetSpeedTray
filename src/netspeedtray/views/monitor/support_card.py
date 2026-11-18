"""
The one-time "rate / star / support" card in the Monitor (2.2.2 D8). The decision is
core/support_prompt (pure); this owns the moment and the card.

- **The moment:** the Monitor window being OPENED (built), at least SUPPORT_PROMPT_AFTER_DAYS after
  ``first_seen_at``. Never on launch: not in the app's first SUPPORT_PROMPT_MIN_UPTIME_S, nor in the
  sign-in rush. Never over a fullscreen app, never with a modal dialog up or Settings open, never while
  another card of ours (the first-run tip / "what's new", an update offer, a notice, a data-cap alert)
  or the "running" panel is up (not one that is closing - its own Monitor row opened this Monitor), and
  never in a session that already had (or still owes) another one-time moment (the tip / "what's new",
  the temperature setup's automatic showing). A blocked open is a "not this time": the card is owed to
  the next open.
- **The card:** inline, at the top of the Monitor under the tabs (so it is there whichever tab the
  Monitor reopens on), shaped like a WinUI InfoBar: a neutral star glyph, the title "A month with
  NetSpeedTray", one line ("It's free and always will be. ... a rating or a star helps others find
  it."), the actions [Rate it in the Store] [Star it on GitHub] and a "Support the project" link, and
  the InfoBar's own close X ("Dismiss"). The two buttons are equal weight - neither is an accent
  button - and the X is the plain way out.
- **Rate only where it works:** the Store accepts a rating only from a user who has the app in their
  Store library, so the Rate button exists only on a Store install (support_prompt.installed_from_store,
  recorded by Setup's /STORE); everyone else gets Star alone and the body says "a star helps".
- **Once, ever:** any action (Rate, Star, the link, the X) marks it done and collapses it (~150 ms, or
  at once with Reduce motion), handing the keyboard to the active tab. So does being on screen for
  SUPPORT_PROMPT_SEEN_MS while the Monitor is the ACTIVE, un-minimized window - re-armed from zero on
  each activation, so a Monitor left behind other windows or minimized never "sees" it. Nothing
  records WHICH action (zero telemetry), nothing is sent anywhere, and nothing in the app changes.
- **Links:** the Store's review page through the ``ms-windows-store:`` protocol (its web page when the
  Store app is missing or the protocol fails; on a Store turned off by Group Policy the protocol opens
  the Store's "blocked" page - acceptable, managed fleets turn this card off with DisableTips), the
  GitHub repo (star), and the app's own Support dialog (the channels it already offers - this card adds
  none). All through helpers.open_external.
- **Saved safely:** through main_widget.save_config_keys, so an open Settings dialog's unsaved preview
  edits never reach the file with it.
- **Off by policy:** DisableTips (DEPLOYING.md), re-read at every open.
- Keyboard: every action is a Tab stop, Enter / Space press it, the focus ring shows for keyboard focus
  only. Narrator reads the card's name (the title) and description (the body), and the X as "Dismiss".
  High Contrast draws in the theme's system colours (the Flyout's palette); right-to-left languages
  mirror the row.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from PyQt6.QtCore import QEvent, QObject, QPropertyAnimation, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from netspeedtray import constants
from netspeedtray.constants.styles import styles as tokens
from netspeedtray.core import support_prompt as logic
from netspeedtray.utils import styles as su

logger = logging.getLogger("NetSpeedTray.SupportCard")

ACTION_RATE, ACTION_STAR, ACTION_SUPPORT, ACTION_DISMISS = "rate", "star", "support", "dismiss"
_GLYPH_STAR = 0xE734             # Segoe Fluent "FavoriteStar" - neutral, in the secondary text colour
_GLYPH_CLOSE = 0xE8BB            # Segoe Fluent "ChromeClose" - the InfoBar's close button
_BUTTON_MIN_H, _BUTTON_MIN_W, _GAP = 32, 96, 8
_CLOSE_SIZE = 32


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
    """Save app-state bookkeeping. Through the widget's save_config_keys when it has one: while a
    Settings dialog is open the live config also holds its unsaved PREVIEW edits, and that path saves
    the dialog's baseline plus these keys - never the preview. Else the controller (no repaint)."""
    config = getattr(main_widget, "config", None)
    if config is None:
        return
    changed = {k: v for k, v in (updates or {}).items() if config.get(k, object()) != v}
    if not changed:
        return
    try:
        save_keys = getattr(main_widget, "save_config_keys", None)
        if callable(save_keys):
            save_keys(changed)          # applies to the live config too
            config.update(changed)
            return
        config.update(changed)
        controller = getattr(main_widget, "config_controller", None)
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
    if _alive_visible(getattr(w, "settings_dialog", None)):
        return "Settings is open"
    notices = getattr(w, "_startup_notices", None)
    live = getattr(notices, "_live_card", None)
    if callable(live):
        try:
            if _alive_visible(live()):
                return "the first-run tip or what's new is up"
        except Exception:
            pass
    for name in ("_update_flyout", "_notice_flyout", "_usage_alert_flyout"):
        if _alive_visible(getattr(w, name, None)):
            return "another card of ours is up"
    # C6: the "running" panel blocks only while the user is on it - a panel whose row opened this very
    # Monitor is closing (it runs the action first and closes on the next loop turn).
    panel = getattr(w, "_running_panel", None)
    if _alive_visible(panel) and not getattr(panel, "closing", False):
        return "another card of ours is up"
    # C7: one one-time moment per session (the tip / what's new, the temperature setup, this card).
    from netspeedtray.views.widget.startup_notices import MOMENT_SUPPORT, one_time_moment_blocked
    return one_time_moment_blocked(w, MOMENT_SUPPORT)


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
    """The inline InfoBar. ``answered(action)`` on Rate / Star / the Support link / the X;
    ``seen`` once it has been on screen for SUPPORT_PROMPT_SEEN_MS in the active Monitor."""

    answered = pyqtSignal(str)
    seen = pyqtSignal()

    def __init__(self, i18n, parent: Optional[QWidget] = None, *, show_rate: bool = False) -> None:
        super().__init__(parent)
        from netspeedtray.views.flyout import _card_colors
        from netspeedtray.utils.components import NoticeLink
        self.setObjectName("supportCard")
        self._c = c = _card_colors()
        self.show_rate = bool(show_rate)
        title = _tr(i18n, "SUPPORT_PROMPT_TITLE", "A month with NetSpeedTray")
        if self.show_rate:
            body = _tr(i18n, "SUPPORT_PROMPT_BODY", "It's free and always will be. If it's earned its spot "
                       "on your taskbar, a rating or a star helps others find it.")
        else:
            body = _tr(i18n, "SUPPORT_PROMPT_BODY_STAR_ONLY", "It's free and always will be. If it's earned "
                       "its spot on your taskbar, a star helps others find it.")
        self.setAccessibleName(title)
        self.setAccessibleDescription(body)
        self.setStyleSheet(
            f"QFrame#supportCard {{ background: {c['subtle_fill']}; border: 1px solid {c['card_stroke']};"
            f" border-radius: {tokens.RADIUS_CARD}px; }}")

        outer = QHBoxLayout(self)       # mirrored automatically in a right-to-left language
        outer.setContentsMargins(tokens.SPACE_M, tokens.SPACE_M, tokens.SPACE_S, tokens.SPACE_M)
        outer.setSpacing(12)

        icon = QLabel(chr(_GLYPH_STAR), self)
        icon.setStyleSheet(f"color: {c['text_secondary']}; background: transparent; border: none;"
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

        self.buttons = {}
        if self.show_rate:
            self.buttons[ACTION_RATE] = self._make_button(
                _tr(i18n, "SUPPORT_PROMPT_RATE_BUTTON", "Rate it in the Store"), ACTION_RATE)
        self.buttons[ACTION_STAR] = self._make_button(
            _tr(i18n, "SUPPORT_PROMPT_STAR_BUTTON", "Star it on GitHub"), ACTION_STAR)
        self.support_link = NoticeLink("", _tr(i18n, "SUPPORT_PROMPT_SUPPORT_LINK", "Support the project"),
                                       lambda: self._answer(ACTION_SUPPORT), font_token=tokens.TYPE_BODY,
                                       surface=c['subtle_fill'], parent=self)
        self._actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(_GAP)
        for btn in self.buttons.values():
            self._actions.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeading)
        self._actions.addWidget(self.support_link, 0, Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter)
        self._actions.addStretch(1)
        col.addLayout(self._actions)
        outer.addLayout(col, 1)

        from netspeedtray.views.flyout import _FlyoutButton   # keyboard-only focus ring
        self.close_button = _FlyoutButton(chr(_GLYPH_CLOSE), self)
        dismiss = _tr(i18n, "SUPPORT_PROMPT_DISMISS", "Dismiss")
        self.close_button.setAccessibleName(dismiss)
        self.close_button.setToolTip(dismiss)
        self.close_button.setFixedSize(_CLOSE_SIZE, _CLOSE_SIZE)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setAutoDefault(False)
        hover = "" if c.get("high_contrast") else (
            f"QPushButton:hover {{ color: {c['text_primary']}; background: {c['card_bg']}; }}")
        self.close_button.setStyleSheet(
            f"QPushButton {{ color: {c['text_secondary']}; background: transparent;"
            f" border: 1px solid transparent; border-radius: {tokens.RADIUS_CONTROL}px;"
            f" font-family: '{su.icon_font_family()}'; font-size: 11px; }}"
            f"{hover}"
            f"QPushButton[kbfocus=\"true\"] {{ color: {c['text_primary']}; border: 2px solid {c['focus']}; }}")
        self.close_button.clicked.connect(lambda: self._answer(ACTION_DISMISS))
        outer.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)

        chain = list(self.buttons.values()) + [self.support_link.button(), self.close_button]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)

        self._seen_timer = QTimer(self)
        self._seen_timer.setSingleShot(True)
        self._seen_timer.setInterval(constants.app.SUPPORT_PROMPT_SEEN_MS)
        self._seen_timer.timeout.connect(self._on_seen)
        self._seen_done = False
        self._answered = False
        self._watched: Optional[QWidget] = None

    # --- buttons and layout ------------------------------------------------------------------

    def _make_button(self, text: str, action: str) -> QPushButton:
        """A Fluent standard button. Rate and Star share one style: equal weight, no accent fill."""
        from netspeedtray.views.flyout import _FlyoutButton   # keyboard-only focus ring
        c = self._c
        btn = _FlyoutButton(text, self)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFont(su.font(tokens.TYPE_BODY))
        btn.setMinimumHeight(_BUTTON_MIN_H)
        btn.setMinimumWidth(_BUTTON_MIN_W)
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
        btn.clicked.connect(lambda _=False, a=action: self._answer(a))
        return btn

    def actions_width(self) -> int:
        """The width the action row needs on one line."""
        widths = [max(b.minimumWidth(), b.sizeHint().width()) for b in self.buttons.values()]
        widths.append(self.support_link.sizeHint().width())
        return sum(widths) + _GAP * (len(widths) - 1)

    def actions_on_one_row(self) -> bool:
        return self._actions.direction() in (QBoxLayout.Direction.LeftToRight, QBoxLayout.Direction.RightToLeft)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        m = self.layout().contentsMargins()
        avail = self.width() - m.left() - m.right() - 20 - 12 - _CLOSE_SIZE - 12
        want = QBoxLayout.Direction.LeftToRight if self.actions_width() <= avail else QBoxLayout.Direction.TopToBottom
        if self._actions.direction() != want:
            self._actions.setDirection(want)

    def minimumSizeHint(self) -> QSize:   # never forces the Monitor wider than its own minimum
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 300), hint.height())

    # --- keyboard ----------------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focused = self.focusWidget()
            if isinstance(focused, QPushButton) and self.isAncestorOf(focused):
                focused.click()
                return
        super().keyPressEvent(event)

    # --- seen: only while the Monitor is the active, un-minimized window -----------------------

    def _counting(self) -> bool:
        win = self.window()
        return (self.isVisible() and win is not None and win.isActiveWindow()
                and not win.isMinimized())

    def _update_seen_timer(self) -> None:
        """Run the 'seen' timer only while it counts; any pause re-arms it from zero."""
        if self._seen_done or self._answered:
            self._seen_timer.stop()
            return
        if self._counting():
            if not self._seen_timer.isActive():
                self._seen_timer.start()
        else:
            self._seen_timer.stop()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._watched and event.type() in (
                QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate,
                QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange):
            QTimer.singleShot(0, self._update_seen_timer)   # after Qt has updated the window's state
        return False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        win = self.window()
        if win is not self and win is not self._watched:
            if self._watched is not None:
                self._watched.removeEventFilter(self)
            self._watched = win
            win.installEventFilter(self)
        self._update_seen_timer()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._seen_timer.stop()     # closed / hidden before it was really seen: not counted

    def _on_seen(self) -> None:
        if not self._seen_done and self._counting():
            self._seen_done = True
            self.seen.emit()

    def _answer(self, action: str) -> None:
        if self._answered:
            return
        self._answered = True
        self._seen_timer.stop()
        self.answered.emit(action)


class SupportPrompt:
    """Wires one SupportCard into one Monitor window (``attach`` decides; returns the card or None).
    ``focus_target``: returns the widget the keyboard goes to when the card is dismissed (the
    active tab's button)."""

    def __init__(self, window: QWidget, main_widget: Any, i18n,
                 opener: Optional[Callable[[str], Any]] = None,
                 focus_target: Optional[Callable[[], Optional[QWidget]]] = None) -> None:
        self._window = window
        self._w = main_widget
        self._i18n = i18n
        self._opener = opener
        self._focus_target = focus_target
        self.card: Optional[SupportCard] = None
        self.host: Optional[QWidget] = None
        self._anim: Optional[QPropertyAnimation] = None

    def attach(self, host_layout, index: int, *, now: Optional[datetime] = None) -> Optional[SupportCard]:
        try:
            if not should_offer(self._w, now):
                return None
            card = SupportCard(self._i18n, self._window, show_rate=logic.installed_from_store())
            card.answered.connect(self._on_answered)
            card.seen.connect(self._mark_done)
            wrap = QWidget(self._window)
            wrap.setObjectName("supportCardHost")
            lay = QVBoxLayout(wrap)
            m = constants.layout.MONITOR_BODY_MARGIN
            lay.setContentsMargins(m, 4, m, 4)
            lay.addWidget(card)
            host_layout.insertWidget(index, wrap)
            self.card, self.host = card, wrap
            from netspeedtray.views.widget.startup_notices import MOMENT_SUPPORT, take_moment
            take_moment(self._w, MOMENT_SUPPORT)
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
        # Done forever, whichever action. Which one is never recorded (zero telemetry).
        self._mark_done()
        try:
            if action == ACTION_RATE:
                open_store_review(self._open)
            elif action == ACTION_STAR:
                self._open(logic.github_repo_url())
            elif action == ACTION_SUPPORT:
                QTimer.singleShot(0, self._open_support_dialog)
        except Exception as e:
            logger.error("Support card action failed: %s", e)
        self._collapse()

    def _collapse(self) -> None:
        """Fold the card away (~150 ms; at once with Reduce motion), then hand the keyboard to the
        active tab so it never falls into the page below."""
        host, self.card = self.host, None
        self._return_focus()
        if host is None:
            return
        if su.prefers_reduced_motion() or constants.app.SUPPORT_PROMPT_COLLAPSE_MS <= 0:
            self._remove(host)
            return
        host.setMinimumHeight(0)
        anim = QPropertyAnimation(host, b"maximumHeight", host)
        anim.setDuration(constants.app.SUPPORT_PROMPT_COLLAPSE_MS)
        anim.setStartValue(max(1, host.height()))
        anim.setEndValue(0)
        anim.finished.connect(lambda: self._remove(host))
        self._anim = anim
        anim.start()

    def _remove(self, host: QWidget) -> None:
        if self.host is host:
            self.host = None
        self._anim = None
        try:
            host.hide()
            host.deleteLater()
        except Exception:
            pass

    def _return_focus(self) -> None:
        target = None
        if self._focus_target is not None:
            try:
                target = self._focus_target()
            except Exception:
                target = None
        if target is not None:
            try:
                target.setFocus(Qt.FocusReason.OtherFocusReason)
            except Exception:
                pass

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
