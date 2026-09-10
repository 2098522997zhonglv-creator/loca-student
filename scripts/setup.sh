#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

python -c 'import sys; assert sys.version_info >= (3, 11), "需要 Python 3.11 或更高版本"'
node -e 'const major=Number(process.versions.node.split(".")[0]); if(major<20) process.exit(1)'

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env；请设置管理员密码和本机模型服务。"
fi

python -m pip install -r requirements.txt
npm --prefix frontend install
npm --prefix frontend run build

set -a
source .env
set +a
python backend/manage.py migrate
python backend/manage.py init_admin
echo "环境、前端构建和本地数据初始化完成。"
