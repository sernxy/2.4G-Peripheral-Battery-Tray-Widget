"""Provider 接口与责任链。

一个 Provider 就是「一种读电量的办法」。链按 priority 升序试，
第一个返回非 None 的赢；失败会被记住并冷却，避免每次刷新都全量试一遍。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Iterable, Sequence

from ..core.model import BatteryReading, DeviceInfo


class BatteryProvider(ABC):
    """所有取数方式的基类。"""

    name: str = "base"
    priority: int = 100          # 越小越先试
    cooldown_seconds: float = 300.0
    max_consecutive_failures: int = 3

    def __init__(self) -> None:
        self._failures = 0
        self._disabled_until = 0.0
        self._last_error = ""

    # --- 子类实现 ---------------------------------------------------------

    @abstractmethod
    def probe(self, device: DeviceInfo) -> bool:
        """这台设备归不归我管。必须便宜（只看 id / usage page）。"""

    @abstractmethod
    def read(self, device: DeviceInfo) -> BatteryReading | None:
        """读一次。读不到返回 None，绝不抛异常给上层。"""

    def close(self) -> None:
        """释放句柄。"""

    # --- 健康度 -----------------------------------------------------------

    def is_available(self) -> bool:
        return time.monotonic() >= self._disabled_until

    def record_success(self) -> None:
        self._failures = 0
        self._disabled_until = 0.0
        self._last_error = ""

    def record_failure(self, error: str = "") -> None:
        self._failures += 1
        self._last_error = error
        if self._failures >= self.max_consecutive_failures:
            self._disabled_until = time.monotonic() + self.cooldown_seconds

    @property
    def status(self) -> str:
        if not self.is_available():
            return f"冷却中（{self._last_error or '多次失败'}）"
        return f"ok（连续失败 {self._failures}）"


class ProviderChain:
    """按顺序尝试所有 Provider，返回第一个成功的结果。"""

    def __init__(self, providers: Sequence[BatteryProvider]) -> None:
        self._providers: list[BatteryProvider] = sorted(providers, key=lambda p: p.priority)
        # 记住「上次是谁成功的」，下次先试它
        self._preferred: str | None = None

    @property
    def providers(self) -> list[BatteryProvider]:
        return list(self._providers)

    def read(self, device: DeviceInfo) -> BatteryReading | None:
        candidates = [p for p in self._providers if p.is_available() and p.probe(device)]
        if self._preferred:
            candidates.sort(key=lambda p: (p.name != self._preferred, p.priority))

        for provider in candidates:
            try:
                reading = provider.read(device)
            except Exception as exc:  # Provider 内部错误不应该炸掉整个刷新
                provider.record_failure(f"{type(exc).__name__}: {exc}")
                continue
            if reading is not None:
                provider.record_success()
                self._preferred = provider.name
                return reading
            provider.record_failure("无应答")
        return None

    def close(self) -> None:
        for p in self._providers:
            try:
                p.close()
            except Exception:
                pass
