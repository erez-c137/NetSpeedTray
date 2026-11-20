# PR #554 Rework — Review Package (LOCAL ONLY, not pushed)

Branch: `pr/widget-left-edge-pin` @ commit `005d8b2` (rework commit on top of the CI-green rebased state)
Patch: `rework.patch` | Failure lists: `failures-rework.txt` (95), `failures-base.txt` (base-branch comparison run still in progress)

## Spec point → implementation

| Erez's spec | Implementation |
|---|---|
| Anchor to the first shell thing on the left (Widgets button's right edge when there, else the bar's left edge) | `PositionManager._left_anchor_base_x()` + `left_anchor_spot_x()` — reuses the #552-era Widgets detection (`taskbar_layout_snapshot`, `widgets_toggle_generation`, `dock_signature`); zero changes to taskbar_utils.py |
| 8 logical px gap, own constant | `constants/layout.py`: `LEFT_ANCHOR_GAP_PX = 8` with the "never abut a shell button, separate from TRAY_EDGE_MIN_GAP_PX" comment |
| Left-aligned bars / Win10 → fall back to the tray spot | `left_end_is_free()`: tasklist_rect at the left end or TaskbarAl==0 → `(False, None)` → the pin is ignored for placement |
| Side bars → option greyed out | `_left_anchor_base_x` returns None on non-TOP/BOTTOM edges; the Widget-page toggle greys out (see widget.py changes) |
| #24 app-overlap yield on the left | The `_app_overlap_context` exemption is REPLACED by a left-anchor-aware yield context — the anchored spot ducks when app buttons cover it and returns when clear, with the "seen clear once" suppression preserved |
| Follow the weather button's width changes | `_refresh_left_anchor_layout()` — generation-keyed probe cache (one-shot slot probe on first sight of a bar, background cadence after, re-probe on Widgets toggle flips + grace window); every placement pass re-derives the anchor from the current button rect |
| Drag offset from the left anchor, Reset clears it | New `left_anchor_drag_dx` config key (defaults + schema), clamped >= 0 in `_left_anchor_drag_dx()`; drag path saves it; `reset_to_default_position` clears it |
| Topmost without the helper (the big one) | Event-driven only: the burial recovery, when the pin is on, re-asserts HWND_TOPMOST on the widget's OWN window at the anchored spot instead of moving it above the bar (#327 adapted); the (re)dock topmost seat also re-seats for a left-anchored dock. NO timers/periodic re-assertion (#200 respected) |
| Settings card on the Widget page | Moved from general.py to widget.py Behavior section, load/save wired, label updated in en_US + mirrored to 16 locales |

## Tests
- `test_pin_left_edge.py`: 39 passed (was 8 — grew for the anchor/fallback/yield/drag/reset/grey-out/burial semantics)
- Full local unit lane: 8536 passed, 95 failed, 50 skipped — the failures are the known environmental classes (font-glyph/shaping, subprocess-probe, DST, COM). One (color-scheme probe) verified identical on the pre-rework branch. A base-vs-rework failure diff was still running at packaging time; GitHub CI is the definitive gate.
- `py_compile` clean on all touched files; all 18 locale JSONs parse.

## Design decisions to review closely
1. `left_end_is_free()` treats a Widgets button in the bar's RIGHT half as the tray end's button (mirrors `_calculate_horizontal_position`'s existing test) — a left-aligned bar then falls back even if its registry read fails.
2. The left-anchor probe reuses the side-bar placement's L1 precedent: one-shot bounded slot probe on first sight, background probes after.
3. The burial recovery in pin mode: `_burial_fresh` is cleared after the anchor re-seat so the recovery cannot loop.
4. RAM of the yield: unchanged for the tray side; the anchored spot is what the #24 yield now evaluates for a pinned widget.

## FINAL verification (base-vs-rework failure diff, completed)
- Base branch failures: 95 | Rework branch failures: 95 | **Failures only on the rework: 0**
- The failure sets are IDENTICAL: every local failure is pre-existing/environmental. The rework introduces zero new test failures. GitHub CI (which runs the full lane in a clean environment) is the definitive gate on push.
