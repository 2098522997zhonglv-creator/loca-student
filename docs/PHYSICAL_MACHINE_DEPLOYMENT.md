# 物理机本地化部署

## 1. 创建环境

```bash
cd loca_stude
conda env create -f environment.yml
conda activate local-knowledge-center
cp .env.example .env
```

修改 `.env` 中的管理员密码。若完全离线运行，不要设置 `QDRANT_URL`，并把 LLM、Embedding、Reranker 地址设置为物理机上的 Ollama、Xinference、vLLM 或 LM Studio 地址。

## 2. 安装和初始化

```bash
./scripts/setup.sh
```

Python 包来自根目录 `requirements.txt`，前端包来自 `frontend/package.json`。脚本不会安装 Docker，也不会启动 PostgreSQL、Redis 或独立 Qdrant。

## 3. 启动

```bash
./scripts/start_backend.sh
```

默认仅监听 `127.0.0.1:8000`。如果需要局域网访问，明确修改 `start_backend.sh` 的监听地址为物理机内网地址或 `0.0.0.0`，并同步设置 `DJANGO_ALLOWED_HOSTS`；不要直接暴露到公网。

## 4. 数据备份

停止服务后备份整个 `data/`：其中包含业务 SQLite、LangGraph 会话、嵌入式 Qdrant 索引及上传文件。恢复时复制回同一项目根目录即可。

嵌入式 Qdrant 适合单 Django 进程。不要同时启动多个操作同一 `data/qdrant` 的进程；需要多进程扩容时应改用独立 Qdrant 服务并设置 `QDRANT_URL`。
