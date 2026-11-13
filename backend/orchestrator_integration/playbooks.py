"""高频确定性剧本：命中后注入强制步骤，减少模型自由选工具。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from orchestrator_integration.intent_router import (
    INTENT_PLATFORM,
    INTENT_WEB,
    INTENT_WRITE,
    IntentDecision,
    KbAccessPolicy,
    is_testcase_execute_intent,
)
from orchestrator_integration.skill_zones import (
    ZONE_BROWSER,
    ZONE_EXECUTE,
    ZONE_PLATFORM,
    ZONE_TICKET_KB,
    ZONE_WEB,
    SkillZone,
)

PLAYBOOK_TICKET_KB = "ticket_kb_upsert"
PLAYBOOK_TESTCASE_EXECUTE = "testcase_execute"
PLAYBOOK_URL_READ = "url_read"
PLAYBOOK_BROWSER_ACT = "browser_act"
PLAYBOOK_PLATFORM_WRITE = "platform_write"
PLAYBOOK_PLATFORM_READ = "platform_read"


@dataclass(frozen=True)
class Playbook:
    playbook_id: str
    title: str
    steps: Sequence[str]
    skip: Sequence[str]
    preferred_skills: Sequence[str]


def resolve_playbook(
    message: str,
    zone: SkillZone,
    decision: IntentDecision,
    kb_policy: Optional[KbAccessPolicy] = None,
) -> Optional[Playbook]:
    text = (message or "").strip()

    if zone.zone_id == ZONE_TICKET_KB or (
        kb_policy and "ticket" in (kb_policy.reason or "")
    ):
        want_upsert = any(
            h in text
            for h in (
                "沉淀到知识库",
                "写入知识库",
                "知识沉淀",
                "同步到知识库",
                "upsert_knowledge",
            )
        )
        if want_upsert:
            return Playbook(
                PLAYBOOK_TICKET_KB,
                "工单知识沉淀入库",
                steps=(
                    "禁止 url-reader / knowledge_search（Teambition 是登录态 SPA）",
                    "read_skill_content(workorder-knowledge)",
                    "用 playwright-skill 只读打开工单并采集字段",
                    "调用写入后由服务端弹出「确认写入」按钮；简述摘要即可，勿要求用户打字",
                    "用户点允许后 resume 再 execute_skill_script(…, user_confirmed=true)",
                ),
                skip=("url-reader", "knowledge_search", "无关 ui-automation"),
                preferred_skills=(
                    "workorder-knowledge",
                    "playwright-skill",
                    "loca-stude",
                ),
            )
        return Playbook(
            PLAYBOOK_TICKET_KB,
            "查看/采集工单",
            steps=(
                "禁止 url-reader / knowledge_search（Teambition/钉钉工单是登录态 SPA）",
                "read_skill_content(workorder-knowledge)",
                "用 playwright-skill 打开工单链接，只读采集标题/描述/评论/关联文档",
                "汇总工单信息给用户；未要求沉淀时不要 upsert 知识库",
            ),
            skip=("url-reader", "knowledge_search", "未确认的 upsert"),
            preferred_skills=("workorder-knowledge", "playwright-skill"),
        )

    if zone.zone_id == ZONE_EXECUTE or (
        kb_policy and kb_policy.reason == "testcase_execute"
    ) or is_testcase_execute_intent(text, decision):
        return Playbook(
            PLAYBOOK_TESTCASE_EXECUTE,
            "执行测试用例/自动化",
            steps=(
                "禁止 knowledge_search / 知识库预检索与精排",
                "同会话若已读过 ui-automation：直接 execute，不要再 read_skill_content",
                "一条命令：python ui_automation_tools.py --action execute_testcase --testcase_id <N> --wait_result",
                "成功后直接汇总结果；禁止紧跟 get_execution_records / create_ui_* / 知识沉淀",
                "若用户还要求生成 UI 自动化：先汇报执行结果；写入由按钮确认，勿中途问「下一步」",
            ),
            skip=(
                "knowledge_search",
                "workorder-knowledge",
                "url-reader",
                "未确认的 create_ui_*/set_case_steps",
            ),
            preferred_skills=("ui-automation", "loca-stude", "api-automation"),
        )

    if zone.zone_id == ZONE_BROWSER:
        return Playbook(
            PLAYBOOK_BROWSER_ACT,
            "浏览器交互操作",
            steps=(
                "先用知识库预检索/knowledge_search（最多 2 次）拿到入口 URL；禁止本机扫盘/读日志猜地址；入口已定则禁止再查库",
                "禁止 url-reader；禁止用知识库结论代替真实页面结果",
                "read_skill_content(playwright-skill)（同会话已读过可跳过）",
                "用户要「所见即所得/传图」时：必须选带 Upload Photo 的商品；禁止改做刻字/戒指/仅填名字的品交差",
                "execute_skill_script：打开已取证目标站 → 传图（须有上传请求或预览非占位）→ 加购；交付真实 cart_id 等",
                "上传：filechooser 优先，失败再 setInputFiles；计数器停在 0/1 或仍 upload.svg 视为失败，不得宣称加购成功",
                "同一失败（上传/加购）至多换策略 2 次；仍失败则 report_task_blocked 说明阻塞，禁止换品类凑 cart_id",
                "只授权加购时不得提交订单；支付/写库才走确认；知识库示例编号不得充当本次结果",
            ),
            skip=("url-reader", "本机考古", "未确认的平台写入", "非目标品类交差"),
            preferred_skills=("playwright-skill", "ui-automation"),
        )

    if zone.zone_id == ZONE_WEB or (
        INTENT_WEB in decision.intents and zone.zone_id != ZONE_BROWSER
    ):
        return Playbook(
            PLAYBOOK_URL_READ,
            "读取网页",
            steps=(
                "只使用 url-reader",
                "read_skill_content(url-reader)",
                'execute_skill_script(skill_name=url-reader, command=python scripts/read_url.py "URL")',
                "失败时把错误原文告知用户；不要改调 playwright/browser-use",
            ),
            skip=("playwright-skill", "browser-use", "playwright-cli"),
            preferred_skills=("url-reader",),
        )

    if zone.zone_id == ZONE_PLATFORM and INTENT_WRITE in decision.intents:
        return Playbook(
            PLAYBOOK_PLATFORM_WRITE,
            "平台写入/落库",
            steps=(
                "read_skill_content(loca-stude)",
                "必要时只读 get_/list_ 查询后直接尝试写入（未确认会被服务端拦截并弹按钮）",
                "简述待写入字段后结束本轮；勿要求用户打字确认",
                "用户点「确认写入」后 resume：一条 commands 串行写入（parallel=false, user_confirmed=true）",
            ),
            skip=("并行写入", "跨区浏览器"),
            preferred_skills=("loca-stude", "ui-automation", "api-automation"),
        )

    if zone.zone_id == ZONE_PLATFORM and INTENT_PLATFORM in decision.intents:
        return Playbook(
            PLAYBOOK_PLATFORM_READ,
            "平台只读查询",
            steps=(
                "read_skill_content(loca-stude)",
                "execute_skill_script 执行 get_/list_ 只读命令",
                "汇总真实查询结果；不要顺手写入",
            ),
            skip=("写入 action",),
            preferred_skills=("loca-stude",),
        )

    return None


def playbook_hint(playbook: Optional[Playbook]) -> str:
    if not playbook:
        return ""
    lines = [
        f"# 本轮剧本（服务端强制）: {playbook.playbook_id} — {playbook.title}",
        "## 必须按序执行",
    ]
    for i, step in enumerate(playbook.steps, 1):
        lines.append(f"{i}. {step}")
    if playbook.preferred_skills:
        lines.append(
            "- 优先 Skill: " + ", ".join(playbook.preferred_skills)
        )
    if playbook.skip:
        lines.append("- 禁止/跳过: " + ", ".join(playbook.skip))
    lines.append("- 不要偏离剧本自由探索其它 Skill。")
    return "\n".join(lines)
