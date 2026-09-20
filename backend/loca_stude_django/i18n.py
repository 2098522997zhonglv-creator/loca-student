import re
from collections.abc import Mapping, Sequence
from typing import Any

from django.utils.translation import get_language


def normalize_language_code(language: str | None = None) -> str:
    return (language or get_language() or '').lower().replace('_', '-')


def is_english_language(language: str | None = None) -> bool:
    return normalize_language_code(language).startswith('en')


def _preserve_whitespace(original: str, translated: str) -> str:
    leading = re.match(r'^\s*', original)
    trailing = re.search(r'\s*$', original)
    return f"{leading.group(0) if leading else ''}{translated}{trailing.group(0) if trailing else ''}"


EXACT_EN_MESSAGES: dict[str, str] = {
    '操作成功': 'Request succeeded',
    '操作成功完成': 'Operation completed successfully',
    '删除操作成功完成': 'Delete completed successfully',
    '请求参数有误或处理失败': 'Request validation failed or processing failed',
    '请求处理失败，请查看错误详情': 'Request failed. Check the error details.',
    '请求处理失败': 'Request processing failed',
    '发生未知错误': 'An unknown error occurred',
    '认证失败': 'Authentication failed',
    '账号或密码错误': 'Invalid username or password',
    '认证服务正在启动，请稍后重试。': 'The authentication service is starting. Please try again shortly.',
    '没有权限访问该资源': 'You do not have permission to access this resource',
    '请求的资源不存在': 'The requested resource does not exist',
    '服务器内部错误': 'Internal server error',
    '服务器无响应，请检查网络连接': 'The server did not respond. Check your network connection.',
    '解析错误响应失败': 'Failed to parse the error response',
    '登录已过期，请重新登录': 'Session expired. Please sign in again.',
    '未登录或会话已过期': 'Not signed in or the session has expired',
    '未登录或登录已过期': 'Not signed in or the session has expired',
    '请求处理时发生错误，具体原因未知。': 'An unknown error occurred while processing the request.',
    '成员已成功移除': 'Member removed successfully',
    '必须提供用户ID': 'A user ID is required',
    '不能移除项目拥有者': 'The project owner cannot be removed',
    '不能移除自己': 'You cannot remove yourself',
    '必须提供用户ID和角色': 'A user ID and role are required',
    '只有项目拥有者或超级管理员可以修改拥有者角色': 'Only the project owner or a super administrator can change the owner role',
    '不能修改自己的角色': 'You cannot change your own role',
    '无效的角色，可选值为:': 'Invalid role. Allowed values:',
    '未提供文件': 'No file provided',
    '未提供用例 ID': 'No test case ID was provided',
    'project 参数必填': 'The project parameter is required',
    'page_step 参数必填': 'The page_step parameter is required',
    'test_case 参数必填': 'The test_case parameter is required',
    '图片不存在': 'Image not found',
    '文档不存在': 'Document not found',
    '该文档没有原始文件': 'The document has no original file',
    '未上传文件': 'No file uploaded',
    '请选择嵌入服务': 'Select an embedding service',
    '请输入模型名称': 'Enter a model name',
    'Web客户端连接成功': 'Web client connected successfully',
    '执行器连接成功': 'Actuator connected successfully',
    '任务已发送给执行器': 'Task sent to actuator',
    '没有选择要执行的用例': 'No cases selected for execution',
    '创建批量执行记录失败': 'Failed to create the batch execution record',
    '批量任务已发送给执行器': 'Batch task sent to actuator',
    '停止信号已发送': 'Stop signal sent',
}

REGEX_EN_MESSAGES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'^消息格式错误: (.+)$'), 'Invalid message format: {0}'),
    (re.compile(r'^处理错误: (.+)$'), 'Processing error: {0}'),
    (re.compile(r'^未知的操作: (.+)$'), 'Unknown action: {0}'),
    (re.compile(r'^执行器 (.+) 不在线$'), 'Actuator {0} is offline'),
    (
        re.compile(r'^没有可用的执行器，请先启动执行器服务$'),
        'No actuator is available. Start the actuator service first.',
    ),
]


def translate_app_text(value: str, language: str | None = None) -> str:
    if not isinstance(value, str) or not is_english_language(language):
        return value

    stripped = value.strip()
    if not stripped:
        return value

    if stripped in EXACT_EN_MESSAGES:
        return _preserve_whitespace(value, EXACT_EN_MESSAGES[stripped])

    for pattern, replacement in REGEX_EN_MESSAGES:
        match = pattern.match(stripped)
        if match:
            return _preserve_whitespace(value, replacement.format(*match.groups()))

    return value


def translate_error_payload(payload: Any, language: str | None = None) -> Any:
    if not is_english_language(language):
        return payload

    if isinstance(payload, str):
        return translate_app_text(payload, language)

    if isinstance(payload, Mapping):
        return {
            key: translate_error_payload(value, language)
            for key, value in payload.items()
        }

    if isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray, str)):
        return [translate_error_payload(item, language) for item in payload]

    return payload
