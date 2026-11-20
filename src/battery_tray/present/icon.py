"""把「87%」画成一个 HICON（不依赖 Pillow）。

思路
----
1. `CreateDIBSection` 建一张 32bpp 的位图（自带 alpha 通道）。
2. 用 GDI 画电池外框、电量条、数字。
3. GDI 不写 alpha，所以画完要**手动扫一遍像素**：把「背景色」的像素 alpha 置 0。
4. `CreateIconIndirect` 需要彩色位图 + 1bpp 掩码位图（掩码全 0 = 不透明）。

⚠️ 未验证：这段代码是骨架，GDI 对象释放顺序、96/120/144 DPI 下的字号需要实机调。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("battery_tray.ui.glyph 仅支持 Windows")

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

TRANSPARENT = 1
FW_SEMIBOLD = 600
DEFAULT_CHARSET = 1
OUT_TT_PRECIS = 4
CLIP_DEFAULT_PRECIS = 0
CLEARTYPE_QUALITY = 5
DIB_RGB_COLORS = 0
BI_RGB = 0


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


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.CreateBitmap.argtypes = [
    ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetTextColor.restype = wintypes.COLORREF
gdi32.TextOutW.restype = wintypes.BOOL
gdi32.Rectangle.restype = wintypes.BOOL
user32.CreateIconIndirect.restype = wintypes.HICON
user32.DestroyIcon.restype = wintypes.BOOL
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.restype = ctypes.c_int

# ⚠️ 64 位下所有 GDI 句柄都是 8 字节。不声明 argtypes，ctypes 会把 HDC 当成
#    32 位 int 传，直接 ArgumentError: int too long to convert。
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
gdi32.Rectangle.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreatePen.restype = wintypes.HPEN
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]


def _rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


# 颜色统一放在 ui/theme.py，想换皮肤改那里就行
from ..ui.theme import (          # noqa: E402  (放在这里是为了挨着用到的常量)
    COLOR_CHARGING as CHARGE,
    COLOR_LOW as RED,
    COLOR_OK as GREEN,
    COLOR_TEXT as WHITE,
    COLOR_UNKNOWN as DIM,
    COLOR_WARN as AMBER,
    LOW_THRESHOLD,
    color_for as _theme_color_for,
)


class GlyphRenderer:
    """按 DPI 缓存字体和尺寸，反复画图不泄漏 GDI 对象。"""

    def __init__(self, dpi: int = 96, size_px: int = 16) -> None:
        self.dpi = dpi
        self.size = max(16, int(round(size_px * dpi / 96)))
        self._font_cache: dict[int, int] = {}

    def color_for(self, percent: int | None, low: int = LOW_THRESHOLD) -> int:
        return _theme_color_for(percent, low)

    def render(self, percent: int | None, charging: bool = False, low: int = 20) -> int:
        """返回 HICON；调用方负责 DestroyIcon。"""
        n = self.size
        hdc_screen = user32.GetDC(None)
        hdc = gdi32.CreateCompatibleDC(hdc_screen)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = n
        bmi.bmiHeader.biHeight = -n            # 负数 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(hdc, hbm)
        try:
            self._paint(hdc, n, percent, charging, low)
            self._clear_background_alpha(bits, n)
            mask = gdi32.CreateBitmap(n, n, 1, 1, None)     # 全 0 = 不透明
            info = ICONINFO()
            info.fIcon = True
            info.hbmMask = mask
            info.hbmColor = hbm
            icon = user32.CreateIconIndirect(ctypes.byref(info))
            gdi32.DeleteObject(mask)
            return icon
        finally:
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc)
            user32.ReleaseDC(None, hdc_screen)

    # --- 绘制 -------------------------------------------------------------

    def _paint(self, hdc, n: int, percent: int | None, charging: bool, low: int) -> None:
        gdi32.SetBkMode(hdc, TRANSPARENT)
        color = CHARGE if charging else self.color_for(percent, low)

        # 电池外框：留 1px 边距，右侧 2px 是正极凸起
        body_w = n - 3
        body_h = int(n * 0.55)
        top = (n - body_h) // 2
        brush = gdi32.CreateSolidBrush(color)
        pen = gdi32.CreatePen(0, max(1, n // 16), color)
        old_brush = gdi32.SelectObject(hdc, brush)
        old_pen = gdi32.SelectObject(hdc, pen)
        gdi32.Rectangle(hdc, 0, top, body_w, top + body_h)
        gdi32.Rectangle(hdc, body_w, top + body_h // 3, n, top + body_h * 2 // 3)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(brush)
        gdi32.DeleteObject(pen)

        # 电量填充（简化：直接铺满，真实实现应按 percent 算宽度）
        if percent is not None and percent > 0:
            fill_w = max(1, int((body_w - 2) * percent / 100))
            fill = gdi32.CreateSolidBrush(color)
            old_fill = gdi32.SelectObject(hdc, fill)
            gdi32.Rectangle(hdc, 1, top + 1, 1 + fill_w, top + body_h - 1)
            gdi32.SelectObject(hdc, old_fill)
            gdi32.DeleteObject(fill)

        # 数字：图标太小的时候不画，交给 tooltip
        if percent is not None and n >= 24:
            self._draw_number(hdc, n, str(percent))

    def _draw_number(self, hdc, n: int, text: str) -> None:
        height = max(8, n // 3)
        font = gdi32.CreateFontW(
            -height, 0, 0, 0, FW_SEMIBOLD,
            0, 0, 0, DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY, 0, "Segoe UI",
        )
        old = gdi32.SelectObject(hdc, font)
        gdi32.SetTextColor(hdc, WHITE)
        gdi32.TextOutW(hdc, 0, 0, text, len(text))
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)

    def _clear_background_alpha(self, bits: ctypes.c_void_p, n: int) -> None:
        """把纯黑背景的像素 alpha 置 0，让托盘背景透出来。"""
        if not bits.value:
            return
        buf = (ctypes.c_ubyte * (n * n * 4)).from_address(bits.value)
        for i in range(0, len(buf), 4):
            b, g, r = buf[i], buf[i + 1], buf[i + 2]
            buf[i + 3] = 0 if (b == 0 and g == 0 and r == 0) else 255
