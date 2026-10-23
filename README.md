# AzurLaneAutoScript — GG + Dashboard + Daily Income 魔改版

基于原版 [LmeSzinc/AzurLaneAutoScript](https://github.com/LmeSzinc/AzurLaneAutoScript) 迁移 AlasGG 分支并持续追加功能。**原版所有功能保持不变**，详见原版仓库。

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

GUI 右上角 Overview 页面内，12 张卡默认全部展开，自适应网格排列；仪表盘右侧紧邻 Cube / Gem 周-月-年汇总表。

资源记录钩子（写入 `config.modified` 并触发快照）：

| 资源 | 钩子文件 |
|------|---------|
| Oil / Coin | `campaign/campaign_status.py` |
| Pt | `campaign/campaign_status.py` |
| Cube | `gacha/gacha_reward.py`（建造前 / 建造后）· `commission/commission.py`（领取委托奖励后） |
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
  "snapshots":   [{"ts": "2026-10-19T11:00:00", "group": "Cube", "value": 570}],
  "daily":       {"2026-10-19": {"Cube": 100, "Gem": 50}},
  "daily_total": {"2026-10-19": {"Cube": 570, "Gem": 5100}},
  "last_update": "2026-10-19T11:00:00"
}
```

- **原始快照**保留 30 天（`SNAPSHOT_RETENTION_DAYS`）
- **每日聚合**（`daily`）永久保留，存当日正差之和（增量）
- **每日总数**（`daily_total`）永久保留，仅跟踪 Cube / Gem，存当天**最后一次快照的绝对值**；同一天多次快照时后写覆盖先写，自然收敛到当天结束前的最新库存
- 写入失败 try/except 保护，不影响主流程
- **Cube 额外快照节点**：魔方数量仅在建造页可读取，除建造流程（建造前 / 建造后各记一次）外，领取委托奖励后也会绕到建造页补记一次，避免当日非建造时段获得的魔方被漏计

**GUI 展示**：
- **仪表盘（Overview）**：每张资源卡片下方自动追加 `| +N` 提示（今日 gross gain）；仪表盘右侧紧邻 Cube / Gem 的今日-本周-本月-今年汇总表（`income_summary`）。
- **工具 > 数据统计**（独立页面）：按「年 → 月 → 日」分层展示，每年一个月份合计表 + 各月每日明细折叠块，全部默认展开。仅 Cube / Gem 参与周/月/年历史统计，其他资源只有仪表盘卡片上的当日增量提示。每日明细表 5 列：`日期 | 魔方 | 钻石 | 魔方总数 | 钻石总数`，前两个为当日增量（`+N`），后两个为当日库存绝对值；该日无快照记录时总数列显示 `-`（区分"无数据"与"真的是 0"）。

### 4. 委托 / 科研任务开关

原版强制锁定委托（Commission）、科研（Research）、收获（Reward）三个任务为始终开启。本 fork：

- **委托 / 科研**：`Scheduler.Enable` 改为普通 checkbox，可在任务设置中自由关闭
- **收获（Reward）**：保持强制开启（奖励领取是其他任务正常工作的前提）
- 战役进行中弹出委托完成通知时，若用户已关闭委托任务，仅记录日志，不再强制写回开关设置

### 5. 日服登录流程强化

- 登录流程卡顿阈值单独放宽（防 `GameStuckError` 误判）：日服黄鸡（Manjuu）加载在网络不稳定时等待时间较长，登录期间阈值放宽，登录结束后恢复默认值
- 允许日服登录成功后再次出现标题画面时重新点击（原逻辑仅首次登录前点击，黄鸡结束退回标题画面会无响应直至超时）
- 以上强化（含黄鸡等待逻辑）**仅对日服生效**，其他服务器保持原版登录行为，不受影响

### 6. 仓库扫描（StorageScan）

只适配国服。

路径：`module/storage/scanner.py`

每日自动进入仓库-材料页扫描一次，记录 17 项白名单物品的数量：特装型突破 / 试作型突破、四类研发图 + 装备研发图、绝密实验计划、特装原型、心智单元 / 心智单元II、五类部件T4、喵箱SSR型。

**识别方案**（无模板匹配、纯 OCR，无需截图素材）：
- 以物品名所在深蓝底横条为行锚点（横条被长名拆断时自动合并、按 178px 等距链补齐），材料页变高分区与翻页相位漂移均可适配
- 物品名 cnocr（两遍阈值 128/96 重试）、数量 AmountOcr（threshold=160 滤图标噪声）
- **同名不同品质物品**（如金/紫研发图）按图标底色区分，分开记录并按游戏稀有度命名：`舰炮研发图UR型`（紫）/ `舰炮研发图SSR型`（金）；特装型/试作型突破记录为 `特装型突破部件` / `试作型突破部件`，装备研发图记录为 `装备研发图UR型`
- **跨页投票共识**：同一物品在翻页中重复出现 2-3 页，按出现页数投票取共识值，单页偶发误读自然被排除
- **部件档位裁决**：金框才匹配 T4，防止部件T1 被误读成 T4

**终止条件**（按优先级）：集齐白名单提前停（物品都在靠前位置，大号约 5 页即停）→ 连续 3 页无新增物品停（小号没有白名单物品时快速结束，不空转）→ 连续 3 页检不出名称横条停 → 到底停 → 页数上限兜底。

**调度**：每天一次（`SuccessInterval=1440`），优先级在收获（Reward）之后——扫描的是收获后的最新数据。**注意**：本 fork 的 `config.py` 会把 NextRun 超过 24h 的任务拉回到当前时刻，StorageScan 已被排除出该机制（否则推迟整 24 小时的任务会被立即再次触发，形成无限重扫）。

**存储**：`log/storage_scan/records.json`，追加式保存，保留最近 90 次记录。

**GUI 展示**：
- 工具组新增"仓库扫描"任务卡（可启用调度 / 手动运行一次）
- "数据统计"页顶部展示：物品名按稀有度/类别上色（UR 型研发图、绝密实验计划、特装型突破部件为彩色渐变；SSR 型研发图、试作型突破部件为金色；心智蓝 / 部件绿 / 原型橙 / 喵箱粉），同类型排在一起（心智单元最上、部件其次、研发图 UR 在 SSR 上），显示当前数量、较上次变化（绿增红减）、最近 7 次扫描历史折叠块

**已知限制**：扫描期间不能同时手动操作游戏（约 1-2 分钟）；扫描需游戏停在可识别页面（主页 / 出击页均可）。

### 7. 困难图星期制（Hard）

路径：`module/hard/hard.py`

原版困难图只能填一个关卡，每天重复打同一关，图纸来源不均匀。本 fork 改为**星期制**：

- 困难图设置页提供 7 个关卡字段：**周一关卡 ~ 周日关卡**，每天可各填不同关卡（如周一 12-4、周四 10-2），均衡收集不同图纸
- 运行时按**服务器时间**判断今天星期几，自动出击对应关卡（日志打印 `Today is Thursday, hard stage: 10-2`）
- **旧配置自动迁移**：原"困难关卡"填写的值会自动填充到 7 个星期字段，升级不丢配置；已手动填写的字段不会被覆盖
- 出击舰队、每日调度（服务器 00:00 刷新后执行）等其余逻辑不变

### 8. 经验检测归并（侵蚀1练级专属）

路径：`module/os/tasks/check_leveling.py`

原版"每日经验检测"逻辑嵌在**短猫相接**和**侵蚀1练级**两个任务开头，检测满级后 `task_delay` 推迟的是当前任务——练满级会误停短猫。本 fork 改为**仅侵蚀1练级生效**：

- 侵蚀1练级运行前检测出击舰队经验，全部达到目标等级且"到达目标等级后推迟任务"开启时，侵蚀1任务自动推迟到次日并停止今日练级
- 短猫相接不再运行经验检测，互不影响
- 移除了侧边栏不可用的"每日经验检测"独立任务（无运行入口、不在调度链），设置组保留在侵蚀1练级页面

### 9. GUI 帮助文本补全

fork 新增功能的设置项均配有简体中文帮助文本（鼠标悬停显示），说明用途与使用方法：

- **GG 管理器 / 战力限制**：组说明与各战力阈值（演习 / 共斗 / 余烬信标）字段说明
- **仪表盘 12 个资源卡片**（石油 / 金币 / 钻石 / Pt / 魔方 / 行动点 / 金硬币 / 紫硬币 / 核心数据 / 勋章 / 功勋 / 大舰队币）：当前值、下限、上限、记录时间等字段的自动更新机制说明
- **困难图星期关卡**：说明按服务器时间自动选择当天关卡
- **经验检测**：目标等级、上次检查时间、到达等级推迟任务（四语言）
- 清理 `override.yaml` 中对 `GGHandler.Timeout` 的冗余覆盖（消除 config_updater 生成时的参数告警）

### 10. MAA 插件（明日方舟 · AlasMaaBridge）适配

路径：`submodule/AlasMaaBridge/`

原版 MAA 插件停留在旧版 MaaCore 接口，本 fork 按 MAA v6.20.1 重新适配了任务参数、资源更新与配置生成。

**任务参数适配（v6.20.1）**

- **生息演算**：任务名改为 `Reclamation`（旧 `ReclamationAlgorithm` 会被 MaaCore 直接拒绝）；主题收敛为 `Tales`（无存档 / 有存档刷繁荣点数）与 `RelaunchAnchor`（RA-1 / RA-15 / RA-4），`Fire` 主题已从 MAA 移除；`clear_store` 只在 Tales 无存档模式生效
- **肉鸽**：6 个主题（Phantom / Mizuki / Sami / Sarkaz / JieGarden / BlackFlow）；`start_with_seed` 按字符串传（MAA 文档写 boolean 是错的）；新增 `core_char_list`，旧 `core_char` 仍可用；黑流模式必须带开局动物与开局奖励，否则整个任务被拒
- **领取奖励**：补齐 `Mining`（限时开采许可）与 `SignInEvent`（限时签到），`DailyAward` 可关闭（默认开启，保持原行为）
- **兼容修复**：剿灭错误过滤 `!=` 改为 `not in`；补上 `Message.ReportRequest` 回调类型且未知类型不再抛异常；神秘代码前缀改用切片解析（`strip` 会吃掉结尾的 a/m）
- **配置生成**：插件自带的 `config_updater` 只写 MAA 自己的产出（`args.json` / `config_generated.py` / `menu.json` / `template.maa.json`），不再覆盖主模块的同名配置

**MAA 资源 / 本体更新**

- `更新 MAA` 任务：`AutoUpdateResource`（默认开）启动时拉取 MaaCore 资源，版本可选 Stable / Beta / Nightly；`UpdatePackage` 可选同时更新本体
- 资源更新走 `tasks.json` + ETag 增量，本体更新走 GitHub Release 包（流式下载 → 解压 → 清理已删除的旧资源），本体 dll 被占用时先落盘为 pending、下次启动再装
- 更新完成后按当前客户端类型校验目标包名，避免资源与实际客户端不匹配
- 本体更新解压时跳过用户数据目录（MAA 的设置、日志与缓存），不会覆盖已有配置

**数据同步与干员培养**

- `更新数据` 任务：`UpdateOperBox` / `UpdateDepot` 让 MAA 识别干员 box 与仓库，结果保存到 `config/maa/`（供查看或导入第三方面板）；两项都没勾选时跳过并把任务推迟到次日
- `干员培养` 任务：在「培养计划」里直接填写 MAA 原生格式的 OperProgress plans JSON，提交后按 MAA 日志输出培养进度

**完成所有任务后**

- `完成所有任务后`：`主页面待机` / `关闭游戏` / `关闭模拟器` 三种收尾方式（模拟器相关的实现见下一节）

**魔改客户端自定义包名**

- `模拟器设置 > 自定义包名（可选）`（`CustomPackageName`）：留空时沿用 MAA `resource/config.json` 里的映射，填写后覆盖（如 `com.hypergryph.arknightt`）
- 注入时机在资源更新之后、加载 MaaCore 之前（资源更新会重写 `config.json`，顺序反了会被冲掉）
- 启动前校验设备上确实装有该包，缺失时列出设备上相关的包并请求人工介入，而不是让 MaaCore 空转 50 秒后报 `Reconnect failed`
- 运行中修改包名会提示需重启实例才生效（MaaCore 资源一个进程只加载一次）

### 11. 模拟器掉线自愈与「关闭模拟器」收尾

路径：`alas.py`、`module/device/device.py`、`submodule/AlasMaaBridge/`

- **`close_emulator` 收尾选项**：`Optimization.WhenTaskQueueEmpty` 新增该选项，完成所有任务后关游戏并关模拟器，下个任务开始前自动拉起；ALAS 与 MAA 插件都支持
- **每条任务开始前自愈**：`Device.emulator_check()` 先做一次 `adb devices` 探测（约 30ms），掉线时重新枚举实例（MuMu 端口漂移会被修正）、拉起模拟器、释放 nemu_ipc 后重连 adb，不再让整个实例 `RequestHumanTakeover` 退出
- **运行中掉线**：任务抛 `RequestHumanTakeover` 且确认设备真的不在时，拉起模拟器并重试该任务一次；其余情况维持原有退出行为
- **停机判据**：`status != 'device'` 也算已关闭（MuMu 关闭后 adb 常留 `offline` 条目，旧逻辑会白等满一个超时）
- **等待参数**：`emulator_stop_and_wait()` 超时 30 → 60 秒，支持 `deadline`（到点立即返回，不占用调度时间），超时返回 `False` 而不是「当作已关闭」
- **空档下限**：距下个任务不足 180 秒（`EMULATOR_CLOSE_MIN_WAIT`）时不关模拟器，按待机处理——关掉再拉起要十几秒，短空档得不偿失
- **GUI 操作不被吞**：配置变更基准 `start_watching()` 前移到任何阻塞操作之前，关闭模拟器期间在 GUI 手动加的任务能立刻执行
- **MAA 插件的额外差异**：启动实例即做一次设备检查（失败才请求人工介入）；`wait_online()` 每 5 秒主动 `adb connect`（MuMu 不会把自己的端口注册进 adb server）并每 10 秒输出等待进度；关闭路径先判在线再连 MaaCore；等待分支不再反复唤醒游戏（避免「唤醒 → 队列又空 → 又关」的自激循环），并用 10 分钟内连续 3 次进入等待分支做空转保护

### 12. GUI 修复

- **科研（Scheduler）启用开关被隐藏**：`assets/gui/css/alas.css` 的开关选择器误伤了该控件，修正选择器后恢复显示
- **设置页 KeyError 崩溃**：帮助文本里贴的 JSON 示例 `{"name": ...}` 未转义，`str.format()` 会把 `"name"` 当成替换字段；四语言 i18n 中的字面花括号统一改为 `{{` / `}}`（这也是 ALAS i18n 的隐式约定：文案里的花括号必须转义）

---

## Fork 维护备忘

<details>
<summary>上游兼容性补丁（点击展开）</summary>

上游 `module/device/connection_attr.py` 中有一段与 uiautomator2 代理清理相关的兼容逻辑，在 fork 修改默认任务配置结构后会产生副作用，表现为 adb 连接异常。本 fork 已做相应调整（commit `914ed1d69`），仅保留其代理清理的正常功能。

**从上游合并代码时，请注意 `connection_attr.py` 的 `__init__` 与本 fork 版本的差异，避免补丁丢失。**

排查提示：若日志出现 `cannot resolve host '127.0.0.1' and port ...`（引号不对称）且模拟器本身正常，用 `repr(serial)` 检查实际字节。

</details>

---

## 启动脚本

`run.bat`：后端快速启动批处理脚本（无需打开 GUI 即可启动 alas 后端）。

---

## 配置路径变化

原版：`config/<实例名>.json`

新增：
- `config/<实例名>_stats.json` → **已废弃**（会被 webui 误扫成假实例）
- `config/stats/<实例名>.json` ← 新位置

## 已知限制

- **科研消耗魔方无法精确统计**：脚本只 OCR 库存值，不知道具体某次消耗了多少。正差之和算法保证 gross gain 不被消耗抵消，但也意味着"手动领邮件补偿但没跑任何脚本任务"的情况不会被记录
- **Pt 在活动结束后会清零**：正差之和只算增量，清零那天 gross gain 从 0 重新开始（与实际一致）
