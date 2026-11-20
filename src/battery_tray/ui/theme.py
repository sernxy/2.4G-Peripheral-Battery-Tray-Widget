"""外观常量：颜色、阈值、字号、间距。

想换皮肤只需要改这个文件，不用动绘制代码。
"""

from __future__ import annotations


def rgb(r: int, g: int, b: int) -> int:
    """Windows 的 COLORREF 是 0x00BBGGRR，所以是 BGR 顺序。"""
    return r | (g << 8) | (b << 16)


# --- 电量配色 -------------------------------------------------------------

COLOR_OK = rgb(80, 220, 120)        # 电量充足
COLOR_WARN = rgb(240, 175, 40)      # 中等
COLOR_LOW = rgb(240, 80, 80)        # 低电量
COLOR_UNKNOWN = rgb(150, 150, 150)  # 读不到
COLOR_CHARGING = rgb(80, 190, 255)  # 充电中
COLOR_TEXT = rgb(245, 245, 245)     # 任务栏文字

#: 低于这个值变红
LOW_THRESHOLD = 20
#: 低于这个值变琥珀色
WARN_THRESHOLD = 50


def color_for(percent: int | None, low: int = LOW_THRESHOLD) -> int:
    if percent is None:
        return COLOR_UNKNOWN
    if percent <= low:
        return COLOR_LOW
    if percent < WARN_THRESHOLD:
        return COLOR_WARN
    return COLOR_OK


# --- 托盘图标 -------------------------------------------------------------

TRAY_ICON_SIZE = 16         # 逻辑像素，按 DPI 缩放
TRAY_ICON_DPI = 96


# --- 任务栏内嵌条 ---------------------------------------------------------

BAND_FONT_PX = 16           # 数字字号
BAND_ICON_W = 24            # 电池宽度
BAND_ICON_H = 14            # 电池高度
BAND_GAP = 5                # 图标与文字间距
BAND_PADDING = 8            # 左右留白
BAND_FALLBACK_BG = rgb(32, 32, 32)   # 非分层模式的兜底底色
