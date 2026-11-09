---
name: workorder-knowledge
description: 钉钉/Teambition 工单分析后的结构化知识沉淀工具。用于把工单采集与需求分析中已确认的业务规则，按产品线路径总结进 loca 知识库（标题形如 产品线/需求名/知识沉淀），替代「只同步钉钉原文、不做总结」的旧路径。当用户提供工单链接、要求从工单沉淀知识、更新产品线知识库或写知识沉淀时使用。
---

# 工单分析 → 知识库路径沉淀

从网翔测试用例工作流提炼的**新知识入库方式**：

```text
工单只读采集 → 需求分析定稿 → 知识沉淀条目 → 写入知识库（路径化标题）
```

与「钉钉文档原文整库同步」互补：同步保留证据原文；本 Skill 写入**可检索的已确认规则总结**。

## 安全边界（只读外部系统）

允许：打开工单/父需求、展开评论动态、只读打开关联钉钉文档/原型/Figma/技术方案。

禁止：评论、编辑、提交、上传、变更状态、代登录。

## 路径约定（映射到知识库文档标题）

| 角色 | 工作流文件路径 | loca 知识库文档标题 |
|------|----------------|---------------------|
| 工单采集 | `{产品线}/{需求名}/requirements/{需求名}-工单采集.md` | `{产品线}/{需求名}/工单采集` |
| 知识沉淀 | `{产品线}/{需求名}/requirements/{需求名}-知识沉淀.md` | `{产品线}/{需求名}/知识沉淀` |
| 产品线主库追加 | `{产品线}/PROJECT-KNOWLEDGE.md` 中的条目 | `{产品线}/PROJECT-KNOWLEDGE`（可选整库文档） |

产品线示例：`callie`、`商品中心`、`销售站`、`设计中心`、`支付中台`、`财务系统`、`供应链系统`、`support系统`。

## 快速开始

```bash
export LOCA_STUDE_BACKEND_URL="http://127.0.0.1:8000"
export LOCA_STUDE_API_KEY="your-api-key"

python workorder_knowledge_tools.py --action <action> [参数...]
```

## 可用操作

| Action | 描述 | 参数 |
|--------|------|------|
| `list_knowledge_bases` | 列出项目知识库 | `--project_id` |
| `render_ticket_collect` | 按模板生成工单采集 Markdown | `--product_line`, `--requirement_name`, `--payload` 或 `--payload_file` |
| `render_knowledge_extract` | 按模板生成知识沉淀 Markdown | `--product_line`, `--requirement_name`, `--payload` 或 `--payload_file` |
| `upsert_kb_document` | 写入/更新知识库文档（md） | `--knowledge_base_id`, `--title`, `--content` 或 `--content_file` |
| `upsert_knowledge_extract` | 渲染沉淀并写入 KB（标题=`产品线/需求名/知识沉淀`） | `--knowledge_base_id`, `--product_line`, `--requirement_name`, `--payload` 或 `--payload_file` |

## 推荐流程

1. 只读采集 Teambition/钉钉关联工单（可用浏览器 Skill），整理 JSON 字段。
2. `render_ticket_collect` → 得到 `{需求名}-工单采集.md`（可先落本地）。
3. 完成需求分析定稿后，整理已确认规则 JSON。
4. `upsert_knowledge_extract` 写入知识库；检索时用「产品线 + 需求名 + 规则关键词」。
5. 同一需求重复沉淀时更新原文档（同标题 upsert），注明修订日期，不重复堆积。

## 沉淀规则

- 只写可确认事实；推测进「待确认项」。
- 冲突保留双方来源，标注待确认，不直接覆盖。
- 跨需求通用规则可另写 `{产品线}/全局规则/...` 标题。
- 钉钉原文仍可通过平台「钉钉同步」入库；本 Skill 不替代原文同步。

## 参考模板

- [ticket-collect-template.md](references/ticket-collect-template.md)
- [knowledge-extract-template.md](references/knowledge-extract-template.md)

## payload 示例（知识沉淀）

```json
{
  "date": "2026-11-09",
  "sources": ["工单采集", "钉钉文档链接"],
  "business_background": ["…"],
  "roles_systems": ["商品中心后台 / 运营"],
  "confirmed_rules": ["加价仅对指定模型生效", "…"],
  "fields": [
    {"name": "模型价格", "purpose": "展示", "rule": "选中后显示", "relation": "AI模型"}
  ],
  "status_flow": ["草稿 → 生效"],
  "config_permission": ["仅管理员可改价"],
  "test_risks": ["权限绕过", "价格未刷新"],
  "open_questions": ["是否含税"],
  "evidence": ["SRC-TB-001"]
}
```
