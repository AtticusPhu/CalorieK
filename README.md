# CalorieK v0.0.4

CalorieK 是一款本地离线的体重与热量记录应用，以日 K 线展示实际体重，并结合饮食、运动、基础消耗进行预测和校准。

## 下载

当前已发布版本：[CalorieK v0.0.4（Windows x64）](https://github.com/AtticusPhu/CalorieK/releases/tag/v0.0.4)。

下载 Windows ZIP，完整解压后运行 `CalorieK.exe`，保留同级依赖目录。无需安装 Python。

## 主要功能

- 体重、饮食、运动历史记录及修正
- 食品、食谱、每日营养与能量汇总
- 体重 K 线、能量 Treemap 和个人模型校准
- kJ/kcal 单位切换、SQLite 本地存储、ZIP 备份恢复和 JSON 导出

### 开发版的实际记录日期

饮食、运动和称重按录入时间自身的本地日历日判断。可以录入今天稍晚的记录，从明天起不能新增实际记录。饮食和运动需要当日已有生效的资料，并且当日或更早至少有一次有效称重。

已有未来记录可以在首页“每日营养构成”选中原日期，点击“查看当日记录”打开并更正。可在原日编辑，也可改到符合上述条件的今天或过去日期。移动首个称重时，已有饮食和运动仍须由同日或更早的称重覆盖。

## 源码运行

已验证环境为 Windows 10/11 x64、Python 3.14。

```powershell
python -m pip install -r requirements.txt
python main.py
```

源码入口为 `main.py`，应用代码、界面、计算模型、数据库结构及迁移均位于 `app/`。应用版本为 0.0.4，数据库 Schema 为 3，计算版本为 `v2-simple-energy-kj-1`。

源码运行默认数据目录为 `data/`；Windows 打包版默认为 `%LOCALAPPDATA%\CalorieK\data`。升级前请使用应用的 ZIP 备份功能。个人数据库、备份、导出、构建与测试产物不应提交到公开仓库。

本分支仅维护应用源码及运行所需配置；开发验证、测试和打包材料不随当前分支公开维护。历史发布版本及其资产保持不变。
