"""V1.1 正式验收场景 AC01–AC30 定义表（发布必测）。

本模块只声明场景元数据；可自动化断言在 test_formal_acceptance_ac.py。
真实 Callie 浏览器/支付链路标记为 e2e_manual，不计入自动化门禁。
"""
from __future__ import annotations

from typing import Any, Dict, List

AC_SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "AC01",
        "fr": ["FR01"],
        "trigger": "子进程以非零退出",
        "expect": "动作 failed，批次不计成功，错误码可查询",
        "automation": "unit",
    },
    {
        "id": "AC02",
        "fr": ["FR01"],
        "trigger": "HTTP 成功但业务拒绝",
        "expect": "动作 failed，保留业务原因，不盲目重试",
        "automation": "unit",
    },
    {
        "id": "AC03",
        "fr": ["FR02"],
        "trigger": "先创建再查询验证",
        "expect": "顺序不变，查询使用创建返回的 ID",
        "automation": "unit",
    },
    {
        "id": "AC04",
        "fr": ["FR02"],
        "trigger": "前置创建失败",
        "expect": "依赖步骤 skipped，独立分支按策略处理",
        "automation": "unit",
    },
    {
        "id": "AC05",
        "fr": ["FR02"],
        "trigger": "共享同一浏览器页面",
        "expect": "相关操作串行，无状态互相覆盖",
        "automation": "unit",
    },
    {
        "id": "AC06",
        "fr": ["FR03"],
        "trigger": "浏览器仅返回超时文本",
        "expect": "验收不通过，不能输出任务已完成",
        "automation": "unit",
    },
    {
        "id": "AC07",
        "fr": ["FR03"],
        "trigger": "测试有终态且测试失败",
        "expect": "执行任务可完成，业务失败被真实报告",
        "automation": "unit",
    },
    {
        "id": "AC08",
        "fr": ["FR04"],
        "trigger": "两个请求同时恢复",
        "expect": "仅一个获得执行权，另一个返回现有状态",
        "automation": "db",
    },
    {
        "id": "AC09",
        "fr": ["FR04", "FR06"],
        "trigger": "提交外部动作后进程崩溃",
        "expect": "恢复先核对原动作，不重复提交",
        "automation": "db",
    },
    {
        "id": "AC10",
        "fr": ["FR05"],
        "trigger": "跨用户或跨项目恢复审批",
        "expect": "拒绝请求，原任务及审批状态不被消费",
        "automation": "db",
    },
    {
        "id": "AC11",
        "fr": ["FR05"],
        "trigger": "重复批准或修改批准参数",
        "expect": "重复请求无重复写入，参数不一致被拒绝",
        "automation": "db",
    },
    {
        "id": "AC12",
        "fr": ["FR05"],
        "trigger": "用户已明确授权原目标",
        "expect": "范围内连续执行，无重复确认要求",
        "automation": "unit",
    },
    {
        "id": "AC13",
        "fr": ["FR06"],
        "trigger": "执行用例后等待超时",
        "expect": "查询原执行记录，不重新启动同一执行",
        "automation": "unit",
    },
    {
        "id": "AC14",
        "fr": ["FR07"],
        "trigger": "批次十动作及后续恢复",
        "expect": "逐动作计费，重试计费，恢复不重置预算",
        "automation": "db",
    },
    {
        "id": "AC15",
        "fr": ["FR07"],
        "trigger": "执行中点击停止",
        "expect": "阻止新步骤，反馈在途状态，确认停止后取消",
        "automation": "db",
    },
    {
        "id": "AC16",
        "fr": ["FR08"],
        "trigger": "查规则生成保存执行汇总",
        "expect": "阶段完整推进，合法工具不被单分区裁掉",
        "automation": "unit",
    },
    {
        "id": "AC17",
        "fr": ["FR08", "FR09"],
        "trigger": "摘要后说继续",
        "expect": "保留授权和产物 ID，只处理剩余步骤",
        "automation": "db",
    },
    {
        "id": "AC18",
        "fr": ["FR10"],
        "trigger": "刷新页面或 SSE 断线",
        "expect": "快照与事件恢复一致，无重复派发或假终态",
        "automation": "db",
    },
    {
        "id": "AC19",
        "fr": ["FR08.5"],
        "trigger": "执行意图使用不同自然表达",
        "expect": "无需执行关键词或工具名，授权与计划一致",
        "automation": "unit",
    },
    {
        "id": "AC20",
        "fr": ["FR08.6"],
        "trigger": "当前分区误裁浏览器工具",
        "expect": "有界纠正路由，不要求重发，不扩大授权",
        "automation": "unit",
    },
    {
        "id": "AC21",
        "fr": ["FR05.6"],
        "trigger": "仅订单分支需要澄清",
        "expect": "已授权且独立的上传和加购继续，仅询问剩余分支",
        "automation": "unit",
    },
    {
        "id": "AC22",
        "fr": ["FR05.5"],
        "trigger": "只授权加购且禁止支付",
        "expect": "不得提交订单，不能用不支付作为下单授权",
        "automation": "unit",
    },
    {
        "id": "AC23",
        "fr": ["FR04.5"],
        "trigger": "旧会话失效或旧文件缺失",
        "expect": "先探测，按权限恢复或报告资源缺口，不声称仍可用",
        "automation": "unit",
    },
    {
        "id": "AC24",
        "fr": ["FR03.6"],
        "trigger": "要求原生选择器但只设置 input",
        "expect": "记录实际方法，不宣称原生选择器验收通过",
        "automation": "unit",
    },
    {
        "id": "AC25",
        "fr": ["FR03.5"],
        "trigger": "计数变为1/1但预览未更新",
        "expect": "上传链未完全通过，继续核对或报告未完成项",
        "automation": "unit",
    },
    {
        "id": "AC26",
        "fr": ["FR03.5"],
        "trigger": "预览成功且购物车出现条目",
        "expect": "核对相同商品和素材，交付真实类型标识及来源",
        "automation": "unit",
    },
    {
        "id": "AC27",
        "fr": ["FR09.5"],
        "trigger": "分支域名不同于知识文档环境",
        "expect": "不直接断言不支持，核对原目标实际配置",
        "automation": "unit",
    },
    {
        "id": "AC28",
        "fr": ["FR01.5"],
        "trigger": "知识库出现订单号示例",
        "expect": "不得作为本次结果；cart和order字段分别标注",
        "automation": "unit",
    },
    {
        "id": "AC29",
        "fr": ["FR10.6"],
        "trigger": "加购成功但交付标识未捕获",
        "expect": "报告已完成步骤与证据，标识未知，不伪造完成",
        "automation": "unit",
    },
    {
        "id": "AC30",
        "fr": ["FR09.6"],
        "trigger": "其他商品属性为空或目标不可用",
        "expect": "不据此否定原商品，不静默换商品或环境",
        "automation": "unit",
    },
]


def ac_ids() -> List[str]:
    return [str(x["id"]) for x in AC_SCENARIOS]


def assert_matrix_complete() -> None:
    ids = ac_ids()
    assert len(ids) == 30, f"expected 30 scenarios, got {len(ids)}"
    assert ids == [f"AC{i:02d}" for i in range(1, 31)]
