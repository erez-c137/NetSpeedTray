"""
The first-run tip and the one-time "what's new" on screen (2.2.2 D1, D2). The decision is
core/startup_notices.plan_startup_notice; this owns the timing and the card.

- Shown STARTUP_NOTICE_DELAY_MS after the widget is really visible, never in the instant it appears.
- Deferred (retried every STARTUP_NOTICE_RETRY_MS, and again whenever the widget comes back) while the
  widget is hidden or stepped aside for app buttons, behind the update offer's gates
  (``_update_offer_blocked``: a fullscreen app, a modal dialog, a locked / idle / away session), during
  the sign-in rush (the first SIGN_IN_DUPLICATE_WINDOW_S of a session), and while another card of ours
  is up. A deferred notice is owed again next launch if this one never got to show it.
- Marked shown (saved) the moment it shows - once ever. It does not time out: it stays beside the
  widget until answered, hides with the widget (a fullscreen app) and comes back with it.
- The IT policy (DisableTips) is re-read at show time, so a policy pushed while it waits drops it.
- Focusable (a click lets the keyboard in: Tab / Enter / Esc) but shown without activating, so it
  never takes the keyboard from the app the user is typing in.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from PyQt6.QtCore import QObject, QPoint, QTimer

from netspeedtray import constants
from netspeedtray.core import startup_notices as logic

logger = logging.getLogger("NetSpeedTray.StartupNotices")


def _seconds_since_sign_in() -> Optional[float]:
    try:
        from netspeedtray.core.shutdown_channel import session_seconds_since_logon
        return session_seconds_since_logon()
    except Exception:
        return None


class StartupNotices(QObject):
    """One per launch, owned by the widget. ``start()`` once the widget has settled."""

    def __init__(self, widget: Any, version: str = constants.app.VERSION) -> None:
        super().__init__(widget if isinstance(widget, QObject) else None)
        self._w = widget
        self._version = version
        self._pending: Optional[str] = None     # a notice still waiting for its moment
        self._flyout = None                     # the card while it is up (or hidden with the widget)
        self._flyout_notice: Optional[str] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.try_show)

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
            self._timer.start(constants.app.STARTUP_NOTICE_DELAY_MS)
        return plan.notice

    def on_widget_shown(self) -> None:
        """The widget became visible: a waiting notice (or a card hidden with the widget) gets its
        moment STARTUP_NOTICE_DELAY_MS from now."""
        if self._pending or self._card_hidden_with_widget():
            self._timer.start(constants.app.STARTUP_NOTICE_DELAY_MS)

    def on_widget_hidden(self) -> None:
        """Never leave the card floating without its widget (a fullscreen app, a yield)."""
        card = self._live_card()
        if card is not None and card.isVisible():
            card.hide()

    # --- gates -------------------------------------------------------------------------------

    def blocked_reason(self) -> Optional[str]:
        """Why a notice may not show right now, or None. Each one is a 'not yet', never a 'never'."""
        w = self._w
        if not w.isVisible():
            return "widget hidden"
        pm = getattr(w, "position_manager", None)
        try:
            if pm is not None and pm.is_yielding_to_apps() is True:
                return "widget stepped aside for app buttons"
        except Exception:
            pass
        if w._update_offer_blocked():
            return "fullscreen app, modal dialog, or nobody at the screen"
        since = _seconds_since_sign_in()
        if since is not None and 0 <= since < constants.app.SIGN_IN_DUPLICATE_WINDOW_S:
            return "the sign-in rush"
        if self._other_card_up():
            return "another notice is up"
        return None

    def _other_card_up(self) -> bool:
        from PyQt6 import sip
        for name in ("_update_flyout", "_notice_flyout", "_usage_alert_flyout"):
            card = getattr(self._w, name, None)
            try:
                if card is not None and not sip.isdeleted(card) and card.isVisible():
                    return True
            except Exception:
                continue
        return False

    # --- show --------------------------------------------------------------------------------

    def try_show(self) -> None:
        w = self._w
        notice = self._pending
        rehome = self._card_hidden_with_widget()
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
                self._timer.start(constants.app.STARTUP_NOTICE_RETRY_MS)
                return
            if rehome:
                self._show_card(self._live_card())
                return
            card = self._build(notice)
            self._pending = None
            self._flyout, self._flyout_notice = card, notice
            card.dismissed.connect(self._on_card_closed)
            self._show_card(card)
            # Once ever: saved the moment it really showed (never before - a failed show keeps it owed).
            self._save(logic.shown_updates(notice, w.config, self._version))
            logger.info("One-time notice shown: %s.", notice)
        except Exception as e:
            logger.error("Error showing the one-time notice: %s", e, exc_info=True)

    def _show_card(self, card) -> None:
        geo = self._w.frameGeometry()
        card.show_at(QPoint(geo.left(), geo.top()))

    def _build(self, notice: str):
        from netspeedtray.views.flyout import Flyout
        i18n = self._w.i18n
        if notice == logic.NOTICE_TIP:
            card = Flyout(i18n.FIRST_RUN_TIP_TITLE, i18n.FIRST_RUN_TIP_BODY,
                          action_text=i18n.FIRST_RUN_TIP_OPEN_MONITOR_BUTTON,
                          secondary_text=i18n.WELCOME_2_0_GOT_IT_BUTTON,
                          close_text=i18n.CLOSE_BUTTON, auto_dismiss_ms=0, focusable=True)
            # Deferred: the card closes (and deletes itself) right after action_clicked returns.
            card.action_clicked.connect(lambda: QTimer.singleShot(0, self._open_monitor))
            return card
        version = logic.display_version(self._version)
        card = Flyout(i18n.WHATS_NEW_TITLE.format(version=version),
                      getattr(i18n, logic.whats_new_key(self._version)),
                      action_text=i18n.WELCOME_2_0_WHATS_NEW_BUTTON,
                      secondary_text=i18n.WHATS_NEW_DISMISS_BUTTON,
                      close_text=i18n.CLOSE_BUTTON, auto_dismiss_ms=0, focusable=True)
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

    def _live_card(self):
        card = self._flyout
        if card is None:
            return None
        try:
            from PyQt6 import sip
            if sip.isdeleted(card):
                self._flyout = None
                return None
        except Exception:
            pass
        return card

    def _card_hidden_with_widget(self) -> bool:
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
