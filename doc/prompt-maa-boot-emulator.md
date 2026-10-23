# 修复任务：MAA 插件启动实例时不拉起模拟器，且关闭动作打在离线设备上抛 RequestHumanTakeover

> 提示词使用说明：本文件全文可直接作为任务提示词交给编码代理/工程师。
> 文中行号基于当前 master（含 commit 662cfb54e 的循环修复），后续会偏移，**认函数名与日志原文比认行号可靠**。

## 0. 环境与约束

- 代码位置：`submodule/AlasMaaBridge`（MAA 插件独立工程）。
- 运行实例使用 `toolkit/python.exe`（Python 3.7.6）。**禁止** 3.8+ 语法与新依赖。
- 该插件继承主模块 `AzurLaneAutoScript`，复用 `loop()` / `wait_until()`。**不要**修改主模块 `alas.py`。
- 修改后需要同步到运行实例目录（如 `D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript`），否则运行的是旧代码。

## 1. 现象（实测日志，直接采信，不要重新排查）

配置：`Optimization.WhenTaskQueueEmpty = close_emulator`。模拟器与 ALAS 实例**都没有运行**，然后启动实例：

```
17:19:08  Start scheduler loop: alas2
17:19:08  No task pending
17:19:08  [Task] MaaStartup (Enable, 2026-10-24 04:00:00)
17:19:08  Wait until 2026-10-24 04:00:00 for task `MaaStartup`
17:19:08  MAA安装路径：D:\...\Maa
17:19:08  MAA 客户端类型 Official 使用自定义包名 com.hypergryph.arknightt
17:19:09  WARNING 无法查询设备 127.0.0.1:16608 的已安装包，跳过包名校验：device offline
17:19:11  [Message.ConnectionInfo] 127.0.0.1:16608 ConnectFailed
17:19:11  ERROR RequestHumanTakeover:  ← 整段 traceback（maa.py:603 handler.connect / handler.py:587）
17:19:11  WARNING 关闭游戏失败，将继续关闭模拟器
17:19:13  cannot connect to 127.0.0.1:16608: ... (10061)
17:19:13  WARNING 模拟器当前已不在线，跳过关闭
```

三个事实：

1. **整个启动过程没有出现任何模拟器启动动作**（没有 `EMULATOR START` / `未检测到模拟器 ... 正在尝试启动`）。
2. 关闭路径先去 `handler.connect()`，失败后抛 `RequestHumanTakeover`，而它被 `except Exception` 吞掉并打印完整堆栈。
3. 「模拟器已不在线，跳过关闭」这个正确判断出现在**最后**，是在一次无意义的 adb 等待（2 秒）之后才做出的。

## 2. 根因

### A. 启动路径完全没有设备层检查（主因）

`emulator_check()`（`maa.py` 的 `MaaDevice.emulator_check`）实现是完整的：
`MaaEmulator.alive()` → `find()` → `start()` → `wait_online()` → `maa_reconnect()`。
但它**只有一个调用点**：`get_next_task()` 里 `task.next_run <= now` 之后的边界判据。

而实例刚启动时，下一个任务是 11 小时后（`MaaStartup @ 04:00`），`get_next_task()` 直接走等待分支，
**永远走不到那个边界判据**。于是：

- 模拟器不会被拉起；
- 等待分支立刻按 `close_emulator` 执行"关闭"，而设备本来就没开 → 关闭动作打在离线上。

对照主模块：`Device.__init__` 在构造期捕获 `EmulatorNotRunningError` 并调用 `emulator_start()`（最多 4 次），
所以**主模块"启动实例就会拉起模拟器"，MAA 插件没有这段等价逻辑**。这是本任务要补的核心缺口。

### B. 关闭路径的顺序错了：先 connect，后判在线

`maa_stop_during_wait()`（`maa.py`）：

```python
try:
    handler = AssistantHandler(config=self.config, asst=self.maa_asst())
    handler.connect()                      # ← 先连，设备离线必失败
    handler.maa_start('CloseDown', {...})
except Exception as e:
    logger.exception(e)                    # ← RequestHumanTakeover 在这里被打成 ERROR + 堆栈
    logger.warning('关闭游戏失败，将继续关闭模拟器')

if not self.device.device_alive():         # ← 判在线却排在后面
    logger.warning('模拟器当前已不在线，跳过关闭')
    return True
```

`maa_close_game_during_wait()`（`maa.py`）有同样的问题：先 `handler.connect()`，后没有任何在线判断。

**「设备不在线」在关闭路径上不是异常，而是期望的终态。** 现在却先花 2 秒尝试连接、
再抛一个 `RequestHumanTakeover`、再打印一整段堆栈、最后才跳过——纯属自找。

### C. `RequestHumanTakeover` 被当成普通异常打印

`AssistantHandler.connect()`（`module/handler/handler.py:587`）在 `asst.connect()` 失败时
`raise RequestHumanTakeover`。在**任务执行**路径上这是对的（MaaCore 连不上确实需要人看），
但在**关闭收尾**路径上它只意味着"设备不在，不用关了"。现在被 `except Exception` 接走，
`logger.exception` 打出完整 traceback，日志看起来像崩溃，实际功能是好的（后面确实跳过了关闭）。
用户会据此误判为"实例启动失败"。

## 3. 需求

### R1. 启动实例时必须做一次设备就绪检查（核心）

在 `ArknightsAutoScript.get_next_task()` **的 `while 1:` 循环之前**，用一次性标志执行设备检查：

```python
if not self.__dict__.get('_maa_boot_checked', False):
    self.__dict__['_maa_boot_checked'] = True      # 先置位，异常路径也不重复
    device = self.device
    device.config = self.config
    if not device.emulator_check():
        logger.critical('实例启动：模拟器无法就绪，请求人工介入')
        raise RequestHumanTakeover
```

要求：

- **只执行一次**（进程生命周期内）。`get_next_task()` 每次任务结束都会被调用，不能每次任务都拉起模拟器。
- 位置必须在 `while 1:` **之外**，即 `start_watching()` 之前。`emulator_check()` 最多阻塞 180 秒
  （`wait_online` 超时），若放在监听基准之后，会重现"阻塞期间 GUI 操作被吞"的老问题。
- 失败要分级，不要统一一句"无法启动"：
  - `find()` 返回 None → `未找到 serial 为 "X" 的模拟器实例`，并列出 `iter_running_emulator()` 拿到的可用实例；
  - `start()` 失败 → `模拟器启动命令发送失败`（附实例名与 MuMu 路径）；
  - `wait_online()` 超时 → `模拟器启动超时，adb 未上线`（附 serial 与超时秒数）；
  - 以上任一失败 → `raise RequestHumanTakeover`（此时确实无法自愈，不能带着离线设备进等待）。
- 本次**真的拉起过**时，打一行明确日志，例如
  `实例启动：模拟器已拉起 127.0.0.1:16608`；已在线时打 `实例启动：模拟器已在线`，不重复启动。

### R2. 关闭路径先判在线，再决定要不要 connect

- `maa_stop_during_wait()`：把在线判断提到**最前面**，在 `AssistantHandler` 创建之前：

```python
if not self.device.device_alive():
    logger.info('模拟器离线，无需关闭')
    return True
```

- `maa_close_game_during_wait()`：同样在最前面加在线判断，离线时 `logger.info('模拟器离线，无需关闭游戏')` 并 return。
- 判据统一用 `MaaEmulator.alive(serial)`（经 `MaaDevice.device_alive()` 封装），
  **不要**用 `adb connect` 打通循环（一次 4.2 秒，见历史日志）。
- 已有的后置 `if not self.device.device_alive()` 可以保留作为二次兜底（关闭命令发出后可能已关完），
  但日志降为 `info`，不要与前置判断重复打两次 warning。

### R3. 关闭路径不要把 `RequestHumanTakeover` 当普通异常

在 `maa_stop_during_wait()` / `maa_close_game_during_wait()` 的 `try` 里，先单独捕获：

```python
except RequestHumanTakeover:
    # MaaCore 未连接，说明设备不在；这不是需要人工介入的错误
    logger.info('MaaCore 未连接，跳过关闭（设备可能已离线）')
```

再保留 `except Exception: logger.exception(...)`。

推荐做法是在调用点捕获（改动面小、不改变 `connect()` 对任务路径的语义）。
**备选**：给 `AssistantHandler.connect()` 增加 `raise_on_failure=True` 参数，关闭路径传 `False`，
让它返回 bool。二选一，不要两边都改。

### R4. 启动检查与 close_emulator 的既定交互（必须写进注释，不许"优化"）

启动实例 → 拉起模拟器 → 首个等待分支发现要等 11 小时 → 立刻按 `close_emulator` 关闭。
**这是与主模块一致的行为**（主模块 `Device.__init__` 也是先 `emulator_start()` 再进等待），
本次修复要保持它，并靠两句日志把因果关系说清楚：

```
实例启动：模拟器已拉起 127.0.0.1:16608
等待 39120s，按「关闭模拟器」策略关闭
```

**不要**为此加"启动后 N 秒内不关闭"的抑制逻辑：那会让 `close_emulator` 的语义变得不可预测，
并且引入新的状态位。

### R5. `emulator_check()` 自身的行为保持不变

- 保持 `MaaEmulator.alive()` → `find()` → `start()` → `wait_online()` → `maa_reconnect()` 的顺序；
- 保持 serial 漂移写回（`config.MaaEmulator_Serial = instance.serial` / `= serial`）；
- 保持 `except RequestHumanTakeover: raise` 与 `except Exception: logger.exception` 的分级；
- **不要**改 `wait_online()` 的 180 秒默认值。

### R6. 启动检查不得影响配置监听与包名校验

- `maa_check_package_changed()` 与 `maa_verify_package()` 的既有顺序不要动；
- `MAA 客户端类型 X 使用自定义包名 Y` 这行日志保留；
- 设备离线时 `maa_verify_package()` 跳过校验是正确行为（当前已实现），只把 WARNING 降为 INFO 即可，
  因为它后面紧跟的启动检查会把设备拉起来，不该在启动阶段就喊 WARNING。

## 4. 明确禁止的做法

1. **不要**把 `emulator_check()` 放进 `while 1:` 循环体。等待期被 `close_emulator` 关闭后，
   下一轮循环会立刻把它拉起来 → 重现"关了又开"的自激循环（这正是 commit 662cfb54e 修掉的问题）。
2. **不要**用 `adb connect` 或在循环里轮询 adb 做存活判据（4.2 秒 / 次）。
3. **不要**在关闭路径吞掉异常后继续 `connect()`，也不要为了"看起来干净"把 `except Exception` 收窄成空 `pass`。
4. **不要**在启动检查失败时只 `warning` 就继续调度——设备离线时后续每个任务都会失败，最后仍要 takeover，
   只是把错误拖到几小时后。
5. **不要**修改 `Maa/resource/config.json`、MaaCore 参数、`StartUp` 的 `start_game_enabled`。
6. **不要**删除 `_maa_wait_enters` 的 10 分钟 × 3 次循环护栏，也不要改动它的判据。

## 5. 验收标准（逐条自测并给出日志片段）

1. **主场景**：模拟器全关、`close_emulator`、下个任务 11 小时后，启动实例。
   日志必须依次出现 `实例启动：模拟器已拉起 ...` → `模拟器 online: ...` → `等待 ...s，按「关闭模拟器」策略关闭`；
   **不得出现** `ConnectFailed` 引发的 `ERROR RequestHumanTakeover` 堆栈。
2. **close_game 场景**：模拟器全关 + `close_game`，启动实例后模拟器被拉起并保持在线，游戏保持关闭。
3. **模拟器已在线**：启动实例时不出现 `未检测到模拟器`，从 `Start scheduler loop` 到进入等待不超过 3 秒。
4. **serial 错误**：把 `MaaEmulator.Serial` 改成不存在的端口，启动实例必须在 5 秒内给出
   `未找到 serial 为 "X" 的模拟器实例` + 可用实例列表 + `RequestHumanTakeover`，不得空转。
5. **等待期加任务**：启动完成进入等待后，在 GUI 手动加一个任务，必须立刻执行
   （`Config changed` 时间与操作一致），证明启动阻塞没有弄歪监听基准。
6. **关闭→到点拉起**：等待期模拟器已被关掉，到点后日志出现 `未检测到模拟器 ... 正在尝试启动` →
   `模拟器已重启` → 任务正常执行，无 `ConnectFailed`。
7. **无循环**：上述所有场景中，10 分钟内不得出现 3 次进入等待分支（`_maa_wait_enters` 护栏不得触发）。
8. **无回归**：`close_game` 模式下正常跑完一轮任务，游戏被 CloseDown，模拟器保持在线。

## 6. 交付物

1. 修改后的 `submodule/AlasMaaBridge/maa.py`：启动期一次性设备检查 + 关闭路径前置在线判断 + 异常分级。
2. 一份简短改动说明（改了什么、为什么、对应 R1~R6 哪条）。
3. 第 5 节 1~8 条自测结果，每条附关键日志行；做不到的注明原因与风险。

## 7. 若发现判断有误

本文件每条结论都标了可验证位置（函数名 + 日志原文 + 时间戳）。
如果你发现 `emulator_check()` 已被其他路径调用、或 `maa_stop_during_wait()` 已改为先判在线，
**请先停下来说明现状与证据**，不要按过期前提硬改。
