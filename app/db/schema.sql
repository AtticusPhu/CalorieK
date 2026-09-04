PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    gender TEXT NOT NULL CHECK (gender IN ('male', 'female')),
    birth_date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL DEFAULT 1 REFERENCES profile(id),
    effective_from TEXT NOT NULL,
    gender TEXT NOT NULL CHECK (gender IN ('male', 'female')),
    birth_date TEXT NOT NULL,
    height_cm REAL NOT NULL CHECK (height_cm > 0),
    wake_time TEXT NOT NULL,
    sleep_time TEXT NOT NULL,
    awake_multiplier REAL NOT NULL CHECK (awake_multiplier > 0),
    sleep_multiplier REAL NOT NULL CHECK (sleep_multiplier > 0),
    bmr_formula TEXT NOT NULL DEFAULT 'mifflin_st_jeor',
    created_at TEXT NOT NULL,
    UNIQUE (profile_id, effective_from)
);

CREATE INDEX IF NOT EXISTS idx_profile_revisions_effective
    ON profile_revisions(profile_id, effective_from DESC);

CREATE TABLE IF NOT EXISTS weight_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    weight_kg REAL NOT NULL CHECK (weight_kg > 0),
    anchor TEXT NOT NULL DEFAULT 'AUTO' CHECK (anchor IN ('AUTO', 'OPEN', 'CLOSE')),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_weight_date_time
    ON weight_measurements(local_date, occurred_at);

CREATE TABLE IF NOT EXISTS foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    builtin_key TEXT UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT,
    basis_amount REAL NOT NULL DEFAULT 100 CHECK (basis_amount > 0),
    basis_unit TEXT NOT NULL CHECK (basis_unit IN ('g', 'ml')),
    kcal REAL NOT NULL CHECK (kcal >= 0),
    protein_g REAL NOT NULL DEFAULT 0 CHECK (protein_g >= 0),
    fat_g REAL NOT NULL DEFAULT 0 CHECK (fat_g >= 0),
    carb_g REAL NOT NULL DEFAULT 0 CHECK (carb_g >= 0),
    fiber_g REAL NOT NULL DEFAULT 0 CHECK (fiber_g >= 0),
    default_serving REAL,
    is_builtin INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1)),
    user_modified INTEGER NOT NULL DEFAULT 0 CHECK (user_modified IN (0, 1)),
    data_source TEXT,
    source_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_foods_active_name ON foods(active, name);
CREATE INDEX IF NOT EXISTS idx_foods_favorite ON foods(is_favorite, active);

CREATE TABLE IF NOT EXISTS food_servings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id INTEGER NOT NULL REFERENCES foods(id),
    name TEXT NOT NULL,
    serving_amount REAL NOT NULL DEFAULT 1 CHECK (serving_amount > 0),
    serving_unit TEXT NOT NULL DEFAULT 'serving',
    base_amount REAL NOT NULL CHECK (base_amount > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    UNIQUE (food_id, name)
);

CREATE INDEX IF NOT EXISTS idx_food_servings_food ON food_servings(food_id, active);

CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_recipes_active_name ON recipes(active, name);

CREATE TABLE IF NOT EXISTS recipe_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES recipes(id),
    food_id INTEGER NOT NULL REFERENCES foods(id),
    amount REAL NOT NULL CHECK (amount > 0),
    unit TEXT NOT NULL CHECK (unit IN ('g', 'ml')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_recipe_items_recipe ON recipe_items(recipe_id, active);

CREATE TABLE IF NOT EXISTS intake_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('FOOD', 'RECIPE', 'CUSTOM')),
    source_id INTEGER,
    meal_type TEXT NOT NULL DEFAULT 'OTHER'
        CHECK (meal_type IN ('BREAKFAST', 'LUNCH', 'DINNER', 'SNACK', 'OTHER')),
    name_snapshot TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    unit TEXT NOT NULL,
    kcal_snapshot REAL NOT NULL CHECK (kcal_snapshot >= 0),
    protein_snapshot REAL NOT NULL DEFAULT 0 CHECK (protein_snapshot >= 0),
    fat_snapshot REAL NOT NULL DEFAULT 0 CHECK (fat_snapshot >= 0),
    carb_snapshot REAL NOT NULL DEFAULT 0 CHECK (carb_snapshot >= 0),
    fiber_snapshot REAL NOT NULL DEFAULT 0 CHECK (fiber_snapshot >= 0),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_intake_date_time ON intake_events(local_date, occurred_at);
CREATE INDEX IF NOT EXISTS idx_intake_source ON intake_events(source_type, source_id);

CREATE TABLE IF NOT EXISTS exercise_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    default_duration_min REAL CHECK (default_duration_min IS NULL OR default_duration_min >= 0),
    default_active_kcal REAL CHECK (default_active_kcal IS NULL OR default_active_kcal >= 0),
    favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_exercise_types_active_name
    ON exercise_types(active, name);

CREATE TABLE IF NOT EXISTS exercise_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    exercise_type_id INTEGER REFERENCES exercise_types(id),
    name_snapshot TEXT NOT NULL,
    duration_min REAL NOT NULL CHECK (duration_min >= 0),
    active_kcal REAL NOT NULL CHECK (active_kcal >= 0),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_exercise_date_time
    ON exercise_events(local_date, occurred_at);

CREATE TABLE IF NOT EXISTS calibration_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    weight_sample_count INTEGER NOT NULL CHECK (weight_sample_count >= 0),
    days_span INTEGER NOT NULL CHECK (days_span >= 0),
    calibration_kcal_day REAL NOT NULL,
    rmse REAL,
    model_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calibration_window
    ON calibration_runs(window_end DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS daily_metrics_cache (
    date TEXT PRIMARY KEY,
    open_kg REAL,
    high_kg REAL,
    low_kg REAL,
    close_kg REAL,
    open_source TEXT,
    close_source TEXT,
    actual_weight_count INTEGER NOT NULL DEFAULT 0 CHECK (actual_weight_count >= 0),
    intake_kcal REAL NOT NULL DEFAULT 0,
    baseline_kcal REAL NOT NULL DEFAULT 0,
    exercise_kcal REAL NOT NULL DEFAULT 0,
    total_burn_kcal REAL NOT NULL DEFAULT 0,
    balance_kcal REAL NOT NULL DEFAULT 0,
    predicted_close_kg REAL,
    calibration_kcal REAL NOT NULL DEFAULT 0,
    calculation_version TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    is_dirty INTEGER NOT NULL DEFAULT 0 CHECK (is_dirty IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_daily_cache_dirty ON daily_metrics_cache(is_dirty, date);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recalculation_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    dirty_from_date TEXT,
    updated_at TEXT NOT NULL
);
