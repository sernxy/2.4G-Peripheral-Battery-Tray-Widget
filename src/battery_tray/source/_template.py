"""新厂商适配模板。

用法：复制本文件为 `source/<vendor>.py`，改三处地方，再到 `source/__init__.py`
里把它加进 `build_default_chain()`。

    1. VID_PIDS —— 你的接收器/鼠标的 VID:PID
    2. build_query() —— 构造查询帧
    3. parse_reply() —— 从应答里取电量和充电位

这三处之外不用动。传输层（win32/hid.py）、呈现层（present/）、交互层（ui/）都与厂商无关。
"""

from __future__ import annotations

import time

from ..core.model import BatteryReading, DeviceInfo, ReadingState, Transport
from .base import BatteryProvider

# =========================================================================
# ① 设备标识：怎么认出「这是我的设备」
# =========================================================================

#: 接收器和有线模式可能是不同 PID，都列上
VID_PIDS: frozenset[tuple[int, int]] = frozenset({
    (0x0000, 0x0000),        # TODO: 改成你的 VID:PID，例如 (0x3837, 0x1018)
})

#: 厂商集合的 Usage Page。绝大多数国产外设落在 0xFF00–0xFFFF
VENDOR_USAGE_PAGES = frozenset({0xFF01, 0xFF0B})

# =========================================================================
# ② 帧格式：怎么问
# =========================================================================

REPORT_ID = 0x00             # TODO: 输出报告的 Report ID
PAYLOAD_LEN = 63             # TODO: Report ID 之后的字节数
QUERY = bytes([0x00, 0x00])  # TODO: 查询帧开头，其余补 0

#: 应答里电量的位置（按 payload 算，即不含 Report ID）
BATTERY_OFFSET = 0           # TODO
CHARGING_OFFSET = 1          # TODO


def build_query() -> bytes:
    """返回含 Report ID 的整包，用于 write_output()。"""
    body = QUERY.ljust(PAYLOAD_LEN, b"\x00")
    return bytes([REPORT_ID]) + body


def parse_reply(raw: bytes | None) -> tuple[int, bool] | None:
    """从一包输入报告里取 (电量, 是否充电)；不匹配返回 None。

    `raw` 是含 Report ID 的整包（ReadFile 的返回值），所以先 raw[1:] 得到 payload。
    三重校验缺一不可：Report ID、帧头、电量范围。
    """
    if not raw or len(raw) <= max(BATTERY_OFFSET, CHARGING_OFFSET) + 1:
        return None
    if raw[0] != REPORT_ID:
        return None

    payload = raw[1:]

    # TODO: 校验帧头，例如 if payload[0] != 0x01 or payload[1] != 0x01: return None
    level = payload[BATTERY_OFFSET]
    if not (0 <= level <= 100):
        return None
    return level, payload[CHARGING_OFFSET] == 1


# =========================================================================
# ③ Provider：串起来
# =========================================================================


class TemplateProvider(BatteryProvider):
    name = "template"
    priority = 20            # 数字越小越先试

    def probe(self, device: DeviceInfo) -> bool:
        """便宜地判断这台设备归不归我管。"""
        return (
            (device.vid, device.pid) in VID_PIDS
            and device.usage_page in VENDOR_USAGE_PAGES
        )

    def read(self, device: DeviceInfo) -> BatteryReading | None:
        try:
            with self._open(device) as hid:
                hid.flush()

                # 有的设备第一次会回「未就绪」，所以重试几次
                for _ in range(3):
                    if not hid.write_output(build_query()):
                        return None

                    deadline = time.monotonic() + 0.6
                    while time.monotonic() < deadline:
                        raw = hid.read_input(timeout_ms=120)
                        if not raw:
                            continue
                        parsed = parse_reply(raw)
                        if parsed is not None:
                            level, charging = parsed
                            return BatteryReading(
                                percent=level,
                                charging=charging,
                                state=ReadingState.OK,
                                transport=Transport.VENDOR_HID,
                                raw=raw,
                                note="template",
                            )
        except OSError as exc:
            self.record_failure(str(exc))
            return None
        return None

    def _open(self, device: DeviceInfo):
        from ..win32.hid import HidDevice
        return HidDevice(device.interface_path)
