"""Check a Windows password, so the owner can sign in to the PC page with the password they already use for this PC.

Windows itself does the checking (LogonUser): the password is never stored or logged here. A PIN, fingerprint or face
sign-in is not a password and will not work; the Windows account password does (for a Microsoft account, that account's password).
"""
from __future__ import annotations

import getpass
import os


def available() -> bool:
    return os.name == "nt"


def current_user() -> tuple[str, str]:
    """(domain, user) of whoever is running the app. The domain is the PC's own name for a local account."""
    return os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ".", getpass.getuser()


def label(domain: str, user: str) -> str:
    return f"{domain}\\{user}"


def check(domain: str, user: str, password: str) -> bool:
    """True if Windows accepts this password for that account."""
    if not available() or not password:
        return False
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.LogonUserW.argtypes = [wintypes.LPWSTR, wintypes.LPWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.LogonUserW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = wintypes.HANDLE()
    LOGON32_LOGON_INTERACTIVE, LOGON32_PROVIDER_DEFAULT = 2, 0  # interactive also accepts the cached sign-in when a work network is out of reach
    ok = advapi32.LogonUserW(user, domain, password, LOGON32_LOGON_INTERACTIVE, LOGON32_PROVIDER_DEFAULT, ctypes.byref(handle))
    if ok:
        kernel32.CloseHandle(handle)
    return bool(ok)
