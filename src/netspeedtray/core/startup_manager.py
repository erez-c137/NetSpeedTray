"""
Startup Manager Module.

Handles all logic related to Windows startup registry keys.
"""
import logging
import os
import sys
import winreg
from typing import Optional

from netspeedtray import constants

# Where Windows keeps Program Compatibility flags ("Run this program as an administrator" etc.),
# as one value per exe path, in both hives.
_COMPAT_LAYERS_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"


def get_compat_layers(exe_path: str) -> str:
    """The compatibility flags Windows applies to `exe_path` (HKCU and HKLM joined), e.g.
    "~ RUNASADMIN". Empty when there are none or the key can't be read."""
    found = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, _COMPAT_LAYERS_KEY) as key:
                value, _ = winreg.QueryValueEx(key, exe_path)
                if value:
                    found.append(str(value))
        except OSError:
            pass
    return " ".join(found)


# Task Manager's Startup apps switch lives apart from the Run value, one REG_BINARY per entry name:
# first byte even (02) = enabled, odd (03) = disabled, then a FILETIME of when it was disabled.
# Windows skips a disabled entry at sign-in even though the Run value is still there (#308).
_STARTUP_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


def get_startup_approved_state(app_name: str) -> str:
    """Task Manager's verdict on the Run entry `app_name`: "enabled", "disabled" or "not set"
    (never toggled in Task Manager, which Windows treats as enabled)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_APPROVED_KEY) as key:
            data, _ = winreg.QueryValueEx(key, app_name)
    except OSError:
        return "not set"
    if not isinstance(data, (bytes, bytearray)) or not data:
        return "not set"
    return "disabled" if data[0] & 1 else "enabled"


def _clear_startup_approved(app_name: str) -> None:
    """Forget Task Manager's switch for `app_name` - a missing value means enabled."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_APPROVED_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, app_name)
    except OSError:
        pass


def is_process_elevated() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class StartupManager:
    """
    Manages the 'Run at Startup' functionality using the Windows Registry.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(f"{constants.app.APP_NAME}.StartupManager")

    def is_startup_enabled(self, force_check: bool = False) -> bool:
        """
        Whether Windows will actually start the app at sign-in: our Run value is present (and, by
        default, points at this exe) AND Task Manager hasn't disabled it. Before 2.1.7 the Task
        Manager half was ignored, so Settings read ON for an entry Windows skipped (#308).
        `force_check=True` does a simple existence check of the Run value.
        """
        if not self._check_startup_registry(check_for_correctness=not force_check):
            return False
        return get_startup_approved_state(constants.app.APP_NAME) != "disabled"

    def toggle_startup(self, enable: bool) -> bool:
        """
        Toggles the startup registry key on or off.
        Returns the new state (True for enabled, False for disabled).
        """
        self.logger.debug(f"Toggling startup: {enable}")
        self._set_startup_registry(enable)
        # An explicit choice in our Settings overrides an older Task Manager disable - otherwise
        # turning it ON here did nothing at all (#308). Turning it off removes the stale entry too.
        _clear_startup_approved(constants.app.APP_NAME)
        actual_state = self.is_startup_enabled()
        
        if actual_state != enable:
            self.logger.warning(f"Startup toggle mismatch! Requested: {enable}, Got: {actual_state}")
        
        return actual_state
        
    def synchronize_startup_task(self, should_be_enabled: bool) -> None:
        """
        Ensures the Windows startup task state matches the setting in the config file.
        This runs once on application startup to correct any mismatches.
        """
        # DEV MODE PROTECTION:
        # If running in development (not frozen), we must NOT overwrite a production registry key.
        # This prevents "python src/monitor.py" from hijacking "NetSpeedTray.exe".
        if not getattr(sys, 'frozen', False):
            try:
                # Direct check of registry value without validity comparison
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                value, _ = winreg.QueryValueEx(key, constants.app.APP_NAME)
                winreg.CloseKey(key)

                val_lower = value.lower()
                # Heuristic: If it points to an .exe and NOT a python executable, assume it's a prod build.
                if ".exe" in val_lower and "python" not in val_lower:
                    self.logger.warning(f"DEV MODE: Detected production startup key ({value}). Skipping synchronization to prevent overwrite.")
                    return
            except Exception:
                pass # Key doesn't exist or other error, safe to proceed with normal logic

        # The Run value only: a Task Manager disable is the user's choice, and this runs on every
        # launch - it must never silently undo it (it warns below instead).
        is_actually_enabled = self._check_startup_registry()
        self.logger.debug(f"Syncing startup task. Config says: {should_be_enabled}, Registry says: {is_actually_enabled}")

        if should_be_enabled and not is_actually_enabled:
            self.logger.debug("Re-enabling startup task to match configuration.")
            self._set_startup_registry(True)
        elif not should_be_enabled and is_actually_enabled:
            self.logger.debug("Disabling startup task to match configuration.")
            self._set_startup_registry(False)

        if should_be_enabled and get_startup_approved_state(constants.app.APP_NAME) == "disabled":
            self.logger.warning(
                "Start with Windows is on in NetSpeedTray, but NetSpeedTray is disabled in Task Manager's "
                "Startup apps, so Windows will NOT start it at sign-in. Turn it on in Settings or in "
                "Task Manager > Startup apps.")
        if should_be_enabled and getattr(sys, 'frozen', False):
            self._warn_if_startup_will_be_skipped(sys.executable)

    def _warn_if_startup_will_be_skipped(self, exe_path: str) -> None:
        """Windows silently skips a Run-key entry whose exe requires elevation (#308: verified on a
        real logon - the flagged entry never ran, even with silent UAC elevation). Nothing reaches
        our log because we never start, so say it now, from a launch that did happen."""
        layers = get_compat_layers(exe_path)
        if "RUNASADMIN" in layers.upper():
            self.logger.warning(
                "Start with Windows is on, but this exe is set to always run as administrator "
                "(compatibility flags: %s). Windows skips startup entries that require elevation, "
                "so NetSpeedTray will NOT start at sign-in. Untick 'Run this program as an "
                "administrator' in the exe's Properties > Compatibility.", layers)

    def _get_executable_path(self) -> str:
        """Gets the correct, quoted executable path or command for the registry."""
        if getattr(sys, 'frozen', False):
            # PyInstaller creates a one-file executable or folder
            exe_path = sys.executable
        else:
            # Development mode: run python.exe with the script
            # Use pythonw.exe if available to avoid console window
            python_exe = sys.executable.replace("python.exe", "pythonw.exe")
            if not os.path.exists(python_exe):
                python_exe = sys.executable
            
            # We need to point to the entry point. Assuming it's src/monitor.py or similar.
            # But the most robust way in dev is to point to the module if possible, or the script.
            # Here we try to reconstruct the command used to launch.
            # Using absolute path to src/monitor.py
            script_path = os.path.abspath(sys.argv[0])
            exe_path = f'"{python_exe}" "{script_path}"'
            return exe_path # Already quoted the parts

        return f'"{exe_path}"'

    def _check_startup_registry(self, check_for_correctness: bool = True) -> bool:
        """
        Checks if the startup registry key exists.
        If `check_for_correctness` is True, it also verifies that the key points 
        to the *current* executable location.
        """
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ
            )
            try:
                value, _ = winreg.QueryValueEx(key, constants.app.APP_NAME)
                winreg.CloseKey(key)
                
                if not check_for_correctness:
                    return True
                
                # Check if the path matches current executable
                expected_path = self._get_executable_path()
                
                # Normalize for comparison (lowercase, handle quotes consistency)
                # The registry might or might not have quotes depending on how it was set previously.
                val_norm = value.lower().strip().replace('"', '')
                exp_norm = expected_path.lower().strip().replace('"', '')
                
                if val_norm == exp_norm:
                    return True
                else:
                    if getattr(sys, 'frozen', False):
                        self.logger.warning(f"Startup key exists but path mismatch. Reg: {value}, Exp: {expected_path}")
                    return False # Treat as "not enabled" so we re-set it correctly
                    
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except Exception as e:
            self.logger.error(f"Failed to check startup registry: {e}")
            return False

    def _set_startup_registry(self, enable: bool) -> None:
        """Sets or deletes the startup registry key."""
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            if enable:
                exe_path = self._get_executable_path()
                self.logger.debug(f"Setting startup registry key for: {exe_path}")
                winreg.SetValueEx(key, constants.app.APP_NAME, 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    self.logger.debug("Deleting startup registry key.")
                    winreg.DeleteValue(key, constants.app.APP_NAME)
                except FileNotFoundError:
                    pass # Key doesn't exist, which is fine
            winreg.CloseKey(key)
        except Exception as e:
            self.logger.error(f"Failed to update startup registry: {e}")
