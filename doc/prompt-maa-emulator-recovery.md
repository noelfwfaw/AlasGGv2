# 任务：为 MAA 插件（AlasMaaBridge）补上「有任务就拉起模拟器」与「完成所有任务后关闭模拟器」

> 本文件全文可直接作为任务提示词交给编码代理/工程师。
> 行号基于当前 master（commit `1b75eeeb1` 之后），后续可能有偏移，**认函数名与日志原文比认行号可靠**。
> 若你（执行者）在读代码时发现本文档描述与代码不符，**先停下来指出证据**，不要按错误前提硬改。

---

## 0. 一句话结论（先读）

主模块 ALAS 的模拟器自愈修复（commit `52af82fcd`）**只覆盖了 ALAS 自己的 `Device`**。
MAA 插件的 `device` 是一个全部方法返回 `None` 的 `FakeDevice`，所以：

1. **MAA 插件现在每次任务启动都会直接 `RequestHumanTakeover`**（回归，必须先修，见 R0）；
2. 模拟器相关能力（存活探测、拉起、关闭）在 MAA 侧**完全不存在**，需要在插件内重新实现一套，不能指望复用 ALAS 的 `Device`。

本任务交付三件事：

- **R0**：修回归 —— 给 MAA 插件一个真正实现 `emulator_check()` 的 device 对象；
- **R1**：有任务到点时，即使 ALAS 实例已经在运行、`Device` 早已构造完毕，也要检查模拟器并把它拉起来（等价于 ALAS 的 `Device.emulator_check()`）；
- **R2**：`优化设置 - 完成所有任务后` 增加「关闭模拟器」选项，关闭模拟器并在下个任务前自动拉起 + 重新唤醒游戏。

---

## 1. 运行环境与硬约束（先看，违反会导致代码直接跑不起来）

| 约束 | 说明 |
|---|---|
| 平台 | Windows + 桌面模拟器（MuMu12 / LDPlayer / BlueStacks / Nox / MEmu 等）。非 Windows 必须整体跳过 |
| Python | 运行实例使用 `toolkit/python.exe`，版本 **3.7.6**。禁止 `:=`、`dict \|`、`functools.cache`、`dataclass(slots=True)` 等 3.8+ 语法/API |
| 依赖 | 禁止新增任何第三方依赖。可用：`psutil`、`adbutils`、`cached_property`、仓库已有模块 |
| 改动范围 | 只改 `submodule/AlasMaaBridge/**`；主模块允许的最小改动仅限第 5.4 节的「可选提取」，**不得**修改 `alas.py` 的调度语义 |
| 配置文件名 | MAA 插件读写 `./config/<name>.maa.json` 与模板 `./config/template.maa.json`，**不是** `./config/<name>.json` |
| 代码风格 | 用户可见日志用中文，技术性日志用英文；沿用现有 `logger.info / logger.warning / logger.critical` 习惯 |

---

## 2. 代码地图（已逐条核实，可直接按图索骥）

### 2.1 MAA 插件

| 文件 | 位置 | 作用 |
|---|---|---|
| `submodule/AlasMaaBridge/maa.py` | `41-47` | `FakeDevice`：`__getattr__` 返回 `empty_func`，任何方法调用 → `None` |
| 同上 | `50-65` | `ArknightsAutoScript.device` → `FakeDevice()`；`.config` → `ArknightsConfig` |
| 同上 | `71-95` | `asst` cached_property → `load_asst()`；`check_task=True` 时会 `task_call('MaaStartup', True)` |
| 同上 | `231-244` | `maa_asst()`：等待期也能取到的 MaaCore 实例 |
| 同上 | `246-270` | `maa_close_game_during_wait()`：走 MAA `CloseDown` 关游戏，异常全兜住 |
| 同上 | `272-303` | `get_next_task()`：MAA 版调度，处理 `stay_there` / `close_game` |
| 同上 | `305-306` | `maa_startup()` → `AssistantHandler(...).startup()` |
| 同上 | `342-348` | `loop()` 入口与 `set_stop_event` |
| `submodule/AlasMaaBridge/module/handler/handler.py` | `387-432` | `AssistantHandler.__init__`；`self.serial = None`，`callback_timer = Timer(600)` |
| 同上 | `454-478` | `maa_start()`：`append_task` → `asst.start()` → 轮询回调，600s 超时或 `TaskChainError` → `RequestHumanTakeover` |
| 同上 | `561-589` | `serial_check()` / `connect()`：`asst.connect(os.path.abspath(DeployConfig().AdbExecutable), self.serial)`，失败 → `RequestHumanTakeover` |
| 同上 | `591-602` | `startup()`：**唯一**调用 `self.connect()` 的地方，随后 `StartUp` 任务带 `start_game_enabled: True` |
| `submodule/AlasMaaBridge/module/config/argument/argument.yaml` | `33-36` | `Optimization.WhenTaskQueueEmpty`，当前 option 只有 `stay_there, close_game` |
| 同上 | `38-52` | `MaaEmulator.Serial / PackageName / MaaPath / TouchMethod / DeploymentWithPause` |
| `submodule/AlasMaaBridge/module/config/config_updater.py` | 文件末尾 `__main__` | MAA 自己的配置生成器，`MOD_NAME = 'maa'` |
| `submodule/AlasMaaBridge/module/config/i18n/*.json` | `zh-CN.json:133-138` 等 | 4 种语言的 `WhenTaskQueueEmpty` 文案 |

### 2.2 主模块（只读参考 / 可复用）

| 位置 | 作用 |
|---|---|
| `alas.py:592-667` | `AzurLaneAutoScript.loop()`。**MAA 插件复用的正是这个循环** |
| `alas.py:618-621` | `task = self.get_next_task()` → `_ = self.device` → `self.device.config = self.config` |
| `alas.py:629-633` | **回归点**：`if not self.device.emulator_check(): raise RequestHumanTakeover` |
| `alas.py:654-667` | 运行期 `RequestHumanTakeover` → `device_alive()` 判定 → `emulator_check()` → 重试一次 |
| `module/device/device.py:221-330` | `device_alive()` / `emulator_check()` / `emulator_stop_and_wait()`：**参考实现**，逻辑与语义照抄，数据源换掉 |
| `module/device/platform/platform_windows.py:47` | `PlatformWindows.execute`（classmethod，`subprocess.Popen`，`start_new_session=True`） |
| 同上 `:61` | `kill_process_by_regex`（classmethod，按 cmdline 正则杀进程） |
| 同上 `:83` | `_emulator_start(instance)`：按模拟器类型拼启动命令，**只依赖 `self.execute`** |
| 同上 `:125` | `_emulator_stop(instance)`：按模拟器类型停实例，**只依赖 `self.kill_process_by_regex`** |
| 同上 `:316` | `emulator_start()`：**先 stop 再 start**，带 3 次重试和 `emulator_start_watch()` |
| 同上 `:340` | `emulator_stop()` |
| `module/device/platform/platform_base.py:57/65` | 基类空实现（非 Windows 走这里，直接返回） |
| `module/device/platform/emulator_base.py:255` | `EmulatorManagerBase`：**不依赖 config**，只声明接口 |
| `module/device/platform/emulator_windows.py:358` | `EmulatorManager`：`all_emulator_instances` 等枚举实现，**同样不依赖 config** |
| 同上 `:499` | `iter_running_emulator()`：psutil 遍历在跑的模拟器 exe（约 17ms） |
| `module/device/connection_attr.py:322` | `adb_client` 的构造方式（`AdbClient('127.0.0.1', 5037)`，支持 `ANDROID_ADB_SERVER_PORT`） |
| `module/device/connection.py:1011` | `list_device()`：发 `host:devices` 拿真实设备列表 |

---

## 3. 现状机理：为什么「实例在运行中」时模拟器不会被拉起

### 3.1 ALAS 侧（已修复，作为对照）

`emulator_start()` 全仓只有 4 个调用点，其中唯一「自动」的一个在 `Device.__init__`（`device.py:77-90`）。
`Device` 是 `Alas.device` 的 `cached_property`，只构造一次；模拟器在等待期掉线后，这条链**再也不会执行**。
故 commit `52af82fcd` 增加了 `Device.emulator_check()`，并在 `loop()` 每个任务开始前调用。

### 3.2 MAA 侧（本任务要补的洞）

```
任务到点 → Alas.loop() → self.device.emulator_check()
                              ↑
                        FakeDevice.empty_func() → 返回 None
                              ↓
                    if not None:  →  True
                              ↓
                    raise RequestHumanTakeover   ← 每次任务必崩（回归）
```

即使绕过这个回归，MAA 也**没有**任何「模拟器不在 → 拉起来」的路径：

- `maa.py` 从不创建真正的 `Device`（`device` 恒为 `FakeDevice`）；
- MAA 唯一的设备连接动作是 `AssistantHandler.connect()` → `asst.connect(adb, serial)`（`handler.py:586`），
  失败即 `raise RequestHumanTakeover`，**没有任何重试或拉起模拟器的分支**；
- `connect()` 只在 `startup()`（`handler.py:592`）里被调用，其它任务（`fight` / `recruit` / `infrast` …）
  默认「MaaCore 连接还活着」，模拟器一旦重启就会一路 `TaskChainError` 或 600s 超时。

### 3.3 MAA 与 ALAS 的三处关键差异（决定了实现不能照抄）

| 差异 | 后果 |
|---|---|
| config 是 `maa.json`，只有 `MaaEmulator_Serial`（默认 `127.0.0.1:5555`），**没有** `Emulator_Serial` / `EmulatorInfo_*` | 直接 `new Device()` 或 `new Platform()` 会读错键或抛 `AttributeError`；`Platform.emulator_instance` 完全不可用 |
| 游戏启动由 MAA 的 `StartUp` 任务（`start_game_enabled: True`）负责，不是 ALAS 的 `app_start()` | 拉起模拟器后必须保证 `MaaStartup` 被排进队列，否则模拟器起来了但游戏没起 |
| 没有 `Restart` 任务，MaaCore 连接是长连接 | 模拟器重启后必须显式 `asst.connect()` 重连，不能指望下一帧自动恢复 |

---

## 4. 需求（全部可验收）

### R0. 给 MAA 插件一个「真实的」device 对象（修回归，最高优先级）

保留 `FakeDevice` 现有的「未知属性一律 no-op」语义，避免破坏 `GGHandler`、`release_during_wait`、
`stuck_record_clear`、`click_record_clear`、`app_stop` 等既有调用；在其之上**显式实现**：

- `emulator_check() -> bool`
- `device_alive() -> bool`
- `emulator_stop_and_wait(timeout=30) -> bool`
- `app_stop()`（MAA 语义下为空实现即可，但**必须**返回 `False` 或 `None` 而不是抛异常）
- `release_during_wait()`（空实现）

**验收**：`_ = self.device` 之后 `emulator_check()` 返回 `True`（模拟器在线时），MAA 任务能正常开跑。

### R1. 有任务到点时自动拉起模拟器

在 MAA 版的 `get_next_task()` 里，**在 `break` 返回任务之前**完成一次检查：

1. 调用 `self.device.emulator_check()`；
2. 返回 `False` → `raise RequestHumanTakeover`（与 ALAS 行为一致，交给上层记录并退出任务）；
3. 若本次调用**真的启动过模拟器**（用标志位或返回值扩展），且当前待执行任务不是 `MaaStartup`，
   则 `self.config.task_call('MaaStartup')` + `del_cached_property(self, 'config')` + `continue`，
   保证游戏被重新唤醒。

`emulator_check()` 内部语义（照 `device.py:255-301` 的顺序，数据源换成 MAA 的）：

1. 非 Windows → 直接 `True`；
2. `device_alive()` 为真 → 直接 `True`（**不产生 WARNING 及以上日志**）；
3. 重新枚举模拟器实例（不要用缓存，MuMu 重启后端口会漂移）；
4. 按 serial 找不到实例 → `logger.critical` 明确提示「serial 填错 / 未找到实例」并返回 `False`，**不得**继续盲启动；
5. 实例 serial 与配置不一致 → 更新 `config.MaaEmulator_Serial`（见 5.3 的写回要求）；
6. 启动模拟器 → 等待 adb 上线；
7. `maa_reconnect()`：用新的 serial 重连 MaaCore；
8. 返回 `True`。

**验收**：模拟器完全关闭、ALAS 实例仍在运行时，到点任务能自动拉起模拟器并正常执行，日志中能看到完整的
`adbd 上线 → reconnect → 任务开始` 时序。

### R2. 运行期掉线兜底

`device_alive()` 必须是**廉价且真实**的判据（`adb devices` 中该 serial 状态为 `device`，约 30ms）。
`alas.py:654-667` 的兜底逻辑依赖它：模拟器在任务执行中死掉 → `RequestHumanTakeover` → `device_alive()` 为假 →
`emulator_check()` 拉起 → 重试一次任务。

**验收**：任务执行中手动关闭模拟器，实例**不退出**，日志出现 `Emulator died during task, trying to recover` 后任务重试。

### R3. 新增配置项：`Optimization.WhenTaskQueueEmpty` 增加「关闭模拟器」

`get_next_task()` 增加 `close_emulator` 分支，**必须**满足以下顺序与收尾：

```
1. 先关游戏（MAA CloseDown，best-effort，失败只告警）
2. 再关模拟器（device_alive() 为真才关；emulator_stop_and_wait()）
3. 无论关模拟器成功与否，都 config.task_call('MaaStartup')
4. wait_until(task.next_run)
5. del_cached_property(self, 'config') + continue
```

理由（不能省的细节）：

- **顺序不能反**：模拟器先被杀，游戏进程可能弹「应用无响应」，MuMu 的 `shutdown_player` 会被卡住；
- **第 3 步不能省**：模拟器关了，游戏必然没起，只有 `MaaStartup`（`StartUp` + `start_game_enabled`）
  会把游戏拉起来；否则下个任务（如 `MaaFight`）会因为「模拟器在、游戏不在」直接失败；
- **关闭失败要降级而不是中断**：`emulator_stop_and_wait()` 返回 `False` 时记 `warning`，仍继续等待并排 `MaaStartup`，
  等价于「关闭游戏」语义；
- 若 `close_emulator` 期间用户把配置改回 `stay_there`/`close_game`，`wait_until` 返回 `False` 后走原 `continue` 分支即可，不要额外处理。

### R4. 配置项落地（生成器驱动，禁止手改产物）

见第 6 节的逐文件清单。

### R5. 日志与错误处理

- 正常路径（模拟器在线）：**零** WARNING/CRITICAL，不得刷 `Find emulator instance` 之类的 HR；
- 自愈路径：启动前 `logger.critical`（与 ALAS 一致，便于用户定位），启动成功 `logger.critical` 或 `info` 明确说「模拟器已重启」；
- 所有新代码路径必须 `try/except Exception` 兜住，异常不得直接冒泡成实例退出（`RequestHumanTakeover` 除外）。

### R6. 不改变适用范围

- 仅 Windows 生效；非 Windows、手机云、`DEVICE_OVER_HTTP` 等路径行为完全不变（MAA 插件目前也不支持这些，保持现状即可）；
- 不修改 `MaaCopilot` / `MaaUpdate` 两个工具入口的行为；
- 不修改用户已有配置文件内容（唯一例外：serial 漂移时写回 `MaaEmulator_Serial`）。

---

## 5. 推荐实现方案

### 5.1 新增 `submodule/AlasMaaBridge/module/device/emulator.py`

> 仓库的 `module/` 下各子目录都是 namespace package（`module/config`、`module/handler` 均无 `__init__.py`），
> 新建 `module/device/` 不需要 `__init__.py`；如果你更保守，也可以把类直接写在 `maa.py` 里，但会牺牲可读性。

```python
class MaaEmulator:
    """MAA 插件专用的模拟器控制器。

    不复用 ALAS 的 Device / Platform：
      - Device.__init__ 会走 adb_connect() -> detect_device()，模拟器不在时直接抛
        EmulatorNotRunningError，正是本功能要处理的场景；
      - Platform 继承 Connection，同样依赖完整的 Emulator_* 配置，而 maa.json 里没有这些键。
    """

    def instances(self):
        """复用 EmulatorManager，不依赖 config。"""
        from module.device.platform.emulator_windows import EmulatorManager
        return EmulatorManager().all_emulator_instances

    def alive(self, serial):
        """adbutils.AdbClient('127.0.0.1', 5037).device_list() 中 serial 状态为 'device'。
        不在列表时补一次 client.connect(serial) 再确认，避免 adb 缓存抖动。"""

    def find(self, serial):
        """1) serial 精确匹配
           2) MuMu 16xxx 端口用 serial_to_id(serial) 匹配 MuMuPlayer12_id
           3) 仍失败返回 None
        """

    def start(self, instance):
        """调用 PlatformWindows._emulator_start，不要调 emulator_start()。"""

    def wait_online(self, serial, timeout=180):
        """轮询 alive(serial)，可额外等 sys.boot_completed == '1'。"""

    def stop(self, instance): ...
    def wait_offline(self, serial, timeout=30): ...
```

**关键实现说明（逐条都有坑，务必照做）**：

1. **`_emulator_start` / `_emulator_stop` 可以直接复用**。二者只调用 `self.execute` / `self.kill_process_by_regex`，
   而这两个在 `PlatformWindows` 上是 **classmethod**（`platform_windows.py:47/61`），所以：

   ```python
   from module.device.platform.platform_windows import PlatformWindows
   PlatformWindows._emulator_start(PlatformWindows, instance)   # 传类自身当 self
   ```

   可用但可读性差；若允许改主模块，优先按 5.4 抽成模块级函数。

2. **不要复用 `emulator_start_watch()`**：它里面的 `list_known_packages()` 只认 ALAS 的包名（`azurlane`），
   MAA 是明日方舟（`com.hypergryph.arknights` 等），会必然超时；它还带窗口最小化/闪烁等 ALAS 专属副作用。

3. **`emulator_start()` 是「先 shutdown 再 launch」**。对**仍在运行**的模拟器盲调会把用户正在跑的游戏一起关掉。
   因此 `start()` 之前必须先用 `alive()` / `find()` 确认「该 serial 真的不在」。

4. **MuMu12 的 `shutdown_player` 是异步的**（`platform_windows.py:158-162` 只发命令不等待）。
   关完必须轮询 `alive(serial)` 直到为假（上限 30s），否则下个任务拉起时会与仍在销毁的实例撞车。

5. **多开场景**：日志实测同时有 5 个 MuMu 实例在跑，`iter_running_emulator()` 只能回答「MuMu 开没开」，
   回答不了「这个实例开没开」。**必须按 serial / 实例 id 判定**，不能按「有没有模拟器进程」判定。

### 5.2 新增 `MaaDevice`（放在同一文件或 `maa.py`）

```python
class MaaDevice(FakeDevice):        # 继承以保留 no-op 兜底
    def __init__(self, script=None):
        self.script = script
        self.config = None          # loop() 会在 emulator_check() 之前赋值
        self.emulator_restarted = False

    def attach(self, config): ...
    def device_alive(self): ...
    def emulator_check(self): ...   # 语义见 R1，成功后 self.emulator_restarted = True
    def emulator_stop_and_wait(self, timeout=30): ...
```

- `maa.py:52-53` 的 `FakeDevice()` 改为 `MaaDevice(self)`；
- `FakeDevice.__getattr__` 的兜底行为必须保留（`GGHandler` 等会随机调用 device 方法）；
- 构造时**不得**做任何 adb / 进程枚举等重操作（`loop()` 里 `_ = self.device` 在每次循环都会触发）。

### 5.3 `maa.py` 需要的新增方法

```python
def maa_reconnect(self):
    """模拟器重启后重连 MaaCore。幂等，未加载过 asst 时直接返回。"""
    asst = self.__dict__.get('asst')
    if asst is None:
        return
    adb = os.path.abspath(DeployConfig().AdbExecutable)
    if not asst.connect(adb, self.config.MaaEmulator_Serial):
        raise RequestHumanTakeover
```

- serial 漂移写回：`self.config.MaaEmulator_Serial = instance.serial`，依靠 `AzurLaneConfig.__setattr__`
  的自动保存（`auto_update`），**不要**手工改 json；
- 若 `maa.json` 的 `MaaEmulator.Serial` 位于非绑定任务下，确认写回生效后重启实例仍保留新值。

### 5.4 （可选，但推荐）主模块最小提取

把 `PlatformWindows._emulator_start` / `_emulator_stop` 从实例方法抽成模块级函数
（如 `emulator_start_instance(instance)` / `emulator_stop_instance(instance)`），原方法保留为薄封装。
这样 MAA 侧无需 `PlatformWindows._emulator_start(PlatformWindows, instance)` 这种 hack。
**不得**顺带修改 `emulator_start()` / `emulator_stop()` 的语义，避免影响已修复的 ALAS 路径。

---

## 6. 配置项落地清单（R4）

### 6.1 唯一手改的文件

`submodule/AlasMaaBridge/module/config/argument/argument.yaml:33-36`

```yaml
Optimization:
  WhenTaskQueueEmpty:
    value: stay_there
    option: [ stay_there, close_game, close_emulator ]
```

### 6.2 i18n（4 个文件，手改）

`zh-CN.json:133-138`、`zh-TW.json`、`en-US.json:133-138`、`ja-JP.json`，每个文件：
更新 `help`，并新增 `close_emulator` 键。建议文案：

| 语言 | close_emulator |
|---|---|
| zh-CN | `关闭模拟器` |
| zh-TW | `關閉模擬器` |
| en-US | `Close emulator` |
| ja-JP | `エミュレーターを終了` |

zh-CN 的 help 建议追加：
> 关闭模拟器：先关闭游戏再退出模拟器，等待期间 CPU / 内存占用最低；下个任务开始前会自动启动模拟器并重新唤醒游戏（启动约需 10~20 秒）

### 6.3 产物文件（生成器产出，不要手改）

```bash
# 必须在仓库根目录执行，且使用运行实例的 Python（3.7.6）
toolkit/python.exe -m submodule.AlasMaaBridge.module.config.config_updater
```

预期变更（跑完请 `git diff` 逐文件核对）：

- `submodule/AlasMaaBridge/module/config/argument/args.json`（`WhenTaskQueueEmpty.option` 多一项）
- `submodule/AlasMaaBridge/module/config/config_generated.py`（`Optimization_WhenTaskQueueEmpty` 行尾注释）
- `submodule/AlasMaaBridge/module/config/argument/menu.json`
- `config/template.maa.json`
- 4 个 i18n 文件（生成器会重排/规范化，注意不要覆盖 6.2 的手写文案）

> 生成器只写 MAA 自己的一套文件（`MOD_NAME = 'maa'`），不会碰主模块的 `args.json` / `template.json`。
> 若发现主模块文件被改动，说明命令用错了，请回滚。

---

## 7. 明确禁止的做法（每一条都会导致更糟的结果）

1. **禁止** `new Device()` / `new Platform()` / `new Connection()` 来做模拟器控制 —— 三者构造期都要连 adb，
   模拟器不在时直接抛 `EmulatorNotRunningError`，正好死在要救的场景里。
2. **禁止**用 `adb_connect()` 做存活探测 —— 模拟器不在时它要走 `10061 → 暴力重连 4 个端口 → detect_device() →
   check_mumu_bridge_network()`，实测 4.2 秒。用 `AdbClient.device_list()`（约 30ms）。
3. **禁止**把「MaaCore `asst.connect()` 返回 False」当作「模拟器没开」的判据 —— 模拟器冷启动、卡在 boot 阶段
   同样会 connect 失败。必须先做设备/进程判据。
4. **禁止**直接调用 `emulator_start()` —— 它先 `shutdown_player` 再 `launch_player`，会误杀还活着的模拟器。
5. **禁止**在 `close_emulator` 分支里调用任何「启动模拟器」的逻辑 —— 唤起由 R1 统一负责，
   两套恢复逻辑并存必然打架。
6. **禁止**读 `config.Emulator_Serial` / `config.EmulatorInfo_*` —— MAA 的 `maa.json` 里没有这些键，
   读到的只会是主模块 `GeneratedConfig` 的类默认值（如 `'auto'`），行为不可预期。
7. **禁止**在 `device_alive()` 里做 `logger.info` 以上的日志，它在每个任务开始前都会执行。
8. **禁止**把检查塞进截图/识别循环 —— MAA 侧虽然没有截图循环，但也不要放进回调节流里。

---

## 8. 自测与验收清单（逐条给出结果）

| # | 场景 | 预期 |
|---|---|---|
| 1 | 模拟器正常开启、实例运行中，任务到点 | 无 `EMULATOR START` 类日志，无 WARNING，任务直接执行 |
| 2 | **模拟器完全关闭、实例运行中**，任务到点 | 自动拉起模拟器 → adb 上线 → `asst.connect` 成功 → 任务正常执行 |
| 3 | 场景 2 中任务不是 `MaaStartup` | 自动补排 `MaaStartup`，游戏被唤醒后继续原任务 |
| 4 | `close_emulator` + 队列清空 | 先 `CloseDown` 再关模拟器 → 等待期模拟器进程消失 → 下个任务到点自动拉起并重跑 |
| 5 | 关模拟器失败（如权限不足 / WinError 740） | 只告警不中断，仍排 `MaaStartup`，等待结束后能继续跑 |
| 6 | 模拟器活着但游戏卡死 | **不得**重启模拟器（先 `device_alive()` 判真），走原有失败重试 |
| 7 | `MaaEmulator.Serial` 填错 | `critical` 明确提示，直接返回 False，不空转、不死循环 |
| 8 | MuMu 端口漂移（16384 → 16385） | 自动修正 `MaaEmulator_Serial` 并写回 `maa.json`，后续 connect 使用新 serial |
| 9 | 任务执行中手动关闭模拟器 | 实例不退出，日志 `Emulator died during task, trying to recover`，任务重试一次 |
| 10 | 非 Windows / 无模拟器环境 | 所有新分支直接返回 True/False，行为与改动前完全一致 |
| 11 | 回归：`stay_there` / `close_game` | 行为、日志与改动前一致 |
| 12 | 回归：`MaaCopilot` / `MaaUpdate` 工具 | 正常可用，不触发模拟器启停 |
| 13 | 配置生成器 | 只改动 6.3 列出的文件，主模块文件零改动 |
| 14 | 语法兼容 | `toolkit/python.exe -m py_compile` 全部通过（3.7.6） |

---

## 9. 交付物

1. 代码 diff（建议拆 2 个 commit：`fix(maa): 修复 emulator_check 缺失导致的启动崩溃`、
   `feat(maa): 支持任务前自动拉起模拟器与完成全部任务后关闭模拟器`）；
2. 每个改动文件的「为什么改」一句话说明；
3. 上表 14 条自测结果（未实测的标注原因，不要写「应该没问题」）；
4. 如无法在本机验证某条（例如没有 MuMu），明确列出并说明替代验证手段（桩测试 / 手工注入）。

---

## 10. 优先级（时间不够时按此顺序交付）

1. **R0**（不修则 MAA 插件完全不可用）；
2. **R1**（本需求核心）；
3. **R3 + R4**（新选项，依赖 R1 才能安全启用）；
4. R2 / 日志打磨 / 文档。
