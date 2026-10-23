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

## [2.2.0-rc.2] - October 23, 2026

*A release candidate, for testers. It's a manual download from the release page - the in-app
updater, WinGet and the Store never offer prereleases.*

rc.2 was meant to be a small follow-up, and then 2.2.0 got a month as Latest ahead of it - so it
became the last full pass: every open defect and promise I could find, plus the smaller feature
requests. One color scheme for dark and light taskbars, the widget on every taskbar, usage by day,
a widget that steps aside for your app buttons, a second launch that brings a lost widget back, and
text that stays readable over the graph, on an accent-colored taskbar and in High Contrast.

> **Upgrading:** from rc.1 or any 2.2 beta, just install over it - the running app closes itself and
> comes back. From 2.1.x, everything in the beta.1 to rc.1 notes applies too.
>
> - **Your settings are converted once more** and backed up first, as
>   `NetSpeedTray_Config.json.bak.v1.2` next to them. Every widget color now has a dark-taskbar and
>   a light-taskbar version. A color you picked is copied to both, so nothing you chose changes.
>   Colors you never changed get the new defaults: High/Medium/Low colors that stay apart with
>   red-green color blindness (on a dark taskbar High is a soft green instead of #00FF00; Low is
>   unchanged), readable light-taskbar versions, and the clock's font, Segoe UI Variable. A font,
>   arrow font or High color you chose stays. The old color keys stay in the file, so going back to
>   rc.1 or 2.1.7 still works.
> - **"Hide while app buttons cover the widget" is on.** On a horizontal Windows 11 taskbar, when
>   your app buttons reach the widget it steps aside and comes back when there's room. Start
>   NetSpeedTray again to bring it back for the session, or turn it off in Settings → Widget.
> - **The speed-threshold boxes show your unit now.** The thresholds were always applied in Mbps,
>   even when the box said MB/s. Nothing is converted, so your color coding doesn't change - but a
>   "5" you typed under MB/s now reads "0.625 MB/s", the speed that was really used. Type it again
>   if you meant 5 MB/s.
> - **The Monitor's network graph leaves a gap where nothing was measured** - the app closed, the PC
>   asleep - instead of drawing a dashed zero line. A quiet stretch while it was running still reads
>   zero.
> - **On a side taskbar, if you dragged the widget in an earlier version,** choose Reset Position
>   once (right-click the widget) so it can find a free spot by itself. A saved drag counts as "you
>   placed it", and automatic placement stays out of its way.
> - **If you followed rc.1's LibreHardwareMonitor hint** (authentication off, listening on all
>   interfaces), make sure port 8085 isn't open in your firewall. The hint now says so - see Security.
> - **Switching Windows between light and dark no longer writes your settings file.** The widget
>   just repaints.

### Added

- **One color scheme for dark and light taskbars.** Text, the High/Medium/Low colors, both arrows
  and the mini graph each get a dark-taskbar and a light-taskbar color - the moon and sun swatches in
  Settings - and the widget switches between them with Windows mode. A widget background you made
  opaque picks the half that reads on it. ([#441]; fixes [#332], thanks [@Angel-Informatico])

  *Under the hood: 16 `<stem>_dark` / `<stem>_light` keys from `THEMED_COLOR_DEFAULTS`, resolved
  only in `RenderConfig.from_dict` from `SystemUsesLightTheme`, so every consumer follows for free;
  `color_is_automatic`, `apply_theme_aware_defaults` and the in-memory theme rewrite are gone. An
  opaque background is composited over a reference taskbar color in sRGB, and the light half wins
  above relative luminance 0.1791, where black and white text read equally. A 144-scenario render
  guard pins that dark taskbars and custom colors paint exactly as rc.1 did.*

- **A Medium speed level for color coding**, between Low and High, with its own color. Off by
  default - give it a speed in Settings → Appearance → Color coding to turn it on. ([#441]; [#9],
  thanks [@2g4b])
- **Mini graph colors.** Settings → Widget → Mini graph → Upload graph color and Download graph
  color, for Trend, Detailed and the split graphs. The Monitor window's graphs keep following the app
  theme. ([#441]; [#117], thanks [@thefaboss])
- **Show on all taskbars.** With Windows' "Show my taskbar on all displays" on, Settings → Widget
  puts a copy of the widget on every taskbar. Right-click and double-click work on each copy; drag
  and the hover card stay on the main widget for now. Off by default. ([#434]; [#229], thanks
  [@dovletix])

  *Under the hood: each copy is a paint-only `MirrorWidget` with its own renderer, layout and
  `PositionManager` (burial recovery per window), fed by the main widget's metrics and a read-only
  config overlay that pins its screen. Copies are keyed by the Win32 monitor rect, not the Qt screen
  name - a just-added display takes the first screen's name and stole its neighbor's copy - and are
  unlinked from their taskbar before every destroy. With the setting off, the module isn't even
  imported. v1 limits: copies don't step aside for app buttons, and a copy on a side taskbar keeps
  the spot it got when it docked.*

- **On a horizontal Windows 11 taskbar, the widget steps aside for your app buttons.** When they
  reach it, it hides, and it comes back once there's room; it never moves. On by default; turn off
  "Hide while app buttons cover the widget" in Settings → Widget. A one-time notice says how to get
  it back. ([#438]; [#24], thanks [@relu616])

  *Under the hood: the buttons are #406's UI Automation app group (the Start-led run, "..." included),
  probed only near the widget, at most twice a second, on foreground changes and a backing-off
  tick. It hides within 8 px and needs two clear readings 300 ms apart to come back. A widget dropped
  onto the buttons is your choice: that offset is saved and the yield stays off for it. No UI
  Automation, a failed probe, Windows 10 or ExplorerPatcher taskbars, side bars and Free Move all
  keep the old behavior. Once stepped aside, an unknown reading on the same taskbar (a failed or
  timed-out probe) keeps it aside for up to 15 s, re-probing about every second, so a storm of new
  windows doesn't flap it; past that it shows ([#444]). A button group that doesn't fit inside the
  taskbar's own window rect - read before and after each probe, so a sliding auto-hide bar is
  still covered - is an unknown reading too: right after a live 150% → 100% change with auto-hide
  on, Explorer reported the buttons at the old scale for a few seconds (reproduced in a VM), which
  stepped the widget aside for ~10 s ([#450]).*

- **On a side taskbar, the widget keeps clear of app buttons as they come and go.** rc.1 placed it
  once at startup; now apps opened later don't slide under it, a switch between centered and top
  icons re-places it, a drag stays below the Widgets button, and the "no free spot" notice no longer
  takes the keyboard. ([#406])

  *Under the hood: an out-of-context `EVENT_OBJECT_LOCATIONCHANGE` hook on Explorer, filtered to the
  Start / ReBar / task-list windows. Start reaches its final spot 15-30 ms before UI Automation does,
  so only a predicted collision runs a probe. `TaskbarAl` is read on each burst because its
  `WM_SETTINGCHANGE` broadcast never arrives on 26200. A re-check landing mid-drag or with the menu
  open waits until you let go.*

- **Starting NetSpeedTray again brings the widget back.** A second launch used to exit silently, so
  a hidden or off-screen widget needed Task Manager. Now it shows the running widget, back on its
  taskbar if it was off-screen - without opening a window or changing your saved position, so a
  duplicate start at sign-in stays invisible. ([#440]; [#234], thanks [@VenusGirl])
- **Usage by day, month and year.** The Overview's usage card has a **Usage by day** link: a table of
  Days, Months and Years, newest first, with Months following your data-cap billing period when the
  reset day isn't the 1st. **Export CSV** writes the rows in bytes. ([#439]; [#334], thanks
  [@Falenone])
- **Back up history.** Settings → Advanced → Back up history saves a checked copy of your history to
  a file while the app keeps recording - to keep it safe, or to move it to a new PC
  ([DATABASE.md](DATABASE.md) has the steps). Restoring from inside the app is planned for 2.3.
  ([#439])

  *Under the hood: `VACUUM INTO` from a dedicated read-only connection, which includes rows still in
  the `-wal` and comes out as one rollback-journal file. The copy is written to a `.partial-<pid>`
  name, verified (`quick_check`, `db_version`, the table set), then `os.replace`d onto the name you
  chose, so a failure leaves nothing behind. The live file's own name is refused under every
  spelling (resolved path plus `samefile`, so 8.3 names and junctions too). Quitting mid-copy
  interrupts it and cleans up.*

- **Network details on Check connection:** your adapter, IPv4 and IPv6 addresses, and DNS servers.
  Copy figures includes the adapter, private IPv4 and DNS, and leaves out IPv6 and any public
  address. ([#437]; [#214], thanks [@CMTriX])
- **The data-cap card says how much of the period was measured** - "NetSpeedTray ran 92% of this
  period · This PC only" - with a tooltip on what the cap can and can't see. When part of the period
  predates the app's records, it shows "This PC only" and doesn't guess. ([#437])
- **Pick which GPU to show**, in Settings → Hardware, on PCs with two or more. Automatic (the busiest)
  stays the default. Temperature and power show only from a sensor of the picked GPU's maker;
  otherwise they read N/A rather than borrow another card's. ([#435]; [#236], thanks
  [@balciseri])
- **Temperatures in °C or °F**, in Settings → Hardware, on the widget, the hover card and the
  Monitor. History and CSV exports stay in °C. 1.3.1's changelog said Fahrenheit was planned for
  1.4.0; it's a little late. ([#435])
- **LibreHardwareMonitor with a password, on any port.** Settings → Hardware → LibreHardwareMonitor
  connection takes the port and an optional user name and password, and says live whether LHM
  answers, asks for a password, or refused it. ([#433])

  *Under the hood: the password is encrypted for your Windows account with DPAPI; the settings file
  holds only that blob, and the support bundle redacts it and the user name. Credentials are only
  ever sent to 127.0.0.1. LHM 0.9.5/0.9.6 save the SHA-256 of the password and hash it again at
  startup, so after each LHM restart only the hash of the old password works - the status line says
  to set it again in LHM. Fixed on LHM's master, not in a release yet.*

- **IT admins can turn update checks off machine-wide**, with
  `HKLM\SOFTWARE\Policies\NetSpeedTray` `DisableUpdateCheck` = 1 (HKCU works per user). The app then
  never contacts GitHub, the menu's Check for Updates is hidden, and Settings shows "Managed by your
  organization". New [DEPLOYING.md](DEPLOYING.md) covers silent installs, the policy and what the app
  connects to. ([#430], [#431])

### Changed

- **The Monitor's network graph labels its axes and its statistic.** The axes say the unit -
  "Download (MB/s)" - and follow your unit setting with round ticks. Past 6 h, a caption says each
  point is the peak of its minute, hour or day, since the header above shows volume. ([#436])
- **Settings titles are in sentence case, without trailing colons**, in every language ("Show mini
  graph", "Graph timespan"). Window and command names keep their capitals. ([#432])
- **The monitoring modes explain what they count.** Auto's tooltip says it follows the adapter your
  traffic uses - usually the VPN's when one is connected - and that "Physical adapters only" is
  closer to what your provider measures. The README's "ignoring VPNs" line was wrong and is fixed.
  ([#437])
- **Grayed-out hardware settings say why** when you hover them - "Turn on CPU monitoring to use
  this", and so on. ([#435])
- **Settings → Widget says when your preferred monitor has no taskbar**, so the widget sits on the
  primary one, and which switch keeps it on that monitor. ([#440])
- **New High/Medium/Low colors that stay apart with red-green color blindness.** Dark taskbar: Low
  #FFA500 (unchanged), Medium #FFFF80, High #8CF2C3 instead of #00FF00. Light taskbar: #57190F,
  #764B0A and #145252. A High left at exactly #00FF00 moves to the new one; any other color is yours
  and stays. ([#446]; [#9], [#332])

  *Under the hood: chosen for ≥14 pairwise ΔE2000 under normal, deuteranopia and protanopia
  simulation, and ≥4.5:1 on #202020, #2B2B2B, #EEEEEE and #E8E8E8; tests check both.*

- **The widget uses Segoe UI Variable, like the taskbar clock**, and falls back to Segoe UI where it
  isn't installed (Windows 10). Light-taskbar text is the clock's #1B1B1B instead of pure black, and
  the arrows use the text's own font and weight instead of a separate 10 pt DemiBold. Only the old
  defaults move; a font or arrow font you picked stays. ([#446])

  *Under the hood: stored as the family Qt resolves, "Segoe UI Variable", with `opsz` 10.5 (the Text
  optical size). Over a mid-gray widget background the factory light text drops to black to stay
  at 4.5:1 or above.*

- **Mini graph:** Detailed draws with a plain blend instead of a glow, with a lighter fill; idle
  traffic no longer fills the graph, because its scale never drops below 1 Mbps (it was 100 kbps);
  and the Split graph's upload line is a muted blue. The Monitor window's graphs don't change.
  ([#446]; [#117])

  *Under the hood: `CompositionMode_Plus` → normal blend, fill start alpha 80, opacity still 66%;
  `MIN_Y_SCALE` 12,500 → 125,000 B/s; upload line #6F86A6 (dark) / #6A7FA3 (light).*

- **Side taskbars:** a 3 px gap after the arrows, and the compact unit row keeps the full "Mbps" or
  "MB/s", stepping its font down to fit. Only a 32 px Small bar with binary units, where even 6 pt
  can't fit, falls back to the short label. ([#446])

### Fixed

- **Update checks work on a fresh Windows install.** Windows doesn't ship every root certificate it
  trusts - it downloads one the first time something needs it - and the update check couldn't
  trigger that download, so until a browser had fetched GitHub's root, every check failed with a
  certificate error. Updates now use Windows' own certificate verification, the way browsers do.
  (2.1.7 too.) ([#451])

  *Under the hood: Python's `ssl` copies only the roots already in the store into OpenSSL and never
  triggers CryptoAPI's on-demand fetch from Windows Update, so a fresh install without USERTrust ECC
  got `CERTIFICATE_VERIFY_FAILED`. The update check, the installer/ZIP download and `checksums.txt`
  all go through the new `utils/https.urlopen`, which uses a `truststore.SSLContext` - chain and host
  name verified by `CertGetCertificateChain` and the SSL policy. Without truststore it falls back to
  `ssl.create_default_context()`, and a context that doesn't verify is refused. Authenticode, the
  SignPath pin and the checksum gate are untouched. On a fresh VM with 21 roots, plain OpenSSL
  failed and this build reported "Up to date" a second later.*

- **Check connection and the Router pill say "No connection" when the cable or Wi-Fi drops.** A
  pulled cable read as "your router doesn't answer pings", and the pill kept "Router: Good" for about
  two minutes before going blank. Now the pill shows "Router: No connection" in red from the first
  dead cycle and goes back to normal on the first reply. Check connection says to check the cable or
  Wi-Fi when the adapter has no link, or that the connection is lost when a router that was
  answering stops; a router that never answers pings keeps the old note. ([#451])

  *Under the hood: `latency_probe.link_state()` reads psutil `isup` for the adapters on the
  router's subnet - local state only, nothing is sent. `LatencySample.gw_link_lost` is set when a
  router that has answered loses a whole burst or its route, or when no adapter has a link; a router
  that never answered, or an answering internet hop, never sets it.*

- **The metrics endpoint answers Windows PowerShell on `[::1]`.** .NET sends the long IPv6 form
  (`[0000:…:0001]`) in the Host header, which got a 403. A bracketed IPv6 or dotted IPv4 literal is
  accepted when it's loopback - a literal can't be DNS-rebound - and names are still limited to
  `localhost`. ([#451])
- **On a left or right secondary taskbar, the widget no longer covers the clock.** With Show on all
  taskbars and a side taskbar, every copy sat on its secondary bar's clock, and so did a main widget
  docked on a secondary side bar. ([#449])

  *Under the hood: a Windows 11 secondary taskbar has no `TrayNotifyWnd`, so `side_bar_geometry()`
  took the bar's bottom edge as the tray end. The side-bar UIA probe now also returns the span of
  the `SystemTray.*` buttons, kept per taskbar HWND and dropped when that bar's dock changes (a live
  100% → 150% change re-docks the same HWND, and the old clock rect still fit). Until a probe lands,
  the tray end is the bar bottom minus `SECONDARY_SIDE_CLOCK_RESERVE_PX` (44, scaled). Drag limits
  use the same geometry.*

- **After an Explorer restart the widget no longer floats at the bottom-right corner** for a couple
  of seconds before the new taskbar exists. It hides and comes back at its spot. (2.1.7 too.)
  ([#440])
- **Hovering the Monitor's graph shows values that were measured.** On Week and short sessions the
  tooltip read the smoothed, drawn line, so it could report a speed between two samples that never
  happened - 38.1 between samples of 50 and 10. (2.1.7 too.) ([#436])
- **The Monitor's network graph no longer draws unmeasured time as zero.** While the app was closed
  or the PC slept, the graph showed a dashed zero line, as if the connection had been idle; that's a
  gap now (2.1.7 too). Long raw windows also keep their short spikes when they're thinned for
  drawing - a 45 Mbps one used to come out at 22. ([#436])

  *Under the hood: the readers stopped padding windows with zero rows, and a new `measured_spans()` -
  app sessions minus the stretches flagged as standby or stalls - tells the renderer which silences
  were measured idle (drawn as zero) and which were not (the line breaks). Without coverage (the
  in-memory Session, a v7 file, a failed read) every silence breaks the line, so it never claims a
  zero. Thinning to 2,000 points now keeps each bucket's peak row instead of every Nth row.*

- **An opaque widget background keeps its text readable.** Automatic text followed only the
  taskbar's mode, so white text could land on an opaque light background, or black on a dark one.
  (2.1.7 too.) ([#441])
- **Full-screen Remote Desktop across several monitors hides the widget**, as any full-screen app
  does; "Keep visible in fullscreen" still opts out. (2.1.7 too.) ([#440]; [#132], thanks
  [@georgi-iovchev])
- **Windows 10: the widget no longer overlaps the taskbar's Desktop, Links or Address toolbars.** It
  sits left of them. Not tested live yet - Windows 10 support is best-effort, and Windows 11 has no
  such toolbars, so nothing changes there. (2.1.7 too.) ([#440]; [#324], thanks [@Achaean])
- **Windows 10: the widget no longer blinks out when you maximize and restore windows.** Each
  maximize and restore puts the taskbar over the widget, and rc.1 counted every recovery toward its
  "fighting the shell" backoff, so quick toggling left it hidden for 0.6 to 2.8 s. It now comes back
  in a few milliseconds (6 ms median in a Windows 10 test). Windows 11 doesn't bury the widget on
  maximize/restore, so nothing changes there - except that the widget is back about 0.1 s sooner
  after Task View or Win+Tab. Windows 10 support is best-effort. ([#424], [#426], thanks
  [@pasharm] for the diagnosis and the measurements)

  *Under the hood: a burial counts toward the backoff only when it lands within 250 ms of our own
  re-seat, which is what a real Z-order fight looks like; anything later is a fresh burial,
  re-seated at once and free. More than 12 free re-seats in 15 s counts as a fight too, so an
  unknown periodic re-burier still decays to the 1-60 s backoff. The Z-order recheck coalescer
  went from 100 ms to 16 ms (one frame), because a maximize fires a burst of desktop
  `EVENT_OBJECT_REORDER`s and the burial lands 10-20 ms in. When `SetWindowPos` is refused (error
  5), the re-seat retries after the first window above the taskbar in the taskbar's own Z-band, and
  logs that window's class and band once per episode.*

- **Text stays readable over the mini graph.** The graph was drawn under the text, so a busy graph
  behind colored digits could make them hard or impossible to read. Now the graph is erased in a
  thin outline around the text. Copies on other taskbars get it too. (2.1.7 too.) ([#446]; [#117])

  *Under the hood: with a graph on, graph and text are drawn into separate premultiplied layers at
  the target's device pixel ratio; the rasterized text alpha, grown to 1.5 px × DPR plus a 1 px
  feather, is cut out of the graph layer with `DestinationOut`, then both are composited over the
  background, which is painted straight onto the target and never erased. Using the rasterized
  alpha rather than glyph paths covers icons, the identity badge and the stepped-down unit row with
  no per-call-site wiring. About +2 ms a frame at 150-200% (3.8 vs 2.2 ms), nothing at 100%. Across
  a 170-scenario contrast render set, text under 4.5:1 went from 34 scenarios (11 under 3:1) to
  none; the lowest is 4.69:1, with no graph involved.*

- **High Contrast themes work.** The widget paints in the theme's text color, hides the mini graph
  and drops color coding and its background - without touching your settings - and follows a theme
  change live. (2.1.7 too.) ([#446])

  *Under the hood: `SPI_GETHIGHCONTRAST` at paint time; every pen becomes `COLOR_WINDOWTEXT`, and
  `graph_enabled` stays as configured so the layout keeps its shape. A change arrives through
  `WM_SETTINGCHANGE(SPI_SETHIGHCONTRAST)`, `WM_SYSCOLORCHANGE` or `WM_DWMCOLORIZATIONCOLORCHANGED`,
  deduped by `check_shell_theme`. Checked live on all four Windows 11 contrast themes.*

- **An accent-colored taskbar keeps the widget readable, in light mode too.** The widget assumed
  the bar was #202020 or #EEEEEE. It now reads the real accent color, and any color under 3:1
  against it - Brand CPU/GPU labels included - is drawn in the normal text color instead; your
  settings aren't changed. If the accent shade can't be read, color coding turns off. Current
  Windows 11 (build 26300) paints the accent bar in light mode too, with a white clock, so the text
  color is judged from the bar itself; Windows 10 keeps the dark-mode-only check. (2.1.7 too.)
  ([#446])

  *Under the hood: the bar color is `AccentPalette[5]`, which matched the painted taskbar exactly
  with transparency on and off.*

- **Changing the display scale while the app runs re-sizes and re-places the widget.** The scale was
  read once per monitor and kept for the session, so going 150% → 100% left a widget sized for a
  Small taskbar, and 100% → 150% made the app-button check see buttons that weren't there - a false
  step-aside and the "no free spot" notice - until a restart. Copies on other taskbars read the
  scale the same way and follow too. (2.1.7 too, for the sizing.) ([#444])

  *Under the hood: `get_dpi_for_monitor` cached per HMONITOR since 1.0.5, but a monitor keeps its
  handle across a live Settings → Display → Scale change. It's read on every call now, and a failed
  handle falls back to the live QScreen ratio instead of 1.0. The #24 check judges in one coordinate
  space - the monitor's scale at the moment of judging (`current_dpi_for_hwnd`) - returns busy while
  the 1 s taskbar snapshot disagrees, and drops its measured group and re-probes at once after a
  change. Qt's device pixel ratio catches up about 3 s after the OS scale, so
  `resize_widget_for_font` records the ratio it measured at and the widget re-sizes once more when
  it moves: 100% → 150% → 100% used to end at 87×48 instead of a fresh start's 85×48.*

- **The speed-threshold boxes show and take your unit.** They were labeled MB/s (or Mibps, MiB/s)
  but stored and applied whatever you typed as Mbps, so "5 MB/s" colored at 5 Mbps. Stored values
  are unchanged; only the box converts. (2.1.7 too.) ([#441])
- **With data usage in Auto mode, traffic before your connection comes up at sign-in is counted.**
  With Wi-Fi slow to connect, the bytes before the active adapter was known were dropped, and so was
  the closed-app recovery for that launch. ([#437])
- **The hover card's pin hint points at the widget:** "Click the widget to keep this open". An
  unpinned card lets clicks through to the app behind it, so the click that pins goes on the widget;
  rc.1's changelog and notes described it wrong too. ([#429])
- **Settings expanders open from the keyboard.** "Advertised plan speed" and "Color-code by load"
  opened only with the mouse; their headers are now Tab stops that Space and Enter toggle, and
  Narrator reads "collapsed" or "expanded". (2.1.7 too.) ([#432])
- **A setting that fails to apply is rolled back properly.** The old settings were saved, but parts
  of the app kept running the new ones until a restart; a failed live preview could also write the
  file. (2.1.7 too.) ([#432])
- **The Monitor's Network header fits at the minimum width when "From recorded history" shows.** rc.1
  said it was fixed in every language; with that note up - every upgrade from 2.1.x - it still
  squeezed the period menu and the Live pill in all 16. The note now narrows, then moves into the
  totals' tooltip. ([#432])
- **With a preferred monitor whose taskbar is on another edge, the widget starts on that taskbar and
  paints for it**, instead of laying out for the primary's taskbar. ([#434], [#440])
- **An eGPU plugged in without a display change is picked up** within a poll; VRAM no longer stays in
  "shared" mode until something else rebuilt the GPU list. ([#435])
- **The installer runs in 64-bit mode on Windows 11 on ARM**, so it installs to Program Files instead
  of Program Files (x86). (2.1.7 too.) ([#431])
- **The history upgrade survives a clock set back into its hourly data**, and a file held for a moment
  by antivirus no longer fails it; before, both stopped the upgrade until the next start. ([#437])
- **Repeated settings conversions on the same version each keep a backup** of the state just before
  them (the newest three, plus the first). ([#437])
- **Reset Position no longer shows for very old default offsets** from configs that predate 1.3.0.
  ([#440])
- **The Setup log is honest about silent SYSTEM installs:** it said the app would start at each user's
  next sign-in, but it only does after they've started it once. ([#430])
- **privacy.md and DATABASE.md describe what the app really does** - when it checks for updates, what
  the support bundle redacts, the LibreHardwareMonitor and Check connection traffic, and the real
  Settings labels. ([#431])

### Security

- **The LibreHardwareMonitor hint no longer tells you to leave LHM open to your network.** rc.1 said
  to turn its authentication off and let it listen on all interfaces, which exposes its sensor server
  to the LAN. LHM up to 0.9.6 can't listen on 127.0.0.1 only, so the hint now says: don't open its
  port in your firewall, pick 127.0.0.1 once a newer LHM offers it, and enter the password here if
  you turn authentication on. ([#433])
- **Portable updates check the publisher's signature on the new NetSpeedTray.exe** before anything is
  staged, on top of the download checksum. An unsigned or wrongly signed exe is refused, nothing is
  installed, and the release page opens instead. ([#430])

  *Under the hood: WinVerifyTrust with whole-chain revocation plus the SignPath pin (Subject CN
  SignPath Foundation, Issuer GlobalSign GCC R45 CodeSigning CA 2020). This covers the bootloader exe
  only - the `_internal\` payload is still vouched for by `checksums.txt` alone, so it's defense in
  depth against a swapped exe, not proof against a swapped release. A locally built portable zip
  can no longer be applied through the in-app updater.*

### Performance

- **Check connection no longer makes the widget and graphs hitch.** Each check's network-details
  read ran a full Python garbage collection, which held the app for about 75 ms (median, idle
  machine); it's under 2 ms now. ([#443])
- **The first session after the history upgrade has no write burst a few minutes in.** The upgrade
  now flushes its leftover write-ahead log while nothing else reads the file, instead of leaving
  ~3 MB for an automatic checkpoint ~5 minutes later. ([#437])
- **The hourly maintenance pass never stalls history writes while the Monitor reads.** It waited up
  to 5 s on a reader; a busy checkpoint now just retries when the app is idle. ([#437])

### Localization

- Hungarian rc.1 strings refined by a native speaker, with LibreHardwareMonitor's menu names
  localized. ([#414], thanks [@cscsanaki])
- Turkish typos and wording fixed, with Windows' own term for network adapters. ([#423], thanks
  [@lezgintekay])
- Settings titles moved to sentence case in all 16 languages and lost their trailing colons; French
  and Turkish had copied the English Title Case and now follow their own rules. ([#432])
- German: the failed-update message is translated, and the temperature guidance gets back the line
  about very new CPUs whose sensors LHM can't read yet; plus clearer wording for the Black font
  weight, power readings and GitHub's star, and consistent graph terms and Mbit/s. ([#427], thanks
  [@emgeka])
- 89 new strings and 62 changed ones (most of those the sentence-case pass) are AI-drafted in every
  language and listed for review in [#364].

### Developer notes

- Release notes now live in `.github/release-notes/vX.Y.Z.md`; CI publishes them exactly as before.
  Proven with a throwaway tag that was cancelled before signing. ([#422])
- Config schema 1.3: the 16 themed color keys, `medium_speed_threshold`, `hide_when_apps_cover`,
  `show_on_all_taskbars`, `gpu_adapter`, `temperature_unit`, `lhm_port`, `lhm_username` and
  `lhm_password_protected`; `app_overlap_drop_offset` is app state, kept by Reset all. Legacy color
  keys stay in the file for rollback. ([#433], [#434], [#435], [#438], [#441])
- Config schema 1.4, applied once to files older than 1.4 and only where a value is exactly the old
  factory one: dark High `#00FF00` (case-insensitive) → `#8CF2C3`, font "Segoe UI" → "Segoe UI
  Variable", and the separate Segoe UI 10 pt DemiBold arrow font turned off. The bump keeps a
  `.bak.v1.2` (or `.bak.v1.3`). It also fixes #441's 1.2 → 1.3 seeding, which compared against the
  *current* dark defaults - with the new High, a 2.1.7 factory `#00FF00` would have been copied to
  both taskbars as a user pick. It now compares against the pinned pre-1.3 factory values
  (`LEGACY_COLOR_FACTORY`). ([#446])
- The #441 byte-identical render golden was re-based on the new look (which changes every frame on
  purpose), with rc.1 configs going through the real 1.2 → 1.4 migration. ([#446])
- The build ignores PATH folders that shadow System32's `icuuc.dll` / `icuin.dll` while PyInstaller
  analyses QtCore - with Poppler's bin on PATH, the exe died at startup with "DLL load failed: The
  specified procedure could not be found" - and stops outright if either DLL gets bundled anyway.
  Builder-side only; shipped builds weren't affected. ([#425], thanks [@emgeka]; [#445])
- **Golden tests pin the parts that must not move:** 160 top/bottom placements generated on rc.1
  ([#406]), 144 color-scheme render scenarios ([#441]), and 57 GPU "Automatic" polls across 14
  machine types ([#435]).
- RAM is collected when CPU or RAM is on, VRAM when GPU or VRAM is on; where they're shown is
  unchanged (RAM rides on the CPU row, VRAM on the GPU row). ([#435])
- `wmi` is imported on the GUI thread at startup and each WMI query breaks the module's class-cache
  cycle itself, so COM proxies are released on their own thread and the "Win32 exception releasing
  IUnknown" line at exit is gone. ([#443])
- Logging: copies of the widget tag their lines `[mirror DISPLAY-…]`; a re-seat in the first 5 s
  after docking or a tray change logs at DEBUG and no longer uses up the one INFO burial line; the
  click-through warning logs the style after the change; installed builds no longer log "The
  running install looks incomplete" on every launch - the post-update cleanup skips quietly without
  `portable.marker`, since only the portable updater leaves anything to sweep. ([#434], [#440],
  [#450])
- New dependency: `truststore` 0.10.4 (MIT, pure Python, the one pip vendors), pinned in
  `requirements.in/.txt` and `dev-requirements.txt`. The build fails if it isn't bundled, and a
  source scan fails the suite on any `urlopen`, `HTTPSConnection` or `create_default_context`
  outside `utils/https.py`. ([#451])
- Four load-dependent test flakes made deterministic - Check connection quit, two monitor-thread
  polls, the migration random-kill test and the side-bar first placement - without weakening what
  they check. ([#442])
- `build/version_info.txt` is no longer tracked (both build scripts generate it), and the
  American-spelling test covers DEPLOYING.md and DATABASE.md. ([#431])
- 6,564 tests.

[#9]: https://github.com/erez-c137/NetSpeedTray/issues/9
[#24]: https://github.com/erez-c137/NetSpeedTray/issues/24
[#117]: https://github.com/erez-c137/NetSpeedTray/issues/117
[#132]: https://github.com/erez-c137/NetSpeedTray/issues/132
[#214]: https://github.com/erez-c137/NetSpeedTray/issues/214
[#229]: https://github.com/erez-c137/NetSpeedTray/issues/229
[#234]: https://github.com/erez-c137/NetSpeedTray/issues/234
[#236]: https://github.com/erez-c137/NetSpeedTray/issues/236
[#324]: https://github.com/erez-c137/NetSpeedTray/issues/324
[#332]: https://github.com/erez-c137/NetSpeedTray/issues/332
[#334]: https://github.com/erez-c137/NetSpeedTray/issues/334
[#364]: https://github.com/erez-c137/NetSpeedTray/issues/364
[#406]: https://github.com/erez-c137/NetSpeedTray/pull/406
[#414]: https://github.com/erez-c137/NetSpeedTray/pull/414
[#422]: https://github.com/erez-c137/NetSpeedTray/pull/422
[#423]: https://github.com/erez-c137/NetSpeedTray/pull/423
[#424]: https://github.com/erez-c137/NetSpeedTray/pull/424
[#425]: https://github.com/erez-c137/NetSpeedTray/pull/425
[#426]: https://github.com/erez-c137/NetSpeedTray/pull/426
[#427]: https://github.com/erez-c137/NetSpeedTray/pull/427
[#429]: https://github.com/erez-c137/NetSpeedTray/pull/429
[#430]: https://github.com/erez-c137/NetSpeedTray/pull/430
[#431]: https://github.com/erez-c137/NetSpeedTray/pull/431
[#432]: https://github.com/erez-c137/NetSpeedTray/pull/432
[#433]: https://github.com/erez-c137/NetSpeedTray/pull/433
[#434]: https://github.com/erez-c137/NetSpeedTray/pull/434
[#435]: https://github.com/erez-c137/NetSpeedTray/pull/435
[#436]: https://github.com/erez-c137/NetSpeedTray/pull/436
[#437]: https://github.com/erez-c137/NetSpeedTray/pull/437
[#438]: https://github.com/erez-c137/NetSpeedTray/pull/438
[#439]: https://github.com/erez-c137/NetSpeedTray/pull/439
[#440]: https://github.com/erez-c137/NetSpeedTray/pull/440
[#441]: https://github.com/erez-c137/NetSpeedTray/pull/441
[#442]: https://github.com/erez-c137/NetSpeedTray/pull/442
[#443]: https://github.com/erez-c137/NetSpeedTray/pull/443
[#444]: https://github.com/erez-c137/NetSpeedTray/pull/444
[#445]: https://github.com/erez-c137/NetSpeedTray/pull/445
[#446]: https://github.com/erez-c137/NetSpeedTray/pull/446
[#449]: https://github.com/erez-c137/NetSpeedTray/pull/449
[#450]: https://github.com/erez-c137/NetSpeedTray/pull/450
[#451]: https://github.com/erez-c137/NetSpeedTray/pull/451
[@2g4b]: https://github.com/2g4b
[@Achaean]: https://github.com/Achaean
[@Angel-Informatico]: https://github.com/Angel-Informatico
[@balciseri]: https://github.com/balciseri
[@CMTriX]: https://github.com/CMTriX
[@cscsanaki]: https://github.com/cscsanaki
[@dovletix]: https://github.com/dovletix
[@emgeka]: https://github.com/emgeka
[@Falenone]: https://github.com/Falenone
[@georgi-iovchev]: https://github.com/georgi-iovchev
[@lezgintekay]: https://github.com/lezgintekay
[@pasharm]: https://github.com/pasharm
[@relu616]: https://github.com/relu616
[@thefaboss]: https://github.com/thefaboss
[@VenusGirl]: https://github.com/VenusGirl

---

## [2.2.0-rc.1] - October 22, 2026

*A release candidate, for testers. It's a manual download from the release page - the in-app
updater, WinGet and the Store never offer prereleases.*

The release candidate: unless something comes up, this becomes 2.2.0. It carries the fixes from a
release-wide checkup, a hover card that can hold your hardware stats and your battery, support for
current LibreHardwareMonitor, and the root-cause fix for Smart App Control blocking a bundled file.

> **Upgrading:** from any 2.2 beta, just install over it - the running app closes itself and comes
> back. From 2.1.x, everything in the beta.1-beta.3 notes applies (the Trend mini graph has been the
> default since beta.3).
>
> - **CPU-only, GPU-only, Combined and Auto-Cycle with RAM/VRAM labels on** start at their full
>   width from the first second. The memory value used to be clipped until the first hardware
>   reading arrived (2.1.7 too), so the widget can look wider right after a restart than it did -
>   it's the width it always grew to.
> - **Reset all resets hidden settings again**, including adapter exclusions and the Monitor's graph
>   options, as 2.1.7 did. Onboarding, window positions and app state are kept.
> - **CSV export:** the time-series file has a new `aggregation` column and holds only stored rows,
>   without zero padding at the window edges. A script that reads columns by position needs a look.
> - **The Monitor's Overview:** if you had picked Session, it shows 24 h once after this upgrade.
>   Pick Session again and it sticks.
> - **History pruning waits about 7 days after this upgrade.** The new clock guard only trusts a
>   clock it has seen run continuously, so every pre-rc.1 or freshly migrated history file waits ~7
>   days before its first prune. Roll-ups run as usual; the file just doesn't shrink for a week.
> - **One copy per Windows user.** A second user signed in on the same PC can now run their own
>   NetSpeedTray; the same user still gets one copy, across sessions too.
> - **Portable updates are staged in `%LOCALAPPDATA%\NetSpeedTray\updates`**, not Downloads.
> - **Metrics endpoint:** `gpu_memory_used_bytes` / `gpu_memory_total_bytes` now carry a
>   `kind="dedicated"` or `kind="shared"` label, which changes the series for a scraper set up on a beta.

### Added

- **Show hardware only when hovering the widget.** Opt-in, in Settings → Hardware → Appearance.
  With CPU or GPU monitoring on, the taskbar shows network speed and the mini graph only -
  Side-by-Side goes from 322 to 83 px wide - and the hover card carries live CPU/GPU %,
  temperatures, power and RAM/VRAM. Turn it off and your previous layout is back exactly. ([#399])

  *Under the hood: one `effective_display_mode()` decides what the bar shows for sizing, painting,
  the Settings preview, the Auto-Cycle timer and scroll; the saved Display Mode is never rewritten.
  Hardware collection is still gated only by the monitoring switches - the widget just stops
  repainting for hardware it no longer shows. Auto-Cycle stops rotating while the switch is on.*

- **A battery section in the hover card, for laptops.** Opt-in, in Settings → General →
  Interaction. It shows the live draw (green `+W` while charging, "On AC" when idle on power),
  today's average, projected runtime and the charge level. A row without real data is left out,
  and desktops never see the section. ([#323], thanks [@kamal63442])

  *Under the hood: the persisted `system_power` stays a non-negative draw; the signed battery
  reading splits at the poll - discharge to `system_power`, charge to a hover-only
  `battery_power`. Mode and charge come from `GetSystemPowerStatus`, because WMI omits
  `EstimatedChargeRemaining` on several firmwares, and signed watts outrank its flags (an IdeaPad
  reported "no battery" while charging). Readings above `IMPLAUSIBLE_MW` (500 W) are dropped at the
  poll, so AC-transition garbage never reaches the history.*

- **Current LibreHardwareMonitor works.** LHM 0.9.5 and newer removed the WMI interface we read, so
  CPU/GPU temperatures and power needed the old 0.9.4. NetSpeedTray now reads LHM's own web server:
  turn on Options → Remote Web Server (port 8085) in LHM, and Settings → Hardware says so. LHM 0.9.4
  and OpenHardwareMonitor still work. It also gives CPUs stuck on a flat ACPI reading ([#237]) a
  real temperature. ([#276], thanks [@emgeka]; fixes [#187])

  *Under the hood: `http.client` on a numeric loopback socket (no system proxy), one 0.5 s total
  deadline across connect, headers and body (a trickling server held a poll for 13.9 s before), a
  0.1 s connect so the no-LHM attempt every 30 s is cheap, a size cap, and only LHM's root JSON
  shape counts as success. Sensors are picked by `SensorId`/`HardwareId`, so a motherboard "CPU
  Socket" reading or an APU's iGPU isn't taken for the CPU or the discrete card. Failures back off
  for 30 s. Source order is HTTP, then WMI, then ACPI.*

- **A crash log.** A crash at sign-in used to leave no trace. `NetSpeedTray_Crash.log` in
  `%APPDATA%\NetSpeedTray` (rotated) now catches it from the first line of startup, and the support
  bundle includes it, scrubbed like the rest. ([#408])
- **A pause cue.** A paused widget dims its values and shows a pause mark in the arrow column; the
  mini graph freezes with the numbers. Same size, no overlap. ([#401], [#405])
- **Ctrl+Tab, Ctrl+Shift+Tab and Ctrl+PgUp/PgDn switch Settings pages**, and the sidebar takes
  keyboard focus. ([#409])
- **Monitor graph tooltips show the date** on Week, Month and All. ([#409])
- **"Open folder" on the support-bundle message**, with the zip selected in Explorer. ([#401])
- **Click to pin the hover card.** A click keeps a hover-opened card open; it refreshes live,
  follows the widget, and closes on a click on the card or the widget, another app coming to the
  front, the menu, a drag, fullscreen, or a taskbar or theme change. The card itself never takes
  focus or passes a click through to the app behind it. Double-click still opens the Monitor.
  "Click to keep this open" shows on the first few cards. Every taskbar edge. ([#420])

  *Under the hood: an unpinned card is click-through via the native `WS_EX_TRANSPARENT` style only,
  set before its first show and cleared on pin - Qt's `WindowTransparentForInput` never delivers
  mouse events again once set, which a real-window test now catches.*

- **Reset Position** in the right-click menu, once you've moved the widget. It also resets the tray
  offsets. ([#420])
- **An integrated GPU's memory use shows as VRAM.** An Intel or AMD iGPU has no dedicated memory, so
  VRAM read 0.0; it now shows the system RAM the iGPU is using, out of its shared budget. The hover
  card and the Monitor label it "VRAM (shared)". A discrete GPU reads exactly as before. ([#421])

  *Under the hood: whether a PC has dedicated video memory is a fixed per-adapter fact from DXGI
  `DedicatedVideoMemory` (>= 512 MiB, or nvidia-smi present), matched to PDH by adapter LUID, so an
  idle Arc or Radeon at startup stays dedicated. Intel reports only its BIOS stolen chunk there (128
  MiB on a UHD 770). The Basic Render adapter is ignored, so a VM keeps VRAM hidden. AMD APUs whose
  carve-out is 512 MiB or more keep today's readout of the carve-out.*


### Changed

- **The hover and Pause toggles moved to Settings → General → Interaction**: "Add Pause/Resume to
  the right-click menu", "Show data usage when hovering the widget" and "Show right-click /
  double-click tips", next to the click actions they go with. Same settings, same defaults;
  Advanced's "Reset this page" no longer touches them. ([#400])
- **The hover card's usage rows and the Overview usage tile agree with your data cap.** With a reset
  day other than the 1st, the row reads "This Period" over your billing period. Today and This
  Period count only the adapters the cap counts, so a VPN or WSL isn't counted twice; with a cap
  set, the period row is the cap's own figure and Today never exceeds it. ([#401], [#407])
- **The first-run hover hint describes the real right-click menu** and your own double-click action.
  ([#401])
- **The Monitor's empty states say why.** "No history recorded yet", "History starts …" or "No
  traffic recorded in this period"; the Hardware tab says hardware history is off and points to
  Settings → Hardware or Settings → Advanced, instead of "Collecting data…" forever. ([#401], [#409])
- **CSV export marks what each row is.** The time-series file gains an `aggregation` column -
  `sample`, `minute_max`/`hour_max` for network roll-ups, `minute_avg`/`hour_avg` for hardware - and
  holds only stored rows for the window, without zero padding at the edges. ([#409])
- **An unplugged monitor or adapter stays selected in Settings**, shown as "not connected", instead
  of dropping out of your selection. ([#409])
- **One instance per Windows user.** A second user on the same PC can run their own copy; the same
  user can't run two, across sessions too. ([#408])

  *Under the hood: the mutex is `Global\NetSpeedTray_Instance_<SID>`. Older builds use the legacy
  name, so a same-user transition checks both (with a short race poll), and a failed WTS session
  scan falls back to psutil. Setup checks the session user's own mutex when WMI is broken.*

- **Long-range graphs build their day buckets faster** - a 400-day graph over ~620k stored rows went
  from 1.26 s to 0.54 s, with identical points. ([#411])
  *Under the hood: one localtime call per row; the local-midnight timestamp is computed once per day
  in Python (23/25 h days, repeated and skipped midnights handled).*

### Fixed

- **The data-cap count keeps going after you change the reset day.** Moving the day later froze
  "used this period" for up to a month, so alerts never fired and the metrics endpoint froze too.
  A regression from beta.1 ([#348]). Toggling the day back restores the exact previous count.
  ([#407])

  *Under the hood: the period is re-keyed explicitly and seeded honestly - from recorded history
  only where history counts exactly what the cap counts, never above the cap's own count in
  automatic mode, and never zeroed by a failed read. The previous period's count is remembered
  across restarts.*

- **A wrong system clock no longer wrecks your history or the data-cap period.** One maintenance
  pass with the clock far ahead rolled up and pruned all history (2.1.7 too); a clock far behind hid
  everything older than a day. A clock that ran ahead froze the cap's period, and one that was
  behind wiped it. ([#407])

  *Under the hood: watermarks move and pruning runs only while the wall clock is continuous -
  monotonic within a session (sleep and VM suspend allowed), or within -1 h/+14 d of the last
  trusted stamp across restarts. A new clock is accepted after 6 h of agreeing passes, pruning then
  waits 7 days, and a watermark is never lowered below rows already rolled up. A data-cap re-key
  needs the history writer's trusted clock stamp as evidence.*

- **Smart App Control should stop blocking `_wmi.pyd` on upgrade.** The build compressed the
  bundled Python files with UPX, which strips the Python Software Foundation's signature and gives
  each file a hash nobody else has, so SAC couldn't vouch for it. Nothing is compressed now. A clean
  install with SAC on already worked; an upgrade with SAC on is still to be confirmed. ([#333],
  thanks [@Keltian] for the CodeIntegrity events; [#408])

  *Under the hood: CodeIntegrity 3033/3077 named `\Program Files\NetSpeedTray\_internal\_wmi.pyd`.
  Without UPX, bundled .pyd/.dll files keep their vendor Authenticode signatures and well-known
  hashes - PSF-signed files go from 7 to 24, none is packed - and a post-build check fails on any
  UPX section. Portable zip +2.2 MB, installer -0.8 MB.*

- **Portable updates clean up after themselves.** The unzipped update stayed in Downloads and the
  previous version stayed beside the new one. The new version now removes both about 10 seconds
  after it starts, retries anything still locked on later launches, and clears the `.old-` folder
  2.1.x left behind. Your own files in the folder are never touched. ([#260], thanks [@kiro5678];
  [#402])

  *Under the hood: a folder is deleted only if it's exactly a NetSpeedTray portable layout (exe with
  a PE header, `portable.marker`, `_internal` with a PyInstaller fingerprint) and holds no links;
  directories are pinned by handle without following reparse points while they're emptied, and hard
  links are refused. The swap and its `<install>.previous-<ts>` backup are recorded in
  `update_backups.json`, which older builds never read. An update refuses to swap a folder holding
  your own files and falls back to the guided copy - before, those files went with the old version.*

- **A refused or rolled-back portable update tells you why** and relaunches the version you had,
  instead of the app just vanishing. `desktop.ini` and `Thumbs.db` no longer block it. ([#408])
- **The memory column is reserved from the start.** CPU-only, GPU-only, Combined and Auto-Cycle
  with RAM/VRAM clipped the memory value at cold start (2.1.7 too). The reserve is sized from the
  machine's total. ([#405])
- **The hover card never covers its widget.** It tries each side for every taskbar edge, flips when
  there's no room, and stays on its screen and off the taskbar. ([#405])
- **Switching Windows between light and dark re-colors the widget right away** (2.1.7 too), and
  Settings' live preview and Cancel use the live automatic color. ([#405])

  *Under the hood: event-driven from `WM_SETTINGCHANGE` "ImmersiveColorSet", no polling. The hover
  card is mouse-transparent, so a forced overlap can't start a hover flicker loop.*

- **A scaled secondary monitor below the primary no longer reads its taskbar as a top bar.** The
  edge is decided in physical pixels against its own monitor. ([#405])
- **At sign-in, before Explorer's taskbar is ready, the widget assumes a bottom bar**, not a left
  side one. ([#405])
- **Small side taskbars:** "100%" becomes "100" when the percent can't fit 32 px, and the unit row
  steps its font down from the widest unit of its family, so its size holds as traffic crosses
  K/M/G. Fixes Russian binary units overflowing. ([#405])
- **The hover card closes when you start dragging the widget.** ([#405])
- **The speed no longer reads high, then low, every 30 seconds with temperatures on.** A stall
  between reading the counters and showing them (a WMI query, an LHM connect) inflated one tick and
  deflated the next - measured 0.63× / 1.35×. A failed usage or history write now loses that one
  poll's bytes instead of counting them twice. ([#276])

  *Under the hood: the rate divided by the GUI thread's handling gap; it now uses the monotonic
  sampling span the odometer already used. Sample time and counters re-baseline together in one
  `finally`, so an exception between them can't leave them out of step.*

- **Reduce motion works.** On forces reduced motion; off follows Windows. ([#401])
- **A hand-edited decimal in config.json no longer breaks Settings.** `graph_opacity: 66.0` stopped
  a page from loading, and Save then reset about 25 settings, history retention from 365 to 31 days
  among them. Values are coerced on load, each page loads on its own, and a page that failed to load
  never writes its settings. ([#409])
- **Settings:** Esc undoes live-preview changes like Cancel; reopening Settings keeps the selected
  adapters; a typed hex text color counts as your choice, and a half-typed one keeps the last
  complete color; Cancel after Reset all restores your settings; Reset all covers hidden settings
  again (a beta regression); threshold units follow bits/bytes; the data cap accepts decimals;
  controls gray out when they'd do nothing. ([#409])
- **Monitor periods:** a period picked on the Overview reaches the Network and Hardware tabs,
  "System uptime" really starts at boot, "Since boot" after "All" starts at boot, and a chosen
  Session period is remembered. ([#409])
- **Network Session totals are time-weighted.** 600 MB showed as 300, 120 or 60 MB at 2, 5 or 10 s
  polling. ([#409])
- **Hour and day buckets follow local time, correct across DST.** The repeated fall-back hour stays
  two buckets. ([#409])
- **The Monitor:** no graph-host leak on every open, no error on quitting with the stats sheet open,
  and the Network header no longer overlaps at narrow widths in any language. Graph messages and the
  boot marker are shaped right-to-left in Hebrew. ([#401], [#409])
- **Installer:** silent installs (Intune, SCCM) no longer start the app as SYSTEM; the relaunch
  skips session 0 and Setup run as a different account (over-the-shoulder admin); an upgrade stays
  in your custom install folder, including one on a mapped drive under over-the-shoulder elevation.
  A missing local folder falls back to the default with a note on the Finish page. ([#408], [#410])

  *Under the hood: the mapped-drive check read `HKCU\Network`, which under over-the-shoulder
  elevation is the administrator's hive. It now reads `HKU\<session user SID>\Network\<letter>` when
  Setup runs as another account, and logs which hive it used. Accounts are compared as
  DOMAIN\user.*

- **Quitting never hangs** on nvidia-smi, DNS or the update check. ([#408])
- **The metrics endpoint answers on `[::1]` too**, falls back to 127.0.0.1 only when IPv6 is denied,
  and retries a busy port. ([#408])
- **A rare crash when the Monitor window, a data-cap alert or the hover card was cleaned up off the
  main thread.** They're now kept alive until Qt destroys them on the main thread. ([#408])
- **The upgrade minute shows up right after a v7 → v8 migration**, instead of hiding for up to an
  hour until the next roll-up. ([#407])
- **The "All interfaces" average speed is no longer overstated when several adapters carry
  traffic.** Hour and day buckets added each adapter's average rate, so the long-period average read
  high (0.0809 instead of 0.0770 Mbps on real data); this was in 2.1.x too. Volume, the data cap and
  the graphs were never affected. ([#413])

  *Under the hood: a rollup bucket's rate is now its bytes over the longest adapter's measured
  seconds, weighted by those seconds, in both history readers' summary and hourly profile. Bytes
  are `avg × min(sample_count, bucket length)`, so a minute holding a whole Modern Standby gap
  keeps its own average instead of dividing the gap's bytes by 60 s.*

- **Upgrading writes the converted settings once and keeps a backup first.** A 2.1.x or beta config
  was converted in memory on every launch and only written on the next change. It's now saved once,
  right after `config.json.bak.v<old>` is made, and never if that backup couldn't be made. A corrupt
  config that can't be moved aside is copied to `.corrupt` before anything overwrites it, and a
  config of `[]`, `null` or non-UTF-8 bytes is handled as corrupt instead of stopping the app. ([#412])
- **A config saved by older Notepad keeps its settings.** Notepad's UTF-8 BOM made the file read as
  corrupt, so it was set aside and every setting reset. It's read with the BOM now, and the next
  save writes plain UTF-8. ([#415])
- **The docked widget comes back when the taskbar buries it.**
  Maximizing and restoring a window (Windows 10), Windows 11 reordering its taskbar band, or an
  Explorer restart could leave the widget hidden behind the taskbar until you clicked it or
  relaunched. It's now re-seated right away. Opening Start or Task View doesn't count as burial,
  so closing them never leaves the widget hidden. ([#314], [#238]; [#327], thanks [@kamal63442] and
  [@pasharm])

  *Under the hood: burial is a Z-order comparison - walk `GW_HWNDPREV` from the docked taskbar (the
  owner, so secondary bars work) - not a hit-test, so no DPI conversion and a menu over the widget
  isn't burial. The re-seat inserts directly above the taskbar (`GetWindow(taskbar, GW_HWNDPREV)`,
  `HWND_TOPMOST` only when the taskbar is first, `SWP_NOOWNERZORDER`), so menus, jump lists and
  Start stay above the widget and #200 can't come back. Triggered by a process-wide
  `EVENT_OBJECT_REORDER` hook on the desktop window plus foreground changes, coalesced, with the 1 s
  tick as a safety net and a 1-60 s backoff as the fight guard. `TaskbarCreated` re-docks after an
  Explorer restart. Never while hidden, mid-drag, with our menu open, under a fullscreen app, or when
  the taskbar itself isn't topmost. Start and Task View lift the taskbar - and its owned widget -
  into a higher Z-band (`GetWindowBand` > 1), where the walk can't verify anything: the check
  defers without charging the backoff, and the first re-seat after the band drops back is free. A
  taskbar raised for over 60 s falls back to the plain check.*

- **After an Explorer restart the widget stays on the taskbar** instead of jumping to the far left
  over the Widgets button for up to ~11 s. The new taskbar reports a zero-width tray until it
  settles; the widget is placed from the last good tray geometry meanwhile. ([#419])

### Localization

- Hungarian reviewed by a native speaker, beta.3 and rc.1 strings both. ([#398], [#403], thanks
  [@cscsanaki])
- Dutch rc.1 strings reviewed by a native speaker. ([#404], [#416], thanks [@boonstraworks])
- Turkish thousands separator and "Memory", the Hungarian Cancel accelerator, Hebrew device names in
  the right direction, and the Monitor's labels translated. ([#409])
- Two leftover English strings translated: the graph boot marker and the support-bundle file
  filter. ([#401])
- 26 new strings and 3 changed ones are AI-drafted in every language and listed for
  review in [#364]. 19 unused strings were removed.

### Security

- **Pillow 13.0.0**, which matplotlib bundles. It fixes 28 advisories (3 critical: TIFF and JPEG
  heap overflows, Ghostscript argument injection). None was reachable - NetSpeedTray only writes
  its own graph images - but the bundle no longer carries them. ([#418])

### Developer notes

- **No UPX anywhere in the build**, enforced by a post-build check; Inno Setup is pinned to 6.7.1 in
  CI. ([#408])
- **The release body is published by CI** from `release-notes/vX.Y.Z.md` in the tagged commit, HTML
  comments (even unclosed) stripped, and the tag must equal `v` + `__version__`. ([#408])
- Config schema 1.2, so a 2.1.x `config.json` is backed up as `config.json.bak.v1.1` before 2.2
  rewrites its values; the runtime-only `taskbar_height` is no longer written. A test forces every
  new setting into either the Reset-all group or the kept group. ([#409])
- A test fails on unused locale keys; the dead data-cap dialog is gone. ([#401])
- [DATABASE.md](DATABASE.md) documents the reserved flag bits and the new clock metadata keys.
  ([#407])
- Tests redirect `APPDATA`/`LOCALAPPDATA` ([#402]); parentless test widgets are destroyed on the
  main thread, which fixed an order-dependent access violation, and helper-process tests no longer
  orphan children ([#408]); rate tests use a fixed monotonic baseline - `(t0 + 1.0) - t0` isn't 1.0
  just below a power of two ([#276]).
- 5,062 tests.

[#187]: https://github.com/erez-c137/NetSpeedTray/issues/187
[#237]: https://github.com/erez-c137/NetSpeedTray/issues/237
[#238]: https://github.com/erez-c137/NetSpeedTray/issues/238
[#260]: https://github.com/erez-c137/NetSpeedTray/issues/260
[#276]: https://github.com/erez-c137/NetSpeedTray/pull/276
[#314]: https://github.com/erez-c137/NetSpeedTray/issues/314
[#323]: https://github.com/erez-c137/NetSpeedTray/pull/323
[#327]: https://github.com/erez-c137/NetSpeedTray/pull/327
[#333]: https://github.com/erez-c137/NetSpeedTray/issues/333
[#348]: https://github.com/erez-c137/NetSpeedTray/pull/348
[#364]: https://github.com/erez-c137/NetSpeedTray/issues/364
[#398]: https://github.com/erez-c137/NetSpeedTray/pull/398
[#399]: https://github.com/erez-c137/NetSpeedTray/pull/399
[#400]: https://github.com/erez-c137/NetSpeedTray/pull/400
[#401]: https://github.com/erez-c137/NetSpeedTray/pull/401
[#402]: https://github.com/erez-c137/NetSpeedTray/pull/402
[#403]: https://github.com/erez-c137/NetSpeedTray/pull/403
[#404]: https://github.com/erez-c137/NetSpeedTray/pull/404
[#405]: https://github.com/erez-c137/NetSpeedTray/pull/405
[#407]: https://github.com/erez-c137/NetSpeedTray/pull/407
[#408]: https://github.com/erez-c137/NetSpeedTray/pull/408
[#409]: https://github.com/erez-c137/NetSpeedTray/pull/409
[#410]: https://github.com/erez-c137/NetSpeedTray/pull/410
[#411]: https://github.com/erez-c137/NetSpeedTray/pull/411
[#412]: https://github.com/erez-c137/NetSpeedTray/pull/412
[#413]: https://github.com/erez-c137/NetSpeedTray/pull/413
[#415]: https://github.com/erez-c137/NetSpeedTray/pull/415
[#416]: https://github.com/erez-c137/NetSpeedTray/pull/416
[#418]: https://github.com/erez-c137/NetSpeedTray/pull/418
[#419]: https://github.com/erez-c137/NetSpeedTray/pull/419
[#420]: https://github.com/erez-c137/NetSpeedTray/pull/420
[#421]: https://github.com/erez-c137/NetSpeedTray/pull/421
[@kamal63442]: https://github.com/kamal63442
[@pasharm]: https://github.com/pasharm
[@emgeka]: https://github.com/emgeka
[@kiro5678]: https://github.com/kiro5678
[@Keltian]: https://github.com/Keltian
[@cscsanaki]: https://github.com/cscsanaki
[@boonstraworks]: https://github.com/boonstraworks

---

## [2.2.0-beta.3] - October 21, 2026

*A beta, for testers. It's a manual download from the release page - the in-app updater, WinGet
and the Store never offer prereleases.*

The taskbar release: NetSpeedTray now follows Windows 11's own taskbar settings - top, left, right
and Small - instead of only fitting the classic bottom bar.

> **Upgrading:** from beta.1 or beta.2, just install over it - the running app closes itself and
> comes back. From 2.1.x, everything in the beta.1 notes applies.
>
> - **The CPU and GPU labels have a color setting now**, and existing setups are carried over by
>   what they had: **Monochrome icons become "Match text"** (the same white on a dark taskbar, and now
>   visible on a light one); **colored icons and text labels move to Brand**, which paints them in
>   your CPU and GPU maker's color. New installs start on Brand.
> - **Some Settings moved.** Mini Graph is on the Widget page now, Preferred Monitor is under Widget →
>   Behavior, and Appearance and Hardware are reordered. Nothing was renamed or reset.

### Added

- **A compact layout for side taskbars.** On Windows 11's native Left or Right taskbar the widget
  fits the 48 px bar: an arrow and up to three digits per row, one shared unit, the graph behind.
  Horizontal taskbars look exactly as before. ([#394])

  *Under the hood: one pure `side_bar_fit()` decides the layout for both sizing and painting - if
  the normal readout fits (wide third-party bars) nothing changes, otherwise it steps down to the
  compact one. `vertical_taskbar_strip()` caps the width at the bar for layout, position, drag and
  `dock_signature`; the old 50 px floor now applies to horizontal bars and free-float only, so the
  widget no longer hangs 2 px past a 48 px bar. Checked pixel-identical on horizontal bars and
  free-float across 75 configs × top/bottom × 48/32 px.*

- **CPU, GPU and memory move to the hover card when a side taskbar has no room for them.** The card
  shows them live, about once a second, and opens beside the bar - or below a top bar, above a
  bottom one. Settings → Widget → Display Mode says so. ([#394])
- **Brand, Match text or Custom colors for the CPU and GPU labels**, in Settings → Hardware →
  Indicator Color. Brand uses NVIDIA green, AMD red and Intel blue, with a sibling shade when the CPU
  and GPU share a maker; Custom gives each a color picker. ([#391])

  *Under the hood: a mixed-vendor GPU setup (a Ryzen iGPU next to an RTX) is treated as unknown and
  stays neutral, because the GPU % is the busiest adapter, not a particular one. The old three-way
  `hardware_label_style` is split into shape + `hardware_label_color` on every load; a color already
  on file wins, so a round trip to beta.2 and back keeps the user's choice. Vendor detection is one
  cached registry read per process.*

- **A "Graph style" option for the mini graph: Trend (new default) or Detailed.** Trend smooths each
  line into a calm curve, so a busy minute reads as a shape instead of a row of spikes; Detailed is the
  graph you know, every sample plotted, one click away on the Widget page under Mini Graph. ([#395])
  *Under the hood: a centered Gaussian moving average over ~6% of the visible window; the newest
  samples use a fixed 11-sample window with a slope-damped fit, so the "now" end catches up within
  ~10 s at any timespan with no overshoot. The line is always kept inside the graph, and the auto-fit
  reads the newest samples as a mean so a lone spike doesn't rescale the whole graph. Applies to the
  hardware mini graphs too, and is far cheaper per repaint than Detailed (cached until a new sample).*

### Changed

- **Settings are organized by what you're looking for.** Units and decimals sit near the top of
  Appearance, Mini Graph moved to the Widget page, and Preferred Monitor sits right above "Float on a
  display without a taskbar", the one setting it affects. Same controls, same saved settings.
  ([#393])
- **The Monitor's Overview tab and Statistics sheet read history in the background.** They open at
  once with "Reading history…" and fill in a moment later, instead of freezing the window - up to
  1.3 s per refresh on a large history file. ([#387], [#388])

### Fixed

- **Moving or resizing the taskbar no longer leaves the widget the wrong size.** Top + Small left a
  48 px widget in a 32 px bar, and moving the bar to the right left a horizontal strip lying across
  the desktop. The widget now re-sizes within a few seconds, without a restart. ([#322], thanks
  [@un99known99]; [#390])

  *Under the hood: `taskbar_changed` only ever re-positioned. A `dock_signature()` - edge, strip
  thickness, DPI, small mode, read from the work-area inset and never the sliding auto-hide rect -
  now triggers `resize_widget_for_font()` only when it changes, so a plain move never resizes. A
  start against a fallback taskbar (logon before Explorer is ready) re-sizes once the real one
  appears.*

- **The widget fits Windows 11's 32 px Small taskbar** with the default font, flush in the bar,
  top or bottom. Larger fonts or languages that can't fit keep the old two-row size. ([#390])

  *Under the hood: the old two-row floor (`metrics.height() * 2 + 2` = 34 px at Segoe UI 9) counted
  the empty space above the capitals. The fit is now decided by painting the strings the readout
  actually draws - the active locale's units, the arrows, the CPU/GPU icon - at every standard
  scale from 100% to 250%, with 1 px of top clearance. Default 48 px bars are pixel-identical.*

- **Monochrome CPU/GPU labels are visible on a light taskbar.** They were hard-coded white; "Match
  text" follows the widget's text color instead. ([#391])
- **The Widgets-button overlap hint no longer warns by mistake on a side taskbar.** ([#394])
- **The Statistics sheet stays on screen** instead of growing past the bottom edge once its figures
  load, and closed sheets are freed instead of piling up behind the Monitor. ([#387])
- **Visiting the Settings page with the monitor picker no longer re-applies every setting live** -
  the picker was connected twice. ([#393])

### Localization

- Hungarian reviewed by a native speaker, including the new label-color strings. ([#386], [#392],
  thanks [@cscsanaki])
- Dutch beta.2 strings reviewed by a native speaker. ([#383], thanks [@boonstraworks])
- 10 new strings are AI-drafted in every language and listed for review in [#364].

### Developer notes

- The support bundle now includes the taskbar geometry - edge, rects, insets, DPI and dock strip,
  rects only, no names. ([#390])
- [DATABASE.md](DATABASE.md)'s "busiest hour" recipe ranks hours by data moved and reads every tier
  in its own watermark range; it used to read `net_hour` only and missed the last week. ([#384])
- Regression pins for two 2.1.5 fixes: the CPU-temperature source order ([#216]) and the Monitor
  graph hover, end to end through `render()`. ([#385])
- A history test built its two fixtures from two clock readings and failed when they straddled a
  minute. ([#389])
- 3,140 tests.

[#216]: https://github.com/erez-c137/NetSpeedTray/issues/216
[#322]: https://github.com/erez-c137/NetSpeedTray/issues/322
[#364]: https://github.com/erez-c137/NetSpeedTray/issues/364
[#383]: https://github.com/erez-c137/NetSpeedTray/pull/383
[#384]: https://github.com/erez-c137/NetSpeedTray/pull/384
[#385]: https://github.com/erez-c137/NetSpeedTray/pull/385
[#386]: https://github.com/erez-c137/NetSpeedTray/pull/386
[#387]: https://github.com/erez-c137/NetSpeedTray/pull/387
[#388]: https://github.com/erez-c137/NetSpeedTray/pull/388
[#389]: https://github.com/erez-c137/NetSpeedTray/pull/389
[#390]: https://github.com/erez-c137/NetSpeedTray/pull/390
[#391]: https://github.com/erez-c137/NetSpeedTray/pull/391
[#392]: https://github.com/erez-c137/NetSpeedTray/pull/392
[#393]: https://github.com/erez-c137/NetSpeedTray/pull/393
[#395]: https://github.com/erez-c137/NetSpeedTray/pull/395
[#394]: https://github.com/erez-c137/NetSpeedTray/pull/394
[@un99known99]: https://github.com/un99known99
[@cscsanaki]: https://github.com/cscsanaki
[@boonstraworks]: https://github.com/boonstraworks

---

## [2.2.0-beta.2] - October 20, 2026

*A beta, for testers. It's a manual download from the release page - the in-app updater, WinGet
and the Store never offer prereleases.*

The checkup release: after beta.1 shipped, four independent reviews and a live test on real data
went over the whole of 2.2, and this is what they found.

> **Upgrading:** from beta.1, just install over it - your history is already on the new database.
> From 2.1.x, the same first-launch rebuild as beta.1 applies, and a one-time notice now explains
> why older totals read lower.
>
> - **Delete all history now also removes the backup** the 2.2 upgrade keeps
>   (`speed_history.db.bak.v7_*`), so older history can no longer be restored after a delete. The
>   confirmation says so.
> - **Backups now age out** with "Keep history for", but never sooner than 30 days of the app
>   actually running after the upgrade, and never on a clock that looks wrong.

### Added

- **Separate upload and download mini graphs, each on its own scale.** Opt-in, in Settings -
  off by default, and the widget looks exactly as before when it's off. ([#336], thanks [@Zogar89])
- **A one-time notice after the history upgrade** explains that totals for earlier days are
  estimates and can read lower than 2.1 showed. ([#378])

### Changed

- **The latency pill and stats sheet say "Router" or "Internet" for the hop they actually
  measure.** By default that's your router; the Settings card is now "Connection latency". ([#378])
- **Check connection judges speed and loss separately.** One lost ping in 20 no longer turns a
  2 ms router red "Slow"; loss gets its own amber/red note. ([#378])
- **"Busiest hour" is now the hour with the highest average**, not the hour that held the single
  biggest spike. ([#379])
- **Graph day and month names follow your language** instead of always showing English. ([#378])

### Fixed

- **Short windows ignored history from before the upgrade.** For a day after updating, the 12-hour
  view showed a peak of 2 Mbps next to an 87 Mbps spike on the same graph. ([#379])
- **Turning Latency off in Settings now stops the pings immediately**, to your router and to any
  public host - before, they continued until the app restarted. ([#376])
- **A network change or wake from sleep no longer records up to a minute of false outage.**
  ([#376])
- **A failed "Delete all history" no longer resets your data-cap counter**, and a reset during
  the first-launch upgrade no longer brings back the bytes it was meant to clear. ([#376])
- **Quitting with the Monitor open is quick and safe.** It could block for several seconds and
  close the database under a running graph query. ([#377])
- **No more hidden PowerShell every 30 seconds.** The Widgets-button check now runs in-process -
  it cost more CPU than the rest of the app. ([#377])
- **The Statistics sheet no longer freezes the window** while the database is busy. ([#377])
- **Settings and your data-usage count are saved when Windows signs out or shuts down.** ([#377])
- **Hebrew:** the "peak" label on graphs read backwards. ([#378])
- **Graphs:** hardware lines no longer cross months with no data, the network fill covers the whole
  range, and the throttle-temperature slider reads "Off" at zero. ([#379])
- **Settings:** the port-in-use message no longer squeezes into a narrow column, and the hover card
  uses your language's decimal separator. ([#378])

### Security

- **The installer no longer runs the installed app with admin rights to close it.** With a custom
  install folder under `C:\`, any program could have replaced that exe beforehand. Setup now asks
  the app to close itself. ([#375])

  *Under the hood: Setup posts the registered shutdown message from Pascal (`EnumWindows` +
  `GetPropW`) and only falls back to `{sys}\taskkill.exe`; nothing in the install folder is ever
  executed by the elevated installer.*

- **Delete all history removes every file that could hold your history** - the live database, the
  upgrade backup and any file the upgrade set aside - and only those, by exact name; it never follows
  a link out of the folder. ([#376])

### Localization

- Dutch reviewed by a native speaker. ([#374], thanks [@boonstraworks])
- 13 new or changed strings are AI-drafted in every language and listed for review in [#364].
- Every Settings control now has a screen-reader name: 138, up from 23. ([#378])

### Developer notes

- Two flaky tests fixed: a fixed 2 s wait in the odometer restart test ([#380]) and a clock read
  twice in the v8 reader parity tests.
- 2,752 tests.

[#336]: https://github.com/erez-c137/NetSpeedTray/pull/336
[#364]: https://github.com/erez-c137/NetSpeedTray/issues/364
[#374]: https://github.com/erez-c137/NetSpeedTray/pull/374
[#375]: https://github.com/erez-c137/NetSpeedTray/pull/375
[#376]: https://github.com/erez-c137/NetSpeedTray/pull/376
[#377]: https://github.com/erez-c137/NetSpeedTray/pull/377
[#378]: https://github.com/erez-c137/NetSpeedTray/pull/378
[#379]: https://github.com/erez-c137/NetSpeedTray/pull/379
[#380]: https://github.com/erez-c137/NetSpeedTray/pull/380
[@Zogar89]: https://github.com/Zogar89
[@boonstraworks]: https://github.com/boonstraworks

---

## [2.2.0-beta.1] - October 20, 2026

*A beta, for testers. It's a manual download from the release page - the in-app updater, WinGet
and the Store never offer prereleases.*

Your history gets exact, exportable, and yours to delete. Under it is a rebuilt database that stores
the bytes each poll actually measured. This release also carries everything that would have been
2.1.8: a month of contributor work, a GPU reading that finally matches Task Manager, and an
installed app that's signed.

> **Upgrading:** a few things will look different, all on purpose.
>
> - **The first launch rebuilds your history database.** On a real 80 MB profile it took about 3
>   seconds and came out at 18 MB. Your old file is kept, unchanged, as
>   `speed_history.db.bak.v7_<date>_<time>` in `%APPDATA%\NetSpeedTray`. If the rebuild can't finish -
>   not enough disk space (it needs 1.5× the file), or another program holding the file - nothing
>   is changed: your history shows read-only, a notice says so, and it tries again next start.
> - **History totals read lower than 2.1.x showed for the same days.** Nothing was lost; 2.1.x
>   over-counted. On the maintainer's 30 days, download went from 9.0 GB to 7.2 GB and upload from
>   4.2 GB to 2.9 GB. Your data-cap figure is a separate counter and doesn't change.
> - **Going back to 2.1.x after upgrading:** 2.1.5-2.1.7 show your history with a "History
>   recording is paused" notice and record nothing. The last day before the upgrade looks empty
>   there, and their totals read high. To record on 2.1.x again, restore the backup as described in
>   [DATABASE.md](DATABASE.md#restoring-a-backup). **On 2.1.3 or older, restore the backup first** -
>   those versions have no guard against a newer database.
> - **Hardware history is only recorded for what you monitor.** CPU/GPU monitoring is off by
>   default, so a default install now records none. History you already have is kept. Turn on
>   monitoring in Settings → Hardware to keep recording it.
> - **The SMART update rate is gone.** If you had it, you're now on 2 s, which is what SMART
>   actually did. Any other rate moves to the nearest of 1, 2, 5 or 10 s.
> - **"All physical adapters" stops counting VPNs and virtual adapters.** With a VPN or WSL running,
>   that mode reads lower now. The old number counted the same traffic twice.
> - **Saved speed history keeps real one-second spikes.** Short Monitor windows can show peaks the
>   widget smoothed away. The widget itself is unchanged.
> - **The latency probe sends 4 small pings every 3 s instead of 1** (about 1.3 packets a second per
>   hop), so loss is measured in 1% steps.

### Added

- **Delete all history.** Settings → Advanced → Data wipes your saved speed, hardware and connection
  history, the adapter names stored with it, and overwrites the freed space in the file. Your
  settings stay, and so does this period's data-cap usage unless you tick the box to reset it too.
  ([#347], [#366])

  *Under the hood: a `DELETE` only moves pages to SQLite's freelist, where the old samples stay
  readable, so the delete runs with `secure_delete` on, hands the pages back with
  `incremental_vacuum`, then `wal_checkpoint(TRUNCATE)`. A checkpoint that comes back busy (a
  Monitor graph mid-read) is retried, and a pending clean-up survives a restart through a
  `metadata` marker. Unflushed samples are dropped first, or they'd write pre-delete rows back.*

- **Check connection.** A button next to the latency pill (and in the Network tab) pings your router
  20 times, and the internet too if you choose, then shows loss, median, 95th percentile and jitter,
  says where the loss starts, and copies a plain-text report you can paste. Nothing is saved.
  ([#352])
- **A localhost metrics endpoint for Prometheus, Grafana and friends.** Off by default. Settings →
  Advanced → Integrations serves your speeds, data usage, latency and the hardware stats you monitor
  at `http://127.0.0.1:9184/metrics`, to this PC only. ([#360])
- **The taskbar mini graph can use a fixed scale.** It tops out at your plan speed plus 0-30%
  headroom, with an optional dotted plan-speed line, so background traffic no longer looks like a
  spike. Off by default. ([#335], thanks [@Zogar89])
- **The interface picker sorts real adapters from virtual ones.** Two groups, "Physical adapters"
  and "Virtual adapters and VPNs", with your active connection marked and listed first. VPNs can now
  be picked in "Select specific interfaces". ([#350])
- **The database now records when NetSpeedTray was running,** when adapters went up or down, their
  link speed, and their error and drop counters. Nothing in the app shows these yet - they're in
  the file for your own queries ([DATABASE.md](DATABASE.md) has recipes), and they're the groundwork
  for telling "the app wasn't running" apart from "no traffic". ([#365], [#366])

### Changed

- **Your history stores exact bytes, in a file about a quarter the size.** Every poll now saves the
  bytes each adapter moved and how long it took, instead of a rate times a guessed interval, so
  volume no longer depends on your update rate. The 2.1 history is rebuilt into the new shape on
  first launch. ([#366], [#359], [#365], [#363])

  *Under the hood: `WITHOUT ROWID` tables keyed on small `interfaces`/`metrics` ids, bytes + `dt_ms`
  at capture, and tiers that are query-precedence layers bounded by four watermarks written in the
  same transaction as each roll-up. The first design summed the tiers and double-counted the
  data-cap figure at exactly 2.00× on live data. The rebuild happens in a side file, is checked row
  for row against the old one, then swapped in by two renames under a journal; `recover()` runs on
  every launch, so a kill at any point leaves either the old database or the new one whole. The
  renamed v7 file is the backup - no second copy is made.*

- **Totals for days before the upgrade are now estimated honestly.** 2.1.x turned each saved minute
  into bytes as its average rate × 60 s, but a second was only saved when the adapter moved data, so
  a minute that was busy for 5 seconds counted as busy for 60 - the quieter the adapter, the bigger
  the over-count (up to ~20× on idle virtual adapters). 2.2 counts only the seconds each minute
  really stands for, and labels those days "From recorded history". ([#366], [#367])
- **The last day of per-second detail becomes per-minute at the upgrade.** 2.1 never stored bytes or
  intervals, so there was nothing exact to keep at second resolution. Totals, averages and peaks for
  those minutes stay; nothing is invented. ([#359])
- **Hardware history follows your monitoring switches.** CPU, GPU and RAM were recorded even with
  monitoring off, and opening the Monitor saved temperatures and power you'd never turned on. Now
  only what you monitor is saved, and a new **Record hardware history** switch turns it off
  entirely. ([#347])

  *Under the hood: `record_hardware_history` defaulted to `True`, had no UI, and was OR-ed into the
  poll gate, so it also forced the polling. Collection and persistence are now separate decisions
  (`hardware_collection_gates`, `hardware_persist_allowed`). The open Monitor window still forces
  collection for its live tiles, but those readings are never written.*

- **"All physical adapters" counts only real network hardware.** Tailscale, WireGuard, NordLynx,
  ZeroTier, Hyper-V/WSL, VirtualBox/VMware host-only, Wi-Fi Direct and WAN Miniport adapters were
  counted on top of the card that carried them - in the widget speed and in data usage. ([#350])

  *Under the hood: the 2.1 rule was a keyword list matched on friendly names, and it missed all of
  those. Adapters are now classified from Windows' own `GetIfTable2` data (the NDIS hardware flag
  and the interface type) first, with keywords as a backstop. If nothing classifies as physical,
  the mode falls back to non-tunnel adapters rather than reading zero forever.*

- **Saved history stores what was measured.** The spike filter used to save its substitute value -
  a number nobody measured - so graphs, summaries and exports from the database now show real
  one-second peaks. The widget and mini graph still smooth them. ([#353], [#366])
- **Packet loss is measured in 1% steps within a minute.** Each 3-second probe now sends a burst of
  4 pings instead of one, and below 1% the pill shows a decimal ("0.6% loss"). ([#346])
- **Week, Month and All graphs stay readable when one speed test towers over the rest.** The speed
  axis scales to the 99th percentile, clipped points get a small marker, and the true peak sits in
  the corner. Hovering still reads the real value. ([#349])
- **"Top talkers" is now "Most connections"** - it always ranked apps by open connections, not by
  bandwidth. Every language got the new name instead of keeping a bandwidth-flavoured translation.
  ([#350])
- **No more daily full-file rewrite.** Maintenance used to `VACUUM` the whole database; it now hands
  back up to ~8 MB of free space per hourly pass, so the file stays small without being rewritten.
  ([#365], [#366])

### Fixed

- **After an in-app update, NetSpeedTray comes back as you, not as administrator.** Since 2.1.6,
  every in-app update left the app running elevated until the next sign-in. The 2.2.0 installer
  does the relaunch, so updating from 2.1.7 is already fixed. ([#321])

  *Under the hood: the updater starts Setup already elevated (so it survives the app quitting), and
  Inno's `runasoriginaluser` means "as whoever started Setup" - the elevated token. The silent
  relaunch now goes through `explorer.exe "<exe>"`, which starts it at medium integrity; the
  original flag stays only as the fallback when no taskbar exists. An elevated app writing its own
  config is also a plausible route to #307's admin-only settings file.*

- **Updates and uninstalls close NetSpeedTray cleanly** instead of force-killing it. The graceful
  close never worked in 2.x, so every upgrade ended in `taskkill /F` and skipped the final history
  flush and config save. Upgrading *from* 2.1.7 still force-closes once - its copy of the close
  command is the broken one. ([#368])

  *Under the hood: `monitor.py --shutdown` called the non-existent
  `win32gui.RegisterWindowMessageW`, nothing listened for the message, and setup.iss looked for a
  `NetSpeedTrayHidden` window 2.x never creates. The widget's HWND now carries a window property;
  `--shutdown` finds it with `EnumWindows` + `GetProp`, posts `NetSpeedTray_Shutdown_v1` to that
  window only (allowed through UIPI), and waits up to 30 s - longer than the 15 s DB flush bound.
  `taskkill /F` stays as the fallback.*

- **Starting NetSpeedTray while an elevated copy is running no longer shows a "critical error"
  dialog.** It exits quietly like any other second launch - also when another Windows user is
  already running it. ([#369])

- **GPU usage matches Task Manager.** We showed the busiest *process* on the GPU, not the busiest
  engine, so three apps sharing one engine at 44%, 34% and 13% read 44% instead of ~91%. ([#328],
  thanks [@ivLis-Studio] for the report and the diagnosis)

  *Under the hood: each `\GPU Engine(*)\Utilization Percentage` instance is one process on one
  engine. Values are now summed per `(luid, phys, eng)` across processes, the busiest engine wins,
  and the result is clamped to 100. `engtype` is deliberately not part of the key - it's often blank,
  and one engine can carry different labels.*

- **The installed NetSpeedTray.exe is signed**, not just the installer and the portable exe. Smart
  App Control blocked the installed copy for having no verified publisher, and it should accept
  the signed exe - confirmation pending from testers. ([#333], thanks [@Keltian]; [#341])

  *Under the hood: SignPath can't re-sign the payload inside an Inno Setup installer. The portable
  zip carries the exact same `dist\NetSpeedTray\` bytes, so CI now signs the portable first, copies
  its signed exe into the payload, compiles the installer from that, and signs the installer -
  two approvals in sequence. A release gate silent-installs the result on the runner and requires a
  valid signature on `{app}\NetSpeedTray.exe`. The uninstaller and the bundled Qt/Python DLLs are
  still unsigned.*

- **Data usage no longer loses what happened while your PC slept.** A gap longer than about 10 s -
  every Modern Standby wake - threw away the whole gap and the next two polls. Bytes that moved while
  the app was closed are now recovered too, as long as Windows wasn't restarted. ([#348])

  *Under the hood: the odometer now runs before the gap gate; only the speed display re-primes. Each
  persist also stores a snapshot (counters, `psutil.boot_time()`, sample time, auto-mode primary) as
  one JSON row in `metadata` (`usage_counter_snapshot`), written in the same transaction as the
  total. The next launch adds `now - snapshot` only on the same boot, the same adapter, and counters
  that didn't go backwards. Each poll's delta is capped at 100 Gbps × elapsed, and bytes are charged
  to the period their measurement window began in, so a gap across the reset day can't trip an early
  overage alert.*

- **Data usage counts on IPv6-only networks.** In automatic mode the active adapter was looked up
  over IPv4 only, so an IPv6-only connection counted nothing. ([#348])
- **The latency pill's loss is your internet's, not your router's.** With the public probe on, the
  pill showed internet ms next to router loss, so a flaky ISP behind a healthy router read "0% loss".
  The stats sheet's Internet figures now follow the same hop. ([#345], [#352])
- **Side-by-Side fits on a left or right taskbar.** Network, CPU and GPU were laid out in a row and
  clipped past the edge; on a side taskbar they now stack top to bottom. ([#337], thanks
  [@Zogar89])
- **RAM and VRAM labels stay put** instead of shifting sideways when a value goes from one digit to
  two. ([#326], thanks [@ZeroFlagLabs])
- **No white areas in the Monitor window on a dark theme** - the process lists, the splitter, the
  scroll bars, a one-pixel graph edge at 125% scaling, and a white flash when the window opens.
  ([#330], thanks [@pasharm])
- **Monitor graph axes never repeat a label.** A short Session read `18:13, 18:13, 18:14`, 48 h read
  `00:00, 12:00, 00:00`, and a 0-2 Mbps axis read `0.0, 0.5, 1, 1, 2`. ([#342], [#344])
- **No Settings page scrolls sideways, in any language.** Titles now wrap and controls drop below
  them in a narrow window, and the sidebar widens to fit long page names (German, Japanese,
  Russian). ([#356])
- **A docked widget stays inside its taskbar strip,** top or bottom, and dragging it along the bar
  keeps it there - including on an auto-hide taskbar. This is defensive: the top-taskbar glitch in
  [#322] isn't reproduced yet, so it may not be the fix for it. ([#343], thanks [@un99known99] for
  the report)
- **A database upgrade that can't finish no longer carries on silently.** Before, an upgrade that
  stopped partway let the app carry on with no sign anything was wrong. Now your history stays
  read-only with a notice (a separate one when the disk is full), and the next start tries again.
  ([#366])
- **A failed backup now stops a database upgrade** instead of carrying on without one, and backups
  are checked against the tables that version should have, not the possibly-damaged file they were
  copied from. ([#353])
- **A backup's `-wal` or `-shm` file is never mistaken for the backup.** Opening a backup in any
  SQLite tool leaves those beside it; 2.1.5-2.1.7 could then name one as the file to restore, and
  backup pruning could keep it and delete the real one. ([#366])
- **Pre-upgrade backups work when your Windows username contains `#` or `%`.** The backup check cut
  the path at the `#`, opened the wrong file, and failed. ([#361])

  *Under the hood: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` isn't encoded - a `#` starts
  the URI fragment, so SQLite truncated the path, dropped `?mode=ro` with it, and created a stray
  empty file read-write. Since #353 a failed check aborts the migration, so on such a profile every
  future upgrade would have stopped. `core/db/sqlite_uri.sqlite_ro_uri()` percent-encodes the path,
  and a source scan fails the suite if an unencoded `file:` URI comes back.*

### Removed

- **The SMART update rate.** It was never adaptive - just a fixed 2-second rate behind a special
  value. The slider is now 1, 2, 5 or 10 s; SMART settings become 2 s, and 10 s stays 10 s.
  ([#351])

### Security

- **The metrics endpoint is local and opt-in.** It listens on 127.0.0.1 only, refuses other hosts
  and anything sent from a web page (so a site can't read it through DNS rebinding), and is
  rate-limited. It does include your adapter names - Settings says so. ([#360])

### Localization

- Every new string in this release is in all 16 languages, as drafts based on each language's
  existing wording. Native-speaker corrections are very welcome in [#364].
- Hungarian and Korean: native-speaker corrections from [#202]. ([#340], thanks [@cscsanaki] and
  [@doeil1614-ops])

### Developer notes

- **The v8 database** landed in eight steps, dormant until the switch-over: schema and v7-named
  compatibility views ([#354]), one history-reader interface ([#357]), the v8 reader ([#363]), the
  migrator with its crash-recovery journal ([#359]), the writer ([#365]), then the switch to
  `_DB_VERSION = 8` ([#366]). Fresh installs create v8 directly. [DATABASE.md](DATABASE.md) is
  rewritten around precedence and watermarks, with the restore steps and the 2.1.x rollback
  caveats ([#367]).
- **Rollback was verified with real binaries:** the signed 2.1.7 portable and a `2.2.0-beta.1`
  build, in a scratch `APPDATA`. Upgrade 2.9 s, 80,273,408 → 18,104,320 bytes; history older than
  24 h identical; kill + relaunch left every row intact; a rolled-back 2.1.7 showed the notice and
  drew graphs through the views without changing the file; the DATABASE.md restore reproduced the
  original file exactly and 2.1.7 recorded normally afterwards. The 2.1.7 → 2.2 *installer* path,
  which force-closes 2.1.7 once ([#368]), is covered by the kill tests, not yet by a live run.
- A new `tests/integration/` suite kills a real migration at 11 points plus random moments and
  checks that either v7 or v8 is whole every time.
- 2,590 tests (2,543 unit + 47 integration), up from 1,496 in 2.1.7. Each new test was seen
  failing without its fix first; most PRs also ran a mutation pass. The v8 reader tests run on one fixed clock (`NST_TEST_NOW` overrides it), which
  removed a 1-in-15 flake ([#370]).
- Release signing ([#341]): two sequential SignPath approvals now (portable, then installer), each
  polled every 20 s for up to 30 min. Proven end to end on a throwaway `v0.0.2-test` tag together
  with SignPath's action v3 ([#325]). Both Store workflows now declare least-privilege tokens
  ([#320]).
- A final release now outranks its betas in the Windows file version: `2.2.0-beta.1` is
  `2.2.0.1001`, `2.2.0` is `2.2.0.10000`, and prereleases set `VS_FF_PRERELEASE`. ([#362])
- The build works from a folder with spaces or parentheses in its path, and stops when PyInstaller
  fails instead of going on to a misleading installer error. ([#329], thanks [@ZeroFlagLabs])
- Windows-only packages carry `sys_platform == "win32"` markers, so Dependabot's Linux pip job
  resolves again. No pinned version changed. ([#339])
- A CI flake in the window-position test was the 400 ms arm timer firing late on a loaded runner;
  the test now waits for it. ([#358])

[#202]: https://github.com/erez-c137/NetSpeedTray/issues/202
[#320]: https://github.com/erez-c137/NetSpeedTray/pull/320
[#321]: https://github.com/erez-c137/NetSpeedTray/pull/321
[#322]: https://github.com/erez-c137/NetSpeedTray/issues/322
[#325]: https://github.com/erez-c137/NetSpeedTray/pull/325
[#326]: https://github.com/erez-c137/NetSpeedTray/pull/326
[#328]: https://github.com/erez-c137/NetSpeedTray/issues/328
[#329]: https://github.com/erez-c137/NetSpeedTray/pull/329
[#330]: https://github.com/erez-c137/NetSpeedTray/pull/330
[#333]: https://github.com/erez-c137/NetSpeedTray/issues/333
[#335]: https://github.com/erez-c137/NetSpeedTray/pull/335
[#337]: https://github.com/erez-c137/NetSpeedTray/pull/337
[#339]: https://github.com/erez-c137/NetSpeedTray/pull/339
[#340]: https://github.com/erez-c137/NetSpeedTray/pull/340
[#341]: https://github.com/erez-c137/NetSpeedTray/pull/341
[#342]: https://github.com/erez-c137/NetSpeedTray/pull/342
[#343]: https://github.com/erez-c137/NetSpeedTray/pull/343
[#344]: https://github.com/erez-c137/NetSpeedTray/pull/344
[#345]: https://github.com/erez-c137/NetSpeedTray/pull/345
[#346]: https://github.com/erez-c137/NetSpeedTray/pull/346
[#347]: https://github.com/erez-c137/NetSpeedTray/pull/347
[#348]: https://github.com/erez-c137/NetSpeedTray/pull/348
[#349]: https://github.com/erez-c137/NetSpeedTray/pull/349
[#350]: https://github.com/erez-c137/NetSpeedTray/pull/350
[#351]: https://github.com/erez-c137/NetSpeedTray/pull/351
[#352]: https://github.com/erez-c137/NetSpeedTray/pull/352
[#353]: https://github.com/erez-c137/NetSpeedTray/pull/353
[#354]: https://github.com/erez-c137/NetSpeedTray/pull/354
[#356]: https://github.com/erez-c137/NetSpeedTray/pull/356
[#357]: https://github.com/erez-c137/NetSpeedTray/pull/357
[#358]: https://github.com/erez-c137/NetSpeedTray/pull/358
[#359]: https://github.com/erez-c137/NetSpeedTray/pull/359
[#360]: https://github.com/erez-c137/NetSpeedTray/pull/360
[#361]: https://github.com/erez-c137/NetSpeedTray/pull/361
[#362]: https://github.com/erez-c137/NetSpeedTray/pull/362
[#363]: https://github.com/erez-c137/NetSpeedTray/pull/363
[#364]: https://github.com/erez-c137/NetSpeedTray/issues/364
[#365]: https://github.com/erez-c137/NetSpeedTray/pull/365
[#366]: https://github.com/erez-c137/NetSpeedTray/pull/366
[#367]: https://github.com/erez-c137/NetSpeedTray/pull/367
[#368]: https://github.com/erez-c137/NetSpeedTray/pull/368
[#369]: https://github.com/erez-c137/NetSpeedTray/pull/369
[#370]: https://github.com/erez-c137/NetSpeedTray/pull/370
[@Zogar89]: https://github.com/Zogar89
[@ZeroFlagLabs]: https://github.com/ZeroFlagLabs
[@pasharm]: https://github.com/pasharm
[@ivLis-Studio]: https://github.com/ivLis-Studio
[@Keltian]: https://github.com/Keltian
[@un99known99]: https://github.com/un99known99
[@cscsanaki]: https://github.com/cscsanaki
[@doeil1614-ops]: https://github.com/doeil1614-ops

---

## [2.1.7] - September 23, 2026

Fixes for what 2.1.6 users reported in its first two weeks: a support bundle that could include your
Windows username, settings that wouldn't save, a "Start with Windows" switch that said ON when
Windows had it off, and a Decimal Places setting that wasn't obeyed. Plus optional RAM/VRAM labels
and Hungarian.

> **Upgrading:** at the default one decimal place, very light traffic shows `0.0 Mbps` again rather
> than `0.004 Mbps` - Decimal Places now means exactly what it says. And if NetSpeedTray is disabled
> in Task Manager's Startup apps, Settings now shows "Start with Windows" as off, because that's what
> Windows does with it.

### Added

- **Optional RAM and VRAM labels.** A new Settings → Hardware switch shows `RAM 8.1/15.7G` instead
  of a bare number after the CPU. Off by default, so your widget keeps its width. ([#250], thanks
  [@seahindeniz])

### Fixed

- **Support bundles no longer include your Windows username.** Paths inside error messages were
  written with doubled backslashes and slipped past the redaction; old log lines are cleaned at
  export too. ([#306], thanks [@CMTriX])

  *Under the hood: the path rules matched only the literal single-backslash form, and every
  `OSError` message carries the path's repr (`C:\\Users\\name\\...`). A separator now matches `\`,
  `\\`, `\\\\` or `/` on any drive letter, and the bare account name is redacted as a backstop. On
  the maintainer's own log, 25 leaking lines became 0.*

- **Settings save even when the settings file only lets an admin write to it** - no more
  "Permission denied" that only went away when running as administrator. The first save puts the
  file's permissions back to normal. ([#307], thanks [@CMTriX])

  *Under the hood: `shutil.move` falls back to copying over an existing file on Windows, which
  needs write access to the file itself. `os.replace` needs only the folder's permission, and the
  new file inherits the folder's permissions.*

- **A settings file the app can't read no longer stops it from starting.** It's set aside as
  `.unreadable` and the app starts on defaults, instead of showing "NetSpeedTray must close".
  ([#307])
- **"Start with Windows" tells the truth.** When Task Manager's Startup apps has NetSpeedTray
  disabled, Settings now shows that - and switching it on in Settings actually turns it on.
  ([#308], thanks [@CMTriX])

  *Under the hood: Task Manager keeps its own switch in `Explorer\StartupApproved\Run` (first byte
  `02` on, `03` off), separate from the Run entry we checked. The launch-time check still never
  overrides a Task Manager choice by itself - only the switch in Settings does.*

- **The log says why NetSpeedTray won't start at sign-in** when the app is set to always run as
  administrator - Windows silently skips those. Support bundles now show that, plus the Task
  Manager state. ([#308])
- **Decimal Places is obeyed.** At 1 decimal place you'd sometimes see `0.004 Mbps`: 2.1.5 added
  extra digits whenever a value rounded to zero. That's gone. ([#303], thanks [@Necromancerx9];
  fix by [@kamal63442] in [#304])
- **Support bundles include the in-app updater's own log**, which 2.1.6 started writing but never
  actually put in the bundle. [privacy.md](privacy.md) and the bundle's `MANIFEST.txt` now list
  everything a bundle contains.

### Localization

- **Hungarian** - 16 languages now. ([#302], thanks [@cscsanaki])
- Simplified Chinese: the two history-paused strings, in native wording. ([#305], thanks
  [@RainThings])
- One new string, "Show RAM/VRAM Labels", in every language - native-speaker review welcome in
  [#202].

### Developer notes

- 1,496 tests. Each fix was seen failing first, then checked live on the maintainer's machine: a
  support bundle built from a real log; a source build started with an unreadable settings file; a
  real sign-in with two throwaway startup entries (one flagged run-as-admin, one not); screenshots of
  the labels in three layouts. Before tagging, the built exe was smoke-tested against a scratch
  profile - every Decimal Places setting, plus the labels on the widget and in Settings.
- Release signing ([#311]): both SignPath requests now go in at once, so both approvals arrive
  together, and the run polls every 20 s instead of backing off to 20 minutes. A throwaway
  `v0.0.1-test` tag proved the prerelease path for the first time: marked prerelease, not Latest,
  WinGet and Store skipped.

[#202]: https://github.com/erez-c137/NetSpeedTray/issues/202
[#250]: https://github.com/erez-c137/NetSpeedTray/issues/250
[#302]: https://github.com/erez-c137/NetSpeedTray/pull/302
[#303]: https://github.com/erez-c137/NetSpeedTray/issues/303
[#304]: https://github.com/erez-c137/NetSpeedTray/pull/304
[#305]: https://github.com/erez-c137/NetSpeedTray/pull/305
[#306]: https://github.com/erez-c137/NetSpeedTray/issues/306
[#307]: https://github.com/erez-c137/NetSpeedTray/issues/307
[#308]: https://github.com/erez-c137/NetSpeedTray/issues/308
[#311]: https://github.com/erez-c137/NetSpeedTray/pull/311
[@seahindeniz]: https://github.com/seahindeniz
[@CMTriX]: https://github.com/CMTriX
[@Necromancerx9]: https://github.com/Necromancerx9
[@kamal63442]: https://github.com/kamal63442
[@cscsanaki]: https://github.com/cscsanaki
[@RainThings]: https://github.com/RainThings

---

## [2.1.6] - September 6, 2026

The hotfix for the in-app updater. Since 2.1.4 it downloaded and verified every update and then
threw it away. If you are on 2.1.4 or 2.1.5, update **once by hand** - after that, updates are one
click again.

> **Upgrading from 2.1.4 or 2.1.5:** the built-in updater in those versions cannot finish an update
> (below). Download the installer from the release page and run it, or use `winget upgrade`, or the
> Microsoft Store. You only have to do this once.

### Fixed

- **In-app updates no longer end in nothing.** Since 2.1.4, every update downloaded, verified, and
  then silently deleted itself: the progress window closing counted as you pressing Cancel. Both the
  installer and the portable paths were affected. ([#296], [#260])

  *Under the hood: Qt's `QProgressDialog` emits `canceled()` from its close event, not only from
  the Cancel button. `_close_progress()` closed the dialog one line before `_on_verified` checked
  the cancelled flag, so the finished download was cleaned up and the updater returned without a
  log line - the one silent path in the module. The dialog is now detached and silenced before it
  is closed, a late `canceled` is ignored, and the cancel path logs. The regression tests drive a
  real `QProgressDialog`; the sixteen updater tests that existed never built one.*

- **The update installs itself and brings the app back.** One click, and about ten seconds later
  NetSpeedTray is running on the new version - no wizard, no copying files. Declining the
  permission prompt keeps the app running and opens the download page instead of leaving you with
  nothing.

  *Under the hood: even with the cancel bug fixed, nothing installed. Two more mechanisms, both
  measured live. Windows tears down an elevated process when the process that requested it exits,
  so quitting for the installer killed it. Keeping the app alive got further, and then the
  installer's own `taskkill /F /IM NetSpeedTray.exe /T` walked the tree it was standing in - as a
  descendant of the app, it killed itself mid-install, its log stopping inside the line that
  announces the taskkill. The app now elevates a shell that starts the installer and exits, so the
  installer is orphaned and the update survives the app quitting, being killed, or crashing. The
  app no longer quits at all; the installer closes it, exactly as a Store or winget upgrade does.
  `/T` is gone from the installer - `/IM` always ended every process with that name.*

- **An update that goes wrong now says so.** The installer writes its own log next to the app's, so
  a support bundle shows what happened after the app handed over, and if the installer never
  replaces the running version the app reports it instead of waiting silently.

### Developer notes

- 1,371 tests. Every fix was reproduced with a failing test first, then the whole path was verified
  live: a client labeled 2.1.4 built from this code, installed, and updated to the published 2.1.5
  release by pressing Download - twelve seconds from click to the new version running. The target
  was the 2.1.5 installer, so the app-side fix stands on its own.

[#260]: https://github.com/erez-c137/NetSpeedTray/issues/260
[#296]: https://github.com/erez-c137/NetSpeedTray/issues/296

---

## [2.1.5] - September 4, 2026

The release that makes rolling back safe - the escape hatch the 2.2 beta cycle depends on, plus
honest numbers on the widget and in every export.

> **Upgrading:** the first launch compacts the history database (the maintainer's own went from
> 53 MB to 3.7 MB - nothing is deleted, the file was mostly slack). And very light traffic now
> shows as `0.02 Mbps` instead of `0.0 Mbps` - a display change, not a measurement change.

### Added

- **Opening a database written by a newer NetSpeedTray now shows an on-screen notice, not just a
  log line.** History stays visible while recording pauses, and the message names the exact
  backup file to go back to. ([#273])

  *Under the hood: the guard shipped in 2.1.4 but never left the log - four
  `_schema_incompatible` sites, none emitting a signal. A rolled-back install looked perfectly
  healthy while recording nothing, forever.*

- **DATABASE.md documents how to restore a pre-migration backup** - including deleting the
  `-wal`/`-shm` sidecar files first, which is not optional.

  *Under the hood: renaming the `.bak` into place with stale sidecars present lets SQLite replay
  the newer schema's write-ahead log onto the restored file - `quick_check` says ok, and the old
  tables are gone.*

- **Your settings are backed up before any migration touches them.** The first time a config
  written by a different version loads, a pristine copy is kept as `config.json.bak.v<version>`.

- **NetSpeedTray is on the [Microsoft Store](https://apps.microsoft.com/detail/XP9MHWQZJPLQM8).**
  Same signed installer, same free app, one click from the Store - and the only official listing
  there. Anything with a similar name is not this project.

- **The updater never offers a prerelease.** When 2.2.0 ships as a beta, trying it will always be
  a deliberate download - nobody gets moved onto one automatically. ([#264])

### Changed

- **After a silent install, NetSpeedTray starts right away.** Installs from the Microsoft Store
  and `winget` show no Finish page, so until now the app was simply not running until your next
  sign-in - and a `winget upgrade` left it closed. The installer now starts it, unelevated. A
  silent upgrade over a running copy also no longer waits on an invisible "close it?" prompt.

- **The startup cleanup only deletes update folders it created itself, and logs every removal.**
  A rollback copy like `NetSpeedTray-2.1.5-backup` kept beside the install now survives every
  launch. ([#264])

- **The read-only refusal message points at your data instead of away from it** - it names the
  newest backup this version can actually open, and no longer suggests discarding your history
  as the first option.

### Fixed

- **A config from a different version no longer loses your settings.** An unparseable
  `config_version` used to reset everything to defaults - and then save the reset. Newer
  versions are never stamped down, and settings this version doesn't know survive untouched.

- **Coverage and totals are no longer wrong in SMART mode.** Every stats sheet and CSV export
  reported 0.0% coverage, and bandwidth totals came out halved. ([#273])

  *Under the hood: SMART's `-1.0` sentinel is truthy, so the `or 1.0` idiom let it straight
  through - and SMART samples every 2.0 s, so normalizing to 1.0 would still be wrong by 2x. One
  shared resolver now feeds the sampler and all the math. A unit test had pinned the wrong 1 s
  assumption as spec; the expectation was corrected rather than the code bent to the test.*

- **Speeds at a unit boundary no longer render as `1000.0 Kbps`.** The unit is chosen from the
  value as displayed, so it promotes to `1.0 Mbps` instead of overflowing the widget's number
  slot.

- **10 GbE speeds fit the widget.** In always-Mbps mode, `10000.0 Mbps` now promotes to
  `10.0 Gbps`, without widening the widget for anyone.

- **Light traffic is no longer shown as nothing.** At the default settings, ~3,000 B/s reads
  `0.02 Mbps` instead of `0.0 Mbps`; a true zero still reads `0.0`.

- **A CPU temperature that never moves is no longer shown as if it were real.** Some boards
  answer every poll with the same round placeholder (290 K, 300 K), which the widget showed as
  17 °C or 27 °C forever. Those zones are now ignored; a real sensor still reads normally.
  ([#275], [#237])

  *Under the hood: ACPI thermal zones report tenths of a kelvin, and a Celsius-derived value
  never lands on a whole ten-kelvin multiple. A zone that reports one is a placeholder until the
  first time it changes, after which it is trusted for good. The 0-150 °C range check that let
  the placeholders through is unchanged.*

- **Hovering the Monitor graph shows a readout on every tab.** It only ever worked on the combined
  Hardware view; the Network tab and the other Hardware layouts showed nothing. The readout uses
  your unit setting, and it reports the peak under the cursor, so a one-second spike reads as
  drawn instead of as its neighbor.

  *Under the hood: two bugs masking each other. Only one plot call passed `label=`, so the
  tooltip skipped every other line as a helper; and the network axes carry Mbps while the
  formatter expected bytes/sec, so had it fired it would have read 40 Mbps as 320 bps. A series
  the renderer splits at gaps now collapses to one row; the dashed zero bridges stay unlabeled
  so a synthesized zero is never reported as a measurement.*

- **The database file actually shrinks now.** Maintenance compaction was gated on a condition
  that could not be true for an install's first year (or ever, on "Keep everything") while
  fragmentation grew from day one. It also no longer leaves a write-ahead log the size of the
  whole database behind afterwards.

### Security

- **Network adapter names no longer appear in logs or support bundles.** Adapters you have
  renamed ("Office VPN") are replaced with stable pseudonyms (`NIC-1a2b3c4d`) that still
  correlate across lines - including lines written by older versions, when a bundle is built.
  ([#263])

- **privacy.md now says exactly what the app does.** Hardware utilization history is recorded to
  the local database by default (the off switch lands in 2.2), and adapter-name scrubbing is
  described honestly as best-effort pseudonymization rather than "not included".

### Localization

- **Italian.** NetSpeedTray now speaks 15 languages - `it_IT` contributed by
  [@parmacvn-beep](https://github.com/parmacvn-beep). ([#279])
- **Two new strings** for the database notice are drafted in every language, matched to each
  file's existing wording; native review continues through [#202].

### Developer notes

- The release pipeline can now ship a beta safely: a prerelease tag publishes as a prerelease
  (not Latest), skips WinGet entirely, and the build chain accepts `-beta.N` version strings end
  to end. The false branch is rehearsed by this very release's tag. ([#257])
- The exe's `FileVersion` string in Explorer's file Properties now reads `2.1.5` (previously
  `2.1.4.0`-style); the numeric version resource is unchanged.
- Each release is submitted to the Microsoft Store listing by CI (`store-submit.yml`, which can
  also be run by hand for an existing release); the Store fetches the installer from the GitHub
  release asset. Prereleases never reach the Store, by the same guard as WinGet.
- The signing step waits up to 30 minutes for SignPath approval instead of 10, so a held
  approval no longer fails the whole run.
- 1,363 tests, up 138 from 2.1.4. Every fix above landed with a test demonstrated failing first.

[#202]: https://github.com/erez-c137/NetSpeedTray/issues/202
[#237]: https://github.com/erez-c137/NetSpeedTray/issues/237
[#257]: https://github.com/erez-c137/NetSpeedTray/issues/257
[#263]: https://github.com/erez-c137/NetSpeedTray/issues/263
[#264]: https://github.com/erez-c137/NetSpeedTray/pull/264
[#273]: https://github.com/erez-c137/NetSpeedTray/pull/273
[#275]: https://github.com/erez-c137/NetSpeedTray/issues/275
[#279]: https://github.com/erez-c137/NetSpeedTray/pull/279

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
