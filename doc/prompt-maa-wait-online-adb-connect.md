# 修复 MAA 插件：拉起模拟器后卡死、任务永不启动

## 现象

实例 `alas2`（serial `127.0.0.1:16608`，MuMu12 实例索引 7）启动，模拟器不在线：

```
17:30:20.348 | INFO     | cannot connect to 127.0.0.1:16608: 由于目标计算机积极拒绝，无法连接。(10061)
17:30:20.350 | CRITICAL | 未检测到模拟器 127.0.0.1:16608，正在尝试启动
17:30:20.410 | INFO     | Start emulator: MuMuPlayer-15.0-7
17:30:20.414 | INFO     | Execute: ".../MuMuManager.exe" api -v 7 launch_player
（此后日志永久静止，无任何新行）
```

模拟器窗口确实被拉起来了，但 ALAS 再没有输出，任务不启动。约 180 秒后才可能出现
`Emulator not online in 180s` → `模拟器启动超时，adb 未上线` → `RequestHumanTakeover`。

## 实测证据（已在本机验证，无需重复排查）

模拟器已完全启动、系统跑起来的情况下：

```bash
adb devices          # → List of devices attached   （空！什么设备都没有）
adb connect 127.0.0.1:16608
                     # → connected to 127.0.0.1:16608
adb devices          # → 127.0.0.1:16608   device
adb -s 127.0.0.1:16608 shell getprop sys.boot_completed
                     # → 1
```

即：**MuMu 启动后不会把自己的 adb 端口注册进 adb server**，必须由外部 `adb connect`
一次，设备才会出现在 `adb devices` 里。一旦 connect，设备立刻可用、boot 已完成。

## 根因

`submodule/AlasMaaBridge/module/device/emulator.py` 的 `MaaEmulator.wait_online()`
（约 215-254 行）只被动轮询 `device_serials()` → `AdbClient.device_list()`，
**整个等待过程从不调用 `adb connect`**。

于是无论模拟器启动得多好，`device_list()` 永远是空的 → 死等满 180 秒 → 返回 `None` →
`emulator_check()` 判定启动失败 → `_maa_boot_check()` 抛 `RequestHumanTakeover` → 实例停止。

对比同文件 `alive()`（83-102 行）：它「不在列表时补一次 `adb connect` 复核」，
所以探测逻辑是对的。**`wait_online()` 缺了完全相同的这一步**，这是唯一的不一致点。

## 修复要求

### R1（必须）`wait_online()` 主动 connect

在 `while 1` 循环体内、读取 `device_serials()` 之前，主动 `adb connect` 一次当前实例的
serial，并做节流（**每 5 秒最多一次**，避免每秒 fork 一个 adb 进程）：

```python
def wait_online(self, instance, timeout=WAIT_ONLINE_TIMEOUT):
    serial = instance.serial
    ...
    timer = Timer(timeout).start()
    interval = Timer(1).start()
    connect_timer = Timer(5).start()

    while 1:
        # MuMu 启动后不会把 adb 端口注册到 adb server，只被动轮询 device_list()
        # 永远看不到设备（实测：adb devices 为空，connect 后立刻出现且 boot 已完成）。
        # 所以必须由这里主动 connect，否则必然空转到超时。
        if connect_timer.reached():
            connect_timer.reset()
            self.try_connect(serial)

        serials = self.device_serials()
        ...
```

新增一个私有方法，供 `alive()` 与 `wait_online()` 共用：

```python
def try_connect(self, serial):
    """
    尝试 adb connect 一个 serial。失败是常态（模拟器还没起完），不记 WARNING。
    失败时只打 DEBUG：等待期间每 5 秒一次，打成 INFO 会刷屏。
    """
    try:
        self.adb_client().connect(serial)
        return True
    except Exception as e:
        logger.debug(f'adb connect {serial} failed: {e}')
        return False
```

要求：
- `try_connect` 失败**不得**向上抛（`adb connect` 在端口未监听时抛 `AdbConnectionError`）。
- `alive()` 保持现有行为，可改为调用 `try_connect()`（现状：`logger.info(msg)` / `logger.info(e)`）。
  这一处**不必改**，避免影响已验证过的探测路径；优先级低。
- serial 漂移的兜底逻辑（`serial_to_id(candidate) == instance_id`）保留，但要放在 connect 之后，
  因为只有 connect 过，设备才可能出现在列表里。
- `WAIT_ONLINE_TIMEOUT = 180` **不要改**（文件里已有注释说明）。

### R2（必须）加等待进度日志

现在 `wait_online()` 整个过程零日志，出问题时日志直接静止，无法区分「在等」和「卡死」。
加一条每 10 秒的进度日志：

```python
logger.info(f'等待 adb 上线 {int(timer.cur)}s/{timeout}s: {serial} (当前设备: {serials or "无"})')
```

（`Timer.cur` 若不可用，用 `datetime.now() - start` 自行计算；`Timer` 来自 `module.base.timer`。）

### R3（不要动的地方）

- 不要改 `_maa_boot_check()` 的调用位置和一次性语义（`maa.py:728-760`、`780`）。
- 不要把它挪进 `while 1` 循环体内（会重演 662cfb54e 修过的自激死循环）。
- 不要改 `emulator_check()` 的 `alive()` 前置判断。
- 不要为了「让模拟器别白开」而在等待分支里加 `task_call('MaaStartup')`（同上，死循环）。
- 不要动 `close_emulator` 策略。修复后会出现「拉起模拟器 → 进入等待分支 → 因空档 > 阈值
  立刻关闭」的现象，这是既定设计，不属于本次修复范围。
- 不要改 `wait_online` 的 180 秒超时。

## 验收

1. 关闭模拟器实例 7 → GUI 启动 `alas2`，日志应连续出现：

```
未检测到模拟器 127.0.0.1:16608，正在尝试启动
Start emulator: MuMuPlayer-15.0-7
Execute: "...MuMuManager.exe" api -v 7 launch_player
等待 adb 上线 10s/180s: 127.0.0.1:16608 (当前设备: 无)      ← 新增
Emulator online: 127.0.0.1:16608
模拟器已重启：127.0.0.1:16608
实例启动：模拟器已拉起 127.0.0.1:16608
（随后正常进入 Wait until ... for task `MaaStartup`）
```

2. **不得**再出现 `Emulator not online in 180s` / `模拟器启动超时，adb 未上线`。
3. 模拟器本就在线时（`adb devices` 已有该 serial），不产生额外的 connect 日志噪音。
4. `MaaEmulator.alive()` 的探测结果与修复前一致（模拟器真关着时仍返回 False，
   `emulator_check()` 仍能正常走拉起路径）。

## 部署提醒

改动文件：`submodule/AlasMaaBridge/module/device/emulator.py`（仅此一个文件）。

该文件在仓库与**运行目录各有一份**，两者必须同步：
- 源码仓：`C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript/submodule/AlasMaaBridge/`
- 运行副本：`D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/submodule/AlasMaaBridge/`

改完重启该实例的调度器（GUI「停止」→「开始」）验证；不要只看源码仓。
