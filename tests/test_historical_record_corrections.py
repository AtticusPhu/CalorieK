"""CAL-38 synthetic service/adapter regressions. Authored, not run by Codex."""

from datetime import date, datetime, time, timedelta, timezone
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.application import ApplicationContext
from app.nutrition import LEGACY_SNAPSHOT_FIELDS, NUTRIENT_NAMES, NutrientValues, intake_contribution
from app.services.weight_service import WeightService
from app.ui.context import (
    CustomIntakeEdit, ExerciseEditDraft, IntakeDraft, IntakeEditDraft,
    IntakeSnapshotEdit, RecordRecalculationError,
)


class CorrectionFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        self.context = ApplicationContext(self.directory)
        self.db = self.context.database
        self.addCleanup(self.db.close)
        self.foods = self.context.foods
        self.exercises = self.context.exercises
        self.day = date.today() - timedelta(days=3)
        self.when = datetime.combine(self.day, time(12, 30, 17))
        self.food = self.foods.create_food(
            name="Synthetic original", category="CAL-38", basis_unit="g", kj=400,
            protein_g_per_100g=10, fiber_g_per_100g=0, fat_g_per_100g=2, carbs_g_per_100g=30,
        )
        self.event = self.foods.record_food_intake(
            self.food, amount=250, occurred_at=self.when, meal_type="LUNCH", note=None,
        )
        self.exercise_type = self.exercises.create_exercise_type(name="Synthetic exercise")
        self.exercise = self.exercises.record_exercise(
            self.exercise_type, duration_min=0.123456789012345, active_kj=100.1234567890123,
            occurred_at=self.when, note=None,
        )
        self.db.clear_dirty()

    def rows(self, table):
        with self.db.connection() as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]

    def state(self):
        return {table: self.rows(table) for table in (
            "intake_events", "exercise_events", "daily_metrics_cache", "recalculation_state",
            "calibration_runs", "foods", "food_servings", "recipes", "recipe_items",
            "exercise_types", "weight_measurements", "profile", "profile_revisions",
        )}

    def edit(self, event_id=None, **changes):
        row = self.foods.get_intake_event(self.event if event_id is None else event_id)
        values = dict(occurred_at=datetime.fromisoformat(row["occurred_at"]), amount=row["amount"],
                      meal_category=row["meal_type"], note=row["note"] or "")
        values.update(changes)
        return IntakeEditDraft(**values)

    def exercise_edit(self, **changes):
        row = self.exercises.get_exercise_event(self.exercise)
        values = dict(occurred_at=datetime.fromisoformat(row["occurred_at"]),
                      duration_min=row["duration_min"], active_kj=row["active_kj"], note=row["note"] or "")
        values.update(changes)
        return ExerciseEditDraft(**values)

    def custom(self):
        return self.foods.record_custom_intake(
            name="Synthetic custom", amount=2.123456789012345, unit="scoops",
            kj=123.12345678901234, protein_g=2.987654321098765, fiber_g=None,
            fat_g=0, carb_g=None, occurred_at=self.when, meal_type="SNACK",
        )

    def mixed_recipe(self):
        volume = self.foods.create_food(name="Synthetic liquid", category="CAL-38", basis_unit="ml", kj=100)
        return self.context.recipes.create_recipe(
            name="Synthetic mixed recipe", items=[(self.food, 100, "g"), (volume, 100, "ml")],
        )


class HistoricalRecordCorrectionTests(CorrectionFixture):
    def test_250_to_25_scales_saved_snapshots_after_catalog_edit_and_deletion(self):
        before = self.foods.get_intake_event(self.event)
        self.foods.update_food(self.food, name="Changed catalog", kj=999, protein_g_per_100g=99)
        self.foods.soft_delete_food(self.food)
        catalog = self.rows("foods")
        self.assertTrue(self.context.update_intake(self.event, self.edit(amount=25)))
        after = self.foods.get_intake_event(self.event)
        for field in ("id", "created_at", "source_type", "source_id", "unit", "name_snapshot", "nutrition_complete"):
            self.assertEqual(after[field], before[field], field)
        for field in ("kj_snapshot", *LEGACY_SNAPSHOT_FIELDS, *NUTRIENT_NAMES):
            self.assertAlmostEqual(after[field], before[field] / 10, msg=field)
        self.assertEqual(len(self.foods.list_intake_events()), 1)
        self.assertEqual(self.rows("foods"), catalog)
        self.assertEqual(self.context.get_daily_nutrition(self.day).nutrients.protein_g, 2.5)
        blocks = self.context.treemap.build_day(self.day, baseline_kj=0)
        self.assertEqual(sum(block.kj for block in blocks if block.side == "intake"), 100)

    def test_dimensions_and_named_servings_are_saved_not_reinterpreted(self):
        volume = self.foods.create_food(name="Liquid", category="CAL-38", basis_unit="ml", kj=100)
        serving = self.foods.add_serving(volume, name="cup", base_amount=240, serving_amount=2)
        cases = ((self.food, "g", None, 1000), (volume, "ml", None, 250),
                 (volume, "serving", serving, 30000))
        for food, unit, serving_id, energy in cases:
            with self.subTest(unit=unit):
                replacement = IntakeDraft(self.when, "food", food, 250, unit, "LUNCH", serving_id=serving_id)
                original = self.foods.get_intake_event(self.event)
                self.context.update_intake(self.event, self.edit(amount=250, replacement=replacement))
                saved = self.foods.get_intake_event(self.event)
                self.assertEqual((saved["id"], saved["created_at"]), (original["id"], original["created_at"]))
                self.assertEqual((saved["unit"], saved["amount"], saved["kj_snapshot"]), (unit, 250, energy))
                self.context.update_intake(self.event, self.edit(amount=25))
                after = self.foods.get_intake_event(self.event)
                self.assertEqual((after["unit"], after["kj_snapshot"]), (unit, energy / 10))
        self.assertEqual(self.foods.get_food(volume)["basis_unit"], "ml")
        self.assertEqual(len(self.foods.list_intake_events()), 1)

    def test_mixed_recipe_fraction_noop_scaling_and_explicit_percent_replacement(self):
        recipe = self.mixed_recipe()
        event = self.context.recipes.record_recipe_intake(recipe, fraction=0.5, occurred_at=self.when)
        before = self.foods.get_intake_event(event)
        self.db.clear_dirty()
        self.assertFalse(self.context.update_intake(event, self.edit(event)))
        self.assertEqual(self.foods.get_intake_event(event), before)
        self.assertIsNone(self.db.get_dirty_from_date())
        self.context.recipes.update_recipe(recipe, name="New recipe", items=[(self.food, 900, "g")])
        self.context.recipes.soft_delete_recipe(recipe)
        self.context.update_intake(event, self.edit(event, amount=0.25))
        after = self.foods.get_intake_event(event)
        self.assertEqual((after["unit"], after["amount"], after["kj_snapshot"]), ("recipe", 0.25, 125))
        self.assertEqual(after["name_snapshot"], before["name_snapshot"])
        new_recipe = self.mixed_recipe()
        replacement = IntakeDraft(self.when, "recipe", new_recipe, 50, "ratio_percent", "LUNCH")
        self.context.update_intake(event, self.edit(event, amount=50, replacement=replacement))
        replaced = self.foods.get_intake_event(event)
        self.assertEqual((replaced["unit"], replaced["amount"], replaced["kj_snapshot"]), ("recipe", 0.5, 250))
        invalid = IntakeDraft(self.when, "recipe", new_recipe, 50, "g", "LUNCH")
        with self.assertRaisesRegex(ValueError, "mixed"):
            self.context.update_intake(event, self.edit(event, amount=50, replacement=invalid))
        self.assertEqual(self.foods.get_intake_event(event), replaced)

    def test_custom_all_fields_persist_on_restart_without_catalog_link(self):
        event = self.custom()
        before = self.foods.get_intake_event(event)
        other = self.foods.get_intake_event(self.event)
        new_time = self.when + timedelta(days=1)
        self.assertTrue(self.context.update_intake(event, self.edit(
            event, amount=0.25, occurred_at=new_time, meal_category="DINNER", note="corrected",
            custom=CustomIntakeEdit("Corrected custom", "pieces"),
            snapshot=IntakeSnapshotEdit(88.12345678901234, NutrientValues(None, 0, 1.123456789012345, None)),
        )))
        saved = self.foods.get_intake_event(event)
        self.assertEqual((saved["source_type"], saved["source_id"]), ("CUSTOM", None))
        self.assertEqual((saved["id"], saved["created_at"]), (before["id"], before["created_at"]))
        self.assertEqual((saved["name_snapshot"], saved["unit"], saved["amount"]), ("Corrected custom", "pieces", 0.25))
        self.assertEqual((saved["meal_type"], saved["occurred_at"], saved["note"]), ("DINNER", new_time.isoformat(), "corrected"))
        self.assertEqual(saved["kj_snapshot"], 88.12345678901234)  # Absolute total, not scaled by 0.25 / old amount.
        self.assertEqual(tuple(saved[name] for name in NUTRIENT_NAMES), (None, 0, 1.123456789012345, None))
        self.assertEqual(saved["protein_snapshot"], 0)  # NOT NULL storage placeholder, not a known nutrient.
        self.assertEqual(saved["nutrition_complete"], 0)
        self.assertIsNone(intake_contribution(saved).values.protein_g)
        self.db.close()
        restarted = ApplicationContext(self.directory)
        self.addCleanup(restarted.database.close)
        self.assertEqual(restarted.foods.get_intake_event(event), saved)
        self.assertEqual(restarted.foods.get_intake_event(self.event), other)
        self.assertEqual(len(restarted.foods.list_intake_events()), 2)

    def test_custom_description_only_then_ordinary_portion_preserves_unknowns(self):
        event = self.custom()
        before = self.foods.get_intake_event(event)
        self.context.update_intake(event, self.edit(event, custom=CustomIntakeEdit("Renamed", "pieces")))
        renamed = self.foods.get_intake_event(event)
        for field in ("amount", "kj_snapshot", *NUTRIENT_NAMES, *LEGACY_SNAPSHOT_FIELDS, "nutrition_complete"):
            self.assertEqual(renamed[field], before[field], field)
        self.context.update_intake(event, self.edit(event, amount=before["amount"] * 2))
        scaled = self.foods.get_intake_event(event)
        self.assertEqual(scaled["kj_snapshot"], before["kj_snapshot"] * 2)
        self.assertEqual(scaled["protein_g"], before["protein_g"] * 2)
        self.assertIsNone(scaled["fiber_g"])
        self.assertEqual(scaled["fat_g"], 0)
        self.assertIsNone(scaled["carbs_g"])
        self.assertEqual((scaled["source_type"], scaled["source_id"], scaled["nutrition_complete"]), ("CUSTOM", None, 0))
        self.assertEqual((scaled["name_snapshot"], scaled["unit"]), ("Renamed", "pieces"))

    def test_manual_food_and_recipe_snapshots_are_isolated_then_scale_new_totals(self):
        recipe = self.mixed_recipe()
        recipe_event = self.context.recipes.record_recipe_intake(recipe, fraction=0.5, occurred_at=self.when)
        untouched = self.foods.record_food_intake(self.food, amount=100, occurred_at=self.when)
        other = self.foods.get_intake_event(untouched)
        self.foods.update_food(self.food, kj=999, protein_g_per_100g=99)
        self.foods.soft_delete_food(self.food)
        self.context.recipes.soft_delete_recipe(recipe)
        catalog = {name: self.rows(name) for name in ("foods", "recipes", "recipe_items")}
        for event in (self.event, recipe_event):
            with self.subTest(event=event):
                before = self.foods.get_intake_event(event)
                self.context.update_intake(event, self.edit(
                    event, snapshot=IntakeSnapshotEdit(80, NutrientValues(1, None, 0, 4)),
                ))
                self.context.update_intake(event, self.edit(event, amount=before["amount"] * 2))
                after = self.foods.get_intake_event(event)
                self.assertEqual(after["kj_snapshot"], 160)
                self.assertEqual(tuple(after[name] for name in NUTRIENT_NAMES), (2, None, 0, 8))
                self.assertEqual(after["nutrition_complete"], 0)
                for field in ("id", "created_at", "source_type", "source_id", "name_snapshot", "unit"):
                    self.assertEqual(after[field], before[field], field)
        self.assertEqual(self.foods.get_intake_event(untouched), other)
        self.assertEqual({name: self.rows(name) for name in catalog}, catalog)

    def test_each_nutrient_can_be_unknown_known_zero_and_complete(self):
        event = self.custom()
        for index, name in enumerate(NUTRIENT_NAMES):
            with self.subTest(nutrient=name):
                self.context.update_intake(event, self.edit(event, snapshot=IntakeSnapshotEdit(100, NutrientValues(1, 2, 3, 4))))
                values = [1, 2, 3, 4]
                values[index] = None
                self.context.update_intake(event, self.edit(event, snapshot=IntakeSnapshotEdit(100, NutrientValues(*values))))
                unknown = self.foods.get_intake_event(event)
                self.assertIsNone(unknown[name])
                self.assertEqual(unknown[LEGACY_SNAPSHOT_FIELDS[index]], 0)
                self.assertEqual(unknown["nutrition_complete"], 0)
                values[index] = 0
                self.context.update_intake(event, self.edit(event, snapshot=IntakeSnapshotEdit(100, NutrientValues(*values))))
                known = self.foods.get_intake_event(event)
                self.assertEqual(known[name], 0)
                self.assertEqual(known["nutrition_complete"], 1)
                self.assertTrue(intake_contribution(known).complete)

    def test_noop_manual_and_ordinary_keep_raw_legacy_flags_offset_and_audit(self):
        for flag in (None, 0, 1):
            for stamp in (f"{self.day} 12:30:17.123456+08:00", f"{self.day}T12:30:17.123456Z"):
                with self.subTest(flag=flag, stamp=stamp):
                    with self.db.transaction() as connection:
                        connection.execute(
                            "UPDATE intake_events SET occurred_at=?, amount=?, kj_snapshot=?, protein_g=?, "
                            "protein_snapshot=?, fiber_g=0, fat_g=2, carbs_g=3, nutrition_complete=?, "
                            "updated_at='2000-01-01T00:00:00' WHERE id=?",
                            (stamp, 0.123456789012345, 100.12345678901234,
                             None if flag is None else 1.123456789012345, 7.987654321098765, flag, self.event),
                        )
                    self.db.clear_dirty()
                    before = self.state()
                    row = self.foods.get_intake_event(self.event)
                    unchanged = IntakeSnapshotEdit(row["kj_snapshot"], intake_contribution(row).values)
                    with patch.object(self.context.daily, "ensure_calculated") as calculate:
                        self.assertFalse(self.context.update_intake(self.event, self.edit()))
                        self.assertFalse(self.context.update_intake(self.event, self.edit(snapshot=unchanged)))
                    calculate.assert_not_called()
                    self.assertEqual(self.state(), before)

    def test_metadata_or_energy_only_changes_do_not_materialize_legacy_nutrients(self):
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE intake_events SET nutrition_complete=NULL, protein_g=NULL, protein_snapshot=? WHERE id=?",
                (12.3456789012345, self.event),
            )
        before = self.foods.get_intake_event(self.event)
        for edit in (self.edit(note="note only"), self.edit(snapshot=IntakeSnapshotEdit(99, intake_contribution(before).values))):
            self.context.update_intake(self.event, edit)
            after = self.foods.get_intake_event(self.event)
            for field in (*NUTRIENT_NAMES, *LEGACY_SNAPSHOT_FIELDS, "nutrition_complete"):
                self.assertEqual(after[field], before[field], field)

    def test_replacement_preserves_original_timestamp_text_and_detects_exact_noop(self):
        stamp = f"{self.day} 12:30:17.123456+08:00"
        with self.db.transaction() as connection:
            connection.execute("UPDATE intake_events SET occurred_at=?, updated_at='2000-01-01' WHERE id=?", (stamp, self.event))
        replacement = IntakeDraft(datetime.fromisoformat(stamp), "food", self.food, 250, "g", "LUNCH")
        self.db.clear_dirty()
        before = self.state()
        self.assertFalse(self.context.update_intake(self.event, self.edit(replacement=replacement)))
        self.assertEqual(self.state(), before)
        self.foods.update_food(self.food, kj=888)
        self.assertTrue(self.context.update_intake(self.event, self.edit(replacement=replacement)))
        self.assertEqual(self.foods.get_intake_event(self.event)["occurred_at"], stamp)

    def test_invalid_values_modes_and_unavailable_records_do_not_mutate(self):
        custom = self.custom()
        replacement = IntakeDraft(self.when, "food", self.food, 100, "g", "LUNCH")
        calls = [
            lambda: self.context.update_intake(self.event, self.edit(meal_category="")),
            lambda: self.context.update_intake(self.event, self.edit(meal_category="INVALID")),
            lambda: self.context.update_intake(self.event, self.edit(custom=CustomIntakeEdit("x", "g"))),
            lambda: self.context.update_intake(custom, self.edit(custom, replacement=replacement)),
            lambda: self.context.update_intake(self.event, self.edit(replacement=replacement, snapshot=IntakeSnapshotEdit(1, NutrientValues()))),
            lambda: self.context.update_intake(self.event, self.edit(replacement=replacement, custom=CustomIntakeEdit("x", "g"))),
            lambda: self.context.update_intake(custom, self.edit(custom, custom=CustomIntakeEdit(" ", "g"))),
            lambda: self.context.update_intake(custom, self.edit(custom, custom=CustomIntakeEdit("x", " \t"))),
            lambda: self.context.update_intake(999999, self.edit()),
            lambda: self.context.update_exercise(999999, self.exercise_edit()),
            lambda: self.context.update_exercise(self.exercise, self.exercise_edit(replacement_type_id=999999)),
        ]
        for bad in (float("nan"), float("inf"), -1.0):
            calls.extend((
                lambda bad=bad: self.context.update_intake(self.event, self.edit(amount=bad)),
                lambda bad=bad: self.context.update_intake(self.event, self.edit(snapshot=IntakeSnapshotEdit(bad, NutrientValues()))),
                lambda bad=bad: self.context.update_exercise(self.exercise, self.exercise_edit(duration_min=bad)),
                lambda bad=bad: self.context.update_exercise(self.exercise, self.exercise_edit(active_kj=bad)),
            ))
            for index in range(4):
                calls.append(lambda bad=bad, index=index: self.context.update_intake(
                    custom, self.edit(custom, snapshot=IntakeSnapshotEdit(1, NutrientValues(
                        *(bad if i == index else None for i in range(4)),
                    ))),
                ))
        calls.append(lambda: self.context.update_intake(self.event, self.edit(amount=0)))
        calls.append(lambda: self.context.update_intake(self.event, self.edit(amount=1e308)))
        self.db.clear_dirty()
        before = self.state()
        for number, call in enumerate(calls):
            with self.subTest(number=number), patch.object(self.context.daily, "ensure_calculated") as calculate:
                with self.assertRaises((ValueError, LookupError)):
                    call()
                calculate.assert_not_called()
                self.assertEqual(self.state(), before)
        self.foods.soft_delete_intake_event(self.event)
        self.exercises.soft_delete_exercise_event(self.exercise)
        before = self.state()
        with self.assertRaises(LookupError):
            self.context.update_intake(self.event, self.edit(amount=25))
        with self.assertRaises(LookupError):
            self.context.update_exercise(self.exercise, self.exercise_edit(active_kj=10))
        self.assertEqual(self.state(), before)

    def test_single_writer_access_rejection_cannot_start_an_edit_transaction(self):
        intake_edit = self.edit(amount=25)
        exercise_edit = self.exercise_edit(active_kj=25)
        before = self.state()
        with patch.object(self.db, "check_access", side_effect=RuntimeError("writer lease unavailable")), \
                patch.object(self.db, "transaction", wraps=self.db.transaction) as transaction:
            with self.assertRaisesRegex(RuntimeError, "writer lease"):
                self.context.update_intake(self.event, intake_edit)
            with self.assertRaisesRegex(RuntimeError, "writer lease"):
                self.context.update_exercise(self.exercise, exercise_edit)
        transaction.assert_not_called()
        self.assertEqual(self.state(), before)

    def test_exercise_noop_preserves_deleted_name_zero_precision_and_offset(self):
        self.exercises.update_exercise_type(self.exercise_type, name="Changed source")
        self.exercises.soft_delete_exercise_type(self.exercise_type)
        stamp = f"{self.day} 12:30:17.123456-04:00"
        with self.db.transaction() as connection:
            connection.execute("UPDATE exercise_events SET occurred_at=?, updated_at='2000-01-01' WHERE id=?", (stamp, self.exercise))
        self.db.clear_dirty()
        before = self.state()
        with patch.object(self.context.daily, "ensure_calculated") as calculate:
            self.assertFalse(self.context.update_exercise(self.exercise, self.exercise_edit()))
        calculate.assert_not_called()
        self.assertEqual(self.state(), before)
        self.context.update_exercise(self.exercise, self.exercise_edit(active_kj=0, duration_min=0, note="zero"))
        saved = self.exercises.get_exercise_event(self.exercise)
        self.assertEqual((saved["name_snapshot"], saved["occurred_at"]), ("Synthetic exercise", stamp))
        self.assertEqual((saved["active_kj"], saved["duration_min"]), (0, 0))
        replacement = self.exercises.create_exercise_type(name="Explicit new type")
        self.context.update_exercise(self.exercise, self.exercise_edit(replacement_type_id=replacement, active_kj=1.123456789, duration_min=2.123456789))
        after = self.exercises.get_exercise_event(self.exercise)
        self.assertEqual(after["name_snapshot"], "Explicit new type")
        self.assertEqual((after["active_kj"], after["duration_min"]), (1.123456789, 2.123456789))
        self.assertEqual(after["created_at"], saved["created_at"])
        self.assertEqual(len(self.exercises.list_exercise_events()), 1)


class HistoricalCorrectionModelTests(CorrectionFixture):
    def setUp(self):
        super().setUp()
        self.today = date.today()
        self.context.profile.create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175, current_weight_kg=70,
            wake_time="07:00", sleep_time="23:00", effective_from=self.day,
            measured_at=datetime.combine(self.day, time(8)),
        )
        WeightService(self.db).record_weight(69.8, occurred_at=datetime.combine(self.today - timedelta(days=1), time(18)))
        self.context.daily.ensure_calculated(self.today)

    def test_all_correction_modes_rollback_fact_cache_and_marker_after_dirty_failure(self):
        custom = self.custom()
        self.context.daily.ensure_calculated(self.today)
        replacement = IntakeDraft(self.when, "food", self.food, 10, "g", "LUNCH")
        actions = (
            lambda: self.context.update_intake(self.event, self.edit(amount=25)),
            lambda: self.context.update_intake(self.event, self.edit(snapshot=IntakeSnapshotEdit(30, NutrientValues(None, 0, 1, None)))),
            lambda: self.context.update_intake(custom, self.edit(custom, custom=CustomIntakeEdit("corrected", "pieces"))),
            lambda: self.context.update_intake(self.event, self.edit(amount=10, replacement=replacement)),
            lambda: self.context.update_exercise(self.exercise, self.exercise_edit(duration_min=1, active_kj=2)),
        )
        original_mark = self.db.mark_dirty

        def fail_after_mark(*args, **kwargs):
            self.assertIsNotNone(kwargs.get("connection"))
            original_mark(*args, **kwargs)
            raise RuntimeError("injected after dirty write")

        before = self.state()
        for index, action in enumerate(actions):
            with self.subTest(mode=index), patch.object(self.db, "mark_dirty", side_effect=fail_after_mark), \
                    patch.object(self.db, "transaction", wraps=self.db.transaction) as transaction, \
                    patch.object(self.context.daily, "ensure_calculated") as calculate:
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    action()
                transaction.assert_called_once()
                calculate.assert_not_called()
                self.assertEqual(self.state(), before)

    def test_sql_write_failures_leave_live_model_intact_and_allow_retry(self):
        for table, action in (
            ("intake_events", lambda: self.context.update_intake(self.event, self.edit(amount=25))),
            ("exercise_events", lambda: self.context.update_exercise(self.exercise, self.exercise_edit(active_kj=3))),
        ):
            with self.subTest(table=table):
                with self.db.transaction() as connection:
                    connection.execute(f"CREATE TRIGGER fail_edit BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'injected SQL'); END")
                before = self.state()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "injected SQL"):
                    action()
                self.assertEqual(self.state(), before)
                with self.db.transaction() as connection:
                    connection.execute("DROP TRIGGER fail_edit")
                self.assertTrue(action())
                self.assertIsNone(self.db.get_dirty_from_date())

    def test_invalid_moves_are_rejected_before_writes_including_existing_dirty_state(self):
        for target in (self.day - timedelta(days=1), date.max):
            for already_dirty in (False, True):
                with self.subTest(target=target, already_dirty=already_dirty):
                    if already_dirty:
                        self.db.mark_dirty(self.day + timedelta(days=1))
                    before = self.state()
                    # The local date, not UTC, defines the guard boundary.
                    occurred = datetime.combine(target, time(23), timezone(timedelta(hours=-12)))
                    with patch.object(self.db, "transaction", wraps=self.db.transaction) as transaction:
                        with self.assertRaisesRegex(ValueError, "记录未修改"):
                            self.context.update_intake(self.event, self.edit(occurred_at=occurred, amount=25))
                        with self.assertRaisesRegex(ValueError, "记录未修改"):
                            self.context.update_exercise(self.exercise, self.exercise_edit(occurred_at=occurred))
                    transaction.assert_not_called()
                    self.assertEqual(self.state(), before)

    def test_earliest_weight_boundary_and_missing_profile_context(self):
        later = self.day + timedelta(days=1)
        with self.db.transaction() as connection:
            connection.execute("UPDATE weight_measurements SET local_date=?, occurred_at=? WHERE local_date=?",
                               (later.isoformat(), datetime.combine(later, time(8)).isoformat(), self.day.isoformat()))
        before = self.state()
        with self.assertRaisesRegex(ValueError, later.isoformat()):
            self.context.update_intake(self.event, self.edit(occurred_at=self.when - timedelta(days=1)))
        self.assertEqual(self.state(), before)
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM profile_revisions")
        before = self.state()
        with self.assertRaisesRegex(ValueError, "缺少用户资料"):
            self.context.update_exercise(self.exercise, self.exercise_edit(occurred_at=self.when + timedelta(days=1)))
        self.assertEqual(self.state(), before)

    def test_cross_day_edits_refresh_today_nutrition_treemap_candles_calibration_and_future(self):
        # Put the corrected facts BETWEEN two actual-weight days, so the
        # calibration fit consumes their original totals (not its anchor day).
        old_day = self.day + timedelta(days=1)
        old_clock = datetime.combine(old_day, time(12))
        self.foods.update_intake_event(self.event, occurred_at=old_clock)
        self.exercises.update_exercise_event(self.exercise, occurred_at=old_clock)
        future = self.today + timedelta(days=2)
        self.context.daily.ensure_calculated(future)
        unchanged = {name: self.rows(name) for name in ("weight_measurements", "profile", "profile_revisions", "foods")}
        midnight = datetime.combine(self.today, time.min)
        before_projection = self.context.daily.calculate_today_projection(datetime.combine(self.today, time(12)))
        before_fit = self.context.daily.fit_calibration(self.today, persist=False)
        with patch.object(self.context.daily, "ensure_calculated", wraps=self.context.daily.ensure_calculated) as calculate:
            self.context.update_intake(self.event, self.edit(occurred_at=midnight, amount=25))
        calculate.assert_called_once_with(future)
        self.context.update_exercise(self.exercise, self.exercise_edit(occurred_at=midnight, active_kj=20, duration_min=10))
        old = self.context.get_dashboard(old_day)
        today = self.context.get_dashboard(self.today)
        self.assertEqual((old.intake_kj, old.exercise_kj), (0, 0))
        self.assertEqual((today.intake_kj, today.exercise_kj), (100, 20))
        self.assertEqual(today.nutrition.nutrients.protein_g, 2.5)
        self.assertEqual(self.context.get_daily_nutrition(old_day).intake_count, 0)
        self.assertEqual(today.candles[-1].intake_kj, 100)
        self.assertEqual(sum(item.kj for item in today.treemap_items if item.side == "intake"), 100)
        self.assertNotEqual(before_projection.predicted_close_kg,
                            self.context.daily.calculate_today_projection(datetime.combine(self.today, time(12))).predicted_close_kg)
        fit = self.context.daily.fit_calibration(self.today, persist=False)
        self.assertTrue(fit.fitted)
        self.assertNotEqual(before_fit.rmse_kg, fit.rmse_kg)
        cached = {row["date"]: row for row in self.context.daily.list_metrics()}
        self.assertAlmostEqual(cached[self.today.isoformat()]["calibration_kj"], fit.calibration_kj_day)
        self.assertEqual(cached[future.isoformat()]["is_dirty"], 0)
        persisted = [row for row in self.rows("calibration_runs") if row["window_end"] == self.today.isoformat()]
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["weight_sample_count"], fit.weight_sample_count)
        self.assertAlmostEqual(persisted[0]["rmse"], fit.rmse_kg)
        self.assertAlmostEqual(persisted[0]["calibration_kj_day"], fit.calibration_kj_day)
        self.assertEqual({name: self.rows(name) for name in unchanged}, unchanged)
        self.assertIsNone(self.db.get_dirty_from_date())
        self.assertEqual((len(self.foods.list_intake_events()), len(self.exercises.list_exercise_events())), (1, 1))
        # The boundary date remains editable; no new 90-day horizon is introduced.
        self.context.update_intake(self.event, self.edit(occurred_at=self.when))
        self.assertEqual(self.foods.get_intake_event(self.event)["local_date"], self.day.isoformat())

    def test_rebuild_failure_keeps_committed_fact_dirty_and_supplies_retry_baseline(self):
        for kind in ("intake", "exercise"):
            with self.subTest(kind=kind):
                action = (lambda: self.context.update_intake(self.event, self.edit(amount=25))) if kind == "intake" else (
                    lambda: self.context.update_exercise(self.exercise, self.exercise_edit(active_kj=25))
                )
                with patch.object(self.context.daily, "_build_calculation_context", side_effect=RuntimeError("injected rebuild")):
                    with self.assertRaisesRegex(RecordRecalculationError, "记录已保存") as error:
                        action()
                self.assertEqual(error.exception.record.kind, kind)
                self.assertEqual(self.db.get_dirty_from_date(), self.day.isoformat())
                self.assertTrue(all(row["is_dirty"] for row in self.rows("daily_metrics_cache")))
                if kind == "intake":
                    self.assertEqual(error.exception.record.amount, 25)
                    self.assertEqual(self.foods.get_intake_event(self.event)["kj_snapshot"], 100)
                else:
                    self.assertEqual(error.exception.record.energy_kj, 25)
                self.context.daily.ensure_calculated(self.today)
                self.assertIsNone(self.db.get_dirty_from_date())
