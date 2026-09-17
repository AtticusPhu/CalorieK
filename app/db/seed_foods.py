"""Versioned, idempotent built-in food catalogue."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.db.database import now_iso
from app.energy_units import kcal_to_kj

if TYPE_CHECKING:
    from app.db.database import Database


DATA_SOURCE = "CalorieK built-in reference values"
SOURCE_VERSION = "2026.08-v1"

# Values are practical reference averages per 100 g (or 100 ml for liquids), not
# medical claims. The reference catalogue is a kcal input boundary; energy is
# converted exactly once to canonical kJ when inserted. Users can customize a row;
# subsequent startup seeding will not
# overwrite the customized record because builtin_key is stable.
BUILTIN_FOODS: tuple[
    tuple[str, str, str, str, float, float, float, float, float, float], ...
] = (
    # key, name, category, unit, kcal, protein, fat, carbs, fiber, default serving
    ("rice-cooked", "大米饭", "主食", "g", 116, 2.6, 0.3, 25.9, 0.3, 150),
    ("brown-rice-cooked", "糙米", "主食", "g", 111, 2.6, 0.9, 23.0, 1.8, 150),
    ("mixed-grain-rice", "杂粮饭", "主食", "g", 118, 3.2, 0.8, 24.5, 1.7, 150),
    ("noodles-cooked", "面条", "主食", "g", 110, 3.5, 0.5, 22.8, 1.1, 200),
    ("mantou", "馒头", "主食", "g", 223, 7.0, 1.1, 47.0, 1.3, 100),
    ("bread", "面包", "主食", "g", 265, 9.0, 3.2, 49.0, 2.7, 35),
    ("oats-dry", "燕麦", "主食", "g", 379, 13.2, 6.5, 67.7, 10.1, 40),
    ("sweet-potato", "红薯", "主食", "g", 86, 1.6, 0.1, 20.1, 3.0, 150),
    ("corn", "玉米", "主食", "g", 112, 4.0, 1.2, 22.8, 2.9, 150),
    ("beef-lean", "牛肉", "肉类", "g", 250, 26.0, 15.0, 0.0, 0.0, 100),
    ("pork-lean", "猪肉", "肉类", "g", 242, 27.0, 14.0, 0.0, 0.0, 100),
    ("chicken-breast", "鸡胸肉", "肉类", "g", 165, 31.0, 3.6, 0.0, 0.0, 120),
    ("chicken-thigh", "鸡腿肉", "肉类", "g", 209, 26.0, 10.9, 0.0, 0.0, 120),
    ("fish-generic", "鱼", "肉类", "g", 128, 20.5, 4.5, 0.0, 0.0, 120),
    ("salmon", "三文鱼", "肉类", "g", 208, 20.4, 13.4, 0.0, 0.0, 120),
    ("shrimp", "虾", "肉类", "g", 99, 24.0, 0.3, 0.2, 0.0, 120),
    ("egg", "鸡蛋", "蛋奶", "g", 143, 12.6, 9.5, 0.7, 0.0, 50),
    ("milk-whole", "牛奶", "蛋奶", "ml", 61, 3.2, 3.3, 4.8, 0.0, 250),
    ("yogurt-plain", "酸奶", "蛋奶", "ml", 72, 3.5, 3.3, 8.5, 0.0, 200),
    ("broccoli", "西兰花", "蔬菜", "g", 34, 2.8, 0.4, 6.6, 2.6, 150),
    ("leafy-greens", "青菜", "蔬菜", "g", 18, 1.5, 0.3, 3.1, 1.2, 150),
    ("spinach", "菠菜", "蔬菜", "g", 23, 2.9, 0.4, 3.6, 2.2, 150),
    ("tomato", "番茄", "蔬菜", "g", 18, 0.9, 0.2, 3.9, 1.2, 150),
    ("cucumber", "黄瓜", "蔬菜", "g", 15, 0.7, 0.1, 3.6, 0.5, 150),
    ("green-pepper", "青椒", "蔬菜", "g", 20, 0.9, 0.2, 4.6, 1.7, 100),
    ("potato", "土豆", "蔬菜", "g", 77, 2.0, 0.1, 17.5, 2.2, 150),
    ("cabbage", "卷心菜", "蔬菜", "g", 25, 1.3, 0.1, 5.8, 2.5, 150),
    ("apple", "苹果", "水果", "g", 52, 0.3, 0.2, 13.8, 2.4, 180),
    ("banana", "香蕉", "水果", "g", 89, 1.1, 0.3, 22.8, 2.6, 120),
    ("orange", "橙子", "水果", "g", 47, 0.9, 0.1, 11.8, 2.4, 180),
    ("avocado", "牛油果", "水果", "g", 160, 2.0, 14.7, 8.5, 6.7, 100),
    ("peanut-oil", "花生油", "油脂", "g", 884, 0.0, 100.0, 0.0, 0.0, 10),
    ("rapeseed-oil", "菜籽油", "油脂", "g", 884, 0.0, 100.0, 0.0, 0.0, 10),
    ("olive-oil", "橄榄油", "油脂", "g", 884, 0.0, 100.0, 0.0, 0.0, 10),
    ("butter", "黄油", "油脂", "g", 717, 0.9, 81.1, 0.1, 0.0, 10),
    ("sugar", "白糖", "其它", "g", 387, 0.0, 0.0, 100.0, 0.0, 10),
    ("mixed-nuts", "坚果", "其它", "g", 607, 20.0, 54.0, 21.0, 7.0, 30),
    ("tofu", "豆腐", "其它", "g", 76, 8.1, 4.8, 1.9, 0.3, 150),
    ("soy-milk", "豆浆", "其它", "ml", 33, 3.0, 1.6, 1.8, 0.6, 250),
)


def _seed(connection: sqlite3.Connection, foods: Iterable[tuple] = BUILTIN_FOODS) -> int:
    timestamp = now_iso()
    before = connection.total_changes
    connection.executemany(
        """
        INSERT OR IGNORE INTO foods(
            builtin_key, name, category, brand, basis_amount, basis_unit,
            kj, protein_g, fat_g, carb_g, fiber_g, default_serving,
            is_builtin, is_favorite, user_modified, data_source,
            source_version, created_at, updated_at, active
        ) VALUES (?, ?, ?, NULL, 100, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0, ?, ?, ?, ?, 1)
        """,
        (
            (
                key,
                name,
                category,
                unit,
                kcal_to_kj(kcal),
                protein,
                fat,
                carbs,
                fiber,
                serving,
                DATA_SOURCE,
                SOURCE_VERSION,
                timestamp,
                timestamp,
            )
            for (
                key,
                name,
                category,
                unit,
                kcal,
                protein,
                fat,
                carbs,
                fiber,
                serving,
            ) in foods
        ),
    )
    # ``foods.default_serving`` is the human-friendly amount shown by the V1
    # catalogue.  Materialize it as a named serving as well so the intake UI can
    # route the choice through ``serving_id`` and preserve a clear "1 份" event
    # snapshot.  INSERT OR IGNORE keeps existing/customized serving rows intact.
    connection.execute(
        """
        INSERT OR IGNORE INTO food_servings(
            food_id, name, serving_amount, serving_unit, base_amount,
            created_at, updated_at, active
        )
        SELECT id, '默认份', 1, 'serving', default_serving, ?, ?, 1
        FROM foods
        WHERE default_serving IS NOT NULL
        """,
        (timestamp, timestamp),
    )
    return connection.total_changes - before


def seed_builtin_foods(target: sqlite3.Connection | Database) -> int:
    """Seed built-ins without duplicating or overwriting user-edited rows."""

    if isinstance(target, sqlite3.Connection):
        return _seed(target)
    with target.transaction() as connection:
        return _seed(connection)
