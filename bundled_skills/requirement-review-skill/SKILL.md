---
name: requirement-review
description: 需求评审数据查询工具集。用于查询项目下的需求文档列表与正文、模块拆分结果、评审报告评分与结论、评审发现的问题清单。当用户问“有哪些需求”“需求文档写了什么”“评审结果怎么样”“评审发现了什么问题”“某个模块评得如何”时使用。只读，不能上传文档或发起评审。
---

# 需求评审数据查询

需求文档与项目文件附件是两套独立的数据。本工具查的是「需求评审」模块的数据；项目文件附件请用 `loca-stude` 的 `list_files`。

## 快速开始

```bash
python requirement_tools.py --action <action_name> --project_id <项目ID> [其他参数]
```

**`--project_id` 是所有操作的必填参数**，缺失或不匹配会返回 403。项目 ID 是整数，可用 `loca-stude` 的 `get_projects` 查到。文档、报告、模块的 ID 都是 UUID。

## 可用操作

| Action | 描述 | 参数 |
|--------|------|------|
| `list_documents` | 列出需求文档（不含正文） | `--project_id`, `--status`(可选), `--search`(可选), `--page`, `--page_size` |
| `get_document` | 获取文档详情与正文 | `--project_id`, `--document_id`, `--max_chars`(可选) |
| `list_modules` | 列出文档的模块拆分结果 | `--project_id`, `--document_id` |
| `get_report` | 获取评审报告概要与各维度评分 | `--project_id`, `--document_id` 或 `--report_id` |
| `list_issues` | 列出评审发现的问题 | `--project_id`, `--document_id` 或 `--report_id`, `--priority`(可选), `--issue_type`(可选), `--is_resolved`(可选) |
| `list_module_results` | 列出各模块的评审结果 | `--project_id`, `--document_id` 或 `--report_id` |

`get_report`、`list_issues`、`list_module_results` 传 `--document_id` 时会自动取该文档**最近一次**的评审报告；要查历史报告则显式传 `--report_id`。

## 典型流程

查“这个项目有什么需求” → `list_documents` 拿到 `document_id` → 按需要 `get_document` 看正文、`get_report` 看结论、`list_issues` 看问题。

## 取值说明

**文档状态 `--status`**：`uploaded`(已上传) / `processing`(处理中) / `module_split`(模块拆分中) / `user_reviewing`(用户调整中) / `ready_for_review`(待评审) / `reviewing`(评审中) / `review_completed`(评审完成) / `failed`(处理失败)

**问题优先级 `--priority`**：`high` / `medium` / `low`（注意字段名是 priority，不是 severity）

**问题类型 `--issue_type`**：`specification`(规范性) / `clarity`(清晰度) / `completeness`(完整性) / `consistency`(一致性) / `feasibility`(可行性) / `logic`(逻辑性)

**报告状态**：`pending`(待开始) / `in_progress`(评审中) / `completed`(已完成) / `failed`(评审失败)。报告状态与文档状态取值不同，别混用。

**总体评级**：`excellent` / `good` / `average` / `needs_improvement` / `poor`

`get_report` 的 `scores` 含六个维度：`completeness`(完整性) / `consistency`(一致性) / `testability`(可测性) / `feasibility`(可行性) / `clarity`(清晰度) / `logic`(逻辑性)。`progress` 是 0~1 的小数，不是百分比。

## 关于正文长度

`get_document` 的正文默认截断到 4000 字符，超出会标注全文长度。需要更多内容时调大 `--max_chars`，传 `0` 表示不截断——需求文档常有上万字，不截断可能塞满上下文，请按需取用。

## 使用示例

```bash
# 列出项目 1 的所有需求文档
python requirement_tools.py --action list_documents --project_id 1

# 只看评审完成的
python requirement_tools.py --action list_documents --project_id 1 --status review_completed

# 按关键词搜索
python requirement_tools.py --action list_documents --project_id 1 --search 素材库

# 看文档正文（默认截断 4000 字）
python requirement_tools.py --action get_document --project_id 1 --document_id <uuid>

# 看完整正文
python requirement_tools.py --action get_document --project_id 1 --document_id <uuid> --max_chars 0

# 看模块拆分
python requirement_tools.py --action list_modules --project_id 1 --document_id <uuid>

# 看评审报告结论与评分
python requirement_tools.py --action get_report --project_id 1 --document_id <uuid>

# 看全部问题
python requirement_tools.py --action list_issues --project_id 1 --document_id <uuid>

# 只看高优先级且未解决的问题
python requirement_tools.py --action list_issues --project_id 1 --document_id <uuid> \
  --priority high --is_resolved false

# 看各模块评审结果
python requirement_tools.py --action list_module_results --project_id 1 --document_id <uuid>
```

## 能力边界

只读。上传需求文档、发起或重启评审、修改模块拆分都不在本工具范围内，请在「需求评审」页面操作。

## 输出格式

所有操作返回 JSON。失败时返回 `{"error": "..."}` 并以非零码退出。
