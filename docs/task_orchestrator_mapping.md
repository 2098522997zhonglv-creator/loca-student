# 任务编排契约映射说明（需求 20261118 §5.2）

首期不新增独立表；语义字段映射到现有模型。

| 契约字段 | 现有落点 |
|---|---|
| task_id / goal / status | `AgentTask.public_task_id` / `goal` / `status` |
| deliverables / authorization_scope / forbidden_actions | `AgentTask.auth_scope`（含 allow/deny/deliver） |
| facts | `AgentTask.facts`（值/来源/确认时间） |
| estimated_nodes / current_node | `auth_scope.estimated_nodes`；`AgentTask.current_stage` |
| 三层步数预设 | `auth_scope.budget_preset` / `layer_step_presets` → `budget.max_tool_actions`（一层预设+二层备用+三层批准预留） |
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
| stage_plan | 由 TaskPlan 节点同步生成；节点完成后 `advance_orchestrator_on_task` 写回 `facts.task_plan` 并同轮扩权下一节点 Skill |
| domain_specialists | 新路径跳过求交 |
| skill_catalog UI/API 动作 | 以 `skill_action_sync` 从脚本同步（D01/D02）；旧名经别名映射 |
| 执行结果关联 | Skill 返回后 `bind_execution_ref` 写入 facts；禁止最新记录串用 |
| 主流程失败清理 | `advance_plan_node(failed=True)` 仍调度 `cleanup_data`（R12） |

## 复杂用例 Skill 能力清单（20261118）

| Skill | 优化要点 |
|---|---|
| task-orchestrator | 来源类型、执行标识、跨工具事实、断言节点、三层步数；P1 `prepare_data`/`cleanup_data` |
| ui-automation | 真实 47 动作入 Catalog；playwright 主浏览器；本次 record_id；P1 变量/条件/参数化/前后置；env→extra_config |
| api-automation | 真实 120 动作入 Catalog；task_execution_id；success≠passed |
| playwright-skill | 业务断言/证据表；主浏览器；禁止弱断言 |

## P1 执行契约摘要

| 项 | 约定 |
|---|---|
| 步骤类型 3/4 | 运行时变量 / 条件；条件假 → skipped |
| 步骤类型 5 | 执行前拒绝（非定位失败） |
| front_* / posterior_sql / parametrize | 模型→序列化→执行器全链路；多行结果隔离 |
| record_id | 受理即建 running 记录；结果更新同一 id |
| env browser 字段 | 写入 `extra_config`，不以同名字段落库 |
