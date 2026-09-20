# CAL-12 实现与独立验证交接

状态：**READY FOR LOCAL VALIDATION**。这不是测试通过或发布结论。

依据：Linear CAL-12 完整说明、用户确认的食品 provenance / 摄入快照三态规则，以及 2026-09-20 最新获批的 pre-CAL-12 v0.0.2 窄范围修复规则（见第 8 节）。
本轮仅完成本地源码、合成测试用例及文档修改和静态检查；未运行测试、GUI、构建或打包，未 commit / push / tag / 发布，未将 issue 标记 Done。
未读取或操作用户真实数据库、上传的备份，也未将真实备份加入仓库。

## 1. 修改文件

| 文件 | 修改内容 |
| --- | --- |
| `app/nutrition.py` | 食品来源判定、逐项兼容计算、摄入三态读取 |
| `app/services/food_service.py` | 查询附带 v3 迁移时间；新摄入与显式替换共用事务保存 |
| `app/services/recipe_service.py` | 原料兼容营养、预览和食谱快照沿用同一计算路径 |
| `app/services/intake_snapshot.py`（新增） | 原位替换或新增快照与 dirty 标记的原子保存 |
| `app/services/nutrition_service.py` | 每日营养使用兼容快照读取 |
| `app/services/treemap_service.py` | 营养详情使用同一快照读取；能量面积不变 |
| `app/db/migrations/schema.py` | 仅更新兼容策略注释；无 DDL / 迁移行为变更 |
| `app/db/database.py` | 已有 v3 初始化中持写锁检测、快照、原子修复；无命中不写入 |
| `app/db/migrations/snapshot.py` | 复用独立 SQLite 快照实现，分离迁移/修复命名和版本校验 |
| `app/db/nutrition_repair.py`（新增） | 获批谓词及仅五个字段的 UPDATE |
| `app/application.py` | 有效营养 DTO、时间线查询、编辑与跨日期重算 |
| `app/ui/context.py` | 时间线与编辑 DTO / UIContext 接口 |
| `app/ui/daily_timeline.py`（新增） | 日期时间线、当日营养摘要、饮食和运动编辑入口 |
| `app/ui/food_dialog.py` | 复用录入弹窗编辑历史饮食，明确区分快照缩放和来源替换 |
| `app/ui/exercise_dialog.py` | 复用录入弹窗编辑运动，默认保留历史项目快照 |
| `app/ui/food_library.py` | 单组有效营养展示与编辑，移除误导性旧字段说明 |
| `app/charts/candlestick.py` | 点击日期信号；日期轴和无障碍文案 |
| `app/ui/dashboard.py` | 打开时间线并联动刷新 |
| `tests/test_legacy_nutrition_compatibility.py`（新增） | 合成来源、三态、单位、快照覆盖 |
| `tests/test_daily_record_edits.py`（新增） | 历史编辑、来源替换、原子回滚、跨日期重算覆盖 |
| `tests/test_cal12_nutrition_repair.py`（新增） | 精确谓词、WAL 快照、失败零修改、回滚、幂等、旧备份恢复覆盖 |
| `tests/test_daily_timeline_ui.py`（新增） | 日期点击、弹窗编辑、展示精度覆盖 |
| `tests/test_nutrition_migration.py` | 修正旧快照一律未知的过时预期；保留无损迁移断言 |
| `tests/test_nutrition_ui.py` | 单组营养列与有效营养 DTO 预期 |
| `README.md` | 用户可见兼容规则和时间线说明 |
| `docs/WINDOWS_RELEASE.md` | 升级后兼容与人工验证说明 |
| `docs/CAL8_NUTRITION.md` | 标记旧兼容策略已被 CAL-12 修订 |
| `docs/CAL12_IMPLEMENTATION.md`（新增） | 本交接报告 |

## 2. 实现摘要与不变量

- `APP_VERSION = 0.0.2`、`SCHEMA_VERSION = 3`、`CALCULATION_VERSION = v2-simple-energy-kj-1` 保持不变。
- 不增加 schema，不回填食品、不由能量反推营养。历史读取不写库；仅第 8 节获批谓词命中的旧 v0.0.2 摄入在启动时复制其自身已有营养快照，不做一般性历史回填。
- 保留既有 kJ/kcal、体重模型、日 K 线 OHLC、Treemap 能量面积和备份逻辑。
- 显示营养两位小数，计算和存储不主动舍入，编辑复用已有精度保留控件。
- 普通编辑不访问当前食品/食谱内容来重建历史营养；来源被停用后仍可修改历史事实。

## 3. Legacy fallback 设计

### 食品来源

`legacy_food_available` 使用现有字段，不新增来源列：

- `is_builtin` 且有 `builtin_key`：按明确内置来源兼容。
- 其他食品：只有可比较的 `created_at < schema_version(version=3).applied_at` 才认定迁移前来源。
- 相等、缺失、非法时间戳或 naive/aware 混用时不猜测旧来源。v3-native 食品不会因为旧列默认 0 而变成已知营养。

### 每项营养

1. 非 NULL 的 v3 每 100g 值优先，明确零也是有效值。
2. 仅该项 v3 为 NULL 且来源符合兼容条件时，读取对应旧食品营养列，包括零。
3. v3 质量值按 `consumed_g / 100` 缩放。旧字段按 `consumed_base_amount / basis_amount` 缩放，沿用 g 或 ml 基准和已有份量映射。
4. 明确的每 100g 值不能用于 ml 摄入时，该项未知；不把 ml 当 g，不虚构密度，也不绕过非 NULL v3 值改取旧值。
5. 食谱逐项汇总有效贡献。混合 g/ml 食谱仍只能按整份比例录入，不改变单位语义。

### 历史摄入三态

| `nutrition_complete` | 读取规则 |
| --- | --- |
| NULL | legacy snapshot：逐项先取 v3 snapshot，缺失时取已有旧 snapshot，包括零 |
| 1 | 只读新快照；若数据异常缺项仍标记不完整，不用旧默认零补齐 |
| 0 | 新快照明确不完整；只读其已知部分，绝不回退旧 snapshot |

历史读取不查询当前食品库。新摄入从 legacy food 成功解析四项后，正常写入 v3 snapshot，`nutrition_complete=1`。
每日汇总、时间线摘要和 Treemap 营养详情共用 `intake_contribution`，避免口径分叉。

### 食品编辑器

移除隐藏旧字段输入区和“旧字段不参与每日营养汇总”说明，仅展示一组有效营养。
旧 g 食品显示换算后的每 100g 值；没有改动时保留原 NULL 元数据，不借保存进行回填。
旧 ml 食品保留原体积基准营养编辑。已有显式质量元数据的项标注无密度不能换算 ml。
兼容食品的 g/ml 维度锁定，避免单位切换把旧来源值重解释；需要不同维度时新建食品。原基准数量仍可修改。

## 4. 时间线与编辑设计

- 点击 K 线日期打开紧凑时间线，按现有本地时间语义排序，包含有效饮食、运动和称重。相同时间按类型/id 稳定排序。
- 展示保存时的名称、时间、份量/单位、餐次、能量及备注 tooltip；称重只读。
- K 线轴为“日期”，无障碍说明为“共 N 天”，不改底层日线模型。
- 饮食普通编辑允许改时间、餐次、数量和备注，能量与两套营养快照按原数量比例缩放，保留来源、单位和 completeness 三态。
- 用户勾选来源/单位替换并明确选择来源后，按当前食品/食谱和份量计算新快照，原位更新同一事件，保留 id / created_at / active，不先删除再插入。
- 运动普通编辑保留历史项目名称与来源；只有明确选择更换项目才更新项目快照。时长和额外能量由用户分别修正，不按时长自动推导消耗。
- 同一次事务内保存事实和最早受影响日期的 dirty 标记；显式替换的来源读取和快照计算也在该事务中。
- 应用层随后沿用已有派生重算 API，覆盖原日期、新日期和今天；时间线刷新后通知首页重新读取营养、Treemap、K 线和指标。
- 未修改的时间保留原时间戳/偏移，数量与能量保留原存储精度；来源更改之后，后续库编辑仍不影响历史。

## 5. 假设、边界与待确认项

1. 用户已确认旧零歧义无法恢复，对确定的旧来源视零为有效；本轮不尝试重新推断旧用户是否填过零。
2. 秒级创建时间恰好等于 v3 应用时间、时区域不一致或异常 provenance 的食品采取保守未知。是否需要针对人工确认来源的另行处理，留给后续审核，不新增 schema 或偷偷回填。
3. 一般 `nutrition_complete=0` 语义不变，即使 v3 数值齐全也不提升完整性。第 8 节是单独获批的初始化数据修复，要求四个 v3 值全部 NULL 且旧快照至少一项大于零；不是读取回退放宽。
4. 记录写入/dirty 失效在事务内；派生重算沿用现有独立缓存事务，不把耗时重算塞进事实写事务。缓存重算失败不能冒充写入回滚。
5. 真实 Windows Qt 点击、弹窗布局、显示缩放和全量回归尚未执行，不能宣称已有 187 项基线或新增用例通过。
6. CAL-9 发布阻断仍需独立审核和用户验证解除，不因本报告而自动放行。

## 6. 后续由 ChatGPT / 用户执行的验证

本节是交接清单，不是本轮执行记录。测试只用临时合成库和合成备份。

### 新增和更新的自动覆盖

- `tests/test_legacy_nutrition_compatibility.py`：合成 v1/v2 来源、新 v3 默认零、内置来源、边界时间、显式零优先、ml/serving 比例、快照三态、不回写历史及食谱快照。
- `tests/test_daily_record_edits.py`：停用来源后的缩放、份量/食谱替换、无效替换和失效标记异常回滚、运动项目更换、称重时间排序、跨日期实际重算。
- `tests/test_cal12_nutrition_repair.py`：第 8 节列明的修复安全与幂等回归；仅合成临时 SQLite 数据。
- `tests/test_daily_timeline_ui.py`：日期点击、编辑后刷新信号、未修改精度、明确来源选择、运动原值保留和单组旧营养编辑。
- 更新原迁移与 UI 测试后执行完整基线，特别复核能量、体重、食谱、快照、Treemap、精度和备份恢复。

### 人工 UI 验证

1. 用合成旧库升级；对照原基准和已有快照，确认有效零与旧 ml 均可用，未产生历史回填。
2. 新建营养空白的 v3 食品再录入，确认仍未知；逐项填明确零和数值后录入，确认新快照完整。
3. 点击多个 K 线日期、空记录日期；核对饮食/运动/称重顺序、日期轴和无障碍文本。
4. 编辑历史饮食的时间、餐次、数量、备注；再改库内容/停用来源，确认历史仍按原快照调整。
5. 明确更换食品、食谱或 serving，确认重算新快照、事件 id 不变、无重复记录。
6. 跨日期移动饮食和运动，核对原日/新日营养、首页、Treemap、K 线和 dirty/cache 收敛。
7. 切换 kJ/kcal，检查历史高精度数值打开再保存不被显示小数位截断。
8. 由用户按仓库交接流程运行 `package_caloriek_handoff.bat`；ChatGPT 审阅后生成专用 Windows 验证 BAT。打包/发布 smoke 属后续独立授权，不由本轮执行。

## 7. 本轮检查结论

仅进行源码及 diff 阅读、修改文件清单核对和 `git diff --check` 空白检查。第 8 节修复另对本次 4 个 Python 改动文件进行 AST 仅解析检查，通过；未导入项目、未打开数据库、未执行测试。
没有测试执行数据，没有真实数据库验证结论，没有 GUI/打包成功声明。

**READY FOR LOCAL VALIDATION**

## 8. 2026-09-20 获批的旧 v0.0.2 营养修复（尚未执行验证）

此前 CAL-8 构建已写入一类 FOOD/RECIPE 事件：明确不完整且 v3 四项为空，但旧快照实际有非零营养。根据 Linear 最新批准，仅对此类记录作显式数据修复，不改变一般三态读取函数，也不访问当前食品或食谱库。

### 精确谓词与写入字段

以下条件必须同时满足，检测与 UPDATE 共用同一个 SQL 谓词：

```sql
source_type IN ('FOOD', 'RECIPE')
AND nutrition_complete = 0
AND protein_g IS NULL AND fiber_g IS NULL AND fat_g IS NULL AND carbs_g IS NULL
AND (protein_snapshot > 0 OR fiber_snapshot > 0 OR fat_snapshot > 0 OR carb_snapshot > 0)
```

只执行 `protein_snapshot → protein_g`、`fiber_snapshot → fiber_g`、`fat_snapshot → fat_g`、`carb_snapshot → carbs_g` 和 `nutrition_complete = 1`。保留 ID、source、数量、单位、餐次、备注、能量、发生/本地日期、旧快照、created_at、updated_at、active。不改变缓存、dirty 状态、设置、自增序列或版本历史。

CUSTOM、全零旧快照、任一非 NULL v3 成分（包括明确零）、标记 NULL/1 的记录均排除。批准谓词没有 active、创建时间、应用版本或当前库来源条件，因此停用但命中的记录也会修复；不额外猜测或收窄范围。

### 初始化与快照顺序

1. 已有 schema-v3 先校验结构；保持现有 journal 模式，不提前作 DELETE → WAL 的持久头部修改。正常用户库原有 WAL 不变，新建/v1/v2 初始化及 ZIP 恢复 staging 仍沿用启用 WAL 的流程。
2. `BEGIN IMMEDIATE` 后重读版本、校验并检测谓词；无命中不创建备份，不执行 INSERT/UPDATE/DELETE，也不补写版本标记。
3. 有命中时持写锁，调用与迁移共用的 raw snapshot 实现：独立只读源连接的 SQLite backup API 包含已提交 WAL；目标转为独立 DELETE-journal SQLite，校验仍为完整 schema-v3，关闭连接，以可写句柄 fsync，再原子公布文件。
4. 文件位于目标数据库目录的 `backups/pre_repair_cal12_nutrition_<时间>_<唯一后缀>.sqlite3`；不同于 `pre_migration_v1_to_v2_*`、`pre_migration_v2_to_v3_*`。这是 raw SQLite，不是 ZIP；保留修复前的结构和全部值，不承诺与 WAL 主文件逐字节相同（已提交数据可能位于 WAL）。
5. 快照失败：停止初始化，尚未修改任何活库行；清理本次未完成快照，不删除既有快照。有完整快照后才用单条 UPDATE 修复全部匹配行并提交同一事务。
6. UPDATE 或提交失败则回滚全部修复，保留快照并在异常中报告路径；回滚异常也报告恢复路径。成功计数只在 commit 成功后记录到内存对象。
7. 仅谓词提供幂等性；成功后这些行不再命中。不增加 schema/表/列/设置标记。恢复旧备份可重新引入候选：原始 SQLite 恢复副本再次启动会重新快照、修复；ZIP 恢复在隔离候选库中完成同样处理，源 ZIP 保留，现库仍受原有 pre_restore 备份保护。候选库内部修复快照与其临时目录一同清理，不作为用户长期恢复副本。

### 新增合成测试（只编写，未运行）

- FOOD / RECIPE 分别只修复一次；四个旧营养维度各自为唯一正数也能命中。
- 全零旧快照、CUSTOM、每一个非 NULL v3 字段（含零）、NULL/1 标记不变；混合批次只改精确命中行，包括停用行。
- 原始完整 schema-v3 独立快照先于第一次写入公布；包含未 checkpoint 的 WAL，期间其他写者被阻止；与迁移命名严格分离。
- 临时文件创建、快照读/校验/fsync/公布故障均停止修复；DELETE 模式还检查主库字节完全不变；WAL 模式检查数据与模式不变。
- UPDATE 后强制故障、commit 故障完整回滚，保留原始快照；重试可以成功且不覆盖旧快照。
- 多次 initialize 无新快照、无 DML/DDL；其他表、缓存、设置、序列、时间戳和版本标识保持原值。
- 修复后的每日营养完整，能量不变；恢复原始 raw snapshot 可再次修复，旧 v3 ZIP 在 staging 中修复而不改源归档。

本次仅实现以上获批例外和合成测试，保留先前 `time.min` 测试稳定性修复。未运行测试、GUI、构建/打包，未提交、推送、打标签或发布；不把之前审查对旧源码的通过结论转用于本次变更。
