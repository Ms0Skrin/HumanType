"""Running with admin rights, so admin windows (a terminal "as administrator", installers) are
reached too: Windows gives a non-elevated low-level hook no keys typed there and blocks its
SendInput, so without it the app goes silent in exactly those windows.

The rights come from a Task Scheduler task with "highest privileges": made once (one UAC prompt),
it starts the app elevated at logon and on every later launch without asking again.
"""

import ctypes
import logging
import os
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from xml.sax.saxutils import escape

from .paths import app_dir

log = logging.getLogger(__name__)

TASK_NAME = "HumanType"
CREATE_NO_WINDOW = 0x08000000

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32.ShellExecuteW.restype = wintypes.HINSTANCE
shell32.ShellExecuteW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  wintypes.LPCWSTR, ctypes.c_int)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
advapi32.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD))
TOKEN_QUERY = 0x0008
TokenElevationType, TokenElevation = 18, 20
ELEVATION_LIMITED = 3  # an admin's filtered token: elevating needs only a "Yes" in UAC


def _token_info(pid: int | None, kind: int) -> int | None:
    """TokenElevation / TokenElevationType of a process (ours when pid is None); None when it cannot be read."""
    own = pid is None
    handle = kernel32.GetCurrentProcess() if own else kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        value, size = wintypes.DWORD(), wintypes.DWORD()
        try:
            if not advapi32.GetTokenInformation(token, kind, ctypes.byref(value), 4, ctypes.byref(size)):
                return None
            return value.value
        finally:
            kernel32.CloseHandle(token)
    finally:
        if not own:
            kernel32.CloseHandle(handle)


def is_admin() -> bool:
    return bool(_token_info(None, TokenElevation))


def can_elevate() -> bool:
    """An administrator running with UAC's filtered token: one "Yes" gives full rights."""
    return _token_info(None, TokenElevationType) == ELEVATION_LIMITED


def process_elevated(pid: int) -> bool:
    """Whether a process runs with admin rights; unknown counts as yes (it hid its token from us)."""
    value = _token_info(pid, TokenElevation)
    return True if value is None else bool(value)


def launch_parts() -> tuple[str, str]:
    """The program and its arguments that start this app."""
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw), f'"{app_dir() / "run.pyw"}"'


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)


def task_installed() -> bool:
    try:
        return _schtasks("/query", "/tn", TASK_NAME).returncode == 0
    except Exception:
        return False


def task_xml(logon: bool) -> str:
    program, arguments = launch_parts()
    user = escape(f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>HumanType: layout switching, admin windows included</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>{str(logon).lower()}</Enabled><UserId>{user}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(program)}</Command><Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{escape(str(app_dir()))}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def install_task(logon: bool) -> bool:
    """Makes (or remakes) the task; needs admin rights. Priority 4 = normal: the scheduler's own
    default (7) would starve the keyboard hook."""
    path = Path(tempfile.gettempdir()) / "humantype-task.xml"
    try:
        path.write_text(task_xml(logon), encoding="utf-16")
        result = _schtasks("/create", "/tn", TASK_NAME, "/xml", str(path), "/f")
        if result.returncode != 0:
            log.error("task create failed: %s", result.stderr.decode("cp866", "replace").strip())
            return False
        return True
    except Exception:
        log.exception("task create failed")
        return False
    finally:
        path.unlink(missing_ok=True)


def remove_task() -> None:
    try:
        _schtasks("/delete", "/tn", TASK_NAME, "/f")
    except Exception:
        log.exception("task delete failed")


def run_task() -> bool:
    try:
        return _schtasks("/run", "/tn", TASK_NAME).returncode == 0
    except Exception:
        return False


def relaunch_as_admin(arguments: str) -> bool:
    """Starts this app again with admin rights (the UAC prompt); False when it was refused."""
    program, base = launch_parts()
    result = shell32.ShellExecuteW(None, "runas", program, f"{base} {arguments}".strip(), str(app_dir()), 1)
    return (result or 0) > 32
