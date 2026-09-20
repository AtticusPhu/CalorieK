"""Typed boundary between the Qt presentation layer and application services.

The UI deliberately depends on this protocol instead of concrete database or model
implementations.  The composition root should provide a small adapter implementing
``UIContext`` and translate these DTOs to the service-layer types used by the app.
All energy fields use canonical kJ; widgets convert only at display/input boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal, Protocol, Sequence, runtime_checkable

from app.energy_units import DEFAULT_KJ_PER_KG, EnergyUnit
from app.nutrition import DailyNutrition, NutritionContribution


ColorMode = Literal["china", "international"]
TreemapColorMode = Literal["intake_red", "intake_green"]
AnchorMode = Literal["AUTO", "OPEN", "CLOSE"]
IntakeSection = Literal["recent", "favorites", "recipes", "search"]
ExerciseSection = Literal["recent", "favorites", "all"]
FoodBasisUnit = Literal["g", "ml"]


@dataclass(frozen=True, slots=True)
class ProfileDraft:
    sex: str
    birth_date: date
    height_cm: float
    current_weight_kg: float
    wake_time: time
    sleep_time: time
    awake_multiplier: float = 1.20
    sleep_multiplier: float = 0.95
    note: str = ""


@dataclass(frozen=True, slots=True)
class CandleDTO:
    local_date: date
    open_kg: float
    high_kg: float
    low_kg: float
    close_kg: float
    intake_kj: float = 0.0
    burn_kj: float = 0.0
    balance_kj: float = 0.0
    actual_weight_count: int = 0
    open_source: str = ""
    close_source: str = ""


@dataclass(frozen=True, slots=True)
class TreemapItemDTO:
    key: str
    name: str
    kj: float
    side: Literal["intake", "burn"]
    category: str = "其它"
    protein_g: float | None = None
    fat_g: float | None = None
    carb_g: float | None = None
    fiber_g: float | None = None
    duration_min: float | None = None


@dataclass(frozen=True, slots=True)
class DashboardDTO:
    as_of: datetime
    latest_actual_weight_kg: float | None = None
    latest_actual_at: datetime | None = None
    predicted_weight_kg: float | None = None
    predicted_change_kg: float | None = None
    intake_kj: float = 0.0
    baseline_kj: float = 0.0
    exercise_kj: float = 0.0
    total_burn_kj: float = 0.0
    balance_kj: float = 0.0
    candles: tuple[CandleDTO, ...] = ()
    treemap_items: tuple[TreemapItemDTO, ...] = ()
    calibration_kj_day: float = 0.0
    nutrition: DailyNutrition | None = None


@dataclass(frozen=True, slots=True)
class FoodServingDTO:
    """One named serving converted to a food's base measurement.

    ``serving_amount`` and ``serving_unit`` describe what the user enters while
    ``base_amount`` is the equivalent amount in the food's ``basis_unit``.
    """

    serving_id: int
    name: str
    serving_amount: float
    serving_unit: str
    base_amount: float
    basis_unit: FoodBasisUnit


@dataclass(frozen=True, slots=True)
class IntakeSourceDTO:
    source_type: Literal["food", "recipe"]
    source_id: int
    name: str
    category: str = "其它"
    detail: str = ""
    kj_reference: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carb_g: float | None = None
    fiber_g: float | None = None
    allowed_units: tuple[str, ...] = ("g",)
    default_amount: float = 100.0
    favorite: bool = False
    servings: tuple[FoodServingDTO, ...] = ()


@dataclass(frozen=True, slots=True)
class IntakeDraft:
    occurred_at: datetime
    source_type: Literal["food", "recipe"]
    source_id: int
    amount: float
    unit: str
    meal_category: str
    note: str = ""
    serving_id: int | None = None


@dataclass(frozen=True, slots=True)
class DailyRecordDTO:
    kind: Literal["intake", "exercise", "weight"]
    record_id: int
    occurred_at: datetime
    name: str
    note: str = ""
    amount: float = 0.0
    unit: str = ""
    meal_type: str = "OTHER"
    energy_kj: float = 0.0
    duration_min: float = 0.0
    exercise_type_id: int | None = None


@dataclass(frozen=True, slots=True)
class IntakeEditDraft:
    occurred_at: datetime
    amount: float
    meal_category: str
    note: str
    replacement: IntakeDraft | None = None


@dataclass(frozen=True, slots=True)
class ExerciseEditDraft:
    occurred_at: datetime
    duration_min: float
    active_kj: float
    note: str
    replacement_type_id: int | None = None


@dataclass(frozen=True, slots=True)
class ExerciseTypeDTO:
    exercise_type_id: int
    name: str
    default_duration_min: float = 30.0
    default_active_kj: float = 0.0
    last_duration_min: float | None = None
    last_active_kj: float | None = None
    favorite: bool = False
    active: bool = True


@dataclass(frozen=True, slots=True)
class ExerciseTypeDraft:
    exercise_type_id: int | None
    name: str
    default_duration_min: float
    default_active_kj: float
    favorite: bool = False


@dataclass(frozen=True, slots=True)
class ExerciseDraft:
    occurred_at: datetime
    exercise_type_id: int
    duration_min: float
    active_kj: float
    note: str = ""


@dataclass(frozen=True, slots=True)
class WeightDraft:
    occurred_at: datetime
    weight_kg: float
    anchor: AnchorMode = "AUTO"
    note: str = ""


@dataclass(frozen=True, slots=True)
class FoodDTO:
    food_id: int
    name: str
    category: str
    brand: str = ""
    basis_amount: float = 100.0
    basis_unit: FoodBasisUnit = "g"
    kj: float = 0.0
    protein_g: float = 0.0
    fat_g: float = 0.0
    carb_g: float = 0.0
    fiber_g: float = 0.0
    default_serving: float = 100.0
    is_builtin: bool = False
    is_favorite: bool = False
    user_modified: bool = False
    data_source: str = ""
    source_version: str = ""
    active: bool = True
    protein_g_per_100g: float | None = None
    fiber_g_per_100g: float | None = None
    fat_g_per_100g: float | None = None
    carbs_g_per_100g: float | None = None
    legacy_nutrition_available: bool = False
    basis_nutrition: NutritionContribution = field(default_factory=NutritionContribution)


@dataclass(frozen=True, slots=True)
class FoodDraft:
    food_id: int | None
    name: str
    category: str
    brand: str
    basis_amount: float
    basis_unit: FoodBasisUnit
    kj: float
    protein_g: float
    fat_g: float
    carb_g: float
    fiber_g: float
    default_serving: float
    is_favorite: bool
    protein_g_per_100g: float | None = None
    fiber_g_per_100g: float | None = None
    fat_g_per_100g: float | None = None
    carbs_g_per_100g: float | None = None


@dataclass(frozen=True, slots=True)
class RecipeSummaryDTO:
    recipe_id: int
    name: str
    total_weight_g: float = 0.0
    total_volume_ml: float = 0.0
    normalization_unit: FoodBasisUnit | None = "g"
    total_kj: float = 0.0
    active: bool = True


@dataclass(frozen=True, slots=True)
class RecipeItemDTO:
    food_id: int
    food_name: str
    amount_g: float
    unit: FoodBasisUnit = "g"


@dataclass(frozen=True, slots=True)
class NutritionDTO:
    total_weight_g: float = 0.0
    total_volume_ml: float = 0.0
    normalization_unit: FoodBasisUnit | None = "g"
    kj: float = 0.0
    protein_g: float = 0.0
    fat_g: float = 0.0
    carb_g: float = 0.0
    fiber_g: float = 0.0
    per_100g_kj: float = 0.0
    per_100g_protein_g: float = 0.0
    per_100g_fat_g: float = 0.0
    per_100g_carb_g: float = 0.0
    per_100g_fiber_g: float = 0.0
    composition: NutritionContribution = field(default_factory=NutritionContribution)


@dataclass(frozen=True, slots=True)
class RecipeDetailDTO:
    recipe_id: int | None
    name: str
    note: str = ""
    items: tuple[RecipeItemDTO, ...] = ()
    nutrition: NutritionDTO = field(default_factory=NutritionDTO)
    active: bool = True


@dataclass(frozen=True, slots=True)
class RecipeDraft:
    recipe_id: int | None
    name: str
    note: str
    items: tuple[RecipeItemDTO, ...]


@dataclass(frozen=True, slots=True)
class SettingsDTO:
    sex: str = "male"
    birth_date: date = field(default_factory=lambda: date(1990, 1, 1))
    height_cm: float = 170.0
    wake_time: time = field(default_factory=lambda: time(7, 0))
    sleep_time: time = field(default_factory=lambda: time(23, 0))
    awake_multiplier: float = 1.20
    sleep_multiplier: float = 0.95
    candle_color_mode: ColorMode = "china"
    treemap_color_mode: TreemapColorMode = "intake_red"
    kj_per_kg: float = DEFAULT_KJ_PER_KG
    data_directory: Path = field(default_factory=lambda: Path("data"))
    energy_display_unit: EnergyUnit = "kj"


@dataclass(frozen=True, slots=True)
class SettingsDraft:
    sex: str
    birth_date: date
    height_cm: float
    wake_time: time
    sleep_time: time
    awake_multiplier: float
    sleep_multiplier: float
    candle_color_mode: ColorMode
    treemap_color_mode: TreemapColorMode
    kj_per_kg: float
    data_directory: Path
    energy_display_unit: EnergyUnit = "kj"


@runtime_checkable
class UIContext(Protocol):
    """Application-facing operations required by the Qt presentation layer."""

    def get_daily_nutrition(self, day: date) -> DailyNutrition: ...

    def get_daily_records(self, day: date) -> Sequence[DailyRecordDTO]: ...

    def update_intake(self, record_id: int, edit: IntakeEditDraft) -> None: ...

    def update_exercise(self, record_id: int, edit: ExerciseEditDraft) -> None: ...

    def has_profile(self) -> bool: ...

    def save_initial_profile(self, profile: ProfileDraft) -> None: ...

    def get_dashboard(self, on_date: date | None = None) -> DashboardDTO: ...

    def list_intake_sources(
        self,
        section: IntakeSection,
        query: str = "",
    ) -> Sequence[IntakeSourceDTO]: ...

    def record_intake(self, event: IntakeDraft) -> None: ...

    def list_exercise_types(
        self,
        section: ExerciseSection,
    ) -> Sequence[ExerciseTypeDTO]: ...

    def record_exercise(self, event: ExerciseDraft) -> None: ...

    def list_exercise_shortcuts(
        self,
        query: str = "",
        include_inactive: bool = False,
    ) -> Sequence[ExerciseTypeDTO]: ...

    def save_exercise_type(self, exercise: ExerciseTypeDraft) -> ExerciseTypeDTO: ...

    def set_exercise_type_active(self, exercise_type_id: int, active: bool) -> None: ...

    def record_weight(self, measurement: WeightDraft) -> None: ...

    def list_foods(
        self,
        query: str = "",
        include_inactive: bool = False,
    ) -> Sequence[FoodDTO]: ...

    def save_food(self, food: FoodDraft) -> FoodDTO: ...

    def set_food_active(self, food_id: int, active: bool) -> None: ...

    def list_recipes(
        self,
        query: str = "",
        include_inactive: bool = False,
    ) -> Sequence[RecipeSummaryDTO]: ...

    def get_recipe(self, recipe_id: int) -> RecipeDetailDTO: ...

    def preview_recipe(self, items: Sequence[RecipeItemDTO]) -> NutritionDTO: ...

    def save_recipe(self, recipe: RecipeDraft) -> RecipeDetailDTO: ...

    def set_recipe_active(self, recipe_id: int, active: bool) -> None: ...

    def get_settings(self) -> SettingsDTO: ...

    def get_energy_display_unit(self) -> EnergyUnit: ...

    def save_settings(self, settings: SettingsDraft) -> bool:
        """Save settings and return whether model/data inputs changed."""
        ...

    def backup_data(self, destination: Path) -> Path: ...

    def restore_data(self, backup_file: Path) -> None: ...

    def export_data(self, destination: Path) -> Path: ...


__all__ = [
    "AnchorMode",
    "CandleDTO",
    "ColorMode",
    "DashboardDTO",
    "DailyRecordDTO",
    "IntakeEditDraft",
    "ExerciseEditDraft",
    "ExerciseDraft",
    "ExerciseSection",
    "ExerciseTypeDTO",
    "ExerciseTypeDraft",
    "FoodBasisUnit",
    "FoodDTO",
    "FoodDraft",
    "FoodServingDTO",
    "IntakeDraft",
    "IntakeSection",
    "IntakeSourceDTO",
    "NutritionDTO",
    "ProfileDraft",
    "RecipeDetailDTO",
    "RecipeDraft",
    "RecipeItemDTO",
    "RecipeSummaryDTO",
    "SettingsDTO",
    "SettingsDraft",
    "TreemapColorMode",
    "TreemapItemDTO",
    "UIContext",
    "WeightDraft",
]
