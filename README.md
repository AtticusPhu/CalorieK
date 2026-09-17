# CalorieK v0.0.2（源码准备，尚待独立验证）

CalorieK 是一个面向 Windows 10/11 x64 的本地离线桌面程序，用股票日 K 线的视觉逻辑记录实际体重，并把摄入、基础/日常消耗与额外运动消耗用于当日预测和个人能量模型校准。当前源码为 `0.0.2`，在 v0.0.1 基础上增加独立的每日营养构成；应用、schema 和能量模型版本相互独立。本次源码修改不表示已经测试、打包或发布。

核心原则是：**实际称重是事实，理论模型只负责预测和补足单次称重的 K 线缺失端。** 预测值不会写入实际称重表；饮食、食谱、运动和资料版本均长期保留为可重算的原始数据。

## Windows 用户：下载与运行

1. 打开 [GitHub Releases](https://github.com/AtticusPhu/CalorieK/releases)，选择实际已发布版本的 Windows x64 ZIP。v0.0.2 源码元数据不代表已有可下载资产；旧版 v0.0.1 不包含本页新增的每日营养功能。
2. **解压整个 ZIP** 到可访问的文件夹；不要直接在压缩包内启动，也不要只复制 EXE。
3. 打开解压后的 `CalorieK` 文件夹，运行 `CalorieK.exe`；保留同级 `_internal` 等依赖目录。

打包版**不需要安装 Python**，运行时无需联网、账号或云服务。实际是否已发布以 Releases 中的资产为准；如果尚未提供上述 ZIP，请等待发布，不要把 GitHub 自动生成的 `Source code` ZIP 当作 Windows 程序。

首次启动会引导建立个人资料。默认数据目录是 `%LOCALAPPDATA%\CalorieK\data`，不在解压目录中。升级、备份和常见启动问题见 [Windows Release 使用说明](docs/WINDOWS_RELEASE.md)。

## 已实现功能

- 首次启动录入性别、出生日期、身高、当前体重、作息和活动系数。
- 39 种版本化内置食品；支持自定义、收藏、软删除和 serving 换算。
- 自定义食谱按 `g` / `ml` 分别汇总；单一单位食谱计算每 100 g / 100 ml 营养并支持按量或比例录入，混合单位食谱仅按整份比例录入。
- 新饮食事件保存蛋白质、膳食纤维、脂肪、碳水化合物的可空快照；后续修改食品或食谱不会改变历史。每日汇总可独立选择历史日期，缺失数据明确标示，见下方“每日营养”。
- 可新建、编辑、收藏、停用/恢复运动快捷项目；录入时带入最近一次时长与能量。运动能量表示额外运动消耗（Active Calories），不重复包含基础/日常消耗。
- 实际体重按真实时间保存，并支持 `AUTO / OPEN / CLOSE` 锚点。
- 实际体重日 K 线、鼠标缩放/拖动、十字光标和 OHLC/热量 Tooltip。
- 默认使用 kJ 显示与输入，可在设置中切换 kcal；覆盖首页、图表提示、食品、食谱和运动界面。
- 今日能量 Treemap；每块面积按原始 `|kJ|`，摄入和消耗分组显示，切换显示单位不改变面积。
- Mifflin-St Jeor RMR、跨午夜睡眠/清醒基线消耗、可配置 kJ/kg 模型。
- 最长 30 个自然日的 EWMA 趋势校准。校准 δ 定义为“额外每日消耗”：正 δ 会降低预测体重。
- `dirty_from_date` 增量失效和逐日重算；删除全部派生缓存后可从原始事实重建。
- ZIP 备份、恢复前安全备份、schema v1 → v2 → v3 安全迁移、版本/表结构/外键/SQLite 完整性校验、JSON 全量导出。
- 中国/国际 K 线颜色模式及可交换的 Treemap 摄入/消耗颜色。

## 版本元数据

版本唯一来源为 [`app/version.py`](app/version.py)：

| 标识 | 当前值 | 含义 |
| --- | --- | --- |
| `APP_VERSION` | `0.0.2` | 待验证源码版本；窗口标题、Qt 应用元数据、备份清单和 JSON 导出共用 |
| `SCHEMA_VERSION` | `3` | v2 上增量添加可空营养字段 |
| `CALCULATION_VERSION` | `v2-simple-energy-kj-1` | kJ / schema-v2 世代的派生计算结果标识，不随文档或显示调整而改变 |

备份清单和 JSON 的 `schema_version` 读取实际数据库版本；`app_version` 表示创建备份/导出的应用版本。恢复兼容性按 schema 检查，并不要求旧备份的应用版本号与当前完全相同。

## 开发环境

- Windows 10 或 Windows 11，x64
- Python 3.14 x64（仓库 Windows 脚本使用 `py -3.14`）
- PySide6 6.11.1
- PyQtGraph 0.14.0
- SQLite（Python 标准库）

以上 Python/依赖要求仅适用于源码运行和开发。Windows GUI 和发布交付以 Python 3.14 环境的独立验证为准；依赖版本见 [`requirements.txt`](requirements.txt)，构建工具见 [`requirements-dev.txt`](requirements-dev.txt)。

## 从源码运行

在 PowerShell 中进入项目目录：

```powershell
py -3.14 -m venv .venv-win
.\.venv-win\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

也可以在虚拟环境准备好后运行：

```powershell
.\run_caloriek.ps1
```

临时指定数据目录：

```powershell
python main.py --data-dir D:\CalorieKData
```

读取源码应用版本而不启动 GUI 或访问数据库：

```powershell
python main.py --version
```

## 数据位置与完整性

没有覆盖设置时，源码运行使用仓库根目录下的外置 `data/`；Windows 打包版使用：

```text
%LOCALAPPDATA%\CalorieK\data
```

数据库文件名为 `caloriek.sqlite3`，自动安全备份保存在该数据目录的 `backups/` 下。路径选择优先级（从高到低）是：`--data-dir` → 环境变量 `CALORIEK_DATA_DIR` → 设置页保存的目录 → 上述默认目录。

可以在设置页选择新的空目录。程序会先创建安全备份，在目标数据库副本上完成设置和重算，再切换到副本；原数据库保留。源码运行的目录偏好写在根目录 `data_location.json`，打包版写在 `%LOCALAPPDATA%\CalorieK\config.json`。如果仍设置了命令行或环境变量覆盖，它们在下次启动时仍优先于保存的偏好。

应用本地保存数据，不自动上传或云同步；数据库、ZIP 备份和 JSON 导出包含个人健康记录，当前没有加密功能。不要将它们提交到公开仓库或直接附到公开 issue。

数据库启用 WAL、外键和事务。主要原始事实表：

- `profile` / `profile_revisions`
- `weight_measurements`
- `foods` / `food_servings`
- `recipes` / `recipe_items`
- `intake_events`
- `exercise_types` / `exercise_events`

`daily_metrics_cache` 和 `calibration_runs` 是可由原始事实重建的派生结果，不是实际称重来源。[`app/db/schema.sql`](app/db/schema.sql) 保留冻结的 **schema v2 基础契约**，[`app/db/migrations/schema.py`](app/db/migrations/schema.py) 追加 v3 列；新建库与升级库使用相同契约。`Database.initialize()` 幂等执行：v2 → v3 不转换能量、不失效缓存，不改变旧行或旧字段（包括时间戳、设置和自增序列）。现有 v2/v3 库启动不自动补种食品、默认份量或运动快捷项；新建库仍按原流程初始化。

有效 schema-v1/v2 数据库在升级前会通过 SQLite backup API 保存原始安全快照（包括已提交的 WAL 内容）：v1 为 `backups/pre_migration_v1_to_v2_*.sqlite3`，v2 为 `backups/pre_migration_v2_to_v3_*.sqlite3`。快照保持原 schema 和数据/单位，是独立 SQLite 文件；快照失败不开始迁移，迁移失败回滚并保留快照。v1 按既有能量迁移到 v2 后再追加 v3，整个升级在同一事务中完成。结构损坏/不完整或比 v3 更新的 schema 会被拒绝，不猜测修复或降级。

### 备份、恢复与导出

- 设置页“创建 ZIP 备份”使用 SQLite 一致性快照，归档包含 `caloriek.sqlite3`、`manifest.json` 和 `config.json`。
- “从备份恢复”接受有效的 schema v1、v2 或 v3 ZIP：解压到隔离候选库 → 校验原结构 → 必要时迁移到 v3 → 再校验 → staging → 当前库安全备份 → 替换。失败时不会用未验证候选库替换当前数据；正在使用数据库的其他实例可能导致安全拒绝。源 ZIP 不被修改。
- ZIP 中的目录配置用于记录来源；恢复不会把当前数据目录改成另一台机器上的原路径。
- JSON 全量导出用于查看或另行分析，当前没有 JSON 导入功能；它不能替代应用支持的 ZIP 恢复流程。
- 原始迁移快照 `.sqlite3` 不是 ZIP，不能直接在 ZIP 恢复对话框中选用。迁移失败时先保留数据目录和错误提示中的快照，再按审核后的恢复方案处理。不要在程序运行时直接复制主库充当完整备份，也不要手动删除 `-wal` / `-shm` 文件。

## 每日营养（CAL-8）

- 食品库新增每 100g 的蛋白质、膳食纤维、脂肪、碳水化合物。编辑器中不勾选“已知”表示 NULL/未知；勾选并填写 `0.00` 表示已知为零。存储和计算不按显示位数舍入，显示统一两位小数。
- 新字段不从旧版默认零的营养列推断、复制或回填。旧字段仍保存并在食品编辑器的兼容区域中提供；它们不参与新的每日汇总。内置食品同样需要明确填写新字段。
- 克数 / 以克为基础的份量按 `实际克数 / 100` 换算。能量基准不一定是 100g，但新营养字段始终以 100g 为基准。`ml` 没有密度数据，不能当作 g；体积食品的营养快照保持未知，原有体积和能量流程不变。
- 食谱分别累加每一项已知营养；缺少任何原料/成分或含无法换算的 ml 时，结果标为不完整。按克数或整份比例摄入时保存已知部分和完整性标记，不按未知为零。以后修改食品/食谱不会改写这些快照。
- 首页营养卡片的日期仅影响此卡片，不改变今日能量、体重 K 线、Treemap 或触发能量重算。按当日有效摄入快照合计，软删除不计入，恢复后重新计入；修改摄入量按原快照比例缩放，未知仍为未知。
- 任一记录缺失数据（包括未回填的历史记录），显示准确文本 **部分记录无营养数据**，数字标注为已知部分合计、不是完整总量。全部未知显示“未知”，无记录日显示 `0.00 g` 并注明仅为已记录合计。
- 营养不反推能量、不参与体重预测，也没有营养目标、评分、建议、诊断、钠/糖或微量营养素扩展。

迁移细节和后续人工验证清单见 [CAL-8 交接说明](docs/CAL8_NUTRITION.md)。

## 核心计算约定

### 日 K 线

- 无称重：`O = H = L = C = 前日 C`，预测值单独变化。
- 单次早晨称重或显式 `OPEN`：实际值为 O，理论模型补 C。
- 单次晚上称重或显式 `CLOSE`：实际值为 C，理论模型反推 O。
- 两次及以上：第一次为 O、最后一次为 C，中间值只保存为事实。
- H/L 来自首尾锚定的日内能量轨迹，并强制包含 O/C。

首个历史日若完全没有实际体重锚点，程序不会凭空生成 K 线。首次建档会同时创建一条真实称重，因此正常使用不会遇到这个状态。

### 能量与校准

数据库和计算模型统一以 **kJ** 存储/计算。新用户和升级用户默认显示 kJ；设置页可选择 `kj` / `kcal`，无效偏好回退 kJ。

换算固定为 `1 kcal = 4.184 kJ`。kJ 输入直接进入存储，kcal 输入在输入边界转换一次；显示格式化不会回写原始数据。仅切换显示单位不会改变能量、模型系数或派生结果。运动设备若给出 kcal，应在 kcal 输入模式下填写对应的额外运动消耗。

```text
日余额（kJ） = 摄入（kJ） - 基础/日常消耗（kJ） - 额外运动消耗（kJ）
模型余额（kJ） = 日余额（kJ） - calibration_kj_day
体重变化（kg） = 模型余额（kJ） / kj_per_kg
```

默认 `kj_per_kg = 32216.8 kJ/kg`（等价于 `7700 kcal/kg`），常量定义在 [`app/energy_units.py`](app/energy_units.py)，由 `SimpleEnergyWeightModel` 使用，可在设置中修改。kcal 显示模式下，该字段显示等价的 kcal/kg 数值，存储仍为 kJ/kg。RMR 公式的 kcal 原始结果也会在计算边界转换为 kJ。

## 测试与审核流程（维护者）

以下命令仅作人工验证参考，**不是授权 Codex 执行测试或打包的指令**。仓库任务必须遵守 [`AGENTS.md`](AGENTS.md)：实现后停止，由用户运行 `package_caloriek_handoff.bat`，交 ChatGPT 审核 handoff ZIP 并生成任务专用 Windows BAT；用户执行 BAT 后的结果才是独立验证证据。

测试使用标准库 `unittest`，既有纯计算/数据库用例，也有依赖 Qt 的控件用例。完整测试入口：

```powershell
python -m unittest discover -s tests -t . -v
```

用例覆盖核心计算、schema-v1 → v2 → v3 迁移及失败保护、历史营养快照、NULL/零与每日汇总、跨午夜代谢、30 日窗口/EWMA、缓存重算、kJ/kcal 输入与显示、Treemap 几何不变、浅色控件、备份恢复和版本元数据。新增 CAL-8 用例尚未执行。Qt 缺失时相关用例会跳过；跳过不等于 Windows GUI 验证通过。

安装 GUI 依赖后，维护者可在临时 PowerShell 会话中按审核后的验证计划执行离屏 smoke；离屏检查不能代替 Windows 原生弹窗的人工检查：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest tests.test_gui_smoke -v
```

## Windows 构建与 Release（维护者）

仅在明确进入构建/发布验证流程时，维护者使用以下脚本创建/复用 Python 3.14 虚拟环境、安装开发依赖、运行完整测试并用 PyInstaller 构建：

```powershell
.\build_windows.ps1
```

已安装依赖时可跳过安装（仍会执行测试、PyInstaller 和打包后 smoke）：

```powershell
.\build_windows.ps1 -SkipInstall
```

输出位置：

```text
dist\CalorieK\CalorieK.exe
```

PyInstaller 成功后，脚本还会用临时数据目录和 Qt `offscreen` 平台启动真正的打包 EXE，检查初始化、主窗口构造和首页刷新。脚本输出的是 **one-folder 目录**，不会自动生成版本化 Windows ZIP，也不会创建 tag、推送或发布 GitHub Release。

打包配置为 [`CalorieK.spec`](CalorieK.spec)，并显式包含 SQLite schema。用户数据库不会打入安装包，也不会因重新打包被覆盖。

最终 Windows ZIP 必须包含整个 `dist\CalorieK` 目录，而不是单独 EXE。**CAL-5 是 v0.0.1 发布关卡**；后续版本同样需要另行授权的发布验证流程、Windows ZIP 独立验证，以及实际 GitHub Release 资产重新下载后的 smoke。构建脚本成功不代表 Release 已完成。CAL-8 仅修改源码、文档和待执行测试，不执行这些操作。

## 目录概览

```text
main.py
app/
  application.py       # UIContext 适配和依赖组装
  version.py           # 应用 / schema / 计算版本唯一来源
  energy_units.py      # kJ/kcal 边界换算与显示格式化
  db/                  # SQLite、schema、seed、migration 入口
  models/              # 纯计算模型
  services/            # 原始事实 CRUD、缓存、Treemap、备份
  charts/              # PyQtGraph K 线、自绘 Treemap
  ui/                  # 主窗口、首页、录入、食品/食谱/运动项目库与设置
tests/                 # 标准库 unittest
docs/                  # Windows Release 使用说明
data/                  # 源码运行默认外置数据目录
```

## 仓库卫生

[`.gitignore`](.gitignore) 排除虚拟环境、`.tools`、构建产物、实际数据库及 WAL/SHM、数据备份、导出、缓存、测试结果、handoff/Release 归档、临时日志及根目录任务专用 `TEST_CAL*.bat`。`data/.gitkeep`、正式构建/运行脚本和源码测试保留在版本控制范围。

忽略规则不会删除本地文件，也不能移除已跟踪文件或清理 Git 历史。自定义名称的 JSON 导出应保存在 `exports/` 或仓库外；提交/发布前仍需人工检查待提交内容，避免个人健康数据和本机路径进入公共仓库。handoff ZIP 与 Release ZIP 是不同产物；前者可能包含测试结果和本机信息，不应公开发布为程序资产。

## 当前边界

当前版本不包含联网食品库、AI/条码识别、云同步、账号、多用户、健康平台接入、自动运动估算、营养诊断、减重计划、Web 或移动端。旧内置食品参考值不构成医疗或营养诊断，也不会自动充当新营养字段。CAL-8 沿用 CAL-6 的精度保留输入控件，仅扩展新的营养显示与输入。

许可证尚待维护者单独决定；本任务不新增或选择 LICENSE。
