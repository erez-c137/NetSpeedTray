CHANGELOG.md

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.2 Beta] - 2024-02-22

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

## [1.0.1] - 2024-02-21

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

## [1.0.0] - 2024-02-21

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
