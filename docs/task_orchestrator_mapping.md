# 任务编排契约映射说明（需求 20261118 §5.2）

首期不新增独立表；语义字段映射到现有模型。

| 契约字段 | 现有落点 |
|---|---|
| task_id / goal / status | `AgentTask.public_task_id` / `goal` / `status` |
| deliverables / authorization_scope / forbidden_actions | `AgentTask.auth_scope`（含 allow/deny/deliver） |
| facts | `AgentTask.facts`（值/来源/确认时间） |
| estimated_nodes / current_node | `auth_scope.estimated_nodes`；`AgentTask.current_stage` |
| nodes[] | `AgentTask.stage_plan`（StageSpec 映射）+ `facts.task_plan` |
| pending_auth | `facts.task_plan.pending_auth`；批准复用写确认/中断 |
| strategy_version | `facts.strategy_version`（`task_orchestrator_v1` / `zone_commission_v1`） |
| 调用结果 | 现有 ToolResult / AgentStep |

## 开关与回退

- 环境变量 / settings：`TASK_ORCHESTRATOR_ENABLED`（默认 true）
- 关闭后**新任务**走旧 zone + commission + Escape Hatch
- **在途任务**按 `facts.strategy_version` 固定策略，不重放写操作

## 旧逻辑处理

| 组件 | 本期 |
|---|---|
| skill_zones / playbooks | 保留快速识别与权限约束；新路径不再用其结果二次裁剪挂载 |
| task_commission 主辅 + Escape | 新路径由三层选择替代；`request_tool_expansion` 适配为二层自动启用 |
| stage_plan | 由 TaskPlan 节点同步生成，节点完成后真实刷新工具集合 |
| domain_specialists | 新路径跳过求交 |
