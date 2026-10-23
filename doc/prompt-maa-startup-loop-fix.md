# 修复任务：MAA 插件「关游戏 → 立刻排开始唤醒 → 又启动游戏」自激死循环

> 提示词使用说明：本文件全文可直接作为任务提示词交给编码代理/工程师。
> 文中行号基于当前 master，后续可能有偏移，**认函数名与日志原文比认行号可靠**。

## 0. 环境与约束

- 代码位置：`submodule/AlasMaaBridge`（MAA 插件独立工程，不是主模块）。
- 运行实例使用 `toolkit/python.exe`（Python 3.7.6）。**禁止** 3.8+ 语法（walrus、`dict |`、f-string `=` 说明符）与任何新依赖。
- 该插件继承主模块的 `AzurLaneAutoScript`（`maa.py:29` 的 import），复用其 `loop()` / `wait_until()`。改子类时**不要**动主模块 `alas.py`，除非本文件明确要求。
- 只改 MAA 插件，不要碰 `Maa/` 下的资源与二进制。

## 1. 现象（用户实测，日志已确认，请直接采信，不要重新排查）

配置：`Optimization.WhenTaskQueueEmpty = close_game`（关闭游戏）。

```
16:58:58  MaaAward 开始执行
16:59:12  MaaAward 完成，Delay 到 2026-10-24 04:00
16:59:12  No task pending → 下一个待执行任务 MaaStartup @ 2026-10-24 04:00
16:59:12  所有任务已完成，正在关闭游戏
16:59:13  MAA START → Task name: CloseDown → 16:59:14 完成
16:59:14  Task call: MaaStartup            ← 关键：把开始唤醒强制拉到「现在」
16:59:19  Config "alas2" changed at 2026-10-23 16:59:14   ← 上一条自己写盘触发的重载
16:59:19  重载后 Pending tasks: ['MaaStartup'] → 立刻执行
16:59:20  MAA START → Task name: StartUp, start_game_enabled=True → 游戏被启动
```

然后 MaaStartup 执行完会 `task_delay` 回 04:00 → 队列又空 → 又走等待分支 → 又关游戏 →
又 `task_call('MaaStartup')` → 又启动游戏……

**周期约 30~90 秒，无限循环，游戏被反复开关。**

用户截图同时印证了循环中间态：调度器「运行中：无任务」、
「队列中：开始唤醒 2026-10-23 16:59:14」、「等待中：领取奖励 2026-10-24 04:00:00」。

## 2. 根因（三条叠加，缺一不成环）

代码在 `maa.py` 的 `ArknightsAutoScript.get_next_task()`（重写调度），等待分支大致是：

```python
if task.next_run > datetime.now():
    ...
    elif method == 'close_emulator':
        self.maa_stop_during_wait(task.next_run)
        needs_startup = True
    else:                                    # close_game
        self.maa_close_game_during_wait()
        needs_startup = method == 'close_game'
    if needs_startup:
        self.config.task_call('MaaStartup')  # ← ① 罪魁
    self.device.release_during_wait()
    if not self.wait_until(task.next_run):
        del_cached_property(self, 'config')
        continue
    if needs_startup:                        # ← ② 配套的二次 continue
        del_cached_property(self, 'config')
        continue
```

**① `task_call()` 的语义是「立刻执行」，不是「预排到某个时间点」。**
`task_call(task, force_call=True)`（主模块 `module/config/config.py`）会无条件把
`NextRun` 设为 `now`、`Enable=True` 并立即落盘。日志里 `MaaStartup.Scheduler.NextRun=...16:59:14`
就是它写的。所以「预排一次开始唤醒」实际效果 = **立刻启动游戏**。
而 MaaStartup 的 `StartUp` 任务带 `start_game_enabled: True`（`handler.py` 里硬编码），
于是「关游戏 → 立刻开游戏」在同一个函数调用里完成。

**② 这行 `task_call` 自己写盘，会触发 `wait_until()` 的配置变更检测。**
调用方在阻塞前已 `start_watching()`（`maa.py:639`），`task_call` 之后文件 mtime 必然变化，
`should_reload()` 为真 → `wait_until()` 返回 `False` → `continue` 重进循环。
日志里 `Config changed at 2026-10-23 16:59:14` 就是**插件自己写的**，不是用户操作。
所以循环不需要任何外部输入，能自己转起来（顺带每轮重建一次 MaaCore 连接，
日志里重复出现的 `SHOW DEPLOY CONFIG` / `UuidGot` / `Connected` 就是这笔额外开销）。

**③ 关闭与唤醒的触发条件互相喂饭。**
关闭的条件是「存在等待期」（`task.next_run > now`），唤醒的条件是「队列空/游戏不在」，
两者在「队列里只剩一个远处的 MaaStartup」这个状态下同时成立，
于是 A 触发 B、B 完成后又回到 A，形成闭环。

**核心认知：`MaaStartup` 既是「任务」又是「启动器」。** 一旦它被 `task_call` 拉到现在执行，
它就会把游戏打开；而打开游戏之后队列依然是空的，于是又回到「关闭」分支。
这与主模块不同——主模块的任务本身负责拉起游戏，不存在「预排一个必然启动游戏的任务」这种写法。

## 3. 必须建立的不变量

> **同一个 `now` 时刻，只能有一个动作处于活跃状态：要么「因为要长时间等待而关闭」，要么「因为任务到点而唤醒」。**

唤醒的职责应**唯一**落在两处（都已在代码中存在）：

1. **任务边界判据**：`get_next_task()` 里 `task.next_run <= now` 之后的
   `emulator_check()` → `game_alive()` → `task_call('MaaStartup')`（`maa.py:675-693`）。
2. **MaaStartup 自身的调度**：它的 `NextRun` 被 `task_delay` 到 `server_update`（如 04:00），
   而调度器在同刻任务中优先取 MaaStartup（日志 `Pending tasks: ['MaaStartup', 'MaaInfrast']` 可证），
   所以任务到点时天然是「先启动游戏，再跑业务任务」。

**因此等待分支里不需要、也不允许再主动唤醒游戏。**

## 4. 需求

### R1. 删除等待分支里的主动唤醒（核心修复）

- 删除 `needs_startup` 变量、`if needs_startup: self.config.task_call('MaaStartup')`，
  以及紧随 `wait_until()` 之后的 `if needs_startup: del_cached_property(...); continue`。
- `close_game` 与 `close_emulator` 两条分支**都要删**——`close_emulator` 的
  `needs_startup = True` 同样会把 MaaStartup 拉到现在，同样死循环（而且更贵：每轮还要启停模拟器）。
- 等待分支改完后只做三件事：判定是否要关闭（含 `EMULATOR_CLOSE_MIN_WAIT` 阈值判断）、执行关闭、
  `wait_until(task.next_run)`。
- 保留 `if not self.wait_until(...): del_cached_property(self, 'config'); continue`（真实的配置变更仍需重载）。

### R2. 唤醒职责收口

- 保留 `maa.py:675-693` 的 `emulator_check()` → `game_alive()` → `task_call('MaaStartup')`，
  并保留 `if task.command != 'MaaStartup'` 这个跳过条件（MaaStartup 自己会启动游戏，不需要前置判据）。
- 该处的 `_maa_wake_attempted` 防死循环逻辑保留不动。
- 注意并写进注释：`task_call('MaaStartup')` 会 `Enable=True`，即使用户在 GUI 里禁用了开始唤醒也会被
  **主动复活**。如果希望尊重禁用状态，在 `task_call` 前检查 `self.config.MaaStartup_Scheduler_Enable`，
  为 `False` 时改为 `logger.critical('开始唤醒已被禁用，无法自动拉起游戏')` + `raise RequestHumanTakeover`。
  （二选一即可，不要两边都做。）

### R3. 自写盘不得触发自身的变更检测

- 等待分支内**不应再有任何写配置的动作**（R1 删掉 `task_call` 后已满足）。
- 若未来在该分支内必须写盘（例如写回漂移后的 serial），写完后必须重新
  `self.config.start_watching()` 刷新基准，否则会把「自己写的」当成「用户改的」（现状），
  或者相反——把用户在写盘到 `start_watching` 之间的真实改动并进基准而漏掉（P2 那类问题）。
- `maa_check_package_changed()` 在 `start_watching()` 之前调用，顺序正确，不要挪动。

### R4. 「关→启」循环护栏（兜底，必须有）

上面 R1 能消除当前这条链，但同类循环很容易在后续改动中再次出现（例如又有人在等待分支里排任务），
所以要有一个与具体实现无关的看门狗。**语义定为「连续进入等待分支却从不执行实质任务」**：

- 实例属性 `self._maa_wait_enters = []`（`datetime` 列表）。
- 每进入一次等待分支（`task.next_run > now`），追加当前时间并剔除 600 秒（10 分钟）之前的记录。
- 若剔除后长度 `>= 3`，判定为循环：`logger.critical` 打出每次进入的时间、当时的关闭方式
  （close_game / close_emulator / stay_there）、当时的 `task.command`，然后 `raise RequestHumanTakeover`。
- 只要有**非 MaaStartup** 任务真正开始执行（在 `break` 前判断 `task.command != 'MaaStartup'`），
  清空该列表。
- **禁止**用「距上次关闭 < 180 秒就报错」这类判据：用户关游戏后一分钟手动加任务属于正常操作，
  会被误杀。必须用「连续进入等待且期间没有任何实质任务执行」这个条件。

### R5. 唤醒失败时的诊断信息

`game_alive()` 判定为假时，日志要能自证是「游戏真没开」还是「包名/镜像问题」：

- 打出当前 `client_type` 与 `game_package()` 得到的包名；
- 若 `game_package()` 返回 `None`，明确说明「包名未知，已跳过判据」（当前行为正确，只是要可见）；
- 在 `RequestHumanTakeover` 之前，用一次 `pm list packages` 抓取设备上含 `arknights` / `hypergryph` /
  `YoStar` / `txwy` 的包名并打印（失败只告警，不影响流程）。

> 背景：本机曾因设备上装的是 `com.hypergryph.arknightt`（多一个 t）而让 MaaCore 连续 5 轮
> `am start` 失败、被包装成 `Disconnect: Reconnect failed`，白耗 50 秒。这条日志能省掉那类排查。

## 5. 明确禁止的做法

1. **不要**把「等待期唤醒」改成 `task_delay('MaaStartup', 任务前 N 分钟)`。
   MaaStartup 完成后队列仍是空的，会再次进入等待分支、再次被提前，同样死循环。
2. **不要**指望 `_maa_wake_attempted` 能挡住这个循环。循环中唯一的任务就是 MaaStartup，
   而该标志的检查点被 `task.command != 'MaaStartup'` 排除在外，永远不会触发。
3. **不要**在等待分支里 sleep / 轮询等待 MaaStartup 完成——那会把等待期变成阻塞，
   并重演「阻塞期间 GUI 操作被吞」的老问题。
4. **不要**为了「避免误关」而给 `close_game` 强行套用 `EMULATOR_CLOSE_MIN_WAIT` 阈值。
   那是模拟器启动成本的阈值；关游戏只需 `app_stop` 级别代价，语义不同。若确实想加，
   另立常量并单独确认，不要混用。
5. **不要**改 `Maa/resource/config.json`、`MaaCore` 参数或 `StartUp` 的 `start_game_enabled`。
6. **不要**在修完后把 `MaaStartup` 从调度优先级里移除或被 `get_next()` 过滤掉。

## 6. 验收标准（逐条自测并给出日志片段）

1. **主场景**：`WhenTaskQueueEmpty = close_game`，队列只剩 `MaaStartup @ 次日 04:00`。
   日志必须为：一次「所有任务已完成，正在关闭游戏」→ 一次 CloseDown → 进入等待，
   之后 **10 分钟内不再出现** `Task call: MaaStartup` / `MAA START` / `Config changed`。
2. **close_emulator**：同上，关闭模拟器后不再出现 `MAA START`，直到下一个任务到点。
3. **反例·用户意图不能被吞**：等待期内手动在 GUI 点「开始唤醒」，必须立即执行
   （日志出现 `Task call: MaaStartup` 与真实的 `Config changed`，时间与操作一致）。
4. **反例·正常插入任务**：等待期内手动加一个 `MaaFight`，应立即执行；
   由于游戏已关闭，任务边界先排 MaaStartup 启动游戏，再跑 MaaFight，**只发生一次，不循环**。
5. **护栏**：人为制造循环（例如临时在等待分支里恢复 `task_call('MaaStartup')` 自测）时，
   第 3 次进入等待应触发 critical 并 `RequestHumanTakeover`，日志能说清原因。
6. **回归 · stay_there**：不关游戏时不出现任何额外 `MAA START`，任务链时间与修改前一致。
7. **回归 · close_game 正常日**：04:00 MaaStartup 先执行并启动游戏，随后业务任务正常接续，
   全天不出现「启动后 60 秒内又被关闭」。
8. **自写盘检测**：正常运行时 `Config changed` 只应在真实 GUI 操作后出现，且时间戳与操作一致；
   不应出现「插件刚写完配置，5 秒后自己报 Config changed」。
9. **无回归**：`emulator_check()` 拉起模拟器、`game_alive()` 判假插唤醒、`maa_reconnect()`
   这三条既有能力仍然可用（可临时 `adb kill-server` 或手动关游戏验证）。

## 7. 交付物

1. 修改后的 `submodule/AlasMaaBridge/maa.py`：等待分支精简 + 护栏 + 诊断日志。
2. 一份简短的改动说明（改了什么、为什么、对应哪条需求）。
3. 第 6 节 1~9 条的自测结果，每条附关键日志行；做不到的注明原因与风险。

## 8. 若发现判断有误

本文件的每条结论都标了可验证的位置（函数名 + 日志原文 + 时间戳）。
如果你在代码里发现 R1 描述的 `task_call('MaaStartup')` 已不存在，或等待分支已被重构，
**请先停下来说明现状与证据**，不要按过期的前提硬改。
