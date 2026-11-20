"""通知区图标（Shell_NotifyIcon）。

只负责图标本身的生命周期：添加、更新、删除、Explorer 重启后重加。
菜单在 ui/menu.py，动作解释在 app.py。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("battery_tray.present.tray 仅支持 Windows")

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_SHOWTIP = 0x80

NOTIFYICON_VERSION_4 = 4

WM_APP = 0x8000
WM_APP_TRAY = WM_APP + 1

NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401
WM_CONTEXTMENU = 0x007B


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]


class TrayIcon:
    def __init__(self, hwnd: int, uid: int = 1) -> None:
        self.hwnd = hwnd
        self.uid = uid
        self._added = False
        self._icon = 0

    # --- 图标 -------------------------------------------------------------

    def add(self, icon: int, tooltip: str, callback_message: int = WM_APP_TRAY) -> None:
        nid = self._base()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.uCallbackMessage = callback_message
        nid.hIcon = icon
        nid.szTip = tooltip[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise OSError("Shell_NotifyIconW(NIM_ADD) 失败")
        nid.uVersion = NOTIFYICON_VERSION_4
        shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
        self._added = True
        self._icon = icon

    def update(self, icon: int, tooltip: str) -> None:
        if not self._added:
            self.add(icon, tooltip)
            return
        nid = self._base()
        nid.uFlags = NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.hIcon = icon
        nid.szTip = tooltip[:127]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        if self._icon and self._icon != icon:
            gdi32.DeleteObject(self._icon)      # 旧图标必须销毁，否则 GDI 句柄泄漏
        self._icon = icon

    def remove(self) -> None:
        if not self._added:
            return
        nid = self._base()
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self._added = False
        if self._icon:
            gdi32.DeleteObject(self._icon)
            self._icon = 0

    @property
    def is_added(self) -> bool:
        return self._added

    def reset_after_explorer_restart(self) -> None:
        """Explorer 重启后任务栏会把所有图标丢掉，必须重新 NIM_ADD。

        NIM_MODIFY 对已经被销毁的图标无效，所以这里只清状态、不动句柄。
        """
        self._added = False
        self._icon = 0

    def _base(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = self.uid
        return nid
