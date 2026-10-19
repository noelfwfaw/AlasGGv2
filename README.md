# AzurLaneAutoScript — GG + Dashboard + Daily Income 魔改版

基于原版 [LmeSzinc/AzurLaneAutoScript](https://github.com/LmeSzinc/AzurLaneAutoScript) 迁移 AlasGG 分支的三块功能。**原版所有功能保持不变**，详见原版仓库。

---

## 新增功能

### 1. GG Handler（GG / 加速器自动控制）

路径：`module/gg_handler/`

- **防弹窗**：GG / 加速器弹出窗口自动关闭
- **自动重启**：游戏崩溃后重启模拟器
- **倍率控制**：设置 GG 速度倍率并在运行中维持
- **战力限制**：高战力任务前自动降低 GG 倍率

接入点：
- `alas.py`：`loop()` 前调 `handle_restart_before_tasks()`，每任务 `check_config()` / `check_then_set_gg_status()`
- `campaign/campaign_event.py`：GemsFarming 按 GG 开启情况选择重置节点
- `raid.py` / `exercise/combat.py` / `os_ash/ash.py`：战力限制钩子

### 2. Dashboard 仪表盘

路径：`module/config/argument/dashboard.yaml` 等

12 个资源卡片（Oil / Coin / Gem / Pt / Cube / ActionPoint / YellowCoin / PurpleCoin / Core / Medal / Merit / GuildCoin），每个显示：
- 当前值 + 限额（如有）
- 最近一次 OCR 的时间
- 当日获取量提示（`| +N`，仅当日 gross gain > 0 时显示）

GUI 右上角 Overview 页面内，默认折叠显示 Oil/Coin/Gem/Pt 四张卡，点击展开全部。

资源记录钩子（写入 `config.modified` 并触发快照）：

| 资源 | 钩子文件 |
|------|---------|
| Oil / Coin | `campaign/campaign_status.py` |
| Pt | `campaign/campaign_status.py` |
| Cube | `gacha/gacha_reward.py` |
| Gem | `shop/shop_status.py` |
| ActionPoint | `os_handler/action_point.py` |
| YellowCoin / PurpleCoin | `os_handler/os_status.py` |
| Medal / Merit / GuildCoin / Core | `shop/shop_status.py` |

### 3. 每日获取量统计

路径：`module/statistics/statistics.py`

**算法**：正差之和。同一天内资源值 1000 → 1050 → 980 → 1100，gross gain = 50 + 120 = 170。**永远不会出现负数**——消耗和手动花掉的部分被 `max(0, delta)` 丢弃。

**存储**：`config/stats/<实例名>.json`（独立子目录，不被 webui 的实例扫描器误识别）。

```json
{
  "snapshots": [{"ts": "2026-10-19T11:00:00", "group": "Cube", "value": 570}],
  "daily":     {"2026-10-19": {"Cube": 100, "Gem": 50}},
  "last_update": "2026-10-19T11:00:00"
}
```

- **原始快照**保留 30 天（`SNAPSHOT_RETENTION_DAYS`）
- **每日聚合**永久保留
- 写入失败 try/except 保护，不影响主流程

**GUI 展示**（嵌在仪表盘内，不独立面板）：
- 每个资源卡片下方那行自动追加 `| +N` 提示（今日 gross gain）
- 仪表盘底部周/月/年汇总表（**仅 Cube 和 Gem**，其他资源只有卡片提示）
- 可折叠的 30 天历史表格（自动裁掉尾部全零行）

---

## 配置路径变化

原版：`config/<实例名>.json`

新增：
- `config/<实例名>_stats.json` → **已废弃**（会被 webui 误扫成假实例）
- `config/stats/<实例名>.json` ← 新位置

## 已知限制

- **科研消耗魔方无法精确统计**：脚本只 OCR 库存值，不知道具体某次消耗了多少。正差之和算法保证 gross gain 不被消耗抵消，但也意味着"手动领邮件补偿但没跑任何脚本任务"的情况不会被记录
- **Pt 在活动结束后会清零**：正差之和只算增量，清零那天 gross gain 从 0 重新开始（与实际一致）
