# 功能任务：MAA 插件新增「自定义包名」设置项（魔改 / 重打包客户端支持）

> 提示词使用说明：本文件全文可直接作为任务提示词交给编码代理或工程师。
> 文中行号基于当前 master，可能后续偏移，**认函数名与日志原文比认行号可靠**。
> 若核查后发现下列任一前提与代码不符，请先停下指出证据，不要按错误前提硬改。

---

## 0. 环境与硬约束

- 运行环境 **Python 3.7.6**（`toolkit/python.exe`）。**禁止** 3.8+ 语法（walrus `:=`、`dict |`、内建泛型下标等），**禁止**新增第三方依赖。
- 只改 Maa 插件：`submodule/AlasMaaBridge/`。**不要**改动主仓 `module/`、`alas.py`、主仓的 `module/config/` 与 `config/template.json`。
- 生成器是 `toolkit/python.exe -m submodule.AlasMaaBridge.module.config.config_updater`（`MOD_NAME='maa'`，产出 `maa.json` 那套）。**跑完必须逐文件 diff**，确认没顺手改脏主仓的配置文件。
- 不要改动 `MaaEmulator.PackageName` 的 option 列表（**不新增** `Custom` 之类的下拉项）。

---

## 1. 背景：为什么需要这个功能

实例 `alas2` 在 2026-10-23 16:34 启动失败，根因是**客户端包名不匹配**，不是网络也不是连接：

```
Call ` adb.exe -s 127.0.0.1:16608 shell am start -n com.hypergryph.arknights/com.u8.sdk.U8UnityContext ` ret 1
stdout:
Starting: Intent { cmp=com.hypergryph.arknights/com.u8.sdk.U8UnityContext }
Error type 3
Error: Activity class {com.hypergryph.arknights/com.u8.sdk.U8UnityContext} does not exist.
```

而该模拟器上实际安装的是 **`com.hypergryph.arknightt`**（注意结尾多了一个 `t`，`versionName=2.7.81`），
它的启动 activity 恰好也是 `com.u8.sdk.U8UnityContext`，`cmd package resolve-activity --brief` 可确认：

```
com.hypergryph.arknightt/com.u8.sdk.U8UnityContext
```

这是**魔改 / 重打包客户端**。MaaCore 只用 `client_type` 去查资源里的包名映射：

`<MaaPath>/resource/config.json`

```json
"packageName": {
    "Official": "com.hypergryph.arknights",
    "Bilibili": "com.hypergryph.arknights.bilibili",
    "YoStarEN": "com.YoStarEN.Arknights",
    "YoStarJP": "com.YoStarJP.Arknights",
    "YoStarKR": "com.YoStarKR.Arknights",
    "txwy": "tw.txwy.and.arknights"
}
```

用户目前只能手工改这个 json 才能用（已验证：把 `Official` 改成 `com.hypergryph.arknightt` 后可以启动）。
但 `MaaUpdate.AutoUpdateResource = true`，**资源一更新就被覆盖回去**，而且改错了没有明确报错。

同时这次失败还暴露了一个体验问题：MaaCore 把 `am start` 的 `ret=1` 当成连接层故障，走了 5 轮
`AdbController::reconnect`（每轮 10 秒：`adb connect` 明明返回 0 成功，却再跑一次同样的 `am start`），
**白等 50.4 秒**后报 `Disconnect: Reconnect failed`，把"包名不存在"伪装成"重连失败"。

---

## 2. 需求

### R1 新增配置项 `MaaEmulator.CustomPackageName`

- 位置：`module/config/argument/argument.yaml` 的 `MaaEmulator` 组（现有 `Serial` / `PackageName` / `MaaPath` / `TouchMethod` / `DeploymentWithPause`）。
- 写法参照同文件里的 `Serial`：

```yaml
MaaEmulator:
  Serial:
    value: 127.0.0.1:5555
    valuetype: str
  PackageName:
    value: Official
    option: [ Official, Bilibili, YoStarEN, YoStarJP, YoStarKR, txwy ]
  CustomPackageName:      # ← 新增
    value: ""
    valuetype: str
```

- 语义（**必须严格按这三条实现，不允许自行放宽**）：
  1. **为空** → 使用「所选服务器对应的预设包名」（内置映射表，见 R2），并把 `resource/config.json` 里的
     `packageName[<选中 client_type>]` **写回预设值**（如果它与预设不一致）。这样"留空 = 用官方预设"的语义是确定的，
     不会因为历史上被手改过而行为漂移。
  2. **非空** → 校验格式后，把它写入 `packageName[<选中 client_type>]`，覆盖预设。
  3. 只影响 `client_type` 与所选服务器相同的那一个键，**其它 5 个键保持原样**。

- i18n 四个语言各加一条（`name` + `help`）。中文文案建议：

```
"CustomPackageName": {
  "name": "自定义包名（可选）",
  "help": "留空则使用「游戏服务器」对应的官方包名。\n仅在游戏为魔改 / 重打包客户端（包名与官方不同）时填写，例如：com.hypergryph.arknightt\n只填包名，不要填 Activity（不要出现 \"/\"）。\n修改后需要重启该 ALAS 实例才会生效。"
}
```

### R2 包名映射的注入（核心实现）

MaaCore 的资源在 `AsstLoadResource()` 时一次性读入，之后改文件无效。因此注入必须发生在**加载资源之前**。

- 注入点：`maa.py` 的 `load_asst()` 里，**`self.maa_update()` 之后、`AssistantHandler.load(...)` 之前**。
  - 顺序不能反：`maa_update()` 可能覆盖 `resource/config.json`，先注入会被冲掉。
  - 当前对应位置约在 `maa.py:336`（`self.maa_update()`）与 `maa.py:351`（`AssistantHandler.load(...)`）之间。
- 实现建议：新建 `module/asst/package_override.py`（或 `module/handler/` 下），暴露两个函数：

```python
DEFAULT_PACKAGE_NAME = {
    "Official": "com.hypergryph.arknights",
    "Bilibili": "com.hypergryph.arknights.bilibili",
    "YoStarEN": "com.YoStarEN.Arknights",
    "YoStarJP": "com.YoStarJP.Arknights",
    "YoStarKR": "com.YoStarKR.Arknights",
    "txwy": "tw.txwy.and.arknights",
}

def resolve_package_name(client_type, custom_package):
    """返回最终应使用的包名（不写文件）"""

def apply_package_override(maa_path, client_type, custom_package):
    """写入 <maa_path>/resource/config.json 的 packageName[client_type]，返回最终包名"""
```

- `apply_package_override()` 的实现要求：
  1. `path = os.path.join(maa_path, 'resource', 'config.json')`；不存在 → `logger.warning` 并返回预设值，**不要中断**。
  2. `json.load` 后取 `data.get('packageName')`；不是 dict 或为空 → 记录 warning，但仍按预设值继续（不写文件）。
  3. 目标值 `target = custom_package.strip() or DEFAULT_PACKAGE_NAME[client_type]`。
  4. **仅当** `packageName[client_type] != target` 时才写回；写回用「临时文件 + `os.replace`」保证原子性，
     `json.dump(..., indent=4, ensure_ascii=False)`，**保留原始 dict 的键顺序与其它所有键**（Py3.7 dict 有序，直接改值再 dump 即可）。
  5. 写文件失败（权限 / 占用）→ `logger.critical` 说明"无法写入 MAA 资源包名映射，自定义包名不会生效"，并把目标值也打出来，
     然后 `raise RequestHumanTakeover`（继续跑只会在 50 秒后以误导性的 `Reconnect failed` 收尾，不如直接停）。
  6. 日志各一条，便于验收时 grep：
     - 自定义生效：`MAA 客户端类型 Official 使用自定义包名 com.hypergryph.arknightt`
     - 使用预设：`MAA 客户端类型 Official 使用预设包名 com.hypergryph.arknights`
     - 发生了写文件：`已更新 <path> 的 packageName.Official: A -> B`
  7. **不做退出还原**（不要 `atexit`）。理由：进程被强杀时不可靠；下次启动会重新注入；还原反而会制造竞态。

- 注入结果要存到实例属性上（如 `self.effective_package_name`），供 R3 校验使用。

### R3 启动前校验包名（把 50 秒空转变成一条明确的错误）

- 位置：`load_asst()` 里 `AssistantHandler.load(...)` 之后、首次 `handler.connect()` 之前；
  或统一放在 `handler.startup()` 里 `StartUp` 之前（二选一，**只做一次，不要每个任务都做**）。
- 做法：用已有的 `MaaEmulator.adb_client()`（`module/device/emulator.py`，`alive()` 已在用）执行
  `adb -s <serial> shell pm list packages`，判断目标包名是否在结果里。
- 不存在时，按下面格式输出并 `raise RequestHumanTakeover`：

```
CRITICAL | 设备 <serial> 上未安装包 <target>
CRITICAL | 当前「游戏服务器」为 <client_type>，「自定义包名」=<custom or 空>
CRITICAL | 设备上看起来相关的包：com.hypergryph.arknightt
CRITICAL | 若这就是你的客户端，请把它填入「MAA 设置 - 自定义包名」；否则请检查「游戏服务器」是否选错
```

  - "看起来相关的包"= 包名中包含 `arknights` / `hypergryph` / `yostar` / `txwy`（不分大小写）的条目，最多列 5 个。
  - 找不到任何相关包时，最后一行给"设备上未找到任何疑似明日方舟的包"。
- 校验失败**不要**继续 `StartUp`，否则又是 50 秒 reconnect。
- 该检查约 50ms，只在首次加载时做一次即可。

### R4 包名格式校验

- 正则：`^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$`
- 命中 `/` 时单独给一句提示："这里只填包名，不要填 Activity（形如 `包名/Activity`）"。
- 校验失败 → `logger.critical` + `raise RequestHumanTakeover`，**不要**静默忽略、也不要降级成预设值
  （静默降级会让用户以为配置生效了，实际上跑的是官方包）。

### R5 运行中改配置需重启实例

`Asst.__lib` 是类级缓存，资源一个进程只加载一次。请在一处日志里说明：
`自定义包名已变更，需重启该 ALAS 实例才会重新加载 MAA 资源`（检测方式可选：把最终包名与上次进程内记录的比较）。

---

## 3. 明确禁止的做法

1. **不要**把自定义包名当作 `client_type` 传进去（如 `maa_start('StartUp', {'client_type': 'com.xxx'})`）。MaaCore 是拿
   `client_type` 去 `packageName` 映射里查值，未知类型无法保证行为；必须保持 `client_type` 为所选服务器，只改映射值。
2. **不要**在 `MaaEmulator.PackageName` 的 option 里加 `Custom` 选项 —— 用户要的是一个文本输入框，不是新的枚举值。
3. **不要**在每次任务前都改写 `resource/config.json`，只在 `AsstLoadResource` 之前做一次。
4. **不要**改动 `resource/config.json` 里的 `connection` / `options` 等其它键，也不要重排/重新序列化掉未知字段（除了缩进）。
5. **不要**用 `atexit` / `finally` 做退出还原。
6. **不要**把 `resource/config.json` 写坏后自行"重建"整个文件；读不到就只告警并用预设值。
7. **不要**在 `MaaDevice` 里另起一套 adb 调用；复用 `MaaEmulator.adb_client()`。
8. **不要**新增第三方依赖（含 `filelock`）。若担心并发写，用 `os.open(path, os.O_CREAT | os.O_EXCL)` 建一个
   `<path>.lock` 并在 `finally` 删除即可，或者干脆只打一行 warning。

---

## 4. 需要改动的位置清单

| 文件 | 改动 |
|---|---|
| `submodule/AlasMaaBridge/module/config/argument/argument.yaml` | `MaaEmulator` 组加 `CustomPackageName: {value: "", valuetype: str}` |
| `submodule/AlasMaaBridge/module/config/i18n/zh-CN.json` 等 4 个 | 加 `name` + `help` |
| `submodule/AlasMaaBridge/module/asst/package_override.py` | 新建：`DEFAULT_PACKAGE_NAME` + `resolve_package_name()` + `apply_package_override()` |
| `submodule/AlasMaaBridge/maa.py` | `load_asst()` 在 `maa_update()` 之后、`AssistantHandler.load()` 之前调用注入；存 `self.effective_package_name`；R3 校验接线 |
| `submodule/AlasMaaBridge/module/handler/handler.py` | 仅当把 R3 校验放在 `startup()` 时改（二选一），不要在两处都做 |
| 生成产物 | `module/config/argument/args.json`、`module/config/config_generated.py`、`config/template.maa.json`、4 个 i18n 的正确性由生成器保证 —— **手写 i18n 后跑一次生成器，再 diff** |

---

## 5. 验收标准（逐条自测并给出日志证据）

设备：`127.0.0.1:16608`，已装 `com.hypergryph.arknightt`（无官方包）。

1. **留空 = 预设**：`CustomPackageName=""`，`PackageName=Official`。日志出现
   `使用预设包名 com.hypergryph.arknights`，且 `resource/config.json` 的 `packageName.Official` 被写回该值（首次会有一条"已更新"日志）。
2. **填写后生效**：`CustomPackageName="com.hypergryph.arknightt"`。日志出现 `使用自定义包名`，
   `resource/config.json` 对应键被改为该值，MaaCore 日志里 `am start` 的 `-n` 变成
   `com.hypergryph.arknightt/com.u8.sdk.U8UnityContext`，`StartUp` 正常通过。
3. **不误伤**：`PackageName=Bilibili` + 留空 → 映射仍是 `com.hypergryph.arknights.bilibili`，
   且 `Official` 键的值**不受影响**。
4. **错误包名快速失败**：把自定义包名填成设备上没有的 `com.hypergryph.arknights`，
   必须在 `StartUp` 之前打出 4 行明确错误（含"设备上看起来相关的包：com.hypergryph.arknightt"），
   **不得**再出现 5 轮 `Reconnecting` / `Disconnect: Reconnect failed` / 50 秒空转。
5. **格式错误**：填成 `com.hypergryph.arknightt/com.u8.sdk.U8UnityContext` → 打出"只填包名"的提示并停，不写文件。
6. **资源更新后自愈**：手动把 `resource/config.json` 的 `Official` 改回官方值，重启实例 → 自动重新注入，日志有"已更新"。
7. **不回归**：`Optimization.WhenTaskQueueEmpty` 的 `stay_there` / `close_game` / `close_emulator` 三种模式各跑一次，
   `CloseDown`（关游戏）必须作用于**自定义包名**（因为用的是同一份映射），日志与其行为与本次改动前一致。
8. **主仓无副作用**：`git status` 里不出现 `module/`、`alas.py`、`config/template.json` 的改动。
9. **兼容性**：`toolkit/python.exe -c "import submodule.AlasMaaBridge.maa"` 通过；所有改动文件 `py_compile` 通过。

---

## 6. 备选方案（仅在主方案被 MAA 资源校验干扰时启用，不要主动上）

如果实测发现"改了 `resource/config.json` 后 MAA 会认为资源损坏并重新下载 / 校验失败"，改用**接管启停**方案：

- `StartUp` 传 `start_game_enabled: False`；由桥接层自己 `adb shell am start -n <pkg>/<activity>`：
  activity 优先用 `cmd package resolve-activity --brief <pkg>` 动态取，取不到用 `com.u8.sdk.U8UnityContext` 兜底。
- `CloseDown` 同样换成 `adb shell am force-stop <pkg>`。
- 其余逻辑（什么时候启动/关闭、`MaaStartup` 的插入时机、`emulator_check`）保持不变。

这个方案不碰资源文件，但把"启停游戏"的职责从 MaaCore 挪到桥接层，行为面更大，**只在主方案确实不可行时采用**，
并且要在交付说明里写清楚为什么切换。

---

## 7. 交付物

1. 代码改动 + 生成后的配置产物。
2. 简短说明：注入时机怎么定的、为什么空值要写回预设、并发/资源更新的风险如何处理。
3. 第 5 节的自测记录（贴关键日志行，尤其是第 2、3、4 条）。
4. 若某项无法验证（例如没有 Bilibili 服环境），明确写出来，不要声称通过。
