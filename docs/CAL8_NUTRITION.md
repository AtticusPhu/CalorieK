# CAL-8：每日营养构成交接

> 历史交接记录：其中“旧值不参与汇总、历史快照一律未知”的策略已由
> [CAL-12 兼容性与时间线说明](CAL12_IMPLEMENTATION.md) 修订。迁移仍不回填旧事实，
> 读取时按食品 provenance 和摄入快照三态复用有效兼容数据。

本次为 v0.0.2 源码实现；未执行测试、GUI、打包或发布，也未访问真实用户数据库。测试须由用户交接给 ChatGPT 审核后，按任务专用 Windows BAT 执行。不要将本文视为执行这些命令的授权。

## 修改文件（26 个）

- 迁移及恢复：`app/db/database.py`、`app/db/migrations/__init__.py`、`app/db/migrations/schema.py`、`app/db/migrations/snapshot.py`、`app/services/backup_service.py`。
- 营养领域与服务：新增 `app/nutrition.py`、`app/services/nutrition_service.py`；修改 `app/services/food_service.py`、`app/services/recipe_service.py`、`app/application.py`。
- 界面：新增 `app/ui/nutrition.py`；修改 `app/ui/context.py`、`app/ui/dashboard.py`、`app/ui/food_library.py`、`app/ui/recipe_editor.py`。沿用原生 Qt、现有样式与精度保留控件，文字明确区分未知/零/已知部分，不依赖颜色表示缺失。
- 版本与文档：`app/version.py`、`README.md`、`docs/WINDOWS_RELEASE.md`、本文件。
- 新测试：`tests/test_nutrition.py`、`tests/test_nutrition_migration.py`、`tests/test_nutrition_ui.py`。
- 更新旧断言：`tests/test_display_settings.py`、`tests/test_energy_migration.py`、`tests/test_migration_snapshot.py`、`tests/test_version_metadata.py`。

## 数据契约

`foods` 新增 nullable REAL：`protein_g_per_100g`、`fiber_g_per_100g`、`fat_g_per_100g`、`carbs_g_per_100g`。

`intake_events` 新增 nullable REAL：`protein_g`、`fiber_g`、`fat_g`、`carbs_g`；另有 nullable INTEGER `nutrition_complete`（0/1）。所有新增列无默认零；历史列和数据全部保留。

NULL 表示未知，0 表示明确已知为零。新摄入保存按质量/份量算出的成分；食谱按每个维度累加可用值。食谱所有维度都有已知部分也不一定完整，因此额外保存完整性标记：任一原料缺少任何成分或无法从 ml 换算成 g，标记为 0。历史标记为 NULL。

每日仅查询所选 `local_date` 的 active 摄入快照，不联查当前食品或食谱。完整性标记不为 1 或任何成分为 NULL 时，日汇总显示准确文本 `部分记录无营养数据`，可用数字说明为已知部分合计、不是完整总量。全部未知与没有记录是不同状态。

内置食品旧营养列有默认零，无法可靠区别未知，不复制到新列。UI 保留旧字段的兼容编辑/展示，同时明确它们不参与新的每日营养。体积食品无密度，不按 ml=g 猜测。上述两点是保守实现边界，若要扩展需另行任务。

## 迁移与恢复

1. 保留 `schema.sql` 的 v2 基础契约；同一组 ADD COLUMN DDL 用于新 v3 库和 v2 升级，校验契约一致。
2. 初始化先拒绝不完整、损坏、错误标记或较新 schema。取得写锁后重读版本，防止并发重复迁移。
3. 升级 v2 前以独立只读连接及 SQLite backup API 创建包括已提交 WAL 的原始快照，命名 `pre_migration_v2_to_v3_*.sqlite3`。完整校验、独立 journal 和 fsync 后才公布快照文件。
4. 同一事务追加字段、记录版本 3。无 UPDATE/DELETE、能量转换或缓存失效；失败回滚，原始安全快照仍保留。v1 沿用已审查的 1→2 路径，再在同一事务中追加 v3。
5. 原 v2 和当前 v3 启动不补种食品、默认份量和运动快捷项，避免更改数据或推进自增序列。重复初始化不再迁移；新建库仍保留内置食品/份量/运动初始化流程。
6. ZIP 恢复接受 v1/v2/v3，先在隔离候选库升级并校验，随后 staging、现库安全备份和替换；源 ZIP 不改变，候选失败不会覆盖现库。WAL checkpoint / Windows 替换保护沿用现有逻辑。

APP_VERSION 为 `0.0.2`，SCHEMA_VERSION 为 `3`，CALCULATION_VERSION 保持 `v2-simple-energy-kj-1`。新增营养模块不调用能量或体重模型，不以营养计算 kJ，也不使旧能量缓存失效。

## 待独立验证（未执行）

- 新增 `tests.test_nutrition_migration`：全部旧列/行/时间戳/设置/序列/缓存保留，历史 NULL，重复启动，新建/升级契约一致，WAL 原始快照，部分 DDL 失败回滚、恢复副本、快照失败、非法 schema 拒绝、v2 staging restore 成功/失败、v3 快照精度往返。
- 新增 `tests.test_nutrition`：NULL/零，非有限/负数拒绝，每 100g 与能量基准独立，g/份量/ml，完整/部分/未知食谱，历史不变，编辑量按快照缩放，软删除/恢复、日期过滤，只读汇总，两位显示与原精度。
- 新增 `tests.test_nutrition_ui`：可空输入，编辑保存精度，食品库未知/零，日汇总精确缺失文本，历史日期独立，kJ/kcal 切换不重新查询营养或重算能量，读取失败清除旧数字。
- 回归既有 `test_energy_migration`、`test_migration_snapshot`、`test_backup_service`、`test_display_settings`、`test_version_metadata`；最新 schema 断言随 v3 更新，但真正 v1/v2 契约及能量换算断言保留。
- 回归 `test_application_context`、食品/食谱快照、缓存、投影、图表单位和数值输入用例，确保新字段不改变原有 kJ、实际体重、预测、K 线、Treemap 与快捷录入行为。
- Windows 人工检查：食品编辑器滚动/键盘焦点与“已知”切换，配方已知部分标注，首页卡片在不同 DPI/窗口大小下的可读性，以及跨午夜日期跟随。不要仅凭离屏 Qt 用例判断原生 UI 通过。
- Windows 数据验证只用审核后的 v0.0.1 副本和临时目录；检查有/无 WAL、迁移错误提示、重启、v1/v2/v3 恢复，以及未授权降级被拒绝。真实用户库操作需另行批准并预先备份。

仍待确认的是运行时与 Windows 原生表现；源码检查不能替代上述验证。发布与实际资产重新下载验证不属于此次实现。
