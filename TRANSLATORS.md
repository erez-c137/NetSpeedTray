# Translators

NetSpeedTray's UI is available in 14 languages - including Hebrew, its first right-to-left locale - thanks to the community contributors below. Each name represents real time and care invested in making the app feel native to its users - thank you.

If you'd like to contribute a translation or improve an existing one, see the locale files in [`src/netspeedtray/constants/locales/`](src/netspeedtray/constants/locales/) and open a pull request. The `en_US.json` file is the source of truth - all other locales must keep key parity with it (enforced by [`test_locales_parity.py`](src/netspeedtray/tests/unit/test_locales_parity.py)).

## Contributors

| Language | Locale | Translator | Initial PR |
|----------|--------|------------|------------|
| Korean | `ko_KR` | [@VenusGirl](https://github.com/VenusGirl) ❤ | [#73](https://github.com/erez-c137/NetSpeedTray/pull/73), polished in [#97](https://github.com/erez-c137/NetSpeedTray/pull/97), [#101](https://github.com/erez-c137/NetSpeedTray/pull/101), [#139](https://github.com/erez-c137/NetSpeedTray/pull/139) |
| Dutch | `nl_NL` | [@CMTriX](https://github.com/CMTriX) | [#124](https://github.com/erez-c137/NetSpeedTray/pull/124) |
| Russian | `ru_RU` | [@ZeoNish](https://github.com/ZeoNish) | [#63](https://github.com/erez-c137/NetSpeedTray/pull/63) |
| Slovenian | `sl_SI` | [@anderlli0053](https://github.com/anderlli0053) (Andrew Poženel) | [#79](https://github.com/erez-c137/NetSpeedTray/pull/79) |
| Polish | `pl_PL` | FadeMind | [#39](https://github.com/erez-c137/NetSpeedTray/pull/39) |
| French | `fr_FR` | [@logounet](https://github.com/logounet) | #94 |
| Japanese | `ja_JP` | [@coolvitto](https://github.com/coolvitto) | [#155](https://github.com/erez-c137/NetSpeedTray/pull/155) |
| Turkish | `tr_TR` | [@lezgintekay](https://github.com/lezgintekay) - **ongoing maintainer** | [#249](https://github.com/erez-c137/NetSpeedTray/pull/249) |
| Italian | `it_IT` | [@parmacvn-beep](https://github.com/parmacvn-beep) (Mirko) | [#277](https://github.com/erez-c137/NetSpeedTray/issues/277) |
| Hungarian | `hu_HU` | [@cscsanaki](https://github.com/cscsanaki) | [#302](https://github.com/erez-c137/NetSpeedTray/pull/302) |
| Simplified Chinese | `zh_CN` | [@RainThings](https://github.com/RainThings) | [#209](https://github.com/erez-c137/NetSpeedTray/pull/209) |
| Traditional Chinese (Taiwan) | `zh_TW` | [@raylolhue](https://github.com/raylolhue), with terminology improvements from [@in2002-tw](https://github.com/in2002-tw) and native polish from [@tony8077616](https://github.com/tony8077616) | [#199](https://github.com/erez-c137/NetSpeedTray/pull/199), [#215](https://github.com/erez-c137/NetSpeedTray/pull/215) |
| Hebrew (RTL) | `he_IL` | [@rami123](https://github.com/rami123) (initiated the locale) — remaining strings AI-assisted, **pending native review** | [#165](https://github.com/erez-c137/NetSpeedTray/pull/165) |

Other locales (English, German, Spanish) are currently maintained by the project owner. Native-speaker reviews are very welcome - even one-line corrections to phrasing or terminology are valuable.

> ℹ️ **About AI-assisted strings:** Strings added for features released *after* a translator's most recent contribution (e.g. the App Activity window, Support Bundle, and Preferred Monitor) have been filled in with **machine / AI-assisted translation** so every language stays complete and usable. These AI-assisted strings are **not** the work of the human translators credited above, and they have **not** yet been reviewed by a native speaker. If you spot an awkward or incorrect one, a one-line correction PR is hugely appreciated - and you'll be credited for it.

> 🆕 **Pending native review for 2.0** (non-English values AI-assisted; a one-line native check before the 2.0 tag is very welcome - see issue #157):
> - Tray "data used" glance: `USAGE_TODAY_LABEL`, `USAGE_THIS_MONTH_LABEL`
> - Update dialog / one-click updater: `UPDATE_RELEASE_NOTES_LABEL`, `UPDATE_DOWNLOADING_TITLE`, `UPDATE_FALLBACK_MESSAGE`
> - First-run welcome: `WELCOME_2_0_TITLE`, `WELCOME_2_0_BODY`, `WELCOME_2_0_WHATS_NEW_BUTTON`, `WELCOME_2_0_GOT_IT_BUTTON` (the **body** is the longest/most nuanced - most worth a look)
> - **Monitor window (Overview / Network / Hardware tabs):** `GRAPH_HW_UTIL_AXIS_LABEL`, `MEMORY_HEADER`, `HARDWARE_LOADING_MESSAGE`, `HARDWARE_NO_DATA_MESSAGE`, and `HARDWARE_SUMMARY_TEMPLATE` (**keep every `{…}` field - `{procs}`, `{cpu:.0f}`, `{ram:.1f}`, `{ram_unit}`, `{updated_at}`**). Drafts were grounded in your existing terms (e.g. "Utilization" follows your `GRAPH_CPU_UTIL_AXIS_LABEL`). `MONITOR_TILE_RAM` / `MONITOR_TILE_VRAM` are acronyms left as-is - localize only if your language does.

> 🆕 **Pending native review for Hebrew (2.1):** `he_IL` was seeded by [@rami123](https://github.com/rami123) (~74 strings); the remaining ~300 were AI-assisted for the 2.1 RTL work, each checked to preserve its `{…}` placeholders. The whole locale is **pending a native-speaker pass** - Hebrew is the app's first right-to-left language, so both the wording and any RTL layout quirks are worth a look. Corrections are hugely welcome and credited.

> 🆕 **Pending native review for 2.2 (side taskbar):** `SIDE_TASKBAR_HOVER_NOTE` - the note under Settings > Widget > Display Mode when the widget is docked to a left/right taskbar ("On a side taskbar, CPU/GPU/memory show when you hover the widget."). Non-English values are AI-assisted; `CPU`/`GPU` are kept as the widget paints them.

> 🆕 **Pending native review for 2.2 (hover-card hardware):** `HARDWARE_IN_HOVER_CARD_LABEL` / `HARDWARE_IN_HOVER_CARD_DESCRIPTION` - the switch under Settings > Hardware > Appearance that keeps the taskbar widget to network speed and shows CPU/GPU/memory in the hover card instead - and `HOVER_CARD_DISPLAY_MODE_NOTE`, the note under Settings > Widget > Display Mode while that switch is on. Non-English values are AI-assisted and reuse each locale's existing "hover the widget" wording; feedback on #364.

> 🆕 **Pending native review for 2.2 (hover card pin + Reset Position - see #364):** non-English values are AI-assisted.
> - `RESET_POSITION_MENU_ITEM` - right-click menu item, shown once the widget has been dragged; it puts the widget back by the tray. Keep the `&` accelerator on a letter no other item of that menu uses.
> - `SIDE_BAR_NO_ROOM_TITLE` / `SIDE_BAR_NO_ROOM_BODY` - a one-time message when a left/right taskbar is so full of app buttons that the widget has no free spot; the body quotes your `RESET_POSITION_MENU_ITEM` (without the `&`).
> - `APP_OVERLAP_YIELD_BODY` - a one-time message (under the `SIDE_BAR_NO_ROOM_TITLE` title) the first time the widget hides because a horizontal taskbar's app buttons have grown into it: it comes back when there is room, starting the app again brings it back at once, and the switch below keeps it visible. Keep the `{setting}`, `{settings}` and `{page}` placeholders - they are filled with your `HIDE_WHEN_APPS_COVER_LABEL`, `SETTINGS_WINDOW_TITLE` and `WIDGET_SETTINGS_GROUP`.
> - `APP_OVERLAP_ACTIVATE_BODY` - shown when the app is started again while the widget had stepped aside: it stays visible until the next restart; same placeholders.
> - `HIDE_WHEN_APPS_COVER_LABEL` - Settings > Widget switch (on by default): hide the widget while the taskbar's app buttons cover it. Sentence case.
> - `HOVER_CARD_PIN_HINT` - a small line at the bottom of the hover card: clicking the widget keeps the card open.

> 🆕 **Pending native review for 2.2 (beta.4 polish, see #364):** non-English values are AI-assisted.
> - `HARDWARE_HISTORY_OFF_MESSAGE` - the note on an empty Monitor → Hardware graph when CPU and GPU monitoring are off (two lines; the page names match your `SETTINGS_WINDOW_TITLE` and `HARDWARE_MONITORING_GROUP`).
> - `HARDWARE_HISTORY_RECORD_OFF_MESSAGE` - the same note when monitoring is on but "Record hardware history" is off (quote your `RECORD_HARDWARE_HISTORY_LABEL`; the page is your `ADVANCED_SETTINGS_GROUP`).
> - `WIDGET_HOVER_TOOLTIP` (rewritten) + `WIDGET_HOVER_DOUBLE_CLICK_HINT` (new) - the first-run hint in the widget's hover card. The old text listed right-click items the menu no longer has. The second line is filled with your `CLICK_ACTION_*_LABEL` for the user's double-click setting - keep `{action}`.
> - `USAGE_THIS_PERIOD_LABEL` - replaces "This Month" in the hover card and Monitor → Overview when a data cap with a reset day other than the 1st is set (the row then counts the billing period, like the data-cap row under it). Match the style of your `USAGE_THIS_MONTH_LABEL`.
> - `GRAPH_BOOT_MARKER` - the small italic label on the Monitor graph's dashed line where the PC last started (was English "Boot" in every language). Keep it to one short word.

> 🆕 **Pending native review for 2.2 (LibreHardwareMonitor web server, #187 / PR #276, see #364):** non-English values are AI-assisted.
> - `LHM_WEB_SERVER_HINT` (rewritten for rc.2) - shown under the temperature note on Settings → Hardware and in the "turn on temperatures" explainer: current LibreHardwareMonitor is read over its web server, which the user has to switch on. It no longer says "leave authentication off". It now says: up to LHM v0.9.6 the server has to listen on all interfaces (those versions cannot pick 127.0.0.1 alone), so **don't open the port in the firewall**, and pick 127.0.0.1 once a newer LHM offers it; and if LHM's authentication is on, enter the same user name and password in Settings → Hardware (use your `SETTINGS_WINDOW_TITLE` → `HARDWARE_MONITORING_GROUP`, as `HARDWARE_HISTORY_OFF_MESSAGE` does). Its last clause warns that on LHM v0.9.6 and older the password has to be re-entered in LHM after every LHM restart (those versions re-hash their stored password on each start). Keep both `{port}`s, and keep `Options → Remote Web Server` in English - it is LibreHardwareMonitor's own (untranslated) menu.
> - `LHM_CONNECTION_SECTION`, `LHM_PORT_LABEL`, `LHM_USERNAME_LABEL`, `LHM_PASSWORD_LABEL` (new, rc.2) - a collapsed section on Settings → Hardware, right under that hint: the port LHM's web server uses (default 8085) and the user name / password of its optional authentication. Use your usual words for "user name" and "password" (Windows' own sign-in terms are a safe choice).
> - `LHM_STATUS_CONNECTED`, `LHM_STATUS_NOT_FOUND`, `LHM_STATUS_AUTH_REQUIRED`, `LHM_STATUS_AUTH_FAILED`, `LHM_STATUS_OTHER` (new, rc.2) - the live result shown under the port: connected; nothing answers on that port; LHM asks for a password; LHM rejected it (LHM up to v0.9.6 changes its stored password every time it restarts, so the text says to set it again in LHM); something else answers on the port. Keep `{port}` where the English has it.
> - `LHM_PASSWORD_UNREADABLE` (new, rc.2) - shown under the password field when the saved password cannot be decrypted on this Windows account (a config copied from another PC or user): it is kept, and the user is asked to enter it again.
> - `HARDWARE_TEMPS_LIMITATION_NOTE` / `TEMP_ONBOARDING_BODY` - the "use LHM v0.9.4, later versions removed the interface" sentence is gone (no longer true). Removed from `en_US`, `hu_HU`, `it_IT` and `tr_TR`, the locales that carried it; nothing else in them changed.

> 🆕 **Pending native review for 2.2 (battery in the hover card, #323, see #364):** non-English values are AI-assisted.
> - `POWER_HOVER_CARD_LABEL` - the opt-in switch under Settings > General > Interaction, next to your `HOVER_USAGE_CARD_LABEL` - reuse its "hover the widget" wording.
> - `POWER_SECTION_TITLE` - the small caption over the card's battery rows ("Battery"). One word, as on your laptop's own battery flyout.
> - `POWER_NOW_LABEL` / `POWER_TODAY_AVG_LABEL` - row labels in the card's battery section ("Now" = the live draw in watts; "Today avg" = today's average draw on battery). Keep them short; the value sits to their right.
> - `POWER_ON_AC_VALUE` - shown in place of the watts when the laptop is plugged in and not charging ("On AC").
> - `POWER_CHARGE_PCT_TEMPLATE` - the charge-level line under the rows; keep `{pct}`, and write the percent the way your `PERCENT_TEMPLATE` does.

> 🆕 **Pending native review for 2.2 (release-candidate checkup, see #364):** non-English values are AI-assisted.
> - Turkish: `MEMORY_HEADER` was still English ("Memory") - now "Bellek", matching the "bellek" in your hover-card strings. `THOUSANDS_SEPARATOR` is now "." (it was "," - the same as your decimal separator, so 1,234 read as a decimal).
> - Hungarian: `CANCEL_BUTTON` is now "Mé&gse" (Alt+G) - "&Mégse" shared Alt+M with "&Mentés". Pick another letter if G reads oddly.
> - New, all locales: the Monitor → Network graph's empty-range notes - `NET_GRAPH_NO_HISTORY_YET` (nothing recorded yet), `NET_GRAPH_HISTORY_STARTS` (the range ends before the first record; **keep `{date}`**, e.g. "Oct 01") and `NET_GRAPH_NO_TRAFFIC` (two lines: history exists, but this period had none because the PC was off, idle or paused).
> - New, all locales: `PREFERRED_MONITOR_ITEM` ("Monitor {index}: {width}x{height}" in Settings > Widget > Preferred Monitor - was hard-coded English; **keep `{index}`, `{width}`, `{height}`**), `PREFERRED_MONITOR_PRIMARY_TAG` ("(primary)"), `SETTINGS_NOT_CONNECTED_TAG` ("(not connected)" - after a saved monitor that is unplugged right now) and `INTERFACE_GROUP_NOT_CONNECTED` (the heading over selected network adapters that aren't present right now, in Settings > Network).

## How translation works in NetSpeedTray

- Each locale lives in a single JSON file under [`src/netspeedtray/constants/locales/`](src/netspeedtray/constants/locales/)
- Keys must match `en_US.json` exactly (the parity test fails the build otherwise)
- Values can use Python format-string placeholders like `{file_path}` or `{error}` - **preserve these verbatim.** A translation that drops a value placeholder or invents a new one fails CI ([`test_placeholder_parity.py`](src/netspeedtray/tests/unit/test_placeholder_parity.py)), because it would crash `.format()` at runtime. (Grammar helpers like `{plural}` may be dropped if your language doesn't pluralize that way.)
- For new keys added by a feature PR, non-English values are first filled with a machine / AI-assisted translation (or, failing that, an English placeholder) so the parity test passes and users get an as-localized-as-possible UI right away. As noted above, these AI-assisted strings are **not** attributed to the credited human translators and are pending native-speaker review - follow-up correction PRs are very welcome

### Finding what needs translation

Every locale is kept in the **exact same key order as `en_US.json`** - so the same line number is the same key in every file. To see what's new or changed for your language:

- **Side-by-side:** open your `xx_XX.json` next to `en_US.json`; matching lines are matching keys.
- **What changed since the last release:** `git diff v1.3.3 -- src/netspeedtray/constants/locales/en_US.json` shows every English string added or reworded (a reworded source means your existing translation is now stale - worth a re-check).
- The maintainer also posts a digest of new/changed strings to the translation thread ([#157](https://github.com/erez-c137/NetSpeedTray/issues/157)) when a batch lands, so you can reply there or open a PR - whichever is easier.
