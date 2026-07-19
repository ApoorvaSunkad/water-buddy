"""Windows-specific integration, isolated behind plain functions.

Every call in here is wrapped so that a failure degrades to "feature off"
rather than crashing the app. If a future Windows build renames something, the
worst case is that the buddy appears during a fullscreen video -- annoying, not
fatal.

Isolating the platform code in one module is also what makes a macOS or Linux
port later a matter of writing a sibling module, not hunting ctypes calls
scattered through the UI.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# --- SHQueryUserNotificationState return values -----------------------------
# https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shqueryusernotificationstate
QUNS_NOT_PRESENT = 1             # screen locked / screensaver
QUNS_BUSY = 2                    # a fullscreen app is running
QUNS_RUNNING_D3D_FULL_SCREEN = 3  # fullscreen game
QUNS_PRESENTATION_MODE = 4       # presenting -- absolutely do not interrupt
QUNS_ACCEPTS_NOTIFICATIONS = 5   # the normal, good case
QUNS_QUIET_TIME = 6              # Windows Focus Assist is on
QUNS_APP = 7                     # a Windows Store app is in the foreground

_DO_NOT_DISTURB = {
    QUNS_NOT_PRESENT,
    QUNS_BUSY,
    QUNS_RUNNING_D3D_FULL_SCREEN,
    QUNS_PRESENTATION_MODE,
    QUNS_QUIET_TIME,
}


def user_is_busy() -> tuple[bool, str]:
    """Ask Windows whether this is a bad moment to show something.

    This one API call covers fullscreen video, games, screen-shared
    presentations, a locked workstation, and Focus Assist -- all the cases
    where a cartoon walking across the screen would range from irritating to
    genuinely embarrassing.

    Returns ``(busy, human_readable_reason)``.
    """
    if not IS_WINDOWS:
        return False, ""
    try:
        state = ctypes.c_int()
        # S_OK == 0
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False, ""
    except (AttributeError, OSError) as exc:
        log.debug("Fullscreen check unavailable: %s", exc)
        return False, ""

    reasons = {
        QUNS_NOT_PRESENT: "screen is locked",
        QUNS_BUSY: "a fullscreen app is running",
        QUNS_RUNNING_D3D_FULL_SCREEN: "a fullscreen game is running",
        QUNS_PRESENTATION_MODE: "presentation mode is on",
        QUNS_QUIET_TIME: "Windows Focus Assist is on",
    }
    if state.value in _DO_NOT_DISTURB:
        return True, reasons.get(state.value, "Windows says do not disturb")
    return False, ""


# --- Always-on-top re-assertion ---------------------------------------------

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def raise_to_topmost(window_id: int) -> None:
    """Force a window back to the top of the Z-order without focusing it.

    Qt's WindowStaysOnTopHint is set once when the window is created, but other
    applications can still push above it -- Remote Desktop clients and
    always-on-top utilities are the usual offenders. Re-asserting topmost each
    time we show the buddy is cheap insurance.

    SWP_NOACTIVATE is the important flag: it means "raise this window but do
    not steal keyboard focus", so the buddy can never eat a keystroke you were
    typing into another app.
    """
    if not IS_WINDOWS or not window_id:
        return
    try:
        ctypes.windll.user32.SetWindowPos(
            int(window_id), HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
    except (AttributeError, OSError) as exc:
        log.debug("Could not re-assert topmost: %s", exc)


# --- Launch on Windows startup ----------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _startup_command() -> str:
    """The command Windows should run at login.

    Frozen into a .exe, that's just the executable. Running from source we need
    ``pythonw.exe <project>\\run.py`` -- pythonw rather than python so no
    console window flashes up at every login.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    entry = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{interpreter}" "{entry}"'


def is_launch_on_startup_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, config.APP_NAME)
        return True
    except (FileNotFoundError, OSError):
        return False


def set_launch_on_startup(enabled: bool) -> bool:
    """Add or remove the HKCU Run entry. Returns True on success.

    HKCU (current user) rather than HKLM (all users) matters: HKLM needs
    administrator rights, HKCU does not. A hydration reminder has no business
    asking for an admin prompt.
    """
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, config.APP_NAME, 0, winreg.REG_SZ,
                                  _startup_command())
                log.info("Enabled launch on startup")
            else:
                try:
                    winreg.DeleteValue(key, config.APP_NAME)
                    log.info("Disabled launch on startup")
                except FileNotFoundError:
                    pass  # already absent, which is the desired end state
        return True
    except OSError as exc:
        log.error("Could not update startup registration: %s", exc)
        return False


def set_app_user_model_id() -> None:
    """Give the app its own taskbar identity.

    Without this, Windows groups our windows under "python.exe" and any
    notification we raise is attributed to Python. One line, entirely cosmetic,
    but it's the difference between looking like a real app and looking like a
    script.
    """
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"{config.APP_NAME}.Desktop.1"
        )
    except (AttributeError, OSError):
        pass
