"""
Temperature setup dialog - how to get CPU/GPU temperatures with LibreHardwareMonitor (LHM).

Two numbered steps (2.2.2 D6): 1) download and open LHM (UAC: Yes; its PawnIO sensor-driver offer:
OK), 2) turn on its web server - Options → Remote Web Server → Run (plus Interface / Port when
NetSpeedTray is set to another port than LHM's default). LHM v0.9.5+ is read over that web server
(#187), which is off by default - the old explainer said temperatures "appear automatically" once LHM
runs, so anyone with a current LHM got blank readings. The menu names are LHM's own (its MainForm
designer: Options > Remote Web Server > Run / Interface / Port / Authentication, Options > Run On
Windows Startup; the PawnIO prompt is an OK/Cancel message box). Version notes, the restart tip and the
firewall advice sit behind "More details".

The steps follow LHM live while the dialog is open (``status_provider``, polled every
``constants.lhm.STATUS_REFRESH_MS``): LHM running with its server off ticks step 1 and highlights step 2;
connected ticks both and says "Temperatures are on.".

Opened from two places:
- the widget, once EVER, when temperatures / power are on but no sensor reads (gated upstream:
  StatsMonitorThread._update_lhm_notice; then the startup cards' gates, widget._on_lhm_not_detected).
  Showing it uses it up (config temp_onboarding_dismissed), so no reminder checkbox (combined review
  C11; ``show_remind`` stays for a caller that wants one).
- Settings > Hardware: the temperature note's "How to set it up" (step 1) and the "Remote Web Server is
  off" notice's "How to turn it on" (step 2). No reminder checkbox there.

NetSpeedTray itself never runs as admin and never ships a driver; the only link opens LHM's official
GitHub releases page in the user's browser - the app sends nothing anywhere.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QCoreApplication, QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStyle,
    QStyleOptionButton, QVBoxLayout, QWidget,
)

from netspeedtray import constants
from netspeedtray.utils import styles as su
from netspeedtray.utils.dwm import apply_win11_chrome

# LibreHardwareMonitor v0.9.5+ dropped its WMI provider (the ".NET 10 build" change, Jan 2026);
# NetSpeedTray reads those versions over LHM's web server instead (#187), and still reads v0.9.4
# over WMI. So the link points at the current release. Opened in the user's browser.
LHM_RELEASES_URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest"

tokens = constants.styles

_BADGE_PX = 24          # the round step number
_CHECK_BOX_PX = 16      # the reminder check box's square
_DETAILS_HEADER_PX = 40  # a compact "More details" header (Settings rows are 58)


def _palette() -> dict:
    """The dialog's colours: the app's Light/Dark tokens, or Windows High Contrast's own while it is on."""
    from netspeedtray.utils.components import notice_link_colors
    from netspeedtray.utils.taskbar_appearance import read_high_contrast_link_colors
    c = su.semantic_colors()
    hc = read_high_contrast_link_colors()
    if hc:
        return {"bg": hc["background"], "text": hc["text"], "muted": hc["text"], "badge": hc["focus"],
                "accent": hc["focus"], "stroke": hc["text"], "current_bg": hc["background"],
                "hover": hc["background"], "box_border": hc["text"], "hc": True}
    bg = c["card_bg"]
    return {"bg": bg, "text": c["text_primary"], "muted": c["text_secondary"],
            "badge": notice_link_colors(bg)["link"], "accent": c["accent"], "stroke": c["card_stroke"],
            "current_bg": c["subtle_fill"], "hover": c["subtle_fill"], "box_border": c["text_secondary"],
            "hc": False}


def _on_color(fill_hex: str) -> str:
    """Black or white, whichever reads better on ``fill_hex``."""
    from netspeedtray.utils.color_scheme import contrast_ratio
    return "#ffffff" if contrast_ratio("#ffffff", fill_hex) >= contrast_ratio("#000000", fill_hex) else "#000000"


def _check_path(rect: QRectF) -> QPainterPath:
    """A check mark inside ``rect`` (painted: no glyph lookup, so no font has to have it)."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QPainterPath()
    path.moveTo(x + w * 0.27, y + h * 0.52)
    path.lineTo(x + w * 0.44, y + h * 0.68)
    path.lineTo(x + w * 0.73, y + h * 0.35)
    return path


def _keyboard_focus(widget, reason) -> bool:
    """Focus that came from the keyboard. Qt also sends a Tab-reason focus-in while a window is being
    shown (its first focus-chain pass, before it is active) - that is no key press, so no ring."""
    return (reason in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                       Qt.FocusReason.ShortcutFocusReason)
            and widget.window().isActiveWindow())


class _StepBadge(QWidget):
    """The round step number, or a check mark once the step is done."""

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
            p.drawPath(_check_path(QRectF(0, 0, _BADGE_PX, _BADGE_PX)))
        else:
            p.setFont(su.font(tokens.TYPE_BODY_STRONG))
            p.setPen(ink)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.number))


class _Step(QFrame):
    """One numbered step: badge, a short title, a description (and an optional action under it).
    ``current`` is the step the user is on (a subtle filled row); ``done`` puts a check in the badge."""

    def __init__(self, number: int, title: str, description: str, action: Optional[QWidget] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("tempStep")
        self.number = number
        self.done = False
        self.current = False
        self._title, self._description = title, description
        row = QHBoxLayout(self)
        row.setContentsMargins(tokens.SPACE_S, tokens.SPACE_S, tokens.SPACE_S, tokens.SPACE_S)
        row.setSpacing(tokens.SPACE_M)
        self.badge = _StepBadge(number, self)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 2, 0, 0)
        col.setSpacing(2)
        self.title_label = su.ui_label(title)
        self.title_label.setObjectName("tempStepTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setFont(su.font(tokens.TYPE_BODY_STRONG))
        col.addWidget(self.title_label)
        self.label = su.ui_label(description)   # "Options → Remote Web Server": no arrow in Nirmala UI
        self.label.setWordWrap(True)
        self.label.setFont(su.font(tokens.TYPE_BODY))
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        col.addWidget(self.label)
        if action is not None:
            col.addSpacing(tokens.SPACE_XS)
            # Full width (no alignment flag): the stacked link's button then starts exactly where the
            # text above starts - an aligned NoticeLink was laid out a few px in (D6 review).
            col.addWidget(action)
        row.addLayout(col, 1)
        # Narrator reads a step as "1. Download and open ... Unzip ..." - the number is painted.
        self.setAccessibleName(f"{number}. {title}")
        self.setAccessibleDescription(description)

    def title(self) -> str:
        return self._title

    def description(self) -> str:
        return self._description

    def text(self) -> str:
        return f"{self._title} {self._description}"

    def set_state(self, *, current: bool, done: bool) -> None:
        self.current, self.done = current, done
        self.badge.done = done
        self.badge.update()
        self.setProperty("current", "true" if current else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class _DialogButton(QPushButton):
    """The footer button: a Win11 standard button whose focus ring shows only for keyboard focus (a
    mouse click or the dialog opening leaves none), and which can turn into the accent button."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("dialogButton")
        self.setProperty("kbfocus", "false")
        self.setProperty("accent", "false")

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_accent(self, on: bool) -> None:
        value = "true" if on else "false"
        if self.property("accent") != value:
            self.setProperty("accent", value)
            self._repolish()

    def focusInEvent(self, event) -> None:
        self.setProperty("kbfocus", "true" if _keyboard_focus(self, event.reason()) else "false")
        self._repolish()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.setProperty("kbfocus", "false")
        self._repolish()
        super().focusOutEvent(event)


class _CheckBox(QCheckBox):
    """A Win11 check box drawn by hand, so it follows the dialog's colours in every theme: unchecked =
    transparent square with a 1 px border, checked = accent square with a check, a ring only for
    keyboard focus. Text and indicator positions come from the style (mirrored in RTL)."""

    def __init__(self, text: str, colors: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._c = colors
        self._kb_focus = False
        self.setMinimumHeight(_CHECK_BOX_PX + 4)

    def focusInEvent(self, event) -> None:
        self._kb_focus = _keyboard_focus(self, event.reason())
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._kb_focus = False
        super().focusOutEvent(event)

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(hint.width() + _CHECK_BOX_PX)
        return hint

    def paintEvent(self, event) -> None:
        c = self._c
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        rtl = self.isRightToLeft()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        y = (self.height() - _CHECK_BOX_PX) / 2.0
        x = self.width() - _CHECK_BOX_PX - 1.0 if rtl else 1.0
        box = QRectF(x + 0.5, y + 0.5, _CHECK_BOX_PX - 1, _CHECK_BOX_PX - 1)
        if self.isChecked():
            p.setPen(QPen(QColor(c["accent"]), 1))
            p.setBrush(QColor(c["accent"]))
            p.drawRoundedRect(box, 3, 3)
            pen = QPen(QColor(_on_color(QColor(c["accent"]).name())))
            pen.setWidthF(1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(_check_path(box))
        else:
            p.setPen(QPen(QColor(c["box_border"]), 1))
            p.setBrush(QColor(c["hover"]) if self.underMouse() and not c["hc"] else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(box, 3, 3)
        gap = tokens.SPACE_S
        text_rect = self.rect().adjusted(0 if rtl else _CHECK_BOX_PX + gap, 0,
                                         -(_CHECK_BOX_PX + gap) if rtl else 0, 0)
        p.setPen(QColor(c["text"]))
        p.setFont(self.font())
        align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft)
        p.drawText(text_rect, int(align.value), self.text())
        if self.hasFocus() and self._kb_focus:
            ring = QPen(QColor(c["text"]), tokens.FOCUS_RING_WIDTH)
            p.setPen(ring)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)


class TempOnboardingDialog(QDialog):
    """A calm, two-step explainer for getting temperatures from LibreHardwareMonitor."""

    ACTION_DISMISS = "dismiss"
    ACTION_GET_LHM = "get_lhm"

    def __init__(self, i18n, parent: Optional[QWidget] = None,
                 lhm_port: int = constants.lhm.HTTP_PORT, focus_step: int = 1,
                 show_remind: bool = True, open_url: Optional[Callable[[str], None]] = None,
                 status_provider: Optional[Callable[[], Optional[str]]] = None) -> None:
        super().__init__(parent)
        from netspeedtray.utils.components import NoticeLink, SettingExpander
        self.i18n = i18n
        self.action = self.ACTION_DISMISS  # default if closed via Close / X / Esc
        self._open_url = open_url
        self._status_provider = status_provider
        self._initial_step = 2 if focus_step == 2 else 1
        self.status: Optional[str] = None
        c = _palette()
        self._colors = c

        self.setWindowTitle(i18n.TEMP_ONBOARDING_WINDOW_TITLE)
        on_accent = _on_color(QColor(c["accent"]).name())
        self.setStyleSheet(
            f"QDialog {{ background: {c['bg']}; }}"
            f" QLabel {{ color: {c['text']}; background: transparent; }}"
            f" QFrame#tempStep {{ background: transparent; border: 1px solid transparent;"
            f" border-radius: {tokens.RADIUS_CARD}px; }}"
            f" QFrame#tempStep[current=\"true\"] {{ background: {c['current_bg']};"
            f" border: 1px solid {c['stroke']}; }}"
            f" QPushButton#dialogButton {{ padding: 6px 20px; border-radius: {tokens.RADIUS_CONTROL}px;"
            f" background: {c['bg']}; color: {c['text']}; border: 1px solid {c['stroke']}; }}"
            f" QPushButton#dialogButton:hover {{ background: {c['hover']}; }}"
            f" QPushButton#dialogButton[accent=\"true\"] {{ background: {c['accent']}; color: {on_accent};"
            f" border: 1px solid {c['accent']}; }}"
            f" QPushButton#dialogButton[kbfocus=\"true\"] {{ border: {tokens.FOCUS_RING_WIDTH}px solid {c['text']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(tokens.SPACE_L, tokens.SPACE_L, tokens.SPACE_L, tokens.SPACE_M)
        layout.setSpacing(tokens.SPACE_M)

        title = su.ui_label(i18n.TEMP_ONBOARDING_TITLE, self)
        title.setFont(su.font(tokens.TYPE_SUBTITLE))
        title.setWordWrap(True)
        layout.addWidget(title)

        self.intro = su.ui_label(i18n.TEMP_ONBOARDING_INTRO, self)
        self.intro.setWordWrap(True)
        self.intro.setMinimumWidth(460)
        self.intro.setStyleSheet(f"color: {c['muted']}; background: transparent;")
        layout.addWidget(self.intro)

        # Step 1's action: LHM's official releases page. The dialog stays open - step 2 is still to do.
        self.get_lhm_link = NoticeLink("", i18n.TEMP_ONBOARDING_GET_LHM_BUTTON, self._get_lhm, external=True,
                                       stacked=True, font_token=tokens.TYPE_BODY, surface=c["bg"])
        step2_text = i18n.TEMP_ONBOARDING_STEP_2_DESC
        if int(lhm_port) != constants.lhm.HTTP_PORT:   # LHM listens on 8085 until told otherwise
            step2_text = f"{step2_text} {i18n.TEMP_ONBOARDING_STEP_2_PORT.format(port=lhm_port)}"
        step1 = _Step(1, i18n.TEMP_ONBOARDING_STEP_1, i18n.TEMP_ONBOARDING_STEP_1_DESC, self.get_lhm_link, self)
        step2 = _Step(2, i18n.TEMP_ONBOARDING_STEP_2, step2_text, None, self)
        self.steps = [step1, step2]
        steps_col = QVBoxLayout()
        steps_col.setSpacing(tokens.SPACE_XS)
        for step in self.steps:
            steps_col.addWidget(step)
        layout.addLayout(steps_col)

        self.connected_label = su.ui_label(i18n.TEMP_ONBOARDING_CONNECTED, self)
        self.connected_label.setFont(su.font(tokens.TYPE_BODY_STRONG))
        self.connected_label.setVisible(False)
        layout.addWidget(self.connected_label)

        # The restart tip, version notes and the firewall advice - for the few who need them.
        self.details = SettingExpander(i18n.TEMP_ONBOARDING_MORE_DETAILS, expanded=False, i18n=i18n)
        self._compact_expander(self.details, c)
        lines = [ln.strip() for ln in i18n.TEMP_ONBOARDING_DETAILS.format(port=lhm_port).split("\n") if ln.strip()]
        lines.append(i18n.TEMP_ONBOARDING_UNSIGNED)   # proposed to the owner: removable on its own
        self.details_lines = []
        for line in lines:
            self.details.contentLayout().addLayout(self._bullet(line, c))
        self.details.expandedChanged.connect(lambda _on: self._fit_height())
        layout.addWidget(self.details)

        foot = QHBoxLayout()
        self.dont_remind = _CheckBox(i18n.TEMP_ONBOARDING_DONT_REMIND, c, self)
        self.dont_remind.setVisible(bool(show_remind))
        foot.addWidget(self.dont_remind)
        foot.addStretch(1)
        self.close_button = _DialogButton(getattr(i18n, "CLOSE_BUTTON", "Close"), self)
        self.close_button.setDefault(True)
        self.close_button.setMinimumWidth(96)
        self.close_button.clicked.connect(lambda: self._choose(self.action))
        foot.addWidget(self.close_button)
        layout.addLayout(foot)

        self._apply_status(None)
        # Focus starts on Close (the default button, as in any Windows dialog); Tab order is the reading
        # order: step 1's link, More details, the reminder box, Close.
        self.close_button.setFocus(Qt.FocusReason.OtherFocusReason)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(constants.lhm.STATUS_REFRESH_MS)
        self._status_timer.timeout.connect(self.refresh_status)

    # -- building blocks ------------------------------------------------------------------------------
    def _bullet(self, text: str, c: dict) -> QHBoxLayout:
        """One "More details" point with a hanging indent: the bullet in its own column."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(tokens.SPACE_S)
        dot = QLabel("•")
        dot.setFont(su.font(tokens.TYPE_BODY))
        dot.setStyleSheet(f"color: {c['muted']}; background: transparent;")
        dot.setAlignment(Qt.AlignmentFlag.AlignTop)
        dot.setAccessibleName("")
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        label = su.ui_label(text)
        label.setWordWrap(True)
        label.setFont(su.font(tokens.TYPE_BODY))
        label.setStyleSheet(f"color: {c['muted']}; background: transparent;")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(label, 1)
        self.details_lines.append(label)
        return row

    @staticmethod
    def _compact_expander(expander, c: dict) -> None:
        """A 40 px header, and High Contrast's own colours (the Settings expander paints app tokens)."""
        header = expander.header()
        header.setMinimumHeight(_DETAILS_HEADER_PX)
        lay = header.layout()
        if lay is not None:
            lay.setContentsMargins(tokens.SPACE_M, tokens.SPACE_XS, tokens.SPACE_M, tokens.SPACE_XS)
        if c["hc"]:
            header.setStyleSheet(
                f"#expanderHeader {{ background-color: {c['bg']}; border: 1px solid {c['text']};"
                f" border-radius: {tokens.RADIUS_CARD}px; }}"
                f" #expanderHeader:focus {{ border: {tokens.FOCUS_RING_WIDTH}px solid {c['badge']}; }}"
                f" QLabel {{ color: {c['text']}; background: transparent; }}")
            import re
            for lbl in header.findChildren(QLabel):   # title and chevron (keep the chevron's icon font)
                sheet = lbl.styleSheet()
                lbl.setStyleSheet(re.sub(r"(?<![-\w])color:\s*[^;]+;", f"color: {c['text']};", sheet)
                                  if "color:" in sheet else f"color: {c['text']}; background: transparent;")

    # -- live state -----------------------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            apply_win11_chrome(int(self.winId()), dark=su.is_dark_mode())
        except Exception:
            pass
        if self._status_provider is not None:
            self.refresh_status()
            self._status_timer.start()

    def hideEvent(self, event) -> None:
        self._status_timer.stop()
        super().hideEvent(event)

    def done(self, result: int) -> None:
        self._status_timer.stop()
        super().done(result)

    def refresh_status(self) -> None:
        """Read LHM's state (the monitor thread's lhm_status) and update the steps - every second while
        the dialog is open, the same cadence as Settings > Hardware's status line."""
        try:
            status = self._status_provider() if self._status_provider is not None else None
        except Exception:
            status = None
        if status != self.status:
            self._apply_status(status)

    def _apply_status(self, status: Optional[str]) -> None:
        L = constants.lhm
        self.status = status
        connected = status == L.STATUS_OK
        if connected:
            states = {1: (False, True), 2: (False, True)}
        elif status == L.STATUS_SERVER_OFF or self._initial_step == 2:
            states = {1: (False, True), 2: (True, False)}   # LHM runs: step 1 is done
        else:
            states = {1: (True, False), 2: (False, False)}
        c = self._colors
        for step in self.steps:
            current, done = states[step.number]
            step.set_state(current=current, done=done)
            step.badge.set_fill(c["badge"] if current or done else c["muted"])
        self.connected_label.setVisible(connected)
        self.close_button.set_accent(connected)
        self._fit_height()

    # -- sizing ---------------------------------------------------------------------------------------
    def _height_for_contents(self, width: int) -> int:
        """The height every wrapped label needs at ``width`` - the layout's height-for-width."""
        lay = self.layout()
        # A nested change (More details' content showing / hiding) reaches this layout only through
        # posted LayoutRequests: deliver them now rather than measure the old contents.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        lay.invalidate()
        lay.activate()
        return lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else lay.totalSizeHint().height()

    def _fit_height(self) -> None:
        """Fit the window to its contents after they change (a step ticks, "Temperatures are on."
        appears, More details opens or closes).

        A top-level window does not follow its layout's height-for-width: Qt keeps the window's size
        and its minimum is the layout's minimumSize(), in which a word-wrapped QLabel counts as one
        short line. So when "Temperatures are on." appeared, the layout squeezed the wrapped step
        labels instead of growing the window, and step 1 lost its last line. The minimum height is
        therefore the height-for-width at the current width (kept current by resizeEvent too), and
        the window snaps to it here."""
        if self.layout() is None:
            return
        need = self._height_for_contents(self.width())
        self.setMinimumHeight(need)
        if self.isVisible() and self.height() != need:
            self.resize(self.width(), need)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Narrowing the window wraps the text onto more lines: never let it get shorter than that.
        lay = self.layout()
        if lay is not None and event.size().width() != event.oldSize().width():
            need = self._height_for_contents(event.size().width())
            if need != self.minimumHeight():
                self.setMinimumHeight(need)

    # -- actions --------------------------------------------------------------------------------------
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

