---
name: task-orchestrator
description: 任务编排：依据用户目标、持续有效授权与能力目录，生成结构化计划并按三层规则选择工具。一层直接执行已授权操作；二层在已有授权内自动备用；三层对超授权动作征求批准后恢复原节点。由 Runtime 连续完成条件获取、工具调用、业务核验与交付。
---

# task-orchestrator

## 连续执行与耗时

对已给定 URL、颜色、文本和数量的购物车测试，先以 `knowledge_search` 查询必要规则并判断适用性，再调用内置表单操作：
1. `execute_skill_script(skill_name="playwright-skill", browser_plan={"action":"inspect_form","url":"目标 PDP","screenshots":true})`：读取真实 DOM 并返回 `bindings`，无需重复读取 Skill。
2. 同一会话调用 `browser_plan={"action":"run_form_test","url":"同一 PDP","bindings":上一步绑定,"rows":[{"key":"row-1","value":"文本","color":"完整颜色名","quantity":1}],"empty_check":true,"no_extras":true,"screenshots":true}`：连续执行必填、预览、参数行与购物车回读。参数行必须对应当前任务，不包含已执行行；额外前置操作先在原脚本路径完成。
3. `needs_binding` 表示未知控件，按 snapshot 修复当前步骤；已确认的成功和业务失败都保留，未知写入先回查。此操作不能覆盖上传、登录、下单等额外目标。

等待页面条件、实际响应、预览更新；不要插入固定秒数等待。使用 Runtime 的 page/context 与原生 script，读取本机产物使用 Node fs，避免跨系统 shell 命令。内置操作已经读取页面结构，原始脚本仍先检查当前 DOM。


## 输入

- 本轮指令、关联任务目标、已有授权与禁止项
- 已确认事实、执行节点、已有产物、工具可用状态
- 统一能力目录摘要（选中后再读完整参数 / Skill 正文）
- 用例来源类型：`platform_testcase` / `ui_testcase` / `api_scenario` / `api_interface_case` / `temp_browser_script`（标识命名空间不混用）

## 复杂测试编排要点

1. **能力目录**：以 Skill 脚本真实 `--action` 为源（UI≈47、API≈120）；旧 Catalog 名经别名映射，失效名不得推荐。
2. **服务探测**：UI / API / 浏览器 CLI 按实际 endpoint 与执行器可用性选择路径；缺失时交付具体缺口。
3. **执行标识**：发起执行即绑定 `execution_id`／`record_id`／`task_execution_id`，等待与报告全程关联本次。
4. **跨工具事实**：API 提取的对象 ID 原样写入 facts，浏览器节点引用同一值；禁止模型改写 token/订单号后再传递。
5. **断言**：每项预期绑定节点与执行 ID；弱断言（URL 含 order、外层 success）不得判通过。
6. **步数预设**：执行长链路使用一层预设 / 二层备用 / 三层批准预留（见 budget_presets）；预计业务节点 ≠ 工具调用上限。
7. **数据生命周期（P1/R12）**：复杂/参数化/前后置话术插入 `prepare_data` → `run_or_browser` → `assert_and_deliver` → `cleanup_data`。仅引用准备成功对象；主流程失败仍清理；清理失败与测试失败分报。
8. **结构化 UI（P1）**：变量/条件/参数化/前后置由执行器打通；条件假 = `skipped`；Python 类型提前报不支持；结果汇总含 `parametrize_results`。
9. **失败收敛**：明确不可重试的业务失败结束当前参数行，保存响应后继续其余可执行行；全部行结束后进行最终态回读与结果交付。测试执行完成与业务断言通过分别记录；禁止为等待业务通过而重复诊断同一错误。已有结果与浏览器会话在同一任务内复用。
10. **进度与计数**：业务节点按实际证据完成；工具动作按唯一 tool_call_id 记录，批次按实际启动动作计数。模型轮次、SSE 重播和历史回放均不增加工具次数；工具详情归到本轮执行记录中。

## 三层规则

| 层 | 判定 | 执行 |
|---|---|---|
| 一层 当前执行 | 当前节点直接需要且已获授权 | 直接调用；只向模型提供当前需要的工具 |
| 二层 自动备用 | 补条件、核验或等价替代，且在已有授权内 | 缺条件/主工具失败时自动加载；不向用户申请扩权 |
| 三层 待授权 | 完成任务需要但超出已有授权 | 展示动作、对象与影响；批准后恢复原节点 |

分层对象是**具体操作及对象**，不是整个 Skill 的永久等级。明确禁止的操作直接排除，不可申请；系统权限禁止的操作不能用普通确认绕过。

## 计划契约（语义）

- 任务：`task_id`、`goal`、`deliverables`、`authorization_scope`、`forbidden_actions`、`facts`、`estimated_nodes`、`current_node`、`status`
- 节点：`node_id`、`goal`、`input_refs`、`output_keys`、`depends_on`、`primary_candidates`、`fallback_candidates`、`completion_checks`、`status`
- 待授权：`action_id`、`task_id`、`node_id`、`target`、关键参数、影响摘要、批准结果
- 调用结果：复用现有 ToolResult（`operation_id`、能力标识、真实状态、证据、错误类型）

## 执行顺序

读取任务 → 补齐必要条件 → 规划当前节点 → 加载工具并执行 → 保存结果并核验 → 推进下一节点 → 核验最终态 → 提取数据并交付。

仅在等待用户、明确取消、真实无法恢复故障或资源上限时中断。

## 三层步数预设

预计业务节点与工具调用次数分开。执行测试用例默认抬高预算，并按用例条数放大：

| 层 | 含义 | 执行用例基线（可随条数放大） |
|---|---|---|
| 一层预设 | 当前节点直接执行已授权操作 | 48 + 每多 1 条用例 ×12 |
| 二层备用 | 补条件 / 主工具失败 / 浏览器等价备用 | 32 + 每多 1 条 ×8 |
| 三层批准预留 | 超授权动作批准后续跑 | 16 |

合计写入 `max_tool_actions`（受服务端硬上限）。续跑时执行路径默认再续 +48。

## 完成条件

- 业务终态满足用户目标（工具 `succeeded` 不足够）
- 交付含目标对象、状态、约定字段与证据；未取得字段标明原因
- 不伪造标识；不重复已成功写操作；禁止支付则不执行也不申请支付

## 工程说明

实现位于 `orchestrator_integration.task_orchestrator`。功能开关 `TASK_ORCHESTRATOR_ENABLED`（默认开启）；关闭后新任务回退 zone/commission。在途任务保留 `strategy_version`，不因开关切换重放写操作。旧 `request_tool_expansion` 映射为二层自动启用适配入口。
