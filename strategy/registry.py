"""
Strategy Registry – discover and instantiate strategies by name.
"""

from __future__ import annotations

from typing import Dict, Type, Any, List, Optional

from strategy.base import BaseStrategy
from strategy.examples.ema_atr_trend import EmaAtrTrendStrategy
from strategy.examples.funding_mean_reversion import FundingMeanReversionStrategy


# Built-in strategies
_REGISTRY: Dict[str, Type[BaseStrategy]] = {
    "ema_atr_trend": EmaAtrTrendStrategy,
    "funding_mean_reversion": FundingMeanReversionStrategy,
}


def register_strategy(name: str, cls: Type[BaseStrategy]) -> None:
    _REGISTRY[name] = cls


def get_strategy_class(name: str) -> Type[BaseStrategy]:
    if name not in _REGISTRY:
        raise KeyError(f"Strategy '{name}' not found. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[name]


def list_strategies() -> List[str]:
    return list(_REGISTRY.keys())


def create_strategy(
    name: str,
    strategy_id: Optional[str] = None,
    symbols: Optional[List[str]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> BaseStrategy:
    cls = get_strategy_class(name)
    return cls(
        strategy_id=strategy_id or name,
        symbols=symbols,
        params=params,
    )
