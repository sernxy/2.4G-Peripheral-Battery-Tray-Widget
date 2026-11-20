"""MCHOSE 无线鼠标电量 —— 两套协议，按设备自动选择。

═══════════════════════════════════════════════════════════════════════
协议 A：Output Report + Input Report（**A7 V3 Ultra+ 实测通过**）
═══════════════════════════════════════════════════════════════════════

    厂商集合：Usage Page 0xFF01，MI = 2
    Report ID：0x4D（输出、输入都是它）
    载荷长度：63 字节（不含 Report ID）；窗口上 1 + 63 = 64

    查询（63 字节，后面补 0）：
        01 01 00 00 09 00 00 08

    应答（同样 63 字节，前面会被驱动补上 0x4D）：
        01 01 10 00 09 00 00 37 38 26 40 04 00 00 00 10 02 00 4e 00 08 e4 c5 …
        下标 0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22
                                                              ↑     ↑
                                                          电量 78   充电 0

    要点：
      * 第一次读经常回 `01 01 01 00 … ff ff`（未就绪），要重发 / 再读；
      * 取到 `[2] == 0x10` 的那条才是数据；
      * 不取反，没有校验和，明文帧。

═══════════════════════════════════════════════════════════════════════
协议 B：Feature Report 0x11 / 0x12（旧款 A7 / L7 / M7 用，留作兜底）
═══════════════════════════════════════════════════════════════════════

    请求 = [ReportID] + invert(cmd + 0x00 * (len-1))
    应答 = [ReportID] + [cmd ^ 0xFF] + invert(payload)
    cmd 0x06，还原后 payload：vid u16 / pid u16 / fw u32 / flags u8 / level@9 / charging@10
    详见 docs/02-acquisition.md。

⚠️ 索引陷阱
    WebHID 的 `data` **不含** Report ID，所以电量在下标 18。
    ctypes 的 `read_input()` 返回的是 **含** Report ID 的整包，所以电量在 19。
    本文件里 parse_output_status() 内部已经统一成「按 payload 算」，调用方不用关心。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..core.model import BatteryReading, DeviceInfo, ReadingState, Transport
from .base import BatteryProvider

# --- 协议 A：Output / Input Report（A7 V3 Ultra+）------------------------

OUTPUT_REPORT = 0x4D
OUTPUT_PAYLOAD_LEN = 63
OUTPUT_QUERY_PREFIX = bytes([0x01, 0x01, 0x00, 0x00, 0x09, 0x00, 0x00, 0x08])

OUTPUT_STATUS_CMD = 0x10        # 应答 payload[2]
OUTPUT_BATTERY_OFFSET = 19      # payload 下标（不含 Report ID）；实测 3/3 稳定
OUTPUT_CHARGE_OFFSET = 20       # 实测恒为 0，充电位尚未确认，先不当真
OUTPUT_VID_OFFSET = 7           # payload[7:9] = 0x3837，用来锚定帧结构

OUTPUT_ATTEMPTS = 3
OUTPUT_READ_WINDOW = 0.6        # 每次写完之后听多久（秒）

# --- 协议 B：Feature Report（旧款）----------------------------------------

VENDOR_IDS = frozenset({0x3837, 0x5253})

REPORT_SHORT = 0x11
REPORT_LONG = 0x12
SHORT_LEN = 20
LONG_LEN = 64

CMD_STATUS = 0x06

FEATURE_CHANNELS = ((REPORT_LONG, LONG_LEN), (REPORT_SHORT, SHORT_LEN))

ATTEMPTS = 3
ATTEMPT_GAP = 0.12

PUSH_REPORT = 0x13
PUSH_SUBTYPE = 0x1D
PUSH_LEVEL_OFFSET = 5

# --- G3 族（另一套协议，目前只在探测工具里用）-----------------------------

G3_QUERY_PREFIX = bytes([0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01, 0x00, 0x00])
G3_RESP_HEAD = 0xAA
G3_RESP_CMD = 0x30
G3_LEVEL_OFFSET = 8
G3_CHARGE_OFFSET = 9


# --- 通用工具 -------------------------------------------------------------


def invert(data: bytes) -> bytes:
    """逐字节取反（只有协议 B 用）。"""
    return bytes(b ^ 0xFF for b in data)


# === 协议 A ===============================================================


def build_output_query() -> bytes:
    """构造 0x4D 的查询帧，返回 64 字节（含 Report ID）。"""
    body = OUTPUT_QUERY_PREFIX.ljust(OUTPUT_PAYLOAD_LEN, b"\x00")
    return bytes([OUTPUT_REPORT]) + body


def parse_output_status(raw: bytes | None) -> tuple[int, bool] | None:
    """解析 0x4D 的状态应答，返回 (电量, 是否充电)；不匹配返回 None。

    `raw` 是含 Report ID 的整包（ReadFile 的返回值），所以 payload = raw[1:]。

    实测帧（3/3 一致）：
        raw   4d 01 01 10 00 09 00 00 37 38 26 40 04 00 00 00 00 10 02 00 4e 00 08 e4
        idx    0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 20 21 22 23
        payload[7:9] = 37 38 → VID 0x3837
        payload[19]  = 4e    → 78%
    """
    if not raw or len(raw) < OUTPUT_BATTERY_OFFSET + 2:
        return None
    if raw[0] != OUTPUT_REPORT:
        return None

    payload = raw[1:]
    if payload[0] != 0x01 or payload[1] != 0x01:
        return None
    if payload[2] != OUTPUT_STATUS_CMD:
        return None          # 0x01 那条是「未就绪」，0x0d 是另一类事件帧

    # 用 VID 锚一下，防止别的机型/别的帧凑巧同头
    vid = int.from_bytes(payload[OUTPUT_VID_OFFSET:OUTPUT_VID_OFFSET + 2], "little")
    if vid not in VENDOR_IDS:
        return None

    level = payload[OUTPUT_BATTERY_OFFSET]
    if not (0 <= level <= 100):
        return None

    return level, payload[OUTPUT_CHARGE_OFFSET] == 1


def describe_output_status(raw: bytes) -> str:
    """调试用：把一包 0x4D 数据翻译成人话。"""
    if not raw:
        return "<空>"
    payload = raw[1:]
    if len(payload) < 3:
        return raw.hex(" ")
    kind = {0x01: "未就绪", 0x10: "状态", 0x0D: "事件", 0x13: "配置", 0x17: "配置"}.get(
        payload[2], f"0x{payload[2]:02x}"
    )
    if payload[2] == OUTPUT_STATUS_CMD and len(payload) > OUTPUT_BATTERY_OFFSET:
        return (
            f"{kind} 电量={payload[OUTPUT_BATTERY_OFFSET]}% "
            f"(充电位={payload[OUTPUT_CHARGE_OFFSET]})"
        )
    if payload[2] == 0x0D and len(payload) > 10:
        return f"{kind} 电量={payload[10]}%"
    return kind


# === 协议 B ===============================================================


def build_feature_request(report_id: int, cmd: int, length: int) -> bytes:
    """构造一个特征报告请求，返回的 bytes[0] 是 Report ID。

    >>> build_feature_request(0x12, 0x06, 4).hex(" ")
    '12 f9 ff ff ff'
    """
    body = bytes([cmd]) + bytes(length - 1)
    return bytes([report_id]) + invert(body)


@dataclass(frozen=True, slots=True)
class StatusReply:
    vid: int
    pid: int
    firmware: int
    flags: int
    level: int
    charging: bool
    raw: bytes

    @property
    def connect_mode(self) -> str:
        return "2.4G" if (self.flags >> 3) & 1 else "有线/未知"

    @property
    def link_ok(self) -> bool:
        return bool((self.flags >> 1) & 1)


def parse_status_response(resp: bytes | None) -> StatusReply | None:
    """解析 0x06 的特征报告应答。任何一处对不上都返回 None。"""
    if not resp or len(resp) < 13:
        return None
    r = bytes(resp)
    if r[0] not in (REPORT_SHORT, REPORT_LONG):
        return None
    if r[1] != (CMD_STATUS ^ 0xFF):
        return None

    payload = invert(r[2:])
    if len(payload) < 11:
        return None

    vid = int.from_bytes(payload[0:2], "little")
    if vid not in VENDOR_IDS:
        return None

    pid = int.from_bytes(payload[2:4], "little")
    firmware = int.from_bytes(payload[4:8], "little")
    flags = payload[8]
    level = payload[9]
    charge = payload[10]
    if level > 100:
        return None

    return StatusReply(
        vid=vid, pid=pid, firmware=firmware, flags=flags,
        level=level, charging=(charge == 1), raw=r,
    )


def parse_push_report(data: bytes | None) -> int | None:
    """接收器主动推的 0x13 / 0x1D 里也带电量（第 5 字节 ^ 0xFF）。"""
    if not data or len(data) <= PUSH_LEVEL_OFFSET:
        return None
    if data[0] != PUSH_REPORT or data[1] != PUSH_SUBTYPE:
        return None
    level = data[PUSH_LEVEL_OFFSET] ^ 0xFF
    return level if level <= 100 else None


# === G3 族 ================================================================


def build_g3_query() -> bytes:
    """G3 族：Output Report，Report ID 0，64 字节，无取反。"""
    return G3_QUERY_PREFIX.ljust(64, b"\x00")


def parse_g3_response(resp: bytes | None) -> tuple[int, bool] | None:
    if not resp or len(resp) <= G3_CHARGE_OFFSET:
        return None
    if resp[0] != G3_RESP_HEAD or resp[1] != G3_RESP_CMD:
        return None
    level = resp[G3_LEVEL_OFFSET]
    if not (1 <= level <= 100):
        return None
    return level, resp[G3_CHARGE_OFFSET] == 1


# === Provider =============================================================


class MchoseHidProvider(BatteryProvider):
    """先试 0x4D 输出报告（新机型），再退到特征报告（旧机型）。"""

    name = "mchose-hid"
    priority = 10

    def __init__(self, hid_factory=None) -> None:
        super().__init__()
        self._hid_factory = hid_factory
        self.verbose = False
        self._known_channel: dict[str, tuple[int, int]] = {}
        self._family: dict[str, str] = {}          # "output" / "feature"

    # --- probe ------------------------------------------------------------

    def probe(self, device: DeviceInfo) -> bool:
        if device.vid not in VENDOR_IDS:
            return False
        return device.is_vendor_collection

    # --- read -------------------------------------------------------------

    def read(self, device: DeviceInfo) -> BatteryReading | None:
        family = self._family.get(device.key)
        if family == "feature":
            reading = self._read_feature(device)
            if reading is not None:
                return reading
            self._family.pop(device.key, None)

        reading = self._read_output(device)
        if reading is not None:
            self._family[device.key] = "output"
            return reading

        reading = self._read_feature(device)
        if reading is not None:
            self._family[device.key] = "feature"
            return reading

        return self._read_push(device)

    # --- 协议 A -----------------------------------------------------------

    def _read_output(self, device: DeviceInfo) -> BatteryReading | None:
        try:
            with self._open(device) as hid:
                hid.flush()
                query = build_output_query()
                for attempt in range(1, OUTPUT_ATTEMPTS + 1):
                    if self.verbose:
                        print(f"[mchose] tx 0x4d ({len(query)}B): {query[:16].hex(' ')} …")
                    if not hid.write_output(query):
                        if self.verbose:
                            print(f"[mchose] tx 失败 err={hid.last_error}")
                        return None

                    deadline = time.monotonic() + OUTPUT_READ_WINDOW
                    while time.monotonic() < deadline:
                        raw = hid.read_input(timeout_ms=120)
                        if not raw:
                            continue
                        if self.verbose:
                            print(f"[mchose] rx: {describe_output_status(raw)}")
                        parsed = parse_output_status(raw)
                        if parsed is not None:
                            level, charging = parsed
                            return BatteryReading(
                                percent=level,
                                charging=charging,
                                state=ReadingState.OK,
                                transport=Transport.VENDOR_HID,
                                raw=raw,
                                note="output 0x4d",
                            )
                    # 这一轮只收到「未就绪」，重发
                    if self.verbose:
                        print(f"[mchose] 第 {attempt} 次没有拿到状态帧，重发")
        except OSError as exc:
            self.record_failure(str(exc))
            return None
        return None

    # --- 协议 B -----------------------------------------------------------

    def _channels(self, key: str) -> tuple[tuple[int, int], ...]:
        known = self._known_channel.get(key)
        if known is None:
            return FEATURE_CHANNELS
        return (known,) + tuple(c for c in FEATURE_CHANNELS if c != known)

    def _read_feature(self, device: DeviceInfo) -> BatteryReading | None:
        try:
            with self._open(device) as hid:
                hid.flush()
                for report_id, length in self._channels(device.key):
                    request = build_feature_request(report_id, CMD_STATUS, length)
                    for _ in range(ATTEMPTS):
                        if not hid.set_feature(request):
                            break
                        time.sleep(ATTEMPT_GAP)
                        reply = parse_status_response(hid.get_feature(report_id, length + 1))
                        if reply is not None:
                            self._known_channel[device.key] = (report_id, length)
                            return BatteryReading(
                                percent=reply.level,
                                charging=reply.charging,
                                state=ReadingState.OK,
                                transport=Transport.VENDOR_HID,
                                raw=reply.raw,
                                note=f"feature 0x{report_id:02x}",
                            )
        except OSError as exc:
            self.record_failure(str(exc))
            return None
        return None

    # --- 兜底：推送 -------------------------------------------------------

    def _read_push(self, device: DeviceInfo) -> BatteryReading | None:
        try:
            with self._open(device) as hid:
                hid.flush()
                deadline = time.monotonic() + 0.6
                while time.monotonic() < deadline:
                    level = parse_push_report(hid.read_input(timeout_ms=200))
                    if level is not None:
                        return BatteryReading(
                            percent=level, charging=False,
                            state=ReadingState.OK, transport=Transport.VENDOR_HID,
                            note="push 0x13",
                        )
        except OSError:
            return None
        return None

    # --- 句柄 -------------------------------------------------------------

    def _open(self, device: DeviceInfo):
        if self._hid_factory is not None:
            return self._hid_factory(device.interface_path)
        from ..win32.hid import HidDevice   # 延迟 import，便于离线测试
        return HidDevice(device.interface_path)
