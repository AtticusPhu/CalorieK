"""Pure calculation models for CalorieK.

This package deliberately has no GUI, database, or third-party dependencies.
The public dataclasses are immutable so a calculation can be reproduced from
the same inputs during cache rebuilding and tests.
"""

from .calibration import (
    CalibrationDay,
    CalibrationEngine,
    CalibrationResult,
    EWMAWeightTrend,
    GridSearchCalibrationEngine,
    WeightTrendModel,
)
from .daily_projection import (
    DailyProjectionInput,
    DailyProjectionResult,
    DailyProjectionService,
    DefaultDailyProjectionService,
)
from .metabolism import (
    BaselineBurnResult,
    MetabolismModel,
    MifflinStJeorModel,
    Sex,
    calculate_age,
)
from .ohlc import (
    EnergyEvent,
    EnergyEventType,
    MissingWeightAnchorError,
    OHLCGenerator,
    OHLCInput,
    OHLCResult,
    TrajectoryPoint,
    WeightAnchor,
    WeightMeasurement,
)
from .weight_model import EnergyBalance, SimpleEnergyWeightModel, WeightModel

__all__ = [
    "BaselineBurnResult",
    "CalibrationDay",
    "CalibrationEngine",
    "CalibrationResult",
    "DailyProjectionInput",
    "DailyProjectionResult",
    "DailyProjectionService",
    "DefaultDailyProjectionService",
    "EnergyBalance",
    "EnergyEvent",
    "EnergyEventType",
    "EWMAWeightTrend",
    "GridSearchCalibrationEngine",
    "MetabolismModel",
    "MifflinStJeorModel",
    "MissingWeightAnchorError",
    "OHLCGenerator",
    "OHLCInput",
    "OHLCResult",
    "Sex",
    "SimpleEnergyWeightModel",
    "TrajectoryPoint",
    "WeightAnchor",
    "WeightMeasurement",
    "WeightModel",
    "WeightTrendModel",
    "calculate_age",
]
