---
name: whart-test
description: 本地知识中心的项目与文件管理工具集。用于查询项目列表，以及项目文件附件的上传、下载、预览、删除、引用查询、file_ids 校验和自动清理设置。当用户需要查询项目信息，或上传/下载/预览/删除项目文件、校验 file_ids、管理文件清理设置时使用。
---

# 本地知识中心项目与文件管理

## 快速开始

```bash
# 设置环境变量
export WHARTTEST_BACKEND_URL="http://your-backend:8000"
export WHARTTEST_API_KEY="your-api-key"

# 执行操作
python whart_tools.py --action <action_name> [--参数名 参数值]
```

## 可用操作

### 项目查询

| Action | 描述 | 参数 |
|--------|------|------|
| `get_projects` | 获取所有项目列表 | 无 |

### 文件管理

| Action | 描述 | 参数 |
|--------|------|------|
| `list_files` | 获取项目文件列表 | `--project_id`, `--page`, `--page_size`, `--search`, `--status`, `--extension`, `--mime_type`, `--ordering` |
| `get_file_detail` | 获取文件详情 | `--project_id`, `--file_id` |
| `upload_file` | 上传单个项目文件 | `--project_id`, `--file_path` |
| `upload_files` | 批量上传项目文件 | `--project_id`, `--file_paths`(逗号分隔) |
| `validate_files` | 校验 file_ids 是否存在、属于项目且状态可用 | `--project_id`, `--file_ids`(JSON数组或逗号分隔) |
| `get_file_references` | 获取文件引用详情 | `--project_id`, `--file_id` |
| `delete_file` | 删除项目文件；被引用文件由后端软删除 | `--project_id`, `--file_id` |
| `get_file_settings` | 获取项目文件管理设置 | `--project_id` |
| `update_file_settings` | 更新自动清理设置 | `--project_id`, `--auto_delete_on_unbind`, `--auto_delete_zero_refs` |
| `cleanup_unreferenced_files` | 立即清理无引用项目文件 | `--project_id` |
| `download_file` | 下载文件到本地 | `--project_id`, `--file_id`, `--output_path` 或 `--output_dir` |
| `preview_file` | 预览文件；文本直接返回，二进制可保存 | `--project_id`, `--file_id`, `--output_path`(可选) |

**文件 ID 约定**：上传文件后返回的 `id` / `file_id` 可传给接口自动化、UI 自动化或智能体对话中的 `file_ids` 字段。使用前可通过 `validate_files` 校验。

**设置布尔值**：`--auto_delete_on_unbind` 与 `--auto_delete_zero_refs` 使用 `true` / `false`。

## 能力边界

本工具只覆盖项目查询与项目文件管理。测试用例、用例模块、用例等级和测试截图相关操作在本系统中没有对应接口，请勿尝试调用。需求文档与评审数据也不在本工具范围内。

## 使用示例

```bash
# 获取项目列表
python whart_tools.py --action get_projects

# 上传项目文件
python whart_tools.py --action upload_file \
  --project_id 1 \
  --file_path "./需求说明.docx"

# 批量上传项目文件
python whart_tools.py --action upload_files \
  --project_id 1 \
  --file_paths "./a.docx,./b.pdf"

# 查询项目文件
python whart_tools.py --action list_files \
  --project_id 1 \
  --search "需求" \
  --page_size 20

# 查看文件详情与引用
python whart_tools.py --action get_file_detail --project_id 1 --file_id 12
python whart_tools.py --action get_file_references --project_id 1 --file_id 12

# 校验附件 file_ids
python whart_tools.py --action validate_files \
  --project_id 1 \
  --file_ids "12,13"

# 预览文件（文本直接返回）
python whart_tools.py --action preview_file --project_id 1 --file_id 12

# 下载项目文件
python whart_tools.py --action download_file \
  --project_id 1 \
  --file_id 12 \
  --output_dir "./downloads"

# 删除项目文件
python whart_tools.py --action delete_file --project_id 1 --file_id 12

# 更新文件清理设置
python whart_tools.py --action update_file_settings \
  --project_id 1 \
  --auto_delete_on_unbind true \
  --auto_delete_zero_refs false

# 立即清理无引用文件
python whart_tools.py --action cleanup_unreferenced_files --project_id 1
```

## 输出格式

所有操作返回 JSON 格式结果，便于解析处理。
