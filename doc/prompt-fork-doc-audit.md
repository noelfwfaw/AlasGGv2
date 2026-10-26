# 工单：fork 自有代码的开发文档合规整改（A / B 批）

> 交付对象：编码 Agent。本文自包含，不需要访问任何历史对话。
> 仓库：`C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript`（ALAS fork，分支 `master`）。
> 上游基线：`upstream/master`（`3b48d7e85`）是本地 `HEAD` 的祖先，`git diff upstream/master..HEAD` 即为 **fork 自有改动全量**。

## 0. 硬约束（不可妥协）

- **禁止运行 ALAS / gui.py / alas.py / 任何任务流程；禁止连接模拟器、adb、uiautomator；禁止 `pip install`。**
- **禁止 `git commit` / `git push` / `git stash`**，改动只留在工作区。**禁止 `git checkout` 掉本工单的改动**。
- 允许的动态命令**只有一条**：
  `/d/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/toolkit/python.exe -m module.config.config_updater`
  （A 批校验用；内部会 `os.chdir` 到仓库根）。系统自带 Python 3.13 缺 yaml，**跑不了，不要试**。
- 允许的静态命令：`python -m py_compile <文件>`、`python -c "import json/yaml; ..."`、`git diff` / `git status` / `grep`。
- **范围只限 fork 自有的 `.py` / `.yaml` 文件**（见第 1 节清单）。**上游文件一律不动**，
  特别是 `module/storage/storage.py`、`module/storage/ui.py`、`module/base/base.py`、`module/device/device.py`
  里的既有逻辑 —— 那里也有 `wait_until_stable`、`if not self.appear`，**不是本工单的目标**，改了就是越界。
- 本仓库已安装 `alas-dev` 技能，开工前请加载；与本文冲突时以本文为准。
- 本工单**不新增任何功能、不改任何用户可见行为**（唯一例外是 B1、B4 修的是"错误行为"）。

## 1. 背景与取证

fork 在 2024-2026 年间加了一批功能（GG 倍率、Dashboard/日收入、仓库扫描、外观装备统计、MAA 插件、
模拟器自愈、GUI 修复），其中相当一部分没有遵循上游开发文档
（`4.4 State loop`、`3.4 Exception`、`4.3 OCR`、`6.1 GUI Option`、`1. Start` 的注释与文件规模约定）。
本工单是**已核实的偏差清单**，不是"全面体检"——每一条都给了 `文件:行号` 或可复现的命令。

### 1.1 已核实偏差总表

| 编号 | 级别 | 类型 | 现象 | 证据 |
|---|---|---|---|---|
| A1 | **高** | 数据丢失 | `Gui.Statistics.GearSkin*` 共 15 个键在 i18n 里手写、**未在 `gui.yaml` 登记**。下次任何人跑配置生成器，四语言共 60 条文案被静默删除（`generate_i18n` 是重建而非合并） | `module/config/argument/gui.yaml:83-90` 的 `Statistics:` 只有 `NoData/Year/Month/Date/Total/CubeTotal/GemTotal` 7 个键；`module/config/i18n/*.json` 的 `Gui.Statistics` 有 22 个 |
| B1 | **高** | 缺陷 | `set()` 里 `if success: raise GameStuckError` 语义反转：`timeout()` 返回 `True` 表示**按时完成（成功）**，于是"开 GG 成功"反而抛异常；真正超时（`False`）时静默继续跑任务，倍率其实没开 | `module/gg_handler/gg_handler.py:104-107`；语义见 `module/base/timer.py:6-25` |
| B2 | 高 | 缺陷 | `handle_u2_restart()` 丢弃 `timeout()` 返回值：`restart_atx` 超时后什么都不会发生，后面直接 `u2.connect(...)` | `module/gg_handler/gg_handler.py:147-161` |
| B3 | 中 | 缺陷 | `restart()` 是 `while True` + `sleep(5)` + `except GameStuckError` 继续重试，同时命中文档两条禁令（"点击-等待"、"捕获 `GameStuckError` 继续跑"），且无次数上限 | `module/gg_handler/gg_handler.py:66-89` |
| B4 | **高** | 缺陷 | OCR 兜底值直接当索引用：`ship_info_get_level_exp()` 两路 OCR 都失败时返回 `(0, 0)`，调用方 `LIST_SHIP_EXP[level - 1]` 变成 `LIST_SHIP_EXP[-1]`（满级经验 4425000）→ 误判"全员满经验"→ `task_delay(server_update=True)` + `task_stop()`，当天不再检查 | `module/os/ship_exp.py:52-89`（`return level, exp` 在 `:78`、`:81`）+ `module/os/tasks/check_leveling.py:38` |
| B5 | 中 | 隐患 | `MaterialScanner` 两个长循环从不调 `device.stuck_record_clear()`：`screenshot()` 内的 60s 卡死计时器只在 `stuck_record_clear()` 里重置，`appear()` 只往 `detect_record` 里加名字、**不重置计时器**。`MAX_PAGE = 60`、`reach_top` 40 次迭代每次 `time.sleep(0.8)`，累计必然超过 60s | `module/storage/scanner.py:170`（`MAX_PAGE`）、`:598-620`（`reach_top`）、`:706-779`（`_scan_pass`）；全文 `grep -n "stuck_record\|appear" module/storage/scanner.py` **零命中** |
| B5′ | 佐证 | 现场 | 该故障模式在本仓真实发生过：StorageScan 进入材料页的循环在 60s 后 `Wait too long` 抛 `GameStuckError` | `log/2026-10-21_whitelist_walk.txt:265-275`（`19:00:33 storage enter material` → `19:01:33 Wait too long` → `Waiting for {'DISASSEMBLE', 'DISASSEMBLE_CANCEL', 'STORAGE_CHECK', 'MATERIAL_CHECK'}`） |
| C1 | 中 | 设计债 | `wait_settled()` 用 `time.sleep(0.4)` + 帧差实现"画面稳定"，是文档点名的"等价点击等待"的第三种自造版本（上游 `wait_until_stable` 本身也是被点名的反例） | `module/storage/scanner.py:366-388` |
| C2 | 中 | 设计债 | `_wait_appear()` 手写等待 helper，框架已有 `Base.loop(timeout=...)`（`module/base/base.py:127`，支持 `for...else` 超时分支） | `module/gear_skin/gear_skin.py:249-262`；`enter_event_shop()` 里还有同型的 `while not timer.reached(): screenshot…`，`:218-247` |
| C3 | 低 | 隐患 | `_scan_pass` 每轮 `click_record_clear()`：`click_record_add()` 只在 `device.click()` 里调用（`module/device/device.py:384`），**swipe 不写记录**，所以这行对滑动无效，却会顺带清掉同循环内真实点击的"连点保护" | `module/storage/scanner.py:722-723`；`:608` 同型 |
| C4 | 低 | 隐患 | `scan_material()` 直接改写模块级 `global WHITELIST` 且用完不恢复，同进程后续扫描会沿用被覆盖的白名单 | `module/storage/scanner.py:632-634` |
| D1 | 低 | 规范 | 注释中文，文档要求英文。实测行数：`scanner.py` 73、`gear_skin/assets.py` 56、`gear_skin/gear_skin.py` 28、`gear_skin/category.py` 18、`MaaBridge/maa.py` 42、`MaaBridge/module/handler/assist.py` 28、`gg_adb.py` 12、`gg_handler.py` 7（合计 ≈ 264 行） | `grep -cP '^\s*(#|""").*[\x{4e00}-\x{9fa5}]' <file>` |
| D2 | 低 | 规范 | 文件超 500 行：`storage/scanner.py` 898、`gear_skin/gear_skin.py` 897、`MaaBridge/module/asst/updater.py` 813、`MaaBridge/maa.py` 737、`MaaBridge/module/handler/handler.py` 530 | `wc -l` |
| D3 | 低 | 死代码 | `gg_screenshot.py`(263 行) 与 `gg_adb.py`(172 行) **全仓零引用**（只有 `gg_handler.py:3/98/122` 的注释里出现）；`GGMethod` 配置项只剩 `u2` 一个选项 | `grep -rn "GGScreenshot" --include=*.py .` 只命中注释 |
| D4 | 低 | 规范 | GUI 渲染路径里 `get_today()` 抛错时 `except Exception: pass`，收入提示坏掉会永久无提示（同文件 `:599` `:710` `:817` 的 `except Exception` 都是"读损坏 JSON 时给默认值"，属合格写法，**不要一起改**） | `module/webui/app.py:1135-1136` |
| D5 | 低 | 规范 | `logger.info('%s ...', x)` 与全仓 f-string 风格不一致 | `module/storage/scanner.py:750`、`:764-765` |

### 1.2 已排除项（查过、确认不是问题，不要改）

1. **3.8+ 语法**：对 `git diff --name-only upstream/master..HEAD -- '*.py'` 的全部 73 个文件扫过 `:=`、
   PEP 585 返回注解（`-> list[int]`）、f-string `=`，**零命中**，运行实例的 3.7.6 可跑。
2. **`module/storage/storage.py` / `module/storage/ui.py`**：这两个文件**上游就有**（`git ls-tree upstream/master module/storage/`
   只有 `assets.py` / `storage.py` / `ui.py`）。它们里面的 `wait_until_stable`、`if not self.appear` 是上游历史代码，
   **不在本工单范围**。同目录只有 `scanner.py` 是 fork 新增的。
3. **`gg_handler/assets.py` 里的 `np.int64(...)`**：`dev_tools/button_extract.py` 的产物，手改无意义。
4. **`module/gear_skin/assets.py` 是手写坐标表**（不是 `Button`/`Area`，没走 `button_extract`）：这是
   `doc/prompt-gearskin-statistic.md` 明确规定的设计（OCR 区域常量），有工单背书，**本次不动**。
5. **`MaaBridge` 里的 `while` 循环与 `sleep`**：驱动的是 MAA 桌面窗口和明日方舟，不是碧蓝的状态循环，
   不适用 `4.4 State loop`（但其注释语言、文件长度适用 D1/D2）。
6. **`GGMethod` 只剩一个选项**：GUI 会渲染成只读标签，不会误导用户，D3 里只作"顺带清理"，不单独修。

## 2. 施工单

**本工单只交付 A 批 + B 批。C / D 批只写诊断，不动手** —— 除非用户明确点名。
每一批、每一项都要**独立可评审、独立可回滚**（做完一项就 `git diff` 一次，不要攒）。

### A 批 · 防止文案被生成器删掉（必须先做，零风险）

#### A1 登记 GearSkin 的 GUI 文案（必须）

1. 打开 `module/config/argument/gui.yaml` 的 `Statistics:` 段（`:83-90`），**按 i18n 里的现有顺序**追加 15 个键（值为空，
   即 `键名:` 后为空）：
   `GearSkin`、`GearSkinLatest`、`GearSkinBox`、`GearSkinAmount`、`GearSkinChange`、`GearSkinKinds`、
   `GearSkinTotal`、`GearSkinHistory`、`GearSkinIncomplete`、`GearSkinBullet`、`GearSkinTorpedo`、
   `GearSkinShell`、`GearSkinPlaneTorpedo`、`GearSkinPlaneFighter`、`GearSkinPlaneBomber`。
2. 跑生成器（命令见第 0 节）。
3. **判读 diff（这一步是本项的全部价值）**：
   - `git status` 里**只应有** `module/config/argument/gui.yaml`。
   - 4 个 `module/config/i18n/*.json` **必须零 diff**（`generate_i18n` 的 GUI 分支是
     `deep_load(keys=['Gui', group], words=(key,))`，值取自旧文件）。**若出现删除，立刻停下报告，不要手补。**
   - `args.json` / `menu.json` / `config_generated.py` / `config/template.json` **必须零 diff**（`gui.yaml` 只喂 i18n）。
   - 若 i18n 出现纯格式噪声（如键顺序重排、末尾换行），先确认没有丢键，再把该文件 `git checkout --` 并把噪声写进交付说明。

### B 批 · 正确性修正（每项独立提交评审）

#### B1 修正 `set()` 的成功/超时判定（必须）

`module/gg_handler/gg_handler.py:104-107`。当前 `timeout()` 的返回值语义是"按时完成"，
`if success: raise GameStuckError` 与之相反。要求：

- 改成"**超时才报错**"，且报错后不要再往下走（当前 `raise` 的位置在 `set()` 中间，
  抛给 `alas.py:670-680` 的 `except GameStuckError` 处理链）。
- 成功路径正常返回，日志里明确写出 `GG enabled, factor=...`。
- 改动不超过 3 行，**不动** `GGU2.set_on` 的实现、不动 `timeout()` 函数本身（见第 3 节第 1 条）。

#### B2 `handle_u2_restart()` 处理超时（必须）

`module/gg_handler/gg_handler.py:147-161`。`timeout(self.device.restart_atx, 60)` 的返回值被丢弃。要求：

- 收到"超时"时走与 `except` 相同的恢复路径（或至少 `logger.warning` 并让调用方知道），
  不要出现"超时了但当作成功继续"的路径。
- 现有两处 `except Exception: ... self.device.emulator_start()` 保留，但补一句 `logger.exception(e)` 而非只 `critical`。

#### B3 `restart()` 去掉 `sleep` 与 `GameStuckError` 继续重试（必须）

`module/gg_handler/gg_handler.py:66-89`。要求：

- 删掉 `sleep(5)`（改为有限次数重试；两次尝试之间用 `self.device.screenshot()` 或
  `self.device.stuck_record_clear()` 让节奏由设备决定，不要固定睡）。
- `GameStuckError` **不再 `except` 后继续**：让它冒泡（`app_restart` 的卡死由上层兜底），
  或把它折进统一的重试计数后**抛出**，不能吞。
- 重试次数上限写成常量（如 `MAX_RESTART_ATTEMPT = 3`），超限抛 `RequestHumanTakeover`。
- 顺手把那句 `# 使用无限循环`、`# 修改这里：...` 这类过程性中文注释删掉（改完这段逻辑它们已经失效）。

#### B4 `check_leveling` 的 OCR 兜底守卫（必须）

`module/os/tasks/check_leveling.py:33-40`。要求：

- 调用 `ship_info_get_level_exp()` 后先判 `level < 1`（等价于两路 OCR 都失败），
  此时**记 `logger.warning` 并放弃本次检查**（不置 full、不 `task_delay`、不 `task_stop`），
  让下一次调度再看，**不能**用 `LIST_SHIP_EXP[-1]` 继续算。
- `exp` 读到 0 但 `level > 0` 的情况允许继续（0 经验是合法值），不需要额外分支。
- 不动 `module/os/ship_exp.py` 的 OCR 类实现。

#### B5 `MaterialScanner` 补 `stuck_record_clear()`（必须）

`module/storage/scanner.py` 的 `reach_top()`（`:598-620`）与 `_scan_pass()`（`:706-779`）：
在两个循环的**每一轮开头**加一次 `self.device.stuck_record_clear()`，与上游战斗循环的做法一致
（参照 `module/combat/combat.py:242`、`module/campaign/run.py:409`）。

理由：`Device.screenshot()` 每次都调 `stuck_record_check()`，60s 没被 `clear` 就抛 `Wait too long`；
`appear()` **只登记名字、不重置计时器**。`MAX_PAGE = 60` + 每页固定 `time.sleep(0.8)` 必然越界。
加了之后不影响任何判定逻辑，只是不再误报 —— **不要顺手删 `time.sleep`，那是 C1 的事**。

## 3. 明确不做什么（防止扩散）

1. **不重写 `module/base/timer.py` 的 `timeout()`**。它用 daemon Thread + `join` 实现"超时"，
   超时后线程仍在跑并被遗弃，会与被放弃的 `app_restart` / `set_on` 并发操作同一个 `Device`。
   这是个真问题，但正确解法要求重做 GG 的启动时序（属于功能重构，必须真机验证），
   本工单**只允许在 B1 里绕开它的语义陷阱**，不允许改它的实现。
2. **不动 GG 的 u2 交互流程**（`gg_u2.py` 的 `set_on` / `_run`：`while 1` 每轮 `sleep(0.5)`、
   `for _i in range(1)`、`finally: pass`、`if ggdata['gg_on']: pass` 都是历史遗留）。理由同上：无人值守的
   生产功能，改错了比不改更糟。已在第 1 节登记，留待专门的真机工单。
3. **不动 C1-C4**（等待语义、`click_record_clear`、`global WHITELIST`）。C5 之外它们都要求真机比对画面节奏，
   属"改了要看运行效果"的改动，不在静态验收能力范围内。
4. **不动 D1-D5**（中文注释、拆分大文件、删死代码）。这些是纯风格项，改动面大（≈264 行注释、≈4000 行搬迁）、
   收益只是可维护性，且会污染 `git blame` 与上游合并。等用户明确要求时单独立项。
5. **不动上游文件的任何一行**（见第 0 节）。判断标准：`git diff upstream/master..HEAD -- <file>` 为空 = 上游文件。
6. **不新增配置项、不改 `argument.yaml` / `task.yaml` / `dashboard.yaml` 的语义**（A1 只改 `gui.yaml` 的键登记）。
7. **不跑生成器以外的任何东西**，尤其不要为了"验证一下"去启动 GUI 或任务。

## 4. 验证边界

### 4.1 允许

- Read / Grep / Glob 任意文件；`git diff` / `git status` / `git ls-tree` / `git grep`。
- `python -m py_compile` 本工单改到的 3 个 `.py` 文件。
- `python -c "import yaml,json; yaml.safe_load(...); json.load(...)"` 校验 YAML/JSON 可解析。
- 运行一次配置生成器（A1 校验）。
- 用 `python` 脚本**只读**比对 `gui.yaml` 的键与 `i18n['Gui']` 的键集合（这是 A1 的验收手段，鼓励做）。

### 4.2 禁止

- 启动 ALAS / GUI / `alas.py` / 任何任务；连接模拟器 / adb / uiautomator。
- `pip install`、改 `requirements*`、改 `toolkit/`。
- `git commit` / `git push` / `git stash` / `git checkout` 掉本工单的改动（噪声文件的 `checkout` 除外）。
- 改第 3 节列出的任何位置。
- 在 `doc/`、`.workbuddy/`、`log/` 下新建或修改文件。

### 4.3 只能人工验证的部分

B1/B2/B3/B5 的**运行时效果**（GG 真能开起来、卡死不再误报）只能真机验证，全部列进第 5 节。
**不要在交付说明里写"已验证功能正常"。**

## 5. 人工验证清单（由用户执行）

1. **A1**：重启 Alas → 「工具 → 数据统计」页面，外观装备统计区块的标题/表头/图例仍是中文
   （说明 `t('Gui.Statistics.GearSkin*')` 仍能取到文案）。再跑一次生成器，`git status` 仍只应有 `gui.yaml` 一处（上一次提交后）。
2. **B1**：开启 GG，观察日志：
   - 正常路径：出现 `GG enabled` 类日志，**不再出现** `GameStuckError`，且调度器**不再空转一轮**
     （旧行为是每次开 GG 后 `check_fail += 1` 再 `continue`）。
   - 失败路径（把 `GGHandler.Timeout` 调到极小值如 1）：日志应明确报超时/失败，**不再静默进任务**。
3. **B3**：手动制造一次 `Restart` 失败（例如关掉模拟器后触发重启），确认日志里重试次数有上限、
   最终抛 `RequestHumanTakeover` 而不是无限重试。
4. **B4**：在 `OpsCheckLeveling` 任务里制造一次"读不到等级"（可临时把 OCR 区域挡黑），
   确认日志出现 warning 且**任务没有**被 `task_delay` 到明天、`OpsiCheckLeveling_LastRun` 未被更新。
5. **B5**：仓库物品很多（>30 页）的账号跑一次 StorageScan，日志不应出现 `Wait too long`；
   扫描结果（`log/storage_scan/<实例>/records.json`）与改动前一致。

## 6. 验收标准（静态可核对）

1. `git diff --name-only` 恰好命中 **4 个文件**：
   `module/config/argument/gui.yaml`、`module/gg_handler/gg_handler.py`、
   `module/os/tasks/check_leveling.py`、`module/storage/scanner.py`
   —— 加可能的 4 个 i18n 噪声（应尽量为 0），**不允许出现第 5 个业务文件**。
2. `gui.yaml` 的 `Statistics:` 键集合 ⊇ {上述 15 个}；`i18n` 四语言的 `Gui.Statistics` 键集合与
   `gui.yaml` 完全一致（脚本比对，双向差集为空）。
3. 跑生成器后 `git diff --stat` 不包含 `args.json` / `menu.json` / `config_generated.py` / `config/template.json`。
4. `grep -n "raise GameStuckError" module/gg_handler/gg_handler.py` 的命中上下文里，
   该分支的条件是"**超时**"而不是"成功"（读代码即可判断，不接受"看起来对"）。
5. `grep -n "sleep" module/gg_handler/gg_handler.py` 在 `restart()` 范围内零命中。
6. `grep -n "stuck_record_clear" module/storage/scanner.py` 命中数 = 2（`reach_top` 与 `_scan_pass` 各一）。
7. `check_leveling.py` 中 `LIST_SHIP_EXP[level - 1]` 之前存在 `level < 1` 的分支，且该分支内没有
   任何 `task_delay` / `task_stop` / `all_full_exp = True`。
8. `py_compile` 通过：`module/gg_handler/gg_handler.py`、`module/os/tasks/check_leveling.py`、
   `module/storage/scanner.py`、`module/config/config_generated.py`（生成器产物需可编译）。
9. `git diff` 中**不出现**以下文件的任何改动：`module/storage/storage.py`、`module/storage/ui.py`、
   `module/base/base.py`、`module/base/timer.py`、`module/gg_handler/gg_u2.py`、`module/gg_handler/gg_screenshot.py`、
   `module/gear_skin/*`、`submodule/**`、`doc/**`。
10. 交付说明逐条回答：A1 的生成器 diff 是否如预期为 0、B1 的判定方向、B3 的重试上限值、
    B4 的放弃路径、B5 的两处 `clear` 位置。

## 7. 交付物

1. `git diff`（工作区，未提交）。
2. **静态自查报告**：逐条对应第 6 节的 10 条，写明核对命令与实际输出。
3. **≤5 行交付说明**：生成器执行结果、`git status` 文件数、有没有噪声文件、哪几条批项**没有**做及原因。
4. 若发现本工单的某个行号已漂移或结论与代码不符，**先报告差异再动手**，不要自行改需求。
5. 第 1 节 C/D 批的内容**不要动**，但请在交付说明里确认"已阅读并理解为何不动"。
