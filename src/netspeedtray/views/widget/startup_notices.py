"""
The first-run tip and the one-time "what's new" on screen (2.2.2 D1, D2). The decision is
core/startup_notices.plan_startup_notice; this owns the timing and the card.

- Shown STARTUP_NOTICE_DELAY_MS after the widget is really visible, never in the instant it appears.
- Deferred while the widget is hidden (re-armed by on_widget_shown) and - retried every
  STARTUP_NOTICE_RETRY_MS - while it is stepped aside for app buttons, behind the update offer's gates
  (``_update_offer_blocked``: a fullscreen app, a modal dialog, a locked / idle / away session), during
  the sign-in rush (the first SIGN_IN_DUPLICATE_WINDOW_S of a session), while Settings is open, and
  while another card of ours is up. A deferred notice is owed again next launch if this one never got
  to show it.
- Marked shown (saved) the moment it shows - once ever. It does not time out: it stays beside the
  widget until answered. It steps aside - hidden, then brought back the same way (the "rehome" path) -
  when the widget hides, when a fullscreen app starts (even with "Keep visible in fullscreen"), and
  while another card (an update offer, a notice, a data-cap alert) is up: theirs win, the tip waits.
- The tip counts as answered when the user does what it teaches: the hover card opens, a double-click,
  the right-click menu - or opens the Monitor or Settings from the "running" panel. Also while it is
  still waiting to show: it is used up then (saved), never shown afterwards (combined review C3/C8).
- It never shows while the hover card is up, and a shown tip steps aside while an app-modal dialog of
  ours is up and comes back after it (C9).
- One one-time moment per session (C7): the tip / "what's new", the Monitor's support card (D8) and the
  temperature setup's one automatic showing (D6) never share a session. A notice that finds another
  moment already taken waits for the next launch (still owed).
- Placed like the hover card (above a bottom bar, below a top one, beside a side bar), the tip with a
  tail pointing at the widget; follows the widget when it moves.
- Focusable (a click lets the keyboard in: Tab / Enter / Esc) but shown without activating, so it
  never takes the keyboard from the app the user is typing in - except while a screen reader runs
  (SPI_GETSCREENREADER): then it is shown activated, so Narrator reads it. Closing it hands the
  foreground back (Flyout.closeEvent).
- The IT policy (DisableTips) is re-read at show time, so a policy pushed while it waits drops it.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from PyQt6.QtCore import QObject, Qt, QTimer

from netspeedtray import constants
from netspeedtray.core import startup_notices as logic

logger = logging.getLogger("NetSpeedTray.StartupNotices")

REASON_HIDDEN = "widget hidden"

# The session's one-time moment (combined review C7): which one-time surface showed this session, on
# the widget (one per process). The first-run tip / "what's new" (here), the support card (D8,
# views/monitor/support_card) and the temperature setup's automatic showing (D6) all check it.
_MOMENT_ATTR = "_one_time_moment_this_session"
MOMENT_SUPPORT = "support_card"
MOMENT_TEMP_SETUP = "temperature_setup"


def moment_taken(widget: Any) -> Optional[str]:
    """Which one-time moment this session already showed, or None."""
    value = getattr(widget, _MOMENT_ATTR, None)
    return value if isinstance(value, str) else None


def take_moment(widget: Any, what: str) -> None:
    try:
        setattr(widget, _MOMENT_ATTR, what)
    except Exception:
        pass


def one_time_moment_blocked(widget: Any, what: str) -> Optional[str]:
    """Why ``what`` (one of the session's one-time moments) may not show now because of ANOTHER one:
    another moment already shown this session, or the first-run tip / "what's new" still owed or up."""
    taken = moment_taken(widget)
    if taken is not None and taken != what:
        return "another one-time moment was shown this session"
    notices = getattr(widget, "_startup_notices", None)
    owes = getattr(notices, "owes_moment", None)
    try:
        if callable(owes) and owes():
            return "the first-run tip or what's new is owed this session"
    except Exception:
        pass
    return None


def _seconds_since_sign_in() -> Optional[float]:
    try:
        from netspeedtray.core.shutdown_channel import session_seconds_since_logon
        return session_seconds_since_logon()
    except Exception:
        return None


def _screen_reader_running() -> bool:
    from netspeedtray.views.flyout import screen_reader_running
    return screen_reader_running()


def tip_body(i18n, config) -> str:
    """The first-run tip's three lines, its double-click line following the configured double-click
    action like the hover card's hint (combined review U7): "Double-click to open the Monitor." as
    written for the default; "Double-click: <action>" for another; no line when it does nothing."""
    body = str(i18n.FIRST_RUN_TIP_BODY)
    lines = body.split("\n")
    defaults = constants.config.defaults
    action = (config or {}).get("double_click_action", defaults.DEFAULT_DOUBLE_CLICK_ACTION)
    if len(lines) != 3 or action == defaults.CLICK_ACTION_OPEN_MONITOR:
        return body
    from netspeedtray.views.widget.main import _DOUBLE_CLICK_HINT_LABELS
    key = _DOUBLE_CLICK_HINT_LABELS.get(action)
    if key:
        lines[1] = i18n.WIDGET_HOVER_DOUBLE_CLICK_HINT.format(action=getattr(i18n, key))
    else:
        del lines[1]
    return "\n".join(lines)


def _mark_opens_browser(card) -> None:
    """The card's action opens a web page: Fluent's OpenInNewWindow glyph on its button (U7)."""
    btn = getattr(card, "_action_btn", None)
    if btn is None:
        return
    try:
        from PyQt6.QtCore import QSize
        from netspeedtray.constants.styles import styles as tokens
        from netspeedtray.utils import styles as su
        color = getattr(card, "_colors", {}).get("text_primary", "#000000")
        px = tokens.NOTICE_LINK_CHEVRON_PX
        btn.setIcon(su.fluent_icon(tokens.OPEN_IN_NEW_CODEPOINT, px, color))
        btn.setIconSize(QSize(px, px))
        btn.setLayoutDirection(Qt.LayoutDirection.LeftToRight if btn.isRightToLeft()
                               else Qt.LayoutDirection.RightToLeft)   # the glyph trails the words
    except Exception as e:
        logger.debug("Could not add the open-in-new glyph: %s", e)


def _modal_dialog_up(ignore=None) -> bool:
    """An app-modal dialog of ours is up (other than ``ignore``)."""
    try:
        from PyQt6.QtWidgets import QApplication
        modal = QApplication.activeModalWidget()
        return modal is not None and modal is not ignore
    except Exception:
        return False


def _alive(card) -> bool:
    if card is None:
        return False
    try:
        from PyQt6 import sip
        return not sip.isdeleted(card)
    except Exception:
        return True


class StartupNotices(QObject):
    """One per launch, owned by the widget. ``start()`` once the widget has settled."""

    def __init__(self, widget: Any, version: str = constants.app.VERSION) -> None:
        super().__init__(widget if isinstance(widget, QObject) else None)
        self._w = widget
        self._version = version
        self._pending: Optional[str] = None     # a notice still waiting for its moment
        self._flyout = None                     # the card while it is up (or stepped aside)
        self._flyout_notice: Optional[str] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.try_show)
        # C9: an app-modal dialog of ours opening takes the focus window - the shown card steps aside.
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.focusWindowChanged.connect(self._on_focus_window_changed)
        except Exception as e:
            logger.debug("Could not watch for modal dialogs: %s", e)

    # --- lifecycle ---------------------------------------------------------------------------

    def start(self) -> Optional[str]:
        """Decide what this launch owes, save the startup bookkeeping, and arm the delayed show.
        Returns the notice owed (for logging / tests)."""
        w = self._w
        config = w.config
        plan = logic.plan_startup_notice(config, self._version, has_string=self._has_string,
                                         prompts_allowed=not logic.tips_disabled_by_policy())
        self._save(plan.updates)
        self._pending = plan.notice
        if plan.notice:
            logger.info("One-time notice owed this launch: %s.", plan.notice)
            if plan.notice == logic.NOTICE_TIP and self._hover_card_open():
                # VM round 2: the pointer rested where the widget appeared, so the hover card opened
                # before this object existed and its gesture hook found nobody - the tip then showed
                # ~30 s later, after the user had seen what it teaches. That hover counts.
                self.on_gesture()
                return plan.notice
            self._timer.start(constants.app.STARTUP_NOTICE_DELAY_MS)
        return plan.notice

    def _hover_card_open(self) -> bool:
        hover = getattr(self._w, "_hover_card", None)
        try:
            return bool(_alive(hover) and hover.isVisible())
        except Exception:
            return False

    def on_widget_shown(self) -> None:
        """The widget became visible: a waiting notice (or a card that stepped aside) gets its moment
        STARTUP_NOTICE_DELAY_MS from now."""
        if self._pending or self._card_stepped_aside():
            self._timer.start(constants.app.STARTUP_NOTICE_DELAY_MS)

    def on_widget_hidden(self) -> None:
        """Never leave the card floating without its widget (a fullscreen app, a yield)."""
        self._step_aside()

    def on_widget_moved(self) -> None:
        """The widget moved (a drag, a re-dock): the visible card follows it."""
        card = self._live_card()
        if card is not None and card.isVisible():
            try:
                card.reanchor(self._w.frameGeometry())
            except Exception as e:
                logger.debug("Could not re-anchor the one-time notice: %s", e)

    def on_fullscreen(self) -> None:
        """A fullscreen app took over (the immediate-hide signal) - also with "Keep visible in
        fullscreen", when the widget itself stays: the card never stays over a game or a video."""
        if self._step_aside():
            self._timer.start(constants.app.STARTUP_NOTICE_RETRY_MS)

    def yield_to(self, other) -> None:
        """Another card of ours (an update offer, a notice, a data-cap alert) just showed: it wins.
        The tip steps aside and comes back STARTUP_NOTICE_DELAY_MS after the other one closes."""
        if self._live_card() is None and not self._pending:
            return
        self._step_aside()
        self._timer.stop()
        try:
            other.dismissed.connect(self._after_other_card)
        except Exception:
            self._timer.start(constants.app.STARTUP_NOTICE_RETRY_MS)

    def yield_to_window(self, window) -> None:
        """Integration 2.2.2 (#592 x #591): the user opened a window of ours from Start - D3's
        "NetSpeedTray is running" panel, which already carries the same gesture hint. It wins like
        another card: the tip / "what's new" steps aside (never lost) and comes back
        STARTUP_NOTICE_DELAY_MS after the window is gone (blocked_reason keeps it waiting meanwhile)."""
        if self._live_card() is None and not self._pending:
            return
        self._step_aside()
        self._timer.start(constants.app.STARTUP_NOTICE_RETRY_MS)   # a backstop if destroyed never comes
        try:
            window.destroyed.connect(self._after_other_card)
        except Exception:
            pass

    def _after_other_card(self) -> None:
        if self._pending or self._card_stepped_aside():
            self._timer.start(constants.app.STARTUP_NOTICE_DELAY_MS)

    def on_gesture(self) -> None:
        """The user did what the tip teaches (the hover card opened, a double-click, the right-click
        menu): the tip has done its job - close it, as answered. A tip still waiting to show is used up
        too (combined review C3): saved as shown, never shown afterwards."""
        self.answer_tip("the gesture it teaches")

    def answer_tip(self, how: str = "the user") -> None:
        """The first-run tip is answered (C3, C8: also the Monitor / Settings row of the "running"
        panel) - closed if it is up or stepped aside, used up if it is still waiting."""
        if self._pending == logic.NOTICE_TIP:
            self._pending = None
            self._timer.stop()
            self._save(logic.shown_updates(logic.NOTICE_TIP, self._w.config, self._version))
            logger.info("First-run tip answered before it showed (%s) - not shown.", how)
            return
        card = self._live_card()
        if card is not None and self._flyout_notice == logic.NOTICE_TIP:
            logger.info("First-run tip answered by %s.", how)
            card._dismiss()

    def owes_moment(self) -> bool:
        """The tip / "what's new" is still owed this session or on screen (stepped aside included)."""
        return bool(self._pending) or self._live_card() is not None

    def _on_focus_window_changed(self, *_args) -> None:
        QTimer.singleShot(0, self.check_modal)

    def check_modal(self) -> None:
        """C9: a shown card steps aside while an app-modal dialog of ours is up, and is re-homed by the
        retry once it is gone (blocked_reason)."""
        card = self._live_card()
        if card is None or not card.isVisible():
            return
        if _modal_dialog_up(card):
            logger.debug("One-time notice steps aside for a modal dialog.")
            card.hide()
            self._timer.start(constants.app.STARTUP_NOTICE_RETRY_MS)

    def tip_visible(self) -> bool:
        card = self._live_card()
        return card is not None and self._flyout_notice == logic.NOTICE_TIP and card.isVisible()

    # --- gates -------------------------------------------------------------------------------

    def blocked_reason(self) -> Optional[str]:
        """Why a notice may not show right now, or None. Each one is a 'not yet', never a 'never'."""
        w = self._w
        if not w.isVisible():
            return REASON_HIDDEN
        pm = getattr(w, "position_manager", None)
        try:
            if pm is not None and pm.is_yielding_to_apps() is True:
                return "widget stepped aside for app buttons"
        except Exception:
            pass
        if w._update_offer_blocked():
            return "fullscreen app, modal dialog, or nobody at the screen"
        if _modal_dialog_up():
            return "a modal dialog is up"
        since = _seconds_since_sign_in()
        if since is not None and 0 <= since < constants.app.SIGN_IN_DUPLICATE_WINDOW_S:
            return "the sign-in rush"
        settings = getattr(w, "settings_dialog", None)
        try:
            if _alive(settings) and settings.isVisible():
                return "Settings is open"
        except Exception:
            pass
        if self._other_card_up():
            return "another notice is up"
        hover = getattr(w, "_hover_card", None)          # C3: never beside (or over) the hover card
        try:
            if _alive(hover) and hover.isVisible():
                return "the hover card is up"
        except Exception:
            pass
        # Integration 2.2.2 (#592 x #591): never beside D3's running panel - the user is using it.
        panel = getattr(w, "_running_panel", None)
        try:
            if _alive(panel) and panel.isVisible():
                return "the running panel is open"
        except Exception:
            pass
        return None

    def _other_card_up(self) -> bool:
        for name in ("_update_flyout", "_notice_flyout", "_usage_alert_flyout"):
            card = getattr(self._w, name, None)
            try:
                if _alive(card) and card.isVisible():
                    return True
            except Exception:
                continue
        return False

    # --- show --------------------------------------------------------------------------------

    def try_show(self) -> None:
        w = self._w
        notice = self._pending
        rehome = self._card_stepped_aside()
        if notice is None and not rehome:
            return
        try:
            if w._app_is_quitting():
                self._pending = None
                return
            if logic.tips_disabled_by_policy():
                logger.info("One-time notice dropped: turned off by policy (DisableTips).")
                self._pending = None
                card = self._live_card()
                self._flyout = None
                if card is not None:
                    card.close()
                return
            reason = self.blocked_reason()
            if reason:
                logger.debug("One-time notice waits: %s.", reason)
                if reason != REASON_HIDDEN:            # a hidden widget re-arms it on show
                    self._timer.start(constants.app.STARTUP_NOTICE_RETRY_MS)
                return
            if rehome:
                self._show_card(self._live_card(), self._flyout_notice)
                return
            taken = moment_taken(w)
            if taken is not None:
                # C7: the session's one-time moment went to another surface - owed next launch.
                logger.info("One-time notice waits for the next launch: %s showed this session.", taken)
                self._pending = None
                return
            card = self._build(notice)
            self._pending = None
            self._flyout, self._flyout_notice = card, notice
            card.dismissed.connect(self._on_card_closed)
            self._show_card(card, notice)
            take_moment(w, notice)
            # Once ever: saved the moment it really showed (never before - a failed show keeps it owed).
            self._save(logic.shown_updates(notice, w.config, self._version))
            logger.info("One-time notice shown: %s.", notice)
        except Exception as e:
            logger.error("Error showing the one-time notice: %s", e, exc_info=True)

    def _show_card(self, card, notice: Optional[str]) -> None:
        w = self._w
        geo = w.frameGeometry()
        screen = None
        try:
            screen = w.screen()
        except Exception:
            pass
        if screen is None:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else geo
        edge = None
        edge_of = getattr(w, "_hover_card_edge", None)
        if callable(edge_of):
            try:
                edge = edge_of()
            except Exception:
                edge = None
        card.show_for(geo, avail, edge=edge, screen=screen, tail=(notice == logic.NOTICE_TIP),
                      activate=_screen_reader_running())

    def _build(self, notice: str):
        from netspeedtray.views.flyout import Flyout
        i18n = self._w.i18n
        if notice == logic.NOTICE_TIP:
            card = Flyout(i18n.FIRST_RUN_TIP_TITLE, tip_body(i18n, getattr(self._w, "config", None) or {}),
                          action_text=i18n.CLICK_ACTION_OPEN_MONITOR_LABEL,
                          secondary_text=i18n.WELCOME_2_0_GOT_IT_BUTTON,
                          close_text=i18n.CLOSE_BUTTON, auto_dismiss_ms=0, focusable=True)
            # Deferred: the card closes (and deletes itself) right after action_clicked returns.
            card.action_clicked.connect(lambda: QTimer.singleShot(0, self._open_monitor))
            return card
        version = logic.display_version(self._version)
        # Both buttons neutral: news is not a call to action (the calmer of the two review options).
        card = Flyout(i18n.WHATS_NEW_TITLE.format(version=version),
                      getattr(i18n, logic.whats_new_key(self._version)),
                      action_text=i18n.WELCOME_2_0_WHATS_NEW_BUTTON,
                      secondary_text=i18n.WELCOME_2_0_GOT_IT_BUTTON,
                      close_text=i18n.CLOSE_BUTTON, auto_dismiss_ms=0, focusable=True,
                      action_style="neutral")
        _mark_opens_browser(card)
        card.action_clicked.connect(self._open_release_page)
        return card

    def _open_monitor(self) -> None:
        try:
            self._w.open_monitor_window()
        except Exception as e:
            logger.error("Could not open the Monitor from the tip: %s", e)

    def _open_release_page(self) -> None:
        try:
            from netspeedtray.utils.helpers import open_external
            open_external(logic.release_page_url(self._version))
        except Exception as e:
            logger.error("Could not open the release page: %s", e)

    def _on_card_closed(self) -> None:
        self._flyout, self._flyout_notice = None, None

    # --- helpers -----------------------------------------------------------------------------

    def _step_aside(self) -> bool:
        """Hide a visible card (kept, to come back). True when there is a card or a notice waiting."""
        card = self._live_card()
        if card is not None and card.isVisible():
            card.hide()
        return card is not None or bool(self._pending)

    def _live_card(self):
        card = self._flyout
        if card is None:
            return None
        if not _alive(card):
            self._flyout = None
            return None
        return card

    def _card_stepped_aside(self) -> bool:
        card = self._live_card()
        return card is not None and not card.isVisible()

    def _has_string(self, key: str) -> bool:
        has = getattr(self._w.i18n, "has_key", None)
        return bool(has(key)) if callable(has) else False

    def _save(self, updates: dict) -> None:
        w = self._w
        changed = {k: v for k, v in (updates or {}).items() if w.config.get(k, object()) != v}
        if not changed:
            return
        w.config.update(changed)
        controller = getattr(w, "config_controller", None)
        if controller is not None:
            controller.update_config(changed, apply_and_repaint=False)   # bookkeeping: no repaint
        else:
            w.update_config(changed)
