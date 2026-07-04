# Translators

NetSpeedTray's UI is available in 11 languages thanks to the community contributors below. Each name represents real time and care invested in making the app feel native to its users - thank you.

If you'd like to contribute a translation or improve an existing one, see the locale files in [`src/netspeedtray/constants/locales/`](src/netspeedtray/constants/locales/) and open a pull request. The `en_US.json` file is the source of truth - all other locales must keep key parity with it (enforced by [`test_locales_parity.py`](src/netspeedtray/tests/unit/test_locales_parity.py)).

## Contributors

| Language | Locale | Translator | Initial PR |
|----------|--------|------------|------------|
| Korean | `ko_KR` | [@VenusGirl](https://github.com/VenusGirl) ❤ | [#73](https://github.com/erez-c137/NetSpeedTray/pull/73), polished in [#97](https://github.com/erez-c137/NetSpeedTray/pull/97), [#101](https://github.com/erez-c137/NetSpeedTray/pull/101), [#139](https://github.com/erez-c137/NetSpeedTray/pull/139) |
| Dutch | `nl_NL` | [@CMTriX](https://github.com/CMTriX) | [#124](https://github.com/erez-c137/NetSpeedTray/pull/124) |
| Russian | `ru_RU` | [@ZeoNish](https://github.com/ZeoNish) | [#63](https://github.com/erez-c137/NetSpeedTray/pull/63) |
| Slovenian | `sl_SI` | [@anderlli0053](https://github.com/anderlli0053) (Andrew Poženel) | [#79](https://github.com/erez-c137/NetSpeedTray/pull/79) |
| Polish | `pl_PL` | [@FadeMind](https://github.com/FadeMind) | [#39](https://github.com/erez-c137/NetSpeedTray/pull/39) |
| French | `fr_FR` | [@logounet](https://github.com/logounet) | #94 |
| Japanese | `ja_JP` | [@coolvitto](https://github.com/coolvitto) | [#155](https://github.com/erez-c137/NetSpeedTray/pull/155) |
| Traditional Chinese (Taiwan) | `zh_TW` | [@raylolhue](https://github.com/raylolhue), with terminology improvements from [@in2002-tw](https://github.com/in2002-tw) — pending native polish | [#199](https://github.com/erez-c137/NetSpeedTray/pull/199) |

Other locales (English, German, Spanish) are currently maintained by the project owner. Native-speaker reviews are very welcome - even one-line corrections to phrasing or terminology are valuable.

> ℹ️ **About AI-assisted strings:** Strings added for features released *after* a translator's most recent contribution (e.g. the App Activity window, Support Bundle, and Preferred Monitor) have been filled in with **machine / AI-assisted translation** so every language stays complete and usable. These AI-assisted strings are **not** the work of the human translators credited above, and they have **not** yet been reviewed by a native speaker. If you spot an awkward or incorrect one, a one-line correction PR is hugely appreciated - and you'll be credited for it.

> 🆕 **Pending native review for 2.0** (non-English values AI-assisted; a one-line native check before the 2.0 tag is very welcome - see issue #157):
> - Tray "data used" glance: `USAGE_TODAY_LABEL`, `USAGE_THIS_MONTH_LABEL`
> - Update dialog / one-click updater: `UPDATE_RELEASE_NOTES_LABEL`, `UPDATE_DOWNLOADING_TITLE`, `UPDATE_FALLBACK_MESSAGE`
> - First-run welcome: `WELCOME_2_0_TITLE`, `WELCOME_2_0_BODY`, `WELCOME_2_0_WHATS_NEW_BUTTON`, `WELCOME_2_0_GOT_IT_BUTTON` (the **body** is the longest/most nuanced - most worth a look)
> - **Monitor window (Overview / Network / Hardware tabs):** `GRAPH_HW_UTIL_AXIS_LABEL`, `MEMORY_HEADER`, `HARDWARE_LOADING_MESSAGE`, `HARDWARE_NO_DATA_MESSAGE`, and `HARDWARE_SUMMARY_TEMPLATE` (**keep every `{…}` field - `{procs}`, `{cpu:.0f}`, `{ram:.1f}`, `{ram_unit}`, `{updated_at}`**). Drafts were grounded in your existing terms (e.g. "Utilization" follows your `GRAPH_CPU_UTIL_AXIS_LABEL`). `MONITOR_TILE_RAM` / `MONITOR_TILE_VRAM` are acronyms left as-is - localize only if your language does.

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
