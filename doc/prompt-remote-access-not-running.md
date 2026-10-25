# 修复任务：远程控制页长期显示「未运行，与服务器的连接断开或服务器离线」

> 提示词使用说明：本文件全文可直接作为任务提示词交给编码代理/工程师。
> 这是一次**缺陷修复 + 架构性整改**，涉及 WebUI 后台任务调度与部署层的子进程调用，不是改文案。

## 0. 硬约束（违反即不合格）

- **只允许修改工作区仓库** `C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript`。
  运行实例在 `D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript`，
  **禁止**在实例目录里改任何文件（改实例由用户自己同步）。
- **禁止改动 `submodule/` 下的任何内容**（尤其 `submodule/AlasMaaBridge/`）。本问题与 MAA 完全无关。
- **禁止执行 `git commit` / `git push` / `git reset --hard` / `git stash` / `git checkout -- .` / `git pull`**。
  工作区里有未提交的 MAA 子模块改动（`i18n/*.json` 等），任何回滚类命令都会摧毁它们。
  改动全部留在工作区，由用户自行决定是否提交。
- 运行实例 Python 为 `toolkit/python.exe`（**3.7.6**）：禁止 walrus（`:=`）、禁止 `dict |`、禁止
  f-string `=` 调试语法、禁止引入新第三方依赖。
- **4.1 验证边界**（见第 4 节，强制）：禁止启动 ALAS / GUI / uvicorn / 任何 alas 实例，
  禁止 adb、禁止模拟器、禁止 `pip install`、禁止真的执行 `git fetch` / `git pull`。

## 1. 现象（日志原文 + 用户实测）

### 1.1 用户看到的

> 「未运行，与服务器的连接断开或服务器离线。远程控制未启用」

这两句命中的是 i18n 键 `Gui.Remote.NotRunning` 与面板标题 `Gui.Remote`，取值来自实例仓库
`module/config/i18n/zh-CN.json`：

```
"Remote": "远程控制",
"Running": "运行中",
"NotRunning": "未运行，与服务器的连接断开或服务器离线",
"NotEnable": "未启用，在 deploy.yaml 中设置 webui 密码并启用远程控制",
"EntryPoint": "远程访问 url 地址:",
```

**为什么可以断定不是 `NotEnable`**：`module/webui/app.py:1601-1611` 的分支是

```
elif state in (0, 3):
    if State.deploy_config.EnableRemoteAccess and State.deploy_config.Password:
        put_text(t("Gui.Remote.NotRunning"))        # app.py:1609
    else:
        put_text(t("Gui.Remote.NotEnable"))         # app.py:1611
```

实例 `config/deploy.yaml` 里 `RemoteAccess.EnableRemoteAccess: true`、`Webui.Password` 非空，
两个条件都成立，代码**不可能**走到 `NotEnable`。所以状态值一定是 `0` 或 `3`，
且渲染的是 `NotRunning`。

### 1.2 实例日志（`D:/…/AlasApp_0.4.13_fullcn/AzurLaneAutoScript/log/2026-10-25_gui.txt`）

**A. 健康周期（能起来，但一起就被杀）**

```
13:40:04.541 | INFO | Add task <keep_ssh_alive (delay=60)>
13:40:05.120 | INFO | Remote access service is not running, starting now
13:40:05.121 | INFO | Start remote access service
13:40:05.131 | INFO | remote access process pid: 4356

13:40:01.733 | INFO | Alas closed.                          ← clearup()
13:40:01.874 | INFO | PyWebIO application remote access service exit.
13:40:01.877 | INFO | Exception occurred, killing ssh process
13:40:01.878 | INFO | Exit remote access service thread
```

在本文件全部 4000+ 行里，**每一次 `remote access service exit.` 都紧跟在 `Alas closed.` 之后**，
没有一次是中继服务器主动断开。也就是说隧道不是"连不上"，而是"每次 WebUI 重启都被 `clearup()` 杀掉"。

**B. 最后一小时：ssh 再也没起来过**

```
14:40:41.982 | INFO | <<< LAUNCHER CONFIG >>>              ← 第 1 次重启
14:40:42.537 | INFO | Add task <keep_ssh_alive (delay=60)>
14:40:42.539 | INFO | "…/git.exe" fetch origin master
14:41:17.299 | INFO | [ allowed failure ], error_code: 128
14:41:37.065 | INFO | [ allowed failure ], error_code: -1073741510

14:41:39.703 | INFO | <<< LAUNCHER CONFIG >>>              ← 第 2 次重启
14:41:40.247 | INFO | Add task <keep_ssh_alive (delay=60)>
14:41:40.248 | INFO | "…/git.exe" fetch origin master
                    ↓↓↓ 此后 5 分 48 秒，整个日志文件一行都没有 ↓↓↓
14:47:28.675 | INFO | <<< LAUNCHER CONFIG >>>              ← 第 3 次重启

14:47:29.209 | INFO | Add task <keep_ssh_alive (delay=60)>
14:47:29.211 | INFO | "…/git.exe" fetch origin master
14:47:50.333 | INFO | [ allowed failure ], error_code: -1073741510
14:48:19.621 | INFO | <<< LAUNCHER CONFIG >>>              ← 第 4 次重启
```

关键事实：**14:40:42 → 14:54:51 共 14 分钟里重启了 7 次（14:40:41 / 14:41:39 / 14:47:28 /
14:48:19 / 14:48:44 / 14:49:14 / 14:54:51），而这 7 个进程里没有一次出现
`Remote access service is not running, starting now`。**

`remote_access process pid:` 也一次都没有再出现。

**C. 用户打开远程控制页的时间点**

```
14:58:19.105 | INFO | Add task <Switch_remote_refresh (delay=1)>
15:04:20.167 | INFO | [ allowed failure ], error_code: 128
```

`Switch_remote_refresh` 就是 `module/webui/app.py:1630` 那个
`self.task_handler.add(remote_switch.g(), delay=1, pending_delete=True)`，
说明用户 14:58 打开了远程控制面板；面板每秒调一次 `RemoteAccess.get_state()`，
拿到的一直是 `0`，于是稳定显示「未运行」。

## 2. 根因

### 2.1 直接原因

`RemoteAccess.get_state()`（`module/webui/remote_access.py:216-226`）返回 `0`
⟸ `RemoteAccess.is_alive()`（同上 `:207-214`）为 `False`
⟸ 它要求 `_ssh_thread is not None and _ssh_thread.is_alive() and _ssh_process.poll() is None`
⟸ 新进程里 `_ssh_thread` 恒为 `None`，因为 **`start_remote_access_service()` 从来没有被调用过**。

### 2.2 致命原因：`git fetch` 卡死，把唯一的 TaskHandler 线程占满，`keep_ssh_alive` 永远排不上

- `module/webui/app.py:2046-2062` `startup()` 的顺序是：
  `task_handler.add(updater.check_update, updater.delay)` → `task_handler.start()` → 最后才
  `task_handler.add(RemoteAccess.keep_ssh_alive(), 60)`。
- `Task` 构造函数把 `next_run` 初始化为 `time.time()`（`module/webui/utils.py:102`），
  即 `check_update` **在启动瞬间就立刻执行**，`keep_ssh_alive` 排在它后面。
- `module/webui/utils.py:197-229` 的 `loop()` 是**单线程串行**：`task.send(self)` 是同步阻塞调用，
  一个任务不返回，后面所有任务都跑不了。
- `updater.check_update`（`module/webui/updater.py:183`）→ `_check_update()`（`:69-93`）→
  `self.execute(f'"{self.git}" fetch {source} {self.Branch}', allow_failure=True)`，**3 次重试**。
- `DeployConfig.execute()` 的实现是 `error_code = os.system(command)`（`deploy/config.py:183`），
  **没有任何超时**。

证据：14:41:40.248 打印了 fetch 命令，然后直到 14:47:28.675 才出现下一条日志，
中间 5 分 48 秒整个进程零输出。这不是"没别的事可记"，而是**任务线程卡在那一次 `os.system()` 里**。
进程被重启后，`keep_ssh_alive` 又是"添加了但没跑到"，如此循环 7 次。

> 为什么 `git fetch` 会挂：实例 `.git/config` 的 origin 是**直连** `https://github.com/noelfwfaw/AlasGGv2`，
> 而 deploy.yaml 里配的是 `https://cdn.gh-proxy.org/https://github.com/noelfwfaw/AlasGGv2.git`。
> 两处不是同一个地址，直连在国内网络下报 `error_code: 128` 或长时间无响应。
> （当前从开发机 `git ls-remote` 该代理地址是通的，返回 `11616577e1b6efd308e1249daf902e25cbd44990`，
> 所以问题不在"地址错"，在"用的是没代理的那个"。）

**这是设计债**：`deploy/git.py:48-50` 的 `remote set-url` 只在 `git_install()` 里执行，
而 `git_install()` 只有真正执行更新时才被调用（`updater.py:204` `run_update` → `_run_update`）。
日常每分钟的**检查更新**直接 `fetch origin`，用的永远是 `.git/config` 里遗留的旧地址。

### 2.3 加剧原因：WebUI 反复重启 + `clearup()` 必定杀 ssh

- `deploy.yaml` 里 `Webui.EnableReload: true`，日志里 `[Reload] True`。
- `module/webui/app.py:2065-2071` `clearup()` 第 2 行就是 `RemoteAccess.kill_ssh_process()`。
- 每次重启都会把已经建立的隧道掐掉，而新进程的 `keep_ssh_alive` 又被 2.2 卡住。

### 2.4 连带缺陷（会被误当根因，需一并处理）

| 位置 | 问题 |
|---|---|
| `module/webui/remote_access.py:139-142` | `finally:` 里**无条件**打印 `Exception occurred, killing ssh process`，正常退出也打，日志严重误导（本次排查第一眼就被它带偏） |
| `module/webui/remote_access.py:197-200` | `ParseError`（`SSHServer` 为 null/格式错）时 `task_handler.remove_current_task()`，**永久移除**保活任务，之后再也不重试，UI 只会显示"未运行"，不提示配置错误 |
| `module/webui/remote_access.py:191` | `keep_ssh_alive` 用 `_ssh_thread.is_alive()` 判断存活，而 UI 用 `is_alive()`（线程 + 进程双条件），两套判定不一致：进程已死、线程还在收尾的窗口内，保活认为"活着"不重启，UI 认为"没运行" |
| `module/webui/remote_access.py:47-48, 80-88` | `setup_timeout=60` 靠 `timeout_killer` 睡 60s 后 kill；而 `startup()` 里保活间隔也是 60s，最坏情况"刚建立就被下一次巡检误判" |
| `module/webui/app.py:2062` | 保活任务依赖 `Password is not None`，但日志里 `[Password] True` 说明这条没问题，**不要改这段判断条件** |

### 2.5 已排除（不要再往这些方向查）

- **不是配置没开**：`EnableRemoteAccess: true`、`Password` 已设、`SSHUser`/`SSHServer` 都有值，
  所以走的是 `NotRunning` 分支而不是 `NotEnable`。
- **不是中继服务器挂了**：开发机 `Test-NetConnection app.hk1.azurlane.cloud:10022` →
  `TcpTestSucceeded=True`，解析到 `43.132.174.132`，端口通。
- **不是 ssh 没装**：`ssh` 解析到 `C:\Windows\System32\OpenSSH\ssh.exe`；
  而且日志里每次都能打印 `remote access process pid: NNNN`，说明 `Popen` 成功了，
  不是 `remote_access.py:71-76` 那个 `FileNotFoundError` 分支（那条分支会置 `_ssh_notfound=True`，
  UI 会额外warning「系统中没有 ssh 工具」）。
- **不是 ssh 参数写错**：`remote_access.py:62` 拼的命令
  `ssh -oStrictHostKeyChecking=no -R /:127.0.0.1:22267 -p 10022 <user>@app.hk1.azurlane.cloud -- --output json`
  与实际运行的 `SSHServer`/`WebuiPort` 对得上。
- **不是 MAA 插件的问题**，也不是 i18n 的 `str.format()` 问题（那是上一轮的 `KeyError` 工单）。

## 3. 步骤

### R1（必须）给子进程调用加超时

`DeployConfig.execute()`（`deploy/config.py:168-194`）目前是 `os.system` + 无超时。
改成带超时的 `subprocess.run`，超时按"失败"处理（`allow_failure` 语义不变），
并且 `show_error()` 打印的"Last command"照旧可用。
注意同仓库还有一份 `deploy/Windows/config.py:199` 的同名实现，两份要保持行为一致
（`module/webui/` 走的是 `deploy/config.py`，别只改一份）。

要求：单次命令的墙钟上限必须可配置且有默认值；`updater.execute_output()` 也要加同样的上限。

### R2（必须）让"检查更新"绝不长时间占住后台线程

`updater._check_update()`（`module/webui/updater.py:69-93`）：

- 3 次重试保留，但每次必须有上限，且**整体**要有上限（建议 ≤ 30 秒）；
- 失败只 `logger.warning("Git fetch failed")` 后 `return False`，不得抛异常；
- 该函数是跑在 TaskHandler 单线程里的，**任何一行都不允许无界阻塞**。

### R3（必须）`keep_ssh_alive` 不再依赖 TaskHandler 单线程

两种落地方式任选其一，写在 diff 注释里说明选了哪个：

- **方案 A（推荐）**：`startup()` 里直接起一个 `threading.Thread(daemon=True)` 跑保活循环，
  进程内首次**立即**尝试连接（不等 60 秒），失败后 5~10 秒重试，成功后按 60 秒巡检。
- **方案 B**：仍用 `task_handler.add`，但在 `startup()` 里**同步**先调一次
  `start_remote_access_service()`，让"打开就能连上"，保活只做兜底。

无论哪种，都要保证：**WebUI 起来后 10 秒内必须已经发起过一次 ssh 连接**。

### R4（必须）统一存活判定为单一真源

`keep_ssh_alive` 内部的 `_ssh_thread.is_alive()` 判断改为 `RemoteAccess.is_alive()`，
与 `get_state()` 用同一套条件，消除"保活认为活着 / UI 认为没运行"的分歧。

### R5（必须）修掉会误导排查的日志与"永久放弃"

- `remote_access.py:139-142`：`finally` 里只有在真的捕获到异常时才打印
  `Exception occurred, ...`；正常退出打印中性文案。
- `remote_access.py:197-200`：`ParseError` 不要 `remove_current_task()`，
  改为记录一个可被 UI 读取的错误状态（例如新增 `RemoteAccess.get_error()`），
  并保留重试（配置改好后不用重启 Alas 就能恢复）。

### R6（建议）让 deploy.yaml 的代理地址对"检查更新"也生效

在执行 fetch 之前，用 `self.Repository` 校正 `origin`（复用 `deploy/git.py:48-50` 那段
`remote set-url` 逻辑，抽成可复用方法），使"检查更新"与"实际更新"走同一个地址。

### R7（必须，用户侧，代码里不要做）

写进交付说明，由用户自己执行：

- 把实例 `.git/config` 的 origin 改成与 deploy.yaml 一致的代理地址，
  或给 `Deploy.Git.GitProxy` 填本地代理；
- 排障期间先临时把 `Deploy.Update.EnableReload` 设为 `false`，停掉重启churn。

### 不需要改的（明确划出，避免扩散）

- 不要动 `deploy.yaml` 的 `Webui.Password` / `RemoteAccess.EnableRemoteAccess` 判断逻辑；
- 不要改 `Gui.Remote.*` 的任何 i18n 文案；
- 不要动 `remote_access.py:62` 的 ssh 命令行拼接；
- 不要顺手"优化" `module/webui/utils.py` 的 TaskHandler 调度模型（这条改动面太大，
  本次只要求 R3 把 ssh 保活挪出去）；
- 不要碰 `submodule/`。

## 4. 约束

- 改动范围：`deploy/config.py`、`deploy/Windows/config.py`（若必须对齐）、
  `deploy/git.py`（仅 R6）、`module/webui/updater.py`、`module/webui/remote_access.py`、
  `module/webui/app.py`（仅 `startup()` / `clearup()` 附近）。
- Python 3.7.6 语法上限；不新增依赖；不改配置生成器（`arguments/` 那套）。
- 保持向后兼容：老的 `deploy.yaml` 不新增必填字段，缺省行为与现在等价。

### 4.1 验证边界（强制，不得省略）

**本任务只允许只读查证，禁止实例化运行验证。** 具体：

- ✅ 允许：`Read` / `Grep` / 读 `log/*_gui.txt`、读 `config/deploy.yaml`、读 `.git/config`、
  静态语法检查（`python -m py_compile`，仅编译不执行）。
- ❌ 禁止：启动 `Alas.exe` / `gui.py` / `uvicorn` / 任何 `alas` 实例；
  禁止 adb / 模拟器 / 真机；禁止 `pip install`；
  **禁止真的执行 `git fetch` / `git pull` / `git remote set-url`**（会改 `.git`，且在有未提交改动时风险极高）；
  禁止 `git commit` / `git push`。
- 所有"只能靠跑起来才能确认"的结论，一律移入第 5 节「人工验证清单」，交回用户执行。
- 若某处判断必须实测才能确定，**停下来写明"需要用户确认"**，不要自行尝试运行。

## 5. 验收（由用户人工执行）

1. 启动 ALAS，打开「远程控制」页。**10 秒内**应显示「运行中」+ 一个可点击的 url；
   不应再出现「未运行，与服务器的连接断开或服务器离线」。
2. `grep -n "remote access" log/<当天>_gui.txt`，检查每次
   `Remote access service is not running, starting now` 之后 5 秒内必须有
   `remote access process pid:`，且**不应**紧随其后就出现 `exit.`。
3. `grep -n "Exception occurred" log/<当天>_gui.txt`：正常情况下应**搜不到**
   （只有真异常才允许出现）。
4. 在断网/代理不通的情况下启动 ALAS：日志里允许出现 `Git fetch failed`，
   但**不允许**出现"一次 fetch 把日志卡住几分钟"的情况，
   也不允许 `<<< LAUNCHER CONFIG >>>` 在 60 秒内连环出现。
5. 修改 `deploy.yaml` 把 `SSHServer` 改成 `bad:port` 重启：UI 应有明确的配置错误提示，
   且改回正确值后**不重启 Alas** 也能自动恢复。

失败排查分支：

- 若第 1 条仍显示「未运行」→ 先看第 2 条 grep 结果，区分是"没调用 `start_remote_access_service`"
  还是"调用了但 ssh 立刻退出"；
- 若是后者 → 打开 `module/logger` 的 debug 级，看 `remote access service command: …` 与
  `ssh server stdout: …` 两行，把 stdout 原文贴出来，再决定是不是中继侧问题。

## 6. 交付物

1. `git diff` 全文（工作区，未提交）。
2. **静态自查报告**：逐条对照 R1~R6，写"改了哪个文件的哪几行、为什么这样改、
   是否有其它调用点受影响"；对每条"禁止项"确认未触碰。
3. **5 行以内说明**：根因一句话 + 每处改动的效果一句话。
4. **人工验证清单**：把第 5 节按可勾选步骤列出来，标明每条"预期观察"。
5. 若发现需要用户提供的信息（例如实例 `.git/config` 是否已改代理、是否需要保留 `EnableReload`），
   单独列成「待用户确认」区块，不要自行假设后强行实现。
