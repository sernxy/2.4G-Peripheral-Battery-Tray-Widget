"""兜底 Provider：标准 HID Battery Strength。

规范：HID Usage Tables
  * Usage Page 0x06 = Generic Device Controls
  * Usage      0x20 = Battery Strength（0..255 线性映射到 0..100%）

Windows 自己也用这条路给部分 HID 设备显示电量，但绝大多数 2.4G 厂商接收器
**不声明**这个字段，所以它基本只有兜底价值。

实现要点（没写，留给你）：
  1. HidD_GetPreparsedData 拿到 preparsed data
  2. HidP_GetValueCaps(HidP_Feature) 遍历所有 value cap
  3. 找 UsagePage == 0x06 && Usage == 0x20 的那一条，记下 ReportID / BitSize / LogicalMin/Max
  4. HidD_GetFeature 读该 ReportID，用 HidP_GetUsageValue 取数值
  5. percent = round(raw * 100 / logical_max)
"""

from __future__ import annotations

from ..core.model import BatteryReading, DeviceInfo, ReadingState, Transport
from .base import BatteryProvider

BATTERY_USAGE_PAGE = 0x06
BATTERY_USAGE = 0x20


class StandardHidProvider(BatteryProvider):
    name = "standard-hid"
    priority = 50

    def __init__(self, hid_factory=None) -> None:
        super().__init__()
        self._hid_factory = hid_factory
        self._caps_cache: dict[str, tuple[int, int, int] | None] = {}

    def probe(self, device: DeviceInfo) -> bool:
        # 还没实现 caps 解析，先不认领任何设备，否则会把全系统的键鼠都当成候选。
        # TODO: 用 HidP_GetValueCaps 找到 UsagePage 0x06 / Usage 0x20 后再返回 True。
        return False

    def read(self, device: DeviceInfo) -> BatteryReading | None:
        caps = self._caps_cache.get(device.key, _UNSET)
        if caps is _UNSET:
            caps = self._find_battery_cap(device)
            self._caps_cache[device.key] = caps
        if caps is None:
            return None

        report_id, length, logical_max = caps
        try:
            handle = self._open(device)
        except OSError:
            return None

        with handle as hid:
            resp = hid.get_feature(report_id, length)
        if not resp or len(resp) < 2:
            return None

        raw = resp[1]           # 单字节字段的常见情形；多字节要改成按位宽解析
        if logical_max <= 0:
            return None
        percent = round(raw * 100 / logical_max)
        if not (0 <= percent <= 100):
            return None

        return BatteryReading(
            percent=percent, charging=False,
            state=ReadingState.OK, transport=Transport.STANDARD_HID,
            raw=resp, note="usage 0x06/0x20",
        )

    # --- 待实现 -----------------------------------------------------------

    def _find_battery_cap(self, device: DeviceInfo) -> tuple[int, int, int] | None:
        """返回 (report_id, report_len, logical_max)；找不到返回 None。

        TODO: 用 HidP_GetValueCaps 遍历，命中 UsagePage 0x06 / Usage 0x20。
        """
        raise NotImplementedError("标准 HID 电量字段的 caps 解析待实现，见模块 docstring")

    def _open(self, device: DeviceInfo):
        if self._hid_factory is not None:
            return self._hid_factory(device.interface_path)
        from ..win32.hid import HidDevice
        return HidDevice(device.interface_path)


class _Unset:
    pass


_UNSET = _Unset()
