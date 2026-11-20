"""把「电池图标 + 百分比」贴进任务栏（分层窗口，逐像素透明）。

为什么用分层窗口
----------------
普通子窗口是不透明的，只能「采样任务栏底色再铺一遍」来假装透明。
任务栏一旦是亚克力/半透明，采样色和真实底色总有偏差，就会看到一块方框。

`WS_EX_LAYERED` + `UpdateLayeredWindow` 是标准解法：自己画一张 32bpp 位图，
带上 alpha 通道交给系统合成，背景真正透明，不再需要猜底色。

流程
----
1. `CreateDIBSection` 建 32bpp 自上而下位图，先清成全 0（全透明）
2. 用 GDI 在上面画电池和文字（GDI 不写 alpha，画在黑色底上等于「预乘」）
3. 扫一遍像素：alpha = max(r,g,b)，RGB 反预乘
4. `UpdateLayeredWindow(hwnd, ..., ULW_ALPHA)`

位置坑
------
`GetWindowRect` 返回屏幕坐标，`SetWindowPos` 对子窗口要父窗口客户区坐标，
中间必须 `ScreenToClient`，否则窗口跑到屏幕外。

字符坑
------
文字用 `GetTextExtentPoint32W` 精确量宽，不要按字符数估。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("battery_tray.ui.taskbar_band 仅支持 Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPSIBLINGS = 0x04000000
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GWL_STYLE = -16

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_SETTINGCHANGE = 0x001A
WM_DPICHANGED = 0x02E0
DT_LEFT = 0x0000
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
TRANSPARENT = 1
BI_RGB = 0
DIB_RGB_COLORS = 0
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.restype = wintypes.HWND
user32.SetParent.restype = wintypes.HWND
user32.ScreenToClient.restype = wintypes.BOOL
user32.ClientToScreen.restype = wintypes.BOOL
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.restype = wintypes.BOOL
user32.InvalidateRect.restype = wintypes.BOOL
user32.GetWindowRect.restype = wintypes.BOOL
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.restype = wintypes.BOOL
user32.GetClientRect.restype = wintypes.BOOL
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.DrawTextW.restype = ctypes.c_int
user32.DrawTextW.argtypes = [
    wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
    ctypes.POINTER(wintypes.RECT), wintypes.UINT,
]

gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.SetTextColor.restype = wintypes.COLORREF
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]

# 64 位句柄必须声明 argtypes，否则 ctypes 会把 HDC/HFONT 截成 32 位
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
]
gdi32.FillRect = user32.FillRect          # FillRect / FrameRect 在 user32，不在 gdi32
gdi32.FrameRect = user32.FrameRect
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.FrameRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
gdi32.GetTextExtentPoint32W.argtypes = [
    wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(SIZE),
]

user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.FindWindowExW.argtypes = [
    wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
]
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
    wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

# 64 位下 HINSTANCE 是 8 字节；不声明 restype 会被截断成 32 位，CreateWindowExW 必失败
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def _rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


# 颜色与尺寸统一放在 ui/theme.py，换皮肤只改那里
from ..ui.theme import (          # noqa: E402
    BAND_FALLBACK_BG,
    BAND_FONT_PX,
    BAND_GAP,
    BAND_ICON_H,
    BAND_ICON_W,
    COLOR_OK,
    COLOR_TEXT,
)


class TaskbarBand:
    CLASS_NAME = "MchoseBatteryBand"

    def __init__(self, padding: int = 8, dpi: int = 96) -> None:
        self.hwnd: int = 0
        self.padding = padding
        self.dpi = dpi
        self.text = "--"
        self.percent: int | None = None
        self.color = COLOR_OK

        scale = dpi / 96.0
        self.font_px = max(14, int(round(BAND_FONT_PX * scale)))
        self.icon_h = max(12, int(round(BAND_ICON_H * scale)))
        self.icon_w = max(20, int(round(BAND_ICON_W * scale)))
        self.gap = max(4, int(round(BAND_GAP * scale)))

        self._wndproc_ref = WNDPROC(self._wndproc)
        self._registered = False
        self.layered = True              # 失败时自动退回不透明模式
        self.verbose = False

        self._mem_dc: int = 0
        self._dib: int = 0
        self._bits: int = 0
        self._dib_w: int = 0
        self._dib_h: int = 0

    # --- 生命周期 ---------------------------------------------------------

    def create(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        if not self._registered:
            wc = WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
            wc.hInstance = hinst
            wc.lpszClassName = self.CLASS_NAME
            wc.hbrBackground = None
            if not user32.RegisterClassW(ctypes.byref(wc)):
                err = ctypes.get_last_error()
                if err != 1410:               # ERROR_CLASS_ALREADY_EXISTS
                    raise OSError(f"RegisterClassW 失败 {err}")
            self._registered = True

        # 不能直接建 WS_CHILD 且父窗口为 NULL 的窗口，CreateWindowExW 会失败。
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            self.CLASS_NAME, "", WS_POPUP,
            0, 0, 100, 24, None, None, hinst, None,
        )
        if not self.hwnd:
            raise OSError(f"CreateWindowExW 失败 err={ctypes.get_last_error()}")
        if self.verbose:
            print(f"[band] create hwnd=0x{self.hwnd:X} exstyle=0x{WS_EX_LAYERED:08X}")
        self.attach()

    def attach(self) -> None:
        tray = user32.FindWindowW("Shell_TrayWnd", None)
        if not tray:
            if self.verbose:
                print("[band] 找不到 Shell_TrayWnd，暂不挂载")
            return
        prev = user32.SetParent(self.hwnd, tray)
        if self.verbose:
            print(
                f"[band] tray=0x{tray:X} SetParent prev=0x{prev:X} "
                f"err={ctypes.get_last_error()}"
            )
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        set_long(self.hwnd, GWL_STYLE, WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS)
        self._reposition(tray)
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        if self.verbose:
            print(f"[band] 挂载完成 parent=0x{(user32.GetParent(self.hwnd) or 0):X}")

    def detach(self) -> None:
        if self.hwnd:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            user32.SetParent(self.hwnd, None)

    def destroy(self) -> None:
        self._free_dib()
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = 0

    def set_content(self, text: str, color: int | None = None, percent: int | None = None) -> None:
        self.text = text
        self.percent = percent
        if color is not None:
            self.color = color
        if self.hwnd:
            tray = user32.GetParent(self.hwnd)
            if tray:
                self._reposition(tray)
            self._render()

    # --- 度量 -------------------------------------------------------------

    def _make_font(self) -> int:
        return gdi32.CreateFontW(
            -self.font_px, 0, 0, 0, 600,
            0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI",
        )

    def _text_width(self) -> int:
        hdc = user32.GetDC(None)
        if not hdc:
            return self.font_px * len(self.text)
        try:
            font = self._make_font()
            old = gdi32.SelectObject(hdc, font)
            size = SIZE()
            gdi32.GetTextExtentPoint32W(hdc, self.text, len(self.text), ctypes.byref(size))
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(font)
            return max(1, size.cx)
        finally:
            user32.ReleaseDC(None, hdc)

    def _content_width(self) -> int:
        return self.icon_w + self.gap + self._text_width()

    # --- 布局 -------------------------------------------------------------

    def _reposition(self, tray: int) -> None:
        notify = user32.FindWindowExW(tray, None, "TrayNotifyWnd", None)
        if not notify:
            return
        rect = wintypes.RECT()
        user32.GetWindowRect(notify, ctypes.byref(rect))

        height = max(20, rect.bottom - rect.top)
        width = self._content_width() + self.padding * 2

        pt = wintypes.POINT()
        pt.x = rect.left - width - self.padding
        pt.y = rect.top
        user32.ScreenToClient(tray, ctypes.byref(pt))

        ok = user32.SetWindowPos(
            self.hwnd, None, pt.x, pt.y, width, height,
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
        if self.verbose:
            print(
                f"[band] notify=({rect.left},{rect.top},{rect.right},{rect.bottom}) "
                f"→ pos=({pt.x},{pt.y}) size=({width}x{height}) ok={bool(ok)}"
            )

    # --- 位图 -------------------------------------------------------------

    def _free_dib(self) -> None:
        if self._dib:
            gdi32.DeleteObject(self._dib)
            self._dib = 0
        if self._mem_dc:
            gdi32.DeleteDC(self._mem_dc)
            self._mem_dc = 0
        self._bits = 0
        self._dib_w = self._dib_h = 0

    def _ensure_dib(self, w: int, h: int) -> bool:
        if self._mem_dc and self._dib_w == w and self._dib_h == h:
            return True
        self._free_dib()

        screen = user32.GetDC(None)
        if not screen:
            return False
        mem = gdi32.CreateCompatibleDC(screen)
        user32.ReleaseDC(None, screen)
        if not mem:
            return False

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        dib = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not dib:
            gdi32.DeleteDC(mem)
            return False

        gdi32.SelectObject(mem, dib)
        self._mem_dc = mem
        self._dib = dib
        self._bits = bits.value or 0
        self._dib_w, self._dib_h = w, h
        return True

    # --- 绘制 -------------------------------------------------------------

    def _draw_battery(self, hdc, x: int, y: int, percent: int | None) -> None:
        w, h = self.icon_w, self.icon_h
        brush = gdi32.CreateSolidBrush(self.color)
        if not brush:
            return
        try:
            body = wintypes.RECT(x, y, x + w - 3, y + h)
            gdi32.FrameRect(hdc, ctypes.byref(body), brush)
            nub = wintypes.RECT(x + w - 3, y + h // 4, x + w, y + h * 3 // 4)
            gdi32.FillRect(hdc, ctypes.byref(nub), brush)
            if percent and percent > 0:
                inner_w = max(1, int((w - 7) * percent / 100))
                inner = wintypes.RECT(x + 2, y + 2, x + 2 + inner_w, y + h - 2)
                gdi32.FillRect(hdc, ctypes.byref(inner), brush)
        finally:
            gdi32.DeleteObject(brush)

    def _paint_into(self, hdc, w: int, h: int) -> None:
        ctypes.memset(self._bits, 0, w * h * 4)      # 全透明

        gdi32.SetBkMode(hdc, TRANSPARENT)
        gdi32.SetTextColor(hdc, COLOR_TEXT)

        icon_y = (h - self.icon_h) // 2
        self._draw_battery(hdc, self.padding, icon_y, self.percent)

        font = self._make_font()
        old = gdi32.SelectObject(hdc, font)
        text_rect = wintypes.RECT(self.padding + self.icon_w + self.gap, 0, w, h)
        user32.DrawTextW(
            hdc, self.text, -1, ctypes.byref(text_rect),
            DT_LEFT | DT_VCENTER | DT_SINGLELINE,
        )
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)

    def _apply_alpha(self, w: int, h: int) -> None:
        """GDI 画在黑色底上 = 已经预乘过；这里把 alpha 补上、RGB 反预乘。"""
        buf = (ctypes.c_ubyte * (w * h * 4)).from_address(self._bits)
        for i in range(0, len(buf), 4):
            b, g, r = buf[i], buf[i + 1], buf[i + 2]
            peak = max(r, g, b)
            if peak == 0:
                buf[i + 3] = 0
                continue
            buf[i] = min(255, b * 255 // peak)
            buf[i + 1] = min(255, g * 255 // peak)
            buf[i + 2] = min(255, r * 255 // peak)
            buf[i + 3] = peak

    def _render(self) -> None:
        if not self.hwnd:
            return
        if self.layered and self._render_layered():
            return
        # 分层失败：退回不透明重绘
        user32.InvalidateRect(self.hwnd, None, True)

    def _render_layered(self) -> bool:
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0:
            if self.verbose:
                print(f"[band] 客户区为空 ({w}x{h})，跳过绘制")
            self.layered = False         # 尺寸都不对，退回不透明路径
            return False
        if not self._ensure_dib(w, h):
            if self.verbose:
                print("[band] CreateDIBSection 失败")
            self.layered = False
            return False

        self._paint_into(self._mem_dc, w, h)
        self._apply_alpha(w, h)

        # 关键：pptDst 传 NULL，让窗口保持在 SetWindowPos 摆好的位置。
        # 子窗口的 UpdateLayeredWindow 坐标语义和顶层窗口不一致，
        # 传屏幕坐标会把窗口挪到父窗口客户区之外——表现就是「调用成功但看不见」。
        size = SIZE(w, h)
        pt_src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

        ok = user32.UpdateLayeredWindow(
            self.hwnd, None, None, ctypes.byref(size),
            self._mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA,
        )
        if not ok:
            if self.verbose:
                print(f"[band] UpdateLayeredWindow 失败 err={ctypes.get_last_error()}")
            self.layered = False
        elif self.verbose:
            wr = wintypes.RECT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(wr))
            print(
                f"[band] UpdateLayeredWindow ok {w}x{h} "
                f"窗口屏幕矩形=({wr.left},{wr.top},{wr.right},{wr.bottom})"
            )
        return bool(ok)

    # --- 窗口过程 ---------------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_PAINT:
                if self.layered:
                    # 分层窗口由 UpdateLayeredWindow 负责显示，这里只需把 PAINT 结构收掉
                    ps = PAINTSTRUCT()
                    hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                    user32.EndPaint(hwnd, ctypes.byref(ps))
                else:
                    self._on_paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg in (WM_SETTINGCHANGE, WM_DPICHANGED):
                tray = user32.GetParent(hwnd)
                if tray:
                    self._reposition(tray)
                    self._render()
                return 0
        except Exception as exc:             # noqa: BLE001 — 回调里绝不能漏
            print(f"[taskbar] wndproc 出错 msg=0x{msg:04x}: {type(exc).__name__}: {exc}")
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_paint(self, hwnd) -> None:
        """非分层时的兜底绘制（不透明，铺深灰底）。"""
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            h = rect.bottom - rect.top

            bg = gdi32.CreateSolidBrush(BAND_FALLBACK_BG)
            if bg:
                gdi32.FillRect(hdc, ctypes.byref(rect), bg)
                gdi32.DeleteObject(bg)

            gdi32.SetBkMode(hdc, TRANSPARENT)
            gdi32.SetTextColor(hdc, COLOR_TEXT)
            icon_y = (h - self.icon_h) // 2
            self._draw_battery(hdc, self.padding, icon_y, self.percent)

            font = self._make_font()
            old = gdi32.SelectObject(hdc, font)
            text_rect = wintypes.RECT(self.padding + self.icon_w + self.gap, 0, rect.right, h)
            user32.DrawTextW(
                hdc, self.text, -1, ctypes.byref(text_rect),
                DT_LEFT | DT_VCENTER | DT_SINGLELINE,
            )
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(font)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))
