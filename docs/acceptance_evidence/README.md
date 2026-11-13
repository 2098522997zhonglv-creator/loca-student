# Agent 验收证据包（文档 13.1 / 14）

本目录用于固化整改文档要求的可复核证据，**远端必验项 skip 不得计通过**。

## 目录约定

| 路径 | 用途 |
|------|------|
| `snapshot_manifest.json` | 由脚本生成：git HEAD、关键测试结果摘要、部署版本 |
| `rc_results.json` | RC01–RC24 本地/跳过状态 |
| `logs/` | 测试原始输出（可选粘贴） |
| `callie_skip_reasons.json` | RC18 缺失环境变量清单 |

## 生成方式

在仓库根目录：

```bash
/opt/miniconda3/envs/loca_test/bin/python scripts/generate_acceptance_evidence.py
```

## 放行提醒

- 所有 P0 关闭；适用 RC 无失败/错误
- Callie E2E / 真实远端幂等未跑通时，报告须写明 **未全面验收通过**
- 发布后用与截图相同环境重跑，并核对 `deploy.git_sha` / `config_schema_version`
