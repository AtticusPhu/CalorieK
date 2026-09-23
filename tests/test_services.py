from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.db import Database
from app.energy_units import kcal_to_kj
from app.services import ExerciseService, ProfileService, WeightService


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._temporary_directory.name) / "caloriek.sqlite3")
        self.db.initialize(seed_foods=False)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_profile_initialization_and_effective_dated_revisions(self) -> None:
        profiles = ProfileService(self.db)
        measurement_id = profiles.create_profile(
            gender="男",
            birth_date="1990-06-15",
            height_cm=175,
            current_weight_kg=70,
            wake_time="07:30",
            sleep_time="23:15",
            measured_at="2026-08-10T07:30:00+08:00",
        )
        self.assertTrue(profiles.is_initialized())
        self.assertEqual(len(profiles.list_revisions()), 1)
        with self.db.connection() as connection:
            weight = connection.execute(
                "SELECT * FROM weight_measurements WHERE id = ?", (measurement_id,)
            ).fetchone()
        # New event writes use canonical recorded-local clocks (CAL-16).
        self.assertEqual(weight["occurred_at"], "2026-08-10T07:30:00")
        self.assertEqual(weight["local_date"], "2026-08-10")
        self.assertEqual(weight["weight_kg"], 70.0)

        self.db.clear_dirty()
        revision_id = profiles.update_profile(
            effective_from="2026-08-15",
            height_cm=176,
            awake_multiplier=1.25,
        )
        self.assertIsInstance(revision_id, int)
        self.assertEqual(profiles.get_revision_for_date("2026-08-14")["height_cm"], 175.0)
        self.assertEqual(profiles.get_revision_for_date("2026-08-15")["height_cm"], 176.0)
        self.assertEqual(self.db.get_dirty_from_date(), "2026-08-15")
        self.assertEqual(ProfileService.age_on("2000-08-26", date(2026, 8, 25)), 25)
        with self.assertRaisesRegex(ValueError, "already initialized"):
            profiles.create_profile(
                gender="female",
                birth_date="1995-01-01",
                height_cm=165,
                current_weight_kg=60,
            )

    def test_invalid_initial_schedule_leaves_no_profile_or_weight_rows(self) -> None:
        profiles = ProfileService(self.db)

        for wake_time, sleep_time in (
            ("07:00", "07:00"),
            ("07:00+08:00", "07:00+09:00"),
        ):
            with self.subTest(wake_time=wake_time, sleep_time=sleep_time):
                with self.assertRaisesRegex(
                    ValueError, "wake_time and sleep_time must differ"
                ):
                    profiles.create_profile(
                        gender="male",
                        birth_date="1990-06-15",
                        height_cm=175,
                        current_weight_kg=70,
                        wake_time=wake_time,
                        sleep_time=sleep_time,
                        measured_at="2026-08-10T07:30:00+08:00",
                    )

        with self.db.connection() as connection:
            counts = tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("profile", "profile_revisions", "weight_measurements")
            )
        self.assertEqual(counts, (0, 0, 0))
        self.assertIsNone(self.db.get_dirty_from_date())

    def test_invalid_schedule_update_preserves_existing_revision(self) -> None:
        profiles = ProfileService(self.db)
        profiles.create_profile(
            gender="male",
            birth_date="1990-06-15",
            height_cm=175,
            current_weight_kg=70,
            wake_time="07:00",
            sleep_time="23:00",
            effective_from="2026-08-10",
            measured_at="2026-08-10T07:30:00+08:00",
        )
        original_revision = profiles.get_revision_for_date("2026-08-10")
        original_profile = profiles.get_profile()
        self.db.clear_dirty()

        with self.assertRaisesRegex(
            ValueError, "wake_time and sleep_time must differ"
        ):
            profiles.update_profile(
                effective_from="2026-08-10",
                height_cm=180,
                wake_time="23:00",
                note="must not be committed",
                update_note=True,
            )
        with self.assertRaisesRegex(
            ValueError, "wake_time and sleep_time must differ"
        ):
            profiles.add_revision(
                effective_from="2026-08-11",
                sleep_time="07:00",
            )

        self.assertEqual(
            profiles.get_revision_for_date("2026-08-10"), original_revision
        )
        self.assertEqual(profiles.get_profile(), original_profile)
        self.assertEqual(len(profiles.list_revisions()), 1)
        self.assertIsNone(self.db.get_dirty_from_date())

    def test_exercise_last_value_snapshot_and_soft_delete(self) -> None:
        exercises = ExerciseService(self.db)
        running_id = exercises.create_exercise_type(
            name="跑步",
            default_duration_min=45,
            default_active_kj=kcal_to_kj(420),
            favorite=True,
        )
        first_id = exercises.record_exercise(
            running_id,
            occurred_at="2026-08-20T18:00:00",
        )
        first = exercises.get_exercise_event(first_id)
        self.assertEqual(first["duration_min"], 45.0)
        self.assertEqual(first["active_kj"], kcal_to_kj(420.0))
        self.assertEqual(first["name_snapshot"], "跑步")

        exercises.update_exercise_type(
            running_id,
            name="户外跑步",
            default_duration_min=30,
            default_active_kj=kcal_to_kj(250),
        )
        self.assertEqual(exercises.get_last_values(running_id), {
            "duration_min": 45.0,
            "active_kj": kcal_to_kj(420.0),
        })
        second_id = exercises.record_exercise(
            running_id,
            occurred_at="2026-08-21T18:00:00",
        )
        self.assertEqual(exercises.get_exercise_event(second_id)["name_snapshot"], "户外跑步")
        self.assertEqual(exercises.get_exercise_event(first_id)["name_snapshot"], "跑步")

        exercises.soft_delete_exercise_type(running_id)
        self.assertEqual(exercises.list_exercise_types(), [])
        self.assertEqual(len(exercises.list_exercise_events()), 2)
        # The source shortcut can be inactive while its historical event remains
        # correctable and keeps the original name snapshot.
        exercises.update_exercise_event(first_id, active_kj=kcal_to_kj(400))
        corrected = exercises.get_exercise_event(first_id)
        self.assertEqual(corrected["active_kj"], kcal_to_kj(400.0))
        self.assertEqual(corrected["name_snapshot"], "跑步")
        exercises.soft_delete_exercise_event(first_id)
        self.assertIsNone(exercises.get_exercise_event(first_id, include_inactive=False))
        exercises.restore_exercise_event(first_id)
        self.assertIsNotNone(exercises.get_exercise_event(first_id, include_inactive=False))

    def test_weight_crud_anchor_resolution_and_dirty_date(self) -> None:
        weights = WeightService(self.db)
        morning_id = weights.record_weight(
            70.1,
            occurred_at="2026-08-20T07:30:00+08:00",
            note="morning",
        )
        evening_id = weights.record_measurement(
            69.8,
            occurred_at="2026-08-20T22:30:00+08:00",
            anchor="CLOSE",
        )
        self.assertEqual(WeightService.resolve_anchor("AUTO", "2026-08-20T07:30:00"), "OPEN")
        self.assertEqual(WeightService.resolve_anchor("AUTO", "2026-08-20T12:00:00"), "CLOSE")
        self.assertEqual(WeightService.resolve_anchor("OPEN", "2026-08-20T22:30:00"), "OPEN")
        self.assertEqual(
            [row["id"] for row in weights.list_measurements(local_date="2026-08-20")],
            [morning_id, evening_id],
        )
        self.assertEqual(weights.latest_measurement()["id"], evening_id)

        self.db.clear_dirty()
        weights.update_measurement(
            evening_id,
            weight_kg=69.7,
            occurred_at="2026-08-18T22:30:00+08:00",
            note="corrected",
            update_note=True,
        )
        corrected = weights.get_measurement(evening_id)
        self.assertEqual(corrected["local_date"], "2026-08-18")
        self.assertEqual(corrected["note"], "corrected")
        self.assertEqual(self.db.get_dirty_from_date(), "2026-08-18")

        weights.soft_delete_measurement(evening_id)
        self.assertIsNone(weights.get_measurement(evening_id, include_inactive=False))
        self.assertEqual(weights.latest_measurement()["id"], morning_id)
        weights.restore_measurement(evening_id)
        self.assertIsNotNone(weights.get_measurement(evening_id, include_inactive=False))
        self.assertEqual(weights.latest_measurement()["id"], morning_id)

    def test_service_input_validation(self) -> None:
        weights = WeightService(self.db)
        with self.assertRaises(ValueError):
            weights.record_weight(float("nan"))
        with self.assertRaises(ValueError):
            weights.record_weight(70, anchor="unknown")
        with self.assertRaises(ValueError):
            weights.list_measurements(
                local_date="2026-08-20",
                start_date="2026-08-01",
            )
        with self.assertRaises(ValueError):
            ProfileService(self.db).create_profile(
                gender="male",
                birth_date="2030-01-01",
                height_cm=175,
                current_weight_kg=70,
                measured_at="2026-08-20T08:00:00",
            )
        with self.assertRaises(ValueError):
            ExerciseService(self.db).create_exercise_type(
                name="无效运动", default_active_kj=float("inf")
            )


if __name__ == "__main__":
    unittest.main()
