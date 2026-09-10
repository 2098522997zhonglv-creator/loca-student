"""
提示词服务模块

统一管理提示词初始化逻辑，默认模板按语言拆分到 default_templates 包。
"""
import logging
from typing import List, Dict
from .default_templates import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, get_templates
from .models import UserPrompt, PromptType

logger = logging.getLogger(__name__)


def get_default_prompts(language: str = DEFAULT_LANGUAGE) -> List[Dict]:
    """根据语言返回默认提示词模板列表。未知语言回退到中文。"""
    return get_templates(language)


PROGRAM_CALL_PROMPT_TYPES = {
    PromptType.COMPLETENESS_ANALYSIS,
    PromptType.CONSISTENCY_ANALYSIS,
    PromptType.TESTABILITY_ANALYSIS,
    PromptType.FEASIBILITY_ANALYSIS,
    PromptType.CLARITY_ANALYSIS,
    PromptType.LOGIC_ANALYSIS,
    PromptType.TEST_CASE_EXECUTION,
    PromptType.DIAGRAM_GENERATION,
}


def _other_language_general_names(current_language: str) -> set:
    """收集除 current_language 外其他语言的通用类型默认提示词名称。

    用于 force_update 切换语言时，清理用户之前用其他语言初始化出的通用类型提示词，
    避免出现"中英文各一条同义默认提示词并存"。
    """
    names: set = set()
    for lang in SUPPORTED_LANGUAGES:
        if lang == current_language:
            continue
        for prompt in get_templates(lang):
            if prompt['prompt_type'] not in PROGRAM_CALL_PROMPT_TYPES:
                names.add(prompt['name'])
    return names


def initialize_user_prompts(user, force_update: bool = False, language: str = DEFAULT_LANGUAGE) -> dict:
    """初始化用户的默认提示词

    Args:
        user: Django User对象
        force_update: 是否强制更新已存在的提示词
        language: 提示词语言 ('zh' 或 'en')，未知语言回退到中文

    Returns:
        dict: 初始化结果，包含 created, skipped, summary, language
    """
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE

    result = {
        'created': [],
        'skipped': [],
        'deleted': [],
        'language': language,
        'summary': {
            'created_count': 0,
            'skipped_count': 0,
            'deleted_count': 0,
        }
    }

    # 强制更新模式下，清理其他语言遗留的通用类型默认提示词（按已知模板名匹配）。
    if force_update:
        other_names = _other_language_general_names(language)
        if other_names:
            stale_qs = UserPrompt.objects.filter(user=user, name__in=other_names)
            stale_names = list(stale_qs.values_list('name', flat=True))
            deleted_count, _ = stale_qs.delete()
            if deleted_count:
                result['deleted'] = stale_names
                result['summary']['deleted_count'] = deleted_count

    default_prompts = get_default_prompts(language)

    for prompt_data in default_prompts:
        prompt_type = prompt_data['prompt_type']

        # 程序调用类型按 prompt_type 检查唯一性（每用户每类型只能有一个）
        # 通用对话类型按名称检查唯一性（可以有多个，但名称不能重复）
        if prompt_type in PROGRAM_CALL_PROMPT_TYPES:
            existing_prompt = UserPrompt.objects.filter(
                user=user,
                prompt_type=prompt_type
            ).first()
        else:
            # 通用对话类型，按名称检查
            existing_prompt = UserPrompt.objects.filter(
                user=user,
                name=prompt_data['name']
            ).first()
        
        if existing_prompt and not force_update:
            result['skipped'].append({
                'name': prompt_data['name'],
                'prompt_type': prompt_type,
                'reason': '已存在'
            })
            result['summary']['skipped_count'] += 1
            continue
        
        if existing_prompt and force_update:
            # 强制更新模式：更新现有提示词
            existing_prompt.name = prompt_data['name']
            existing_prompt.content = prompt_data['content']
            existing_prompt.description = prompt_data['description']
            existing_prompt.is_default = prompt_data.get('is_default', False)
            existing_prompt.save()
            result['created'].append({
                'name': prompt_data['name'],
                'prompt_type': prompt_type,
                'action': 'updated'
            })
            result['summary']['created_count'] += 1
        else:
            # 创建新提示词
            UserPrompt.objects.create(
                user=user,
                name=prompt_data['name'],
                content=prompt_data['content'],
                description=prompt_data['description'],
                prompt_type=prompt_type,
                is_default=prompt_data.get('is_default', False),
                is_active=True
            )
            result['created'].append({
                'name': prompt_data['name'],
                'prompt_type': prompt_type,
                'action': 'created'
            })
            result['summary']['created_count'] += 1

    return result


# 测试类型对应的提示词片段
TEST_TYPE_PROMPTS = {
    'smoke': '''【测试类型：冒烟测试】
- 目标：生成最小化用例，仅验证核心主流程可用性
- 要求：每个功能点最多1-2条用例，覆盖最基本的正向场景
- 原则：快速验证系统基本功能是否正常，不深入边界和异常场景''',

    'functional': '''【测试类型：功能测试】
- 目标：使用等价类划分技术，全面验证功能正确性
- 要求：覆盖有效等价类和无效等价类，每类至少1条用例
- 原则：确保正向场景完整，主要功能路径全覆盖''',

    'boundary': '''【测试类型：边界测试】
- 目标：使用边界值分析技术，验证边界条件处理
- 要求：测试边界值、边界值+1、边界值-1、典型值
- 原则：关注数值范围、字符长度、日期时间等边界条件''',

    'exception': '''【测试类型：异常测试】
- 目标：使用错误推测法，验证系统异常处理能力
- 要求：覆盖异常输入、网络异常、数据异常、并发冲突等场景
- 原则：验证错误提示友好性、系统稳定性、数据完整性''',

    'permission': '''【测试类型：权限测试】
- 目标：验证系统访问控制机制
- 要求：识别角色矩阵，测试有权限/无权限/越权场景
- 原则：验证功能入口隐藏、操作权限拒绝、数据隔离正确''',

    'security': '''【测试类型：安全测试】
- 目标：验证系统安全防护机制
- 要求：关注OWASP Top 10，测试XSS/SQL注入防护、敏感数据保护
- 原则：验证输入校验、输出编码、敏感信息脱敏、会话管理''',

    'compatibility': '''【测试类型：兼容性测试】
- 目标：验证系统在不同环境下的兼容性
- 要求：从需求中提取目标设备/浏览器列表，为每个环境生成独立用例
- 原则：验证页面显示、交互操作、功能完整性在各环境下一致''',
}


def get_test_type_prompt(test_types: list) -> str:
    """根据测试类型列表生成对应的提示词片段

    Args:
        test_types: 测试类型标识列表，如 ['functional', 'boundary']

    Returns:
        str: 组合后的测试类型提示词
    """
    if not test_types:
        return TEST_TYPE_PROMPTS.get('functional', '')

    prompts = []
    for test_type in test_types:
        if test_type in TEST_TYPE_PROMPTS:
            prompts.append(TEST_TYPE_PROMPTS[test_type])

    if not prompts:
        return TEST_TYPE_PROMPTS.get('functional', '')

    return '\n\n'.join(prompts)
