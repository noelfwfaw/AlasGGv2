# 修复任务：`close_emulator` 的三个连带缺陷（等待无下限 / 配置变更被吞 / 停机判据不感知截止）

> 提示词使用说明：本文件全文可直接作为任务提示词交给编码代理或工程师。
> 文中行号基于当前 master，可能后续偏移，**认函数名与日志原文比认行号可靠**。
> 若你核查后发现下列任一前提与代码不符，请先停下来指出证据，不要按错误前提硬改。

---

## 0. 环境与硬约束

- 运行环境是 **Python 3.7.6**（`toolkit/python.exe`）。**禁止** 3.8+ 语法：不允许 walrus `:=`、`dict |`、`list[str]` 之类内建泛型下标、`functools.cached_property` 之外的 3.8+ 标准库新特性。
- **禁止** 新增第三方依赖。
- **禁止** 新增用户可见配置项（不要改 `argument.yaml` 的 option 列表、不要改 4 个 i18n、不要重跑 `config_updater`）。本任务全部用代码内常量与默认参数解决。
- 不要修改 `EmulatorInfo` / `Emulator_Serial` / `Emulator_ScreenshotMethod` 等既有配置的含义。
- 改动必须同时覆盖 **ALAS 主模块** 与 **MAA 插件（`submodule/AlasMaaBridge`）**，两者共享 `Alas.wait_until()`。

---

## 1. 现象与实测证据（不要重新排查，直接采信）

配置 `Optimization.WhenTaskQueueEmpty = close_emulator`，Windows + MuMu12 多开（同一台机器同时跑 5~8 个 MuMu 实例）。

实例 `A` 的日志（时间轴，已删减）：

```
16:00:49.141  [Task] Commission (Enable, 2026-10-23 16:19:41)
16:00:49.153  Wait until 2026-10-23 16:19:41 for task `Commission`
16:00:49.157  Close emulator during wait
16:00:49.159  App stop: com.bilibili.azurlane
16:00:49.390  EMULATOR STOP
16:00:49.509  Found emulator instance: MuMuPlayer12(serial="127.0.0.1:16384", name="MuMuPlayer-12.0-0")
16:00:49.516  Execute: MuMuManager.exe api -v 0 shutdown_player
16:01:19.556  WARNING | Emulator still in adb device list after 30s, continue anyway
（此后 8 分钟，实例 A 的日志没有任何一行输出，进程仍在运行）
```

同一时间 GUI 日志（实例 A 的配置文件）：

```
16:01:07.068  Save config ./config\A.json, Commission.Scheduler.NextRun='2020-01-01 00:00:00'
```

事后核查（16:09）：

| 检查项 | 实际值 | 说明 |
|---|---|---|
| `config/A.json` 的 mtime | `16:01:07` | 之后没有任何写入 |
| `Commission.Scheduler.NextRun` | `2020-01-01 00:00:00` | 仍是“立即执行”，说明调度器从未消费它 |
| `adb devices -l` | 完全没有 `127.0.0.1:16384`，也没有 `offline` 残留 | 模拟器**确实已关闭**，条目最终消失了 |
| 实例 A 进程 | 仍在 | 卡在 `wait_until()` 里，要等 `16:19:41` |

**两个可以直接读出的事实：**

1. `30s` 是**超时分支**被触发（`49.516 + 30 = 79.516`，实际 `19.556`，误差 40ms），不是“serial 消失”分支。当时 VM 还活着，adb 如实报在线。
2. 用户在 `16:01:07` 手动启动的 `Commission` **被吞掉了**——任务不会丢失，但会迟到 18.5 分钟才执行（等到旧的 `task.next_run = 16:19:41`）。

---

## 2. 代码地图（已核实）

### ALAS 主模块

| 位置 | 内容 |
|---|---|
| `alas.py:485-509` | `wait_until(future)`：**第 496 行 `self.config.start_watching()`** |
| `alas.py:511-580` | `get_next_task()`：`while 1` → `task.next_run > now` 时进等待分支 |
| `alas.py:528-576` | 5 个等待分支（`close_game` / `goto_main` / `close_emulator` / `stay_there` / 兜底） |
| `alas.py:549-562` | `close_emulator` 分支：`app_stop()` → `release_during_wait()` → `emulator_stop_and_wait()` → `wait_until()` |
| `alas.py:534/546/556/567/574` | `wait_until()` 的**全部 5 个调用点**，都在 `get_next_task()` 内部 |
| `module/device/device.py:303-326` | `emulator_stop_and_wait(timeout=30)`：1 秒轮询 + 30s 超时 |
| `module/device/device.py:224-253` | `device_alive()`：判据是 `status == 'device'`，且不在列表时补一次 `adb_client.connect()` |
| `module/device/connection.py:1011-1035` | `list_device()`：把 `host:devices` 原始行原样转成 `AdbDeviceWithStatus`，**`offline` 条目也在其中** |
| `module/config/watcher.py:12-33` | `start_watching()` 记 `start_mtime`；`should_reload()` 判 `mtime > start_mtime` |

### MAA 插件

| 位置 | 内容 |
|---|---|
| `submodule/AlasMaaBridge/maa.py:434-478` | 重写的 `get_next_task()`，第 463 行同样在阻塞之后调 `wait_until()` |
| `submodule/AlasMaaBridge/maa.py:406-432` | `maa_stop_during_wait()`：CloseDown → `device_alive()` 判定 → `emulator_stop_and_wait()` |
| `submodule/AlasMaaBridge/maa.py:144-169` | `MaaDevice.emulator_stop_and_wait()`，委托 `MaaEmulator.wait_offline()` |
| `submodule/AlasMaaBridge/module/device/emulator.py:56-77` | `device_serials()`：已按 `status != 'device'` 过滤，**这一层判据是对的** |
| `submodule/AlasMaaBridge/module/device/emulator.py:224-243` | `wait_offline(serial, timeout=30)`：串口不在「status==device 列表」里即返回 True |

注意 MAA 侧与主模块的判据**并不一致**：MAA 用 status 过滤，主模块用「条目是否存在」。这也是下面 P3 只针对主模块的原因。

---

## 3. 根因（三个独立缺陷，必须都修）

### P1（最严重）`close_emulator` 没有“等待时长下限”

`alas.py:525` 的条件只有 `task.next_run > datetime.now()`。也就是说**只要下一个任务还有 1 秒到期，也会执行完整的“关游戏 → 关模拟器”流程**，然后下一秒又要 `emulator_start()` 把它拉回来。

实测代价（20 秒空档的乐观路径）：

```
T+0    任务 A 结束，B 在 T+20 到期 → 进入等待分支 → 关模拟器
T+1    shutdown_player
T+10   模拟器真关掉了 → return True → wait_until(T+20) → 睡到 T+20
T+20   task_call('Restart') → continue → 下一轮
T+20   emulator_check() → 设备不在 → emulator_start()（shutdown+launch ≈15s）
T+35   模拟器起来 → Restart 任务 → 启动游戏 → 登录
T+60+  才轮到 B
```

**为一个 20 秒的空档付 60 秒以上**，且模拟器被无意义地关了又开。

### P2 配置变更基准取在阻塞之后，导致阻塞期间的 `task_call` 全部丢失

```python
# alas.py:549-556
self.device.emulator_stop_and_wait()      # 阻塞 30 秒（最坏）或 15 秒（emulator_start）
if not self.wait_until(task.next_run):    # ← 进入后第一件事：

# alas.py:495-496
def wait_until(self, future):
    future = future + timedelta(seconds=1)
    self.config.start_watching()          # start_mtime = 此刻 mtime = 16:01:07
```

`start_watching()` 取到的基准已经是**被改过之后**的 mtime（16:01:07）。此后 `should_reload()` 永远做 `16:01:07 > 16:01:07` → 恒为 False。**等待期内（阻塞前）发生的任何 GUI 操作都不会被发现。**

`close_game` 分支也有同一问题，只是阻塞只有 ~2 秒，撞上的概率低。

### P3 停机判据与超时语义

- `device.py:321`：`if not self.list_device().select(serial=self.serial)` —— 只判「条目是否还在」，**不看 `status`**。`offline` 条目会让它误判为「还没停」。应与 `device_alive()` 一致：`不存在 或 status != 'device'` 即视为已停。
- 等待循环完全不感知 `task.next_run`：任务在 `T+20` 到期，循环照样傻等到 `T+30`。
- 超时后 `return True`（假成功），调用方 `alas.py:554-555` 的 `if not ...: warning('fallback to close_game')` 永远不会触发，日志里只剩一句含糊的 `continue anyway`。
- `timeout=30` 对 8 实例负载偏短（本次就是关机本身超过 30 秒）。

---

## 4. 需求（可验收行为）

### R1 加“等待时长下限”

- 在 `alas.py:525` 的等待分支里，先算 `wait_seconds = (task.next_run - datetime.now()).total_seconds()`（**只取一次 `now`**）。
- `method == 'close_emulator'` 且 `wait_seconds < EMULATOR_CLOSE_MIN_WAIT` 时：
  - **不关游戏、不关模拟器**，按 `stay_there` 分支的行为处理（`release_resources()` + `release_during_wait()` + `wait_until()`）；
  - 打一行 INFO，说明跳过的原因和剩余秒数，例如：
    `Wait only 20s, skip closing emulator (threshold 180s)`。
- `EMULATOR_CLOSE_MIN_WAIT` 用模块级常量，默认 **180**（3 分钟）。不新增配置项。
- 阈值判断只对 `close_emulator` 生效，**不要**顺手给 `close_game` 也加上——`close_game` 回滚成本低，改了会改变现有用户预期。

### R2 修 `start_watching()` 的基准时机

- 目标：**阻塞期间发生的配置变更必须被下一次 `wait_until()` 发现。**
- 推荐实现（改动最小）：
  1. 在 `get_next_task()` 里、`if task.next_run > datetime.now():` **之前**（`self.config.bind(task)` 之后即可）调用一次 `self.config.start_watching()`；
  2. 删掉 `wait_until()` 内部第 496 行的 `self.config.start_watching()`。
- 安全性依据：`wait_until()` 的全部调用点（`alas.py:534/546/556/567/574` + `maa.py:463`）都在各自的 `get_next_task()` 内，且每次进等待分支前都会重新取基准，不会出现“基准过期到误判”的情况。
- 若你更倾向保留 `wait_until()` 的自包含性，替代方案是给它加可选参数 `baseline_mtime=None`，由调用方在阻塞前捕获并传入；两种都接受，但**不接受“在阻塞后再 `start_watching()`”**。
- 注意：`start_watching()` 依赖 `os.stat(filepath_config(...))`，文件必须存在（正常情况下一定存在），不要额外加异常吞掉。
- 已核实（可省你一轮验证）：`bind()`（`config.py:132-192`）与 `get_next()`（`config.py:236-260`）都**不落盘**，因此把 `start_watching()` 放在 `bind()` 之后不会因为自身写盘而把基准推迟、失真。

### R3 停机判据与截止感知

改 `Device.emulator_stop_and_wait()`（`device.py:303-326`）：

- 判据改为：`device = self.list_device().select(serial=self.serial).first_or_none()`；`device is None or device.status != 'device'` 即视为已停止 → 日志 `Emulator stopped` → `return True`。
- 新增可选参数 `deadline=None`（`datetime`）：`deadline` 到达时立即退出循环，打 INFO（例如 `Deadline reached while waiting emulator stop, continue`）并 `return False`，**不要**继续占用调度时间。
- `alas.py:554` 调用处传入 `deadline=task.next_run`；超时/未确认时的 `logger.warning('Failed to stop emulator, fallback to close_game')` 应当**真的出现**（因为它现在的返回值语义变成了“确认已停”）。
- `timeout` 默认值从 `30` 提到 **`60`**（8 实例负载实测 >30s）。
- 同步处理 MAA 侧：`maa.py:144` 的 `emulator_stop_and_wait()` 透传 `timeout=60`，并把 `deadline` 一并透传给 `MaaEmulator.wait_offline()`；`emulator.py:224` 加同样的截止判断。MAA 的 status 判据已经正确，不要动。

### R4 MAA 插件的 `wait_until` 与阈值

- `maa.py:434-478` 的 `get_next_task()` 里，`method == 'close_emulator'` 分支同样要加 R1 的时长下限；不满足时走 `maa_close_game_during_wait()` 的语义还是直接 `stay_there`？**选择直接 `stay_there`**（不关游戏），并同样打一行 INFO。
- R2 的 `start_watching()` 调整对 MAA 自动生效（继承关系），无需重复实现，但**必须验证** `maa.py:463` 这条路也生效。

---

## 5. 明确禁止的做法（省事但会引入更糟的问题）

1. **不要**把 `emulator_stop_and_wait()` 改成“发完命令就走”的 fire-and-forget。下一次 `emulator_start()` 内部第一步就是 `shutdown_player`，会和仍在销毁的实例撞车（`nemu_connect` 可能连到正在关机的实例）。
2. **不要**用 `adb_connect()` 做存活探测（实测 4.2 秒：10061 → 暴力重连 4 个端口 → `detect_device()` → 读 `customer_config.json`）。用 `list_device()`（~30ms）。
3. **不要**在 `list_device()` 之外另起一套设备枚举；也不要改成 `adb devices` 子进程调用。
4. **不要**在等待循环里加 `sleep` 之外的耗时操作（每次轮询都要在 ~50ms 以内）。
5. **不要**为了绕过 P2 而在 `get_next_task()` 里手动 `del_cached_property(self, 'config')` 后就地重新 `get_next()`——那会绕过 `task_call` 的 `force_call` 语义，产生重复调度。
6. **不要**改动 `close_game` / `goto_main` / `stay_there` 三个分支的任何行为（除 R2 的基准时机外）。R2 是共享代码，改完必须回归验证这三个分支仍正常。
7. **不要**新增用户可见配置项。
8. **不要**用 `iter_running_emulator()` / 进程枚举来判定“这个实例是否已停止”。同一台机器实测有 5~8 个 MuMu 实例同时在跑，`MuMuPlayer-12.0-1` 在不在只能按 **serial / 实例名** 判定，全局进程数回答不了这个问题。

---

## 6. 验收标准（逐条自测并给出日志证据）

配置：`Optimization.WhenTaskQueueEmpty = close_emulator`，MuMu 实例 `127.0.0.1:16384`。

1. **短间隔不关模拟器**：把下一个任务设到 20 秒后。日志必须出现 `skip closing emulator`，且**没有** `EMULATOR STOP` 段落，模拟器全程不重启。
2. **长间隔照常关闭**：把下一个任务设到 20 分钟后。日志出现 `Close emulator during wait` → `EMULATOR STOP` → `Emulator stopped`（**不是** `still in adb device list after 60s`），确认耗时明显短于 30 秒。
3. **阻塞期的 task_call 立即生效**：在 `EMULATOR STOP` 期间（关闭命令发出后 1~2 秒内）于 GUI 手动启动任意任务。日志必须出现 `Config "..." changed at ...`，并且该任务在 5 秒内开始执行，**不得**等到原定的 `NextRun`。
4. **截止感知**：把下一个任务设到 5 分钟后、且故意让模拟器关不掉（例如临时把 `EMULATOR_CLOSE_MIN_WAIT` 调成 0 并选一个关不掉的实例）。到达 `task.next_run` 时循环必须立即退出，日志有 `Deadline reached`，任务准点开始。
5. **超时降级可见**：模拟器关不掉时，60 秒后必须出现 `Failed to stop emulator, fallback to close_game`（而不是只有 `continue anyway`），并且调度继续、不 `exit(1)`。
6. **正常流零干扰**：`stay_there` / `close_game` / `goto_main` 三种模式各跑一次完整任务，日志与改动前一致（无新增 WARNING、无额外延迟）。
7. **MAA 插件同步通过 1~5**（`maa.py` 侧对应日志文案可不同，但行为必须一致）。
8. **不回归设备自愈**：模拟器被外部关掉后仍能 `emulator_check()` → `emulator_start()` 恢复（这条上一轮已实现，不能被本次改动破坏）。
9. **Python 3.7.6 兼容**：`toolkit/python.exe -c "import alas"` 不报语法错误；`python -m py_compile` 全部通过。

---

## 7. 交付物

1. 代码改动（`alas.py`、`module/device/device.py`、`submodule/AlasMaaBridge/maa.py`、`submodule/AlasMaaBridge/module/device/emulator.py`）。
2. 一份简短说明：每个缺陷对应的改动点、为什么这么改、以及第 6 节的自测记录（贴关键日志行）。
3. 如果某些验收项因环境限制无法验证，明确写出来，不要声称通过。
