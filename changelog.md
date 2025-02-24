CHANGELOG.md

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

### NetSpeedTray 1.0.2-beta.3 - February 24, 2025

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

### Known Issues

    None reported in this release. Please test on multi-monitor setups with mixed DPI settings to confirm behavior.

## Acknowledgments

   Special thanks to the user community for detailed feedback on scaling and positioning issues, driving these improvements!

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

[1.0.2]: https://github.com/erez-c137/NetSpeedTray/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/erez-c137/NetSpeedTray/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/erez-c137/NetSpeedTray/releases/tag/v1.0.0
