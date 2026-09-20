---
name: prototype-vision
description: 原型截图/识图需求整理工具。用于把视觉识图或原型 PDF 提取结果，按标准需求文档格式整理，并在写入需求文档前强制检索 loca 知识库作为前置条件。当用户提到原型截图、识图提取、图片型 PDF、Axure 导出、按知识库规范落库需求正文时使用。
---

# 原型识图需求整理

把「截图/原型 PDF 识图结果」整理成可拆分、可评审的需求正文。**未通过知识库检索前置条件，禁止保存。**

## 硬性前置条件（保存门槛）

保存前必须同时满足：

1. 已调用本技能 `query_knowledge`（或同会话内已拿到有效 `gate_token`）
2. 检索命中至少 1 条 `sources`（或有非空 `answer`）
3. 整理后的正文符合下方「原型需求格式」必填章节

任一不满足 → 只输出草稿，**不得**调用 `save_document_content`。

知识库用于两件事：

- **格式规范**：检索「需求文档模板 / 原型评审规范 / 模块拆分规范」等
- **业务依据**：检索与当前功能相关的已有说明，避免纯幻觉落库

## 快速开始

```bash
export LOCA_STUDE_BACKEND_URL="http://your-backend:8000"
export LOCA_STUDE_API_KEY="your-api-key"

python prototype_vision_tools.py --action <action_name> [参数...]
```

## 可用操作

| Action | 描述 | 参数 |
|--------|------|------|
| `list_knowledge_bases` | 列出项目下知识库 | `--project_id` |
| `query_knowledge` | 检索知识库（生成 `gate_token`） | `--knowledge_base_id`, `--query`, `--top_k`(可选) |
| `assemble_content` | 按模板组装识图结果 + 知识库命中 | `--vision_text` 或 `--vision_file`, `--kb_payload`(query 返回 JSON), `--title`(可选) |
| `save_document_content` | 写入需求文档正文（必须带 gate） | `--project_id`, `--document_id`, `--content` 或 `--content_file`, `--gate_token` |

## 推荐流程

```text
1. list_knowledge_bases --project_id <id>
2. query_knowledge --knowledge_base_id <kb> --query "需求文档模板 原型评审规范 <功能关键词>"
   → 记录返回的 gate_token 与 sources
3. assemble_content --vision_text "<识图原文>" --kb_payload '<query整包JSON>'
4. 人工/模型检查必填章节齐全后：
   save_document_content --project_id <id> --document_id <uuid> \
     --content_file assembled.md --gate_token <token>
```

平台侧「模块拆分」若对图片 PDF 走视觉识图，得到正文后仍应走本 Skill 的 **KB 前置校验 + 格式组装**，再进入拆分/评审。

## 原型需求格式（assemble 输出模板）

```markdown
# {标题}

## 1. 文档信息
- 来源：原型截图/识图
- 知识库依据：{命中文档标题列表}
- gate_token：{token 摘要}

## 2. 背景与目标
{...}

## 3. 范围
### 3.1 In Scope
### 3.2 Out of Scope

## 4. 用户与场景
{角色 / 主路径}

## 5. 功能说明（按页面/模块）
### 5.x {页面或模块名}
- 入口：
- 界面要素：
- 交互规则：
- 校验与提示：
- 权限：

## 6. 业务规则
- ...

## 7. 数据与状态
- 字段：
- 状态流转：

## 8. 异常与边界
- ...

## 9. 验收要点
- [ ] ...

## 10. 识图原文附录
{原始识图文本，按页保留}
```

必填章节：`背景与目标`、`功能说明`、`业务规则`、`验收要点`。缺任一节时 `assemble_content` 返回 `format_ok=false`，不得保存。

## 与其他 Skill 的配合

- 查已有需求/评审：`requirement-review`
- 外部 WeKnora 库：可先用 `weknora-kb` 补充检索，但**本 Skill 落库门槛仍以 loca `query_knowledge` 的 gate_token 为准**
- 上传截图到用例：`loca-stude` 的 `upload_screenshot`（与需求正文保存无关）

## 使用示例

```bash
# 1. 列知识库
python prototype_vision_tools.py --action list_knowledge_bases --project_id 1

# 2. 检索规范 + 业务关键词
python prototype_vision_tools.py --action query_knowledge \
  --knowledge_base_id <kb-uuid> \
  --query "商品中心 AI模型加价提醒 需求模板 原型规范"

# 3. 组装（kb_payload 为上一步完整 JSON）
python prototype_vision_tools.py --action assemble_content \
  --title "商品中心新增AI模型加价提醒" \
  --vision_file vision_raw.txt \
  --kb_payload_file kb_query.json

# 4. 保存（必须带 gate_token）
python prototype_vision_tools.py --action save_document_content \
  --project_id 1 \
  --document_id <doc-uuid> \
  --content_file assembled.md \
  --gate_token <gate_token>
```

## 能力边界

- 不负责渲染 PDF / 调用视觉模型（由需求模块拆分的识图回退或外部识图完成）
- 不发起模块拆分或评审
- 无知识库命中时只允许产出草稿，不允许 `save_document_content`

## 输出格式

所有操作返回 JSON。失败为 `{"error": "..."}` 并以非零码退出。
`query_knowledge` 成功时额外包含 `gate_token` 与 `gate_ok: true`。
