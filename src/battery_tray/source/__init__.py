"""① 找电量：Provider 接口、责任链、各家厂商的实现。

换厂商只需要在这里新增一个 Provider，其余模块不用动。
"""

from __future__ import annotations

from .base import BatteryProvider, ProviderChain
from .hub_bridge import HubBridgeProvider
from .mchose_a7v3 import MchoseHidProvider
from .standard_hid import StandardHidProvider

__all__ = [
    "BatteryProvider",
    "ProviderChain",
    "MchoseHidProvider",
    "StandardHidProvider",
    "HubBridgeProvider",
    "build_default_chain",
]


def build_default_chain(verbose: bool = False) -> ProviderChain:
    chain = ProviderChain([
        MchoseHidProvider(),
        StandardHidProvider(),
        HubBridgeProvider(),
    ])
    if verbose:
        for provider in chain.providers:
            setattr(provider, "verbose", True)
    return chain
