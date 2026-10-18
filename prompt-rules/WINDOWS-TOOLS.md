# WINDOWS-TOOLS：Windows 与工具操作专项规则

- prompt-rules/WINDOWS-SSH.md：通过 SSH 访问用户授权的 Windows 目标机时，加载 AskPass 认证、会话控制和凭据边界。

本规则适用于 Windows 文件、PowerShell、网络、Git、外部账号和 Codex 本地配置，由上级 `../AGENTS.md` 按任务范围加载。

- Shell：所有命令使用 PowerShell 语法，搜索优先使用 `rg` 和 `rg --files`。
- 路径：文件操作使用 PowerShell 原生 Cmdlet；变量或外部路径在 Cmdlet 支持时使用 `-LiteralPath`，写入前解析绝对路径。创建目录时，先用 `Test-Path -LiteralPath` 核对目标，再通过 `New-Item -Path` 传入已核对的固定绝对路径。
  - 正例：
    ```powershell
    if (Test-Path -LiteralPath $targetDirectory) {
        throw "目标目录已存在：$targetDirectory"
    }
    New-Item -ItemType Directory -Path $targetDirectory -ErrorAction Stop
    ```
- Shell 边界：文件枚举、移动、覆盖和删除在同一 PowerShell 流程中完成；使用 PowerShell 自带的文件操作命令，把路径作为参数交给它。
- 写入：文本修改使用专用补丁工具，批量转换限定输入、输出和写入范围。
- 覆盖：覆盖已存在的目标，以任务明确授权覆盖为执行条件；递归移动或删除前验证解析路径处于用户指定边界。
- 命令验证：命令完成后检查退出状态、关键输出和目标状态；输出仅保留验证所需的信息；凭据、令牌和无关敏感数据采用省略或完整遮蔽的形式。
- 网络续跑：临时网络中断时保持原地址、参数和上下文，使用继续或恢复机制多次重试。
- 外部副作用：外部写入可能重复时，重试前查询远端状态和幂等依据。
- 终止条件：权限拒绝、认证失败、配额终止、无效输入和确定性业务错误终止执行。
- Git 读取：Git 默认操作范围限定为读取状态、差异、历史、分支和远端信息。
- Git 写入：暂存、提交、推送、拉取、合并、变基、切换或创建分支、标签及改写历史需要单独授权。
- 外部账号：只访问用户指定范围；发布、发送、删除、授权、付款和共享状态变更需要明确授权；结果不确定时先查询实际状态。

## 补丁载荷与工具调用（执行前逐项检查）

每次调用工具前，核对本节中与当前解析层和操作类型相关的全部检查项，全部确认后执行调用。

- 字符串插值边界：变量后紧接冒号或其他变量名字符时，必须使用 `${name}`、`$($name)` 或格式化运算符分隔变量名。
  - 正例：`"### $($path):$start-$end"`、`'{0}:{1}-{2}' -f $path, $start, $end`。
- 范围集合建模：每个范围使用包含起点、终点两个明确字段的对象，或作为单个元素保存和遍历的二元对象。
  - 正例：`$ranges = @([pscustomobject]@{ Start = 2520; End = 2600 }); foreach ($range in $ranges) { $end = [Math]::Min($range.End, $lines.Count) }`。
- `foreach` 管道边界：先收集 `foreach` 语句的输出，再将收集结果传入管道。
  - 正例：
    ```powershell
    $results = @()
    foreach ($path in $paths) {
        $results += [pscustomobject]@{ Path = $path; Exists = Test-Path -LiteralPath $path }
    }
    $results | Format-Table
    ```
- 多路径参数扁平化：向 `-LiteralPath` 等接受字符串数组的参数传入多组结果时，先合并为一维字符串数组，再传入参数。
  - 正例：
    ```powershell
    $paths = @(
        $mappings | ForEach-Object {
            $_.Current
            $_.Backup
        }
    )
    Get-Item -LiteralPath $paths
    ```
- 当 apply_patch 通过 functions.exec 嵌套调用时，JavaScript 只负责调度；Markdown、LaTeX 和目标文件内容属于补丁载荷。载荷解析失败时，按 JavaScript 调度层错误定位和修正。
  - 正例：
    ```javascript
    const patch = String.raw`+ Markdown 文本`;
    text(await tools.apply_patch(patch));
    ```
- 统一补丁文本的行首 `+` 和 `-` 是补丁控制符。需要添加或删除本身以这两个字符开头的 Markdown 内容时，在操作前缀后保留内容字符：添加 `- 项目` 写作 `+- 项目`，删除 `- 项目` 写作 `-- 项目`。
  - 正例：
    ```javascript
    const patch = [
      "*** Begin Patch",
      "*** Update File: C:\\work\\note.md",
      "@@",
      "-- 旧项目",
      "+- 新项目",
      "*** End Patch",
    ].join("\n");
    text(await tools.apply_patch(patch));
    ```
- 补丁载荷包含 Markdown 反引号、LaTeX 反斜杠或美元花括号表达式时，优先使用直接的 freeform 补丁；嵌套调用时采用分隔符安全的结构化传递或占位符替换。String.raw 用于保留反斜杠，模板字符串分隔符按所选传输方式处理。
  - 正例：
    ```javascript
    const patch = ["+ 文本包含 `inline code`"].join("\n");
    text(await tools.apply_patch(patch));
    ```
- 使用 JavaScript 模板字符串承载 PowerShell 的 `${name}` 时，先用占位符表示 `${`，再在调用 `apply_patch` 前还原。
  - 正例：
    ```javascript
    const patchTemplate = String.raw`+$manifest = "scenario_manifest_§OPEN§robustPlanName}.tsv"`;
    const patch = patchTemplate.replaceAll("§OPEN§", "$" + "{");
    text(await tools.apply_patch(patch));
    ```
- 区分错误语义：JavaScript SyntaxError 发生在调用 apply_patch 前的调度字符串解析阶段，修正后再调用；apply_patch verification failed 表示补丁上下文匹配失败，重新读取目标范围并修正补丁；补丁成功返回且回读确认后，报告文件已修改。
  - 正例：
    ```javascript
    const current = await tools.shell_command({
      command: "Get-Content -LiteralPath 'C:/work/note.md' -Raw"
    });
    const patch = String.raw`根据当前内容重建的最小补丁`;
    text(await tools.apply_patch(patch));
    const updated = await tools.shell_command({
      command: "Get-Content -LiteralPath 'C:/work/note.md' -Raw"
    });
    ```
- 重试前定位失败层级，根据原因更换传输方式或修正上下文，再执行重试。
- 补丁使用最小必要上下文；涉及中文、LaTeX、反引号或多次连续修改时，先读取精确页块，再分段提交并逐段回读验证。
