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
        Checks if the application is configured to start with Windows.
        By default, this uses the improved 'correctness' check.
        `force_check=True` can be used to do a simple existence check.
        """
        return self._check_startup_registry(check_for_correctness=not force_check)

    def toggle_startup(self, enable: bool) -> bool:
        """
        Toggles the startup registry key on or off.
        Returns the new state (True for enabled, False for disabled).
        """
        self.logger.debug(f"Toggling startup: {enable}")
        self._set_startup_registry(enable)
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

        is_actually_enabled = self.is_startup_enabled()
        self.logger.debug(f"Syncing startup task. Config says: {should_be_enabled}, Registry says: {is_actually_enabled}")

        if should_be_enabled and not is_actually_enabled:
            self.logger.debug("Re-enabling startup task to match configuration.")
            self._set_startup_registry(True)
        elif not should_be_enabled and is_actually_enabled:
            self.logger.debug("Disabling startup task to match configuration.")
            self._set_startup_registry(False)

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
