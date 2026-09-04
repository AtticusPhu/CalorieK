# CalorieK V1

CalorieK 是一个仅面向 Windows 10/11 的离线桌面程序，用股票日 K 线的视觉逻辑记录实际体重，并把摄入、基础/日常消耗与运动 Active Calories 用于当日预测和个人能量模型校准。

核心原则是：**实际称重是事实，理论模型只负责预测和补足单次称重的 K 线缺失端。** 预测值不会写入实际称重表；饮食、食谱、运动和资料版本均长期保留为可重算的原始数据。

## V1 功能

- 首次启动录入性别、出生日期、身高、当前体重、作息和活动系数。
- 39 种版本化内置食品；支持自定义、收藏、软删除和 serving 换算。
- 自定义食谱按 `g` / `ml` 分别汇总；单一单位食谱计算每 100 g / 100 ml 营养并支持按量或比例录入，混合单位食谱仅按整份比例录入。
- 饮食事件保存完整营养快照，后续修改食品或食谱不会改变历史。
- 可新建、编辑、收藏、停用/恢复运动快捷项目；运动录入自动带入最近一次时长/kcal，运动 kcal 统一为 Active Calories。
- 实际体重按真实时间保存，并支持 `AUTO / OPEN / CLOSE` 锚点。
- 实际体重日 K 线、鼠标缩放/拖动、十字光标和 OHLC/热量 Tooltip。
- 今日 kcal Treemap；每块面积严格按 `|kcal|`，摄入和消耗分组显示。
- Mifflin-St Jeor RMR、跨午夜睡眠/清醒基线消耗、可配置 kcal/kg 模型。
- 最长 30 个自然日的 EWMA 趋势校准。校准 δ 定义为“额外每日消耗”：正 δ 会降低预测体重。
- `dirty_from_date` 增量失效和逐日重算；删除全部派生缓存后可从原始事实重建。
- ZIP 备份、恢复前安全备份、schema 版本/表结构/外键/SQLite 完整性校验、JSON 全量导出。
- 中国/国际 K 线颜色模式及可交换的 Treemap 摄入/消耗颜色。

## 环境

- Windows 10 或 Windows 11，64 位
- 推荐 Python 3.14（开发验证目标为 Python 3.14.7）
- PySide6 6.11.1
- PyQtGraph 0.14.0
- SQLite（Python 标准库）

当前源码的核心层兼容 Python 3.12+；Windows GUI 和交付环境以 Python 3.14 为准。

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

## 数据位置与完整性

源码运行默认使用项目外置目录 `data/`；PyInstaller 版本默认使用：

```text
%LOCALAPPDATA%\CalorieK\data
```

也可以在设置页选择新的空目录。程序会先创建安全备份，在目标数据库副本上完成设置和重算，再原子切换；原数据库会保留作为恢复副本。环境变量 `CALORIEK_DATA_DIR` 或命令行 `--data-dir` 可覆盖默认位置。

数据库启用 WAL、外键和事务。主要原始事实表：

- `profile` / `profile_revisions`
- `weight_measurements`
- `foods` / `food_servings`
- `recipes` / `recipe_items`
- `intake_events`
- `exercise_types` / `exercise_events`

派生表 `daily_metrics_cache` 和 `calibration_runs` 可以删除后重建。`schema_version=1` 的初始 migration 由 [`app/db/schema.sql`](app/db/schema.sql) 定义，并由 `Database.initialize()` 幂等执行。

## 核心计算约定

### 日 K 线

- 无称重：`O = H = L = C = 前日 C`，预测值单独变化。
- 单次早晨称重或显式 `OPEN`：实际值为 O，理论模型补 C。
- 单次晚上称重或显式 `CLOSE`：实际值为 C，理论模型反推 O。
- 两次及以上：第一次为 O、最后一次为 C，中间值只保存为事实。
- H/L 来自首尾锚定的日内能量轨迹，并强制包含 O/C。

首个历史日若完全没有实际体重锚点，程序不会凭空生成 K 线。首次建档会同时创建一条真实称重，因此正常使用不会遇到这个状态。

### 能量与校准

```text
日余额 = 摄入 - 基础/日常消耗 - 运动 Active Calories
模型余额 = 日余额 - calibration_kcal_day
体重变化 = 模型余额 / kcal_per_kg
```

默认 `kcal_per_kg = 7700`，集中封装于 `SimpleEnergyWeightModel`，可在设置中修改。

## 测试

核心测试不依赖 Qt：

```powershell
python -m unittest discover -s tests -t . -v
python -m compileall -q app main.py
```

测试覆盖任务书 A–L、数据库 migration/事务、历史营养快照、跨午夜代谢、30 日窗口、EWMA、历史失效/重算一致性、Treemap 绝对面积、颜色切换和备份失败保护。

安装 GUI 依赖后可运行离屏窗口 smoke test（仓库提供 `tests/test_gui_smoke.py` 时会自动跳过无 Qt 环境）：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest tests.test_gui_smoke -v
```

## Windows 打包

一条命令创建/复用 Python 3.14 虚拟环境、安装开发依赖、运行测试并用 PyInstaller 打包：

```powershell
.\build_windows.ps1
```

已安装依赖时可跳过安装：

```powershell
.\build_windows.ps1 -SkipInstall
```

输出位置：

```text
dist\CalorieK\CalorieK.exe
```

PyInstaller 成功后，脚本还会用临时数据目录和 Qt `offscreen` 平台启动**真正打包后的 EXE**执行 GUI 冒烟测试；只有 EXE 能完成数据库初始化、首次资料初始化、主窗口构造和首页刷新，打包任务才算成功。这样可以捕获“PyInstaller 成功但 Qt/MSVC DLL 缺失导致 EXE 无法启动”的问题。

打包配置为 [`CalorieK.spec`](CalorieK.spec)，并显式包含 SQLite schema。用户数据库不会打入安装包，也不会因重新打包被覆盖。

## 目录概览

```text
main.py
app/
  application.py       # UIContext 适配和依赖组装
  db/                  # SQLite、schema、seed、migration 入口
  models/              # 纯计算模型
  services/            # 原始事实 CRUD、缓存、Treemap、备份
  charts/              # PyQtGraph K 线、自绘 Treemap
  ui/                  # 主窗口、首页、录入、食品/食谱/运动项目库与设置
tests/                 # 标准库 unittest
data/                  # 源码运行默认外置数据目录
```

## V1 边界

V1 不包含联网食品库、AI/条码识别、云同步、账号、多用户、健康平台接入、自动运动估算、营养诊断、减重计划、Web 或移动端。内置食品营养值是带版本的常用参考均值，不构成医疗或营养诊断。
