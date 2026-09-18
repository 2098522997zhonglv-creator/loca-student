# 提取范围与架构

## 保留的领域

| 领域 | 后端应用 | 前端入口 |
|---|---|---|
| 登录、用户、组织、模型权限 | `accounts` | 用户/组织/权限管理 |
| 项目层级和成员角色 | `projects` | 项目管理和全局项目选择器 |
| LLM、会话、Token、工具审批 | `langgraph_integration` | LLM 配置、知识问答 |
| Agent、上下文摘要、停止/恢复 | `orchestrator_integration` | SSE 对话流 |
| Prompt | `prompts` | 对话系统提示词 |
| 知识库配置、文档、分块、检索 | `knowledge` | 知识库管理 |
| 需求、模块拆分、评审报告 | `requirements` | 需求管理和评审报告 |
| 对话附件 | `file_management` | 文件管理和附件选择器 |
| 外部工具扩展 | `mcp_tools`, `skills`, `api_keys` | MCP、Skills、API Key |
| 审计 | `operation_logs` | 操作日志 |
| 用例管理、套件、执行、思维导图 | `testcases`, `testcase_templates` | 用例管理（含脑图）/套件/执行历史/模板 |
| UI 自动化（页面/步骤/执行记录） | `ui_automation` | UI自动化（WebSocket 需 Daphne） |

接口自动化、定时任务中心和微信插件仍不属于本项目运行边界。UI 自动化的远程浏览器执行依赖独立 Actuator 客户端。

## 数据层级

```text
User
 └─ ProjectMember ── Project
                       ├─ KnowledgeBase
                       │    └─ Document
                       │         └─ DocumentChunk ── embedded Qdrant collection
                       ├─ RequirementDocument
                       │    ├─ RequirementModule
                       │    └─ ReviewReport
                       │         ├─ ReviewIssue
                       │         └─ ModuleReviewResult
                       ├─ ChatSession ── ChatMessage
                       ├─ FileAsset / FileReference
                       └─ Skill
```

`KnowledgeGlobalConfig` 是全局单例，保存 Embedding、Reranker 和默认分块参数；每个 KnowledgeBase 可覆盖分块大小与重叠长度。`LLMConfig` 独立保存对话模型参数。

## 本地运行链

浏览器访问 Django；Django 同时提供构建后的 Vue SPA 和 REST/SSE API。业务模型写入 SQLite，LangGraph checkpoint 写入另一 SQLite，向量写入嵌入式 Qdrant。需求拆分、文档向量化在 Celery eager 模式中直接执行，不需要 Redis 和 Worker。
