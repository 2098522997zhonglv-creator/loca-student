"""默认提示词模板按语言拆分到独立模块，方便维护。"""

from typing import Dict, List

from . import en as _en
from . import zh as _zh

SUPPORTED_LANGUAGES = ('zh', 'en')
DEFAULT_LANGUAGE = 'zh'


def get_templates(language: str = DEFAULT_LANGUAGE) -> List[Dict]:
    """根据语言返回对应的默认提示词模板列表。未知语言回退到中文。"""
    if language == 'en':
        return _en.DEFAULT_PROMPTS
    return _zh.DEFAULT_PROMPTS
