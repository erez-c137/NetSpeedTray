"""
Data-cap usage alerts.

Fires a tray toast at 80% and 100% of the configured data cap, at most once each per
billing period. Restart-safe: which levels already fired is persisted in the config key
`usage_alert_state` as "<period_key>:<level>,<level>", so a relaunch mid-period doesn't
re-nag, and a new period clears it. Dependencies are injected so the logic is testable
without Qt or a live tray.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Set, Tuple

logger = logging.getLogger("NetSpeedTray.UsageAlerts")

# ISPs advertise caps in decimal GB.
_BYTES_PER_GB = 1000 ** 3
_LEVELS = (80, 100)


def used_for_count(up_bytes: float, down_bytes: float, count: str) -> float:
    """Bytes that count toward the cap given the 'total' | 'download' | 'upload' mode."""
    if count == "download":
        return down_bytes
    if count == "upload":
        return up_bytes
    return up_bytes + down_bytes


class UsageAlertController:
    def __init__(self,
                 usage_getter: Callable[[], Tuple[float, float]],
                 config_getter: Callable[[], Dict],
                 period_getter: Callable[[], str],
                 notify: Callable[[str, str], None],
                 save_state: Callable[[str], None],
                 i18n=None) -> None:
        self._usage = usage_getter        # () -> (up_bytes, down_bytes) this period
        self._config = config_getter      # () -> config dict
        self._period = period_getter      # () -> current period_key
        self._notify = notify             # (title, message) -> tray toast
        self._save_state = save_state     # (encoded_state) -> persist to config
        self._i18n = i18n                 # for localized toast title/message

    def _tr(self, key: str, default: str) -> str:
        return str(getattr(self._i18n, key, default)) if self._i18n is not None else default

    @staticmethod
    def _state_id(period: str, cap_gb: float, count: str) -> str:
        """Identity of the current alert context. Includes the cap + count mode so that
        changing either mid-period clears the fired levels and re-evaluates (a lowered
        cap can warn again; a raised cap won't suppress a genuinely-new crossing)."""
        return f"{period}|{cap_gb:g}|{count}"

    @staticmethod
    def _parse_state(raw: str, state_id: str) -> Set[int]:
        """Levels already fired for this exact (period, cap, mode); empty if any differ."""
        if not raw or "::" not in raw:
            return set()
        stored_id, _, levels = raw.partition("::")
        if stored_id != state_id:
            return set()
        out: Set[int] = set()
        for tok in levels.split(","):
            tok = tok.strip()
            if tok.isdigit():
                out.add(int(tok))
        return out

    @staticmethod
    def _encode_state(state_id: str, fired: Set[int]) -> str:
        return f"{state_id}::{','.join(str(x) for x in sorted(fired))}"

    def check(self) -> None:
        """Evaluate usage vs cap and fire any newly-crossed threshold. Cheap; call on a
        modest cadence. No-op unless the cap and its alerts are both enabled."""
        cfg = self._config()
        if not cfg.get("data_cap_enabled") or not cfg.get("data_cap_alert_enabled"):
            return
        cap_gb = float(cfg.get("data_cap_gb", 0) or 0)
        if cap_gb <= 0:
            return

        up, down = self._usage()
        used_gb = used_for_count(up, down, cfg.get("data_cap_count", "total")) / _BYTES_PER_GB
        pct = (used_gb / cap_gb) * 100.0

        period = self._period()
        state_id = self._state_id(period, cap_gb, cfg.get("data_cap_count", "total"))
        fired = self._parse_state(cfg.get("usage_alert_state", ""), state_id)

        newly_levels = [level for level in _LEVELS if pct >= level and level not in fired]
        newly = bool(newly_levels)
        if newly_levels:
            fired.update(newly_levels)
            # If more than one threshold is newly crossed in a single check (e.g. usage is already over
            # cap on the first check of a period), fire ONLY the highest - otherwise the 80% and 100%
            # flyouts render on top of each other at the same position (#9). All crossed levels are still
            # marked fired so neither re-fires later in the period.
            self._fire(max(newly_levels), used_gb, cap_gb)

        if newly:
            try:
                self._save_state(self._encode_state(state_id, fired))
            except Exception as e:  # never let alert bookkeeping break the poll
                logger.error("Could not persist usage_alert_state: %s", e)

    def _fire(self, level: int, used_gb: float, cap_gb: float) -> None:
        try:
            from netspeedtray.utils.helpers import format_decimal
            cap = f"{cap_gb:g}"
            used = format_decimal(used_gb, self._i18n, 1)
            if level >= 100:
                title = self._tr("USAGE_ALERT_REACHED_TITLE", "Data cap reached")
                msg = self._tr("USAGE_ALERT_REACHED_MSG",
                               "You've used your {cap} GB data cap ({used} GB this period).")
                msg = msg.format(cap=cap, used=used)
            else:
                title = self._tr("USAGE_ALERT_WARNING_TITLE", "Data cap warning")
                msg = self._tr("USAGE_ALERT_WARNING_MSG",
                               "You've used {level}% of your {cap} GB data cap ({used} GB this period).")
                msg = msg.format(level=level, cap=cap, used=used)
            self._notify(title, msg)
        except Exception as e:
            logger.error("Could not show usage alert toast: %s", e)
