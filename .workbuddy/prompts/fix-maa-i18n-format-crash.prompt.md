# 任务：修复 ALAS MAA 插件「干员培养」页面 KeyError 崩溃

> 这是一份自包含的修复工单。根因已定位完毕，你不需要重新做全量排查，但**必须自己复现一次**再动手。

## 1. 环境

**唯一允许修改的仓库**
- 路径：`C:/Users/Administrator/Documents/GitHub/AzurLaneAutoScript`
- 当前分支：`master`，最新提交 `52af82fcd feat: 新增「关闭模拟器」选项并修复模拟器掉线自动拉起`
- 待修文件所在的插件目录：`submodule/AlasMaaBridge/`（普通目录，不是 git submodule）

**运行实例（部署副本，不要在这里做正式修改）**
- 路径：`D:/360极速浏览器X下载/AlasApp_0.4.13_fullcn/AzurLaneAutoScript`
- Python：`toolkit/python.exe`，版本 **3.7.6**。写脚本时**不能使用 walrus `:=`、dict 合并 `|`、`str.removeprefix` 等 3.8+ 语法**，f-string 可以用。
- 用户的实际部署方式：源码仓库提交后，在实例目录执行 `git fetch dev && git reset --hard dev/master`。因此正式改动只做在源码仓库；如需在实例上立刻看效果，可把同一个幂等脚本在实例再跑一遍。

## 2. 现象

WebUI → MAA 设置 → 点击任务按钮「干员培养」（task 名 `MaaOperProgress`）→ 页面弹出「应用发生内部错误」，整页渲染中断。

关键堆栈：

```
module/webui/app.py:427 in set_group
    arg_help = t(f"{group_name}.{arg_name}.help")
module/webui/lang.py:37 in t
    return _t(s, LANG).format(*args, **kwargs)
KeyError: '"name"'
```

上下文中可见：`group_name = 'MaaOperProgress'`、`arg_name = 'Plans'`、`s = 'MaaOperProgress.Plans.help'`、`arg_dict = {'Plans': {'type': 'textarea', 'value': '[]'}}`。

**最小复现（不需要启动 GUI、不需要 MAA、不需要模拟器）**

```python
import json
p = 'submodule/AlasMaaBridge/module/config/i18n/zh-CN.json'
help_text = json.load(open(p, encoding='utf-8'))['MaaOperProgress']['Plans']['help']
help_text.format()      # KeyError: '"name"'
```

## 3. 根因（已确认，请自行验证后直接进入修复）

`module/webui/lang.py:30-37`：

```python
def t(s, *args, **kwargs):
    if TRANSLATE_MODE:
        return s
    return _t(s, LANG).format(*args, **kwargs)
```

**所有 i18n 文案在显示前都会过一遍 `str.format()`。**

而 `MaaOperProgress.Plans.help` 里贴了一段 JSON 示例：

```
[
  {"name": "艾雅法拉", "elite": 2},
  {"name": "艾雅法拉", "skill_level": 7},
  {"name": "史尔特尔", "skill_mastery": [3, 0, 0]}
]
```

`format()` 把 `{"name": "艾雅法拉", "elite": 2}` 当作替换字段：字段名从 `{` 之后一直解析到第一个 `:`，得到 **`"name"`（连引号一起）**，kwargs 里没有这个键，于是 `KeyError('"name"')`。这就是报错信息里 key 带引号的原因。异常从 `set_group()` 抛出，pywebio 捕获后弹「应用发生内部错误」。

**影响面（已用脚本全量扫描，供你复核）**

- `submodule/AlasMaaBridge/module/config/i18n/{zh-CN,zh-TW,en-US,ja-JP}.json` 四个文件里，**只有 `MaaOperProgress.Plans.help` 一个 key** 会在 `.format()` 时抛异常（四语言全中，因为四份文案里是同一段 JSON 示例）。
- 主模块 `module/config/i18n/*.json` 里的 `Gui.Text.InvalidFeedBack = "格式错误。示例：{0}"` **是合法的位置参数占位符**，`module/webui/app.py:432` 用 `t("Gui.Text.InvalidFeedBack", value)` 传参调用，**不是 bug，不要动它**。

## 4. 修复要求

### 必须做

1. **只改数据，不改渲染逻辑**：把四个 i18n 文件里 `MaaOperProgress.Plans.help` 中的字面花括号转义为 `{{` / `}}`。
   - `submodule/AlasMaaBridge/module/config/i18n/zh-CN.json`
   - `submodule/AlasMaaBridge/module/config/i18n/zh-TW.json`
   - `submodule/AlasMaaBridge/module/config/i18n/en-US.json`
   - `submodule/AlasMaaBridge/module/config/i18n/ja-JP.json`
   - 只转义 `{` 和 `}`，**不要**碰文案里的 `[]`、`"`、`\n`。
2. **幂等**：脚本重复执行不能把 `{{` 再变成 `{{{{`。先判断该字符串是否已含 `{{`，已含就跳过。
3. **保持文件原格式**：用项目自带的 `module.config.utils.read_file` / `write_file` 读写（它处理编码和缩进）。不要手工 `json.dump(indent=2)` 等，否则会整文件重写、diff 爆炸。写之前先 `git diff` 确认没有大面积格式变动。
4. **保持 3.7 兼容**。

### 禁止做

- 不要修改 `module/webui/lang.py`、`module/webui/app.py`、`submodule/AlasMaaBridge/module/config/config_updater.py`。
- 不要给 `t()` 加 `try/except` 兜底。那是上游主模块代码，会与后续同步上游产生冲突。如果你认为这个方案明显更优，**先说明理由并等待确认**，不要擅自实施。
- 不要修改主模块 i18n 里的 `{0}` 占位符。
- 不要改动任何 MAA 任务参数、调度逻辑、配置生成逻辑——本工单只解决显示层崩溃。
- 不要 `git commit` / `git push` / `git commit --amend`。改动留在工作区。

### 可选（先报告再动手，不要默认实施）

加一个回归检查 `tests/test_i18n_format.py`：遍历 `module/config/i18n` 与各 mod 的 i18n，对每个字符串调用 `.format()`，捕获异常并列出 key；对 `{0}` 这类合法占位符用白名单豁免。目的是以后谁再往 i18n 里贴 JSON 示例能立刻暴露。是否加由你判断后说明，不要静默新增文件。

## 5. 验收标准（逐条附实际输出）

1. 四个语言文件里 `help_text.format()` 不再抛异常。
2. `help_text.format()` 的结果与转义前的原文**完全相等**（即渲染出来仍是单花括号），并断言结果包含 `{"name": "艾雅法拉"`，确认示例内容没被吃掉、也没被当成占位符吞掉。
3. 全量扫描四个插件 i18n 文件，`.format()` 异常数 = 0。
4. `git diff --stat` 只出现 4 个 i18n JSON，且每文件只有 1 行左右的改动（`help` 那一行）。若出现整文件重写，说明第 4 节第 3 条做错了，重做。
5. 用 Python 3.7 语义可运行的脚本完成验证（不得使用 3.8+ 语法）。

## 6. 交付物

1. 四个改好的 JSON 文件。
2. `git diff` 全文。
3. 5 行以内的说明：改了什么、为什么这么改、验证结果（把验收 1-4 的实际输出贴出来）。
4. 一句给用户的提醒：**i18n 只在 `startup()` 里 `lang.reload()` 载入一次，改完必须重启 Alas，刷新浏览器不生效。**

## 7. 背景信息（避免误伤）

- 这四个 i18n 文件由 `submodule/AlasMaaBridge/module/config/config_updater.py::generate_i18n` 生成，但它用 `deep_get(old, keys=k, default=d)` **优先保留旧译文**，所以手改的转义不会被下次生成覆盖。
- `MaaOperProgress`（干员培养）与 `MaaDataUpdate`（更新数据）是本项目上一轮新增的 MAA v6 适配功能。`Plans` 是 `textarea` 类型参数，值为 JSON 数组字符串（默认 `[]`），帮助文案里的示例就是它的格式说明——这也是不能简单删掉示例的原因。
- 项目历史里 MAA 相关改动集中在 `submodule/AlasMaaBridge/`：`maa.py`（入口与调度）、`module/handler/handler.py`（任务参数构造）、`module/config/argument/argument.yaml`（参数定义）、`module/config/i18n/*.json`（文案）。本工单只涉及最后一类。
