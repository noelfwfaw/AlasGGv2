# 补充任务：MAA 插件「任务开始前确保游戏在运行」

> **本文是 `doc/prompt-maa-emulator-recovery.md` 的补充篇，单独成文也可执行。**
> 前置条件：关闭模拟器（`Optimization.WhenTaskQueueEmpty = close_emulator`）正在实现中。
> 本文不改关闭模拟器的逻辑，只补「游戏没在跑时，任务开始前把它拉起来」这一层保障。
> 行号基于当前 master（`1b75eeeb1` 之后），认函数名比认行号可靠；若发现描述与代码不符，先停下给证据。

---

## 0. 为什么需要这篇

MAA 插件现在的「唤起游戏」是一个**只覆盖自己关掉的游戏**的闭环，不是一个通用保障：

| 现有机制 | 位置 | 覆盖范围 |
|---|---|---|
| `StartUp` + `start_game_enabled: True` | `module/handler/handler.py:598-601` | 唯一的拉起动作，只在 `MaaStartup` 任务里执行 |
| 首次访问 `asst` 时插入 `MaaStartup` 并 `task_stop()` | `maa.py:92-95` | 进程生命周期内**只生效一次**（`asst` 是 cached_property） |
| `close_game` 等待后 `task_call('MaaStartup')` | `maa.py:289-292` | **仅当** `maa_close_game_during_wait()` 返回 `True` |

全插件 grep 不到 `pidof` / `dumpsys` / `am start` / `game_running` —— **没有任何「游戏在不在运行」的判据**。

于是下列场景会「有任务，但游戏没被唤起，任务裸跑后失败」：

1. `stay_there` / `close_emulator` 模式下，游戏被用户手动关闭、被系统杀掉、自行崩溃；
2. `close_game` 的 `CloseDown` 抛异常 → `maa.py:266-269` 吞掉并 `return False` → `closed` 为假 → 不排 `MaaStartup`；
3. 模拟器重启后（游戏进程随之消失）；
4. 上一次 `MaaStartup` 完成后 `task_delay(server_update=True)`，下次是次日 04:00，中间没有第二次机会。

**关闭模拟器上线后，第 3 条会从偶发变成每次等待后必现**：模拟器被拉起、`asst.connect()` 成功，但游戏没起，
下一个 `MaaFight` / `MaaRecruit` 直接 `TaskChainError`。所以这篇必须做。

---

## 1. 需求

### R7. 任务开始前，做一次「游戏是否在运行」的判据

在 MAA 版 `get_next_task()`（`maa.py:272-303`）**`break` 返回任务之前**，按以下顺序处理：

```
1. emulator_check()          # 设备层：模拟器不在就拉起来（已有 / 正在实现）
   └─ False → raise RequestHumanTakeover

2. 设备在线 且 待执行任务 != 'MaaStartup'
   └─ game_alive() 为假 → task_call('MaaStartup') + del_cached_property(config) + continue
   └─ game_alive() 为真 → break，正常执行
```

**为什么顺序不能反**：`game_alive()` 依赖 `adb shell`。设备不在线时 `pidof` 必然失败，
若先判游戏就会把「模拟器没开」误判成「游戏没开」，然后无限插 `MaaStartup`。

### R8. 游戏判据要稳定、廉价、可失败

- 主判据：`adb shell pidof <package_name>`（Android 7+ 可用，MuMu12 是 Android 12，无兼容问题）；
- 备用判据：`adb shell ps -A` 里匹配包名（`pidof` 在某些魔改镜像上不可用）；
- 单次开销约 30~80ms，**只在任务边界做一次**，不得进入任何循环或回调节流器；
- 判定失败要区分「广告：游戏没开」与「adb 抖动」：先确认 `device_alive()` 为真，再把 `pidof` 的一次异常
  视为「未知」，重试 1 次；两次都拿不到才算「游戏没开」；
- 包名映射按 `MaaEmulator_PackageName` 取值：

  | client_type | package_name |
  |---|---|
  | `Official` | `com.hypergryph.arknights` |
  | `Bilibili` | `com.hypergryph.arknights.bilibili` |
  | `YoStarEN` | `com.YoStarEN.Arknights` |
  | `YoStarJP` | `com.YoStarJP.Arknights` |
  | `YoStarKR` | `com.YoStarKR.Arknights` |
  | `txwy` | `tw.txwy.and.arknights` |

  **优先**从 `<MaaPath>/resource/config.json` 读 `client_type → package_name`（MAA 自己维护这份表，
  比内置常量更抗更新），读不到再用上表兜底；两者都拿不到 → `logger.warning` 并**跳过**游戏判据（返回 True），
  宁可漏判也不要误判成「永远没开」。

### R9. 必须防止「无限插任务」

`MaaStartup` 失败时（游戏要更新、账号掉登录、资源损坏），`game_alive()` 会持续为假。
若不设防，调度器会陷入 `MaaStartup → 仍为假 → 再插 MaaStartup` 的死循环。

要求：
- 记录「本次唤醒是否已经尝试过」（实例级标志，**在任务成功执行后清除**）；
- 若上一次执行的**就是** `MaaStartup` 且游戏仍不在 → 不再插入，
  `logger.critical` 明确说明「开始唤醒后游戏仍未运行，请检查账号/更新/MAA 日志」，
  并 `raise RequestHumanTakeover`（与现有失败语义一致，由 `alas.py:675-687` 的 3 次失败计数兜底退出）；
- 连续插入次数上限 1，重置时机明确写在注释里。

### R10. 收紧现有的两条唤醒入口（可选但推荐）

- `maa.py:289-292`：不再用 `closed` 决定是否排 `MaaStartup`。`close_game` 分支统一改为「关游戏（best-effort）→ 排 MaaStartup → 等待 → continue」，
  失败路径交给 R7 的边界判据兜住；
- `maa.py:92-95` 的 `load_asst(check_task=True)`：保留作为第一道防线即可，**不要**再加逻辑；
  注意它内部 `task_stop()` 会中止当前任务，边界判据生效后这条基本不会被触发，不要让它和 R7 互相插任务。

### R11. 不改变的东西

- 不改 `MaaStartup` 任务本身的参数（`start_game_enabled: True` 保持）；
- 不改 `close_emulator` 的实现（本文只消费它带来的「设备在线但游戏不在」状态）；
- 不改 `maa.json` 的任何键（本篇不新增配置项）；
- 不影响 `MaaCopilot` / `MaaUpdate` 工具入口（它们的 `config.task.command == 'Maa'`，不走 `get_next_task`）；
- 非 Windows / 无 adb 环境：判据返回 True，行为与改动前一致。

---

## 2. 实现要点

### 2.1 `game_alive()` 放哪里

放在 `MaaDevice`（`doc/prompt-maa-emulator-recovery.md` 的 R0 引入）里，与 `device_alive()` 同层，
复用同一套 `adbutils.AdbClient` 连接：

```python
def game_alive(self, timeout=2):
    """
    游戏进程是否在运行。

    Returns:
        bool: True 表示在运行或无法判定（宁可漏判也不误判）
    """
    if not self.device_alive():
        return False                      # 设备都不在，谈不上游戏
    package = self.game_package()         # 见 R8 的映射，返回 None 时直接 True
    if package is None:
        return True

    for _ in range(2):                    # adb 抖动重试一次
        try:
            out = self.adb_shell(['pidof', package])
            if out.strip():
                return True
        except Exception as e:
            logger.info(e)
        # pidof 不可用时的兜底
        try:
            if package in self.adb_shell(['ps', '-A']):
                return True
        except Exception as e:
            logger.info(e)
    return False
```

注意：
- `adb shell pidof` 在进程不存在时**退出码非 0**，`adbutils` 会抛异常，必须 `try/except`，不能当成「在运行」；
- 不要在 `adb_shell` 里传管道（`| grep`），adb shell 的引号处理在 Windows 上很容易出错，直接在 Python 侧判断；
- 日志用 `info` 级别，这是每个任务都会走的热路径。

### 2.2 `get_next_task()` 的改动

```python
def get_next_task(self):
    while 1:
        task = self.config.get_next()
        self.config.task = task
        self.config.bind(task)

        if task.next_run > datetime.now():
            ...  # 等待逻辑保持现状（含 close_game / close_emulator 分支）
            ...

        # 设备层：模拟器不在就拉起来
        if not self.device.emulator_check():
            raise RequestHumanTakeover

        # 游戏层：设备在线但游戏没起，先补一次开始唤醒
        if task.command != 'MaaStartup' and not self.device.game_alive():
            if self._maa_wake_attempted:
                logger.critical('开始唤醒后游戏仍未运行，请检查账号登录、游戏更新或 MAA 日志')
                raise RequestHumanTakeover
            logger.critical('游戏未运行，先执行开始唤醒')
            self._maa_wake_attempted = True
            self.config.task_call('MaaStartup')
            del_cached_property(self, 'config')
            continue

        self._maa_wake_attempted = False   # 任务即将真正执行，重置
        break
```

要点：
- `task_call` 默认 `force_call=True`（`module/config/config.py:553`），会无条件把 `MaaStartup.Scheduler.NextRun`
  设为 now 并**落盘**（`update() → load() → save()`，`ArknightsConfig.save` 的 `mod_name='maa'`），
  所以 `del_cached_property(self, 'config')` 之后仍然有效，不要自己去改 json；
- `_maa_wake_attempted` 必须在**真正要执行任务时**重置，否则一次失败会永久锁死后续唤醒；
- 不要在 `emulator_check()` 内部做游戏判定 —— 设备层与游戏层职责分开，`emulator_check` 在 `Device` 语义下只关心 adb 可达。

### 2.3 （推荐）把「是否重启过模拟器」的标志删掉

`doc/prompt-maa-emulator-recovery.md` 的 R1 里建议用 `emulator_restarted` 标志来判断「要不要补 `MaaStartup`」。
**游戏判据天然覆盖这个场景**（模拟器重启 → 游戏进程必然消失 → `game_alive()` 为假），
两者并存会出现「标志位和真实状态不一致」的边界问题。若那份文档尚未落地，直接用本篇替代；
若已经写了标志位，保留也无害，但**不要**用它替代 `game_alive()`。

---

## 3. 明确禁止

1. **禁止**用 `asst.connect()` 的返回值判断游戏是否运行 —— 它只验证 adb 连接，模拟器在、游戏杀掉了同样返回成功；
2. **禁止**在 `get_next_task` 里直接 `asst.append_task('StartUp', ...)` 或 `handler.startup()` ——
   必须走 `task_call('MaaStartup')` 交给调度器，否则会与 `config.task` 绑定、`task_delay`、
   回调 `callback_timer` 互相打架；
3. **禁止**在等待期（`wait_until` 循环里）轮询 `pidof`；
4. **禁止**在设备离线时把 `pidof` 失败当成「游戏没开」；
5. **禁止**在包名映射缺失时返回「没开」（会变成永远插任务）；
6. **禁止**无上限地插 `MaaStartup`（见 R9）；
7. **禁止**把判据塞进 MAA 回调处理函数（回调在 MaaCore 线程里，`adb shell` 会阻塞回调节流）。

---

## 4. 验收清单

| # | 场景 | 预期 |
|---|---|---|
| 1 | 游戏在运行，任务到点 | **不**排 `MaaStartup`，无 `游戏未运行` 日志，任务直接执行 |
| 2 | `stay_there` 下手动杀掉游戏，任务到点 | 先跑 `MaaStartup`（`StartUp` + `start_game_enabled`），游戏被唤起，原任务随后执行 |
| 3 | `close_game` 的 `CloseDown` 失败 | 仍能在下个任务前被唤起（不再依赖 `closed` 标志） |
| 4 | `close_emulator`：模拟器被拉起后 | 依次出现 `模拟器已重启` → `游戏未运行` → `MaaStartup` → 原任务，全程无失败 |
| 5 | 游戏未安装 / 账号掉登录，`StartUp` 救不回来 | 只插 **1 次** `MaaStartup`，随后 `critical` 明确提示并 `RequestHumanTakeover`，**不出现死循环** |
| 6 | 模拟器离线 | 只走 `emulator_check`，不触发游戏判据（日志里不应出现 `pidof` 相关） |
| 7 | 任务队列等待期间 | 不做任何 `pidof` 调用（等待期只有一次设备检查） |
| 8 | `MaaCopilot` / `MaaUpdate` 工具 | 行为不变，不触发 `MaaStartup` |
| 9 | 正常路径日志 | 无 WARNING 及以上；`pidof` 只在 debug/info 级别留痕 |
| 10 | 语法 | `toolkit/python.exe -m py_compile` 全通过（3.7.6） |

---

## 5. 交付物

1. 代码 diff（建议单独一个 commit：`feat(maa): 任务边界确保游戏在运行`）；
2. `game_package()` 的实际实现方式说明（走 `resource/config.json` 还是内置表，以及兜底行为）；
3. 上表 10 条自测结果，未实测的标注原因；
4. 一句话说明 `_maa_wake_attempted` 的重置时机，便于后续 review。
