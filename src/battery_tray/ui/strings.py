"""界面文案。想改措辞、做多语言，只需要动这里。"""

from __future__ import annotations

from ..core.model import BatterySnapshot, ReadingState

APP_NAME = "鼠标电量"

TRAY_TIP_LOADING = "正在读取电量…"
TRAY_TIP_NO_DEVICE = "没有找到设备"

MENU_REFRESH = "立即刷新"
MENU_MODE = "显示方式"
MENU_MODE_TRAY = "托盘图标"
MENU_MODE_TASKBAR = "任务栏内嵌"
MENU_INTERVAL = "刷新间隔"
MENU_QUIT = "退出"

INTERVAL_UNIT = " 秒"


def format_interval(seconds: int) -> str:
    return f"{seconds}{INTERVAL_UNIT}"


def format_tooltip(snap: BatterySnapshot, count: int = 1) -> str:
    """托盘图标的悬停提示。"""
    name = snap.device.short_name
    if snap.percent is None:
        return f"{name}：协议未知，无法读取电量"

    stamp = snap.observed_at.astimezone().strftime("%H:%M")
    extra = f"（共 {count} 台设备）" if count > 1 else ""

    if snap.state is ReadingState.ASLEEP:
        return f"{name}：{snap.percent}% · {stamp} 的数据 · 设备休眠{extra}"
    if snap.charging:
        return f"{name}：{snap.percent}% · 充电中{extra}"
    return f"{name}：{snap.percent}% · {stamp}{extra}"
