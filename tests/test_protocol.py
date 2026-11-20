"""协议纯函数测试：不需要真机，不需要 Windows。

    python -m unittest discover -s tests
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from battery_tray.source.mchose_a7v3 import (  # noqa: E402
    CMD_STATUS,
    OUTPUT_REPORT,
    REPORT_LONG,
    REPORT_SHORT,
    build_feature_request,
    build_g3_query,
    build_output_query,
    invert,
    parse_g3_response,
    parse_output_status,
    parse_push_report,
    parse_status_response,
)


def make_output_reply(level: int = 78, charging: int = 0, vid: int = 0x3837) -> bytes:
    """构造一包 0x4D 状态应答（含 Report ID），布局照实测帧。"""
    payload = bytearray(63)
    payload[0] = 0x01
    payload[1] = 0x01
    payload[2] = 0x10
    payload[4] = 0x09
    payload[7:9] = vid.to_bytes(2, "little")
    payload[19] = level
    payload[20] = charging
    return bytes([OUTPUT_REPORT]) + bytes(payload)


class TestOutputFamily(unittest.TestCase):
    """A7 V3 Ultra+ 实测通过的 0x4D 输出/输入报告协议。"""

    def test_query_shape(self) -> None:
        q = build_output_query()
        self.assertEqual(len(q), 64)
        self.assertEqual(q[0], 0x4D)
        self.assertEqual(q[1:9], bytes([0x01, 0x01, 0x00, 0x00, 0x09, 0x00, 0x00, 0x08]))

    def test_parse_valid(self) -> None:
        self.assertEqual(parse_output_status(make_output_reply()), (78, False))

    def test_parse_charging(self) -> None:
        self.assertEqual(parse_output_status(make_output_reply(level=100, charging=1)), (100, True))

    def test_reject_not_ready(self) -> None:
        raw = bytearray(make_output_reply())
        raw[3] = 0x01          # payload[2] = 0x01 → 「未就绪」
        self.assertIsNone(parse_output_status(bytes(raw)))

    def test_reject_unknown_vid(self) -> None:
        self.assertIsNone(parse_output_status(make_output_reply(vid=0x1234)))

    def test_reject_out_of_range(self) -> None:
        self.assertIsNone(parse_output_status(make_output_reply(level=200)))

    def test_reject_wrong_report_id(self) -> None:
        raw = bytearray(make_output_reply())
        raw[0] = 0x11
        self.assertIsNone(parse_output_status(bytes(raw)))

    def test_reject_short(self) -> None:
        self.assertIsNone(parse_output_status(b"\x4d\x01\x01\x10"))


def make_status_reply(
    vid: int = 0x3837,
    pid: int = 0x100A,
    firmware: int = 0x00010000,
    flags: int = 0x0A,
    level: int = 87,
    charging: int = 0,
    report_id: int = REPORT_LONG,
) -> bytes:
    payload = (
        struct.pack("<HHI", vid, pid, firmware)
        + bytes([flags, level, charging])
        + bytes(20)
    )
    return bytes([report_id, CMD_STATUS ^ 0xFF]) + invert(payload)


class TestFraming(unittest.TestCase):
    def test_invert(self) -> None:
        self.assertEqual(invert(b"\x06\x00\x00"), b"\xf9\xff\xff")

    def test_short_request(self) -> None:
        req = build_feature_request(REPORT_SHORT, CMD_STATUS, 4)
        self.assertEqual(req, bytes([0x11, 0xF9, 0xFF, 0xFF, 0xFF]))

    def test_long_request_length(self) -> None:
        req = build_feature_request(REPORT_LONG, CMD_STATUS, 64)
        self.assertEqual(len(req), 65)
        self.assertEqual(req[0], 0x12)
        self.assertEqual(req[1], 0xF9)
        self.assertTrue(all(b == 0xFF for b in req[2:]))


class TestStatusParse(unittest.TestCase):
    def test_valid(self) -> None:
        reply = parse_status_response(make_status_reply())
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertEqual(reply.vid, 0x3837)
        self.assertEqual(reply.pid, 0x100A)
        self.assertEqual(reply.level, 87)
        self.assertFalse(reply.charging)

    def test_charging(self) -> None:
        reply = parse_status_response(make_status_reply(level=100, charging=1))
        assert reply is not None
        self.assertTrue(reply.charging)

    def test_unknown_vendor_rejected(self) -> None:
        # 0x1234 不在 VENDOR_IDS 里：多半是回显或脏数据
        self.assertIsNone(parse_status_response(make_status_reply(vid=0x1234)))

    def test_out_of_range_level_rejected(self) -> None:
        self.assertIsNone(parse_status_response(make_status_reply(level=200)))

    def test_echoed_request_rejected(self) -> None:
        echo = build_feature_request(REPORT_LONG, CMD_STATUS, 64)
        self.assertIsNone(parse_status_response(echo))

    def test_all_zero_rejected(self) -> None:
        self.assertIsNone(parse_status_response(bytes(65)))

    def test_short_buffer_rejected(self) -> None:
        self.assertIsNone(parse_status_response(b"\x12\xf9"))
        self.assertIsNone(parse_status_response(None))


class TestPush(unittest.TestCase):
    def test_push_level(self) -> None:
        data = bytes([0x13, 0x1D, 0x00, 0x00, 0x00, 87 ^ 0xFF])
        self.assertEqual(parse_push_report(data), 87)

    def test_push_wrong_report(self) -> None:
        self.assertIsNone(parse_push_report(bytes([0x12, 0x1D, 0, 0, 0, 0x00])))


class TestG3(unittest.TestCase):
    def test_query_shape(self) -> None:
        q = build_g3_query()
        self.assertEqual(len(q), 64)
        self.assertTrue(q.startswith(bytes([0x55, 0x30, 0xA5, 0x0B, 0x2E])))

    def test_parse(self) -> None:
        resp = bytearray(64)
        resp[0], resp[1] = 0xAA, 0x30
        resp[8], resp[9] = 42, 1
        self.assertEqual(parse_g3_response(bytes(resp)), (42, True))

    def test_parse_bad(self) -> None:
        self.assertIsNone(parse_g3_response(bytes(64)))
        self.assertIsNone(parse_g3_response(b"\xAA\x30" + bytes(20)))


if __name__ == "__main__":
    unittest.main()
