# 本地知识中心

这是从 WHartTest 中独立提取的知识库与需求评审子系统。项目保留了项目级数据隔离、用户/组织/模型权限、LLM 配置、Prompt、文件附件、知识库、RAG 问答、需求拆分与需求评审、MCP 和 Skills 扩展。

UI 自动化、接口自动化、测试套件、执行器、任务中心和微信插件均不包含在运行边界内。

## 本地架构

- Django REST 后端：`127.0.0.1:8000`
- Vue 3 前端：`127.0.0.1:5173`
- 业务数据库：本地 SQLite 文件 `data/knowledge_center.sqlite3`
- LangGraph 会话：本地 SQLite 文件 `data/chat_history.sqlite3`
- 向量数据库：嵌入式 Qdrant 目录 `data/qdrant`
- 文档附件：`data/media`
- 运行日志：`data/logs/app.log`（按日轮转；页面「运行日志」可远程查看）
- 异步任务：Celery eager 模式，在 Django 进程内同步执行

不需要 Docker、PostgreSQL、Redis 或独立 Qdrant 服务。

## 环境要求

- Python 3.11+
- Node.js 20+
- npm 10+

## 安装

在目标物理机上创建 Conda 环境。源码包自身不携带 Python 解释器、虚拟环境、Node 模块或模型文件：

```bash
conda env create -f environment.yml
conda activate local-knowledge-center
cp .env.example .env
# 编辑 .env，至少修改 DJANGO_ADMIN_PASSWORD
./scripts/setup.sh
```

`setup.sh` 会构建前端，之后只启动 Django 即可由同一进程提供前端和 API：

```bash
./scripts/start_backend.sh
```

访问 `http://127.0.0.1:8000`。

开发前端时也可以打开第二个终端：

```bash
./scripts/start_frontend.sh
```

此时访问 `http://127.0.0.1:5173`。API 文档位于 `http://127.0.0.1:8000/api/schema/swagger-ui/`。

## 首次配置顺序

1. 使用 `.env` 中的管理员账户登录。
2. 创建项目，并添加项目成员与角色。
3. 在 LLM 配置中添加 OpenAI 兼容模型、DeepSeek、Qwen 或 Ollama。
4. 在知识库全局配置中设置 Embedding API，可选配置 Reranker。
5. 创建知识库并上传文档，等待本地进程完成分块和向量化。
6. 在知识问答中选择项目与知识库后开始 RAG 对话。
7. 在需求评审中上传需求文档，执行模块拆分和专项评审。

本地化仅指数据和基础设施本地存储。LLM/Embedding 是否联网取决于你填写的模型 API；如需完全离线，请连接本机 Ollama、Xinference、vLLM 或 LM Studio。

## 权限模型

- Django 模型权限控制功能入口和增删改查。
- `ProjectMember` 控制项目成员关系，角色为 owner/admin/member。
- 知识库与需求文档均绑定 Project，并在 API 层校验项目成员。
- 全局知识库配置仅管理员可修改。
- LLM API Key、Embedding API Key 等敏感字段存储在本地 SQLite；请限制目录访问权限并避免提交 `.env` 和 `data/`。

## 运行日志

- 文件位置：`data/logs/app.log`（以及按日归档的 `app.log.YYYY-MM-DD`）
- 覆盖范围：系统启动、知识库处理/检索、LLM Agent 对话、需求拆分与评审、Django 请求错误等
- 远程查看：登录后侧栏「运行日志」（需管理员或操作日志查看权限）
- 配置项（`.env`）：`LOG_LEVEL`、`LOG_BACKUP_COUNT`、可选 `LOG_DIR`
- 与「操作日志」区别：操作日志是 API 审计入库；运行日志是进程日志文件
