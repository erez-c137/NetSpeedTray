CHANGELOG.md

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.2] - February 26, 2025

### Added

- **Startup Synchronization**: Added command-line arguments (`--set-startup`, `--unset-startup`) to sync "Start with Windows" between the installer and app settings.

### Fixed

- **Invisible Widget Issue**: Resolved the issue where the app runs but the `Widget` remains invisible, ensuring proper display on the taskbar after launch, even after system restarts or environment changes (e.g., multi-monitor setups, fullscreen apps).
- **Dragging Error**:
  -Fixed and improving mouse event handling.

### Known Limitations

- **Multi-Monitor Support**:
  - Supports multi-monitor setups by detecting the taskbar screen, but may experience positioning or sizing issues if monitors have different DPI scaling levels (e.g., 125%, 150%, 200%, 300%).
  - Handles resolution mismatches between monitors, but scaling mismatches can cause issues.
- **KVM Switches**:
  - Should return to the correct position after switching via a KVM, but temporary mispositioning or scaling issues may occur if the new monitor setup differs (resolution, scaling, taskbar position).
- **Start Menu Interaction Issue**:
  - The `Widget` may hide or become unresponsive when the Windows Start menu is opened, particularly on multi-monitor setups or with custom taskbar configurations. This issue arises due to a Windows limitation where `Shell_TrayWnd` (the taskbar window) and related UI elements (e.g., Start menu) can obscure or temporarily disable overlay windows like the widget.
  - Windows does not provide a reliable API or event to distinguish Start menu activation from other fullscreen or taskbar-related states, leading to potential misdetection in `is_fullscreen_app_active` or `check_and_update`. This behavior is outside NetSpeedTray’s control but may be mitigated in future updates by enhancing taskbar and Start menu state tracking.
  - Functionally - when clicking on the start menu, the widget 'hides' and when clicking anywhere but on the taskbar, it will reappear
- **Edge Cases**:
  - Multiple or docked taskbars, monitor hot-plugging, high DPI scaling on small monitors, fullscreen apps on non-taskbar monitors, low-performance systems, KVM switches to non-Windows OS, and custom taskbar positions may cause issues or misbehavior.

### Future Improvements

- Enhanced multi-monitor support with per-monitor DPI scaling awareness.
- Robust handling of KVM switches, monitor hot-plugging, and custom taskbar positions.
- Performance optimizations for low-end systems.

[1.0.2]: https://github.com/erez-c137/NetSpeedTray/releases/tag/v1.0.2

## [1.0.2-beta5]- - February 25, 2025

### Fixed

- **High-DPI Scaling Issues**
  - Resolved oversized widget and visibility problems on 4K monitors with 200% scaling by improving DPI handling and ensuring consistent use of logical pixels.
- **Multi-Monitor Behavior**
  -Fixed widget hiding when moving the mouse between monitors in dual-monitor setups by refining screen detection and visibility logic.
- **Configuration Persistence**
  -Corrected an issue where `history_hours` reset to 1 hour on restart; now uses `history_minutes` with proper validation to persist user settings (e.g., 30 minutes).
- **Timer Management**
  - Removed duplicate `keep_alive_timer` initialization to prevent potential double-execution of visibility checks.

### Changed

- **History Units**
  -Switched from `history_hours` (fractional) to `history_minutes` (integer) for simpler, more readable configuration and UI consistency.
- **Positioning Logic**
  - Consolidated `load_position()`, `set_default_position()`, and `recover_position()` into `initialize_position()` for cleaner code and reliable taskbar alignment.
- **User-Specific Install**
  - Switched to per-user installation (`HKEY_CURRENT_USER`) and removed admin requirement to avoid UAC prompts and enhance compatibility with standard user accounts.

### Notes

- This beta includes significant improvements for high-DPI and multi-monitor environments. Please test on varied setups and report any issues.
- Old config files with `history_hours` will be automatically migrated to `history_minutes` on first load.

## Acknowledgments

Special thanks to the user community for detailed feedback on scaling and positioning issues, driving these improvements!

## [1.0.2-beta4]- - February 24, 2025

### Added

- **Automatic positioning**
  -Auto position to the left of the taskbar overflow menu or leftmost icon.
- **Initial speed display delay**
  - Added a small delay to ensure accurate rates only are displayed.

### Changed

- **Enhanced taskbar height matching**
  - Now at high DPI (e.g 200% on 4K) by using exact taskbar height.
- **Improved dragging stability**
  - Should now work better with stricter bounds checking.
- **Strengthened KVM switch position recovery**
  - App should now return to the correct positon by prioritizing saved X coordinate.

### Fixed

- **Prevented crashes during dragging**
  - Hoping this would fix the issue users reported, by adding error handling.
- **Corrected fullscreen app detection**
  - Changes were made to work correctly on multi-monitor setups with `QRect` intersection check.
- **Fixed graph poping over the taskbar**
  - The slicing errors should be resolved.

## [1.0.2-beta3]- February 24, 2025

**Overview:** This beta release resolves critical issues with widget positioning, DPI scaling, and settings stability, ensuring the widget embeds seamlessly on the taskbar and works reliably across different display scales.

#### Enhancements

- **Taskbar Embedding**:
  - Adjusted widget positioning to sit **on the taskbar**, aligning its top edge with the taskbar’s top edge for a seamless, embedded look similar to the clock and date.  
    Previously positioned above the taskbar; now fully contained within its height.
- **DPI Scaling Support**: Improved handling of high-DPI displays (e.g., 125%, 150% on 1080p, 200% on 4K) to ensure the widget remains visible and correctly positioned.
  - Widget no longer renders off-screen at non-standard scaling factors.

#### Bug Fixes

- **Widget Visibility at High DPI**:

  - Fixed an issue where the widget became invisible at 150% scaling on 1080p displays and was pushed down at 125% scaling.
  - Corrected Y-position calculations to use `taskbar_top` and bounds checking to keep the widget within the taskbar and screen.

- **Widget Movement**:
  - Fixed a `TypeError` in `mouseMoveEvent` where `new_pos.setY()` expected an integer but received a float.
  - Added `int()` to ensure proper type.
  - Ensured horizontal dragging locks Y to the taskbar’s top edge, preventing vertical drift.
- **Settings Dialog Positioning**:
  - Adjusted the Settings dialog to shrink and reposition correctly above the taskbar when disabling color coding, instead of moving to the upper-left corner.

#### Technical Notes

- Positioning now uses `y = taskbar_top` with a bounds check (`taskbar_bottom - self.height()`) to embed the widget within the taskbar’s logical height.
- Widget height is set to match the taskbar’s logical height via `get_taskbar_height()`, ensuring consistent scaling.
- All coordinates are adjusted with `devicePixelRatioF()` for DPI-aware rendering.

## [1.0.2-beta2] - February 24, 2025

### Enhanced

- **The fullscreen detection**
  - Improved fetection during system transitions (e.g., no active window, screen locked, etc.).
  - Using UPX compression to reduce the installation size.
- **Code Cleanup**:
  - Eliminated redundant timer initialization, ensuring consistent behavior.
  - Fixed unused variable warnings (e.g., font, content_width, old_config) by either utilizing them (e.g., right-aligned text in \_draw_speed_text) or removing them where appropriate.
- **Multi-Screen Support**:
  - Enhanced handling for multiple monitors, allowing the widget to stay on the taskbar of the screen it’s dragged to, with proper alignment for vertical and horizontal taskbars.
- **Reduced "Flickering"**:
  - Optimized z-order management using GW_HWNDPREV to keep the widget consistently above the taskbar without flicker when interacting with taskbar elements or switching apps.
  - Minimized unnecessary redraws by conditionally updating position and refreshing the widget only when needed.
- **Changed the install path**:
  - App installer will now place at a more appropriate location (C:\Program Files\NetSpeedTray).
  - Using UPX compression to reduce the installation size.

### Fixed

- **Reduced "Flickering"**:
  - Optimized z-order management using GW_HWNDPREV to keep the widget consistently above the taskbar without flicker when interacting with taskbar elements or switching apps.
  - Minimized unnecessary redraws by conditionally updating position and refreshing the widget only when needed.
- **"Ensured only a single instance of the application can run at a time to prevent conflicts and improve stability"**

## [1.0.2-beta1] - February 23, 2025

### Fix

- **Attempting to Resolved issue where the UI doesn't not appear for some users**:
  - The application now correctly initializes and ensures the widget is visible.
  - Startup sequence adjusted to prevent race conditions during first launch.
  - UI position resets if an off-screen position is detected.
  - Should also work fine now with "Auto hide taskbar" setting.
- **Cleaned up and improved logging-related code** to improve efficiency.

### Known Issues

- **The UI "flickers" once when clicking on the taskbar after activity in another app**:
  - This is due to the change in the way the app is drawn over the taskbar, trying to resolve the issue some users reported, but before I continue down this path trying to fix it, I'd like some feedback that this fix works.

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

### Fixed

- **Settings dialog now properly shows the application icon in the title bar.**
- **Application visibility now properly syncs with taskbar:**
  - Widget auto-hides when taskbar is hidden (fullscreen mode).
- **Settings dialog behavior:**
  - Proper expand/collapse animation
  - Maintains screen position when expanding
  - Consistent spacing and alignment
  - Better visual hierarchy

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
