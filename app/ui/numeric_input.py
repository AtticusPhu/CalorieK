"""High-precision editors with switchable presentation of canonical kJ values."""

from __future__ import annotations

from math import isfinite

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QDoubleSpinBox

from app.energy_units import (
    EnergyUnit, energy_from_input, energy_to_display, energy_unit_label, normalize_energy_unit,
)


class PreciseDoubleSpinBox(QDoubleSpinBox):
    """Keep an untouched stored float, even beyond the displayed precision.

    Qt rounds values to ``decimals`` on assignment. Remember the supplied float
    separately so opening and saving an editor cannot quantize existing data.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stored_value: float | None = None
        self._displayed_value: float | None = None
        self._display_decimals = 2
        # Keep Qt's effective numeric precision high. ``textFromValue`` below
        # controls presentation only, so showing two decimals cannot quantize
        # an untouched stored float.
        QDoubleSpinBox.setDecimals(self, 12)

    def setDecimals(self, decimals: int) -> None:
        # User-facing decimal fields use a fixed two-decimal presentation.
        # Qt still keeps at least 12 fractional digits internally.
        self._display_decimals = 2
        QDoubleSpinBox.setDecimals(self, max(12, int(decimals), self._display_decimals))

    def textFromValue(self, value: float) -> str:
        decimals = getattr(self, "_display_decimals", 2)
        return f"{float(value):.{decimals}f}"

    def valueFromText(self, text: str) -> float:
        # A two-decimal rendering must not become a data edit merely because
        # Qt interprets the unchanged line-edit text on focus loss or before a
        # kJ/kcal unit switch. If the text still equals the current rendered
        # value, return the high-precision internal Qt value unchanged.
        current = QDoubleSpinBox.value(self)
        numeric_text = str(text)
        prefix = self.prefix()
        suffix = self.suffix()
        if prefix and numeric_text.startswith(prefix):
            numeric_text = numeric_text[len(prefix):]
        if suffix and numeric_text.endswith(suffix):
            numeric_text = numeric_text[:-len(suffix)]
        if numeric_text.strip() == self.textFromValue(current).strip():
            return current
        return QDoubleSpinBox.valueFromText(self, text)

    def setValue(self, value: float) -> None:
        value = float(value)
        if not isfinite(value):
            raise ValueError("Numeric editor values must be finite")
        # Do not clamp a valid pre-existing value to an arbitrary UI limit.
        if value < self.minimum():
            self.setMinimum(value)
        if value > self.maximum():
            self.setMaximum(value)
        super().setValue(value)
        self._stored_value = value
        self._displayed_value = super().value()

    def value(self) -> float:
        displayed = super().value()
        if self._stored_value is not None and displayed == self._displayed_value:
            return self._stored_value
        return displayed


class EnergySpinBox(PreciseDoubleSpinBox):
    """Edit either unit and return kJ, preserving exact no-op/switch round trips."""

    def __init__(self, parent=None, *, unit: EnergyUnit = "kj") -> None:
        super().__init__(parent)
        self._unit = normalize_energy_unit(unit)
        self._loaded_kj: float | None = None
        self._loaded_display: float | None = None
        self._unit_tail = ""
        self._range_kj = (energy_from_input(self.minimum(), self._unit),
                          energy_from_input(self.maximum(), self._unit))
        self._step_kj = energy_from_input(self.singleStep(), self._unit)
        self._update_suffix()

    @property
    def display_unit(self) -> EnergyUnit:
        return self._unit

    def _update_suffix(self) -> None:
        self.setSuffix(f" {energy_unit_label(self._unit)}{self._unit_tail}")

    def set_unit_tail(self, tail: str) -> None:
        """Keep e.g. /kg attached to the selected unit when it changes."""
        self._unit_tail = tail
        self._update_suffix()

    def setRange(self, minimum: float, maximum: float) -> None:
        self._range_kj = (energy_from_input(minimum, self._unit), energy_from_input(maximum, self._unit))
        super().setRange(minimum, maximum)

    def set_kj_range(self, minimum: float, maximum: float) -> None:
        self._range_kj = (float(minimum), float(maximum))
        super().setRange(energy_to_display(minimum, self._unit), energy_to_display(maximum, self._unit))

    def setSingleStep(self, value: float) -> None:
        self._step_kj = energy_from_input(value, self._unit)
        super().setSingleStep(value)

    def set_kj_single_step(self, value: float) -> None:
        self._step_kj = float(value)
        super().setSingleStep(energy_to_display(value, self._unit))

    def set_display_unit(self, unit: object) -> None:
        new_unit = normalize_energy_unit(unit)
        if new_unit == self._unit:
            return
        # Commit pending text before capturing its canonical value, then block
        # intermediate range/value signals so no caller persists a transient value.
        self.interpretText()
        canonical = self.energy_kj()
        with QSignalBlocker(self):
            self._unit = new_unit
            self._update_suffix()
            super().setRange(*(energy_to_display(value, self._unit) for value in self._range_kj))
            super().setSingleStep(energy_to_display(self._step_kj, self._unit))
            self.set_energy_kj(canonical)

    def setValue(self, value: float) -> None:
        self._loaded_kj = None
        self._loaded_display = None
        super().setValue(value)

    def set_energy_kj(self, value: float) -> None:
        value = float(value)
        super().setValue(energy_to_display(value, self._unit))
        self._loaded_kj = value
        self._loaded_display = self.value()

    def energy_kj(self) -> float:
        value = self.value()
        if self._loaded_kj is not None and value == self._loaded_display:
            return self._loaded_kj
        return energy_from_input(value, self._unit)


class KcalEnergySpinBox(EnergySpinBox):
    """Compatibility wrapper for explicit kcal-only callers."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent, unit="kcal")
