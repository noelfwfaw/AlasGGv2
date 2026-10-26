# 工单：MuMu 12 启动时自动隐藏模拟器窗口（启动期隐藏 + 老板键唤出）

## 0. 硬约束（先读，违反任何一条即返工）

1. **禁止运行程序。** 不许执行 `alas.py` / `gui.py` / 任何任务入口；不许启动、停止、隐藏、显示任何模拟器；
   不许调用 `MuMuManager.exe`、`adb`；不许连接模拟器。你只做三件事：改代码、跑配置生成器、`compileall` 语法检查。
2. **禁止 git 写操作。** 不许 `git commit` / `git push` / `git stash` / `git checkout -- .`。
   只允许 `git status`、`git diff`、`git show` 这类只读命令。所有改动留在工作区待人工评审。
3. **禁止加依赖**，禁止 3.8+ 语法（本仓按 **Python 3.7** 写：不用海象运算符 `:=`、不用 `dict |`、不用 `match`）。
4. **禁止改游戏内状态循环、素材、截图方式/控制方式的选择逻辑。** 本工单只碰模拟器进程与窗口层面。
5. **禁止顺手重构。** 只改第 3 节列出的文件与位置，其它文件一律不动。
6. 动手前先跑一次 `git status` 存档；改完再跑一次，交付时说明每个文件改了什么。

## 1. 背景与目标

### 1.1 现象

ALAS 通过 `MuMuManager.exe api -v N launch_player` 拉起 MuMu 模拟器时，模拟器窗口会跳到前台，
盖住 ALAS 的 WebUI，用户每次都要手动按 MuMu 的老板键（默认 **Alt+Q**）把它藏起来。

目标：**启动阶段窗口一出现就自动隐藏**，之后只有用户主动按老板键才显示。
注意这不是"最小化"——最小化后任务栏还有图标，用户要的是完全不可见。

### 1.2 运行环境（实测，不要重新假设）

| 项 | 值 |
|---|---|
| 模拟器 | MuMu 模拟器 12，5.x（进程 `MuMuNxMain.exe` / `MuMuNxDevice.exe`） |
| 安装根目录 | `D:/Program Files/Netease/MuMu` |
| 控制台程序 | `D:/Program Files/Netease/MuMu/nx_main/MuMuManager.exe` |
| 实例窗口标题 | **等于实例名**（实测窗口标题就是 `一` / `二` / `三` / `四` / `六`） |
| 多开器主窗口标题 | `MuMu模拟器`（**绝不能隐藏它**） |

### 1.3 已实测结论（这些是有数据支撑的，不要再质疑或改成别的做法）

1. `MuMuManager.exe control -v N hide_window` / `show_window` 可用，返回 `{"errcode":0,"errmsg":""}`；
   实例未启动时返回 `{"errcode":-201,"errmsg":"player not running"}`。单次调用约 **0.45s**（大部分是进程启动开销）。
2. **窗口隐藏后 ALAS 的两条截图链路都正常**：`nemu_ipc` 仍返回 1280x720 且连续两帧 md5 不同（画面在动），
   `adb screencap` 仍返回 600KB 正常截图。点击走 adb / MaaTouch，本就不依赖窗口可见性。
   → 所以**不要**加"隐藏时切换截图方式"之类逻辑。
3. **隐藏状态下模拟器能正常启动**：`sys.boot_completed` 在 21s 内变成 `1`。
4. **MuMu 不记忆隐藏状态**：`hide_window` → `shutdown` → 再 `launch`，0.62s 后窗口又出现。
   所以不能靠"上次隐藏了这次就自动隐藏"。
5. `control launch` 返回后 **0.6~1.5s** 窗口才可见；用 Win32 `ShowWindow(hwnd, SW_HIDE)` 以 20ms 轮询抢隐藏，
   实测"可见时间"可压到 **<50ms**。只用官方 CLI 隐藏会闪 0.5s 以上，只用 Win32 隐藏会让 MuMu 的
   老板键内部状态错位 —— **必须两者都做**：Win32 抢先隐藏，随后补一次官方 `hide_window` 对齐状态。
6. `layout_window` 不接受负坐标（传 -30000 被钳到 50,50），**"把窗口挪到屏幕外"这条路走不通，别试**。
7. 实例配置（`MuMuManager.exe setting -v N --all`，113 个键）里**没有**"启动时隐藏"这类开关，
   只有 `window_save_rect` / `window_size_fixed` / `dynamic_adjust_frame_rate` 等。不用再去找原生开关。

## 2. 现状代码（改动前先读这三个位置）

| 位置 | 现状 | 说明 |
|---|---|---|
| `module/device/platform/platform_windows.py:20-42` | `get_focused_window()` / `set_focus_window()` / `minimize_window()`（`ShowWindow(hwnd, 6)`）/ `get_window_title()` / `flash_window()` | 已有的 Win32 封装，可复用风格，不要删 |
| `module/device/platform/platform_windows.py:83-123` `_emulator_start()` | MuMu12 分支在 `:94-102`，只发 `api -v N launch_player`，不管窗口 | **本工单不改这里** |
| `module/device/platform/platform_windows.py:238-379` `emulator_start_watch()` | 记录 `current_window`，窗口抢焦点时 `set_focus_window(current_window)`；**只有等到 `sys.boot_completed == 1` 之后**（`:369-371`）才 `minimize_window(new_window)` | 问题就在这：从窗口出现到 boot 完成（本机 21s，上限 180s）之间没人处理窗口 |
| `module/device/platform/platform_windows.py:381-403` `emulator_start()` | 3 次重试：`_emulator_stop` → `_emulator_start` → `emulator_start_watch()` | 隐藏器的启停挂在这里（见 R2） |

**因果断言**：窗口从 launch 后 0.6~1.5s 出现，到 boot 完成（21s，上限 180s）才被 `minimize_window` 处理，
这期间窗口一直可见并抢焦点 —— 这就是"挡界面"的全部原因。

**已排除**（不要再往这些方向改）：
- 不是截图方式问题（实测隐藏后两种截图都正常）。
- 不是 `emulator_start_watch` 抢焦点逻辑的问题（那段是对的，保留）。
- 不是 MuMu 设置项问题（113 个键里没有相关开关）。
- 不需要改 `deploy/` 下的 easy install 代码（它有自己的 `deploy/install/emulator_windows.py`，与本工单无关）。

## 3. 实现规格

### R1（必须）新增隐藏器：`module/device/platform/mumu_window_hider.py`

**为什么新开文件**：`platform_windows.py` 现在 424 行，加 100+ 行会超过仓库"单文件 ≤500 行"的规定。

**为什么不能写进 `emulator_windows.py`**：那个文件头部注明会被 Alas Easy Install 直接使用，
不允许 import 本仓模块。本文件要 import `module.base.timer` 与 `module.logger`，只能被 `platform_windows.py` 引用。

类接口（签名照抄，实现自己写）：

```python
class MuMuWindowHider:
    POLL_INTERVAL = 0.02
    NAME_MATCH_TIMEOUT = 3.0

    def __init__(self, console_exe: str, instance_id: int, instance_name: str): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

行为要求：

1. `start()`：先快照当前所有 `MuMuNxDevice.exe` 进程的 pid 作为"基线"，再启动一个 **daemon** `threading.Thread`。
   重复 `start()` 要安全（已在跑就直接返回）。
2. 主循环每轮做两件事，顺序不能颠倒：
   - 对**已记录的 hwnd 集合**再次 `ShowWindow(hwnd, 0)`（`SW_HIDE`）。
     原因：MuMu 在 `starting_vm → starting_rom` 阶段可能自己把窗口 `ShowWindow` 回来，必须持续压制。
   - 再扫一遍候选窗口，把新出现的追加进集合。
3. 候选窗口的判定（用 `EnumWindows`，**回调函数必须保存在实例属性上防止被 GC 回收**，否则枚举会静默失败）：
   - 条件 A（首选）：`IsWindowVisible` 为真、`GetWindowTextW` 标题去掉首尾空白后**等于** `instance_name`；
     若一直不相等，允许退化为 `startswith(instance_name)`（并在 `logger.info` 里打出实际标题）。
   - 条件 B（兜底）：距 `start()` 超过 `NAME_MATCH_TIMEOUT` 秒仍未靠标题命中时，
     取"进程映像名为 `MuMuNxDevice.exe` 且 pid 不在基线集合里"的进程所拥有的可见窗口。
   - **任何候选都要校验进程映像名是 `MuMuNxDevice.exe`**（`QueryFullProcessImageNameW`，大小写不敏感），
     不是就丢弃并 `logger.info` 一次。这条是为了防止误隐藏多开器主窗口（标题 `MuMu模拟器`）。
4. 第一次成功隐藏时：
   - `logger.info` 打出 hwnd、标题、命中的方式（标题/pid）；
   - **在本线程内**执行一次 `subprocess.run([console_exe, 'control', '-v', str(instance_id), 'hide_window'], capture_output=True, timeout=10)`，
     解析 stdout 的 JSON `errcode`：为 `0` 打 `logger.info`；非 0 打 `logger.warning` 带上 `errmsg`，
     **不抛异常、不中断**，继续只靠 Win32 隐藏（旧版 MuMu 没有该子命令时的降级路径）。
   - 这次官方调用只做一次，不要每轮都 spawn 进程（0.45s/次，会把 20ms 轮询节奏彻底拖垮）。
5. 节奏用 `module.base.timer.Timer(self.POLL_INTERVAL)` 的 `wait()/reset()`，不要用裸 `time.sleep`。
6. 线程体内部必须 `try/except` 兜住所有异常并 `logger.info(e)` 后继续循环 —— **这个线程绝不能把异常抛出去**
   （抛出去会静默杀死线程，用户只会看到"有时候隐藏有时候不隐藏"）。允许用 `except Exception as e`，
   但必须记日志，不允许 `pass`。
7. `stop()`：置 `threading.Event`，`join(timeout=3)`；超时也返回（daemon 线程不会阻塞退出）。
8. 不做去隐藏（`show_window`）相关的任何事 —— **显示只由用户的老板键负责**，代码不得主动显示窗口。

### R2（必须）接线：`module/device/platform/platform_windows.py`

1. 顶部新增 `from module.device.platform.mumu_window_hider import MuMuWindowHider`。
2. 新增实例方法 `_create_window_hider(self)`，返回 `MuMuWindowHider` 或 `None`。全部条件满足才创建：
   - `self.config.Emulator_HideWindowOnStart` 为真；
   - `instance = self.emulator_instance` 非 `None` 且 `instance == Emulator.MuMuPlayer12`；
   - `instance.MuMuPlayer12_id is not None`；
   - `instance.name` 非空；
   - `console = Emulator.single_to_console(instance.emulator.path)`，且 `os.path.exists(console)`。
   任何一个条件不满足（含"用户没开这个选项"）→ `logger.info` 说明跳过原因后返回 `None`，**不要用 warning**。
3. `emulator_start()`（`:381`）：在 `logger.hr('Emulator start', level=1)` 之后、`for _ in range(3)` 之前
   创建并 `start()` 隐藏器；整段重试用 `try/finally` 包起来，`finally` 里 `stop()` 隐藏器。
   **必须用 `finally`**：启动失败/超时/抛异常都要停，否则线程会一直把窗口按下去，把用户的老板键废掉。
4. **不改** `_emulator_start()`、**不改** `emulator_stop()`、**不改** `emulator_start_watch()`。
   特别说明：`emulator_start_watch()` 末尾的 `minimize_window(new_window)` 保留原样 ——
   隐藏器生效时它对隐藏窗口是空操作；隐藏器没生效（旧版 MuMu、用户关掉开关）时它是唯一的兜底。
   `flash_window()` 那段也保留。

### R3（必须）配置项：`module/config/argument/argument.yaml`

在 `Emulator:` 组里 `AdbRestart: false` 之后追加一行（**只写默认值，不要写 `type` / `option`**，生成器会推导成 checkbox）：

```
  HideWindowOnStart: true
```

不要加到 `EmulatorInfo:` 组 —— 那个组的帮助文案写着"下列数值是根据 Serial 自动填充的，如果不懂请不要随意修改"，
是自动填充区，不适合放用户开关。

### R4（必须）跑配置生成器

必须用**运行实例自带的 Python 3.7.6**（系统 Python 3.13 没有 `yaml`/`inflection`，跑必失败）：

```
"D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/toolkit/python.exe" -m module.config.config_updater
```

预期产物改动（只有这些）：
- `module/config/argument/args.json`：路径是 `Alas` → `Emulator` → `HideWindowOnStart`，
  内容是 `{"type": "checkbox", "value": true}`（**checkbox 没有 `option` 字段**，别照 `select` 的样式写）；
- `module/config/config_generated.py`：新增一行 `Emulator_HideWindowOnStart = True`（放在 `Emulator_AdbRestart` 附近）；
- `module/config/i18n/{zh-CN,zh-TW,en-US,ja-JP}.json`：`Emulator` 下新增 `HideWindowOnStart`，
  值会是占位字符串（形如 `Emulator.HideWindowOnStart.name`），需要按 R5 手工改成真文案。
- `module/config/argument/menu.json` **不应该变**（本次没有增删任务/分组）。若变了，说明 yaml 写错位置，检查后重跑。

**已知陷阱（必须处理，不要用 `git checkout` 掩盖）**：这个生成器的 `generate_i18n()` 是从空字典重建的，
会把 `module/config/argument/gui.yaml` 里没登记的 `Gui.*` 键静默删掉。历史事故：一次运行删掉了
15 个 `Gui.Statistics.GearSkin*` × 4 语言。所以跑完必须：

1. `git diff module/config/i18n` 逐个文件看；
2. 若出现与 `HideWindowOnStart` 无关的删除（尤其 `Gui.Statistics.GearSkin*`），
   用 `git show HEAD:module/config/i18n/zh-CN.json` 取原文，把这些键**逐条加回去**；
3. **禁止** `git checkout -- module/config/i18n`（会把本次新增的键一起丢掉）。

### R5（必须）四语言文案

在四个 i18n 文件的 `Emulator` → `HideWindowOnStart` 下填 `name` 与 `help`（结构照抄同级 `AdbRestart`，只有这两个键）：

| 语言 | name | help |
|---|---|---|
| zh-CN | 启动时隐藏模拟器窗口 | 启动模拟器后立刻隐藏窗口，需要查看时按 MuMu 老板键（默认 Alt+Q）唤出。仅对 MuMu 模拟器12 生效。 |
| zh-TW | 啟動時隱藏模擬器視窗 | 啟動模擬器後立即隱藏視窗，需要查看時按 MuMu 老闆鍵（預設 Alt+Q）喚出。僅對 MuMu 模擬器12 生效。 |
| en-US | Hide emulator window on start | Hide the emulator window as soon as it is launched. Press the MuMu boss key (Alt+Q by default) to show it again. MuMu Player 12 only. |
| ja-JP | 起動時にエミュレータのウィンドウを隠す | エミュレータ起動直後にウィンドウを隠します。表示するには MuMu のボスキー（既定 Alt+Q）を押してください。MuMu プレイヤー12 のみ有効。 |

该键只有 `name` 与 `help` 两个子键（照抄同级 `AdbRestart` 的结构），不要自己加 `true` / `false` 文案。
文案里不需要花括号，但如果要写字面 `{` `}`，必须写成 `{{` `}}`（`t()` 会走 `str.format()`）。

## 4. 验证边界（本工单最重要的约束）

**你（编码 Agent）允许做的**：
- 读代码、`git status/diff/show`、`python -m compileall <改过的文件>` 做语法检查；
- 跑上面 R4 那一条生成器命令（这是构建步骤，不是运行程序）。

**你禁止做的**（做了就等于在用户的生产挂机环境里乱试）：
- 启动/停止/重启/隐藏/显示任何模拟器；调用 `MuMuManager.exe`、`mumu-cli.exe`、`adb`；
- 运行 `alas.py`、`gui.py`、任何 `module` 下的任务入口、任何 `tests/` 脚本；
- 往 `D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/` 那个运行实例里复制文件（同步由用户自己做）；
- 装依赖、改 `requirements*.txt`、改 `.gitignore`、提交 `.workbuddy/` 或临时调试脚本。

**判断标准**：任何一个验证动作，如果它会让模拟器窗口出现/消失，或者让 ALAS 连上模拟器，就属于禁止项。

## 5. 验收（全部可静态核对，逐条打勾写进交付报告）

1. `module/device/platform/mumu_window_hider.py` 存在，含 `MuMuWindowHider` 类，方法齐全；
   全文搜不到裸 `time.sleep`；`EnumWindows` 的回调对象被保存在 `self.` 上。
2. `platform_windows.py` 里出现 `_create_window_hider()`，且 `emulator_start()` 中的 `start()/stop()` 在 `try/finally` 内；
   `_emulator_start()` / `emulator_stop()` / `emulator_start_watch()` 的 diff 为空（用 `git diff` 证明）。
3. `platform_windows.py` 行数 < 500；`mumu_window_hider.py` 行数 < 200。
4. `argument.yaml` 的 `Emulator:` 组里只有一行新增 `HideWindowOnStart: true`，没有动 `EmulatorInfo:`。
5. `config_generated.py` 里有 `Emulator_HideWindowOnStart = True`；`args.json` 的 `Alas.Emulator.HideWindowOnStart`
   是 `{"type": "checkbox", "value": true}`（与 `Alas.Emulator.AdbRestart` 结构一致）。
6. 四个 i18n 文件都有 `Emulator.HideWindowOnStart.name/help`，且没有出现任何 `Gui.*` 键的删除
   （用 `git diff module/config/i18n` 逐文件确认，把结果贴进报告）。
7. `compileall` 对改过的三个 `.py` 文件零报错。
8. 新代码里没有 `except Exception: pass`、没有 3.8+ 语法、没有新增 import 第三方库。
9. 注释是英文、Google 风格（`Args:` / `Returns:` 视情况），关键函数写清"为什么这么做"（尤其是"为什么必须
   停止隐藏器"和"为什么 Win32 + 官方 API 两道都要"）。

## 6. 人工验证清单（交给用户执行，你不要做）

1. 把改动同步到运行实例 `D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript`
   （按文件覆盖，其中 `config_generated.py`、`args.json`、四个 i18n 都要一起过去）。
2. 重启 ALAS（只关窗口不算，i18n 与配置只在启动时加载一次）。
3. 在 WebUI 的"模拟器设置"里确认出现新开关"启动时隐藏模拟器窗口"，默认勾选。
4. 手动点"启动模拟器"，预期：窗口最多闪一下（<0.1s），随后完全消失、任务栏无图标，ALAS 正常连上并开始跑任务。
5. **按一次 Alt+Q**，预期窗口出现且**一次就能唤出**。若需要按两次才出现，说明老板键状态错位：
   把现象反馈给我，改法是"命中后延迟 1 秒再补一次官方 `hide_window`"，**不要**退化成 Win32-only。
6. 注意一个设计取舍：**启动过程还没结束时按老板键，窗口会被隐藏器立刻按回去**（守护线程到 boot 完成才停）。
   想看模拟器请等启动完成（约 20~90 秒）之后再按。这是刻意的 —— 否则自动隐藏和老板键会互相打架。
7. 让 ALAS 跑满 10 分钟，确认：不卡死、截图正常（`log/` 里没有连续截图失败）、游戏内时间流速正常。
8. 关掉开关再测一次，预期恢复成原来的"启动后最小化"行为（证明回滚路径可用）。

## 7. 交付物

1. `git diff` 全文（含未跟踪的新文件，用 `git status` + `git diff --stat` 概览）。
2. 静态自查报告：逐条对应第 5 节的 9 项，写明核对方式（如"`grep -n 'time.sleep' mumu_window_hider.py` 无输出"）。
3. 一段不超过 5 行的说明：改了哪几个文件、隐藏器什么时候启停、失败时如何降级。
4. 第 6 节人工验证清单原样贴出来，并标注哪些项你无法验证（应该是全部）。
5. 如果发现工单与代码现实不符（行号漂移、`MuMuPlayer12_id` 取不到、`Emulator_HideWindowOnStart` 没生成出来这类），
   **先停下来报告**，不要自行改变设计。

## 8. 不确定项的处理方式

- 行号可能因你之前读过/改过而漂移，用函数名定位，不要按行号硬改。
- 如果 `Emulator.single_to_console()` 对你的路径不返回 `MuMuManager.exe`，按 R2 第 2 条直接跳过隐藏（返回 None），
  不要自己拼路径。
- 如果 MuMu 的窗口标题不是实例名（你无法实测，只能读代码），就按 R1 第 3 条的两级匹配 + pid 兜底实现，
  这已经是能覆盖两种情况的写法，不要再加第三种猜测。
