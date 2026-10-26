# 工单：新增「活动图-3」任务（Event3）

> 交付对象：编码 Agent。本文自包含，无需其他上下文，也不需要访问本次对话。
> 仓库：`C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript`（ALAS fork，分支 `master`）。
> 本工单对应「功能」类需求，**不涉及任何游戏操作代码的新增或修改**，全部落在配置、注册与调度层面。

## 0. 硬约束（不可妥协）

- **禁止运行 ALAS / gui.py / alas.py / 任何任务流程；禁止连接模拟器、adb、uiautomator；禁止安装依赖。**
  本工单不新增任何状态循环，没有任何需要"跑起来看"的东西。需要真机确认的部分已全部列进第 5 节。
- **禁止 `git commit` / `git push` / `git add`**，改动只留在工作区。
- 允许的动态命令**只有一条**：配置生成器
  `/d/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/toolkit/python.exe -m module.config.config_updater`
  （在源码仓根执行；生成器内部会自己 `os.chdir` 到仓库根）。系统自带的 Python 3.13 **跑不了**（缺 yaml），不要试。
- 允许的静态命令：`python -m py_compile <文件>`、`python -c "import json; json.load(...)"`、`git diff`/`git status`。
- 本仓库已安装 `alas-dev` 技能。开工前请加载它，并遵守其铁律；本工单与该技能冲突时，**以本工单的显式路径与内容为准**。
- **不得手改任何生成物**。纯生成物（`args.json`、`menu.json`、`config_generated.py`、`config/template.json`）
  只能由生成器产出；`module/config/i18n/*.json` 是半产物（旧值保留 + 新键补占位），
  文案由人手写、生成器只做保留，先后顺序见 R7 → R8。唯一例外是第 R8 节末尾写的"生成器跑不起来"降级方案。
- **不得改动 `module/campaign/run.py`、`module/event/*`、`module/config/config.py` 的任何逻辑。**
  本任务完全复用 `CampaignRun.run()`，Event3 与 Event2 是同一个类、同一条调用链。
- 不得顺手「优化」本工单第 3 节点名的既有设计。

## 1. 需求与已验证事实

### 1.1 需求

活动（`Event`）分组下现在有 `活动图`（`Event`）和 `活动图-2`（`Event2`）两个任务，
各带自己独立的 `Campaign / StopCondition / Fleet / Submarine / Emotion / HpControl / EnemyPriority` 配置组，
用途是让用户用**不同的舰队配置**分别刷活动里的不同地图（例如 Event 用 1+2 队刷 D3，Event2 用 3+4 队刷 C2）。

需求：**再增加一个 `Event3`（活动图-3）**，即第三条独立的"活动图"任务线，让用户能再挂一队刷第三张活动图。

这是纯镜像工作量：`Event2` 当初就是这么加进来的（上游 commit `dacbb518f`
「Opt: Remove 7-2 mystery farming, 12-2 leveling, 12-4 leveling; add multiple Main and Event tasks」），
**照它的 6 个手工落点 + 生成器产物即可**，实现上没有任何新逻辑。

### 1.2 现状取证（全部已核对，不必重复确认）

| 事实 | 位置 |
|---|---|
| `Event2` 任务定义 = 8 个配置组，与 `Event` 完全一致 | `module/config/argument/task.yaml:86-94` |
| `Event2` 的 override 只有两条：`Campaign.Mode=normal`、`Campaign.AmbushEvade=true`（会强制锁死并隐藏该选项） | `module/config/argument/override.yaml:71-73` |
| `Event2` 的入口方法体与 `event()` 逐字相同 | `alas.py:398-401` |
| 任务名 → 入口方法名的转换是 `inflection.underscore`（`Event3` → `event3`），无注册表 | `alas.py:683`、`alas.py:695` |
| 调度顺序常量（`SCHEDULER_PRIORITY`，不暴露给 GUI） | `module/config/config_manual.py:11-38`（Event 那行在 `:33`） |
| 活动类任务的统一清单，驱动「活动 PT 上限 / 活动时间上限自动关闭」、`Campaign.Event` 选项生成、最新活动自动切换、默认难度 D3 | `module/config/config_updater.py:34`（`EVENTS`） |
| `TaskBalancer`（金币不足切图）判定"当前是不是活动任务"的硬编码清单 | `module/campaign/campaign_status.py:210-227` |
| `func.startswith("Event")` 会自动把 `EventGeneral` 与 `TaskBalancer` 绑到任务上，**Event3 无需额外登记** | `module/config/config.py:148-158` |
| `EventGeneral` 的两个上限开关、活动入口关闭、与共斗互斥的两侧，全部通过 `EVENTS` 常量统一生效；Event3 加入 `EVENTS` 即自动覆盖 | `module/campaign/campaign_event.py:68`、`:100`、`:159`、`:214`、`:237` |
| 本 fork 的 GG 倍率处理器**已经预置了 `'event3'`**，无需改动 | `module/gg_handler/gg_handler.py:307` |
| 任务列表由 `menu.json` 驱动，webui 无第二份任务白名单 | `module/config/argument/menu.json:27` |
| `Campaign.Name` 为自由文本（默认 `12-4`），事件任务会在生成期被改成 `D3`；`Campaign.Event` 的选项由 `campaign/Readme.md` 的表格按服务器生成，**两者都不需要为新任务单独准备数据** | `module/config/config_updater.py:478-486`、`:692-699` |

### 1.3 生成器产物的预期内容（用于判读 diff，不要照抄）

跑完生成器后，`Event3` 段应当与现有 `Event2` 段**同构**：

- `args.json`：新增顶层 `Event3`，内含 8 个组 + 自动追加的 `Storage` 组；`Campaign.Mode` 与 `Campaign.AmbushEvade`
  带 `display: hide`（来自 override.yaml）；`Scheduler.Command.value == "Event3"` 且 `display: hide`；
  `Campaign.Event` 带 `option_<server>` / `option` / `option_bold` 三份选项列表。
- `config/template.json`：新增 `Event3` 段，`Campaign.Name == "D3"`、`Campaign.Event == "campaign_main"`，
  其余值与 `Event2` 段逐键相同。
- `config_generated.py`：新增一批 `Event3_*` 常量。
- `menu.json`：`Event.tasks` 里在 `"Event2"` 之后多出 `"Event3"`。

## 2. 改动清单

按 R1 → R7 顺序改完再跑生成器（R8），**不要中途跑**。

### R1 活动任务清单（必须）

`module/config/config_updater.py:34`，把 `Event3` 插在 `Event2` 之后：

```
EVENTS = ['Event', 'Event2', 'Event3', 'EventA', 'EventB', 'EventC', 'EventD', 'EventSp']
```

这一处是**单点**：`campaign_event.py` 里 5 处任务集合判定（PT 上限、时间上限、活动入口不可用时批量关闭、
与共斗互斥的两侧）、`config_updater.py` 里 4 处选项生成/默认值逻辑，全部读这个常量，不需要逐个去改。

### R2 任务定义（必须）

`module/config/argument/task.yaml`，在 `Event2` 块（现 `:86-94`）之后、`Raid:`（现 `:95`）之前插入 9 行：

```yaml
    Event3:
      - Scheduler
      - Campaign
      - StopCondition
      - Fleet
      - Submarine
      - Emotion
      - HpControl
      - EnemyPriority
```

顺序即 GUI 里"活动"折叠菜单的顺序，必须在 `Event2` 之后。

### R3 override（必须）

`module/config/argument/override.yaml`，在 `Event2` 块（现 `:71-73`）之后、`EventA:`（现 `:75`）之前插入：

```yaml
Event3:
  Campaign:
    Mode: normal
    AmbushEvade: true
```

这两条虽然与 `argument.yaml` 的默认值相同，但 override 会额外把选项设为 `display: hide`，
使 `Campaign.Mode` 对活动图不可见（活动图不用 Mode 切换难度，C/D 图是独立的地图文件）。
**必须在跑生成器之前加**，否则 `args.json` 里 Event3 的 Mode 会变成可见的 `normal/hard` 下拉。

### R4 入口方法（必须）

`alas.py`，在 `event2()`（现 `:398-401`）之后插入 4 行，方法体与 `event2()` 逐字相同，只把名字改成 `event3`：

```
    def event3(self):
        from module.campaign.run import CampaignRun
        CampaignRun(config=self.config, device=self.device).run(
            name=self.config.Campaign_Name, folder=self.config.Campaign_Event, mode=self.config.Campaign_Mode)
```

不要抽公共函数、不要加参数、不要加日志。

### R5 调度优先级（必须）

`module/config/config_manual.py:33`，把 Event3 接在 Event2 后面：

```
    > Event > Event2 > Event3 > Raid > Hospital > Coalition > Main > Main2 > Main3
```

`Event → Event2 → Event3` 是顺序执行关系：三项都开启时，Alas 按这个顺序依次刷。
（不暴露给 GUI，`TaskBalancer.TaskCall` 的选项列表里**不要**加 Event3，理由见第 3 节。）

### R6 TaskBalancer 任务判定（必须）

`module/campaign/campaign_status.py` 的 `is_balancer_task()`，`tasks` 列表（现 `:215-221`）在 `'Event2',` 之后加一行 `'Event3',`。

漏掉这行不会报错，但会让 TaskBalancer 的"金币不足自动切图"对 Event3 失效 —— 属于沉默失效，必须一起改。

### R7 四语言文案（必须）

在四个文件各自的 `"Task"` 对象里，`"Event2"` 条目（现均为 `:77`）之后插入：

| 文件 | name | help |
|---|---|---|
| `module/config/i18n/zh-CN.json` | `活动图-3` | `""` |
| `module/config/i18n/zh-TW.json` | `活動圖-3` | `""` |
| `module/config/i18n/en-US.json` | `Event-3` | `""` |
| `module/config/i18n/ja-JP.json` | `イベント海域-3` | `""` |

- 命名格式必须与同文件里 `Event2` 的写法完全一致（现有值：`活动图-2` / `活動圖-2` / `Event-2` / `イベント海域-2`）。
- `help` 保持空字符串（`Event`/`Event2` 都是空）。
- **`Event3` 不是 `Gui.*` 键**，不需要登记 `gui.yaml`，生成器不会删它。
- 保持 JSON 的 2 空格缩进与现有键顺序，不要整体重排、不要改文件末尾换行。

### R8 跑生成器并逐文件判读 diff（必须）

```bash
PY="/d/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/toolkit/python.exe"
"$PY" -m module.config.config_updater
git status --short
git diff --stat
```

**预期 `git status --short` 里的已修改文件恰好这 14 个**（10 个手改 + 4 个生成物）：

```
 M alas.py
 M module/campaign/campaign_status.py
 M module/config/config_manual.py
 M module/config/config_updater.py
 M module/config/argument/task.yaml
 M module/config/argument/override.yaml
 M module/config/i18n/zh-CN.json
 M module/config/i18n/zh-TW.json
 M module/config/i18n/en-US.json
 M module/config/i18n/ja-JP.json
 M module/config/argument/args.json            ← 生成
 M module/config/argument/menu.json            ← 生成
 M module/config/config_generated.py           ← 生成
 M config/template.json                        ← 生成
```

`git status` 里还会出现一个未跟踪的 `?? doc/prompt-event3-task.md`（本工单文件本身），这是预期的，
**不要提交、不要删除**，也不计入上面 14 个。

判读规则（生成器不报错但可能悄悄删东西，必须逐文件看）：

1. `git diff` 里**只允许出现 Event3 相关的 "新增"**。
2. 如果 i18n 或 args.json 出现**删除**，先判断是不是"手写键没在 yaml 里登记"（本工单不涉及 `Gui.*`，
   正常不会发生）。**不要用 `git checkout` 掩盖**，先报告。
3. 如果 4 个 i18n 里 `Task.Event3` 的文案变成了 `"Task.Event3"` 这种点号占位符，说明生成器没读到 R7 的手写值
   —— 检查 R7 的插入位置是否落在 `"Task"` 对象内（不是 `"Menu"`，也不是顶层）。
4. 如果出现与本工单无关的改动（例如别的任务、别的语言段落被重排、文件末尾换行变化），
   把**该噪声文件单独 `git checkout -- <file>`**，并在交付说明里注明。
5. 如果 4 个生成物里缺 Event3（多见于 R1 未改），不要手改生成物，回到 R1。

**生成器跑不起来时**（例如 `ModuleNotFoundError: yaml`）：不要退回去用系统 Python 硬跑、不要 `pip install`。
改成**手工同步 4 个生成物**（这是本工单唯一允许手改生成物的情形），并在交付说明里显著标注"手工同步"：
`args.json` 复制 `Event2` 段改名为 `Event3`（`Scheduler.Command.value` 改 `Event3`，`Campaign.Event.option_*` 原样保留）；
`config/template.json` 同理；`config_generated.py` 复制 `Event2_*` 常量为 `Event3_*`；
`menu.json` 在 `Event.tasks` 里插 `"Event3"`。

## 3. 明确不改的东西（非问题清单）

以下是**看起来像漏改、但确认不需要改**的点，禁止"顺手补全"：

1. **`module/gg_handler/gg_handler.py:307` 已有 `'event3'`** —— 本 fork 早先预留的位置，本次正好对上。不要动它，也不要因为"它凭什么先有"去删。
2. **`module/config/argument/argument.yaml` 的 `TaskBalancer.TaskCall.option` 保持 `[Main, Main2, Main3]`** ——
   TaskBalancer 的语义是"金币不足时切去主线图刷金币"，活动图不是它的目标图，不加 Event3。
3. **`module/config/config_updater.py:624-634` 的 `redirection` 注释块** —— 整段是注释掉的死代码，**不要解注释、不要补 Event3**。
4. **`module/campaign/run.py`、`module/event/*`、`module/config/config.py`、`module/webui/app.py`** —— 零改动。
   `CampaignRun.run()` 已经支持任意 `campaign_event` 文件夹 + 任意阶段名；`webui` 的
   `Campaign.Event` 渲染逻辑不认任务名（唯一特判的是 `GemsFarming`，见 `app.py:417-424`）。
5. **`assets/`、`dev_tools/`、`campaign/`** —— 零改动。本功能不新增素材、不需要 `button_extract`。
6. **`Event` / `Event2` 的既有配置不动** —— 包括它们的 `Scheduler.Enable` 默认值（false）、`Campaign.Name`（D3）、
   以及 `EventGeneral` 的两个上限（PT / 时间）。Event3 与它们**共享** `EventGeneral`：
   PT 上限或时间上限触发时，`Event / Event2 / Event3` 会被一起关掉，这是既有设计，不是 bug。
7. **不新增 `module/event/` 下的类** —— Event3 不需要新任务类，`alas.py` 里直接复用 `CampaignRun`。
8. **不加 `task_delay`** —— `CampaignRun.run()` 的收尾与调度延时由 `StopCondition` 分支负责，
   Event3 与 Event2 走同一条路径；不要为了"新任务"在 `alas.event3` 里加任何延时。
9. **`README.md` / `README_en.md` / `README_jp.md`** —— 不写功能说明。后两个必须保持上游原样，
   否则每次合并上游都冲突。

## 4. 验证边界

### 4.1 允许

- 任意 Read / Grep / Glob。
- `python -m py_compile alas.py module/campaign/campaign_status.py module/config/config_manual.py module/config/config_updater.py module/config/config_generated.py`。
- `python -c "import json;json.load(open(...,encoding='utf-8'))"` 校验 4 个 i18n 与 args.json / menu.json / template.json 可解析。
- 对 4 个 i18n 做键集合对比（改动前后 `Task.*` 键数应各 +1，且四语言新增的键名完全相同）。
- 运行一次配置生成器（命令见第 0 节）。
- `git status` / `git diff` / `git diff --stat`。

### 4.2 禁止

- 启动 ALAS（`gui.py` / `alas.py` / `run.bat` / 任何 `AzurLaneAutoScript(...).run()`）。
- 连接模拟器、执行 `adb` / `uiautomator` / `minitouch` 相关命令。
- 运行 pytest、任何 `tests/` 下的用例、任何需要截图的任务。
- `pip install`、改 `requirements*.txt`、改 `toolkit/`。
- `git commit` / `git push` / `git stash` / `git checkout` 掉本工单的改动。
- 改第 3 节列出的任何文件。
- 修改 `.workbuddy/`、`log/`、`doc/*-shots/`。

### 4.3 无法静态验证、必须移交人工的部分

Event3 是否真的能被调度、GUI 是否真的出现第三张活动图、三队是否真的按 Event → Event2 → Event3 依次执行
—— 这些只能真机跑，全部写进第 5 节，**不要假装验证过，也不要在交付说明里写成"已测试通过"**。

## 5. 人工验证清单（由用户执行）

1. **重启 Alas**（`.py` / yaml / i18n 都不会热加载；只关窗口不算重启）。
2. GUI → 活动（折叠菜单）：应看到 `活动图`、`活动图-2`、`活动图-3` 三项，四语言下文案分别为
   `活动图-3` / `活動圖-3` / `Event-3` / `イベント海域-3`。
3. 点开「活动图-3」：配置组与「活动图-2」逐项一致（Campaign / 停止条件 / 舰队 / 潜艇 / 心情 / 血量控制 / 敌人优先级），
   且 `模式`（Campaign.Mode）**不显示**、`伏击规避` 默认开启。
4. 「活动图-3」的 `活动` 下拉（Campaign.Event）里能看到当期的活动目录（如 `event_20261022_cn`），
   不是只有 `campaign_main`。若为空 → 说明 R1 没生效或生成器没跑。
5. 给三项分别设置**不同的地图与不同的舰队**（例如 Event=D3 用 1/2 队、Event2=C2 用 3/4 队、Event3=B1 用 5/6 队），
   三项都开启。运行调度器，观察日志顺序为 `Event → Event2 → Event3`，且每项都按各自的地图与舰队进入战斗。
6. 打开「活动通用设置」的 PT 上限，跑到上限：日志应出现 `Reach event PT limit`，
   且三项 `Scheduler.Enable` 全部变为 false（这是 `EVENTS` 生效的证据）。
7. 若启用了 TaskBalancer（金币不足切图）：在 Event3 运行期间把金币压到阈值以下，
   应能触发切图（这是 R6 生效的证据）。

## 6. 验收标准（静态可核对）

1. `py_compile` 通过：`alas.py`、`module/campaign/campaign_status.py`、`module/config/config_manual.py`、
   `module/config/config_updater.py`、`module/config/config_generated.py`。
2. 7 个 JSON 文件全部 `json.load` 不抛异常：`module/config/argument/args.json`、`menu.json`、
   `config/template.json`、4 个 `module/config/i18n/*.json`。
3. `args.json` 存在 `Event3` 顶层键，且：
   - 8 个配置组齐全（`Scheduler` / `Campaign` / `StopCondition` / `Fleet` / `Submarine` / `Emotion` / `HpControl` / `EnemyPriority`）；
   - `Event3.Scheduler.Command.value == "Event3"` 且 `display == "hide"`；
   - `Event3.Campaign.Mode.display == "hide"` 且 `value == "normal"`；
   - `Event3.Campaign.AmbushEvade` 存在且 `value == true`；
   - `Event3.Campaign.Event` 含非空 `option_<server>`（`cn`/`en`/`jp`/`tw` 四份）与 `option`。
4. `menu.json` 的 `Event.tasks` 里 `"Event3"` 紧跟 `"Event2"`，且该数组长度为原值 +1。
5. `config/template.json` 的 `Event3` 段与 `Event2` 段的键集合**完全相同**，且 `Campaign.Name == "D3"`。
6. `config_generated.py` 里 `Event3_` 开头的常量数量与 `Event2_` 开头的常量数量相等。
7. 4 个 i18n 各自 `Task.Event3.name` 为 R7 规定文案、`help == ""`；四语言 `Task` 键集合相同（无缺键/多键）。
8. 手写落点齐全（按大小写分别查，方法名是小写的）：
   - `grep -rn "Event3" --include=*.py .` 只命中 `module/config/config_manual.py`、`module/config/config_updater.py`、
     `module/campaign/campaign_status.py`（以及生成物 `config_generated.py`，不计）；
   - `grep -n "def event3" alas.py` 命中 1 处；
   - `grep -rn "Event3" --include=*.yaml .` 只命中 `module/config/argument/task.yaml` 与 `override.yaml`；
   - `module/gg_handler/gg_handler.py` 里的 `'event3'` **不得出现在 `git diff` 中**（它原来就有）。
9. `git diff --name-only` 与第 R8 节列出的 14 个文件完全一致，无第 15 个文件。
10. 工作区除本工单文件 `doc/prompt-event3-task.md` 外**无新增文件**（不新建任何 `.py` / `.png` / `.yaml`）。

## 7. 交付物

1. `git diff`（工作区，未提交，覆盖全部 14 个文件）。
2. **静态自查报告**：逐条对应第 6 节的 10 条验收标准，写明核对方式（用了哪条命令/读了哪个键）与结果。
3. **≤5 行交付说明**：生成器是否成功、`git status` 是否恰好 14 个文件、有无噪声文件被 `git checkout` 掉、
   是否用了手工同步生成物的降级方案、遗留风险。
4. 若发现本工单的描述与仓库实际不符（例如某个行号已漂移、某处已存在 Event3 的痕迹），
   **先报告差异再动手**，不要自行改需求。
