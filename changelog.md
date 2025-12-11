# Changelog

All notable changes to this project will be documented in this file.

---

## [1.1.8] - December 11, 2025

This release marks a significant maturity milestone for NetSpeedTray. We are proud to announce that the application is now **digitally signed**, establishing a chain of trust and eliminating security warnings. Additionally, this update brings full Russian language support and a completely modernized, automated build pipeline.

### 🛡️ Security & Trust
*   **Digitally Signed Release:** NetSpeedTray is now officially signed with a trusted code signing certificate.
    *   Eliminates the "Unknown Publisher" warning from Windows SmartScreen.
    *   Guarantees that the executable has not been tampered with since it left the build server.
*   **Security Patches:** Updated critical dependencies (including `fonttools` and `pandas`) to the latest secure versions to resolve reported vulnerabilities (CVEs).
*   **Hardened Build Process:** Implemented strict input sanitization in the GitHub Actions workflow to prevent script injection attacks.

### 🌍 Localization
*   **Russian Language Support:** Added complete translation for the Russian language (Русский).
*   **Locale Best Practices:** Updated the internal localization engine to use native language names (Endonyms) in the settings menu.

### 🤖 Automation & CI/CD
*   **Fully Automated Pipeline:** Implemented a robust CI/CD workflow using GitHub Actions. Every release is now built, tested, and packaged in a clean, isolated environment, ensuring 100% reproducibility.
*   **Automated Versioning:** The application version is now dynamically injected from Git tags directly into the executable, installer, and internal metadata. This ensures the "File Version" in Windows Properties always matches the release tag perfectly.
*   **Quality Gates:** Unit tests are now automatically executed before every build. If a test fails, the build is stopped immediately, preventing buggy releases from reaching users.

---

## [1.1.7] - October 29, 2025

This is a landmark release focused on stability and making the application's most complex feature—the **Network Speed Graph** - a fast, and visually insightful tool.

The graph has been completely re-architected for performance and clarity. This update also includes an extensive list of critical bug fixes that address phantom speed spikes, UI glitches, installer problems, and instability when the Windows shell is restarted.

### 🚀 Major Graph Window Overhaul

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

### 🛡️ Core Stability & Reliability

*   **Definitive Fix for "Phantom" Speed Spikes:** Implemented a new multi-stage "re-priming" state to permanently fix the bug where impossible network speeds would be recorded after the computer resumed from sleep or experienced heavy lag. The data collection engine now waits for the network drivers to stabilize before resuming measurements.
*   **Enhanced Shell & Display Resilience:** Fixed major bugs where the widget would disappear or move to the wrong position after `explorer.exe` was restarted, a monitor was disconnected (e.g., via a KVM switch), or on some multi-monitor setups. The application is now significantly more robust in detecting and recovering from these events.
*   **Fixed "Zombie" Process Bug:** Solved a critical issue where closing the graph window would also incorrectly close the main widget, leaving a lingering "zombie" process running in the background.
*   **Fixed Start Menu Shortcut:** Corrected a bug in the installer that prevented the Start Menu shortcut from being created on a fresh installation.
*   **Fixed "0 Mbps" Bug:** Fixed a logic error that caused the meter to show `0.00 Mbps` for users with a very fast `update_rate` by making internal timing checks dynamic and more robust.
*   **Fixed UI Glitches:**
    *   Resolved an issue where the widget would incorrectly move position after the user clicked the "Show hidden icons" tray chevron.
    *   Fixed a visual glitch that could cause duplicated "Apply" and "Cancel" buttons to appear in the settings dialog.

### ⚙️ Under the Hood & Code Quality

*   **Comprehensive Code Refactoring:** Many internal components were refactored to improve maintainability and performance. This includes centralizing application-wide constants to eliminate "magic numbers" and improve consistency.
*   **Hardened Test Suite:** The project's automated test suite (`pytest`) has been significantly expanded and improved, ensuring that all new features and bug fixes are thoroughly validated, leading to a more stable application.
*   **Enhanced Logging Privacy:** The logging system's privacy filter has been replaced with a more powerful `ObfuscatingFormatter` that redacts sensitive information (user paths, IP addresses) from the *entire* log message, including full tracebacks.

---

##  [1.1.6] - August 27, 2025

This version represents a major leap forward in stability, internationalization, and user control, addressing critical bugs from previous versions and fundamentally improving the application's architecture for future development.

### ✨ New & Reworked Features

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

### 🐛 Fixed & Stability Improvements

-   **Definitive Fix for Disappearing/Flickering Widget:**
    -   A fundamental issue in event handling, which caused the widget to disappear when interacting with the desktop, taskbar, or RDP sessions, has been resolved.
    -   **New Architecture:** The old, aggressive logic has been replaced with a **debounced refresh architecture**. The application now uses `WinEventHook` listeners to intelligently wait for system UI events (like window focus changes or resizes) to "settle" before performing a single, authoritative check on the widget's visibility and Z-order.
    -   This resolves all related stability issues, including the widget hiding when right-clicking for a context menu and correctly handles browser fullscreen videos.

-   **Database Aggregation Logic:**
    -   Fixed a bug in the SQL `GROUP BY` clause for both minute-to-hour and raw-to-minute data aggregation, ensuring that records are correctly combined and preventing duplicate entries in aggregated tables.

### 🏠 Architectural & Internal Improvements

-   **Constants Refactoring:** All user-facing strings were removed from the `constants` files and replaced with non-translatable keys. This improves code clarity and centralizes all text in the `locales` directory.
-   **Dependency Injection for i18n:** The internationalization object (`i18n`) is now properly initialized at the application entry point (`monitor.py`) and passed down as a dependency to all UI components (`widget`, `settings`, `graph`, `renderer`) and helper functions (`format_speed`).
-   **Test Suite Updates:** The `pytest` unit tests have been updated to reflect all architectural changes, including the new interface monitoring modes and the use of i18n keys, ensuring the application's logic remains sound.
-   **Installer Reliability:** The installer continues to gracefully shut down a running instance of the application before updating, preventing common installation errors.
-   **Improved Decimal Formatting UI:** The confusing "Force Decimals" toggle has been removed in favor of a single, intuitive slider that directly controls the number of decimal places (0, 1, or 2), with output now consistently padded with zeros for a more stable appearance.

---

## [1.1.5] - August 24, 2025 (Hotfix)

This is a critical hotfix release that provides a definitive and comprehensive fix for the startup crash and several related stability issues discovered during the beta cycle.

**This is a highly recommended update for all users.**

### 🐛 Fixed

-   **Critical Startup Crash & Systemic Stability Issues:** Resolved a complex chain of initialization and rendering errors that caused the application to crash on first launch, particularly on systems with specific UI configurations (like a small taskbar).
    -   Following user feedback from the beta releases (a huge thank you to GitHub user **[CMTriX](https://github.com/CMTriX)**!), a full codebase audit was performed.
    -   This audit eradicated a systemic typo pattern and fixed numerous latent bugs in the widget rendering, positioning, and various utility modules (`taskbar`, `network`, `config`).
    -   The result is a stable and reliable experience across a much wider variety of Windows environments.

### ✨ Improved

-   **Installer Reliability:** The installer is now much more robust when updating a running instance of NetSpeedTray. It now attempts a graceful shutdown of the application before proceeding with the update, preventing the "Setup was unable to automatically close all applications" error and ensuring a smoother, more successful update process.

---

## [1.1.4] - August 23, 2025 (Hotfix)

This is an immediate hotfix to address a critical bug in the "Start with Windows" feature introduced in v1.1.3.

### 🐛 Critical Bug Fixes

-   **Fixed "Start with Windows" Toggle:** Resolved a critical logic flaw where the "Start with Windows" setting could not be disabled. The toggle in the settings window will now correctly reflect the user's saved choice, and disabling the feature now correctly removes the application's entry from the Windows Registry.

---

## [1.1.3] - August 22, 2025 (Hotfix)

This is an urgent hotfix release that addresses a critical startup crash reported by users after the v1.1.2 update. It also restores and improves the widget's positioning stability, resolving several visual regressions.

### 🐛 Critical Bug Fixes & Stability Improvements

-   **Fixed Critical Startup Crash:** Resolved a critical `AttributeError` that prevented the application from launching on some systems. This was caused by an unreliable network dependency (`netifaces`) which has now been completely removed and replaced with a more robust, built-in solution.
-   **Restored Widget Stability (Fixed Flashing):** Re-engineered the widget's core update logic to eliminate a visual "flashing" regression introduced in v1.1.2. The widget is now perfectly stable and only updates its position when absolutely necessary, removing all polling-related flicker.
-   **Improved UI Responsiveness:** Fixed two key regressions where the widget would not:
    -   Reliably reappear after launching an application (like Calculator) from the Start Menu.
    -   Automatically reposition itself when new icons appeared in the system tray.

---

## [1.1.2] - August 22, 2025

This is a major stability and quality-of-life release that addresses critical bugs, enhances UI intelligence, improves user privacy, and completely overhauls the installer and settings backend for a more professional and robust user experience.

### ✨ Major Features & Improvements

-   **Intelligent Interface Monitoring (New Default):** The method for selecting network interfaces has been completely redesigned for clarity and accuracy.
    -   It now uses a clear, three-option radio button system: `All`, `Auto`, and `Selected`.
    -   **"Auto (Primary)" is the new default mode.** It intelligently identifies your main internet adapter, providing a much more accurate speed reading by ignoring noise from VPNs, virtual machines, and other virtual adapters.

-   **Language Selection:** The application is now fully internationalized. Users can select their preferred language from a new dropdown in the General settings. A restart is required for the change to take full effect.

-   **Intelligent Taskbar Positioning:** The widget's positioning logic has been completely re-architected. It now actively scans the taskbar for "obstacles" like the Start Menu, pinned application icons, and the Windows Weather/Widgets icon. It intelligently places itself in the nearest truly empty space, preventing it from overlapping with other UI elements.

-   **Adaptive Layout for Small Taskbars:** The widget now automatically detects when Windows' "Use small taskbar buttons" setting is active and switches to a clean, compact, single-line horizontal layout for a much better visual fit.

### 🐛 Critical Bug Fixes & Refinements

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

### ⚙️ Build System, Installer & Privacy

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

---

## [1.1.1] - August 18, 2025

This release focuses on providing a fast, and native-feeling user experience as much as possible. It introduces a major startup performance overhaul, addresses key bugs related to the history graph and UI integration, and refines the application's overall stability.

### ✨ Major Features & Improvements

-   **Drastically Improved Startup Performance:** The application's compiled structure has been changed to eliminate the slow, single-file unpacking process.
    -   **Faster Launch:** Startup time is now significantly faster, as the application and its dependencies are no longer extracted to a temporary folder on every launch.
    -   **New Distribution Formats:** To support this, NetSpeedTray is now distributed with a fast Inno Setup installer and a portable `.zip` archive for users who prefer a non-install option.
-   **Seamless UI Responsiveness & Integration:** The widget's visibility logic is now fully event-driven, eliminating delays and making it feel like a native part of the Windows shell.
    -   **Instantaneous Auto-Hide & Fullscreen Detection:** The widget now appears and disappears instantly with an auto-hiding taskbar and when entering or exiting fullscreen applications.
    -   **Graceful System UI Handling:** Proactively hides when core system menus (like the Start Menu and network/volume flyouts) are opened, and reappears upon closing them. This provides a polished, non-intrusive experience and avoids visual glitches.
    -   **Reliable Z-Order:** The widget now correctly stays on top of the taskbar and other applications after focus changes.
-   **Smart Taskbar Theme Detection:** Fixed a critical bug where the widget's text color would be incorrect for users with a "mixed theme" (e.g., Light app mode with a Dark taskbar). The widget now correctly bases its text color on the **taskbar's theme**, ensuring visibility in all configurations.

### 🐛 Bug Fixes & Refinements

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

### ✨ Major Features & Improvements

-   **Intelligent Graph Visualization:** The history graph is now significantly more insightful and readable.
    -   **Dynamic Logarithmic Scale:** A new `symlog` scale solves the "flat line" problem, allowing you to see fine-grained detail in your low-speed traffic without high-speed spikes squashing the visualization.
    -   **Smart Axis Boundaries:** The Y-axis scale is now data-driven, analyzing your traffic patterns to set a "normal usage" range that ensures the graph is always well-suited to your network.
    -   **Clean, Readable Ticks:** The Y-axis labels are now always round numbers (e.g., 0, 10, 100, 1000), making the graph intuitive and easy to read.
-   **Smart Interface Monitoring:** The application can now automatically identify your primary internet connection (e.g., "Wi-Fi" or "Ethernet") and display its speed by default, providing a cleaner and more accurate reading.
-   **Per-Interface Graph Filtering:** A new "Interface" dropdown in the graph settings allows you to visualize the speed history for any specific network adapter on your system.
-   **Improved Data Accuracy:**
    -   Fixed a bug that could cause large, incorrect speed spikes in the database after the computer woke from sleep. The controller now handles these events correctly, ensuring historical data is more reliable.
-   **Safer Data Retention Policy:** If you reduce the data retention time (e.g., from 1 year to 7 days), the application now waits for a 48-hour grace period before pruning old data, preventing accidental data loss.

### 🌍 Internationalization

-   **Added full German (de_DE) language support.** Many thanks to the users and communities on **Chip.de** and **Softpedia** for their attention and support.

### 🐛 Bug Fixes & Refinements

-   **Architectural Overhaul:**
    -   The core data layer (`WidgetState`) has been rebuilt with a multi-tiered database (`raw`, `minute`, `hour`) and a dedicated worker thread to improve UI responsiveness.
    -   The `Controller` has been updated to support granular, per-interface data collection.
    -   The application's constants have been refactored into a more organized package structure.
-   **Performance:** Key libraries like `numpy` and `matplotlib` are now lazy-loaded, making the initial application startup faster and more lightweight.
-   **Graphing Engine:**
    -   The "Session" view now correctly uses live in-memory data.
    -   The `Export to CSV` feature now exports the currently filtered view.
-   **Technical Debt:** The obsolete `core/model.py` module has been removed, simplifying the codebase.

### ⚠️ Important Note for Existing Users

-   This version introduces a new database format to enable per-interface monitoring and improved accuracy.
-   **The upgrade is automatic and safe.** When you first run v1.1.0, the application will detect the old database, back it up by renaming it to `speed_history.db.old`, and create a new one. No manual steps are required.

---

## [1.0.9] - August 4, 2025

This is a major stability, performance, and quality-of-life update focused on refining the widget's core behavior, improving data accuracy, and optimizing the application's architecture for future features.

### ✨ Major Improvements

- ** Improved Widget Stability & Behavior:** The widget now feels much more like a native part of the Windows taskbar. An entirely new, event-driven architecture (using a WinEventHook and a safety-net timer) has been implemented to intelligently manage visibility.
  The widget no longer flickers, disappears, or gets stuck behind the taskbar when interacting with the Calendar, Network/Volume flyouts, or the tray overflow menu.

- **Enhanced Startup Performance:** The application's impact on system startup has been reduced. By implementing "lazy loading" for the graph window and other UI components, the initial launch is now faster and lighter on system resources.

### 🐛 Bug Fixes & Refinements

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

### ⚠️ Important Note for Existing Users

- To enable the data accuracy fixes, **users upgrading from a previous version must delete their old history database.** A new, clean database will be created automatically.
- **Instructions:**
  1.  Ensure NetSpeedTray is not running.
  2.  Open File Explorer, paste `%APPDATA%\NetSpeedTray` into the address bar, and press Enter.
  3.  Delete the file named `speed_history.db`.

---

## [1.0.8] - July 31, 2025

This release focuses on improving the reliability and accuracy of the Network Speed Graph, resolving all known bugs related to data display and state persistence.

### ✨ Graph Window Improvements

- **Accurate "System Uptime" Timeline:** The "System Uptime" view now correctly uses the system's actual boot time as its starting point, perfectly matching the behavior of the Windows Task Manager.
- **Correct "Session" Timeline:** The "Session" view is now persistent and correctly displays data from the start of the application, regardless of how many times the graph window is opened or closed.
- **Accurate Statistics Bar:** The "Max" and "Total" statistics displayed at the top of the graph are now always calculated correctly based on the selected timeline, ensuring you see the right data for the right period.
- **Improved Initial Load:** Fixed a bug where the graph would sometimes show "No data available" on its first launch, even when historical data was present. It now reliably displays the correct timeline from the moment it opens.

### 🐛 Bug Fixes & Refinements

- **Build Process:** The build script has been made more robust.
- **Configuration:** The application's configuration file now officially recognizes and validates all graph-related settings, eliminating harmless but noisy warning messages from the log files.

---

## [1.0.7] - July 30, 2025

This is a landmark stability release that perfects the core user experience of the widget. Through a comprehensive overhaul of the positioning and state management logic, the widget now behaves with the rock-solid predictability of a native Windows UI element. All visual "flickering," "jumping," and "drifting" issues have been eliminated.

### ✨ Core Experience & Stability Overhaul

- **Perfectly Stable Positioning:** Resolved a complex series of deep-seated bugs that caused the widget to flicker or jump. The widget's position is now completely stable during application startup, after closing the settings window, and while the system tray icons change.
- **Smooth & Intuitive Dragging:** The widget no longer "fights" the user's cursor or snaps to a slightly different spot after being moved. The position where you release the mouse is now its final, pixel-perfect location.
- **Intelligent Snap-to-Edge:** When dragging the widget near the system tray, it now intelligently snaps to a minimum safe distance if moved too far, gracefully respecting the user's intent to place it as close as possible.
- **Unrestricted Placement:** The arbitrary limit on how far the widget could be dragged along the taskbar has been removed. Placement is now constrained only by the edges of your screen.

### 🐛 Bug Fixes & Refinements

- **Graph Window:** Fixed a critical `AttributeError` that could prevent the Graph Window from opening correctly.
- **Configuration File:** The app's config file is now cleaner and no longer stores unnecessary `position_x` and `position_y` fields when they are not in use.
- **UI Text:** The "Enable Free Move" label in settings has been simplified to "Free Move (No Snapping)" for better clarity.

---

## [1.0.6] - July 29, 2025

This release focused on quality-of-life improvements, introducing the initial version of the adaptive widget positioning and overhauling the installation process for seamless future updates.

#### ✨ Added

- **Adaptive Positioning (Beta):**
  - The widget now learns and maintains your preferred distance from the system tray.
  - It automatically shifts its position to prevent being overlapped by new application icons appearing in the tray.
  - Your custom spacing is "learned" simply by dragging the widget while _Free Move_ is disabled.

#### 🛠️ Improved

- **Seamless Upgrades & WinGet Compatibility:** The Windows installer has been overhauled. It now correctly replaces the previous version's files, ensuring a clean and reliable upgrade experience. This change also prepares the application for distribution via the Windows Package Manager (WinGet).
- **UI Clarity:** Renamed the confusing "Smart Threshold" toggle to **"Dynamic Update Rate"** to more accurately describe its power-saving function.
- **User-Friendly File Naming:** The configuration and log files have been renamed to be more descriptive (`NetSpeedTray_Config.json` and `NetSpeedTray_Log.log`), making them easier for users to identify.

---

## [1.0.5] - July 29, 2025

### ✨ New Features

- **Free Move is Here!**

  - You can now unlock the widget from the taskbar and place it **anywhere on your screen**.
  - The widget's position is automatically saved when Free Move is enabled and it reliably snaps back to its default location when disabled.

- **Total Control Over Speed Units:**
  - A new **Speed Units** panel has been added to the settings for granular control over the text display.
  - **Display Mode:** Choose between 'Auto' scaling (bps, Kbps, Mbps) and 'Always Mbps'.
  - **Decimal Places:** Set the speed value precision from 0 to 2 decimal places.
  - **Text Alignment:** Align the speed text left, center, or right within the widget.
  - **Force Decimals:** A new option to always show decimal points (e.g., `5.0` instead of `5`).

### 🛠️ Improvements & Refinements

- **Smarter Installer:** The Windows installer now automatically replaces the old executable, ensuring a clean and seamless upgrade experience. Old version files are removed.
- **Improved Configuration:** The configuration file has been renamed to `NetSpeedTray_Config.json` for clarity. The management system is now more robust, preventing settings from being accidentally discarded.
- **Cleaner Log Files:** The log file has been unified and renamed to `NetSpeedTray_Log.log` to make troubleshooting easier.
- **Accurate Graph Stats:** The statistics in the main graph's status bar are now calculated correctly for all timelines, including "All".

### 🐛 Bug Fixes

- **CRITICAL: Mini-Graph Now Renders Correctly:** Fixed a series of silent failures that were preventing the mini-graph from appearing on the widget. This was the most significant bug from the beta and is now fully resolved.
- **CRITICAL: State Persistence Fixed:** Resolved a major bug where toggle states (like **Free Move**, **Force Decimals**, and **Start with Windows**) were not being saved correctly across application restarts.
- **"Snap-Back" Bug Fixed:** Corrected a state management flaw where disabling "Free Move" sometimes required clicking "Save" twice. The widget now snaps back to its default position instantly and reliably on the first click.

---

## [1.0.5-Beta2] - July 29, 2025

This release introduces powerful new customization features for the taskbar widget and resolves a series of critical bugs that were discovered following the major refactor in Beta1.

#### Added

- **Free Move Feature:**

  - Introduced the **Free Move** feature, allowing users to unlock the widget from the taskbar and place it anywhere on the screen.
  - The widget's position is now saved when Free Move is enabled and it automatically and reliably snaps back to its default location when disabled.

- **Speed Units Customization:**
  - Added a new **Speed Units** section to the settings for granular control over the text display.
  - **Speed Display Mode:** Choose between 'Auto' scaling (bps/Kbps/Mbps) and 'Always Mbps'.
  - **Decimal Places:** Set the precision of the speed values (0, 1, or 2).
  - **Text Alignment:** Align the speed text to the left, center, or right of the widget.
  - **Force Decimals:** An option to always show decimal points (e.g., '5.0' or '5.00' instead of '5').

#### Changed

- **Speed Unit Logic:** The old 'Use MB/s' toggle has been removed and replaced by the more flexible and powerful Speed Display Mode.
- **Configuration Management:** Refactored the `ConfigManager` to be more robust and declarative, preventing future issues with unsaved settings.
- **Data Flow:** Streamlined the data pipeline between the widget and the renderer to prevent data corruption and improve stability.

#### Fixed

- **Main Graph Stats:** Improved the accuracy of the main graph's status bar; statistics for the 'All' timeline are now calculated correctly.
- **Mini-Graph Rendering:** Fixed a critical bug where the **Mini-Graph would not render** on the widget. This was caused by a series of silent failures, including a `NameError`, a data corruption issue in the renderer's data flow, and an incorrect drawing call.
- **State Persistence:**
  - Resolved an issue where the **'toggle states were not being saved** across application restarts.
  - Fixed a state management bug that required clicking 'Save' twice to disable Free Move; the widget now snaps back instantly.
- **Settings Not Saving:** Corrected a validation issue that prevented several settings (`force_decimals`, `start_with_windows`, etc.) from being saved to the configuration file.

---

## [1.0.5-Beta1] - July 27, 2025

#### Major Overhaul

- **Full Modular Refactor:**  
  Migrated from a single-file script to a modern, maintainable package structure (`src/netspeedtray/`). All logic is now organized into `core`, `views`, `utils`, `constants`, and `tests` modules.

#### Added

- **Modern UI/UX:**
  - Redesigned settings and graph windows with PyQt6, custom dark mode and improved layout.
  - Hamburger menu for quick access to graph settings.
  - New icons and centralized asset management.
- **Testing:**
  - Added unit tests for configuration, constants, and core logic.

#### Changed

- **Code Quality:**
  - Improved type hints, docstrings, and error handling throughout.
  - Enhanced logging and configuration management.
- **User Experience:**
  - Graph and settings dialogs now always open centered and within screen bounds.

#### Fixed

- **Stability & Layout:**
  - Fixed window icon issues and theme inconsistencies.
  - Improved error overlays and ensured dialogs respect minimum sizes and DPI scaling.

#### Known Issues

- **App Usage Tab:** Temporarily disabled pending further development.
- **Multi-Monitor:** Still limited to primary taskbar’s screen; multi-monitor support improvements planned.

---

## [1.0.4] - March 7, 2025

#### Added

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

#### Changed

- **UI Improvements**:
  - Modernized `SettingsDialog` with toggle switches and better layout spacing.
  - Increased `GraphWindow` default size to 802x602 pixels for improved readability.
  - Enhanced mini-graph rendering with configurable opacity.
- **Performance**:
  - Throttled `GraphWindow` updates to 500ms intervals to reduce UI lag.
- **Configuration**:
  - Updated default font to "Segoe UI Variable Small" for consistency.

#### Fixed

- **Stability**:
  - Improved error handling in `GraphWindow` and CSV logging with thread-safe locking.
- **Layout**:
  - Ensured dialogs (Settings, Graph) stay within screen bounds and anchor correctly relative to the widget.
- **Visibility**:
  - Refined fullscreen app detection to prevent widget hiding issues.

#### Known Issues

- **Multi-Monitor**: Limited to the primary taskbar’s screen; doesn’t fully support multiple taskbars or dynamic monitor changes without manual repositioning.
- **High-Frequency Updates**: May still impact performance on low-end systems despite throttling.
- **False Flagging** - [VirusTotal report](https://www.virustotal.com/gui/file/3c045c40ae2dd077fa66f5881649763b11b2584419f9e35b4421bee4f17fc3cf)

---

## [1.0.3] - March 1, 2025

### Bug Fixes

- **Startup Positioning Issue**: Resolved the issue where the `Widget` jumps to the top-left corner on startup after login, ensuring it maintains its exact saved position immediately.
- **Desktop Click Hiding/Flashing**: Fixed the issue where clicking the desktop causes the widget to hide and "flash", improving fullscreen detection and adding debouncing to prevent rapid visibility toggles.
- **Portable Startup Issue**: Fixed the "Start with Windows" option in the portable version, ensuring it creates a shortcut in the Startup folder for automatic launch on login, matching installed version behavior (requires `pywin32` for shortcut creation).

### Enhancements

- **Improved Widget Visibility and Z-Order Management**
  - Enhanced the widget’s visibility handling to ensure the widget remains visible across window switches and taskbar interactions.
- **Optimized Settings Dialog Positioning**
  - Streamlined the positioning of the settings dialog to use Qt’s screen geometry, ensuring it remains fully visible above the taskbar in multi-monitor setups, with better handling of size changes after saving settings.

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

### Bugs Fixes

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

### Enhanced

- **Settings dialog improvements:**
  - Streamlined layout and organization
  - Smart collapsible sections
  - Dynamic position adjustment
  - Improved interface selection controls
- **Default speed thresholds adjusted:**
  - High speed threshold: **5 Mbps** (was **1 Mbps**)
  - Low speed threshold: **1 Mbps** (was **0.1 Mbps**)

### Bug Fixes

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
