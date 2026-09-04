"""Chart widgets used by the CalorieK dashboard."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .candlestick import CandlePoint, CandlestickChart
    from .treemap import TreemapItem, TreemapWidget, layout_treemap


_LAZY_EXPORTS = {
    "CandlePoint": (".candlestick", "CandlePoint"),
    "CandlestickChart": (".candlestick", "CandlestickChart"),
    "TreemapItem": (".treemap", "TreemapItem"),
    "TreemapWidget": (".treemap", "TreemapWidget"),
    "layout_treemap": (".treemap", "layout_treemap"),
}


def __getattr__(name: str) -> Any:
    """Delay Qt imports so calculation-only tests stay GUI-independent."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value

__all__ = list(_LAZY_EXPORTS)
