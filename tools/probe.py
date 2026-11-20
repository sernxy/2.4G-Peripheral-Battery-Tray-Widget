"""独立探测器：不加载 UI，只回答五个问题。

    1. 这台机器上到底有哪些 HID 集合？
    2. 哪个集合是 MCHOSE 的厂商集合（Usage Page 0xFF01 / 0xFF0B）？
    3. 发一帧特征报告查询，它回什么？失败的话错误码是多少？
    4. 换成 G3 族（Output Report + Input Report）会不会应答？
    5. 接收器会不会主动推输入报告？（用来判断鼠标是不是醒着）

用法（在项目根目录下）
----
    python src/tools/probe.py --list
    python src/tools/probe.py --dump 3837:1018                 # 特征报告，只读
    python src/tools/probe.py --dump 3837:1018 --report 0x11   # 只试短通道
    python src/tools/probe.py --dump 3837:1018 --cmd 0x03      # 换只读命令试探
    python src/tools/probe.py --g3 3837:1018                   # G3 族：发 Output 听 Input
    python src/tools/probe.py --descriptor 3837:1018           # 报告描述符（部分设备不放行）
    python src/tools/probe.py --scan-ids 3837:1018             # 探测哪些 Report ID 存在
    python src/tools/probe.py --listen 8                       # 听 8 秒输入报告

--cmd 只允许填**只读**命令：
    0x06  读状态（VID/PID/固件/电量/充电）  ← 默认
    0x03  读设备信息（bond/VID/PID/连接状态）
    0x04  读固件版本

⚠️ 绝对不要改成 0x02 / 0x40 / 0x42 / 0x58 / 0x67 —— 那些是写命令，会改配置。

关于长度（实测 A7 V3 Ultra+ 3837:1018 得到）
--------------------------------------------
    caps: feature=64 input=64 output=64
`FeatureReportByteLength` **包含 Report ID 字节**，所以：
    * 缓冲区总长 = caps.feature_len（这里是 64），不是「载荷 64 + 1 = 65」
    * 这个集合不接受 Report ID 0x12（SetFeature 返回 err=87），只接受 0x11
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

# 本文件在 <项目根>/src/tools/probe.py：
#   parents[0]=tools  parents[1]=仓库根
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from battery_tray.source.mchose_a7v3 import (  # noqa: E402
    CMD_STATUS,
    build_feature_request,
    build_g3_query,
    build_output_query,
    describe_output_status,
    parse_g3_response,
    parse_output_status,
    parse_status_response,
)
from battery_tray.win32.hid import HidDevice, enumerate_devices  # noqa: E402

#: 同一个请求要发几次。接收器是「先写入、稍后回填」，一次基本拿不到。
ATTEMPTS = 3
#: 每次写完之后等多久再读（秒）。与 source/mchose_a7v3.py 的 ATTEMPT_GAP 保持一致。
ATTEMPT_GAP = 0.2
#: --g3 模式监听输入报告的总时长（秒）
G3_LISTEN_SECONDS = 1.5

#: 允许的诊断命令，全部只读
READONLY_CMDS = {0x06: "读状态", 0x03: "读设备信息", 0x04: "读固件版本"}


def hexdump(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def describe_error(err: int) -> str:
    try:
        return ctypes.FormatError(err)
    except (ValueError, OSError):
        return "unknown"


def candidates(caps) -> list[tuple[int, int]]:
    """返回 [(report_id, 缓冲区总长)]，按「最可能成功」排序。

    Windows 的 FeatureReportByteLength 是**含 Report ID 的总长**，
    所以这里统一用「总长」，不再区分载荷。
    """
    out: list[tuple[int, int]] = []
    if caps is not None and caps.feature_len:
        out.append((0x12, caps.feature_len))
        out.append((0x11, caps.feature_len))
    out.append((0x12, 65))      # 公开资料里的「64 字节载荷 + 1」
    out.append((0x11, 21))      # 公开资料里的「20 字节载荷 + 1」

    seen: set[tuple[int, int]] = set()
    uniq: list[tuple[int, int]] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def cmd_list() -> int:
    devices = enumerate_devices()
    if not devices:
        print("一个 HID 集合都没枚举到——检查权限或驱动。")
        return 1
    print(f"{'VID:PID':>9}  {'UsagePage':>9}  {'Usage':>6}  MI  产品")
    for d in devices:
        vendor = "  ← 厂商集合" if d.is_vendor_collection else ""
        print(
            f"{d.vid:04X}:{d.pid:04X}  {d.usage_page:#09x}  {d.usage:#06x}  "
            f"{d.interface_number:>2}  {d.product}{vendor}"
        )
    return 0


def _match(devices, spec: str):
    try:
        vid_s, pid_s = spec.split(":")
        vid, pid = int(vid_s, 16), int(pid_s, 16)
    except ValueError:
        raise SystemExit("格式应为 VID:PID，例如 3837:1018")
    return [d for d in devices if d.vid == vid and d.pid == pid]


def cmd_dump(spec: str, report_filter: int | None, cmd: int) -> int:
    devices = _match(enumerate_devices(), spec)
    if not devices:
        print(f"没有找到 {spec}")
        return 1

    for device in devices:
        print(f"\n=== {device.interface_path}")
        print(
            f"    usage_page={device.usage_page:#06x} usage={device.usage:#06x} "
            f"MI={device.interface_number}"
        )
        if not device.is_vendor_collection:
            print("    跳过：不是厂商集合（0xFF01 / 0xFF0B）")
            continue

        try:
            with HidDevice(device.interface_path) as hid:
                caps = getattr(hid, "caps", None)
                if caps is not None:
                    print(
                        f"    caps: feature={caps.feature_len} "
                        f"input={caps.input_len} output={caps.output_len}"
                    )
                else:
                    print("    caps: 拿不到（HidP_GetCaps 失败）")

                hid.flush()
                success = False
                for report_id, total_len in candidates(caps):
                    if report_filter is not None and report_id != report_filter:
                        continue

                    request = build_feature_request(report_id, cmd, total_len - 1)
                    print(
                        f"    tx report=0x{report_id:02x} cmd=0x{cmd:02x} "
                        f"len={total_len}: {hexdump(request)}"
                    )

                    for attempt in range(1, ATTEMPTS + 1):
                        if not hid.set_feature(request):
                            print(
                                f"    tx 失败 err={hid.last_error} "
                                f"({describe_error(hid.last_error)})"
                            )
                            break      # 这个通道/长度不对，换下一个候选

                        time.sleep(ATTEMPT_GAP)   # 给接收器回填的时间
                        resp = hid.get_feature(report_id, total_len)

                        if not resp:
                            print(
                                f"    rx 空 err={hid.last_error} "
                                f"({describe_error(hid.last_error)}) attempt={attempt}"
                            )
                            continue

                        print(f"    rx ({len(resp)}B): {hexdump(resp)}")
                        if len(resp) > 1 and resp[1] == (cmd ^ 0xFF):
                            print("    回声字节匹配（cmd ^ 0xFF）")

                        parsed = parse_status_response(resp)
                        if parsed is not None:
                            print(
                                f"    => 解析成功 vid={parsed.vid:04x} pid={parsed.pid:04x} "
                                f"fw={parsed.firmware} level={parsed.level}% "
                                f"charging={parsed.charging}"
                            )
                            success = True
                            break
                        print("    => 解析失败（可能不是这台设备、或协议族不同）")

                    if success:
                        break
        except OSError as exc:
            print(f"    打开失败：{exc}")

    return 0


def cmd_g3(spec: str) -> int:
    """G3 族：发一包 Output Report，然后听 Input Report。

    为什么值得试：0xFF01 集合的 caps 是 feature=64 input=64 output=64，
    但特征报告读回来全是 err=31。MCHOSE 的另一族协议正好是
    「Output 提问、Input 回答」，完全不用特征报告——和现象吻合。
    """
    devices = [
        d for d in _match(enumerate_devices(), spec) if d.is_vendor_collection
    ]
    if not devices:
        print(f"没有找到 {spec} 的厂商集合")
        return 1

    for device in devices:
        print(f"\n=== {device.interface_path}")
        print(
            f"    usage_page={device.usage_page:#06x} usage={device.usage:#06x} "
            f"MI={device.interface_number}"
        )
        try:
            with HidDevice(device.interface_path) as hid:
                if hid.caps is not None:
                    print(
                        f"    caps: feature={hid.caps.feature_len} "
                        f"input={hid.caps.input_len} output={hid.caps.output_len}"
                    )
                hid.flush()

                query = build_g3_query()          # 64 字节：55 30 A5 0B 2E 01 01 01 00 00 …
                print(f"    tx output ({len(query)}B): {hexdump(query[:16])} ...")
                if not hid.write_output(query):
                    print(
                        f"    tx 失败 err={hid.last_error} "
                        f"({describe_error(hid.last_error)})"
                    )
                    continue

                got = False
                deadline = time.monotonic() + G3_LISTEN_SECONDS
                while time.monotonic() < deadline:
                    pkt = hid.read_input(timeout_ms=250)
                    if not pkt:
                        continue
                    got = True
                    print(f"    rx ({len(pkt)}B): {hexdump(pkt[:32])}")
                    parsed = parse_g3_response(pkt)
                    if parsed is not None:
                        level, charging = parsed
                        print(f"    => 解析成功 level={level}% charging={charging}")
                        break
                    print("    （不是 G3 应答：应以 aa 30 开头）")

                if not got:
                    print(f"    {G3_LISTEN_SECONDS} 秒内没收到任何输入报告")
        except OSError as exc:
            print(f"    打开失败：{exc}")

    return 0


def parse_report_descriptor(data: bytes) -> list[tuple[int, str, int]]:
    """粗略解析 HID 报告描述符，返回 [(report_id, 类型, 位数)]。

    只关心三个全局项和三个主项：
        0x85 Report ID / 0x75 Report Size / 0x95 Report Count
        0x81 Input / 0x91 Output / 0xB1 Feature
    """
    items: list[tuple[int, str, int]] = []
    rid, size_bits, count = 0, 0, 0
    i, n = 0, len(data)
    while i < n:
        prefix = data[i]
        i += 1
        if prefix == 0xFE:                      # long item，跳过
            if i + 2 > n:
                break
            dsize = data[i]
            i += 2 + dsize
            continue
        bsize = {0: 0, 1: 1, 2: 2, 3: 4}[prefix & 0x03]
        btype = (prefix >> 2) & 0x03
        btag = (prefix >> 4) & 0x0F
        if i + bsize > n:
            break
        val = int.from_bytes(data[i:i + bsize], "little") if bsize else 0
        i += bsize

        if btype == 1:                          # Global
            if btag == 0x05:
                rid = val
            elif btag == 0x07:
                size_bits = val
            elif btag == 0x09:
                count = val
        elif btype == 0:                        # Main
            kind = {0x8: "Input", 0x9: "Output", 0xB: "Feature"}.get(btag)
            if kind is not None:
                items.append((rid, kind, size_bits * count))
    return items


def cmd_descriptor(spec: str) -> int:
    """打印报告描述符原始字节 + 解析表。这是唯一能看清「有哪些 Report ID」的办法。"""
    devices = [d for d in _match(enumerate_devices(), spec) if d.is_vendor_collection]
    if not devices:
        print(f"没有找到 {spec} 的厂商集合")
        return 1

    for device in devices:
        print(f"\n=== {device.interface_path}")
        try:
            with HidDevice(device.interface_path) as hid:
                data = hid.get_report_descriptor()
        except OSError as exc:
            print(f"    打开失败：{exc}")
            continue

        if not data:
            print(
                f"    取描述符失败 err={hid.last_error} "
                f"({describe_error(hid.last_error)})"
            )
            print("    → 这个设备不放行 IOCTL_HID_GET_REPORT_DESCRIPTOR，"
                  "改用 --scan-ids 探测有效的 Report ID")
            continue

        print(f"    描述符 {len(data)} 字节：")
        for off in range(0, len(data), 16):
            chunk = data[off:off + 16]
            print(f"      {off:04x}  {hexdump(chunk)}")

        items = parse_report_descriptor(data)
        if not items:
            print("    解析不出任何报告项（描述符可能是嵌套结构）")
            continue

        agg: dict[tuple[int, str], int] = {}
        for rid, kind, bits in items:
            agg[(rid, kind)] = agg.get((rid, kind), 0) + bits

        print(f"    {'ReportID':>9}  {'类型':<8} {'位':>6} {'字节':>5}")
        for (rid, kind), bits in sorted(agg.items()):
            print(f"    0x{rid:02x}      {kind:<8} {bits:>6} {(bits + 7) // 8:>5}")
    return 0


def cmd_scan_ids(spec: str) -> int:
    """用 HidD_GetInputReport 探测哪些 Report ID 是存在的（纯读，安全）。

    返回码含义：87 = 没有这个 ID；31 = ID 存在但设备没数据；0 = 读到数据。
    """
    devices = [d for d in _match(enumerate_devices(), spec) if d.is_vendor_collection]
    if not devices:
        print(f"没有找到 {spec} 的厂商集合")
        return 1

    for device in devices:
        print(f"\n=== {device.interface_path}")
        try:
            with HidDevice(device.interface_path) as hid:
                length = hid.caps.input_len if hid.caps else 64
                print(f"    input_len={length}，扫描 Report ID 0x00–0xFF …")
                for rid in range(0x00, 0x100):
                    data = hid.get_input_report(rid, length)
                    err = hid.last_error
                    if data is not None:
                        print(f"    0x{rid:02x}: 有数据  {hexdump(data[:24])}")
                    elif err == 87:
                        continue                  # 这个 ID 不存在，不用报
                    elif err == 31:
                        print(f"    0x{rid:02x}: 存在，但设备没有数据（err=31）")
                    else:
                        print(f"    0x{rid:02x}: err={err} ({describe_error(err)})")
        except OSError as exc:
            print(f"    打开失败：{exc}")
    return 0


def cmd_replay(spec: str) -> int:
    """回放 A7 V3 的 0x4D 查询帧，收 Input Report 并解析电量。

    这是 WebHID 里验证通过的那一套：
        发 01 01 00 00 09 00 00 08（63 字节，Report ID 0x4D）
        收 01 01 10 00 09 00 00 …，电量在 payload 下标 18
    """
    devices = [d for d in _match(enumerate_devices(), spec) if d.is_vendor_collection]
    if not devices:
        print(f"没有找到 {spec} 的厂商集合")
        return 1

    for device in devices:
        print(f"\n=== {device.interface_path}")
        try:
            with HidDevice(device.interface_path) as hid:
                if hid.caps is not None:
                    print(
                        f"    caps: feature={hid.caps.feature_len} "
                        f"input={hid.caps.input_len} output={hid.caps.output_len}"
                    )
                hid.flush()
                query = build_output_query()
                print(f"    tx 0x4d ({len(query)}B): {hexdump(query[:16])} …")

                for attempt in range(1, 4):
                    if not hid.write_output(query):
                        print(
                            f"    tx 失败 err={hid.last_error} "
                            f"({describe_error(hid.last_error)})"
                        )
                        break

                    deadline = time.monotonic() + 0.6
                    while time.monotonic() < deadline:
                        raw = hid.read_input(timeout_ms=120)
                        if not raw:
                            continue
                        print(
                            f"    rx ({len(raw)}B): {hexdump(raw[:24])} …  "
                            f"[{describe_output_status(raw)}]"
                        )
                        parsed = parse_output_status(raw)
                        if parsed is not None:
                            level, charging = parsed
                            print(f"    => 电量 {level}%  充电 {charging}")
                            return 0
                    print(f"    第 {attempt} 次没拿到状态帧，重发")
        except OSError as exc:
            print(f"    打开失败：{exc}")
    return 2


def cmd_listen(seconds: float) -> int:
    """只听不写：接收器在鼠标醒着时会周期性推 0x13/0x1D，这条最直观。"""
    targets = [
        d for d in enumerate_devices()
        if d.vid in (0x3837, 0x5253) and d.is_vendor_collection
    ]
    if not targets:
        print("没有找到 MCHOSE 厂商集合。")
        return 1

    print(f"监听 {seconds:.0f} 秒……现在动一动鼠标。")
    deadline = time.monotonic() + seconds
    total = 0
    for device in targets:
        try:
            with HidDevice(device.interface_path) as hid:
                hid.flush()
                while time.monotonic() < deadline:
                    pkt = hid.read_input(timeout_ms=250)
                    if pkt:
                        total += 1
                        print(f"    [{device.usage_page:#06x}] {len(pkt)}B  {hexdump(pkt[:32])}")
        except OSError as exc:
            print(f"    打开失败：{exc}")

    if total == 0:
        print("一条输入报告都没收到。")
        print("  → 鼠标可能睡着了、没连上，或被官方软件占着。"
              "再动几下鼠标重跑；仍然没有就退出 MCHOSE HUB Watcher 再试。")
    else:
        print(f"共收到 {total} 条输入报告。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCHOSE HID 探测器（只读）")
    parser.add_argument("--list", action="store_true", help="列出所有 HID 集合")
    parser.add_argument("--dump", metavar="VID:PID", help="对厂商集合发一帧只读特征报告查询")
    parser.add_argument("--g3", metavar="VID:PID", help="按 G3 族发 Output 报告并听 Input 报告")
    parser.add_argument("--replay", metavar="VID:PID", help="回放 0x4D 查询帧并解析电量（A7 V3）")
    parser.add_argument("--descriptor", metavar="VID:PID", help="打印 HID 报告描述符并解析")
    parser.add_argument("--scan-ids", metavar="VID:PID", help="探测哪些 Report ID 存在（纯读）")
    parser.add_argument("--listen", type=float, metavar="SECONDS", help="只听不写，收集输入报告")
    parser.add_argument(
        "--report", type=lambda s: int(s, 0), default=None,
        help="只试某个 Report ID，例如 0x11",
    )
    parser.add_argument(
        "--cmd", type=lambda s: int(s, 0), default=CMD_STATUS,
        help="只读命令字：0x06(默认) / 0x03 / 0x04",
    )
    args = parser.parse_args(argv)

    if args.cmd not in READONLY_CMDS:
        parser.error(
            f"0x{args.cmd:02x} 不是允许的诊断命令；"
            f"只允许 {', '.join(f'0x{k:02x}' for k in READONLY_CMDS)}（其余是写命令）"
        )

    if args.list:
        return cmd_list()
    if args.dump:
        return cmd_dump(args.dump.lower(), args.report, args.cmd)
    if args.g3:
        return cmd_g3(args.g3.lower())
    if args.replay:
        return cmd_replay(args.replay.lower())
    if args.descriptor:
        return cmd_descriptor(args.descriptor.lower())
    if args.scan_ids:
        return cmd_scan_ids(args.scan_ids.lower())
    if args.listen is not None:
        return cmd_listen(args.listen)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
