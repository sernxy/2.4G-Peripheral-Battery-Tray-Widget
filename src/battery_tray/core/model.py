"""数据模型：纯数据，不 import 任何本项目其它模块。

设计原则：
  * 界面只渲染 BatterySnapshot，不直接碰设备。
  * 「读不到」和「读到 0%」是两件事，用 ReadingState 区分。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ReadingState(str, Enum):
    OK = "ok"                    # 本次真的读到了
    ASLEEP = "asleep"            # 设备休眠 / 未应答，但历史值可用
    STALE = "stale"              # 用的是缓存，且已经过期
    UNAVAILABLE = "unavailable"  # 从来没有读到过


class Transport(str, Enum):
    """读数是从哪条通道拿到的。与具体厂商无关，方便新增品牌。"""

    VENDOR_HID = "vendor-hid"      # 厂商自定义 HID 协议（各品牌自己的帧）
    STANDARD_HID = "standard-hid"  # 标准 HID Battery Strength（0x06/0x20）
    HUB_BRIDGE = "hub-bridge"      # 借官方驱动的数据（UI 自动化 / 本地接口）
    MANUAL = "manual"              # 抓包标定出来的固定帧
    SIMULATED = "simulated"        # 演示用，永远不连硬件


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """一个 HID 顶层集合（不是一台物理设备——一台鼠标会有好几个集合）。"""

    vid: int
    pid: int
    product: str = ""
    serial: str = ""
    interface_path: str = ""     # \\?\hid#vid_3837&pid_100a&mi_02#...
    usage_page: int = 0
    usage: int = 0
    interface_number: int = -1

    @property
    def key(self) -> str:
        """稳定标识，用于配置记忆 / 协议缓存。"""
        return f"{self.vid:04x}:{self.pid:04x}:{self.usage_page:04x}:{self.usage:04x}"

    @property
    def is_vendor_collection(self) -> bool:
        """MCHOSE 的厂商集合在 0xFF01 / 0xFF0B。"""
        return self.usage_page in (0xFF01, 0xFF0B)

    @property
    def short_name(self) -> str:
        return self.product or f"{self.vid:04X}:{self.pid:04X}"


@dataclass(frozen=True, slots=True)
class BatteryReading:
    percent: int | None
    charging: bool = False
    state: ReadingState = ReadingState.UNAVAILABLE
    transport: Transport = Transport.SIMULATED
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: bytes | None = None          # 原始应答，--debug 时打印
    note: str = ""                    # 给 tooltip 的一句话

    @property
    def is_live(self) -> bool:
        return self.state is ReadingState.OK

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.observed_at).total_seconds()


@dataclass(frozen=True, slots=True)
class BatterySnapshot:
    """主线程渲染用的不可变快照。"""

    device: DeviceInfo
    percent: int | None
    charging: bool
    state: ReadingState
    transport: Transport | None      # None = 完全没读到，不知道是谁给的
    observed_at: datetime
    seq: int = 0

    @classmethod
    def from_reading(cls, device: DeviceInfo, r: BatteryReading, seq: int = 0) -> "BatterySnapshot":
        return cls(
            device=device,
            percent=r.percent,
            charging=r.charging,
            state=r.state,
            transport=r.transport,
            observed_at=r.observed_at,
            seq=seq,
        )

    def display_text(self, show_percent: bool = True) -> str:
        """托盘图标下面 / 任务栏里显示的字符串。"""
        if self.percent is None:
            return "--"
        if not show_percent:
            return ""
        return f"{self.percent}%"
