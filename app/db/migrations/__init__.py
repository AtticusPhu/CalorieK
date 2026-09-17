"""Public schema and migration helpers for the persistence layer."""

from .schema import (
    ENERGY_COLUMN_MIGRATIONS,
    NUTRITION_COLUMN_MIGRATIONS,
    execute_schema,
    migrate_v1_to_v2,
    migrate_v2_to_v3,
    schema_signature,
    schema_sql_for_version,
    validate_schema,
)

__all__ = [
    "ENERGY_COLUMN_MIGRATIONS",
    "NUTRITION_COLUMN_MIGRATIONS",
    "execute_schema",
    "migrate_v1_to_v2",
    "migrate_v2_to_v3",
    "schema_signature",
    "schema_sql_for_version",
    "validate_schema",
]
