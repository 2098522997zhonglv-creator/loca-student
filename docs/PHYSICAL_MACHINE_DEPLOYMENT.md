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

macOS / Linux：

```bash
./scripts/start_backend.sh
```

Windows（局域网访问，监听 `0.0.0.0:8000`）：

```powershell
conda activate local-knowledge-center
.\scripts\start_backend.ps1
```

然后用 `http://192.168.32.138:8000` 访问（IP 以实际为准）。请确认 `.env` 中 `DJANGO_ALLOWED_HOSTS` 包含该 IP；不要直接暴露到公网。

## 4. Windows 自动拉取更新（方案 A）

推送到 GitHub `main` 后，由物理机计划任务自动 `git pull`、按需构建并重启服务。详见 [WINDOWS_AUTO_UPDATE.md](./WINDOWS_AUTO_UPDATE.md)。

## 5. 运行日志

统一写入 `data/logs/app.log`（日轮转）。管理员可在 Web 侧栏「运行日志」远程查看、筛选级别、下载文件。自动更新脚本的 `auto_update.log` / `django.*.log` 也在同一目录。

## 6. 数据备份

停止服务后备份整个 `data/`：其中包含业务 SQLite、LangGraph 会话、嵌入式 Qdrant 索引、上传文件与运行日志。恢复时复制回同一项目根目录即可。

嵌入式 Qdrant 适合单 Django 进程。不要同时启动多个操作同一 `data/qdrant` 的进程；需要多进程扩容时应改用独立 Qdrant 服务并设置 `QDRANT_URL`。
