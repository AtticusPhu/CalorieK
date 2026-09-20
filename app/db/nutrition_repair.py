"""The exact CAL-12 exception for snapshots written by the broken CAL-8 build.

This is a startup data repair, not a relaxation of nutrition_complete=0 reads.
No catalogue lookup, time/provenance guess, active filter, or extra marker is used.
"""

import sqlite3


CAL12_REPAIR_PREDICATE = """
    source_type IN ('FOOD', 'RECIPE')
    AND nutrition_complete = 0
    AND protein_g IS NULL AND fiber_g IS NULL AND fat_g IS NULL AND carbs_g IS NULL
    AND (protein_snapshot > 0 OR fiber_snapshot > 0 OR fat_snapshot > 0 OR carb_snapshot > 0)
"""


def has_cal12_nutrition_repair_candidates(connection: sqlite3.Connection) -> bool:
    """Inspect under the caller's writer lock, before any initialization writes."""
    return connection.execute(
        f"SELECT 1 FROM intake_events WHERE {CAL12_REPAIR_PREDICATE} LIMIT 1"
    ).fetchone() is not None


def repair_cal12_nutrition(connection: sqlite3.Connection) -> int:
    """Copy only the approved fields; caller owns the snapshot and transaction.

    A successful update removes its own candidates. Restoring an old backup can
    legitimately reintroduce them; no schema/settings marker should suppress it.
    """
    if not connection.in_transaction:
        raise RuntimeError("CAL-12 nutrition repair requires the startup transaction")
    cursor = connection.execute(
        f"""
        UPDATE intake_events SET
            protein_g = protein_snapshot,
            fiber_g = fiber_snapshot,
            fat_g = fat_snapshot,
            carbs_g = carb_snapshot,
            nutrition_complete = 1
        WHERE {CAL12_REPAIR_PREDICATE}
        """
    )
    return cursor.rowcount
