"""配置：%APPDATA%\\MchoseBattery\\config.json

原则：
  * 解析失败不要抛异常把程序搞死——备份坏文件，用默认值继续。
  * 写入用「临时文件 + os.replace」，断电不会写出半个 JSON。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "MchoseBattery"
CONFIG_VERSION = 1

DEFAULT_POLL_SECONDS = 60
MIN_POLL_SECONDS = 15
MAX_POLL_SECONDS = 1800


def config_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME}"


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class DeviceOverride:
    """按 VID:PID 记住「上次是怎么读通的」，下次优先用同一条路。"""

    vid: str
    pid: str
    alias: str = ""
    protocol: str = ""            # "mchose-feature-0x12" / "mchose-feature-0x11" / "g3-output" / "standard"
    last_percent: int | None = None
    last_seen: str = ""           # ISO8601
    hidden: bool = False


@dataclass
class DisplayConfig:
    mode: str = "tray"            # "tray" | "taskbar"
    show_percent: bool = True
    low_threshold: int = 20
    colorize: bool = True
    layered: bool = True          # 任务栏窗口是否用分层窗口（逐像素透明）


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    poll_seconds: int = DEFAULT_POLL_SECONDS
    display: DisplayConfig = field(default_factory=DisplayConfig)
    devices: list[DeviceOverride] = field(default_factory=list)
    provider_order: list[str] = field(
        default_factory=lambda: ["mchose-hid", "standard-hid", "hub-bridge", "manual"]
    )
    debug: bool = False

    # --- 查询 -------------------------------------------------------------

    def override_for(self, vid: int, pid: int) -> DeviceOverride | None:
        key = (f"{vid:04x}", f"{pid:04x}")
        for d in self.devices:
            if (d.vid.lower(), d.pid.lower()) == key:
                return d
        return None

    def upsert_override(self, ov: DeviceOverride) -> None:
        for i, d in enumerate(self.devices):
            if (d.vid.lower(), d.pid.lower()) == (ov.vid.lower(), ov.pid.lower()):
                self.devices[i] = ov
                return
        self.devices.append(ov)


def load() -> AppConfig:
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppConfig()
    except (OSError, json.JSONDecodeError) as exc:
        # 坏配置：挪走，别让用户卡在启动不了
        try:
            path.replace(path.with_suffix(".json.broken"))
        except OSError:
            pass
        print(f"[config] 读取失败，已重置：{exc}", file=sys.stderr)
        return AppConfig()

    cfg = AppConfig()
    try:
        cfg.version = int(raw.get("version", CONFIG_VERSION))
        cfg.poll_seconds = _clamp(
            int(raw.get("poll_seconds", DEFAULT_POLL_SECONDS)),
            MIN_POLL_SECONDS, MAX_POLL_SECONDS,
        )
        display = raw.get("display") or {}
        cfg.display = DisplayConfig(
            mode=display.get("mode", "tray"),
            show_percent=bool(display.get("show_percent", True)),
            low_threshold=int(display.get("low_threshold", 20)),
            colorize=bool(display.get("colorize", True)),
            layered=bool(display.get("layered", True)),
        )
        cfg.devices = [DeviceOverride(**d) for d in raw.get("devices", []) if isinstance(d, dict)]
        order = raw.get("provider_order")
        if isinstance(order, list) and order:
            cfg.provider_order = [str(x) for x in order]
        # debug 是命令行开关，不落盘：否则某次 --debug 之后会被菜单的 save()
        # 写进配置，导致之后每次启动都刷日志
        cfg.debug = False
    except (TypeError, ValueError) as exc:
        print(f"[config] 字段非法，退回默认值：{exc}", file=sys.stderr)
        return AppConfig()
    return cfg


def save(cfg: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    data = asdict(cfg)
    data.pop("debug", None)          # 运行时开关，不写进文件
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)   # 原子替换


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))
