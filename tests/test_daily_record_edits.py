"""CAL-12 edits and chronological facts, exclusively temporary synthetic data."""

from datetime import date, datetime, time, timedelta
import tempfile
import unittest
from unittest.mock import patch

from app.application import ApplicationContext
from app.services.weight_service import WeightService
from app.ui.context import IntakeDraft, IntakeEditDraft, ExerciseEditDraft


class DailyRecordEditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.context = ApplicationContext(temporary.name)
        self.foods = self.context.foods
        self.food = self.foods.create_food(
            name="Original", category="Synthetic", basis_unit="g", kj=400,
            protein_g_per_100g=10, fiber_g_per_100g=0, fat_g_per_100g=2, carbs_g_per_100g=30,
        )
        self.when = datetime(2026, 9, 1, 12, 0, 17)
        self.event = self.foods.record_food_intake(self.food, amount=50, occurred_at=self.when, note="old")

    def edit(self, **changes):
        values = dict(occurred_at=self.when, amount=50, meal_category="LUNCH", note="changed")
        values.update(changes)
        return IntakeEditDraft(**values)

    def test_ordinary_intake_edit_scales_saved_snapshot_even_after_source_deleted(self):
        before = self.foods.get_intake_event(self.event)
        self.foods.update_food(self.food, name="New library name", kj=999, protein_g_per_100g=99)
        self.foods.soft_delete_food(self.food)
        new_time = datetime(2026, 9, 2, 8, 15, 13)
        self.context.database.clear_dirty()
        with patch.object(self.context.daily, "ensure_calculated") as calculate:
            self.context.update_intake(self.event, self.edit(amount=125, occurred_at=new_time, meal_category="BREAKFAST"))
        after = self.foods.get_intake_event(self.event)
        for field in ("source_type", "source_id", "name_snapshot", "unit", "nutrition_complete", "created_at"):
            self.assertEqual(after[field], before[field], field)
        for field in ("kj_snapshot", "protein_snapshot", "fat_snapshot", "carb_snapshot", "fiber_snapshot",
                      "protein_g", "fiber_g", "fat_g", "carbs_g"):
            self.assertEqual(after[field], before[field] * 2.5, field)
        self.assertEqual(after["note"], "changed")
        self.assertEqual(after["meal_type"], "BREAKFAST")
        self.assertEqual(after["occurred_at"], new_time.isoformat())
        self.assertEqual(self.context.database.get_dirty_from_date(), "2026-09-01")
        calculate.assert_called_once_with(max(date.today(), date(2026, 9, 2)))
        self.assertEqual(self.context.get_daily_nutrition(date(2026, 9, 1)).intake_count, 0)
        self.assertEqual(self.context.get_daily_nutrition(date(2026, 9, 2)).nutrients.protein_g, 12.5)
        blocks = self.context.treemap.build_day(date(2026, 9, 2), baseline_kj=0)
        self.assertEqual(blocks[0].kj, 500)
        self.assertEqual(blocks[0].details["protein_g"], 12.5)

    def test_intentional_unit_replacement_uses_current_serving_then_freezes(self):
        serving = self.foods.add_serving(self.food, name="bowl", base_amount=200, serving_amount=2)
        original = self.foods.get_intake_event(self.event)
        replacement = IntakeDraft(self.when, "food", self.food, 3, "serving", "LUNCH", serving_id=serving)
        self.context.update_intake(self.event, self.edit(amount=3, replacement=replacement))
        saved = self.foods.get_intake_event(self.event)
        self.assertEqual(saved["id"], self.event)
        self.assertEqual(saved["created_at"], original["created_at"])
        self.assertEqual(saved["amount"], 3)
        self.assertEqual(saved["unit"], "serving")
        self.assertEqual(saved["kj_snapshot"], 1200)
        self.assertEqual(saved["protein_g"], 30)
        self.foods.update_serving(serving, base_amount=900)
        self.foods.update_food(self.food, kj=999)
        self.context.update_intake(self.event, self.edit(amount=6))
        self.assertEqual(self.foods.get_intake_event(self.event)["kj_snapshot"], 2400)
        self.assertEqual(len(self.foods.list_intake_events()), 1)

    def test_recipe_replacement_and_mixed_units_preserve_fraction_semantics(self):
        volume = self.foods.create_food(name="Volume", category="Test", basis_unit="ml", kj=100)
        recipe = self.context.recipes.create_recipe(name="Mixed", items=[(self.food, 100, "g"), (volume, 100, "ml")])
        replacement = IntakeDraft(self.when, "recipe", recipe, 50, "ratio_percent", "LUNCH")
        self.context.update_intake(self.event, self.edit(amount=50, replacement=replacement))
        after = self.foods.get_intake_event(self.event)
        self.assertEqual((after["source_type"], after["source_id"], after["unit"], after["amount"]), ("RECIPE", recipe, "recipe", 0.5))
        self.assertEqual(after["kj_snapshot"], 250)
        self.assertEqual(after["nutrition_complete"], 0)
        self.context.update_intake(self.event, self.edit(amount=0.25))
        after = self.foods.get_intake_event(self.event)
        self.assertEqual(after["kj_snapshot"], 125)
        self.assertEqual(after["nutrition_complete"], 0)

    def test_invalid_replacement_and_invalidation_failure_roll_back(self):
        self.context.database.clear_dirty()
        before = self.foods.get_intake_event(self.event)
        invalid = IntakeDraft(self.when, "food", self.food, 50, "ml", "LUNCH")
        with self.assertRaises(ValueError):
            self.context.update_intake(self.event, self.edit(replacement=invalid))
        self.assertEqual(self.foods.get_intake_event(self.event), before)
        valid = IntakeDraft(self.when, "food", self.food, 100, "g", "LUNCH")
        with patch.object(self.context.database, "mark_dirty", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.context.update_intake(self.event, self.edit(amount=100, replacement=valid))
        self.assertEqual(self.foods.get_intake_event(self.event), before)
        self.assertIsNone(self.context.database.get_dirty_from_date())

    def test_exercise_edit_preserves_or_explicitly_replaces_type_snapshot(self):
        exercises = self.context.exercises
        exercise_type = exercises.create_exercise_type(name="Original exercise")
        event_id = exercises.record_exercise(exercise_type, duration_min=30, active_kj=300, occurred_at=self.when)
        exercises.update_exercise_type(exercise_type, name="Renamed library type")
        exercises.soft_delete_exercise_type(exercise_type)
        self.context.database.clear_dirty()
        earlier = datetime(2026, 8, 31, 10, 0)
        with patch.object(self.context.daily, "ensure_calculated") as calculate:
            self.context.update_exercise(event_id, ExerciseEditDraft(earlier, 45.12345, 450.12345, "corrected"))
        after = exercises.get_exercise_event(event_id)
        self.assertEqual(after["name_snapshot"], "Original exercise")
        self.assertEqual(after["duration_min"], 45.12345)
        self.assertEqual(after["active_kj"], 450.12345)
        self.assertEqual(after["note"], "corrected")
        self.assertEqual(self.context.database.get_dirty_from_date(), "2026-08-31")
        calculate.assert_called_once_with(max(date.today(), self.when.date()))
        new_type = exercises.create_exercise_type(name="Correction")
        self.context.update_exercise(event_id, ExerciseEditDraft(earlier, 45, 450, "", new_type))
        self.assertEqual(exercises.get_exercise_event(event_id)["name_snapshot"], "Correction")

    def test_timeline_is_chronological_includes_weight_and_excludes_deleted(self):
        self.context.exercises.record_exercise(1, duration_min=30, active_kj=50, occurred_at="2026-09-01T09:00:00")
        WeightService(self.context.database).record_weight(70, occurred_at="2026-09-01T08:00:00")
        removed = self.foods.record_custom_intake(name="Deleted", amount=1, unit="g", kj=1, occurred_at="2026-09-01T07:00:00")
        self.foods.soft_delete_intake_event(removed)
        self.foods.update_food(self.food, name="Changed library")
        records = self.context.get_daily_records(self.when.date())
        self.assertEqual([record.kind for record in records], ["weight", "exercise", "intake"])
        self.assertEqual(records[-1].name, "Original")
        self.assertEqual(records[-1].record_id, self.event)
        self.assertEqual(self.context.get_daily_records(date(2026, 9, 3)), ())

    def test_noop_intake_edit_retains_unrounded_snapshot_and_null_flag(self):
        with self.context.database.transaction() as connection:
            connection.execute(
                "UPDATE intake_events SET nutrition_complete=NULL, protein_g=NULL, "
                "protein_snapshot=12.3456789012345 WHERE id=?", (self.event,),
            )
        before = self.foods.get_intake_event(self.event)
        self.context.update_intake(self.event, self.edit())
        after = self.foods.get_intake_event(self.event)
        for field in ("protein_snapshot", "protein_g", "nutrition_complete", "kj_snapshot", "occurred_at"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(self.context.get_daily_nutrition(self.when.date()).nutrients.protein_g, 12.3456789012345)

    def test_edit_recalculates_both_days_dashboard_candles_and_treemap(self):
        today = date.today()
        yesterday = today - timedelta(days=1)
        self.context.profile.create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175, current_weight_kg=70,
            wake_time="07:00", sleep_time="23:00",
            effective_from=yesterday, measured_at=datetime.combine(yesterday, time(8)),
        )
        self.foods.soft_delete_intake_event(self.event)
        event_id = self.foods.record_food_intake(self.food, amount=100, occurred_at=datetime.combine(yesterday, time(12)))
        self.context.daily.ensure_calculated(today)
        self.context.update_intake(event_id, IntakeEditDraft(datetime.combine(today, time.min), 200, "BREAKFAST", "moved"))
        self.assertIsNone(self.context.database.get_dirty_from_date())
        old = self.context.get_dashboard(yesterday)
        new = self.context.get_dashboard(today)
        self.assertEqual(old.intake_kj, 0)
        self.assertEqual(new.intake_kj, 800)
        self.assertEqual(new.nutrition.nutrients.protein_g, 20)
        self.assertEqual(new.candles[-1].intake_kj, 800)
        self.assertEqual(sum(item.kj for item in new.treemap_items if item.side == "intake"), 800)
        exercise = self.context.exercises.record_exercise(1, duration_min=10, active_kj=100, occurred_at=datetime.combine(yesterday, time(12)))
        self.context.update_exercise(exercise, ExerciseEditDraft(datetime.combine(today, time.min), 20, 200, "moved"))
        self.assertEqual(self.context.get_dashboard(yesterday).exercise_kj, 0)
        self.assertEqual(self.context.get_dashboard(today).exercise_kj, 200)
