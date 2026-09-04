"""Pure color-mode resolution kept separate from all numeric calculations."""

from __future__ import annotations

from typing import Literal


CandleColorMode = Literal["china", "international"]
TreemapColorMode = Literal["intake_red", "intake_green"]

RED = ("#B91C1C", "#DC2626", "#EF4444", "#F87171")
GREEN = ("#047857", "#059669", "#10B981", "#34D399")


def candle_palette(mode: CandleColorMode) -> tuple[str, str, str]:
    """Return ``(up, down, flat)`` without touching candle values."""

    if mode == "china":
        return "#DC2626", "#059669", "#64748B"
    if mode == "international":
        return "#059669", "#DC2626", "#64748B"
    raise ValueError(f"unsupported candle color mode: {mode}")


def treemap_palette(side: str, mode: TreemapColorMode) -> tuple[str, ...]:
    if side not in {"intake", "burn"}:
        raise ValueError(f"unsupported treemap side: {side}")
    if mode not in {"intake_red", "intake_green"}:
        raise ValueError(f"unsupported treemap color mode: {mode}")
    intake_uses_red = mode == "intake_red"
    return RED if (side == "intake") == intake_uses_red else GREEN


__all__ = [
    "CandleColorMode",
    "TreemapColorMode",
    "candle_palette",
    "treemap_palette",
]

