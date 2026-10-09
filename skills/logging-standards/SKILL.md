---
name: logging-standards
description: 创建、审查、重构或审计应用日志，使其具备业务可追溯性、有效诊断价值、受控日志量和隐私保护。用户要求新增或修改日志、减少重复或噪声日志、制定日志规范、追踪业务流程，或评估日志是否反映真实状态变化与失败边界时使用。
---

# 日志规范

设计日志，使支持人员无需阅读源码或处理大量内部细节，即可还原业务流程、关键决策与真实失败边界。

## 仓库级接入规则

每个仓库只定义一个全局、通用的 logger 实例或全局指针。所有模块和业务链路复用该入口，并通过 `module` 字段区分来源；不要按功能、模块或单条流程再创建新的全局 logger。

| 规则 | 正例 | 反例 |
|---|---|---|
| 复用唯一全局实例 | 应用生命周期内初始化一个全局 logger，订单、导入和导出流程都复用它 | 每个功能各自创建 `g_pOrderLogger`、`g_pImportLogger` 和 `g_pExportLogger` |
| 使用模块字段区分来源 | `[INFO][ImportService] ...` 与 `[INFO][ExportService] ...` 共用同一 logger | 用不同 logger 实例代替模块来源字段 |
| 保持既有生命周期 | 在既定的应用或业务上下文开始处初始化，在既定结束处释放 | 在循环、helper 或每次请求中重复创建全局 logger |

接入或调整日志前，先读取仓库已有 logger 的实际 API 和实现，确认可用级别、字段顺序、初始化与销毁时机、返回语义、并发行为、落盘策略和敏感数据限制。已有契约优先于调用方自行发明的格式、配置或第二套日志框架。

## 上下文字段契约

保持业务阶段、函数调用点和消息正文三者职责分离：`module` 表示日志来源，`stage` 表示业务阶段，`functionName` 表示代码调用点，`message` 表示已确认的业务事实。

当前 `WorkflowLogger` 的实际输出顺序是：

```text
[timestamp][LEVEL][module][stage][functionName] message
```

它的调用形态是 `appendInfo(stage, functionName, message)`、`appendWarn(...)` 和 `appendError(...)`。调用方必须遵循此顺序；不要把阶段名塞入函数名、把函数名塞入消息，或自行调整字段顺序。

| 规则 | 正例 | 反例 |
|---|---|---|
| 字段职责分离 | `[INFO][Billing][Saving][InvoiceStore::save] 发票已持久化 | invoiceId=...` | `[INFO][Billing][InvoiceStore::save][保存发票] InvoiceStore::save completed` |
| 遵循现有 API | `appendInfo("Saving", "InvoiceStore::save", "发票已持久化 | invoiceId=...")` | `appendInfo("InvoiceStore::save | Saving | 成功")` |
| 遵循真实输出顺序 | 按现有实现解析 `[LEVEL][module][stage][functionName]` | 调用方假设或另造 `[LEVEL][function][module][stage]` 格式 |

## 日志格式化职责

将用于日志的稳定格式化函数集中在共享日志组件中，例如 `WorkflowLogger`。这类格式化函数负责把候选对象身份、枚举、状态、布尔值或其他需要跨模块保持一致的领域值转换为可检索文本；目标工程负责提供真实业务值并调用格式化函数。

| 规则 | 正例 | 反例 |
|---|---|---|
| 格式化函数归日志组件所有 | 在 `WorkflowLogger` 中提供并复用 `formatCandidateKey(key)` | 在每个业务 `.cpp` 中重复实现候选对象键的字符串拼接 |
| 复用稳定映射 | 目标工程调用 `Logger::formatStatus(status)` 后写日志 | 在调用点用局部 lambda 或 `switch` 自行把同一状态转成文本 |
| 保持 formatter 纯粹 | formatter 只将输入值转换为稳定、脱敏的日志文本 | formatter 修改领域对象、读取业务状态或触发 I/O |
| 按共享需求扩展 | 多个调用点或日志检索依赖同一表示时，在日志组件新增格式化函数 | 为单次消息拼接在目标工程新增局部格式化函数并让格式逐渐分叉 |

先检查共享日志组件是否已有对应 `format*` 函数；已有时直接复用。需要新增时，将格式化函数放在日志组件的公开接口与实现中，并遵循其构建 profile、外部类型依赖和敏感数据限制。不要为日志格式化污染目标工程的业务源文件。

## 工作流

1. 读取已有 logger 的 API、格式、生命周期和 I/O 限制。
2. 查找共享日志组件已有的 formatter，并确定缺失格式化是否需要跨模块复用。
3. 确认支持人员需要回答的问题：执行了哪条业务流程、作出了什么决策、发生了什么变化、失败位于哪里。
4. 沿状态变化、持久化、请求、响应和失败边界追踪流程。
5. 按下列规则分类每个候选事件。
6. 每个有业务意义的事件保留一条记录；汇总重复工作。
7. 除非用户明确授权业务行为变更，只修改日志调用、级别、消息和 guard。
8. 验证正常与失败路径仍可重建，并确认删除的事件不会改变支持人员的排障判断。

## 事件规则

| 规则 | 保留或记录 | 删除或避免 |
|---|---|---|
| 记录业务事件 | `[INFO] 账户状态已变更 | accountId=... | pending->active` | `[INFO] validateAccount() completed` |
| 只陈述已确认事实 | `[INFO] 导出请求已发出 | requestId=...` | 工作刚入队就写 `导出成功` |
| 用 Info 记录正常流程 | `[INFO] 导入完成 | accepted=98 | rejected=2` | 完全省略正常路径证据，只记录错误 |
| 用 Error 记录真实失败 | `[ERROR] 导出失败 | phase=upload | requestId=... | reason=timeout` | 把允许的空结果当作错误 |
| 用 Warn 记录可处理的降级 | `[WARN] 缓存不可用 | source=primary | fallback=database` | 为每次常规重试或预期分支写 Warn |
| 保留有诊断价值的身份和数量 | `[INFO] 候选对象已解析 | candidateId=... | matches=1` | 记录无法改变判断的循环下标、临时标记或派生统计 |
| 汇总重复工作 | `[INFO] 记录处理完成 | total=1000 | accepted=998 | failed=2` | 为 998 条成功记录逐条写日志 |
| 控制数据暴露与日志量 | `[INFO] 请求负载已发送 | requestId=... | payloadBytes=2048` | 记录完整负载、个人信息、自由文本或逐元素内容 |
| 保持业务行为 | 在确认状态更新后追加 logger 调用 | 为改善日志而重排状态更新、改变返回值或修改请求内容 |

不要机械地为每个函数添加 `Started` 和 `Completed`。仅在开始或结束构成有意义的业务阶段边界时记录。事实已确认时，优先使用 `requested`、`dispatched`、`sent`、`received`、`changed`、`matched` 或 `persisted` 等事实性表述。

## 消息形态

现有 logger 支持字段时，使用稳定形态：

```text
[级别] 业务阶段 | 已确认事件 | stableIdentity=... | relevantCount=... | reason=...
```

只包含能够回答支持人员问题的字段。使用稳定业务标识或已批准的脱敏标识。仅在变化本身有价值时记录前后值。

## 审查清单

- 确认仓库只使用一个全局通用 logger，模块通过上下文字段区分。
- 确认调用点遵循已有 logger 的级别、字段顺序、生命周期、返回语义与落盘限制。
- 确认 `module`、`stage`、`functionName` 和 `message` 的职责没有混淆。
- 确认日志专用格式化函数位于共享日志组件，目标工程没有重复的局部格式化实现。
- 确认每条保留的 Info 都记录真实状态转换、决策或结果。
- 确认每条 Error 都标识真实失败边界与原因。
- 确认正常路径证据足以还原流程。
- 删除跨层重复确认与循环中的逐项成功噪声。
- 只保留能够改变诊断结论的身份、数量、请求/响应事实和值。
- 除非明确要求并获得批准，排除敏感、大体积或自由文本内容。
- 仅用于日志记录的局部临时变量必须在 logger guard（例如 `if (g_pWorkflowLogger)`）内声明和使用；不得在 guard 外创建后仅供日志使用。表达清晰时优先直接拼接。
- 验证补丁没有改变业务状态、执行顺序、返回值、持久化或请求内容。

## 验收标准

交付的日志应使支持人员能够回答：执行了什么流程、发生了哪些关键决策和变化、观察到了哪些请求或响应事实，以及具体在何处因何失败。日志应足够简洁以适用于常规同步落盘，并对目标读者保持安全。
