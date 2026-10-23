# 修复任务：ALAS 运行中模拟器掉线后无法自动拉起 + 新增「关闭模拟器」选项

> 提示词使用说明：本文件全文可直接作为任务提示词交给编码代理/工程师。
> 文中行号基于当前 master，可能后续有偏移，**认函数名与日志原文比认行号可靠**。

**本任务包含两件事，且强耦合，建议一起交付：**

1. 修复「ALAS 实例运行中，模拟器掉线后无法自动拉起，直接 exit(1)」的崩溃（第 2-5、8 节）。
2. 在「当任务队列清空后」增加「关闭模拟器」选项（第 6 节）。开启该项后，「实例在跑、模拟器已关」会变成常态，
   因此第 2 项必须以第 1 项为前提，否则一开就崩（见第 7 节）。

---

## 1. 项目与环境（先读）

- 仓库：`AzurLaneAutoScript`（ALAS，碧蓝航线自动化），Windows 平台，本地开发分支 master。
- 运行实例目录（另一份拷贝，用于实测）：`D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript`，
  其解释器是 `toolkit/python.exe`，**Python 3.7.6**。
- 因此：**新增代码必须是 Python 3.7 兼容语法**（不要 walrus、不要 `match`、不要 `dict | dict`、不要 3.8+ 的 typing 写法）。
  不要引入新的第三方依赖。
- 目标模拟器：MuMu Player 12（5.x 目录结构 `nx_main/MuMuNxMain.exe`，`nx_device/12.0/shell/sdk/external_renderer_ipc.dll`）。
  机器上存在 8 个 MuMu 实例（`MuMuPlayer-12.0-0` ~ `-15.0-7`），ALAS 实例 `B` 使用 `127.0.0.1:16416`（`MuMuPlayer-12.0-1`）。
- 截图方式 `nemu_ipc`，控制方式走 minitouch/maatouch 系列。

## 2. 现象

**一句话**：只有在“ALAS 实例尚未启动、`Device` 还没构造完成”时，模拟器没开才会被自动拉起；
一旦 ALAS 实例已经跑起来（`Device` 已构造），模拟器中途消失，ALAS 不会去启动模拟器，而是直接 `RequestHumanTakeover` 退出整个实例。

### 2.1 失败日志（14:07，模拟器中途消失后触发 Restart 任务）

```
INFO  13:45:04.194 │ Wait until 2026-10-23 14:07:02 for task `Commission`
INFO  13:45:04.197 │ Close game during wait
INFO  13:45:04.201 │ App stop: com.bilibili.azurlane
INFO  13:45:04.441 │ nemu_ipc released
...
INFO  14:07:04.756 │ Scheduler: Start task `Restart`
INFO  14:07:04.764 │ NemuIpcImpl init, nemu_folder=D:\Program Files\Netease\MuMu, ipc_dll=...\nx_device\12.0\shell\sdk\external_renderer_ipc.dll, instance_id=1, display_id=0
WARNING 14:07:04.778 │ Failed to call nemu_connect, result=0
ERROR   14:07:04.785 │ NemuIpcError: Connection failed, please check if nemu_folder is correct and emulator is running
ERROR   14:07:04.789 │ Emulator info incorrect
...（找到 MuMuPlayer-12.0-1，再次 init，4 次 connect 全部 result=0，间隔 1s/3s/3s）
CRITICAL 14:07:11.944 │ Retry connect_with_retry() failed
CRITICAL 14:07:11.947 │ Request human takeover
```

注意：这份日志里**完全没有 `DEVICE` / `DETECT DEVICE` / `EMULATOR START` 段落**。

### 2.2 成功日志（14:38，模拟器本来没开，直接启动 ALAS 实例）

```
INFO  14:38:26.624 │ DEVICE
INFO  14:38:26.641 │ <<< DETECT DEVICE >>>
INFO  14:38:28.687 │ cannot connect to 127.0.0.1:16416: 由于目标计算机积极拒绝，无法连接。 (10061)
INFO  14:38:30.695 │ cannot connect to 127.0.0.1:16414/16417/16415/16418 ... (10061)
INFO  14:38:30.715 │ <<< DETECT DEVICE >>>（第二次）
INFO  14:38:30.757 │ FIND EMULATOR INSTANCE → Found emulator instance: MuMuPlayer12(serial="127.0.0.1:16416", name="MuMuPlayer-12.0-1", ...)
INFO  14:38:30.879 │ [customer.network_bridge_opened] false
WARNING 14:38:30.884 │ No such device exists, please restart the emulator or set a correct serial
INFO  14:38:30.940 │ EMULATOR START
INFO  14:38:30.944 │ Execute: "D:/Program Files/Netease/MuMu/nx_main/MuMuManager.exe" api -v 1 shutdown_player
INFO  14:38:30.968 │ Execute: "D:/Program Files/Netease/MuMu/nx_main/MuMuManager.exe" api -v 1 launch_player
INFO  14:38:44.961 │ Emulator online: AdbDevice(127.0.0.1:16416, device)
```

## 3. 已定位的根因（已逐行核对，请勿重新推翻，但可验证）

### 3.1 唯一能“拉起模拟器”的入口只有一处，且只在构造期可达

`emulator_start()` 全仓调用点只有 4 个：

| 位置 | 触发条件 |
|---|---|
| `module/device/device.py:83`（`Device.__init__` 的 `for trial in range(4)` 里） | `super().__init__()` 抛 `EmulatorNotRunningError` |
| `alas.py:627` | GGHandler 抛 `GameStuckError` 且 `check_fail > 3` |
| `module/gg_handler/gg_handler.py:154` / `:161` | GG 的 ATX / u2 重启失败 |

而 `EmulatorNotRunningError` 全仓只有一个抛出点：

```python
# module/device/connection.py:796-810  (adb_connect)
elif '(10061)' in msg:
    if self.is_mumu12_family:
        ...adb_brute_force_connect(...); self.detect_device()...
    run_once(self.check_mumu_bridge_network)()
    logger.warning('No such device exists, please restart the emulator or set a correct serial')
    raise EmulatorNotRunningError
```

它只在 `Connection.__init__`（`connection.py:111-133`，第 118 行 `detect_device()`、第 121 行 `adb_connect(wait_device=False)`）
这条链上被 `Device.__init__` 接住。而 `Alas.device` 是 `cached_property`（`alas.py:47-58`），
调度循环里只有 `_ = self.device`（`alas.py:606`），**`Connection.__init__` 一辈子只跑一次**。

### 3.2 运行期这条异常即使抛出，也会被吞成 RequestHumanTakeover

所有截图/控制方法的 `@retry` 在遇到 `AdbError` 时会 `init = self.adb_reconnect`（`module/device/method/adb.py:44-47`），
而 `adb_reconnect()` 内部就是 `adb_connect()`（`connection.py:905/909`）。
所以模拟器运行中死掉时，adb 路径确实会抛 `EmulatorNotRunningError`，但它是抛在 `retry` 的 `try` 块里的，
被 `adb.py:67-71` 的 `except Exception` 接走，5 次重试后统一变成 `RequestHumanTakeover`。
**没有任何地方把它翻译成“去启动模拟器”。**

### 3.3 `nemu_ipc` 截图完全绕过 adb

`nemu_ipc.py:476-516` 的 `nemu_ipc` cached_property 在 `nemu_connect` 返回 0 时只做 `logger.error` + `RequestHumanTakeover`，
`retry` 装饰器（`nemu_ipc.py:159-207`）只会 `reconnect()`。
这就是 14:07 那份日志的直接死因：`Restart` 任务第一步 `self.device.screenshot()`（`alas.py:72-74`）就炸了。

### 3.4 结论

问题不是“配置错 / nemu_folder 错 / instance_id 错”（两段解析算出的 folder 与 `instance_id=1` 完全一致，且 13:45 可用）。
真正的缺口是：**运行期缺少“设备存活检查 + 拉起模拟器”的闭环**，一旦模拟器掉线，任何任务都会把整个 ALAS 实例 `exit(1)` 拖死。

## 4. 需求（行为规范）

请实现以下行为，且不改变现有正常路径的任何日志与耗时特征：

**R1. 任务前存活检查**
每次从调度器取到任务、真正执行前，做一次**廉价**的设备存活检查。
- 判据：`self.list_device().select(serial=self.serial)` 中存在且 `status == 'device'`。
- 若不在或非 `device`，再补一次 `adb connect` 确认（避免 adb 缓存抖动造成的误判）。
- **禁止**直接使用 `adb_connect()` 做探测：模拟器不在时它要走 10061 → 暴力重连 4 个端口 → `detect_device()` →
  `check_mumu_bridge_network()`，实测耗时 4.2 秒（14:38:26.676 → 14:38:30.884），会把每个任务都拖慢。
- 设备正常时，此项开销应 ≤ 50ms，且**不产生 WARNING/ERROR 级日志**。

**R2. 确认不在才拉起，且只拉起一次**
- 判定“该 serial 确实不在”时，才调用 `self.emulator_start()`。
- 严禁无条件调用：`platform_windows.py:316-338` 的 `emulator_start()` 是“先 `_emulator_stop`（MuMuManager `shutdown_player`）
  再 `_emulator_start`（`launch_player`）”。对**仍在运行**的模拟器盲调，会把只是游戏卡死的场景变成整机重启。
- `emulator_instance is None`（serial 写错/找不到实例）时**不要**尝试启动，直接按原逻辑 `RequestHumanTakeover`，
  否则 `_emulator_function_wrapper` 内部会因 `None` 而必然失败并空转。
- 每个任务最多自愈一次，防止“启动失败→重试→再失败”的死循环；自愈失败后必须回落到现有 `exit(1)` 行为。

**R3. 自愈后的状态刷新**
`emulator_start()` 返回 True 后（它内部 `emulator_start_watch()` 已等到 adb `echo pong` + 包名可用），必须清理陈旧状态：
- `self.nemu_ipc_release()`（`nemu_ipc.py:591-595`），否则旧的 `connect_id` 指向已死实例。
- `self.adb_reconnect()` 或 `adb_disconnect()` + `adb_connect()` + `detect_device()`。
- 注意 MuMu 端口会漂移（配置 16416，实际可能变成 16417/16414…）：`self.serial` 是普通实例属性，
  `detect_device()` 可能把它改写，自愈后要保证后续流程读到的是新 serial。
- 若 `emulator_start()` 返回 False，不要清理/重试，直接回落到原流程。

**R4. 运行期异常兜底**
在 `Alas.loop()` 里包住任务执行，捕获 `RequestHumanTakeover`：
- 先按 R1 判断是否真的是“设备不在”；是则执行 R2/R3 并重试该任务一次。
- 不是（真正的逻辑错误/人为接管）则维持现状，原样退出。
- 该兜底同样要覆盖 `nemu_ipc` 与 `adb` 两条死法（3.2 与 3.3）。

**R5. 不改变适用范围**
- 仅 Windows + 真实模拟器生效。非 Windows、手机云、HTTP 连接（`DEVICE_OVER_HTTP`）路径行为保持完全不变。
- 不要修改 `EmulatorInfo` 配置项；本任务唯一允许新增的用户可见配置是第 6 节的 `close_emulator` 选项。

**R6. 新增配置项：`Optimization.WhenTaskQueueEmpty` 增加「关闭模拟器」**
- 新增第四个选项 `close_emulator`：无任务时关闭游戏并把模拟器整机停掉，等待期间彻底释放资源；
  唤醒后不做任何“特殊启动”，由 R1 的任务前检查自动拉起。
- 完整改动清单、实现代码与 MuMu 特有的竞态坑见**第 6 节**。

## 5. 实现建议（可调整，但请说明理由）

建议两处改动，改完请给出 diff：

### 5.1 新增 `Device.emulator_check()`（放 `module/device/device.py`）

```python
def emulator_check(self) -> bool:
    """
    运行期设备存活检查，必要时拉起模拟器。
    Returns:
        bool: True 表示设备可用（含“已成功拉起”），False 表示无法恢复
    """
```

要点：
- 非 Windows / 非模拟器直接 `return True`（无副作用）。
- 先 `list_device()` 判 serial 与 status；正常 → 直接 `return True`。
- 异常分支：`emulator_instance is None` → `return False`；否则 `emulator_start()`。
- 拉起成功后清理 R3 列出的缓存，再 `return True`。
- 日志用 `logger.critical` 或 `logger.warning` 明确写出“检测到模拟器不在 → 尝试拉起 → 结果”，
  便于用户从日志判断是否是自愈路径（这条日志是必要的，用户需要知道发生过自愈）。

### 5.2 接入 `Alas.loop()`（`alas.py:578-666`）

- 在 `self.device.config = self.config` 之后（`is_first_task` 跳过 Restart 的逻辑之后），
  插入一次 `self.device.emulator_check()`；失败则走原有的 `RequestHumanTakeover` 退出路径。
- 在 `success = self.run(inflection.underscore(task))`（`alas.py:634`）外层加 `try/except RequestHumanTakeover`，
  按 R4 处理，并保证不会绕过现有的 `failure_record` / `failed >= 3` 统计。

### 5.3 不要做的“省事”写法

- ❌ 只在 `nemu_ipc` 的 cached_property 里 catch 后 `emulator_start()`：`ldopengl` / `minitouch` / maatouch 等其它方法仍会死，
  而且 `nemu_connect` 返回 0 不等于“没开”（冷启动/boot 卡住同样返回 0），会误杀。
- ❌ 把存活检查塞进 `Device.screenshot()` 循环：每帧几十毫秒，会显著拖慢识别。
- ❌ 在 `platform_windows.emulator_start()` 里加“如果活着就跳过 stop”：会改变 `Start` 任务（用户主动重启模拟器）的语义。

## 6. 新增配置项：`Optimization.WhenTaskQueueEmpty` 增加 `close_emulator`

### 6.1 需求
在「设置 → 优化 → 当任务队列清空后」现有三个选项（`stay_there` / `goto_main` / `close_game`）之后，
**追加第四个选项「关闭模拟器」**：无任务时把游戏关掉、再把模拟器整机停掉，等待期间释放内存与 CPU；
到点后被下一轮任务按 R1 自动拉起。

语义与 `close_game` 保持一致（唤醒后 `task_call('Restart')` 恢复游戏），区别只在于多停一个模拟器。

### 6.2 需要改动的文件（缺一不可）

| 文件 | 改什么 |
|---|---|
| `module/config/argument/argument.yaml:96-98` | `option: [ stay_there, goto_main, close_game, close_emulator ]` |
| `module/config/argument/args.json`（第 428 行起的 `WhenTaskQueueEmpty.option`） | 生成产物，由生成器写入 |
| `module/config/config_generated.py:44` | 注释同步：`# stay_there, goto_main, close_game, close_emulator` |
| `module/config/i18n/zh-CN.json`（569-575 行区域） | `"close_emulator": "关闭模拟器"` |
| `module/config/i18n/zh-TW.json` | `"close_emulator": "關閉模擬器"` |
| `module/config/i18n/en-US.json` | `"close_emulator": "Close Emulator"` |
| `module/config/i18n/ja-JP.json` | `"close_emulator": "エミュレーターを終了"` |
| `alas.py:528-562`（`get_next_task()` 里的 `method` 分支） | 新增 `elif method == 'close_emulator':` |

生成命令（在仓库根执行，脚本内部会自行 `chdir`）：

```
python -m module.config.config_updater
```

⚠️ 该命令会重写 `args.json` / `config_generated.py` / 四个 i18n / `template.json`。
**跑完必须 `git diff` 逐文件确认只包含本次预期变更**；本仓库带多个 submodule（`submodule/AlasMaaBridge` 等有各自的
`config_updater.py`），不要顺手把子模块的配置一起生成掉。

### 6.3 `alas.py` 分支实现（照 `close_game` 的样子写）

```python
elif method == 'close_emulator':
    logger.info('Close emulator during wait')
    self.device.app_stop()
    release_resources()
    self.device.release_during_wait()
    if not self.device.emulator_stop():
        logger.warning('Failed to stop emulator, fallback to close_game')
    if not self.wait_until(task.next_run):
        del_cached_property(self, 'config')
        continue
    if task.command != 'Restart':
        self.config.task_call('Restart')
        del_cached_property(self, 'config')
        continue
```

必须注意：
- `app_stop()` 要排在 `emulator_stop()` **之前**，否则 MuMu 可能弹「应用无响应」阻塞关机。
- `emulator_stop()` 在 `PlatformBase`（非 Windows）是空实现、返回 `None`；在 `PlatformWindows` 返回 `True/False`。
  用 `if not ...` 判断，不要把 `None` 当成成功。
- 停不掉时**回落成 `close_game` 的语义**（游戏已关、模拟器还在），不要让后面的流程误以为模拟器已关闭。
- **不要**在这里调 `emulator_start()`。唤醒后的模拟器启动必须由 R1/R2 的任务前检查负责——这是本任务的核心，
  在这里补一个启动调用会把两套恢复逻辑搅在一起。
- 不要改默认值（仍是 `goto_main`），不要调整现有三个选项的顺序。

### 6.4 MuMu 特有坑（务必处理）

`platform_windows._emulator_stop()` 对 MuMu12 只是发一条
`MuMuManager.exe api -v N shutdown_player`（`platform_windows.py:158-162`），**是异步的、不等待关机完成**。
而 `emulator_start()` 内部第一步又是 `shutdown_player`（先 stop 再 start）。

因此：如果用户在等待期结束后立刻触发下一轮，可能出现「上一个实例还在关，新的 start 已经在跑」的竞态，
或者 `nemu_connect` 连到一个正在销毁的实例。

要求：`emulator_stop()` 之后、进入 `wait_until()` 之前，轮询 `self.list_device()` 直到该 serial 消失
（上限建议 30s，超时只打 warning 不阻塞）；或者在 R2 启动前做同样的「确认实例已彻底消失」判断。
两种做法选一种，并在交付说明里写清选了哪种。

## 7. 该选项与 R1-R4 是同一条链，必须一起交付

开启 `close_emulator` 之后，「ALAS 实例在运行、但模拟器处于关闭状态」会从异常场景变成**每次等待都会发生的常态**。

所以：**只加选项不修 R1-R4，用户一开这个选项就会 100% 复现第 2 节的崩溃**（下次任务第一个截图就 `RequestHumanTakeover`）。
如果时间有限只能交付一部分，请先交付 R1-R4，再交付本节的选项。

## 8. 验收标准（请逐条自测并给出结果）

1. **掉线自愈**：实例处于等待期（`Wait until ...`）时，手动执行
   `MuMuManager.exe api -v 1 shutdown_player` 杀掉模拟器；等下一个任务到点，
   ALAS 应打印自愈日志 → `EMULATOR START` → `Emulator online` → **继续执行任务**，进程不退出。
2. **正常路径零干扰**：模拟器正常时，连续跑 3 个任务，日志中不得出现 `EMULATOR START`，
   且新增检查不得产生 WARNING 及以上日志；单次任务起始耗时增加 ≤ 100ms。
3. **不误杀**：模拟器正常运行、仅游戏卡死时，应走原有 `Restart`（`app_restart`），**不得**重启模拟器。
4. **配置错误不空转**：把 `Emulator.Serial` 改成不存在的 `127.0.0.1:16499` 启动，
   应维持原有 `RequestHumanTakeover` 行为，不得反复 `launch_player`。
5. **状态刷新**：自愈后 `nemu_ipc` 截图、minitouch/maatouch 点击均正常（跑完一个真实任务验证，例如 `Reward`）。
6. **语法兼容**：`toolkit/python.exe -m compileall module alas.py` 通过（Python 3.7.6）。
7. **不回归**：`tests/` 下现有测试若有涉及 device 初始化的用例，需保持通过。
8. **关闭模拟器选项（真机）**：设为「关闭模拟器」后进入等待，日志应出现
   `Close emulator during wait` → `Execute: ... api -v 1 shutdown_player`，模拟器窗口消失；
   到点后自动 `launch_player` → `Emulator online` → 继续执行任务，进程不退出。
9. **关闭模拟器选项（降级）**：在非 Windows 或手机云环境下选该选项，
   应打印明确 warning 并回落为「关闭游戏」语义，不得异常、不得卡死。
10. **配置兼容**：旧配置文件（值为 `goto_main` / `close_game` / `stay_there`）升级后不受影响；
   四种语言的 GUI 都能正确显示新选项文案；`template.json` 正常生成。

## 9. 交付物

1. 改动文件的 unified diff（或逐文件 diff）。
2. 一段说明：每个改动点解决了上面哪条需求（R1-R5），以及为什么放在那个位置。
3. 第 6 节 7 条验收的自测记录（哪几条在真机上验证过、哪几条只能静态验证）。
4. 如果实现过程中发现本文档的根因判断有误，**先停下来指出证据**，不要按错误前提硬改。
