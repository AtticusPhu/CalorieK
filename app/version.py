"""Release, storage and model identifiers have independent lifecycles.

APP_VERSION identifies the public application release. SCHEMA_VERSION describes
the SQLite layout. CALCULATION_VERSION identifies compatible derived results:
generation v2 uses canonical kJ with schema v2. Documentation/display changes
do not require a model-version bump or invalidate already calculated rows.
"""

APP_NAME = "CalorieK"
APP_VERSION = "0.0.1"
APP_DISPLAY_NAME = f"{APP_NAME} v{APP_VERSION} · 体重热量 K 线"
CALCULATION_VERSION = "v2-simple-energy-kj-1"
SCHEMA_VERSION = 2
