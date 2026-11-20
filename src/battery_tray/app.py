"""程序入口：托盘常驻 + 可选的任务栏内嵌窗口。

线程模型
--------
主线程：Win32 消息循环（托盘、任务栏窗口、定时器、PnP 事件）
工作线程：枚举 HID + 读电量（一次一个，永不排队）
两者之间只传不可变的 BatterySnapshot 列表，通过 queue + PostMessage 交接。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import queue
import sys
import threading
import time
import traceback
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("这个工具只支持 Windows 10/11")

from .core import config as cfgmod
from .core.model import BatteryReading, BatterySnapshot, DeviceInfo, ReadingState
from .source.base import ProviderChain
from .source.hub_bridge import HubBridgeProvider
from .source.mchose_a7v3 import MchoseHidProvider
from .source.standard_hid import StandardHidProvider
from .present.icon import GlyphRenderer
from .present.taskbar import TaskbarBand
from .present.tray import TrayIcon
from .ui.menu import MenuItem, popup as popup_menu
from .ui import strings
from .ui.theme import LOW_THRESHOLD, color_for

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- 常量 -----------------------------------------------------------------

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_TIMER = 0x0113
WM_DEVICECHANGE = 0x0219
WM_POWERBROADCAST = 0x0218
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205

WM_APP = 0x8000
WM_APP_REFRESH = WM_APP + 10
WM_APP_READY = WM_APP + 11
WM_APP_PING = WM_APP + 20          # 第二个实例唤起第一个实例的菜单

TIMER_POLL = 1
PBT_APMRESUMEAUTOMATIC = 0x0012
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004

MENU_REFRESH = 1001
MENU_QUIT = 1099
MENU_MODE_TRAY = 1101
MENU_MODE_TASKBAR = 1102
MENU_INTERVAL_BASE = 2000          # 2000 + 秒数 = 命令 id
INTERVAL_CHOICES = (10, 30, 60, 120, 300, 600)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
user32.SetTimer.restype = wintypes.UINT
user32.SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

# 64 位下句柄是 8 字节，不声明 restype 会被截断成 int —— 这是 ctypes 最经典的崩溃原因
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


# --- 崩溃日志 -------------------------------------------------------------
# pythonw.exe 没有控制台，异常一抛进程就静默消失，托盘还会留下一个「幽灵图标」。
# 所以这里把所有未捕获异常写进文件，至少事后能查。


def crash_log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "MchoseBattery" / "crash.log"


def _write_crash(text: str) -> None:
    try:
        path = crash_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} ===\n{text}")
    except OSError:
        pass


def install_crash_log() -> None:
    def hook(exc_type, exc, tb):
        _write_crash("".join(traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)


# --- 工作线程 -------------------------------------------------------------


class Poller(threading.Thread):
    """定时读一次，把结果丢进队列，然后 PostMessage 通知主线程。"""

    def __init__(self, chain: ProviderChain, wakeup: int, interval: float = 60.0) -> None:
        super().__init__(name="battery-poller", daemon=True)
        self._chain = chain
        self._hwnd = wakeup
        self._interval = interval
        self._event = threading.Event()
        self._stop = threading.Event()
        self._reschedule = False        # 只是改节奏，不需要立刻轮询
        self.results: "queue.Queue[list[BatterySnapshot]]" = queue.Queue(maxsize=4)
        self.busy = False
        self._seq = 0

    def trigger(self) -> None:
        self._event.set()

    def set_interval(self, seconds: float) -> None:
        """改轮询间隔并唤醒正在 wait 的线程，否则新值要等本轮睡完才生效。"""
        self._interval = max(1.0, float(seconds))
        self._reschedule = True
        self._event.set()

    def stop(self) -> None:
        self._stop.set()
        self._event.set()

    def run(self) -> None:
        while not self._stop.is_set():
            woken = self._event.wait(timeout=self._interval)
            self._event.clear()
            if self._stop.is_set():
                break
            if woken and self._reschedule:
                self._reschedule = False
                continue                    # 重新排期，不触发额外轮询
            self.busy = True
            try:
                snapshots = self._poll_once()
                self._seq += 1
                if not self.results.full():
                    self.results.put_nowait(snapshots)
                user32.PostMessageW(self._hwnd, WM_APP_READY, 0, 0)
            finally:
                self.busy = False

    def _poll_once(self) -> list[BatterySnapshot]:
        snapshots: list[BatterySnapshot] = []
        for device in discover_targets(self._chain.providers):
            reading = self._chain.read(device)
            snapshots.append(_to_snapshot(device, reading, self._seq))
        return snapshots


def discover_targets(providers) -> list[DeviceInfo]:
    """枚举所有 HID 集合 → 按 VID:PID 分组 → 让 Provider 认领。

    这里**不按 VID 硬编码过滤**：哪个设备归哪个 Provider 管，由 `probe()` 决定。
    新增厂商时只需要写 Provider，不用回来改这个函数。

    一台设备会暴露 3–5 个集合，优先挑厂商集合（Usage Page 0xFF00–0xFFFF）。
    """
    from .win32.hid import enumerate_devices

    try:
        devices = enumerate_devices()
    except OSError:
        return []

    grouped: dict[tuple[int, int], list[DeviceInfo]] = {}
    for d in devices:
        grouped.setdefault((d.vid, d.pid), []).append(d)

    chosen: list[DeviceInfo] = []
    for group in grouped.values():
        vendor = [d for d in group if d.is_vendor_collection]
        others = [d for d in group if not d.is_vendor_collection]
        for candidate in vendor + others:
            if any(p.probe(candidate) for p in providers):
                chosen.append(candidate)
                break
    return chosen


def _to_snapshot(device: DeviceInfo, reading: BatteryReading | None, seq: int) -> BatterySnapshot:
    if reading is None:
        return BatterySnapshot(
            device=device, percent=None, charging=False,
            state=ReadingState.UNAVAILABLE, transport=None,  # type: ignore[arg-type]
            observed_at=datetime.now(timezone.utc), seq=seq,
        )
    return BatterySnapshot.from_reading(device, reading, seq)


# --- 应用 -----------------------------------------------------------------


class Application:
    def __init__(self, cfg: cfgmod.AppConfig) -> None:
        self.cfg = cfg
        self.hwnd = 0
        self._wndproc_ref = WNDPROC(self._wndproc)
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.last: dict[str, BatterySnapshot] = {}

        self.chain = ProviderChain([
            MchoseHidProvider(),
            StandardHidProvider(),
            HubBridgeProvider(),
        ])
        for p in self.chain.providers:
            setattr(p, "verbose", cfg.debug)

        self.tray = TrayIcon(0)
        self.glyph = GlyphRenderer()
        self.band: TaskbarBand | None = None      # 由 _apply_mode() 按配置创建
        self.poller = Poller(self.chain, 0, cfg.poll_seconds)

    # --- 启动 -------------------------------------------------------------

    def run(self) -> int:
        if not self._acquire_single_instance():
            # 已有实例：把它唤出来（弹菜单），别让用户以为程序没反应
            hwnd = user32.FindWindowW("MchoseBatteryHost", None)
            if hwnd:
                user32.PostMessageW(hwnd, WM_APP_PING, 0, 0)
                print("已经有一个实例在运行，已尝试唤起它的菜单。", file=sys.stderr)
            else:
                print("已经有一个实例在运行（找不到它的窗口）。", file=sys.stderr)
            return 1

        self._create_host_window()
        self.tray.hwnd = self.hwnd
        self.poller._hwnd = self.hwnd

        icon = self.glyph.render(None)
        try:
            self.tray.add(icon, "正在读取电量…")
        except OSError as exc:
            # 加不上托盘不算致命：任务栏模式还能用，至少别让进程直接死掉
            print(f"[app] 托盘图标创建失败：{exc}")
            _write_crash(f"托盘图标创建失败：{exc}")

        self._apply_mode()          # 按配置决定要不要建任务栏窗口

        user32.SetTimer(self.hwnd, TIMER_POLL, self.cfg.poll_seconds * 1000, None)
        self.poller.start()
        self.poller.trigger()          # 启动立刻读一次

        try:
            self._message_loop()
        finally:
            self.shutdown()
        return 0

    def _acquire_single_instance(self) -> bool:
        handle = kernel32.CreateMutexW(None, False, "Local\\MchoseBatteryTray")
        already = ctypes.get_last_error() == 183      # ERROR_ALREADY_EXISTS
        self._mutex = handle
        return not already

    def _create_host_window(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = _WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = "MchoseBatteryHost"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            0, "MchoseBatteryHost", "", 0, 0, 0, 0, 0, None, None, hinst, None
        )
        if not self.hwnd:
            raise OSError("创建宿主窗口失败")

    def _message_loop(self) -> None:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # --- 窗口过程 ---------------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        """窗口过程外壳：兜住所有异常。

        ctypes 回调里抛出的异常会被 Windows 静默吞掉，只打印一行
        「Exception ignored …」，界面上表现成「点了没反应」，极难排查。
        这里统一接住，打到控制台 + crash.log，然后交给 DefWindowProc。
        """
        try:
            return self._handle_message(hwnd, msg, wparam, lparam)
        except Exception as exc:                       # noqa: BLE001
            print(f"[app] wndproc 出错 msg=0x{msg:04x}: {type(exc).__name__}: {exc}")
            text = traceback.format_exc()
            print(text, file=sys.stderr)
            _write_crash(f"wndproc msg=0x{msg:04x}\n{text}")
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _handle_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_READY:
            self._drain_results()
            return 0
        if msg == WM_APP_REFRESH:
            self.poller.trigger()
            return 0
        if msg == WM_APP_PING:
            self._show_menu()
            return 0
        if msg == WM_TIMER and wparam == TIMER_POLL:
            if not self.poller.busy:
                self.poller.trigger()
            return 0
        if msg == WM_DEVICECHANGE:
            if wparam in (DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE):
                self.poller.trigger()
            return 0
        if msg == WM_POWERBROADCAST and wparam == PBT_APMRESUMEAUTOMATIC:
            self.poller.trigger()
            return 0
        if msg == getattr(self, "_taskbar_created", 0):
            self._on_taskbar_created()
            return 0
        if msg == 0x8000 + 1:            # 托盘回调
            self._on_tray_event(wparam, lparam)
            return 0
        if msg == WM_CLOSE:
            self.shutdown()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)    # ← 少了这行就是僵尸进程
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # --- 数据 -------------------------------------------------------------

    def _drain_results(self) -> None:
        latest: list[BatterySnapshot] = []
        while not self.poller.results.empty():
            latest = self.poller.results.get_nowait()
        for snap in latest:
            self.last[snap.device.key] = snap
        if latest:
            self._render()

    def _render(self) -> None:
        snapshots = list(self.last.values())
        if not snapshots:
            self._apply(None, strings.TRAY_TIP_NO_DEVICE)
            return
        primary = min(
            snapshots,
            key=lambda s: (s.percent is None, s.percent if s.percent is not None else 255),
        )
        self._apply(primary, strings.format_tooltip(primary, len(snapshots)))

    def _apply(self, snap: BatterySnapshot | None, tooltip: str) -> None:
        percent = snap.percent if snap else None
        charging = snap.charging if snap else False
        low = self.cfg.display.low_threshold or LOW_THRESHOLD
        icon = self.glyph.render(percent, charging, low)
        self.tray.update(icon, tooltip)

        if self.band is not None:
            # 只用纯文本：Taskbar 的 DrawTextW 走 GDI，emoji 经常会渲染成方框
            text = snap.display_text() if snap else "--"
            self.band.set_content(text, color_for(percent, low), percent)

    # --- 事件 -------------------------------------------------------------

    def _on_taskbar_created(self) -> None:
        """Explorer 重启：任务栏窗口全没了，托盘图标也要重加。"""
        if self.band is not None:
            try:
                self.band.destroy()      # 先销毁旧窗口，否则会漏一个句柄
                self.band.create()
            except OSError:
                pass
        self.tray.reset_after_explorer_restart()
        self.tray.add(self.glyph.render(None), "正在读取电量…")

    def _on_tray_event(self, wparam: int, lparam: int) -> None:
        event = lparam & 0xFFFF
        if event in (WM_LBUTTONUP,):
            self.poller.trigger()
        elif event in (WM_RBUTTONUP, 0x007B):
            # NOTIFYICON_VERSION_4：wParam 低字/高字是图标的屏幕坐标
            anchor = (wparam & 0xFFFF, (wparam >> 16) & 0xFFFF)
            self._show_menu(anchor)

    def _show_menu(self, anchor: tuple[int, int] | None = None) -> None:
        mode = self.cfg.display.mode
        items = [
            MenuItem(id=MENU_REFRESH, text=strings.MENU_REFRESH),
            MenuItem(separator=True),
            MenuItem(text=strings.MENU_MODE, submenu=[
                MenuItem(id=MENU_MODE_TRAY, text=strings.MENU_MODE_TRAY,
                         checked=mode != "taskbar"),
                MenuItem(id=MENU_MODE_TASKBAR, text=strings.MENU_MODE_TASKBAR,
                         checked=mode == "taskbar"),
            ]),
            MenuItem(text=strings.MENU_INTERVAL, submenu=[
                MenuItem(
                    id=MENU_INTERVAL_BASE + sec,
                    text=strings.format_interval(sec),
                    checked=(self.cfg.poll_seconds == sec),
                )
                for sec in INTERVAL_CHOICES
            ]),
            MenuItem(separator=True),
            MenuItem(id=MENU_QUIT, text=strings.MENU_QUIT),
        ]
        choice = popup_menu(self.hwnd, items, anchor)

        if choice == MENU_REFRESH:
            self.poller.trigger()
        elif choice == MENU_MODE_TRAY:
            self._set_mode("tray")
        elif choice == MENU_MODE_TASKBAR:
            self._set_mode("taskbar")
        elif MENU_INTERVAL_BASE < choice < MENU_INTERVAL_BASE + 10000:
            self._set_interval(choice - MENU_INTERVAL_BASE)
        elif choice == MENU_QUIT:
            self.shutdown()

    def _set_mode(self, mode: str) -> None:
        if mode == self.cfg.display.mode:
            return
        self.cfg.display.mode = mode
        cfgmod.save(self.cfg)
        self._apply_mode()

    def _apply_mode(self) -> None:
        """让配置里的显示方式立刻生效：需要就建窗口，不需要就销毁。"""
        want_taskbar = (self.cfg.display.mode == "taskbar")

        if want_taskbar and self.band is None:
            band = TaskbarBand()
            band.verbose = self.cfg.debug
            band.layered = self.cfg.display.layered
            try:
                band.create()
            except OSError as exc:
                print(f"[app] 任务栏窗口创建失败，退回托盘模式：{exc}")
                _write_crash(f"任务栏窗口创建失败：{exc}")
                return
            self.band = band
            self._render()                 # 立刻填上当前电量，别停在 "--"

        elif not want_taskbar and self.band is not None:
            self.band.destroy()
            self.band = None

    def _set_interval(self, seconds: int) -> None:
        seconds = max(10, min(3600, int(seconds)))
        if seconds == self.cfg.poll_seconds:
            return
        self.cfg.poll_seconds = seconds
        cfgmod.save(self.cfg)

        user32.KillTimer(self.hwnd, TIMER_POLL)
        user32.SetTimer(self.hwnd, TIMER_POLL, seconds * 1000, None)
        self.poller.set_interval(seconds)   # 唤醒线程，马上按新间隔跑
        print(f"[app] 刷新间隔改为 {seconds} 秒")

    # --- 退出 -------------------------------------------------------------

    def shutdown(self) -> None:
        self.poller.stop()
        self.chain.close()
        try:
            self.tray.remove()
        finally:
            if self.band is not None:
                self.band.destroy()
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = 0


class _WNDCLASSW(ctypes.Structure):
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


# --- 命令行 ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    install_crash_log()

    parser = argparse.ArgumentParser(prog="battery_tray", description="2.4G 外设电量托盘")
    parser.add_argument("--once", action="store_true", help="读一次就退出")
    parser.add_argument("--list", action="store_true", help="列出所有 HID 集合")
    parser.add_argument("--taskbar", action="store_true", help="启动时用任务栏内嵌模式")
    parser.add_argument("--no-layered", action="store_true", help="任务栏窗口不用分层（退回不透明绘制）")
    parser.add_argument("--debug", action="store_true", help="打印每一帧十六进制")
    parser.add_argument("--interval", type=int, default=None, help="刷新间隔（秒）")
    args = parser.parse_args(argv)

    cfg = cfgmod.load()
    if args.debug:
        cfg.debug = True
    if args.taskbar:
        cfg.display.mode = "taskbar"
    if args.no_layered:
        cfg.display.layered = False
    if args.interval:
        cfg.poll_seconds = max(cfgmod.MIN_POLL_SECONDS, min(cfgmod.MAX_POLL_SECONDS, args.interval))

    if args.list:
        _print_devices()
        return 0

    if args.once:
        return _run_once(cfg)

    try:
        return Application(cfg).run()
    except Exception:                                   # noqa: BLE001
        text = traceback.format_exc()
        print(text, file=sys.stderr)
        _write_crash(text)
        return 2


def _print_devices() -> None:
    from .win32.hid import enumerate_devices

    try:
        devices = enumerate_devices()
    except OSError as exc:
        print(f"枚举失败：{exc}", file=sys.stderr)
        return
    print(f"{'VID:PID':>10}  {'UsagePage':>9}  {'Usage':>5}  {'MI':>3}  产品")
    for d in devices:
        print(
            f"{d.vid:04X}:{d.pid:04X}  {d.usage_page:#09x}  {d.usage:#05x}  "
            f"{d.interface_number:>3}  {d.product}"
        )


def _run_once(cfg: cfgmod.AppConfig) -> int:
    chain = ProviderChain([MchoseHidProvider(), StandardHidProvider(), HubBridgeProvider()])
    for p in chain.providers:
        setattr(p, "verbose", cfg.debug)
    targets = discover_targets(chain.providers)
    if not targets:
        print("没有找到 MCHOSE 设备（VID 3837 / 5253）。")
        return 2
    ok = 0
    for device in targets:
        reading = chain.read(device)
        if reading is None:
            print(f"{device.short_name}: --（读不到，见 docs/adapt-new-vendor.md 的排查表）")
        else:
            ok += 1
            print(f"{device.short_name}: {reading.percent}%  [{reading.transport.value}] {reading.note}")
    chain.close()
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
