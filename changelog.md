CHANGELOG.md

# Changelog

All notable changes to this project will be documented in this file.

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
