"""
Temperature setup dialog - how to get CPU/GPU temperatures with LibreHardwareMonitor (LHM).

Two numbered steps (2.2.2 D6): 1) install LHM and run it as administrator, 2) turn on its Options ->
Remote Web Server. LHM v0.9.5+ is read over that web server (#187), which is off by default - the old
explainer said temperatures "appear automatically" once LHM runs, so anyone with a current LHM got
blank readings. Version notes and the firewall advice sit behind "More details".

Opened from two places:
- the widget, once per session, when temperatures / power are on but no sensor reads (gated upstream:
  StatsMonitorThread._update_lhm_notice; "Don't remind me again" -> config temp_onboarding_dismissed).
  With LHM already running but its web server off, it opens at step 2.
- Settings > Hardware: the temperature note's "How to set it up" (step 1) and the "LHM is running, but
  its web server is off" notice's "How to turn it on" (step 2). No reminder checkbox there.

NetSpeedTray itself never runs as admin and never ships a driver; the only link opens LHM's official
GitHub releases page in the user's browser - the app sends nothing anywhere.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.dwm import apply_win11_chrome

# LibreHardwareMonitor v0.9.5+ dropped its WMI provider (the ".NET 10 build" change, Jan 2026);
# NetSpeedTray reads those versions over LHM's web server instead (#187), and still reads v0.9.4
# over WMI. So the link points at the current release. Opened in the user's browser.
LHM_RELEASES_URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"

tokens = constants.styles

# Step badge: a filled circle with the step number (or a check mark once the step is done).
_BADGE_PX = 24


def _palette() -> dict:
    """The dialog's colours: the app's Light/Dark tokens, or Windows High Contrast's own while it is on
    (text, background, and the highlight colour for the badges)."""
    from netspeedtray.utils.components import notice_link_colors
    from netspeedtray.utils.taskbar_appearance import read_high_contrast_link_colors
    c = su.semantic_colors()
    hc = read_high_contrast_link_colors()
    if hc:
        return {"bg": hc["background"], "text": hc["text"], "muted": hc["text"], "badge": hc["focus"],
                "stroke": hc["text"], "current_bg": hc["background"], "button_bg": hc["background"],
                "hc": True}
    bg = c["card_bg"]
    return {"bg": bg, "text": c["text_primary"], "muted": c["text_secondary"],
            "badge": notice_link_colors(bg)["link"], "stroke": c["card_stroke"],
            "current_bg": c["subtle_fill"], "button_bg": c["subtle_fill"], "hc": False}


def _on_color(fill_hex: str) -> str:
    """Black or white, whichever reads better on ``fill_hex`` (the number inside a badge)."""
    from netspeedtray.utils.color_scheme import contrast_ratio
    return "#ffffff" if contrast_ratio("#ffffff", fill_hex) >= contrast_ratio("#000000", fill_hex) else "#000000"


class _StepBadge(QWidget):
    """The round step number. Painted (no glyph lookup), so the check mark needs no font that has it."""

    def __init__(self, number: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.number = number
        self.done = False
        self._fill = QColor("#0067c0")
        self.setFixedSize(_BADGE_PX, _BADGE_PX)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_fill(self, color: str) -> None:
        self._fill = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._fill)
        p.drawEllipse(QRectF(0.5, 0.5, _BADGE_PX - 1, _BADGE_PX - 1))
        ink = QColor(_on_color(self._fill.name()))
        if self.done:
            pen = QPen(ink)
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            s = _BADGE_PX / 24.0
            path.moveTo(7 * s, 12.5 * s)
            path.lineTo(10.5 * s, 16 * s)
            path.lineTo(17 * s, 8.5 * s)
            p.drawPath(path)
        else:
            f = su.font(tokens.TYPE_BODY_STRONG)
            p.setFont(f)
            p.setPen(ink)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.number))


class _Step(QFrame):
    """One numbered step: badge + text (+ an optional action under it). ``current`` is the step the user
    is on (a subtle filled row); ``done`` shows a check mark in the badge."""

    def __init__(self, number: int, text: str, action: Optional[QWidget] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("tempStep")
        self.number = number
        self.done = False
        self.current = False
        self._text = text
        row = QHBoxLayout(self)
        row.setContentsMargins(tokens.SPACE_S, tokens.SPACE_S, tokens.SPACE_S, tokens.SPACE_S)
        row.setSpacing(tokens.SPACE_M)
        self.badge = _StepBadge(number, self)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 2, 0, 0)
        col.setSpacing(tokens.SPACE_XS)
        self.label = su.ui_label(text)   # "Options → Remote Web Server": no arrow in Nirmala UI
        self.label.setWordWrap(True)
        self.label.setFont(su.font(tokens.TYPE_BODY))
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        col.addWidget(self.label)
        if action is not None:
            col.addWidget(action, 0, Qt.AlignmentFlag.AlignLeading)
        row.addLayout(col, 1)
        # Narrator reads a step as "1. Install ..." - the number is painted, so it rides in the name.
        self.setAccessibleName(f"{number}. {text}")

    def text(self) -> str:
        return self._text

    def set_state(self, *, current: bool, done: bool) -> None:
        self.current, self.done = current, done
        self.badge.done = done
        self.badge.update()


class TempOnboardingDialog(QDialog):
    """A calm, two-step explainer for getting temperatures from LibreHardwareMonitor."""

    ACTION_DISMISS = "dismiss"
    ACTION_GET_LHM = "get_lhm"

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass

    def __init__(self, i18n, parent: Optional[QWidget] = None,
                 lhm_port: int = constants.lhm.HTTP_PORT, focus_step: int = 1,
                 show_remind: bool = True, open_url: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(parent)
        from netspeedtray.utils.components import NoticeLink, SettingExpander
        self.i18n = i18n
        self.action = self.ACTION_DISMISS  # default if closed via Close / X / Esc
        self._open_url = open_url
        focus_step = 2 if focus_step == 2 else 1
        c = _palette()
        self._colors = c

        self.setWindowTitle(i18n.TEMP_ONBOARDING_WINDOW_TITLE)
        self.setStyleSheet(
            f"QDialog {{ background: {c['bg']}; }}"
            f" QLabel {{ color: {c['text']}; background: transparent; }}"
            f" QCheckBox {{ color: {c['text']}; }}"
            f" QFrame#tempStep {{ background: transparent; border: 1px solid transparent;"
            f" border-radius: {tokens.RADIUS_CARD}px; }}"
            f" QFrame#tempStep[current=\"true\"] {{ background: {c['current_bg']};"
            f" border: 1px solid {c['stroke']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_L, tokens.SPACE_L, tokens.SPACE_L, tokens.SPACE_M)
        layout.setSpacing(tokens.SPACE_M)

        title = QLabel(i18n.TEMP_ONBOARDING_TITLE, self)
        title.setFont(su.font(tokens.TYPE_SUBTITLE))
        title.setWordWrap(True)
        layout.addWidget(title)

        self.intro = QLabel(i18n.TEMP_ONBOARDING_INTRO, self)
        self.intro.setWordWrap(True)
        self.intro.setMinimumWidth(440)
        self.intro.setStyleSheet(f"color: {c['muted']}; background: transparent;")
        layout.addWidget(self.intro)

        # Step 1's action: LHM's official releases page. The dialog stays open - step 2 is still to do.
        self.get_lhm_link = NoticeLink("", i18n.TEMP_ONBOARDING_GET_LHM_BUTTON, self._get_lhm,
                                       font_token=tokens.TYPE_BODY, surface=lambda: self._step_surface(1))
        step1 = _Step(1, i18n.TEMP_ONBOARDING_STEP_1, self.get_lhm_link, self)
        step2 = _Step(2, i18n.TEMP_ONBOARDING_STEP_2.format(port=lhm_port), None, self)
        self.steps = [step1, step2]
        self._focus_step = focus_step
        steps_col = QVBoxLayout()
        steps_col.setSpacing(tokens.SPACE_XS)
        for step in self.steps:
            # Opened at step 2: LHM was seen running, so step 1 is done.
            step.set_state(current=(step.number == focus_step), done=(step.number < focus_step))
            step.setProperty("current", "true" if step.current else "false")
            step.badge.set_fill(c["badge"] if step.current or step.done else c["muted"])
            steps_col.addWidget(step)
        layout.addLayout(steps_col)
        self.get_lhm_link.refresh_theme()

        # Version notes and the firewall advice - for the few who need them.
        self.details = SettingExpander(i18n.TEMP_ONBOARDING_MORE_DETAILS, expanded=False, i18n=i18n)
        self.details_label = su.ui_label(i18n.TEMP_ONBOARDING_DETAILS.format(port=lhm_port))
        self.details_label.setWordWrap(True)
        self.details_label.setFont(su.font(tokens.TYPE_CAPTION))
        self.details_label.setStyleSheet(f"color: {c['muted']}; background: transparent;")
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.contentLayout().addWidget(self.details_label)
        self.details.expandedChanged.connect(lambda _on: self.adjustSize())
        layout.addWidget(self.details)

        foot = QHBoxLayout()
        self.dont_remind = QCheckBox(i18n.TEMP_ONBOARDING_DONT_REMIND, self)
        self.dont_remind.setVisible(bool(show_remind))
        foot.addWidget(self.dont_remind)
        foot.addStretch(1)
        self.close_button = QPushButton(getattr(i18n, "CLOSE_BUTTON", "Close"), self)
        self.close_button.setDefault(True)
        self.close_button.setMinimumWidth(96)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # The Support dialog's footer button (same surface tokens); High Contrast: the theme's colours.
        self.close_button.setStyleSheet(
            f"QPushButton {{ padding: 6px 20px; border-radius: {tokens.RADIUS_CONTROL}px;"
            f" background: {c['button_bg']}; color: {c['text']}; border: 1px solid {c['stroke']}; }}"
            f" QPushButton:hover, QPushButton:focus {{ border: {tokens.FOCUS_RING_WIDTH}px solid {c['badge']}; }}")
        self.close_button.clicked.connect(lambda: self._choose(self.action))
        foot.addWidget(self.close_button)
        layout.addLayout(foot)

        # Keyboard: focus starts on Close (the default button, as in any Windows dialog - a ring on the
        # link would look pre-selected to a mouse user); Tab order is the reading order: step 1's link,
        # More details, the reminder box, Close.
        self.close_button.setFocus()

    def _step_surface(self, number: int) -> str:
        """What a step's link sits on (NoticeLink measures its contrast against it)."""
        c = self._colors
        step = next((s for s in getattr(self, "steps", []) if s.number == number), None)
        current = step.current if step is not None else number == getattr(self, "_focus_step", 1)
        return c["current_bg"] if current and not c["hc"] else c["bg"]

    def _get_lhm(self) -> None:
        self.action = self.ACTION_GET_LHM
        opener = self._open_url
        if opener is None:
            from netspeedtray.utils.helpers import open_external
            opener = open_external
        opener(LHM_RELEASES_URL)

    def dismissed_forever(self) -> bool:
        """"Don't remind me again" - only where it is offered (not when opened from Settings)."""
        return not self.dont_remind.isHidden() and self.dont_remind.isChecked()

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()
