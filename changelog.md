# Changelog

What changed in each release of NetSpeedTray, newest first.

Versions follow [semantic versioning](https://semver.org): a **patch** release
(2.1.**3**) is mostly fixes, sometimes with a small opt-in addition; a **minor**
release (2.**1**.0) adds features; a **major** one (**2**.0.0) changes how the app
works. Every entry links the issue or PR behind it, so you can read the full story
or the code.

Downloads and signed installers: [Releases](https://github.com/erez-c137/NetSpeedTray/releases).

<!--
=============================================================================
 HOW TO WRITE AN ENTRY  (read this before editing - human or AI)
=============================================================================

WHO READS THIS FILE
  Users deciding whether to upgrade, people checking if their bug got fixed,
  and developers looking for when a behavior changed. Write for the first two;
  the link at the end of each line serves the third.

THE RULE
  One line per change. Lead with what the USER sees, not what the code does.
  Aim for 20-30 words. If you need more, the detail belongs in the linked
  issue or PR, not here.

    GOOD  - **The app now starts in your Windows display language.** Auto-detect
            never worked before, so most non-English users saw English. ([#234])
    BAD   - **Auto-detect language fix.** Replaced `locale.getlocale(LC_CTYPE)`
            with `GetUserDefaultUILanguage()` and added a zh_CHS/zh_CHT alias
            table because the CRT returns "Korean_Korea" which matches no...

  The bad example is true, useful, and belongs in the commit message. A user
  reading it learns nothing about whether to upgrade.

WHEN THE MECHANISM MATTERS
  Some fixes are worth explaining to whoever maintains this next - a wrong
  assumption that took a while to find, or a trap that could easily come back.
  Add ONE indented italic line under the bullet, after a blank line:

    - **The app now starts in your Windows display language.** Auto-detect
      never worked, so most non-English users saw English. ([#234])

      *Under the hood: `locale.getlocale()` returns the C-runtime name
      ("Korean_Korea") on Windows, not "ko_KR", so every lookup missed. Now
      reads `GetUserDefaultUILanguage()`.*

  Use it sparingly - if every bullet has one, none of them stand out. Reserve
  it for the ones where a future maintainer would otherwise repeat the mistake.
  A user can skip the italic line; that is the point.

SECTIONS - use only these, in this order, and omit any that are empty:
    ### Added        new features
    ### Changed      behavior that changed for existing users
    ### Fixed        bugs
    ### Removed      features taken away
    ### Security     anything with a security impact
    ### Localization translation work (project-specific; keep it, it earns its place)

  Do NOT invent new section names or add emoji to them. Earlier entries in this
  file do both - that is drift, not a pattern to copy.

ALSO
  - Start each release with ONE sentence saying what it is.
  - Anything that changes behavior on upgrade goes in a "> **Upgrading:**"
    callout right under that sentence, before the sections.
  - Credit inline and compactly: ([#234], thanks [@user]).
  - Link issues as ([#234]) using the reference-style links kept at the bottom
    of each entry, so the prose stays readable.
  - Never claim a fix you have not verified. If something is partially fixed,
    say which part.
=============================================================================
-->

---

## [2.1.4] - August 23, 2026

The portable build updates itself - plus a log that had been drowning out the bugs it was meant to record, and the groundwork that makes the next release safe to try.

> **Upgrading (portable only):** this is the release that teaches NetSpeedTray to update itself, not the one that benefits. Updating takes two halves - the *old* version has to hand the job over, and only the *new* one can ship in a release. So **2.1.3 → 2.1.4 is the copy-it-yourself flow one last time**, and **2.1.4 → 2.1.5 onward is automatic**. Installer users were never affected.

### Added

- **The portable build now updates itself.** Click *Download Update* and it verifies the new version, replaces your folder and restarts - no copying files by hand. Your settings and history are kept. ([#264], reported by [@kiro5678] in [#260])

  *Under the hood: the verified new EXE does the work, because a program cannot replace the folder it runs from. Deliberately not a helper script - "terminate a process, overwrite binaries" is a malware heuristic, and this path is otherwise WinVerifyTrust + a SignPath pin. The old install is renamed aside before anything is written, so a failure is reversible, and anything doubtful falls back to the guided copy rather than being forced.*

### Fixed

- **The log no longer fills with one repeated line when you lose your connection.** Losing the network wrote a warning every second - one support bundle was 98.5% that single message, which pushed everything useful out and made the bug it was attached to impossible to diagnose. ([#263])

  *Under the hood: the routing lookup is cached to once per 15s, but the guard also required a previous success - so with no route it fell through and the blocking UDP connect ran on every poll. The cache stopped applying in exactly the case it was written for. The throttle is time-only now, and a lost connection logs once on the way out and once on the way back with the duration.*
- **The in-app updater now records what it did**, so a support bundle can show whether an update downloaded, verified and installed. It previously logged only failures. ([#263])
- **Total VRAM now shows whether or not LibreHardwareMonitor is running.** It appeared as used-only with LHM open and used/total with it closed. ([#265], thanks [@PilaScat])

  *Under the hood: total VRAM only ever comes from `nvidia-smi`, and that call was gated on needing temperature or power from it - so with LHM supplying those, it never ran. The total is a property of the card, not a reading, so it is fetched once and cached.*
- **The update dialogs no longer hide behind the taskbar**, and the downloaded folder goes to your real Downloads folder even if you have moved it. Either one could make an update look like it did nothing at all. ([#267], [#260])

  *Under the hood: the dialogs were parented to the widget, which is docked into the taskbar's own Z-order - the same class of problem as #200. And `_downloads_dir()` guessed `~/Downloads` and fell back to the home directory, so a relocated Downloads folder sent the update somewhere nobody would look. Now uses `SHGetKnownFolderPath`.*
- **The memory readout on the widget dropped the `|` before it.** ([#267], thanks [@PilaScat])
- **Closing the Monitor no longer errors in the background.** A database read could be cut off mid-query, which was harmless but wrong. ([#268])

  *Under the hood: per-thread read connections were reclaimed by checking the owning thread against `threading.enumerate()` - which never lists Qt worker threads, so the Monitor's graph worker looked permanently dead and had its connection closed while still querying. Registering the thread would have been worse: on CPython 3.11 the `_DummyThread` never leaves `enumerate()`, so nothing would ever be reclaimed. Pruning is by idle time now, which a busy thread cannot trip.*
- **On a laptop or PC with only integrated graphics, the widget no longer shows `0.0G` for VRAM.** An integrated GPU has no dedicated video memory, so there is nothing to report - it shows nothing rather than a zero that looks like a reading. ([#269])

  *Under the hood: the Monitor's Overview tile already hid itself in this case; the widget and the Hardware telemetry strip had drifted from it. The threshold now lives once, in `helpers.has_dedicated_vram()`.*
- **The "All" period on the Monitor's graphs now shows your actual history.** It was drawing a timeline stretching back roughly a decade, with every real reading squeezed into an unreadable sliver at the right-hand edge. ([#271])

  *Under the hood: `get_start_time()` falls back to `now - 10 years` for ALL when it is not given the earliest row in the database, and the graph only looked that up for the System Uptime period. The query already existed; the Overview tab had always called it. Month and the shorter periods were never affected.*
- **The SMART update rate no longer speeds up to ten times a second after you save settings.** It starts correctly at 2 seconds, then silently dropped to 100 ms the first time you opened Settings. ([#273])

  *Under the hood: SMART is the sentinel `-1.0`, and `set_interval` clamps with `max(0.1, interval)` - so handing it the raw value meant 0.1s, the one rate `constants/update_mode.py` rules out in its own header. `calculate_timer_interval()` already resolved the sentinel; this path never called it. It was also a data bug: the raw tier's key is one-second and written `INSERT OR IGNORE`, so at 100 ms roughly nine of every ten samples were discarded.*
- **Going back to an older version no longer silently stops recording.** Open a database written by a newer NetSpeedTray and it now says so and runs read-only, instead of looking healthy while saving nothing. ([#273])

  *Under the hood: the migration loop is empty on a downgrade, so the old code fell straight through - it still wrote a full copy of the database to a `.bak` on every launch, which nothing ever deleted, and every write raised an `OperationalError`, a `sqlite3.Error` subclass the handlers swallow. Reads kept working, so nothing looked wrong. This matters now because 2.2.0's schema change is the first one anyone will roll back from.*
- **The backup taken before a database upgrade is verified now, and actually contains your history.** ([#273])

  *Under the hood: it was `shutil.copy2`, which copies only the main `.db` - in WAL mode everything since the last checkpoint lives in the sidecar, and the installer force-kills the app, so the WAL is hot exactly when the backup matters. Measured: a copy taken that way recovered zero of 30,000 committed rows and still passed `integrity_check`, because an empty database is a valid one. Now `VACUUM INTO` (which also compacts it ~8x), and the copy is opened and its row counts compared against the source before it is trusted. Old backups are pruned to the newest two.*
- **Pre-release versions are compared correctly**, so anyone testing a beta can be offered the next one. ([#273])

  *Under the hood: parsing stopped at the first non-integer component, so every `2.2.0-beta.N` became `(2, 2)` and successive betas compared EQUAL - a tester was stranded on whichever build they installed first. Needed before 2.2.0 ships as a beta.*

### Localization

- Simplified Chinese refresh from [@RainThings] - including the same "dashboard" → "Monitor" terminology fix that was made in the English source, found independently. ([#261])
- **Turkish now has a maintainer:** [@lezgintekay], who contributed the language in 2.1.3, has offered to keep it current. ([#266])

### Developer notes

- **User-facing text is American English throughout**, and a test keeps it that way. The app's own strings were already consistent; the changelog and README had drifted, at one point using the British spelling in the same sentence as the real UI label "Custom arrow colors". ([#262])
- **The app now ships on Python 3.13.** Nothing changes for you - the runtime is bundled, so this is not something you install - but 3.11.9 was the last release of its series with Windows binaries, so the build could no longer pick up CPython security fixes at all. ([#270])

  *Under the hood: dependency versions were deliberately left alone - every pin already had cp313 wheels - so the interpreter move is verifiable on its own. Also fixed a UPX exclusion that named `python311.dll` literally and would have silently stopped applying.*
- **The history database has a documented schema.** [`DATABASE.md`](DATABASE.md) at the repo root describes every table, unit and rollup tier so you can query your own history - and lists the traps: speeds are bytes, not bits; there is no "All interfaces" row; virtual adapters are mixed in with your real one. Every example query was run before it was published. ([#272])
- CI moved to `actions/setup-python@v7`. ([#256])
- **1,225 automated tests**, up from 1,141 in 2.1.3.

[#256]: https://github.com/erez-c137/NetSpeedTray/pull/256
[#260]: https://github.com/erez-c137/NetSpeedTray/issues/260
[#261]: https://github.com/erez-c137/NetSpeedTray/pull/261
[#262]: https://github.com/erez-c137/NetSpeedTray/pull/262
[#263]: https://github.com/erez-c137/NetSpeedTray/pull/263
[#264]: https://github.com/erez-c137/NetSpeedTray/pull/264
[#265]: https://github.com/erez-c137/NetSpeedTray/pull/265
[#266]: https://github.com/erez-c137/NetSpeedTray/pull/266
[#267]: https://github.com/erez-c137/NetSpeedTray/pull/267
[#268]: https://github.com/erez-c137/NetSpeedTray/pull/268
[#269]: https://github.com/erez-c137/NetSpeedTray/pull/269
[#270]: https://github.com/erez-c137/NetSpeedTray/pull/270
[#271]: https://github.com/erez-c137/NetSpeedTray/pull/271
[#272]: https://github.com/erez-c137/NetSpeedTray/pull/272
[#273]: https://github.com/erez-c137/NetSpeedTray/pull/273
[@kiro5678]: https://github.com/kiro5678
[@PilaScat]: https://github.com/PilaScat
[@RainThings]: https://github.com/RainThings
[@lezgintekay]: https://github.com/lezgintekay

---

## [2.1.3] - August 22, 2026

Fixes from a month of bug reports, including one that has been in every release since the beginning: the app never actually detected your system language.

> **Upgrading:** if you have never picked a language by hand, NetSpeedTray may now start in your **Windows display language** instead of English - Hebrew also switches the layout to right-to-left. To keep English, choose it in **Settings → General → Language** - it is the third item down, under the first dropdown. If your display language is English but your *regional format* is German, Spanish or French, you keep the localized app you already had.

### Added

- **Upload and download arrows can have their own colors.** Turn on *Custom arrow colors* in **Settings → Appearance**. Off by default, so nothing moves unless you ask. ([#168], thanks [@VenusGirl])
- **The Settings and Monitor windows can stay on top.** New toggle in **Settings → Advanced**, so the Monitor can sit over a full-screen app. ([#213], thanks [@CMTriX])

### Changed

- **Clearer guidance for CPU/GPU temperatures.** The setup dialog now names the LibreHardwareMonitor version that works (v0.9.4 - later ones removed the interface NetSpeedTray reads) and says plainly that some very new CPUs report no temperature at all, so you can stop troubleshooting something that cannot be fixed from our side. Reading current LibreHardwareMonitor releases over their web interface is still open ([#187]).

### Fixed

- **The app now starts in your Windows display language.** Auto-detect never worked, so Korean, Japanese, Russian, Polish, Dutch, Slovenian, Hebrew and both Chinese variants always fell back to English - in every release ever shipped. If you found the app in English despite your system being set otherwise and assumed it simply wasn't translated: it was, and I'm sorry. The Language settings card now shows what was detected, so this can't fail quietly again, and choosing Auto-detect correctly prompts you to restart - it used to stay silent. ([#234], thanks [@VenusGirl])

  *Under the hood: `locale.getlocale()` returns the C-runtime name on Windows ("Korean_Korea"), not "ko_KR", so every lookup missed and fell through to English. German, Spanish and French worked only because CPython's `locale_alias` happens to carry their CRT names - which is why this survived a year on an English dev machine. Now reads `GetUserDefaultUILanguage()`.*
- **The CPU temperature no longer shows your graphics card's.** On laptops whose CPU reports no temperature, NetSpeedTray picked up the GPU's sensor instead and labeled it CPU. ([#237], thanks [@Aaronxiexyl], whose sensor report made this visible)

  *Under the hood: when nothing is named exactly "CPU Package" we fall back to matching sensor names, and one keyword was "CORE" - which matches NVIDIA's "GPU Core". The sensor identifier now decides before any name is considered.*
- **GPU usage now counts apps you start after NetSpeedTray.** It only ever watched programs that were already running, so on laptops with two GPUs the discrete card was never measured at all. ([#236], thanks [@balciseri], who filed this as a feature request and turned out to have found a bug)

  *Under the hood: `\GPU Engine` PDH instances are per-process, and they were enumerated once at startup. Now a wildcard counter re-expanded on every sample. Note `AddCounter` succeeds on a wildcard even with no GPU present, so "is there a GPU" must come from the returned array, not from the handle.*
- **An unmeasured CPU or GPU now shows N/A instead of 0%.** Over Remote Desktop the GPU is deliberately not polled, but the readout still displayed a confident `0%`. A real 0% still shows as 0%.

  *Under the hood: the widget seeded its usage fields to `0.0`, and under RDP the GPU poll is skipped entirely, so that seed was all it ever had. Seeded `NaN` now, which renders as N/A.*
- **VRAM no longer reads 0.0 GB after sleep.** It stayed there until you opened Settings and clicked Save. ([#237])
- **The taskbar and the Monitor window now agree on the temperature.** One rounded and the other cut the decimal off, so 27.9 °C showed as both "27" and "28". ([#237])
- **Dragging the widget more than ~500 px from the tray no longer snaps it back** on the next launch. ([#234])
- **The Monitor's Hardware graph draws again.** It had been showing an empty grid with a 0-1 scale instead of your CPU, GPU and RAM history - in 2.1.0, 2.1.1 and 2.1.2. The Network graph on the next tab was never affected.

  *Under the hood: `get_hardware_history()` returns `datetime` timestamps, as its type hint says, but the hardware renderers built their arrays with `np.array(rows, dtype=float)`, which cannot convert one. The `TypeError` was caught and logged by `GraphHost._on_data_ready`, so the tab failed silently and only a log line ever said so - which is why it survived three releases. Found by opening the tab during a release smoke test.*
- **The app now tells Windows what version it is.** Right-clicking `NetSpeedTray.exe` and opening Properties showed a blank Details tab in every release so far - no version, no publisher, no copyright. Installers, update tooling and antivirus reputation checks all read that same metadata.

  *Under the hood: the generated version resource put its `StringStruct`s straight into `StringFileInfo` with no `StringTable` wrapper, so there was no lang-codepage key for Windows to read them under. PyInstaller accepts that silently and the strings really were written into the binary - they were just unreachable. The key and the `Translation` entry also disagreed (Unicode vs 1252) and now match.*
- **The language list shows every language.** Three sat below the fold with no way to scroll to them, and the list has since grown to fourteen. ([#237])

### Security

- **Pillow updated to 12.3.0**, closing thirteen advisories in the image library matplotlib uses for the history graph. Nothing here was exploitable - NetSpeedTray never opens an image, it only writes its own graph - but the dependency is current again. ([#252])

### Localization

- **Turkish - the fourteenth language**, contributed by [@lezgintekay] ([#249]).
- Japanese ([@coolvitto]) and Russian ([@ZeoNish]) updates, a Korean review ([@VenusGirl]), and a fill-in pass across the remaining languages. Several strings that were still English - the free-float label and the portable-update messages - are now translated. Native review is still welcome: [#202].

### Developer notes

- **Tests grew from 865 to 1135**, covering language auto-detect, the tray-menu accelerators, CPU sensor selection, the GPU wildcard counters and the arrow colors.
- **A new locale is now checked for more than key parity.** A locale file that isn't registered in `LANGUAGE_MAP` is unreachable from the UI - Turkish arrived that way and every existing test passed, because parity only ever compares keys between files. ([#254])

  *Under the hood: the new checks also pin each locale's decimal separator to CLDR and cap `DEFAULT_TEXT`, which is painted on the taskbar itself - a spelled-out phrase overflows a slot sized for "N/A".*

[#168]: https://github.com/erez-c137/NetSpeedTray/issues/168
[#187]: https://github.com/erez-c137/NetSpeedTray/issues/187
[#202]: https://github.com/erez-c137/NetSpeedTray/issues/202
[#213]: https://github.com/erez-c137/NetSpeedTray/issues/213
[#234]: https://github.com/erez-c137/NetSpeedTray/issues/234
[#236]: https://github.com/erez-c137/NetSpeedTray/issues/236
[#237]: https://github.com/erez-c137/NetSpeedTray/issues/237
[#252]: https://github.com/erez-c137/NetSpeedTray/pull/252
[#254]: https://github.com/erez-c137/NetSpeedTray/pull/254
[#249]: https://github.com/erez-c137/NetSpeedTray/pull/249
[@Aaronxiexyl]: https://github.com/Aaronxiexyl
[@balciseri]: https://github.com/balciseri
[@CMTriX]: https://github.com/CMTriX
[@coolvitto]: https://github.com/coolvitto
[@lezgintekay]: https://github.com/lezgintekay
[@VenusGirl]: https://github.com/VenusGirl
[@ZeoNish]: https://github.com/ZeoNish

---

## [2.1.2] - July 15, 2026

One fix for the in-app updater.

### Fixed

- **"Check for updates" no longer opens the update window twice.** The second copy popped up over the download and could stop the installer from launching. The automatic daily check was never affected.

  *Under the hood: `update_available` was wired to the handler once at construction, and `check_for_updates` added a second identical connection.*


---

## [2.1.1] - July 15, 2026

Auto-hide taskbar behavior, a flat CPU temperature, and an antivirus false positive.

### Fixed

- **The widget stops jumping when an auto-hide taskbar slides in and out.** It chased the taskbar's moving edge frame by frame instead of settling where it belongs. Fixed on all four screen edges. ([#135], thanks [@Johnnym3334])
- **Widget text is no longer cropped top and bottom on some taskbars.** ([#221], thanks [@vilmantasr])

  *Under the hood: when Windows does not inset the work area for the taskbar - some Windows 11 builds, and always with auto-hide - the widget derived a height of zero and collapsed. It now falls back to the true taskbar height and never shrinks below what the text needs.*
- **CPU temperature no longer sticks at a flat value, often around 27 °C.** It now prefers the LibreHardwareMonitor / OpenHardwareMonitor CPU sensor, and records which source and sensor it used so a bad reading is traceable. ([#216], thanks [@Jackboy001] and [@CMTriX])

  *Under the hood: the ACPI "thermal zone" was read first and won, but it is frequently a motherboard or ambient sensor sitting near room temperature, so the reading never moved under load.*
- **Cleared an antivirus false positive.** Some heuristic scanners flagged `win32evtlog.pyd`, which NetSpeedTray never used - it was bundled by accident and has been dropped. Heuristic scanners can still warn on *unsigned* builds; signed releases are unaffected. ([#135])

### Localization

- Updates to French ([@logounet]), Korean ([@VenusGirl]), Simplified Chinese ([@RainThings]) and Traditional Chinese ([@in2002-tw]), filling in the 2.1 network-identity, Location and portable-update strings.


[#135]: https://github.com/erez-c137/NetSpeedTray/issues/135
[#216]: https://github.com/erez-c137/NetSpeedTray/issues/216
[#221]: https://github.com/erez-c137/NetSpeedTray/issues/221
[@CMTriX]: https://github.com/CMTriX
[@in2002-tw]: https://github.com/in2002-tw
[@Jackboy001]: https://github.com/Jackboy001
[@Johnnym3334]: https://github.com/Johnnym3334
[@logounet]: https://github.com/logounet
[@RainThings]: https://github.com/RainThings
[@VenusGirl]: https://github.com/VenusGirl
[@vilmantasr]: https://github.com/vilmantasr

---

## [2.1.0] - July 8, 2026

Connection awareness: see *which* Wi-Fi network you are on, not just how fast. Plus Hebrew - the first right-to-left language - and Simplified and Traditional Chinese.

### Added

- **The widget can show your Wi-Fi band (2.4G / 5G / 6G), and optionally the network name.** Turn it on in **Settings → Network**. The band can show *Always*, *Color-coded*, or **Alert only** - a red `2.4G` appears solely when your PC has quietly dropped to the slow band, and the widget stays clean otherwise. It answers "did my PC rejoin 2.4 GHz after the last reconnect?" at a glance.

  > **The band needs no permission. The network name does.** Windows only reveals the SSID to apps with Location access, so choosing to show the name asks you to turn Location on. That is a Windows privacy gate, not GPS or tracking - NetSpeedTray reads the name locally to display it and never stores or sends it. A one-time explainer offers a "just show the band" option instead. See the [Privacy Policy](privacy.md).
- **Hebrew, and full right-to-left support.** The whole interface mirrors - Settings, the Monitor, menus - and the graph renders Hebrew correctly. Started from [@rami123]'s translation; the rest is an AI-assisted first pass, so native review is genuinely wanted - corrections are always welcome ([TRANSLATORS.md]).
- **Simplified Chinese**, contributed by [@RainThings]. ([#209])
- **Traditional Chinese (Taiwan)**, contributed by [@raylolhue], with terminology from [@in2002-tw] and native polish from [@tony8077616]. ([#199], [#215])
- **Japanese and Korean refreshed** by [@coolvitto] ([#205]) and [@VenusGirl] ([#207]).
- **The widget can live on a display that has no taskbar.** Pick a Preferred Monitor that Windows leaves taskbar-less - an accessory panel like the Corsair Xeneon Edge - and the widget free-floats at the bottom of that screen instead of snapping back. It stays put across sleep and monitor changes. ([#188])
- **A one-time nudge when the widget overlaps the Windows Widgets panel.** It never moves your widget for you; leave it overlapping if you prefer. ([#200])

### Changed

- **The readout sits flush against the system tray in every mode.** Cycle and single-metric layouts used to leave a gap when the reading was narrow. Spare width now tucks onto the app-icon side, where it is invisible. ([#106])

### Fixed

- **Windows menus and flyouts no longer hide behind the taskbar.** The taskbar right-click menu, Safely remove hardware, and jump lists could all slip underneath while NetSpeedTray was running. ([#200])

  *Under the hood: the widget kept re-asserting top-most on a timer, which as a side effect dragged the taskbar itself above any open menu. It now holds position structurally, with no periodic re-assert.*
- **The widget no longer crowds the "show hidden icons" chevron.** ([#161])
- **On a second monitor, the widget no longer lands on the clock.** Secondary taskbars have no system tray of their own, so there was nothing to measure from. Tunable via `secondary_clock_reserve_px` for wide date formats. ([#186])
- **No more runaway logging on taskbar-less monitors.** A "falling back to the primary taskbar" line was written every second, bloating logs and Support Bundles. ([#191])
- **The portable version can install updates.** "Download Update" launched the installer, which cannot update an unzipped folder, so nothing happened. The portable build now downloads the new version, verifies its checksum against the official release, extracts it and opens it ready to copy over - your settings in `%APPDATA%` are untouched. ([#195])
- **Scrolling the Settings window no longer changes the control under your cursor.** A control now responds to the wheel only once you click into it.


[#106]: https://github.com/erez-c137/NetSpeedTray/issues/106
[#161]: https://github.com/erez-c137/NetSpeedTray/issues/161
[#186]: https://github.com/erez-c137/NetSpeedTray/issues/186
[#188]: https://github.com/erez-c137/NetSpeedTray/issues/188
[#191]: https://github.com/erez-c137/NetSpeedTray/issues/191
[#195]: https://github.com/erez-c137/NetSpeedTray/issues/195
[#199]: https://github.com/erez-c137/NetSpeedTray/issues/199
[#200]: https://github.com/erez-c137/NetSpeedTray/issues/200
[#205]: https://github.com/erez-c137/NetSpeedTray/issues/205
[#207]: https://github.com/erez-c137/NetSpeedTray/issues/207
[#209]: https://github.com/erez-c137/NetSpeedTray/issues/209
[#215]: https://github.com/erez-c137/NetSpeedTray/issues/215
[@coolvitto]: https://github.com/coolvitto
[@in2002-tw]: https://github.com/in2002-tw
[@rami123]: https://github.com/rami123
[@RainThings]: https://github.com/RainThings
[@raylolhue]: https://github.com/raylolhue
[@tony8077616]: https://github.com/tony8077616
[@VenusGirl]: https://github.com/VenusGirl
[TRANSLATORS.md]: TRANSLATORS.md

---

## [2.0.1] - July 2, 2026

Polish on top of 2.0.0: localization fixes for the history graph, a rounding fix, a steadier hardware readout, Preferred Monitor working across multiple monitors, and a download half the size.

### Changed

- **The download is roughly half the size.** The one-folder build was packing the whole app a second time. ([#172])

### Fixed

- **The history graph now uses your language's number format.** Peak labels and axis ticks were hardcoded to an English "." and "Mbps", so a German user saw "12.3 Mbps" on the graph and "12,3 Mbit/s" everywhere else in the same session. ([#176])
- **Japanese and Korean graph labels no longer show as empty boxes.** matplotlib's default font has no CJK glyphs; the graph now picks an installed Windows CJK font. No font is bundled, so the download is unchanged. ([#173])
- **Data sizes no longer round into the wrong unit.** 999,999 bytes showed as "1000.0 KB" instead of "1.0 MB". ([#174])

  *Under the hood: the formatter rounded before checking whether the value had crossed into the next unit. It now promotes after rounding.*
- **Preferred Monitor works on multi-monitor setups.** Choosing a secondary display did nothing at all before. ([#72], thanks [@Mythos])

  *Under the hood: two separate faults. Taskbar discovery required a system-tray area, which on Windows 11 only the primary taskbar has - so every secondary taskbar was silently discarded. And even once placed, the once-a-second refresh re-resolved the primary taskbar and pulled the widget back within a frame. Every repositioning path now honors the setting.*
- **Both arrows on the plan-speed and data-cap spinboxes now work.** The text field overlapped the up button and swallowed its clicks. ([#169])
- **The Monitor's settings gear only appears on the Hardware tab**, where it actually does something. ([#170])
- **The side-by-side hardware readout stays aligned and stops sliding.** The block shifted sideways whenever a percentage ticked over (9→10, 99→100), RAM and VRAM didn't line up between rows, and memory could clip to a single digit after a language change. Each field now sits in its own fixed-width column. ([#179])
- **"Show RAM" and "Show VRAM" gray out when their monitor is off.** RAM rides on the CPU readout and VRAM on the GPU readout, so enabling either alone showed nothing.
- **Honest guidance when temperature and power aren't available.** NetSpeedTray used to tell you to run LibreHardwareMonitor as Administrator, sending you after a permissions problem that doesn't exist. ([#134], thanks [@Mythos])

  *Under the hood: LHM removed its WMI provider in v0.9.5, so `root\LibreHardwareMonitor` no longer exists in current builds - no amount of elevation brings it back. The download link now points at v0.9.4, the last WMI-capable release.*


[#72]: https://github.com/erez-c137/NetSpeedTray/issues/72
[#134]: https://github.com/erez-c137/NetSpeedTray/issues/134
[#169]: https://github.com/erez-c137/NetSpeedTray/issues/169
[#170]: https://github.com/erez-c137/NetSpeedTray/issues/170
[#172]: https://github.com/erez-c137/NetSpeedTray/issues/172
[#173]: https://github.com/erez-c137/NetSpeedTray/issues/173
[#174]: https://github.com/erez-c137/NetSpeedTray/issues/174
[#176]: https://github.com/erez-c137/NetSpeedTray/issues/176
[#179]: https://github.com/erez-c137/NetSpeedTray/issues/179
[@Mythos]: https://github.com/Mythos

---

## [2.0.0] - June 30, 2026

**The widget is now part of the taskbar rather than a window sitting on top of it**, so the Windows shell can no longer cover it. And everything beyond the taskbar moved into one new window: the **Monitor**.

> **Upgrading:** your settings and history carry over. The separate Graph and App Activity windows are gone - both live in the Monitor now, which no longer shows a per-app download/upload speed, because Windows cannot honestly measure that per app (see Changed, below). New installs now start with Windows by default; existing installs keep whatever you had.

### Added

- **The widget now docks into the taskbar itself**, so the Start menu, Quick Settings and the tray overflow can no longer cover it. It used to vanish behind them - sometimes until another window took focus - and now stays put through every shell interaction and recovers after an Explorer restart.

  *Under the hood: it was a separate top-level window competing for the same pixels. It is now an owned window of the taskbar (`GWLP_HWNDPARENT`), so Windows keeps it above. This is the "native embed" problem the project chased since v1.0 - after years of trying, the widget finally behaves like it belongs there, which is why this is a major version.*
- **The Monitor - one window with three tabs**, replacing the old Graph and App Activity windows. It is the half of the app most people never found, finally given a front door. Double-click the widget or pick it from the tray menu.
  - **Overview** - live tiles for network, CPU, GPU, RAM and VRAM with sparklines that adapt their scale, a data-usage card, and a top-talkers list. Every card clicks through to detail.
  - **Network** - the history graph over a per-app *connection* list: how many connections each program holds, how many are active, and which remote hosts it reaches.
  - **Hardware** - a combined CPU+GPU graph over live per-process CPU / RAM / GPU%, plus temperatures, power and memory.
- **Honest statistics you can export.** Click any Overview card for the real distribution - min, average, peak with percentiles, peak vs off-peak, throttle and connection-drop counts. Export the window as a `.zip` (summary CSV, raw samples CSV, JSON sidecar), or run `NetSpeedTray.exe --export-csv --period 24h --out DIR` headless.

  *Percentiles are shown only where they are exact - the live 24-hour tier - and marked "unavailable" for older rolled-up data rather than fabricated.*
- **One-click secure update.** **Download** now fetches the signed installer, verifies it in-app and runs it, instead of opening a browser. Two gates, fail-closed: Windows' own `WinVerifyTrust`, plus a pin on NetSpeedTray's SignPath Foundation certificate. Anything off and it falls back to the release page, so it is never worse than before.
- **Usage and data-cap tracking.** Daily, weekly and monthly totals, a settable monthly cap with a billing reset day, and opt-in 80% / 100% alerts shown as a calm on-widget flyout - no tray icon.

  *Under the hood: the cap reads a dedicated monotonic counter, not the sampled history, which would under-count. Alerts are debounced and restart-safe.*
- **Network latency and a plain-word verdict** - *Internet: Good / OK / Slow* - with milliseconds as subtext. Defaults to your gateway, so it stays on your LAN; a public host is strictly opt-in.
- **Native Windows 11 chrome** - dark title bars that follow your theme, rounded corners, a Fluent tab strip, styled settings controls, and a rebuilt right-click menu and Support dialog. Silently does nothing on Windows 10.
- **Live settings preview.** Settings shows an inert render of the widget on a taskbar-like strip that updates as you change font, color, arrows, layout or mode - drawn through the same paint path as the real widget, so it matches exactly.
- **Six arrow styles** plus a custom option: Classic, Solid, Compact, Outline, Outline Compact, Double.
- **Scroll to switch metrics in Cycle mode**, instead of waiting for the rotation.
- **Pause / Resume in the right-click menu**, and a hover card showing data used today and this month.
- **Configurable double-click and middle-click actions** in **Settings → General**. Contributed by [@rami123] ([#165]).
- **Survives sleep and monitor changes** - resume, KVM switches, docking and undocking - without a manual nudge.
- **An Advanced settings page** with data retention, an app-wide reduce-motion flag that also follows the Windows animation setting, and per-page or full reset (your history is kept).

### Changed

- **New installs start with Windows.** A taskbar status widget should come back after a reboot. Existing installs keep your setting; it is a toggle in **Settings → General** either way.
- **App Activity is now honest about what it measures.** It used to show per-app "Download/Upload speed" estimated from each process's *total* I/O - disk and network combined - because Windows cannot attribute network bytes per app without a driver. The Monitor reports only what it can measure exactly: live connections per app, how many are active, and the distinct hosts they reach. No estimates dressed up as speeds.

### Fixed

- **No more flicker when you click the taskbar** or switch foreground windows.

  *Under the hood: two causes. A "re-assert position when the taskbar takes focus" step had been lost in an earlier refactor, and a redundant Z-order re-promotion briefly dropped the translucent widget out of the top layer - unnecessary once docked.*
- **Fullscreen hide and show is near-instant both ways**, including apps that go fullscreen without taking focus, like double-clicking a video. It used to lag up to a second.
- **Color coding now follows the real speed, not the number on screen.** Thresholds are defined in Mbps but were compared against whatever was displayed, so in Kbps or MB/s mode the bands triggered at the wrong speeds - a 0.5 Mbps stream shown as "500 Kbps" counted as fast. Banding is now computed from the canonical speed.
- **Log files are written reliably again.** They could previously fail silently, so Support Bundles arrived with no logs at all.

  *Under the hood: `config.py` imported `logging` but not `logging.handlers`, which Python does not import automatically. Depending on import order this raised `AttributeError`, fell back to console-only logging, and produced no file.*
- **Windows now remember their position, including which monitor.** Closing Settings with Cancel or X discarded the move - only Save kept it - and a window saved on a secondary monitor was pulled back to the primary on reopen.
- **Hardware temperature and power no longer freeze at the last reading** when a sensor drops out. They now clear to N/A.
- **Taskbar text no longer truncates at very high speeds.** In always-Mbps mode with byte units the widget reserved only three digits and clipped past ~1000 MB/s. ([#106])
- **Cycle mode no longer clips CPU/GPU text** - the widget now sizes for the widest enabled phase, not just the network one.
- **Hardware-monitor detection is less strict.** A sensor source exposing only power or load, with no temperature, was rejected outright. ([#130])
- **Monitoring recovers from errors instead of dying.** Roughly ten consecutive errors used to stop it permanently and silently; it now notifies once, backs off and keeps retrying, so a driver reload or sleep/resume heals itself.
- **The Support Bundle reports the real monitor resolution on high-DPI displays**, showing native and logical together. ([#152])
- **No more stray system-wide hook at startup** when Explorer was mid-restart, which had permanently disabled the app's own restart recovery.
- **No gap at the right edge of the history graph.** Reads now wait for the most recent write to land.
- **The graph window no longer crashes while building a tab title.** It referenced a translation key that exists in no language file.

  *Under the hood: a new test now scans the code for references to nonexistent translation keys, so this class cannot come back silently.*
- **The color threshold boxes now follow your chosen unit.** They always read "Mbps", even when the widget was set to byte or binary units. Contributed by [@rami123] ([#165]).
- **Dragging the widget right of the tray no longer snaps it back.** The saved offset went negative and was clamped to zero, so the next reposition pulled it left again. Contributed by [@rami123] ([#165]).

### Performance

- **Removed duplicate signal wiring** - the display, CPU and GPU update slots were each connected twice and ran twice per tick.
- **`nvidia-smi` no longer stalls the speed readout.** That subprocess could take 1.5 s and ran every second on the same path as the network reading; it now runs on a slow sub-cadence with cached values.
- **Cheaper repaints and a quieter database.** The mini-graph hashed up to 5,000 points on every paint (now an O(1) key), the write thread blocks on its queue instead of waking every 100 ms, and a full VACUUM runs at most daily instead of every maintenance cycle.

### Localization

- **Japanese** - the tenth language - added by [@coolvitto] ([#155], [#163]), and **Korean** fixes and a 2.0 refresh from [@VenusGirl] ([#156], [#164]).
- **The entire Monitor is localized** across all 10 languages, AI-drafted for non-English and pending native confirmation ([#157]). Native corrections from [#158], [#159], [#160] and [#162] were folded in, plus a final pass of 155 fixes across the nine non-English locales.
- **CI now fails the build** if any locale drifts from the English key set or mangles a `{placeholder}`.

### Developer notes

- **First CI pipeline.** The full suite now runs on every push and PR to `main`; previously tests ran only on release tags, so a regression could land unnoticed.
- **Tests grew from 196 to 722.** Notable new coverage: the data-cap odometer and restart-safe alerts, the shared paint path and preview render-parity, the honest connection model, the recoverable circuit breaker, and the first headless GUI tests including render-pixel verification of the color bands.
- **Shared widget paint path.** The widget's drawing was lifted into one pure render function driven by a metrics snapshot, so the live widget and every preview render through identical code.
- **The Monitor reuses the existing matplotlib engine byte-for-byte** behind one lazily-loaded canvas, under an import firewall - the matplotlib-free Overview never triggers the heavy import, keeping the idle-RAM win.
- **History is no longer silently lost on the "Keep everything" setting.** A crash in retention maintenance had been stopping all history writes - Windows raises `OSError 22` on pre-1970 `datetime.timestamp()`. Fixed with safe cutoff arithmetic.


[#106]: https://github.com/erez-c137/NetSpeedTray/issues/106
[#130]: https://github.com/erez-c137/NetSpeedTray/issues/130
[#152]: https://github.com/erez-c137/NetSpeedTray/issues/152
[#155]: https://github.com/erez-c137/NetSpeedTray/issues/155
[#156]: https://github.com/erez-c137/NetSpeedTray/issues/156
[#157]: https://github.com/erez-c137/NetSpeedTray/issues/157
[#158]: https://github.com/erez-c137/NetSpeedTray/issues/158
[#159]: https://github.com/erez-c137/NetSpeedTray/issues/159
[#160]: https://github.com/erez-c137/NetSpeedTray/issues/160
[#162]: https://github.com/erez-c137/NetSpeedTray/issues/162
[#163]: https://github.com/erez-c137/NetSpeedTray/issues/163
[#164]: https://github.com/erez-c137/NetSpeedTray/issues/164
[#165]: https://github.com/erez-c137/NetSpeedTray/issues/165
[@coolvitto]: https://github.com/coolvitto
[@rami123]: https://github.com/rami123
[@VenusGirl]: https://github.com/VenusGirl

---

## [1.3.3] - June 25, 2026

A stabilization release for v1.3.2. The headline fix restores HTTPS to the packaged app - v1.3.2 accidentally shipped without OpenSSL, which broke the in-app update checker for everyone. Because that also means v1.3.2's updater cannot notify you about this release, **v1.3.2 users must update manually** from the [GitHub Releases](https://github.com/erez-c137/NetSpeedTray/releases) page or via WinGet.

### Changed

- **Installer performs a clean upgrade:** The Inno Setup installer now wipes `{app}\_internal` before copying the new build. Previously it overwrote files in place but left behind anything a newer build no longer shipped, so v1.3.1 → v1.3.2 upgraders kept stale, mismatched DLLs - which is why the OpenSSL breakage surfaced as a version mismatch on upgrade rather than an outright missing file. User data in `%APPDATA%\NetSpeedTray` is never touched.
- **Hardened numpy packaging:** numpy's compiled core (`_multiarray_umath`) and its bundled OpenBLAS are now excluded from UPX compression - a known cause of "DLL load failed while importing _multiarray_umath" (#136).

### Fixed

- **Update checker broken in v1.3.2 - "Could not check for updates" (critical):** The v1.3.2 PyInstaller build stripped Python's OpenSSL DLLs (`libcrypto-3.dll` / `libssl-3.dll`) from the bundle. The Qt-trimming filter in `netspeedtray.spec` dropped them by basename on the false premise that "urllib uses Windows SChannel" - but on Windows Python's `ssl` module is backed by OpenSSL, so `_ssl.pyd` could no longer load and **every HTTPS request failed**, most visibly the update checker. Restored the OpenSSL DLLs, excluded them (plus `_ssl`/`_hashlib`) from UPX, and added a build-time guard that fails the build if these libraries ever go missing again.
- **Crash on startup after enabling Auto-Cycling (#131):** A `constants.renderer.renderer.CYCLE_INTERVAL_MS` typo (one `renderer` too many) raised `AttributeError` while starting the cycle timer. Once "cycle" display mode was saved to config, the app crashed on every launch. Corrected to `constants.renderer.CYCLE_INTERVAL_MS`.
- **No speed shown above ~5 Gbit/s on 10GbE NICs (#154):** The per-interface speed was capped at the link speed reported by `psutil.net_if_stats()`, which is unreliable on Windows for multi-gigabit adapters (often a wrong or half value). Any real sample above that bogus cap was silently dropped, so high-speed NICs displayed a constant 0. Removed the per-NIC cap; sanity-checking now relies on the absolute 100 Gbps ceiling plus the existing rolling-average spike filter.
- **Color coding showed the Low Color at idle and only applied after a restart (#153):** Two fixes. (1) Color and threshold edits now apply live - `update_config()` rebuilds the cached pens instead of leaving them stale until the next restart. (2) The bands are now ascending: below the Low threshold (including idle) uses the Default Color, between Low and High uses the Low Color, and above High uses the High Color - so the widget matches the tray's default color at rest. Threshold tooltips were updated to match in all 9 languages.
- **Widget vanished over fullscreen apps even with "keep visible" enabled (#107):** The emergency immediate-hide path that triggers on unambiguous fullscreen windows ignored the `keep_visible_fullscreen` setting that the normal visibility refresh already respected. It now honors the setting.
- **AMD Ryzen CPU temperature not detected (#148):** LibreHardwareMonitor exposes the Ryzen CPU temperature as `Core (Tctl/Tdie)` rather than `CPU Package`. CPU-temperature matching now also keys off the sensor's LHM identifier (`/amdcpu/`, `/intelcpu/`) and recognizes the `Tctl` / `Tdie` / `Tccd` labels.
- **Settings window scrollbars:** The Settings window now auto-sizes its width to the widest page's content (measured with the actual on-screen fonts) and is a bit taller, so the General page no longer shows a horizontal *or* vertical scrollbar. Dropdowns also no longer balloon to the width of their longest item.

### Localization

- Updated the High/Low Speed threshold tooltips to describe the new ascending-band behavior across all 9 supported languages (English, German, Spanish, French, Korean, Dutch, Polish, Russian, Slovenian). 100% locale key parity preserved.
- Filled in the UI strings that were still showing in English for German, Spanish, French, Dutch, Polish, Russian, and Slovenian - mainly the App Activity window, the Support Bundle export, and the Preferred Monitor setting. These are AI-assisted translations pending native-speaker review (clearly noted as such in `TRANSLATORS.md`, and **not** attributed to the human translators); placeholder/format safety was validated automatically.

### Developer notes

- Added `test_v1_3_3_regressions.py` pinning the #131 startup crash, the #154 high-speed-drop bug, and the #153 live-apply fix. Suite now at **196 passing tests**.


---

## [1.3.2] - June 2, 2026

### Added

- **Preferred Monitor (#72):** A new dropdown in Settings → General lets users pin the widget to a specific monitor in multi-monitor setups instead of always landing on the primary taskbar. The setting stores the screen's stable Windows identifier (`\\.\DISPLAY1`), and gracefully falls back to primary if the saved monitor is no longer connected.
- **Export Support Bundle:** Replaces the "Export Error Log" button in Settings → Troubleshooting. Bundles all log files (current + rotated backups), the user's `config.json`, and a `system_info.txt` (NetSpeedTray version, Windows version, Python, monitor count + resolutions - no display names, no hostname) into a single timestamped zip ready to drag into a GitHub issue. Log content is run through the obfuscator one extra time before zipping as belt-and-suspenders against any future logging-setup mistakes. App Activity per-process / per-connection data is never included.
- **GitHub Issue Templates:** Bug-report and feature-request templates now require the right context up front (version, Windows build, monitor layout, attached Support Bundle) so triage doesn't stall waiting on follow-up questions. Blank issues are disabled - general questions are now directed to Discussions.
- **TRANSLATORS.md:** Credits the contributors who have translated the UI (Korean: @VenusGirl, Dutch: @CMTriX, Russian: @ZeoNish, Slovenian: Andrew Poženel).

### Changed

- **Live Theme Detection (#62):** The widget now updates its text color the moment Windows switches between Light and Dark mode, instead of waiting for the next app restart. Uses Qt 6.5+'s `colorSchemeChanged` signal in place of the previous WM_SETTINGCHANGE native event filter, which fired on every system setting change (mouse, language, accessibility). Runtime theme changes are applied in-memory only - flipping themes no longer churns the config file on disk. Only affects users with "Automatic" text color enabled (the default).
- **Lower RAM at idle:** Moved matplotlib's `use('QtAgg')` setup out of `monitor.py` into `views/graph/window.py` and made the `from .views.graph import GraphWindow` lazy via `__getattr__` in `views/__init__.py`. Also deferred numpy import inside the one helper function that uses it (mini-graph curve interpolation). Result: matplotlib + numpy + PIL no longer load at startup. Users who never open the graph window see idle RAM drop from ~135 MB to ~40-75 MB depending on hardware (a 45-70% reduction in working-set memory).
- **Smaller installer (106 → 81 MB, -24%) and portable zip (127 → 91 MB, -28%) (#143):** Trimmed the PyInstaller bundle by excluding Qt subsystems we don't import (QtNetwork, QtPdf, Quick/QML, Multimedia, WebEngine, Sql, Designer, Charts, Test, etc.), Pythonwin's MFC runtime (`mfc140u.dll` ~5 MB), and unused PIL image-format codecs (AVIF, HEIF, etc.). Added UPX compression - auto-downloaded into `build/tools/` by the build script if not present.
- **Log levels for field diagnosis:** Bumped four state-transition logs from DEBUG to INFO so bug-report logs include the breadcrumbs we need without users having to enable verbose logging. Affects `StatsMonitorThread` (init + polling interval changes + hardware monitor connection + run loop start) and `StatsController` (init mode + primary interface changes). Production logs are marginally larger; the additions fire once or only when state actually changes, so volume stays low.

### Fixed

- **Free-Move Widget Reverts to Primary Screen After Reboot (#133):** The saved free-move position is now validated against the screen it actually belongs to (via `QApplication.screenAt()`) instead of the primary screen's taskbar. Previously, dragging the widget to a secondary monitor and restarting would snap it back to the primary screen because the saved coordinates were rejected as "off-screen" by the primary-screen validator. If the original monitor has since been disconnected, the widget now falls back to its calculated position near the tray instead of remaining off-screen. Position-restore decisions are now logged at INFO level for easier field diagnosis.
- **Settings dialog rendering on Windows 10 (#149):** Group-box titles ("Font Settings", "Arrow Styling", etc.) were getting clipped at the top, and labels appeared washed-out on dark mode. Root cause: the stylesheet referenced `Segoe UI Variable` (a Windows 11-exclusive font) without a fallback, so on Windows 10 Qt picked an unrelated default font with different metrics. Added the standard fallback chain `'Segoe UI Variable', 'Segoe UI', sans-serif` across all 11 stylesheet usages and the two `QFont()` constructors that hardcoded the family. Also bumped QGroupBox `margin-top` from 12px to 22px so the bold 14px title has room to render without clipping, even with the slightly taller Segoe UI Variable metrics on Windows 11.

### Security

- **PII obfuscator hardened (#141):** Audited and strengthened the log redaction layer (`ObfuscatingFormatter`) to cover six previously-leaking categories: compressed IPv6 (`::1`, `2001:db8::1`, `fe80::abcd:1234%5` with zone IDs - the previous regex only matched the rarely-used full 8-group form), forward-slash Windows paths (`pathlib.Path` repr leaks username on Windows), hostname / computer name, MAC addresses (colon and dash forms), and Windows network interface GUIDs. Each redaction uses a distinct sentinel (`<REDACTED_IP>`, `<REDACTED_MAC>`, `<REDACTED_GUID>`, `<REDACTED_PATH>`, `<REDACTED_HOST>`) so log readers can tell what was scrubbed. The console handler now uses the same obfuscator (was plain `Formatter`). Dead `helpers.setup_logging` was removed to prevent future contributors from accidentally wiring up non-obfuscated logging. Test coverage went from 0 to 31 unit tests on the formatter, including idempotency and catastrophic-backtracking guards.

### Localization

- **Korean translation polish (#139, closes #122):** Merged @VenusGirl's terminology updates (`라벨→레이블`, `라이브→실시간`, `공격적→적극적`, `피크→정점`), full Korean translations for the App Activity window and support menu strings, and fixed two bugs found during merge (a typo in `CHECK_FOR_UPDATES_MENU_ITEM` and a regression that left `Current:` / `Latest:` in English).
- 100% locale key parity preserved across all 9 supported languages (English, German, Spanish, French, Korean, Dutch, Polish, Russian, Slovenian).

### Developer notes

- Unit suite grew from **146 → 191 passing tests** (+45 new tests). New coverage includes: PII obfuscator (31 tests), Support Bundle (9 tests), multi-monitor position-restore regressions (5 tests), and live theme-change signal wiring.


---

## [1.3.1] - April 15, 2026

### Added

- **Update Checker:** The app now checks for new releases via the GitHub API on startup (every 24 hours) and offers a "Check for Updates" option in the right-click menu. Users can download the latest version, skip a specific release, or disable the check entirely in Settings > Behavior.
- **Support Dialog:** Added a "Support this Project" menu item with links to GitHub Sponsors, Ko-fi, Buy Me a Coffee, and Star on GitHub.
- **LibreHardwareMonitor Notice:** When temperature or power readouts are enabled but no data source is detected after startup, a one-time notification explains that LibreHardwareMonitor (or OpenHardwareMonitor) is required and links to the download page.
- **RDP Session Detection:** Automatic detection of Remote Desktop sessions via `GetSystemMetrics(SM_REMOTESESSION)`. GPU monitoring is skipped and App Activity displays an informational message instead of attempting unreliable psutil queries in virtualized environments.

### Changed

- **Context Menu Grouping:** Right-click menu items are now organized into logical groups with separators (windows / updates & support / exit) for easier scanning.
- **Global Window Icon:** All application windows and dialogs (including update and support popups) now display the NetSpeedTray icon in the title bar.
- **README Overhaul:** Rewrote the README to reflect all v1.3.0/v1.3.1 features including hardware monitoring, App Activity, display modes, and RDP detection. Moved the Support section above Building from Source for better visibility.

### Fixed

- **App Crash in RDP (Windows Server):** GPU polling errors are now caught and logged independently without incrementing the circuit breaker's consecutive error counter, preventing GPU failures from killing the entire monitor thread and crashing the app.
- **App Sluggishness in RDP:** Wrapped `psutil.net_connections()` in a daemon thread with a 2-second timeout to prevent the App Activity window from stalling the 1-second polling loop in RDP and low-privilege environments.
- **Graph Render Crash (`datetime` type error):** Fixed `float() argument must be a string or a real number, not 'datetime.datetime'` by requesting raw numeric timestamps from the database and adding a defensive type guard before numpy conversion. Also applied to the Overview tab's database-backed path.
- **Color Coding Always Yellow/Orange:** Fixed color coding failing on configs migrated from v1.2.6 by replacing the broken threshold repair logic (which set `low = high`, still leaving the band unreachable) with a cross-field guard that resets both thresholds to defaults when the `low < high` invariant is violated.
- **Swap Upload/Download Checkbox:** The "Swap upload/download" setting now correctly swaps both the speed values/units and arrow icons together. Previously only the arrow icon was swapped while upload and download values always rendered in fixed positions.
- **Vertical Taskbar Layout:** Widget layout mode is now determined from the taskbar edge position (Left/Right edge → horizontal layout, Top/Bottom edge → vertical layout) instead of the unrelated `is_small_taskbar()` height check, fixing broken widget rendering on vertical taskbars.
- **Config Migration Resetting Display Mode:** Removed `side_by_side` from the display mode downgrade rule - it gracefully degrades to network-only at render time and no longer silently resets to `network_only` when hardware monitors are disabled. Also cleaned up dead `cpu_only`/`gpu_only` branches that could never trigger and tightened the `cycle` downgrade condition to gate only on CPU/GPU (not RAM/VRAM).
- **Widget Flickering:** Eliminated per-paint Win32 taskbar enumeration by caching the layout mode and refreshing it only on taskbar geometry changes. Also removed redundant `setRenderHint(Antialiasing)` calls in the icon and mini-graph paint paths.
- **High-DPI Widget Clipping:** The `MAX_WIDGET_WIDTH_PX` / `MAX_WIDGET_HEIGHT_PX` safety constraints are now scaled by the display's DPI factor, preventing text truncation on 4K and other high-DPI screens.
- **Dialog Close Hiding Widget:** Fixed all popup dialogs (update checker, support, LHM notice) using `parent=None`, which caused closing the dialog to also hide the widget. Dialogs now correctly parent to the widget.
- **nvidia-smi Console Flash:** Hidden the console window that briefly appeared every poll cycle when using `nvidia-smi` for GPU temperature/power readings.
- **Widget Spacing with Temp/Power:** Tightened the layout width reference strings for temperature and power suffixes, reducing excess gap between the widget and the system tray when hardware readouts are enabled.

### Security

- Updated Pillow, pytest, and Pygments dependencies for CVE fixes.

### Known Limitations

- Temperature readings are displayed in °C only. Fahrenheit support is planned for v1.4.0.


---

## [1.3.0] - April 14, 2026

### Added

- **App Activity Window:** Added an App Activity window (accessible from the tray menu) to view estimated per-app network activity (includes a non-admin mode with reduced accuracy).
- **Hardware Monitoring:** Added CPU/GPU utilization tracking and optional RAM/VRAM readouts (vendor-agnostic GPU support via Windows Performance Counters / PDH).
- **Optional Temperature Readouts:** Added a widget toggle to show CPU/GPU temperatures when available. Sources are tried in priority order: LibreHardwareMonitor/OpenHardwareMonitor WMI (all vendors, requires admin), `nvidia-smi` (NVIDIA GPU), and Windows PDH Thermal Zone / WMI ACPI (CPU fallback).
  > **Note:** CPU and GPU temperatures require a kernel-level driver on most modern hardware. Install [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) and run it as Administrator - NetSpeedTray will detect it automatically. NVIDIA GPU temperature also works natively via `nvidia-smi`.
- **Optional Power Readouts:** Added a widget toggle to show CPU/GPU power draw in Watts. Uses Intel RAPL via PDH (non-admin) for CPU, `nvidia-smi` for NVIDIA GPU, and LibreHardwareMonitor/OpenHardwareMonitor for all GPU vendors.
- **Graph Window Hardware Views:** Added CPU and GPU history tabs, plus a refreshed Overview tab with live-updating, synchronized Network/CPU/GPU charts and quick at-a-glance stats.
- **Widget Layout Modes:** Added Side-by-Side and Cycle display modes, a stacked CPU+GPU column option, and per-segment display ordering (Network/CPU/GPU/None).
- **Context Menu Graph Access:** Added "Show Graph" to the right-click context menu for easier graph window discovery (previously only accessible via double-click).
- **Hardware Data Retention:** Added hourly aggregation tier for hardware stats (matching speed data's 3-tier architecture: raw 24h → minute 30d → hour user-configurable), preventing data loss after 30 days.
- **LibreHardwareMonitor Integration:** Auto-detects a running LibreHardwareMonitor or OpenHardwareMonitor instance via WMI, enabling temperature and power readings across all GPU vendors. The probe retries each poll cycle, so LHM can be started after NetSpeedTray.

### Changed

- **Settings UI Redesign:** Consolidated settings pages from 8 to 6. Merged Mini Graph into Appearance, Troubleshooting into a footer Export Log button, and moved Tray Offset to General (renamed "Options" → "Behavior"). Removed the AdaptiveStackedWidget in favor of a fixed-size dialog with per-page scroll areas to eliminate resize oscillation and taskbar overlap on 1080p.
- **Collapsible Hardware Settings:** Replaced the Hardware page's three QGroupBoxes with CollapsibleSection accordion widgets (Hardware Monitoring expanded, Widget Display Mode and Display Order collapsed by default) to reduce visual density on small screens.
- **Win11 Flat Card Styling:** Removed QGroupBox borders across all settings pages, keeping subtle background + border-radius for a modern Windows 11 flat card aesthetic.
- **Interface Mode Subtitles:** Added visible subtitle descriptions below each monitoring mode radio button (replacing hard-to-discover tooltips) explaining what each mode does.
- **Clearer Labels:** Renamed "Force MB Display" → "Always Show Megabytes", "Stacked Column" → "Stacked", "Cycle All" → "Auto-Cycle", "All Physical Interfaces" → "Physical Adapters Only", "All Interfaces (including virtual)" → "All Adapters", and "Label Style" → "Indicator Style".
- **Hardware Settings UI:** Expanded the Hardware settings page with clearer Windows 11-style controls (hardware toggles, label style, widget mode, display order, temperature and power options).
- **Graph Window Layout:** Unified spacing/padding and status-bar placement across tabs for a more consistent Windows 11 look and feel.
- **Widget Layout:** Improved width budgeting for multi-segment modes and increased spacing between the Network and CPU/GPU sections.
- **Localization:** Translated all new UI keys (hardware power, monitoring mode subtitles, collapsible section titles) across all 9 supported locales. Maintained strict key parity via `test_locales_parity`.
- **Log Noise Reduction:** Hardware monitor "0 temperature sensors" INFO message now only appears once per namespace per app session instead of every poll cycle.
- **Updated Defaults:** Adjusted default configuration to better match recommended settings: font weight Normal (400), separate arrow font enabled, decimal places 1, side-by-side widget mode, monochrome hardware icons, hardware temps enabled, stacked stats enabled.
- **GPU Polling Refactor:** Replaced the opaque GPU poll 4-tuple with a structured `GpuPollResult` NamedTuple for clearer code and extensibility (now includes power field).

### Fixed

- **Tray Offset Y Default:** Fixed a copy-paste bug where the Y-offset fallback incorrectly used the X-offset default constant.
- **Hardware Aggregation Idempotence:** Changed hourly hardware stats aggregation from `INSERT OR IGNORE` to `INSERT OR REPLACE` to prevent silent data loss if maintenance re-runs after a crash.
- **COM/WMI Resource Cleanup:** Added proper `CoUninitialize()` and WMI object release in the monitor thread shutdown path to prevent COM reference leaks.


---

## [1.2.6] - February 21, 2026

### Added

- **High-DPI Alignment Fix:** Improved vertical centering of the widget on Windows 11 high-DPI displays by accurately calculating the visible taskbar region.
- **Widget Visibility Constraints:** Added `MAX_WIDGET_WIDTH_PX` (500) and `MAX_WIDGET_HEIGHT_PX` (100) constraints to prevent the widget from growing too large and becoming inaccessible.
- **Graph Peak Tag Improvements:** Lowered the horizontal flip threshold (0.88 -> 0.8) and improved vertical alignment to prevent peak labels from being cut off on graph edges.
- **Unit Testing:** Added `test_renderer_logic.py` and updated `test_position_manager.py` to ensure regression-free positioning and rendering.

### Changed

- **Settings UI Enhancement:** Increased the Settings Dialog minimum size to 650x560 to ensure compatibility with high-DPI screens and longer translations.
- **Smart Updates Constraint:** Unified "Force MB" and "SMART" update rate logic; SMART mode is now automatically disabled when "Force MB" is off to prevent unit-switching jitter.
- **Layout Precision:** Fixed layout width calculations in `WidgetLayoutManager` to correctly respect the `short_unit_labels` setting, preventing text truncation.
- **Positioning Robustness:** Enhanced `PositionCalculator` with unified property calculation and improved error handling for temporary taskbar detection failures.
- **Settings Consolidation:** Merged arrow styling settings into the Appearance page for a more streamlined configuration flow.
- **Log Noise Reduction:** Reduced log chatter by changing "Spike detected" messages from `WARNING` to `DEBUG` level.

### Fixed

- Resolved an issue where the widget could "disappear" or be positioned incorrectly when used with very large fonts or small taskbars.
- Fixed a rare race condition where the widget would fail to re-anchor correctly after a shell restart or display change.
- Corrected peak tag positioning in corner cases (top-right and top-left peaks).


---

## [1.2.5] - February 20, 2026

This is a major stability and reliability release that resolves several critical bugs, preventing data loss, off-screen widgets, and visual distortions. It also introduces new features and a massively expanded test suite to ensure flawless performance on a wide range of hardware.

### Added

-   **Keep Widget Visible in Fullscreen (#107):** Added a new option (`keep_visible_fullscreen` in General settings) to keep the widget visible during fullscreen applications (e.g., games, F11 browser mode). This is disabled by default.

### Fixed

-   **Phantom Spike Elimination:** A comprehensive, multi-layered defense system has been implemented to eliminate "phantom" speed spikes and ensure accurate graph statistics.
    -   **Problem:** OS scheduling jitter and statistical anomalies caused brief, impossible speed readings, distorting graph scales and averages.
    -   **Solution:**
        -   **Time-Delta Clamping:** Increased minimum time difference to 10ms to filter out scheduling artifacts (`constants/network.py`).
        -   **Statistical Outlier Filtering (IQR):** Added Interquartile Range (IQR) filtering to remove statistical spikes from graph data before rendering (`views/graph/logic.py`).
        -   **Historical Spike Detection:** Implemented a rolling average checker in the main controller to detect and clamp sudden spikes inconsistent with recent traffic (`core/controller.py`).
        -   **Intelligent Y-Axis Scaling:** The graph's Y-axis now uses a 95th percentile calculation to prevent single spikes from squashing the entire visualization (`utils/widget_renderer.py`).
    -   **Impact:** Over 99.5% of speed spikes are eliminated or masked, resulting in accurate and readable graphs.

-   **Hardware-Aware Link Speed Clamping:** Introduced a dynamic clamping system that respects the actual capabilities of your network adapter.
    -   **Problem:** Fallback limits were static (10 Gbps), potentially allowing invalid readings on 1 Gbps lines or conversely capping performance for users with 10 Gbps or 100 Gbps hardware.
    -   **Solution:** The controller now queries `psutil.net_if_stats()` to determine the negotiated physical link speed and uses it as a hard ceiling (plus a 5% jitter margin) for data validation.
    -   **Future-Proofing:** Raised the absolute fallback ceiling to 100 Gbps for virtual adapters where link speeds cannot be queried.

-   **Historical Graph Data Fixed:** Corrected a critical bug where historical graph timelines (24H, Week, Month, All) would show no data.
    -   **Problem:** The data query logic only checked single aggregated tables (e.g., `speed_history_minute`), ignoring fresh, un-aggregated raw data.
    -   **Solution:** Refactored `WidgetState.get_speed_history()` to construct **multi-tier UNION queries**. These queries combine data from raw, minute, and hour tables (`speed_history_raw`, `speed_history_minute`, `speed_history_hour`) to ensure a complete and accurate dataset is always returned.
    -   **Impact:** All timeline views now display data correctly, eliminating the empty or stale graphs seen in previous versions.

-   **Invisible Widget Prevention:** Fixed a critical bug where the widget could become oversized and positioned off-screen, making it invisible.
    -   **Problem:** No upper bounds were enforced on widget size, allowing a positioning bug to create an invisible, 2000px+ widget.
    -   **Solution:** Introduced `WidgetConstraints` with maximum width/height (`constants/ui.py`) and implemented size clamping in `PositionCalculator.calculate_position()` to ensure the widget always remains within safe, visible screen bounds.
    -   **Impact:** Prevents a catastrophic UX failure where the widget would disappear completely.

-   **Configuration Safeguards:** Hardened the config migration process to prevent silent failures and data loss.
    -   **Problem:** Invalid version strings in the config would cause migration logic to fail silently, risking user settings.
    -   **Solution:** Updated `_version_less_than()` to raise a custom `ConfigError`. The migration process (`_migrate_config()`) now catches this error, logs it, and safely resets the configuration to prevent corruption.
    -   **Impact:** Ensures user settings are safely migrated or reset, preventing silent data loss during upgrades.


-   **Graph & UI:**
    -   Removed a non-functional "Legend" toggle that could cause crashes in dual-graph mode. (#100)
    -   Fixed an issue where the graph settings panel would shrink when toggled. (#103)
    -   **Fixed Y-Axis Label Clipping:** Increased graph left-margin from 8% to 12% to prevent "Download" labels from being cut off on high-speed connections.
    -   **Corrected Graph Peaks:** Fixed a data mismatch where raw database spikes (pre-filtering) were incorrectly plotted despite being filtered from the text stats.
    -   Corrected widget positioning on high-DPI displays (125%/150%) to fix cumulative rounding errors. (#104)
    -   Fixed text truncation and misalignment when using the `short_unit_labels` setting. (#106)
-   **Multi-Monitor & Taskbar:**
    -   The widget can now be dragged freely across multiple monitors while in "free-move" mode. (#102)
    -   Added intelligent font scaling to improve readability on narrow vertical taskbars. (#99)

### Localization

-   **Korean (ko_KR):** Updated with improved phrasing and technical terminology (Thanks @VenusGirl, PR #101).

### Developer notes

-   **Massively Expanded Test Suite:** Added over **50 new unit tests** to lock in stability for critical systems.
    -   **Developer Experience:** Added `build-exe-only.bat` to the repository, allowing developers to quickly compile the standalone executable without the overhead of building the full setup installer.
-   **Positioning (`test_positioning_edge_cases.py`):** Added 20 focused tests covering ultrawide displays (21:9, 32:9), mixed-DPI transitions, multi-monitor boundaries, and extreme resolutions from 800x600 to 8K.
    -   **Configuration (`test_config.py`):** Added 7 new tests for version validation to prevent invalid or corrupt settings from being saved.
    -   **Widget Sizing:** Added 4 new tests to validate widget dimension constraints.
-   **Code Health & Refactoring:**
    -   Improved `PositionManager` for better maintainability.
    -   Extracted over 20 hardcoded rendering values to tunable constants in `RendererConstants`.
    -   Added a `threading.Lock` to the gradient cache in `GraphRenderer` to prevent race conditions.
    -   Refactored a nested function into a standalone `_process_plot_segment()` method for better testability.



---


## [1.2.4] - February 2, 2026

### Changed

*   **Locked Graph Layout:** Standardized subplot margins (8% left) so the "graph box" width remains identical across all views, regardless of Y-axis label length. No more "jumping" grid boxes.
*   **Trailing Bridges to "Now":** Added dashed bridging lines that connect the last recorded data point to the current time, ensuring the graph always feels active and fills the entire X-axis.
*   **Strict Edge Alignment:** Eliminated horizontal margins (`xmargin=0`) so data flows perfectly from the exact left to the exact right of the grid.
*   **High-Res Time Labels:** Automatically switches to seconds resolution (`%H:%M:%S`) for extremely short ranges, preventing repeating labels in the Session view.
*   **Multi-Layer Peak Markers:** Refined glowing indicator dots for Max and Peak speeds with a three-layer glow effect and magnetic snapping.
*   **Integrated Graph Status Indicator:** Replaced overlapping "No Data" overlays with a professional, three-state status light (LIVE/LOAD/NO DATA) integrated into the stats bar.
*   **Refined Data Retention UI:** Integrated the duration label directly into the Windows 11 slider handle and implemented conditional database size display (visible only for the "ALL" duration).

*   **Vertical Taskbar Support (#99):** Intelligent font-scaling engine for vertical taskbars with automatic text shrinking.
*   **Geometry Debouncing:** High-frequency coordinate filter eliminates redundant UI operations.
*   **Intelligent Drag Persistence:** Smart anchoring remembers relative offset from taskbar tray.
*   **Window State Persistence:** Settings Menu and Graph Window remember their last screen positions.

*   **Instant Timeline Pills:** Replaced the legacy slider with a modern segmented button array: `SESS | BOOT | 24H | WEEK | MONTH | ALL`.
*   **Optimized Defaults:** The graph now defaults to the **24H** timeline, identified as the most useful starting point for most users.
*   **Window State Persistence:** Settings and Graph windows now remember their last screen positions across app restarts.

*   **O(log n) Lookup:** Implemented binary search for nearest-point finding, ensuring fluid tooltip movement even on high-resolution displays.
*   **Blitting Performance:** Cached static background with dynamic artist redraws for a smooth 60 FPS interaction experience.

### Fixed

*   **Explicit Exit Logic (#98):** Fixed a critical bug where closing the settings or graph windows could inadvertently shut down the entire application.
*   **Y-Axis Sticky Logic:** Integrated "Sticky Top" scaling that prevents the Y-axis from jittering when speed fluctuates slightly, while still adapting to huge spikes.
*   **Thread-Safe Cleanup:** Hardened the exit sequence for database workers and background monitors to ensure zero "dangling" processes on shutdown.

### Performance

*   **Smart Downsampling (10K+ Fix):** Implemented stride-based downsampling that caps graph data at 2,000 points. Switching to long timelines like **BOOT** or **ALL** is now near-instant even with years of data.
*   **Adaptive Gap Detection:** Resolved a UI freeze where large gaps in data caused redundant rendering loops. The system now automatically adjusts gap sensitivity based on data density.
*   **Database Schema v3 Migration:** Upgraded the internal SQLite schema to Version 3. This includes:
    *   **Covering Indexes:** New `idx_minute_covering` and `idx_hour_covering` indexes serve graph queries directly from memory for maximum performance.
    *   **Advanced Metadata:** Added granular tracking for database creation time and improved migration safety with automated backups.
*   **Instant Switch UI:** Added immediate graph clearing and a "Loading..." indicator when switching timelines to provide better feedback during data retrieval.

### Localization

*   **Korean (ko_KR) Refinement (#97):** Major update with idiomatic phrasing and improved technical terms. (Thanks @VenusGirl!)
*   **Universal Locale Parity (#90):** Synchronized all 9 supported languages with new descriptive tooltips and technical parity.



---


## [1.2.3] - January 29, 2026

This release addresses the remaining critical bug reports tracked in the v1.2.x series, focusing on graph performance and accuracy, settings UX, and rendering glitches.

### Changed

*   **Custom Arrow Styling:** Added granular control over arrow font family and size, independent of the speed value text.
*   **Settings Menu Streamlining:** Merged the standalone "Arrows" tab into the "Appearance" page, simplifying the navigation hierarchy (Visuals vs. Data rules).
*   **Modernized Mini-Graph:** Updated the widget's background graph to use a premium "Area Chart" style with gradient fills, smoother lines (antialiased), and improved Z-ordering (graph now properly sits behind text).
*   **Streamlined Interface Selection:** The specific interface list is now hidden by default to reduce visual clutter, automatically appearing only when "Select Specific Interfaces" is chosen.
*   **Enhanced Control Visibility:** Fixed rendering issues where radio buttons and checkboxes could become invisible in certain themes. Implemented custom, high-contrast circular styling for all radio buttons to ensure perfect visibility.
*   **Visual Polish:** Resolved transparency artifacts (black backgrounds) in the interface selection list.
*   **Adaptive Text Spacing:** Optimized the whitespace between arrows and speed values. Units like `MiB/s` now use tighter, cleaner spacing (3-digit reservation), while `Mbps` maintains a safe buffer for Gigabit speeds to prevent layout jitter.
*   **Windows 11 Slider Styling:** Updated all sliders to match the modern Windows 11 Fluent Design with thinner borders, precise handle sizing, and better hover states.
*   **Restored Speed Color Coding (#90):** Brought back full customization for color coding thresholds and colors in the Appearance settings.
*   **Smart Settings Auto-Resize:** The settings window now dynamically expands when toggling features and intelligently repositions itself upward if the expansion would go behind the taskbar on 1080p screens.
*   **Enhanced Font Weight Slider:** Replaced raw number inputs with a descriptive slider (e.g., "Regular", "Bold", "Extra Black") for easier configuration.

### Fixed

*   **Startup Path Safety:** Fixed a critical issue where launching via Registry could fail due to incorrect working directory resolution (`cwd` correctness).
*   **Dev-Mode Protection:** Added safeguards to `startup_manager` to prevent development instances from accidentally overwriting production registry keys.
*   **Critical Font Crash Resolved:** Fixed a regression where interacting with the font selection dialog would crash the application due to a return-value swap (passing a boolean instead of a font object).
*   **Defensive Type Checking:** Added explicit `isinstance` checks and `try...except` blocks in the settings application layer to prevent future crashes from malformed configuration data.
*   **Widget Rendering Glitches:**
    *   **Hide Arrows (#84) & Unit Suffix (#86):** Fixed rendering logic that ignored these "Show/Hide" preferences in certain layout modes.
    *   **Font Style Visibility (#88):** Resolved a bug where changing font styles could cause the widget to disappear.
    *   **Font Weight Scaling (#89):** Added robust support for legacy string values (e.g., "bold", "normal") when loading weights from older configuration files.
*   **Positioning Stability:**
    *   **Free Move Snapping (#87):** Refined the "Free Move" logic to ensure the widget is correctly constrained to screen bounds.
    *   **Tray Offset (#92):** Validated and fine-tuned the widget's offset calculations relative to the system tray.
*   **Graph Settings Sliders (#93):** Replaced editable text boxes in graph settings with read-only labels for "Timeline" and "Retention" sliders to prevent invalid input.

### Performance

*   **Critical Memory Leak Fix:** Resolved an issue where graph tooltips were creating orphan `QLabel` widgets on every timeline switch. Added explicit `deleteLater()` cleanup to prevent memory accumulation over prolonged sessions.
*   **Strict Rendering Caps:** Enforced a hard 800-point limit for all graph views (including 'Session'). The downsampling algorithm is now universally applied, preventing UI thread freezes during high-volume data rendering.
*   **Robust Date Handling:** Added failsafes around the plot date conversion loop to prevent renderer crashes even if data capping fails.
*   **Zero-Latency Interactions:** Implemented a dual-timer debouncing system (150ms for data updates, 500ms for configuration saves) that prevents UI "freezing" when rapidly adjusting sliders or filters.
*   **Intelligent Database Tier Selection:** Rewrote data retrieval to automatically select the most efficient single table (`raw`, `minute`, or `hour`) based on the requested time range, eliminating the overhead of complex `UNION ALL` queries for common views.
*   **Graph Caching & Smart Updates:** Implemented efficient in-place data updates for the "Live Session" graph, significantly reducing CPU usage during active monitoring.
*   **Improved Aggregation Accuracy:** Fixed a long-standing "binning shift" where data points in long-term views were forced to the start of their time window. Plot points are now rendered at the **mean timestamp** of their respective bins, providing a more accurate representation of the data.
*   **System Uptime Precision:** Resolved an issue where the "System Uptime" timeline showed inconsistent X-axis labels; it now correctly uses boot-time synchronization and high-precision locators.
*   **Obsolete Request Cancellation:** The background data worker now checks for newer request IDs and instantly skips processing for stale requests, ensuring the UI remains responsive under heavy interaction.

### Localization

*   **Korean (ko_KR):** Major update with idiomatic phrasing and improved technical terms (Thanks @VenusGirl for the contribution! #95)
*   **French (fr_FR):** Updated translation with corrections and improvements (Thanks @logounet for the contribution! #94)
*   **Multi-language Audit:** Synchronized and updated missing terms across all supported languages (Russian, Spanish, Dutch, Polish, Slovenian).

### Developer notes

*   **Modular Settings Architecture:** Decomposed the monolithic settings dialog into dedicated page classes (`AppearancePage`, `GraphPage`, etc.) located in a new `pages/` sub-package for significantly better maintainability.
*   **Core Logic Decoupling:** Extracted complex logic from `NetworkSpeedWidget` into specialized controllers:
    *   **`ConfigController`:** Now handles the entire settings lifecycle, including loading, saving, and rollbacks.
    *   **`InputHandler`:** Centralizes all mouse and keyboard events, separating user interaction from display logic.
*   **Adaptive Component Design:** Implemented a custom `AdaptiveStackedWidget` that allows the settings window to resize dynamically based on the current page's content.
*   **Throttled Configuration Engine:** Added a `QTimer`-based throttling mechanism to prevent redundant disk I/O when making rapid adjustments in the settings menu.

*   **Reduced Log Verbosity:** Demoted over 30 redundant initialization and routine cleanup messages from `INFO` to `DEBUG`, resulting in much cleaner and more readable log files.
*   **Architectural Polish:**
    *   **Shadowed Property Fix:** Renamed `self.font` and `self.metrics` to `self.current_font` and `self.current_metrics` to prevent internal conflicts with native `QWidget` methods.
    *   **Deduplication:** Removed a redundant second implementation of the mini-graph rendering logic.
*   **DRY Consolidation:** Centralized all timeline duration logic, aggregation thresholds, and resolution rules into `constants/data.py` to ensure consistency between the database layer and multiple UI components.
*   **Dead Code Cleanup:** Removed legacy startup registry logic from the main widget that was left over from recent architectural refactors.


---

## [1.2.2] - January 29, 2026

This is a hotfix release addressing immediate UI and stability issues reported after v1.2.1.

### Fixed

*   **Settings Window Stability (#81):** Enforced a minimum size of `620x500` for the settings dialog to prevent layout breakage and hidden sidebar items.
*   **Log Cleanup (#83):** Synchronized UI configuration keys with the backend schema to eliminate "ignoring unknown fields" warnings.
*   **I18n Parity (#82):** Added missing `FIXED_WIDTH_VALUES_LABEL` to the English locale to resolve start-up validation warnings.
*   **Dev Mode Silence:** Suppressed the "Startup key path mismatch" warning when running from source or virtual environments.


---

## [1.2.1] - January 29, 2026

This is a major stable release that combines significant performance overhauls with critical stabilization fixes. It introduces vectorized graph processing, a modular settings architecture, and definitive fixes for long-standing accuracy and layout issues.

### Added

*   **Session View Default:** The graph now defaults to the high-resolution "Session" view, ensuring data is visible immediately upon opening.
*   **Widget Background:** Added custom background color and opacity controls.
*   **Short Unit Labels:** Added a toggle for compact unit display (e.g. "Mb" vs "Mbps").
*   **Precise Thresholds:** Replaced sliders with precise `QDoubleSpinBox` inputs (0-10,000 Mbps).

### Changed

*   **Optimized Tray Offset:** Reduced default tray offset from 10px to 1px, allowing the widget to sit flush against the system tray overflow menu for a cleaner look.
*   **Layout Stability:** Fixed scaling issues and potential crashes (`NameError`) during font resizing or unit switching.

### Fixed

*   **Memory Leak Fix:** Resolved an issue where closing the graph window would leave "ghost" instances running in the background. Windows are now properly destroyed, freeing up system resources.
*   **Fixed Graph Freeze:** Decoupled recursive signal loops that could lock up the interface when switching between long timelines.
*   **Fixed Missing Plot Lines:** Resolved a Matplotlib epoch mismatch that caused data to be rendered thousands of years in the future; transitioned to robust native datetime plotting.
*   **Background Bandwidth Calculation:** Moved heavy statistical summations to the data worker thread, preventing UI lag when calculating totals for massive datasets.
*   **Fixed "Stuck 0.00 Mbps" Bug (#64):** Lowered minimum display threshold to `0.0`. Meters now react to even the smallest background transfers (below 80kbps).
*   **Accuracy & Lag Resilience (#78):** Fixed timing logic and increased validity thresholds (3s -> 10s) to prevent inaccurate speed drops during minor system lag.
*   **Vertical Taskbar Support (#77):** 
    *   Changed layout to bottom-align the widget on vertical taskbars (placing it near the tray).
    *   Hardened Z-order preservation to ensure the widget stays on top even when the taskbar is clicked.
*   **Taskbar Detection Fixes (#75, #76):** Added safe screen fallbacks and silenced benign "ambiguous edge" log spam.
*   **Phantom Speed Spikes:** Corrected the rate-limiting math to prevent erratic behavior after system wake or intense jitter.

*   **Safe Database Migrations:** Replaced the destructive "Drop & Recreate" logic with a safe, versioned migration system.

### Performance

*   **Zero-Latency Timeline Switching:** Removed a 100ms synchronous freeze in the graph data retrieval path, making the interface feel significantly more snappy.
*   **Obsolete Result Filtering (Sequence IDs):** High-speed slider interaction no longer causes a "render backlog"; the UI now instantly drops stale results, preventing cumulative performance degradation.
*   **Resource Caching:** Implemented a 60-second cache for static values like system boot time and earliest database records to minimize redundant system calls.
*   **Background Monitoring Thread:** Offloaded network polling to a dedicated thread, ensuring consistent 60+ FPS widget movement and zero micro-stutters during network stack latency.
*   **Vectorized Graph Logic:** Replaced legacy loop-based processing with vectorized NumPy operations, achieving a **42x speed improvement** in graph rendering for large datasets.
*   **Optimized Graph Queries:** Refactored historical data retrieval to group results per-table before unioning, significantly reducing database load times for multi-month timelines.
*   **Zero-Copy Data Retrieval:** Updated database layer to fetch raw timestamps directly, bypassing expensive datetime object instantiation.
*   **Pandas Removal:** Completely removed the `pandas` dependency. The application is now lighter and launches significantly closer to instant.

### Localization

*   **New Languages:** Added full support for **Korean (ko_KR)** and **Slovenian (sl_SI)**.
*   **Key Parity (#74):** Backfilled all 9 supported locales to ensure 100% key parity with English, preventing "missing key" crashes.

### Developer notes

*   **Tray Icon Manager:** Extracted system tray logic into a dedicated component.
*   **System Event Handler:** Centralized low-level Windows hooks (taskbar detection, fullscreen logic) for improved testability.
*   **Main Widget Decoupling:** Split the monolithic `NetworkSpeedWidget` by extracting `StartupManager` (registry logic) and enhancing `PositionManager` (Z-order/window control), significantly reducing code complexity.


---

## [1.2.0-Beta] - January 11, 2026

*Original release of the interactive graph overhaul, later designated as Beta due to accuracy and layout regressions reported in high-frequency monitoring scenarios.*

### Added

*   **Precision Crosshairs:** Added comprehensive dual-axis crosshair system (vertical timestamp snap & horizontal speed tracking).
*   **Dual-Axis Layout:** Split graph into dedicated independently-scaled charts for Download and Upload.
*   **Smooth Interaction:** Switched rendering to an idle-loop model to eliminate UI freezes.

### Fixed

*   **Fixed Startup Crashes:** Solved `AttributeError` issues related to `matplotlib.dates`.
*   **Fixed Widget Disappearance:** Resolved regression where closing the Graph window could hide the main widget.


---

## [1.1.9] - December 31, 2025

This release addresses a critical bug where the widget would incorrectly hide when applications were maximized, even though the taskbar remained visible.

### Fixed

*   **Fixed Widget Hiding with Maximized Apps:** Resolved issues where the widget would disappear when other applications were maximized.
    *   The `is_taskbar_obstructed` logic was overly aggressive and incorrectly identified maximized windows as obstructions.
    *   The detection has been simplified: the widget now only hides when a **true fullscreen application** is running (window dimensions exactly match the monitor).
    *   Maximized windows, borderless windowed games, and other non-fullscreen scenarios no longer cause the widget to hide.

### Developer notes

*   **Fixed Build Script:** Corrected a filename mismatch in `build.bat` where the expected installer filename did not include the `-x64` suffix, causing builds to fail at the packaging stage.


---

## [1.1.8] - December 11, 2025

This release marks a significant maturity milestone for NetSpeedTray. We are proud to announce that the application is now **digitally signed**, establishing a chain of trust and eliminating security warnings. Additionally, this update brings full Russian language support and a completely modernized, automated build pipeline.

### Security

*   **Digitally Signed Release:** NetSpeedTray is now officially signed with a trusted code signing certificate.
    *   Eliminates the "Unknown Publisher" warning from Windows SmartScreen.
    *   Guarantees that the executable has not been tampered with since it left the build server.
*   **Security Patches:** Updated critical dependencies (including `fonttools` and `pandas`) to the latest secure versions to resolve reported vulnerabilities (CVEs).
*   **Hardened Build Process:** Implemented strict input sanitization in the GitHub Actions workflow to prevent script injection attacks.

### Localization

*   **Russian Language Support:** Added complete translation for the Russian language (Русский).
*   **Locale Best Practices:** Updated the internal localization engine to use native language names (Endonyms) in the settings menu.

### Developer notes

*   **Fully Automated Pipeline:** Implemented a robust CI/CD workflow using GitHub Actions. Every release is now built, tested, and packaged in a clean, isolated environment, ensuring 100% reproducibility.
*   **Automated Versioning:** The application version is now dynamically injected from Git tags directly into the executable, installer, and internal metadata. This ensures the "File Version" in Windows Properties always matches the release tag perfectly.
*   **Quality Gates:** Unit tests are now automatically executed before every build. If a test fails, the build is stopped immediately, preventing buggy releases from reaching users.


---

## [1.1.7] - October 29, 2025

This is a landmark release focused on stability and making the application's most complex feature-the **Network Speed Graph** - a fast, and visually insightful tool.

The graph has been completely re-architected for performance and clarity. This update also includes an extensive list of critical bug fixes that address phantom speed spikes, UI glitches, installer problems, and instability when the Windows shell is restarted.

### Added

*   **Definitive Visualization:** The graph has been completely redesigned to solve the core problem of displaying asymmetric network speeds.
    *   **Dual-Axis Layout:** The graph is now split into two dedicated, independently-scaled charts for **Download** and **Upload**, ensuring that upload activity is always perfectly visible and not "flattened" by large download spikes.
    *   **Hybrid Rendering Engine:** The graph uses a smart, hybrid approach for visualization. Short timelines (e.g., "24 Hours") are rendered as detailed line plots, while long timelines (e.g., "Month") are rendered as a beautiful **Mean & Range Plot**, showing both the daily average trend and the min/max volatility.

*   **Massive Performance Improvements:** The entire data pipeline is now asynchronous, eliminating UI freezes.
    *   **Instantaneous Loading:** The graph window now opens instantly. Data is fetched and processed in a **background worker thread**, preventing the application from becoming unresponsive when loading large time ranges.
    *   **Responsive UI:** Switching between timelines, hovering over the graph, and resizing the window is now dramatically faster and smoother.

*   **Full Interactivity & Polish:**

    *   **NEW:** **Fixed Graph Timeline Display:** Solved multiple issues with the X-axis, including incorrect time windows being shown and cluttered, nonsensical timestamps. Timelines from 3-24 hours now have clean, sensible tick intervals.
    *   **NEW:** **Fixed Live Update Initialization:** Resolved a bug where the "Live Update" feature in the graph would not work on the first open, requiring the user to toggle it off and on again.
    *   **Fixed "No Data Available" Bug:** Resolved a critical bug in the database query logic that could cause the graph to incorrectly show "No data available."
    *   **Accurate Total Bandwidth:** Corrected the stats bar logic to ensure "Total" bandwidth calculations are fast and accurate across all timelines.
    *   **Visual Glitch Fixes:** Resolved bugs that caused Y-axis labels to appear in black on a dark background or display in scientific notation. Added a separator line for better clarity.

### Fixed

*   **Definitive Fix for "Phantom" Speed Spikes:** Implemented a new multi-stage "re-priming" state to permanently fix the bug where impossible network speeds would be recorded after the computer resumed from sleep or experienced heavy lag. The data collection engine now waits for the network drivers to stabilize before resuming measurements.
*   **Enhanced Shell & Display Resilience:** Fixed major bugs where the widget would disappear or move to the wrong position after `explorer.exe` was restarted, a monitor was disconnected (e.g., via a KVM switch), or on some multi-monitor setups. The application is now significantly more robust in detecting and recovering from these events.
*   **Fixed "Zombie" Process Bug:** Solved a critical issue where closing the graph window would also incorrectly close the main widget, leaving a lingering "zombie" process running in the background.
*   **Fixed Start Menu Shortcut:** Corrected a bug in the installer that prevented the Start Menu shortcut from being created on a fresh installation.
*   **Fixed "0 Mbps" Bug:** Fixed a logic error that caused the meter to show `0.00 Mbps` for users with a very fast `update_rate` by making internal timing checks dynamic and more robust.
*   **Fixed UI Glitches:**
    *   Resolved an issue where the widget would incorrectly move position after the user clicked the "Show hidden icons" tray chevron.
    *   Fixed a visual glitch that could cause duplicated "Apply" and "Cancel" buttons to appear in the settings dialog.

### Developer notes

*   **Comprehensive Code Refactoring:** Many internal components were refactored to improve maintainability and performance. This includes centralizing application-wide constants to eliminate "magic numbers" and improve consistency.
*   **Hardened Test Suite:** The project's automated test suite (`pytest`) has been significantly expanded and improved, ensuring that all new features and bug fixes are thoroughly validated, leading to a more stable application.
*   **Enhanced Logging Privacy:** The logging system's privacy filter has been replaced with a more powerful `ObfuscatingFormatter` that redacts sensitive information (user paths, IP addresses) from the *entire* log message, including full tracebacks.


---

## [1.1.6] - August 27, 2025

This version represents a major leap forward in stability, internationalization, and user control, addressing critical bugs from previous versions and fundamentally improving the application's architecture for future development.

### Added

-   **Full Internationalization (i18n) Framework:**
    -   The application has been completely re-architected to support multiple languages.
    -   **Modular Language Files:** All user-facing strings have been externalized from Python source code (`.py`) into language-specific JSON files (`locales/*.json`). This decouples translation from application logic, making it vastly easier for the community to add new languages or fix typos.
    -   **UX Improvement:** The language selection menu in the settings now correctly displays language names in their native form (endonyms), such as "Deutsch" instead of "German". This is a global best practice that prevents users from getting "trapped" in a language they cannot read.

-   **Overhauled Network Interface Monitoring:**
    -   The ambiguous "Monitor All Interfaces" option has been replaced with a clear, explicit set of four radio-button choices in the settings, giving users full control and transparency.
    -   **New Monitoring Modes:**
        1.  **Auto (Primary Interface):** The smart default that automatically finds the main internet-facing adapter.
        2.  **All Physical Interfaces:** Aggregates speed from hardware like Wi-Fi and Ethernet while intelligently filtering out virtual adapters (VPNs, VMs) to reduce noise.
        3.  **All Interfaces (including virtual):** A new power-user option that aggregates traffic from **every** adapter reported by the system, including VPNs, virtual machines, and system loopbacks.
        4.  **Select Specific Interfaces:** The existing manual selection mode.
    -   The core `NetworkController` logic was updated to support these new modes, applying the virtual interface exclusion list *only* when "All Physical" is selected.

### Fixed

-   **Definitive Fix for Disappearing/Flickering Widget:**
    -   A fundamental issue in event handling, which caused the widget to disappear when interacting with the desktop, taskbar, or RDP sessions, has been resolved.
    -   **New Architecture:** The old, aggressive logic has been replaced with a **debounced refresh architecture**. The application now uses `WinEventHook` listeners to intelligently wait for system UI events (like window focus changes or resizes) to "settle" before performing a single, authoritative check on the widget's visibility and Z-order.
    -   This resolves all related stability issues, including the widget hiding when right-clicking for a context menu and correctly handles browser fullscreen videos.

-   **Database Aggregation Logic:**
    -   Fixed a bug in the SQL `GROUP BY` clause for both minute-to-hour and raw-to-minute data aggregation, ensuring that records are correctly combined and preventing duplicate entries in aggregated tables.

### Developer notes

-   **Constants Refactoring:** All user-facing strings were removed from the `constants` files and replaced with non-translatable keys. This improves code clarity and centralizes all text in the `locales` directory.
-   **Dependency Injection for i18n:** The internationalization object (`i18n`) is now properly initialized at the application entry point (`monitor.py`) and passed down as a dependency to all UI components (`widget`, `settings`, `graph`, `renderer`) and helper functions (`format_speed`).
-   **Test Suite Updates:** The `pytest` unit tests have been updated to reflect all architectural changes, including the new interface monitoring modes and the use of i18n keys, ensuring the application's logic remains sound.
-   **Installer Reliability:** The installer continues to gracefully shut down a running instance of the application before updating, preventing common installation errors.
-   **Improved Decimal Formatting UI:** The confusing "Force Decimals" toggle has been removed in favor of a single, intuitive slider that directly controls the number of decimal places (0, 1, or 2), with output now consistently padded with zeros for a more stable appearance.


---

## [1.1.5] - August 24, 2025 (Hotfix)

This is a critical hotfix release that provides a definitive and comprehensive fix for the startup crash and several related stability issues discovered during the beta cycle.

**This is a highly recommended update for all users.**

### Changed

-   **Installer Reliability:** The installer is now much more robust when updating a running instance of NetSpeedTray. It now attempts a graceful shutdown of the application before proceeding with the update, preventing the "Setup was unable to automatically close all applications" error and ensuring a smoother, more successful update process.

### Fixed

-   **Critical Startup Crash & Systemic Stability Issues:** Resolved a complex chain of initialization and rendering errors that caused the application to crash on first launch, particularly on systems with specific UI configurations (like a small taskbar).
    -   Following user feedback from the beta releases (a huge thank you to GitHub user **[CMTriX](https://github.com/CMTriX)**!), a full codebase audit was performed.
    -   This audit eradicated a systemic typo pattern and fixed numerous latent bugs in the widget rendering, positioning, and various utility modules (`taskbar`, `network`, `config`).
    -   The result is a stable and reliable experience across a much wider variety of Windows environments.


---

## [1.1.4] - August 23, 2025 (Hotfix)

This is an immediate hotfix to address a critical bug in the "Start with Windows" feature introduced in v1.1.3.

### Fixed

-   **Fixed "Start with Windows" Toggle:** Resolved a critical logic flaw where the "Start with Windows" setting could not be disabled. The toggle in the settings window will now correctly reflect the user's saved choice, and disabling the feature now correctly removes the application's entry from the Windows Registry.


---

## [1.1.3] - August 22, 2025 (Hotfix)

This is an urgent hotfix release that addresses a critical startup crash reported by users after the v1.1.2 update. It also restores and improves the widget's positioning stability, resolving several visual regressions.

### Fixed

-   **Fixed Critical Startup Crash:** Resolved a critical `AttributeError` that prevented the application from launching on some systems. This was caused by an unreliable network dependency (`netifaces`) which has now been completely removed and replaced with a more robust, built-in solution.
-   **Restored Widget Stability (Fixed Flashing):** Re-engineered the widget's core update logic to eliminate a visual "flashing" regression introduced in v1.1.2. The widget is now perfectly stable and only updates its position when absolutely necessary, removing all polling-related flicker.
-   **Improved UI Responsiveness:** Fixed two key regressions where the widget would not:
    -   Reliably reappear after launching an application (like Calculator) from the Start Menu.
    -   Automatically reposition itself when new icons appeared in the system tray.


---

## [1.1.2] - August 22, 2025

This is a major stability and quality-of-life release that addresses critical bugs, enhances UI intelligence, improves user privacy, and completely overhauls the installer and settings backend for a more professional and robust user experience.

### Added

-   **Intelligent Interface Monitoring (New Default):** The method for selecting network interfaces has been completely redesigned for clarity and accuracy.
    -   It now uses a clear, three-option radio button system: `All`, `Auto`, and `Selected`.
    -   **"Auto (Primary)" is the new default mode.** It intelligently identifies your main internet adapter, providing a much more accurate speed reading by ignoring noise from VPNs, virtual machines, and other virtual adapters.

-   **Language Selection:** The application is now fully internationalized. Users can select their preferred language from a new dropdown in the General settings. A restart is required for the change to take full effect.

-   **Intelligent Taskbar Positioning:** The widget's positioning logic has been completely re-architected. It now actively scans the taskbar for "obstacles" like the Start Menu, pinned application icons, and the Windows Weather/Widgets icon. It intelligently places itself in the nearest truly empty space, preventing it from overlapping with other UI elements.

-   **Adaptive Layout for Small Taskbars:** The widget now automatically detects when Windows' "Use small taskbar buttons" setting is active and switches to a clean, compact, single-line horizontal layout for a much better visual fit.

### Developer notes

-   **Installer Overhaul:** The Inno Setup installer and uninstaller have been significantly improved:
    -   **Correct 64-bit Installation:** Ensured the installer correctly recognizes the application as 64-bit, defaulting to the native `C:\Program Files` directory instead of `C:\Program Files (x86)`.
    -   The uninstaller now reliably detects if the application is running and will prompt the user to close it before proceeding.
    -   The uninstaller now provides a clear option to completely remove all personal data (settings, database, logs).
    -   Fixed a bug where the desktop shortcut was sometimes left behind after uninstallation.
    -   Silent Uninstall with Data Removal: For a complete, unattended removal, run the following command in an **Administrator PowerShell**:
    ```powershell
    & "C:\Program Files\NetSpeedTray\unins000.exe" /SILENT /PURGE=true
    ```
-   **Log File Privacy (Verified):** The privacy filter is now fully effective. It automatically obfuscates personal information before it is written to the log file.
    -   User home directories in file paths are replaced (e.g., `C:\Users\Erez\...` becomes `<USER_HOME>\...`).
    -   IP addresses found in rare error messages are partially redacted (e.g., `192.168.1.100` becomes `192.168.x.x`).

### Fixed

-   **Fixed "Auto" Interface Monitoring:** Fixed a critical bug where the "Auto" mode logic existed but was never actually called, making the feature non-functional. The controller now correctly uses this logic when the "Auto" mode is selected.
-   **Fixed Phantom Speed Spikes (Data Integrity):** Resolved a critical bug where waking the computer from sleep could cause the application to calculate and save impossibly high network speeds. The data collection logic is now more robust and includes multiple sanity checks to discard these "phantom" spikes.
-   **Fixed "Invisible Shield" & RDP Bugs (UI Stability):** Resolved a severe bug where the widget could act as an "invisible shield," blocking mouse clicks to other applications. The widget's transparent areas are now correctly "click-through" by default. This also resolves related stability issues for users in Remote Desktop (RDP) sessions.
-   **Fixed Graph Window Accuracy (Live Data):** Corrected a bug in the Graph Window where selecting a specific network interface would still display the total speed of all interfaces in "Live Update" mode. The graph now correctly displays only the selected interface's data.
-   **Fixed Settings Window Stability:** The Settings window has been re-engineered to be a normal, non-modal window. This fixes numerous UI bugs, including dropdown menus instantly closing and the entire application shutting down when the settings window was closed.
-   **Fixed Interface Selection Logic:** Corrected a logic flaw where monitoring would fall back to all interfaces if the "Selected" option was chosen but no interfaces were checked. It now correctly shows zero speed.
-   **Database & Data Integrity:**
    -   Fixed a bug where negligible, sub-byte network speeds were being incorrectly saved to the database.
    -   Resolved a `KeyError` crash that could occur during application shutdown due to a race condition in the timer management system.
-   **UI & Visual Polish:**
    -   The mini-graph on the widget now has dynamic Y-axis padding, preventing graph peaks from being "cut off".
    -   Fixed inconsistent decimal formatting for download speeds.


---

## [1.1.1] - August 18, 2025

This release focuses on providing a fast, and native-feeling user experience as much as possible. It introduces a major startup performance overhaul, addresses key bugs related to the history graph and UI integration, and refines the application's overall stability.

### Added

-   **Drastically Improved Startup Performance:** The application's compiled structure has been changed to eliminate the slow, single-file unpacking process.
    -   **Faster Launch:** Startup time is now significantly faster, as the application and its dependencies are no longer extracted to a temporary folder on every launch.
    -   **New Distribution Formats:** To support this, NetSpeedTray is now distributed with a fast Inno Setup installer and a portable `.zip` archive for users who prefer a non-install option.
-   **Seamless UI Responsiveness & Integration:** The widget's visibility logic is now fully event-driven, eliminating delays and making it feel like a native part of the Windows shell.
    -   **Instantaneous Auto-Hide & Fullscreen Detection:** The widget now appears and disappears instantly with an auto-hiding taskbar and when entering or exiting fullscreen applications.
    -   **Graceful System UI Handling:** Proactively hides when core system menus (like the Start Menu and network/volume flyouts) are opened, and reappears upon closing them. This provides a polished, non-intrusive experience and avoids visual glitches.
    -   **Reliable Z-Order:** The widget now correctly stays on top of the taskbar and other applications after focus changes.
-   **Smart Taskbar Theme Detection:** Fixed a critical bug where the widget's text color would be incorrect for users with a "mixed theme" (e.g., Light app mode with a Dark taskbar). The widget now correctly bases its text color on the **taskbar's theme**, ensuring visibility in all configurations.

### Fixed

-   **Graph Window Polish:**
    -   **Timeline Persistence:** The graph window now correctly remembers and restores the last selected time range (e.g., "6 Hours", "1 Day") across application restarts.
    -   **Corrected Data Display:** Fixed a bug where the graph would show "No data available" for long-term views on a fresh launch; it now correctly displays all available historical data.
-   **UI Visibility with Start Menu:** Resolved a regression where the widget would not reappear after launching an application (like Calculator or Settings) from the Start Menu.
-   **Light Mode Stability:**
    -   Fixed a crash that occurred when opening the Settings dialog in Windows Light Mode.
    -   Resolved the "invisible text" bug on the very first launch for users in Light Mode.
-   **Architectural Improvements:**
    -   The `WinEventHook` utility has been upgraded to be more robust and efficient.
    -   The core visibility logic in `taskbar_utils.py` has been significantly improved to handle edge cases more reliably.
    -   The build process now performs a full cleanup, leaving no intermediate files behind.


---

## [1.1.0] - August 12, 2025

This is a significant update focused on improving data accuracy, providing more detailed graphing features, and creating a more stable foundation for the future. The data collection and storage pipeline has been substantially rebuilt to make NetSpeedTray a more capable and reliable network monitor.

### Added

-   **Intelligent Graph Visualization:** The history graph is now significantly more insightful and readable.
    -   **Dynamic Logarithmic Scale:** A new `symlog` scale solves the "flat line" problem, allowing you to see fine-grained detail in your low-speed traffic without high-speed spikes squashing the visualization.
    -   **Smart Axis Boundaries:** The Y-axis scale is now data-driven, analyzing your traffic patterns to set a "normal usage" range that ensures the graph is always well-suited to your network.
    -   **Clean, Readable Ticks:** The Y-axis labels are now always round numbers (e.g., 0, 10, 100, 1000), making the graph intuitive and easy to read.
-   **Smart Interface Monitoring:** The application can now automatically identify your primary internet connection (e.g., "Wi-Fi" or "Ethernet") and display its speed by default, providing a cleaner and more accurate reading.
-   **Per-Interface Graph Filtering:** A new "Interface" dropdown in the graph settings allows you to visualize the speed history for any specific network adapter on your system.
-   **Improved Data Accuracy:**
    -   Fixed a bug that could cause large, incorrect speed spikes in the database after the computer wakes from sleep. The controller now handles these events correctly, ensuring historical data is more reliable.
-   **Safer Data Retention Policy:** If you reduce the data retention time (e.g., from 1 year to 7 days), the application now waits for a 48-hour grace period before pruning old data, preventing accidental data loss.

### Fixed

-   **Architectural Overhaul:**
    -   The core data layer (`WidgetState`) has been rebuilt with a multi-tiered database (`raw`, `minute`, `hour`) and a dedicated worker thread to improve UI responsiveness.
    -   The `Controller` has been updated to support granular, per-interface data collection.
    -   The application's constants have been refactored into a more organized package structure.
-   **Performance:** Key libraries like `numpy` and `matplotlib` are now lazy-loaded, making the initial application startup faster and more lightweight.
-   **Graphing Engine:**
    -   The "Session" view now correctly uses live in-memory data.
    -   The `Export to CSV` feature now exports the currently filtered view.
-   **Technical Debt:** The obsolete `core/model.py` module has been removed, simplifying the codebase.

### Localization

-   **Added full German (de_DE) language support.** Many thanks to the users and communities on **Chip.de** and **Softpedia** for their attention and support.

### Important Note for Existing Users

-   This version introduces a new database format to enable per-interface monitoring and improved accuracy.
-   **The upgrade is automatic and safe.** When you first run v1.1.0, the application will detect the old database, back it up by renaming it to `speed_history.db.old`, and create a new one. No manual steps are required.


---

## [1.0.9] - August 4, 2025

This is a major stability, performance, and quality-of-life update focused on refining the widget's core behavior, improving data accuracy, and optimizing the application's architecture for future features.

### Changed

- ** Improved Widget Stability & Behavior:** The widget now feels much more like a native part of the Windows taskbar. An entirely new, event-driven architecture (using a WinEventHook and a safety-net timer) has been implemented to intelligently manage visibility.
  The widget no longer flickers, disappears, or gets stuck behind the taskbar when interacting with the Calendar, Network/Volume flyouts, or the tray overflow menu.

- **Enhanced Startup Performance:** The application's impact on system startup has been reduced. By implementing "lazy loading" for the graph window and other UI components, the initial launch is now faster and lighter on system resources.

### Fixed

- **Graphing Engine Overhaul:** The graph window's data pipeline has been completely revised for accuracy and reliability.
  - Fixed a critical bug that caused the graph to appear flat-lined or empty due to a data unit mismatch in the database.
  - Corrected the "Total" data calculation in the stats bar, which was showing vastly inflated numbers.
  - Fixed an issue where the "Live Update" toggle was not updating the graph in real-time.
- **Accurate Speed Calculation:** Resolved a bug where network speeds could show an incorrect, massive spike after the computer wakes from sleep or hibernation.
- **Application Stability:**
  - Fixed a potential crash (`AttributeError`) that could occur when closing the application.
  - Hardened the DPI detection logic to prevent log spam and errors from invalid monitor handles reported by the OS.
- **Code Quality & Architecture:**
  - The core data management model has been centralized into a single `WidgetState` class, improving clarity and retiring the redundant `SpeedHistory` class.
  - The developer console is now clean, with benign `matplotlib` warnings on first launch being properly handled.
  - Numerous unused imports and obsolete code paths have been removed.

### Important Note for Existing Users

- To enable the data accuracy fixes, **users upgrading from a previous version must delete their old history database.** A new, clean database will be created automatically.
- **Instructions:**
  1.  Ensure NetSpeedTray is not running.
  2.  Open File Explorer, paste `%APPDATA%\NetSpeedTray` into the address bar, and press Enter.
  3.  Delete the file named `speed_history.db`.


---

## [1.0.8] - July 31, 2025

This release focuses on improving the reliability and accuracy of the Network Speed Graph, resolving all known bugs related to data display and state persistence.

### Changed

- **Accurate "System Uptime" Timeline:** The "System Uptime" view now correctly uses the system's actual boot time as its starting point, perfectly matching the behavior of the Windows Task Manager.
- **Correct "Session" Timeline:** The "Session" view is now persistent and correctly displays data from the start of the application, regardless of how many times the graph window is opened or closed.
- **Accurate Statistics Bar:** The "Max" and "Total" statistics displayed at the top of the graph are now always calculated correctly based on the selected timeline, ensuring you see the right data for the right period.
- **Improved Initial Load:** Fixed a bug where the graph would sometimes show "No data available" on its first launch, even when historical data was present. It now reliably displays the correct timeline from the moment it opens.

### Fixed

- **Build Process:** The build script has been made more robust.
- **Configuration:** The application's configuration file now officially recognizes and validates all graph-related settings, eliminating harmless but noisy warning messages from the log files.


---

## [1.0.7] - July 30, 2025

This is a landmark stability release that perfects the core user experience of the widget. Through a comprehensive overhaul of the positioning and state management logic, the widget now behaves with the rock-solid predictability of a native Windows UI element. All visual "flickering," "jumping," and "drifting" issues have been eliminated.

### Fixed

- **Perfectly Stable Positioning:** Resolved a complex series of deep-seated bugs that caused the widget to flicker or jump. The widget's position is now completely stable during application startup, after closing the settings window, and while the system tray icons change.
- **Smooth & Intuitive Dragging:** The widget no longer "fights" the user's cursor or snaps to a slightly different spot after being moved. The position where you release the mouse is now its final, pixel-perfect location.
- **Intelligent Snap-to-Edge:** When dragging the widget near the system tray, it now intelligently snaps to a minimum safe distance if moved too far, gracefully respecting the user's intent to place it as close as possible.
- **Unrestricted Placement:** The arbitrary limit on how far the widget could be dragged along the taskbar has been removed. Placement is now constrained only by the edges of your screen.


- **Graph Window:** Fixed a critical `AttributeError` that could prevent the Graph Window from opening correctly.
- **Configuration File:** The app's config file is now cleaner and no longer stores unnecessary `position_x` and `position_y` fields when they are not in use.
- **UI Text:** The "Enable Free Move" label in settings has been simplified to "Free Move (No Snapping)" for better clarity.


---

## [1.0.6] - July 29, 2025

This release focused on quality-of-life improvements, introducing the initial version of the adaptive widget positioning and overhauling the installation process for seamless future updates.

### Added

- **Adaptive Positioning (Beta):**
  - The widget now learns and maintains your preferred distance from the system tray.
  - It automatically shifts its position to prevent being overlapped by new application icons appearing in the tray.
  - Your custom spacing is "learned" simply by dragging the widget while _Free Move_ is disabled.

### Changed

- **Seamless Upgrades & WinGet Compatibility:** The Windows installer has been overhauled. It now correctly replaces the previous version's files, ensuring a clean and reliable upgrade experience. This change also prepares the application for distribution via the Windows Package Manager (WinGet).
- **UI Clarity:** Renamed the confusing "Smart Threshold" toggle to **"Dynamic Update Rate"** to more accurately describe its power-saving function.
- **User-Friendly File Naming:** The configuration and log files have been renamed to be more descriptive (`NetSpeedTray_Config.json` and `NetSpeedTray_Log.log`), making them easier for users to identify.


---

## [1.0.5] - July 29, 2025

### Added

- **Free Move is Here!**

  - You can now unlock the widget from the taskbar and place it **anywhere on your screen**.
  - The widget's position is automatically saved when Free Move is enabled and it reliably snaps back to its default location when disabled.

- **Total Control Over Speed Units:**
  - A new **Speed Units** panel has been added to the settings for granular control over the text display.
  - **Display Mode:** Choose between 'Auto' scaling (bps, Kbps, Mbps) and 'Always Mbps'.
  - **Decimal Places:** Set the speed value precision from 0 to 2 decimal places.
  - **Text Alignment:** Align the speed text left, center, or right within the widget.
  - **Force Decimals:** A new option to always show decimal points (e.g., `5.0` instead of `5`).

### Changed

- **Smarter Installer:** The Windows installer now automatically replaces the old executable, ensuring a clean and seamless upgrade experience. Old version files are removed.
- **Improved Configuration:** The configuration file has been renamed to `NetSpeedTray_Config.json` for clarity. The management system is now more robust, preventing settings from being accidentally discarded.
- **Cleaner Log Files:** The log file has been unified and renamed to `NetSpeedTray_Log.log` to make troubleshooting easier.
- **Accurate Graph Stats:** The statistics in the main graph's status bar are now calculated correctly for all timelines, including "All".

### Fixed

- **CRITICAL: Mini-Graph Now Renders Correctly:** Fixed a series of silent failures that were preventing the mini-graph from appearing on the widget. This was the most significant bug from the beta and is now fully resolved.
- **CRITICAL: State Persistence Fixed:** Resolved a major bug where toggle states (like **Free Move**, **Force Decimals**, and **Start with Windows**) were not being saved correctly across application restarts.
- **"Snap-Back" Bug Fixed:** Corrected a state management flaw where disabling "Free Move" sometimes required clicking "Save" twice. The widget now snaps back to its default position instantly and reliably on the first click.


---

## [1.0.5-Beta2] - July 29, 2025

This release introduces powerful new customization features for the taskbar widget and resolves a series of critical bugs that were discovered following the major refactor in Beta1.

### Added

- **Free Move Feature:**

  - Introduced the **Free Move** feature, allowing users to unlock the widget from the taskbar and place it anywhere on the screen.
  - The widget's position is now saved when Free Move is enabled and it automatically and reliably snaps back to its default location when disabled.

- **Speed Units Customization:**
  - Added a new **Speed Units** section to the settings for granular control over the text display.
  - **Speed Display Mode:** Choose between 'Auto' scaling (bps/Kbps/Mbps) and 'Always Mbps'.
  - **Decimal Places:** Set the precision of the speed values (0, 1, or 2).
  - **Text Alignment:** Align the speed text to the left, center, or right of the widget.
  - **Force Decimals:** An option to always show decimal points (e.g., '5.0' or '5.00' instead of '5').

### Changed

- **Speed Unit Logic:** The old 'Use MB/s' toggle has been removed and replaced by the more flexible and powerful Speed Display Mode.
- **Configuration Management:** Refactored the `ConfigManager` to be more robust and declarative, preventing future issues with unsaved settings.
- **Data Flow:** Streamlined the data pipeline between the widget and the renderer to prevent data corruption and improve stability.

### Fixed

- **Main Graph Stats:** Improved the accuracy of the main graph's status bar; statistics for the 'All' timeline are now calculated correctly.
- **Mini-Graph Rendering:** Fixed a critical bug where the **Mini-Graph would not render** on the widget. This was caused by a series of silent failures, including a `NameError`, a data corruption issue in the renderer's data flow, and an incorrect drawing call.
- **State Persistence:**
  - Resolved an issue where the **'toggle states were not being saved** across application restarts.
  - Fixed a state management bug that required clicking 'Save' twice to disable Free Move; the widget now snaps back instantly.
- **Settings Not Saving:** Corrected a validation issue that prevented several settings (`force_decimals`, `start_with_windows`, etc.) from being saved to the configuration file.


---

## [1.0.5-Beta1] - July 27, 2025

### Added

- **Modern UI/UX:**
  - Redesigned settings and graph windows with PyQt6, custom dark mode and improved layout.
  - Hamburger menu for quick access to graph settings.
  - New icons and centralized asset management.
- **Testing:**
  - Added unit tests for configuration, constants, and core logic.

### Changed

- **Full Modular Refactor:**  
  Migrated from a single-file script to a modern, maintainable package structure (`src/netspeedtray/`). All logic is now organized into `core`, `views`, `utils`, `constants`, and `tests` modules.


- **Code Quality:**
  - Improved type hints, docstrings, and error handling throughout.
  - Enhanced logging and configuration management.
- **User Experience:**
  - Graph and settings dialogs now always open centered and within screen bounds.

### Fixed

- **Stability & Layout:**
  - Fixed window icon issues and theme inconsistencies.
  - Improved error overlays and ensured dialogs respect minimum sizes and DPI scaling.

### Known Issues

- **App Usage Tab:** Temporarily disabled pending further development.
- **Multi-Monitor:** Still limited to primary taskbar’s screen; multi-monitor support improvements planned.


---

## [1.0.4] - March 7, 2025

### Added

- **Double-Click Full Graph**:
  - Double-clicking the widget now opens the detailed `GraphWindow` for network speed history.
- **GraphWindow Features**:
  - **Live Updates**: Toggleable real-time updates (2-second interval).
  - **Dark Mode**: Switch between light and dark themes for better visibility.
  - **Legend Positioning**: Options include Off, Left, Middle, Right.
  - **Export Options**: Save graph as PNG or history as CSV.
  - **History Periods**: Select from Session, 24h, 1 Week, 1 Month, All, or System Uptime.
  - **Data Retention**: Configurable retention periods (1 day to 1 year).
- **Settings Dialog Enhancements**:
  - Replaced checkboxes with modern `ToggleSwitch` controls for a cleaner UI.
  - Added live preview for font size and weight adjustments.
  - Improved network interface selection with a scrollable list and "All Interfaces" toggle.

### Changed

- **UI Improvements**:
  - Modernized `SettingsDialog` with toggle switches and better layout spacing.
  - Increased `GraphWindow` default size to 802x602 pixels for improved readability.
  - Enhanced mini-graph rendering with configurable opacity.
- **Performance**:
  - Throttled `GraphWindow` updates to 500ms intervals to reduce UI lag.
- **Configuration**:
  - Updated default font to "Segoe UI Variable Small" for consistency.

### Fixed

- **Stability**:
  - Improved error handling in `GraphWindow` and CSV logging with thread-safe locking.
- **Layout**:
  - Ensured dialogs (Settings, Graph) stay within screen bounds and anchor correctly relative to the widget.
- **Visibility**:
  - Refined fullscreen app detection to prevent widget hiding issues.

### Known Issues

- **Multi-Monitor**: Limited to the primary taskbar’s screen; doesn’t fully support multiple taskbars or dynamic monitor changes without manual repositioning.
- **High-Frequency Updates**: May still impact performance on low-end systems despite throttling.
- **False Flagging** - [VirusTotal report](https://www.virustotal.com/gui/file/3c045c40ae2dd077fa66f5881649763b11b2584419f9e35b4421bee4f17fc3cf)


---

## [1.0.3] - March 1, 2025

### Changed

- **Improved Widget Visibility and Z-Order Management**
  - Enhanced the widget’s visibility handling to ensure the widget remains visible across window switches and taskbar interactions.
- **Optimized Settings Dialog Positioning**
  - Streamlined the positioning of the settings dialog to use Qt’s screen geometry, ensuring it remains fully visible above the taskbar in multi-monitor setups, with better handling of size changes after saving settings.

### Fixed

- **Startup Positioning Issue**: Resolved the issue where the `Widget` jumps to the top-left corner on startup after login, ensuring it maintains its exact saved position immediately.
- **Desktop Click Hiding/Flashing**: Fixed the issue where clicking the desktop causes the widget to hide and "flash", improving fullscreen detection and adding debouncing to prevent rapid visibility toggles.
- **Portable Startup Issue**: Fixed the "Start with Windows" option in the portable version, ensuring it creates a shortcut in the Startup folder for automatic launch on login, matching installed version behavior (requires `pywin32` for shortcut creation).

### Known Issues

- **Start Menu Interaction Issue**

  - The `Widget` may hide or become unresponsive when the Windows Start menu is opened, This issue arises due to a Windows limitation where `Shell_TrayWnd` (the taskbar window) and related UI elements (e.g., Start menu) can obscure or temporarily disable overlay windows - like the widget.
  - Windows does not provide a reliable API or event to distinguish Start menu activation from other fullscreen or taskbar-related states, leading to potential misdetection in `is_fullscreen_app_active` or `check_and_update`. This behavior is outside NetSpeedTray’s control but may be mitigated in future updates by enhancing taskbar and Start menu state tracking.
  - What this all means to the avarage user - when clicking on the start menu, the widget 'hides' and when clicking anywhere other than the taskbar, it will reappear

  ### Detailed Bug Fixes (for those interested)

- **Enhanced Position Persistence**
  - Modified `NetworkSpeedWidget.initialize_with_saved_position` and `use_saved_position` to prioritize loading and applying the last saved position (`position_x`, `position_y`) from `netspeedtray.conf` on startup, ensuring the widget appears exactly where the user left it after each Windows logon.
  - Updated `update_position` to check for the `initial_position_set` flag and saved coordinates, defaulting to the last saved position unless explicitly overridden by dragging or major failures.
  - Improved error handling in `use_saved_position` and `update_position` to only fall back to position (100, 100) if there’s a critical failure (e.g., `taskbar_hwnd` or screen geometry cannot be detected). This prevents unnecessary repositioning to the top-left corner.
- **"Flashing" Prevention**:
  - Delayed widget visibility (`self.show()`) until `initialize_with_saved_position` confirms the correct position, avoiding premature display in the wrong location. This eliminates the "flashing" by ensuring smooth positioning before rendering.
  - Added logging in `initialize_with_saved_position` and `use_saved_position` to debug positioning issues, ensuring visibility of any errors causing the "flash" or incorrect placement.
- **Configuration Validation**:
  - Enhanced `load_config` and `validate_config` to ensure `position_x` and `position_y` are integers and within valid screen bounds, preventing corrupted or invalid position data from causing positioning errors.


---

## [1.0.2] - February 26, 2025

### Added

- **Startup Synchronization** Added command-line arguments (`--set-startup`, `--unset-startup`) to sync "Start with Windows" between the installer and app settings.

### Fixed

- **Invisible Widget Issue**: Resolved the issue where the app runs but the `Widget` remains invisible, ensuring proper display on the taskbar after launch, even after system restarts or environment changes (e.g., multi-monitor setups, fullscreen apps).
- **Dragging Error**:
  -Fixed and improving mouse event handling.

### Known Limitations

- **Multi-Monitor Support**
  - Supports multi-monitor setups by detecting the taskbar screen, but may experience positioning or sizing issues if monitors have different DPI scaling levels (e.g., 125%, 150%, 200%, 300%).
  - Handles resolution mismatches between monitors, but scaling mismatches can cause issues.
- **KVM Switches**
  - Should return to the correct position after switching via a KVM, but temporary mispositioning or scaling issues may occur if the new monitor setup differs (resolution, scaling, taskbar position).
- **Start Menu Interaction Issue**
  - The `Widget` may hide or become unresponsive when the Windows Start menu is opened, particularly on multi-monitor setups or with custom taskbar configurations. This issue arises due to a Windows limitation where `Shell_TrayWnd` (the taskbar window) and related UI elements (e.g., Start menu) can obscure or temporarily disable overlay windows like the widget.
  - Windows does not provide a reliable API or event to distinguish Start menu activation from other fullscreen or taskbar-related states, leading to potential misdetection in `is_fullscreen_app_active` or `check_and_update`. This behavior is outside NetSpeedTray’s control but may be mitigated in future updates by enhancing taskbar and Start menu state tracking.
  - Functionally - when clicking on the start menu, the widget 'hides' and when clicking anywhere but on the taskbar, it will reappear
- **Edge Cases**:
  - Multiple or docked taskbars, monitor hot-plugging, high DPI scaling on small monitors, fullscreen apps on non-taskbar monitors, low-performance systems, KVM switches to non-Windows OS, and custom taskbar positions may cause issues or misbehavior.

### Future Improvements

- Enhanced multi-monitor support with per-monitor DPI scaling awareness.
- Robust handling of KVM switches, monitor hot-plugging, and custom taskbar positions.
- Performance optimizations for low-end systems.


---

## [1.0.1] - February 21, 2025

### Added

- **Network interface selection feature:**
  - Interface monitoring modes (**All / Selected / Exclude**)
  - Dynamic detection and status of active interfaces
  - Per-interface bandwidth monitoring
  - Interface selection persists between sessions
- **Enhanced error logging system:**
  - Detailed error reporting with system information
  - Error log rotation (**10MB limit, 3 files**)
  - Error log export functionality in settings
  - Comprehensive system diagnostics in logs

### Changed

- **Settings dialog improvements:**
  - Streamlined layout and organization
  - Smart collapsible sections
  - Dynamic position adjustment
  - Improved interface selection controls
- **Default speed thresholds adjusted:**
  - High speed threshold: **5 Mbps** (was **1 Mbps**)
  - Low speed threshold: **1 Mbps** (was **0.1 Mbps**)

### Fixed

- **Settings dialog now properly shows the application icon in the title bar.**
- **Application visibility now properly syncs with taskbar:**
  - Widget auto-hides when taskbar is hidden (fullscreen mode).
- **Settings dialog behavior:**
  - Proper expand/collapse animation
  - Maintains screen position when expanding
  - Consistent spacing and alignment
  - Better visual hierarchy


---

## [1.0.0] - February 21, 2025

### Added

- **Initial release**
- **Real-time network speed monitoring in system tray**
- **Upload and download speed display**
- **Customizable color coding based on speed thresholds**
- **Optional speed history graph**
- **Drag-and-drop positioning**
- **Settings dialog with:**
  - Update rate configuration
  - Color coding options
  - Graph settings
  - Auto-start with Windows
- **Portable and installer versions**
- **Windows taskbar integration**
- **System tray context menu**
- **Configuration file saving/loading**
- **Error logging system**

### Known Issues

- **Two processes appearing in Task Manager**
- **Startup delay when loading application**
- **Application does not reappear when the taskbar auto-hides**
