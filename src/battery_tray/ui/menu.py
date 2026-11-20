"""右键菜单：数据结构 + 弹出逻辑。

这一层只负责「菜单长什么样、被点了返回哪个 id」，不关心这些 id 代表什么动作——
动作的解释在 app.py 里。所以换皮肤、加子菜单、改文案都不用碰业务代码。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass, field

if sys.platform != "win32":
    raise ImportError("battery_tray.ui.menu 仅支持 Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)

TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
TPM_BOTTOMALIGN = 0x0020      # 锚点定在菜单底边 → 菜单向上展开
TPM_LEFTALIGN = 0x0000

MF_STRING = 0x0000
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008

user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.restype = wintypes.BOOL
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.DestroyMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, ctypes.POINTER(wintypes.RECT),
]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


@dataclass
class MenuItem:
    """一个菜单项。submenu 非空时渲染成子菜单，checked 显示打勾。"""

    id: int = 0
    text: str = ""
    disabled: bool = False
    separator: bool = False
    checked: bool = False
    submenu: list["MenuItem"] = field(default_factory=list)


def build(items: list[MenuItem], submenus: list[int]) -> int:
    """递归构建 HMENU。子菜单句柄收集到 submenus 里，供调用方统一销毁。"""
    menu = user32.CreatePopupMenu()
    if not menu:
        return 0

    for item in items:
        if item.separator:
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            continue

        if item.submenu:
            sub = build(item.submenu, submenus)
            if not sub:
                continue
            submenus.append(sub)
            flags = MF_POPUP | (MF_GRAYED if item.disabled else 0)
            user32.AppendMenuW(menu, flags, sub, item.text)
            continue

        flags = MF_STRING
        if item.disabled:
            flags |= MF_GRAYED
        if item.checked:
            flags |= MF_CHECKED
        user32.AppendMenuW(menu, flags, item.id, item.text)

    return menu


def popup(hwnd: int, items: list[MenuItem],
          anchor: tuple[int, int] | None = None) -> int:
    """弹出菜单，返回选中的命令 id（0 = 取消）。

    anchor 是图标在屏幕上的坐标（NOTIFYICON_VERSION_4 的回调里带）。
    传了就以「图标右上方」为基准弹出，没传就退回鼠标位置。
    """
    submenus: list[int] = []
    menu = build(items, submenus)
    if not menu:
        return 0

    try:
        user32.SetForegroundWindow(hwnd)      # 否则点别处菜单不消失

        flags = TPM_RETURNCMD | TPM_RIGHTBUTTON
        if anchor and (anchor[0] or anchor[1]):
            x, y = anchor
            flags |= TPM_BOTTOMALIGN | TPM_LEFTALIGN
            y -= 6
        else:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            x, y = pt.x, pt.y

        cmd = user32.TrackPopupMenu(menu, flags, x, y, 0, hwnd, None)
        user32.PostMessageW(hwnd, 0x0000, 0, 0)   # WM_NULL
        return int(cmd)
    finally:
        for sub in submenus:
            user32.DestroyMenu(sub)
        user32.DestroyMenu(menu)
