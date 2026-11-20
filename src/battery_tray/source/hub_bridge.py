"""兜底 Provider：从官方 MCHOSE HUB Watcher 拿电量。

只在前面的 HID 路线全部失败时才用。三条子路线，按可靠性排：

  B1  本地 HTTP/WS    Watcher 若监听端口，`curl 127.0.0.1:<port>/battery` 最省事
  B2  落盘文件        %APPDATA% / %LOCALAPPDATA% 下 MCHOSE 目录里的 json / db / log
  B3  UI Automation   读窗口里那段「87%」文本（最脆，但零风险）

为什么不一上来就用它：
  * Watcher 没启动就读不到；
  * 官方软件一升级，UI 结构/文件名就变；
  * 用户可能根本不想装官方驱动。

⚠️ 未实现：三条子路线都只留了入口，需要先在目标机器上侦察（docs/02-acquisition.md）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..core.model import BatteryReading, DeviceInfo, ReadingState, Transport
from .base import BatteryProvider

#: 常见的候选路径，按优先级排
CANDIDATE_DIRS = (
    Path(os.environ.get("APPDATA", "")) / "MCHOSE",
    Path(os.environ.get("LOCALAPPDATA", "")) / "MCHOSE",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "MCHOSE HUB",
    Path(os.environ.get("ProgramData", "")) / "MCHOSE",
)

_PERCENT_RE = re.compile(r"(\d{1,3})\s*%")


class HubBridgeProvider(BatteryProvider):
    name = "hub-bridge"
    priority = 80

    def __init__(self, endpoint: str | None = None) -> None:
        super().__init__()
        self.endpoint = endpoint
        self._last_good: BatteryReading | None = None

    def probe(self, device: DeviceInfo) -> bool:
        # 只对 MCHOSE 的设备尝试，别的品牌不浪费时间
        return device.vid in (0x3837, 0x5253)

    def read(self, device: DeviceInfo) -> BatteryReading | None:
        for reader in (self._from_endpoint, self._from_files, self._from_uia):
            try:
                percent = reader()
            except Exception:
                continue
            if percent is None:
                continue
            return BatteryReading(
                percent=percent, charging=False,
                state=ReadingState.OK, transport=Transport.HUB_BRIDGE,
                note="来自 MCHOSE HUB Watcher",
            )
        return None

    # --- B1: 本地端口 -----------------------------------------------------

    def _from_endpoint(self) -> int | None:
        """TODO: 先侦察端口。

        PowerShell:
            Get-Process | ? ProcessName -match 'MCHOSE|Watcher' | % {
                Get-NetTCPConnection -OwningProcess $_.Id -State Listen }
        拿到端口后，用 urllib 请求，解析 JSON 里的 battery / percent 字段。
        """
        if not self.endpoint:
            return None
        raise NotImplementedError("B1 未实现：需要先确认 Watcher 的监听端口与返回结构")

    # --- B2: 落盘文件 -----------------------------------------------------

    def _from_files(self) -> int | None:
        """扫描候选目录里的 json，找形如 "battery": 87 的字段。"""
        for base in CANDIDATE_DIRS:
            if not base or not base.exists():
                continue
            for path in base.rglob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                except (OSError, json.JSONDecodeError):
                    continue
                found = _find_percent(data)
                if found is not None:
                    return found
        return None

    # --- B3: UI Automation -------------------------------------------------

    def _from_uia(self) -> int | None:
        """TODO: 用 UIAutomation 找到 Watcher 窗口，遍历文本节点，正则抽百分比。

        零依赖做法是 ctypes 走 `oleacc` / `IUIAutomation` 的 COM 接口，
        但那套 vtable 样板代码很长；允许第三方依赖的话用 `uiautomation` 包最省事。
        """
        raise NotImplementedError("B3 未实现：建议先用 uiautomation 包验证可行性")


def _find_percent(node, depth: int = 0) -> int | None:
    """在 JSON 里递归找「像电量」的值。"""
    if depth > 6:
        return None
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, (int, float)) and re.search(
                r"batter|power|charge|percent|level", str(key), re.I
            ):
                v = int(value)
                if 0 <= v <= 100:
                    return v
            found = _find_percent(value, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node[:32]:
            found = _find_percent(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, str):
        m = _PERCENT_RE.search(node)
        if m:
            v = int(m.group(1))
            if 0 <= v <= 100:
                return v
    return None
