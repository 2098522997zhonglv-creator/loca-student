"""
Skill 工具

提供渐进式加载的 Skill 系统：
- read_skill_content: 读取 Skill 的 SKILL.md 内容（按需加载）
- execute_skill_script: 执行 Skill 的 shell 命令（支持持久化浏览器会话）
"""

import logging
import subprocess
import os
import shutil
import sys
import threading
import json
import time
import mimetypes
import re
from typing import Optional, Union

from langchain_core.tools import tool as langchain_tool
from django.conf import settings

from .output_sanitizer import strip_terminal_control_sequences
from .persistent_playwright import PlaywrightSessionManager, extract_runjs_args

logger = logging.getLogger("orchestrator_integration")

_playwright_session_manager: Optional[PlaywrightSessionManager] = None
_playwright_session_manager_lock = threading.Lock()


class SkillExecutionCancelled(Exception):
    """用户停止信号打断子进程（FR07.3）。"""


def _current_stop_session_id() -> str:
    try:
        from orchestrator_integration.telemetry_context import get_telemetry_context

        return (get_telemetry_context().session_id or "").strip()
    except Exception:
        return ""


def _should_stop_execution(session_id: str = "") -> bool:
    try:
        from orchestrator_integration.stop_signal import should_stop

        sid = session_id or _current_stop_session_id()
        return bool(sid and should_stop(sid))
    except Exception:
        return False


def _run_shell_cancellable(
    exec_command: str,
    *,
    cwd: str,
    env: dict,
    timeout: int = 120,
    text_mode: bool = True,
    session_id: str = "",
):
    """可取消的 shell 执行：并发读管道 + 轮询停止信号（FR07.3 / R22）。"""
    import signal
    import threading

    sid = session_id or _current_stop_session_id()
    if _should_stop_execution(sid):
        raise SkillExecutionCancelled("任务已停止，拒绝启动新的工具动作")

    popen_kwargs = {
        "args": exec_command,
        "shell": True,
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "start_new_session": True,
    }
    if text_mode:
        popen_kwargs.update({"text": True, "encoding": "utf-8", "errors": "replace"})

    proc = subprocess.Popen(**popen_kwargs)
    deadline = time.time() + max(1, int(timeout))
    chunks_out: list = []
    chunks_err: list = []

    def _drain(stream, bucket):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                bucket.append(chunk)
        except Exception:
            pass

    readers = []
    if proc.stdout is not None:
        t = threading.Thread(target=_drain, args=(proc.stdout, chunks_out), daemon=True)
        t.start()
        readers.append(t)
    if proc.stderr is not None:
        t = threading.Thread(target=_drain, args=(proc.stderr, chunks_err), daemon=True)
        t.start()
        readers.append(t)

    def _join_output():
        for t in readers:
            t.join(timeout=1)
        if text_mode:
            return "".join(chunks_out), "".join(chunks_err)
        return b"".join(chunks_out), b"".join(chunks_err)

    def _kill_group(sig=signal.SIGTERM):
        try:
            os.killpg(proc.pid, sig)
        except Exception:
            if sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()

    try:
        while True:
            if _should_stop_execution(sid):
                _kill_group(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    _kill_group(signal.SIGKILL)
                _join_output()
                raise SkillExecutionCancelled("任务已停止，子进程已终止")
            rc = proc.poll()
            if rc is not None:
                stdout, stderr = _join_output()
                return subprocess.CompletedProcess(
                    args=exec_command, returncode=rc, stdout=stdout, stderr=stderr
                )
            if time.time() >= deadline:
                _kill_group(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    _kill_group(signal.SIGKILL)
                _join_output()
                raise subprocess.TimeoutExpired(exec_command, timeout)
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
            _join_output()
_ARTIFACT_EXTENSIONS = {
    ".drawio",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".pdf",
    ".html",
    ".htm",
    ".txt",
    ".json",
    ".csv",
    ".xml",
    ".zip",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
}
_MAX_ARTIFACT_SIZE_BYTES = 50 * 1024 * 1024
_ARTIFACT_TOKEN_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[^\s<>\"'`|]+?\.(?:drawio|png|jpe?g|gif|svg|pdf|html?|txt|json|csv|xml|zip|docx?|xlsx?|pptx?))",
    re.IGNORECASE,
)
_QUOTED_ARTIFACT_TOKEN_RE = re.compile(
    r"[`'\"](?P<path>[^`'\"]+?\.(?:drawio|png|jpe?g|gif|svg|pdf|html?|txt|json|csv|xml|zip|docx?|xlsx?|pptx?))[`'\"]",
    re.IGNORECASE,
)

# --action 采用只读白名单：不在白名单里的 action 一律视为写入，避免 edit_/approve_ 等新动词漏网。
# execute_/run_ 触发用例执行，不改用例数据，放行。
_ACTION_RE = re.compile(r"(?i)--action[\s=]+['\"]?(?P<action>[\w-]+)")
_READONLY_ACTION_RE = re.compile(
    r"(?i)^(?:get|list|search|query|find|read|view|show|count|describe|detail|check|fetch"
    r"|download|export|preview|validate|help|stat|stats|execute|run)(?:[_-]|$)"
)
# 无 --action 的脚本：只识别明显的写入函数名
_MUTATING_FUNC_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:batch_)?(?:add|create|update|edit|delete|save|insert|remove|upsert)_[\w-]+"
)

_NO_ASK_APPROVAL_RE = re.compile(r"(不用|无需|不需要|不要|别)(再)?(问我|确认|询问)")
# 「确认执行用例」不算同意写入；写入须出现写入/保存/创建等语义
_EXPLICIT_WRITE_APPROVAL_RE = re.compile(
    r"(确认|同意|允许|可以|直接)(写入|入库|落库|保存|创建|提交|执行写入)"
    r"|直接(写|存|建|保存|入库)"
    r"|(继续|接着|剩下的?|其余的?)(也|都)?(更新|修改|改|编辑|写入|保存|创建|添加|新增|处理)"
)
# 肯定词后须紧跟标点/空白/结尾，避免“可以帮我执行用例35吗”被当成同意
_SHORT_APPROVAL_RE = re.compile(
    r"^\s*(?:(?:确认|同意|可以|好的?|好滴|行|是的?|对的?|没问题|ok|okay|yes|y)(?:$|[\s。.!！~，,、;；])"
    r"|(?:执行|写入|保存|继续|开始|写)吧?\s*[。.!！~]*\s*$)",
    re.IGNORECASE,
)
_SHORT_APPROVAL_MAX_CHARS = 20
_WRITE_REJECT_RE = re.compile(r"不要|不用了|别写|先不|暂不|取消|不同意|不确认|不可以|不行|算了")


def detect_user_write_approval(message: Optional[str]) -> bool:
    """按用户原话判定本轮是否明确同意写入（≠ 同意执行用例）。"""
    text = (message or "").strip()
    if not text:
        return False
    if _NO_ASK_APPROVAL_RE.search(text):
        return True
    if _WRITE_REJECT_RE.search(text):
        return False
    # 执行用例意图且未说写入/保存/创建：不算写入授权（防执行区越权）
    # 用关键字判定，避免与 classify→detect_user_write_approval 递归
    from orchestrator_integration.intent_router import (
        looks_like_testcase_execute_keywords,
    )
    from orchestrator_integration.skill_governance import (
        message_has_explicit_write_intent,
    )

    if looks_like_testcase_execute_keywords(text) and not message_has_explicit_write_intent(
        text
    ):
        return False
    if _EXPLICIT_WRITE_APPROVAL_RE.search(text):
        return True
    return len(text) <= _SHORT_APPROVAL_MAX_CHARS and bool(_SHORT_APPROVAL_RE.match(text))


def normalize_commands_arg(raw: str) -> list[dict[str, str]]:
    """把 JSON 字符串形式的 commands 解析为列表；单个对象包装成单元素列表。"""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"commands 必须是列表（JSON 数组），解析失败: {exc}。"
            '正确示例: commands=[{"skill_name": "loca-stude", "command": "python ..."}]'
        ) from exc
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not all(isinstance(c, dict) for c in data):
        raise ValueError("commands 必须是由 {skill_name, command} 对象组成的列表")
    return data


def _write_allowed(user_confirmed: bool, user_write_approved: Optional[bool]) -> bool:
    """user_write_approved 为 None 时（无用户原话可判定）沿用模型的 user_confirmed；
    否则只认服务端按用户原话的判定，模型无法自行放行。"""
    if user_write_approved is None:
        return bool(user_confirmed)
    return user_write_approved

_HTTP_URL_RE = re.compile(r"https?://[^\s\"'`<>]+", re.IGNORECASE)
_BROWSER_NAV_HINT_RE = re.compile(
    r"(?i)\b(goto|new_tab|page\.goto|navigate|chromium\.launch|wait_for_load|page_info)\b"
)
_BROWSER_SKILLS = frozenset(
    {"browser-use", "playwright-skill", "playwright-cli"}
)


def is_mutating_skill_command(command: Optional[str]) -> bool:
    """判断 skill 命令是否会写入平台/数据库（用于确认门与串行策略）。"""
    if not command or not str(command).strip():
        return False
    text = str(command)
    actions = [m.group("action") for m in _ACTION_RE.finditer(text)]
    if actions:
        return any(not _READONLY_ACTION_RE.match(a) for a in actions)
    return bool(_MUTATING_FUNC_RE.search(text))


def prefer_url_reader_instead(
    skill_name: Optional[str], command: Optional[str]
) -> Optional[str]:
    """静态网页/报告误用浏览器 Skill 时，返回改走 url-reader 的提示；否则 None。"""
    name = (skill_name or "").strip().lower()
    cmd = command or ""
    if name not in _BROWSER_SKILLS:
        return None

    urls = _HTTP_URL_RE.findall(cmd)
    sample_url = urls[0] if urls else "https://example.com/page.html"

    # browser-use 在本仓库几乎总被写成裸 API/heredoc：禁止执行。
    # 交互站/加购等应改 playwright-skill；切勿再推 url-reader（SPA 会空转）。
    if name == "browser-use":
        spa = bool(
            re.search(
                r"(?i)teambition\.com|dingtalk\.com|callie\.cc|bomiv\.com|"
                r"click|fill|login|cart|加购|下单|传图|购物车",
                cmd,
            )
        )
        if spa or not urls:
            return (
                "错误: browser-use 不接受 goto/new_tab/heredoc 这类裸命令。"
                "请改用 playwright-skill（持久会话）：\n"
                'node run.js "await page.goto(\''
                + sample_url
                + '\'); /* 再 click/fill/加购 */"\n'
                "禁止改调 url-reader 处理需要交互的页面。"
            )
        return (
            "错误: browser-use 不接受 goto/new_tab/heredoc 这类裸命令。"
            "静态网页/HTML 报告请用 url-reader：\n"
            f'python scripts/read_url.py "{sample_url}"\n'
            "需要点击/登录/填表/加购时请用 playwright-skill。"
        )

    # 登录态 / 交互 SPA：必须浏览器，禁止改道 url-reader
    if re.search(
        r"(?i)teambition\.com|dingtalk\.com|callie\.cc|bomiv\.com|"
        r"addCookies|TEAMBITION_|TB_ACCESS_TOKEN|"
        r"getPageText|getPageStructure|helpers\.",
        cmd,
    ):
        return None

    # playwright：明显只是打开 URL 读 HTML 报告 → 拦
    looks_like_nav = bool(_BROWSER_NAV_HINT_RE.search(cmd))
    is_html_report = any(
        u.lower().endswith((".html", ".htm")) or "agent-test" in u.lower() for u in urls
    )
    has_interaction = bool(
        re.search(
            r"(?i)\b(click|fill|type|press|login|wait_for_selector|locator|get_by_)\b",
            cmd,
        )
    )
    # screenshot / waitForTimeout 不算「交互」——纯打开 HTML 报告仍应走 url-reader
    if urls and (is_html_report or looks_like_nav) and not has_interaction:
        return (
            "错误: 读取静态网页/HTML 报告请使用 url-reader，不要用 playwright 打开：\n"
            f'python scripts/read_url.py "{sample_url}"\n'
            "playwright-skill 仅用于需要真实浏览器交互的场景。"
        )
    return None


def compact_skill_tool_output(text: str, max_chars: int = 2500) -> str:
    """压缩过长 skill 输出，保留决策信息，避免撑爆上下文。"""
    raw = text if isinstance(text, str) else str(text or "")
    if not raw:
        return raw
    # 确认门与短结果原样返回
    if "needs_confirmation" in raw or len(raw) <= max_chars:
        return raw

    # 批量 JSON：优先保留 summary + 每条 result 截断
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "summary" in data and "results" in data:
            trimmed_results = []
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    trimmed_results.append(item)
                    continue
                copy = dict(item)
                if isinstance(copy.get("result"), str) and len(copy["result"]) > 800:
                    body = copy["result"]
                    copy["result"] = (
                        body[:500]
                        + f"\n…[已截断 {len(body) - 700} 字符]…\n"
                        + body[-200:]
                    )
                trimmed_results.append(copy)
            data["results"] = trimmed_results
            data["_compacted"] = True
            return json.dumps(data, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    head = max_chars - 400
    if head < 800:
        head = 800
    return (
        raw[:head]
        + f"\n\n…[skill 输出过长，已截断 {len(raw) - head - 200} 字符，完整日志见服务端]…\n\n"
        + raw[-200:]
    )


def _needs_write_confirmation_payload(
    *,
    skill_name: Optional[str],
    command: Optional[str],
    pending_commands: Optional[list] = None,
    executed_readonly: Optional[list] = None,
) -> str:
    """返回需用户确认的结构化提示；可附带已放行的只读/执行结果。"""
    message = (
        "检测到平台/数据库写入操作，用户本轮消息没有明确同意写入，已拒绝执行写入。"
        "服务端按用户原话判定，本轮内任何写入重试（包括 user_confirmed=true）都会被拒绝。"
        "界面已弹出「确认写入」按钮，请勿要求用户打字确认。"
        "请立即停止调用写入命令，用一两句话列出将要写入的内容与影响后结束本轮；"
        "用户点允许后系统会自动 resume 并放行写入。"
    )
    if executed_readonly:
        message = (
            "只读/执行类命令已先执行；写入类命令仍需用户确认，本轮未执行写入。"
            "界面已弹出「确认写入」按钮，请勿要求用户打字。"
            "请汇报已执行结果并简述待写入内容后结束本轮。"
        )
    draft_mode = False
    try:
        from orchestrator_integration.telemetry_context import get_telemetry_context

        draft_mode = bool(get_telemetry_context().draft_writes)
    except Exception:
        draft_mode = False
    if draft_mode:
        message = (
            "【草稿态】写入未落库，仅生成待确认变更草稿。"
            + message
        )
    payload = {
        "status": "needs_confirmation",
        "mode": "draft" if draft_mode else "confirm",
        "message": message,
        "pending": {
            "skill_name": skill_name,
            "command": command,
            "commands": pending_commands,
        },
        "executed_readonly": executed_readonly or [],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _sanitize_runtime_path_segment(value: Optional[str], default: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return default

    sanitized = raw.replace("..", "_")
    for separator in filter(None, {os.sep, os.altsep}):
        sanitized = sanitized.replace(separator, "_")

    sanitized = sanitized.strip(" .")
    return sanitized or default


def _build_skill_screenshots_dir(
    project_id: Optional[int] = None,
    case_dir_key: Optional[str] = None,
) -> str:
    project_segment = str(project_id if project_id is not None else 0)
    case_segment = _sanitize_runtime_path_segment(case_dir_key, "_default")
    return os.path.abspath(
        os.path.join(
            settings.MEDIA_ROOT,
            "skill_runtime",
            "screenshots",
            project_segment,
            case_segment,
        )
    )


# 截图/产物目录空闲超过该时长（秒）才允许清理，避免 chat_session_id 变化时误删正在使用的截图
_SKILL_DIR_STALE_SECONDS = 5 * 60 * 60


def _dir_latest_mtime(path: str) -> float:
    """目录树内最新文件 mtime；无文件返回 0（目录结构变更不算活跃写入）。"""
    latest = 0.0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                mtime = os.path.getmtime(os.path.join(root, name))
            except OSError:
                continue
            if mtime > latest:
                latest = mtime
    return latest


def _prepare_skill_screenshots_dir(
    project_id: Optional[int] = None,
    case_dir_key: Optional[str] = None,
) -> str:
    screenshots_dir = _build_skill_screenshots_dir(project_id, case_dir_key)
    if not case_dir_key:
        os.makedirs(screenshots_dir, exist_ok=True)
        return screenshots_dir

    should_clear = False
    if os.path.exists(screenshots_dir):
        latest = _dir_latest_mtime(screenshots_dir)
        if latest and time.time() - latest > _SKILL_DIR_STALE_SECONDS:
            should_clear = True

    if should_clear:
        shutil.rmtree(screenshots_dir, ignore_errors=True)
        logger.info(f"[execute_skill_script] 清空旧截图目录: {screenshots_dir}")

    os.makedirs(screenshots_dir, exist_ok=True)
    return screenshots_dir


def _build_skill_artifacts_dir(
    project_id: Optional[int] = None,
    case_dir_key: Optional[str] = None,
) -> str:
    project_segment = str(project_id if project_id is not None else 0)
    case_segment = _sanitize_runtime_path_segment(case_dir_key, "_default")
    return os.path.abspath(
        os.path.join(
            settings.MEDIA_ROOT,
            "skill_runtime",
            "artifacts",
            project_segment,
            case_segment,
        )
    )


def _prepare_skill_artifacts_dir(
    project_id: Optional[int] = None,
    case_dir_key: Optional[str] = None,
) -> str:
    artifacts_dir = _build_skill_artifacts_dir(project_id, case_dir_key)
    if not case_dir_key:
        os.makedirs(artifacts_dir, exist_ok=True)
        return artifacts_dir

    should_clear = False
    if os.path.exists(artifacts_dir):
        latest = _dir_latest_mtime(artifacts_dir)
        if latest and time.time() - latest > _SKILL_DIR_STALE_SECONDS:
            should_clear = True

    if should_clear:
        shutil.rmtree(artifacts_dir, ignore_errors=True)
        logger.info(f"[execute_skill_script] 清空旧产物目录: {artifacts_dir}")

    os.makedirs(artifacts_dir, exist_ok=True)
    return artifacts_dir


def _is_allowed_artifact_file(file_path: str) -> bool:
    if not file_path or not os.path.isfile(file_path) or os.path.islink(file_path):
        return False
    suffix = os.path.splitext(file_path)[1].lower()
    if suffix not in _ARTIFACT_EXTENSIONS:
        return False
    if os.path.basename(file_path).startswith("."):
        return False
    try:
        return os.path.getsize(file_path) <= _MAX_ARTIFACT_SIZE_BYTES
    except OSError:
        return False


def _snapshot_artifact_files(root_dir: str) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    if not root_dir or not os.path.isdir(root_dir):
        return snapshot

    for current_root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        for name in files:
            full_path = os.path.join(current_root, name)
            if not _is_allowed_artifact_file(full_path):
                continue
            rel_path = os.path.relpath(full_path, root_dir).replace(os.sep, "/")
            snapshot[rel_path] = full_path
    return snapshot


def _path_to_media_url(file_path: str) -> Optional[str]:
    try:
        media_root = os.path.abspath(settings.MEDIA_ROOT)
        candidate = os.path.abspath(file_path)
        if os.path.commonpath([media_root, candidate]) != media_root:
            return None
        relative_path = os.path.relpath(candidate, media_root).replace(os.sep, "/")
        return f"{settings.MEDIA_URL.rstrip('/')}/{relative_path}"
    except Exception:
        return None


def _build_artifact_payload(file_path: str) -> Optional[dict[str, object]]:
    media_url = _path_to_media_url(file_path)
    if not media_url:
        return None

    mime_type, _ = mimetypes.guess_type(file_path)
    if file_path.lower().endswith(".drawio"):
        mime_type = mime_type or "application/vnd.jgraph.mxfile"

    payload: dict[str, object] = {
        "type": "file",
        "name": os.path.basename(file_path),
        "url": media_url,
        "path": os.path.relpath(file_path, settings.MEDIA_ROOT).replace(os.sep, "/"),
        "mime_type": mime_type or "application/octet-stream",
    }
    try:
        payload["size"] = os.path.getsize(file_path)
    except OSError:
        pass
    return payload


def _extract_artifact_candidates(text: str) -> list[str]:
    if not text:
        return []

    seen: set[str] = set()
    candidates: list[str] = []
    strip_chars = " \t\r\n()[]{}<>,;:：。，“”‘’"

    for pattern in (_QUOTED_ARTIFACT_TOKEN_RE, _ARTIFACT_TOKEN_RE):
        for match in pattern.finditer(text):
            candidate = (match.group("path") or "").strip(strip_chars)
            if "：" in candidate:
                candidate = candidate.split("：")[-1].strip(strip_chars)
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

    return candidates


def _find_named_file(root_dir: str, candidate: str) -> Optional[str]:
    if not root_dir or not os.path.isdir(root_dir):
        return None

    normalized_candidate = candidate.replace("\\", os.sep)
    joined_path = os.path.join(root_dir, normalized_candidate.lstrip("./"))
    if _is_allowed_artifact_file(joined_path):
        return joined_path

    basename = os.path.basename(normalized_candidate)
    if not basename:
        return None

    for current_root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        if basename in files:
            full_path = os.path.join(current_root, basename)
            if _is_allowed_artifact_file(full_path):
                return full_path
    return None


def _collect_skill_artifacts(
    output_text: str,
    *,
    skill_dir: str,
    artifacts_dir: str,
    artifacts_before: dict[str, str],
) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    seen_urls: set[str] = set()

    def add_file(file_path: Optional[str]) -> None:
        if not file_path or not _is_allowed_artifact_file(file_path):
            return
        payload = _build_artifact_payload(file_path)
        if not payload:
            return
        media_url = str(payload.get("url") or "")
        if not media_url or media_url in seen_urls:
            return
        seen_urls.add(media_url)
        collected.append(payload)

    artifacts_after = _snapshot_artifact_files(artifacts_dir)
    for rel_path, full_path in artifacts_after.items():
        if rel_path not in artifacts_before:
            add_file(full_path)

    for candidate in _extract_artifact_candidates(output_text):
        if candidate.startswith("/media/"):
            continue
        if os.path.isabs(candidate):
            add_file(candidate)
            continue
        add_file(_find_named_file(artifacts_dir, candidate))
        add_file(_find_named_file(skill_dir, candidate))

    return collected


def _finalize_skill_result(
    result_output: str,
    *,
    skill_dir: str,
    artifacts_dir: str,
    artifacts_before: dict[str, str],
    exit_code: Optional[int] = None,
    skill_name: str = "",
    command: str = "",
    duration_ms: float = 0.0,
    structured: bool = True,
) -> str:
    artifacts = _collect_skill_artifacts(
        result_output,
        skill_dir=skill_dir,
        artifacts_dir=artifacts_dir,
        artifacts_before=artifacts_before,
    )
    compact = compact_skill_tool_output(result_output)

    # FR01：结构化 ToolResult（保留 artifacts 给前端下载）
    if structured:
        try:
            from orchestrator_integration.tool_result import classify_skill_output

            tr = classify_skill_output(
                text=compact if isinstance(compact, str) else str(compact),
                exit_code=exit_code,
                duration_ms=duration_ms,
                skill_name=skill_name,
                command=command or "",
            )
            if artifacts:
                tr.data = dict(tr.data or {})
                tr.data["artifacts"] = artifacts
                tr.evidence = dict(tr.evidence or {})
                tr.evidence["artifact_count"] = len(artifacts)
            # 记录到任务立项，供运行时验收；并落库 AgentStep
            try:
                from orchestrator_integration.telemetry_context import (
                    get_telemetry_context,
                )

                ctx = get_telemetry_context()
                commission = ctx.commission
                if commission is not None:
                    commission.last_tool_results.append(tr.to_tool_text())
                    if len(commission.last_tool_results) > 20:
                        commission.last_tool_results = commission.last_tool_results[
                            -20:
                        ]
                if ctx.runtime_task_id:
                    try:
                        from orchestrator_integration.agent_runtime import (
                            record_step_result,
                        )
                        from orchestrator_integration.models import AgentTask

                        task = AgentTask.objects.filter(pk=ctx.runtime_task_id).first()
                        if task is not None:
                            step_summary = tr.summary or (
                                compact[:500]
                                if isinstance(compact, str)
                                else str(compact)[:500]
                            )
                            record_step_result(
                                task,
                                tool_name=skill_name or "execute_skill_script",
                                tool_input={"command": (command or "")[:500]},
                                structured=tr.to_dict(),
                                summary=step_summary,
                                stage_id=ctx.current_stage or "",
                            )
                    except Exception as step_err:
                        logger.debug("record_step_result 失败: %s", step_err)
                # FR06：失败/unknown 附加集中重试与核对提示
                if tr.status in {"failed", "unknown"}:
                    try:
                        from orchestrator_integration.reconcile import (
                            build_reconcile_plan,
                        )
                        from orchestrator_integration.task_commission import (
                            CHANNEL_LIGHT,
                        )

                        channel = (
                            commission.channel if commission else CHANNEL_LIGHT
                        )
                        err_blob = tr.summary or (
                            compact if isinstance(compact, str) else str(compact)
                        )
                        plan = build_reconcile_plan(
                            skill_name=skill_name or "",
                            text=err_blob,
                            status=tr.status,
                            structured=tr.to_dict(),
                            attempt=0,
                            channel=channel,
                        )
                        # 不确定结果消耗一次重试预算标记（实际重放仍禁止）
                        if plan.get("must_reconcile") and ctx.runtime_task_id:
                            try:
                                from orchestrator_integration.agent_runtime import (
                                    consume_budget,
                                )
                                from orchestrator_integration.models import AgentTask

                                t = AgentTask.objects.filter(
                                    pk=ctx.runtime_task_id
                                ).first()
                                if t is not None:
                                    consume_budget(t, retries=1)
                            except Exception:
                                pass
                        return (
                            tr.to_tool_text()
                            + f"\n\n[核对/重试] {plan.get('hint')}"
                            + (
                                f"\n[reconcile_action]={plan.get('reconcile_action')}"
                                if plan.get("reconcile_action")
                                else ""
                            )
                        )
                    except Exception:
                        pass
            except Exception:
                pass
            return tr.to_tool_text()
        except Exception as exc:
            logger.debug("ToolResult 适配失败，回退原文: %s", exc)

    if not artifacts:
        return compact

    text_content = compact.strip() if compact and compact.strip() else ""
    if not text_content:
        text_content = f"已生成 {len(artifacts)} 个文件，可直接下载。"

    payload = [{"type": "text", "text": text_content}, *artifacts]
    return json.dumps(payload, ensure_ascii=False)


def _get_playwright_session_manager() -> PlaywrightSessionManager:
    """延迟初始化，避免在 import 时启动后台清理线程（线程安全）"""
    global _playwright_session_manager
    if _playwright_session_manager is None:
        with _playwright_session_manager_lock:
            if _playwright_session_manager is None:
                idle_timeout = getattr(
                    settings, "PLAYWRIGHT_BROWSER_SESSION_IDLE_TIMEOUT_SECONDS", 15 * 60
                )
                max_sessions = getattr(settings, "PLAYWRIGHT_BROWSER_MAX_SESSIONS", 20)
                _playwright_session_manager = PlaywrightSessionManager(
                    idle_timeout_seconds=int(idle_timeout),
                    max_sessions=int(max_sessions),
                )
    return _playwright_session_manager


def _resolve_skill_runtime_api_key(user_id: int) -> tuple[str, str]:
    """解析 Skill 运行时使用的 API Key。

    优先级：
    1. 非空的 settings/环境变量 LOCA_STUDE_API_KEY（运维覆盖）
    2. 当前对话用户名下最新一条有效 API Key
    3. 空字符串（不注入，保留 skill 脚本内默认值）

    Returns:
        (api_key, source_label) source_label 仅用于日志，不含密钥内容。
    """
    configured = (
        getattr(settings, "LOCA_STUDE_API_KEY", None)
        or os.environ.get("LOCA_STUDE_API_KEY")
        or ""
    ).strip()
    if configured:
        return configured, "env_or_settings"

    if not user_id:
        return "", "none"

    try:
        from api_keys.models import APIKey
    except ImportError:
        logger.debug(
            "[execute_skill_script] api_keys 应用不可用，跳过用户 Key 解析 user_id=%s",
            user_id,
        )
        return "", "none"

    try:
        for key_obj in APIKey.objects.filter(user_id=user_id, is_active=True).order_by(
            "-created_at"
        ):
            if key_obj.is_valid() and (key_obj.key or "").strip():
                return key_obj.key.strip(), f"user_key_id={key_obj.id}"
    except Exception as e:
        logger.error(
            "[execute_skill_script] 读取用户 API Key 失败 user_id=%s: %s",
            user_id,
            e,
            exc_info=True,
        )
        return "", "error"
    return "", "none"


def _prepend_interpreter_to_path(env: dict) -> None:
    """把当前解释器目录插到子进程 PATH 最前面。

    Skill 脚本以 `python xxx.py` 形式调用，但服务可能由 conda 环境里的
    python.exe 直接启动（未经 activate），子进程 PATH 里没有解释器目录。
    Windows 上 `python` 于是命中 Microsoft Store 的 App Execution Alias
    存根，以退出码 9009 失败；插到最前同时也能盖掉该别名。
    """
    interpreter_dir = os.path.dirname(sys.executable)
    if not interpreter_dir:
        return

    # Windows/conda 的入口脚本在 Scripts/，venv 与 posix 布局在 bin/
    candidates = [
        interpreter_dir,
        os.path.join(interpreter_dir, "Scripts"),
        os.path.join(interpreter_dir, "bin"),
    ]
    prefix = [path for path in candidates if os.path.isdir(path)]
    if not prefix:
        return

    existing = [
        path
        for path in (env.get("PATH") or "").split(os.pathsep)
        if path and path not in prefix
    ]
    env["PATH"] = os.pathsep.join(prefix + existing)


def _resolve_skill_runtime_backend_url() -> str:
    """Skill 在 backend 进程内执行时，默认回环访问本服务。"""
    configured = (
        getattr(settings, "LOCA_STUDE_BACKEND_URL", None)
        or os.environ.get("LOCA_STUDE_BACKEND_URL")
        or ""
    ).strip()
    if configured:
        return configured.rstrip("/")
    # 容器/本机同进程：默认本机 8000，避免 skill 脚本默认 127.0.0.1 与部署端口不一致时无覆盖
    return "http://127.0.0.1:8000"


def get_skill_tools(
    user_id: int,
    project_id: Optional[int] = None,
    test_case_id: Optional[int] = None,
    chat_session_id: Optional[str] = None,
    user_write_approved: Optional[bool] = None,
    allowed_skill_names: Optional[set[str]] = None,
    allow_execute: bool = True,
) -> list[object]:
    """获取 Skill 工具列表（Skills 全局共享，不限制项目）。

    allowed_skill_names: 本轮分区白名单；None 表示不限制，空 set 表示禁止执行任何 skill。
    allow_execute: False 时不挂载 execute_skill_script（纯知识库问答分区）。
    """
    current_user_id = user_id
    current_project_id = project_id if project_id is not None else 0
    current_test_case_id = test_case_id
    current_chat_session_id = chat_session_id
    allowed_names = (
        {n.strip() for n in allowed_skill_names if n and str(n).strip()}
        if allowed_skill_names is not None
        else None
    )

    def _assert_skill_allowed(skill_name: Optional[str]) -> Optional[str]:
        name = (skill_name or "").strip()
        if not name:
            return None
        # 任务立项主/辅授权（优先于工具创建时的静态分区白名单）
        # Escape Hatch 动态扩权写在 commission.granted_auxiliary，必须在此直接放行，
        # 否则会落到下方 closed-over allowed_names 仍被拒。
        try:
            from orchestrator_integration.telemetry_context import get_telemetry_context

            ctx = get_telemetry_context()
            commission = ctx.commission
            if commission is not None:
                granted = commission.effective_allowlist()
                if granted is not None:
                    if name not in granted:
                        if name in commission.auxiliary_skills:
                            return (
                                f"Skill「{name}」属于辅类，尚未授权。"
                                "请先 request_tool_expansion(skill_name=...) 申请一次 Escape Hatch，"
                                "禁止要求用户点名 Skill 或重写指令。"
                            )
                        return (
                            f"Skill「{name}」不在本任务主类授权内。"
                            f"主类: {', '.join(sorted(commission.primary_skills)) or '(无)'}。"
                            "请改用主类 skill 自愈；禁止要求用户重写指令。"
                        )
                    return None
        except Exception:
            pass
        if allowed_names is None:
            return None
        if not allowed_names:
            return (
                f"本轮分区禁止执行任何 Skill（当前请求 skill_name={name}）。"
                "请用知识库/已挂载工具回答；禁止要求用户重写指令或点名 Skill。"
            )
        if name not in allowed_names:
            return (
                f"Skill「{name}」不在本轮分区白名单内。允许: {', '.join(sorted(allowed_names))}。"
                "请改用允许的 skill_name 自愈；禁止要求用户换话术或点名 Skill。"
            )
        return None

    def _emit_telemetry(
        *,
        tool_name: str,
        skill_name: str = "",
        success: bool = True,
        duration_ms: float = 0.0,
        rejected_by_allowlist: bool = False,
        rejected_by_catalog: bool = False,
        error_kind: str = "",
        result_text: str = "",
    ) -> None:
        try:
            from orchestrator_integration.execution_policy import classify_attribution
            from orchestrator_integration.telemetry_context import get_telemetry_context
            from orchestrator_integration.tool_telemetry import record_tool_telemetry

            ctx = get_telemetry_context()
            attr = classify_attribution(
                error_kind=error_kind, result_text=result_text
            )
            record_tool_telemetry(
                zone=ctx.zone,
                playbook=ctx.playbook,
                tool_name=tool_name,
                skill_name=skill_name,
                success=success,
                duration_ms=duration_ms,
                rejected_by_allowlist=rejected_by_allowlist,
                rejected_by_catalog=rejected_by_catalog,
                error_kind=error_kind,
                attribution=attr,
                tools_mounted=ctx.tools_mounted,
                session_id=ctx.session_id or (current_chat_session_id or ""),
                user_id=ctx.user_id if ctx.user_id is not None else current_user_id,
            )
        except Exception as exc:
            logger.debug("skill telemetry 失败: %s", exc)

    def _persist_task():
        try:
            from orchestrator_integration.telemetry_context import get_telemetry_context
            from orchestrator_integration.models import AgentTask

            ctx = get_telemetry_context()
            if not ctx.runtime_task_id:
                return None
            return AgentTask.objects.filter(pk=ctx.runtime_task_id).first()
        except Exception:
            return None

    def _reserve_batch_budget(n: int) -> int:
        task = _persist_task()
        if task is None:
            return 0
        try:
            from orchestrator_integration.agent_runtime import reserve_tool_actions

            ok, took, _detail = reserve_tool_actions(task, n)
            return int(took if ok else 0)
        except Exception as exc:
            logger.debug("reserve_tool_actions failed: %s", exc)
            return 0

    def _release_batch_reservation() -> None:
        task = _persist_task()
        if task is None:
            return
        try:
            from orchestrator_integration.agent_runtime import release_tool_reservation

            release_tool_reservation(task)
        except Exception as exc:
            logger.debug("release_tool_reservation failed: %s", exc)

    def _consume_budget(
        n: int = 1, *, duration_ms: int = 0, prefer_reserved: bool = False
    ) -> Optional[str]:
        try:
            from orchestrator_integration.telemetry_context import get_telemetry_context

            ctx = get_telemetry_context()
            commission = ctx.commission
            # 先扣持久化任务预算（FLOW03），再扣内存立项计数
            task = _persist_task()
            if task is not None:
                try:
                    from orchestrator_integration.agent_runtime import (
                        consume_budget,
                        settle_reserved_action,
                    )

                    if prefer_reserved and n == 1 and not duration_ms:
                        ok_db, detail_db = settle_reserved_action(task)
                    else:
                        ok_db, detail_db = consume_budget(
                            task,
                            tool_steps=n,
                            duration_ms=max(0, int(duration_ms or 0)),
                        )
                    if not ok_db:
                        _emit_telemetry(
                            tool_name="execute_skill_script",
                            skill_name="budget",
                            success=False,
                            error_kind="budget_exceeded",
                            result_text=detail_db,
                        )
                        return detail_db
                    # A persisted task has one authoritative budget. Keep the
                    # commission counters as a display mirror, never a second cap.
                    if commission is not None:
                        budget = task.budget or {}
                        commission.max_tool_steps = int(budget.get("max_tool_actions", budget.get("max_tool_steps", commission.max_tool_steps)))
                        commission.tool_steps_used = int(budget.get("used_tool_actions", budget.get("used_tool_steps", 0)))
                    return None
                except Exception as persist_err:
                    logger.debug("persist budget consume failed: %s", persist_err)
            if commission is None:
                return None
            ok, detail = commission.consume_tool_step(n)
            if not ok:
                _emit_telemetry(
                    tool_name="execute_skill_script",
                    skill_name="budget",
                    success=False,
                    error_kind="budget_exceeded",
                    result_text=detail,
                )
                return detail
        except Exception:
            return None
        return None

    def _channel() -> str:
        try:
            from orchestrator_integration.telemetry_context import get_telemetry_context
            from orchestrator_integration.task_commission import CHANNEL_LIGHT

            c = get_telemetry_context().commission
            return c.channel if c else CHANNEL_LIGHT
        except Exception:
            from orchestrator_integration.task_commission import CHANNEL_LIGHT

            return CHANNEL_LIGHT

    def _record_result_snippet(text: str) -> None:
        try:
            from orchestrator_integration.telemetry_context import get_telemetry_context

            c = get_telemetry_context().commission
            if c is not None:
                c.last_tool_results.append(str(text or "")[:1500])
                if len(c.last_tool_results) > 20:
                    c.last_tool_results = c.last_tool_results[-20:]
        except Exception:
            pass

    @langchain_tool
    def read_skill_content(skill_name: str) -> str:
        """
        读取指定 Skill 的完整 SKILL.md（按需加载说明与示例）。

        在需要执行 Skill 副作用前调用：落库/平台操作、读网页(url-reader)、浏览器自动化等。
        必须先于 execute_skill_script；有依赖的多步不要与写入并行。
        业务文档问答请用 knowledge_search 或已注入的预检索，不要用本工具代替检索。
        注意：skill_name 是参数，不是顶层 tool 名；不要直接调用名为 url-reader 的 tool。

        Args:
            skill_name: Skill 名称（如 loca-stude、url-reader）

        Returns:
            SKILL.md 的完整内容，包含详细的使用说明和示例
        """
        from skills.models import Skill

        logger.info(f"[read_skill_content] skill_name={skill_name}")
        blocked = _assert_skill_allowed(skill_name)
        if blocked:
            logger.info("[read_skill_content] 分区拒绝: %s", blocked)
            _emit_telemetry(
                tool_name="read_skill_content",
                skill_name=skill_name,
                success=False,
                rejected_by_allowlist=True,
                error_kind="allowlist",
            )
            return blocked

        try:
            skill = Skill.objects.filter(name=skill_name, is_active=True).first()

            if not skill:
                available = Skill.objects.filter(is_active=True).values_list(
                    "name", flat=True
                )
                available_list = list(available)
                return f"错误: 未找到名为 '{skill_name}' 的 Skill。可用的 Skills: {available_list}"

            if not skill.skill_content:
                return f"错误: Skill '{skill_name}' 没有 SKILL.md 内容"

            return skill.skill_content

        except Exception as e:
            logger.error(f"[read_skill_content] 读取失败: {e}", exc_info=True)
            return f"错误: {str(e)}"

    def _execute_single_skill_script(
        skill_name: str,
        command: str,
        session_id: Optional[str] = None,
    ) -> str:
        """内部函数：执行单条 Skill 命令"""
        from skills.models import Skill

        logger.info(
            f"[execute_skill_script] skill_name={skill_name}, command={command}"
        )

        try:
            skill = Skill.objects.filter(name=skill_name, is_active=True).first()

            if not skill:
                available = Skill.objects.filter(is_active=True).values_list(
                    "name", flat=True
                )
                available_list = list(available)
                return f"错误: 未找到名为 '{skill_name}' 的 Skill。可用的 Skills: {available_list}"

            skill_dir = skill.get_full_path()
            if not skill_dir or not os.path.isdir(skill_dir):
                return f"错误: Skill '{skill_name}' 目录不存在"

            logger.info(f"[execute_skill_script] 在目录 {skill_dir} 执行: {command}")

            env = os.environ.copy()
            _prepend_interpreter_to_path(env)
            # 内网 Skill（url-reader 等）勿走系统代理，否则 192.168.* 常被劫持失败
            for proxy_key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            ):
                env.pop(proxy_key, None)
            env["NO_PROXY"] = "*"
            env["no_proxy"] = "*"
            # 1) 运维配置 / 2) 当前用户有效 API Key / 3) 不注入（保留脚本默认）
            backend_url = _resolve_skill_runtime_backend_url()
            api_key, key_source = _resolve_skill_runtime_api_key(current_user_id)
            if backend_url:
                env["LOCA_STUDE_BACKEND_URL"] = backend_url
            if api_key:
                env["LOCA_STUDE_API_KEY"] = api_key
                logger.info(
                    "[execute_skill_script] 已注入运行时 API Key (user_id=%s, source=%s)",
                    current_user_id,
                    key_source,
                )
            else:
                logger.warning(
                    "[execute_skill_script] 未找到可用 API Key (user_id=%s, source=%s)，"
                    "将依赖 skill 脚本内默认值（可能 401）",
                    current_user_id,
                    key_source,
                )

            case_dir_key = None
            if current_test_case_id:
                case_dir_key = str(current_test_case_id)
            elif session_id:
                case_dir_key = session_id

            screenshots_dir = _prepare_skill_screenshots_dir(
                project_id=current_project_id,
                case_dir_key=case_dir_key,
            )
            env["SCREENSHOT_DIR"] = screenshots_dir
            artifacts_dir = _prepare_skill_artifacts_dir(
                project_id=current_project_id,
                case_dir_key=case_dir_key,
            )
            env["SKILL_OUTPUT_DIR"] = artifacts_dir
            env["ARTIFACT_DIR"] = artifacts_dir
            # FR06.4：写/执行动作注入幂等键（本地关联；非外部恰好一次保证）
            try:
                from orchestrator_integration.idempotency import idempotency_env
                from orchestrator_integration.telemetry_context import (
                    get_telemetry_context,
                )

                _ctx = get_telemetry_context()
                _task_id = ""
                if _ctx.commission is not None:
                    _task_id = getattr(_ctx.commission, "task_id", "") or ""
                if _ctx.runtime_task_id:
                    _task_id = _task_id or str(_ctx.runtime_task_id)
                for _k, _v in idempotency_env(
                    task_id=_task_id,
                    skill_name=skill_name or "",
                    command=command or "",
                    step_id=_ctx.current_stage or "",
                ).items():
                    env[_k] = _v
            except Exception:
                pass
            artifacts_before = _snapshot_artifact_files(artifacts_dir)

            # Windows 兼容：将单引号包裹的参数转换为双引号（用于 cmd.exe）
            # 同时处理多行字符串，将换行符转换为单行
            import platform
            import re

            exec_command = command
            if platform.system() == "Windows":
                # 处理多行字符串：将双引号内的换行符替换为空格或分号
                def collapse_multiline(m):
                    content = m.group(1)
                    # 将换行替换为空格，保持代码可执行
                    collapsed = " ".join(
                        line.strip() for line in content.split("\n") if line.strip()
                    )
                    return f'"{collapsed}"'

                # 匹配 "..." 形式的多行字符串
                exec_command = re.sub(r'"([^"]*\n[^"]*)"', collapse_multiline, command)

                # 单引号转双引号
                def convert_quotes(m):
                    param = m.group(1)
                    value = m.group(2)
                    escaped = value.replace('"', '\\"')
                    return f'{param}"{escaped}"'

                exec_command = re.sub(
                    r"(--\w+\s+)'([^']*)'", convert_quotes, exec_command
                )

                if exec_command != command:
                    logger.info(f"[execute_skill_script] Windows 命令转换完成")

            # 持久化 Playwright 会话路径
            # 仅当 session_id 存在 + skill_name == 'playwright-skill' + 命令是 run.js 调用时启用
            if session_id and skill_name == "playwright-skill":
                run_js_args = extract_runjs_args(exec_command)
                if run_js_args is not None:
                    # 调试日志
                    logger.debug(f"[execute_skill_script] run_js_args: {run_js_args}")
                    # session_key 包含 chat_session_id 以隔离不同对话的浏览器会话
                    chat_id_part = current_chat_session_id or "default"
                    session_key = f"{current_user_id}_{current_project_id}_{chat_id_part}_{session_id}"
                    try:
                        manager = _get_playwright_session_manager()
                        output = manager.execute_run_js(
                            session_key=session_key,
                            skill_dir=skill_dir,
                            run_js_args=run_js_args,
                            env=env,
                            timeout_seconds=120,
                        )
                        logger.info(
                            f"[execute_skill_script] 持久化会话执行完成, session_key={session_key}"
                        )
                        cleaned_output = strip_terminal_control_sequences(output)
                        result_output = (
                            cleaned_output.strip()
                            if cleaned_output.strip()
                            else "(无输出)"
                        )
                        heal_hint = ""
                        low = result_output.lower()
                        if "filechooser" in low or "waiting for event \"filechooser\"" in low:
                            heal_hint = (
                                "\n[自愈] filechooser 超时：勿再 waitForEvent('filechooser')；"
                                "改为定位隐藏 input[type=file] 后 setInputFiles(真实图片路径)，"
                                "然后同一轮内继续带同一 session_id 完成加购/校验。"
                            )
                        result_output = (
                            f'[PERSISTENT_SESSION] session_id={session_id}\n'
                            f'[SCREENSHOT_DIR] {screenshots_dir}\n'
                            f'{result_output}{heal_hint}\n'
                            f'[提示] 同一轮内立即用 session_id="{session_id}" 继续下一工具调用，'
                            f'勿向用户索要新指令；截图目录 {screenshots_dir}'
                        )
                        return _finalize_skill_result(
                            result_output,
                            skill_dir=skill_dir,
                            artifacts_dir=artifacts_dir,
                            artifacts_before=artifacts_before,
                            exit_code=None,
                            skill_name=skill_name or "",
                            command=command or "",
                        )
                    except TimeoutError:
                        logger.error(
                            "[execute_skill_script] 持久化 Playwright 执行超时"
                        )
                        from orchestrator_integration.tool_result import (
                            classify_skill_output,
                        )

                        return classify_skill_output(
                            text="错误: 命令执行超时（120秒）",
                            skill_name=skill_name or "",
                            command=command or "",
                        ).to_tool_text()
                    except Exception as e:
                        logger.error(
                            f"[execute_skill_script] 持久化 Playwright 执行失败: {e}",
                            exc_info=True,
                        )
                        return f"错误: {str(e)}"

            # Windows 编码处理：cmd.exe 默认使用 GBK (cp936)，需要使用系统默认编码
            import locale

            # playwright-cli 在只读的 skill 目录下以相对文件名保存截图会 EACCES，
            # 改在可写的截图目录执行，使截图默认落入 SCREENSHOT_DIR
            exec_cwd = screenshots_dir if skill_name == "playwright-cli" else skill_dir

            if platform.system() == "Windows":
                # Windows：字节模式 + 智能解码；仍支持停止信号打断
                result = _run_shell_cancellable(
                    exec_command,
                    cwd=exec_cwd,
                    env=env,
                    timeout=120,
                    text_mode=False,
                    session_id=session_id or "",
                )

                # 智能解码：先尝试 UTF-8（现代工具通常输出 UTF-8），失败再用 GBK（Windows cmd 默认）
                def smart_decode(data: bytes) -> str:
                    if not data:
                        return ""
                    try:
                        return data.decode("utf-8")
                    except UnicodeDecodeError:
                        return data.decode("gbk", errors="replace")

                stdout = strip_terminal_control_sequences(smart_decode(result.stdout or b""))
                stderr = strip_terminal_control_sequences(smart_decode(result.stderr or b""))
            else:
                result = _run_shell_cancellable(
                    exec_command,
                    cwd=exec_cwd,
                    env=env,
                    timeout=120,
                    text_mode=True,
                    session_id=session_id or "",
                )
                stdout = strip_terminal_control_sequences(result.stdout or "")
                stderr = strip_terminal_control_sequences(result.stderr or "")

            output = ""
            if stdout:
                output += stdout
            if stderr:
                if output:
                    output += "\n--- stderr ---\n"
                output += stderr

            if result.returncode != 0:
                output = f"命令执行失败 (退出码: {result.returncode})\n{output}"

            logger.info(
                f"[execute_skill_script] 执行完成, returncode={result.returncode}, output_len={len(output)}"
            )
            if output:
                logger.debug(f"[execute_skill_script] output: {output[:500]}")
            result_output = output.strip() if output.strip() else "(无输出)"

            # 如果是 playwright-skill 的 run.js 调用但没有使用 session_id，提醒 LLM
            if (
                skill_name == "playwright-skill"
                and "run.js" in command
                and not session_id
            ):
                result_output = f"[SCREENSHOT_DIR] {screenshots_dir}\n{result_output}\n\n[注意] 此次执行未使用 session_id，浏览器已关闭。如果这是多步骤测试的一部分，请在后续调用中使用 session_id 参数保持浏览器会话。"
            elif skill_name == "playwright-skill":
                result_output = f"[SCREENSHOT_DIR] {screenshots_dir}\n{result_output}"

            return _finalize_skill_result(
                result_output,
                skill_dir=skill_dir,
                artifacts_dir=artifacts_dir,
                artifacts_before=artifacts_before,
                exit_code=result.returncode,
                skill_name=skill_name or "",
                command=command or "",
            )

        except SkillExecutionCancelled as cancel_exc:
            logger.info("[execute_skill_script] 已按停止信号取消: %s", cancel_exc)
            from orchestrator_integration.tool_result import ToolResult, new_operation_id

            return ToolResult(
                status="cancelled",
                operation_id=new_operation_id(),
                error_code="cancelled_by_user",
                summary=str(cancel_exc),
                data={"raw_text": str(cancel_exc)},
            ).to_tool_text()
        except subprocess.TimeoutExpired:
            logger.error("[execute_skill_script] 执行超时")
            from orchestrator_integration.tool_result import classify_skill_output

            return classify_skill_output(
                text="错误: 命令执行超时（120秒）",
                exit_code=None,
                skill_name=skill_name or "",
                command=command or "",
            ).to_tool_text()
        except Exception as e:
            logger.error(f"[execute_skill_script] 执行失败: {e}", exc_info=True)
            from orchestrator_integration.tool_result import classify_skill_output

            return classify_skill_output(
                text=f"错误: {str(e)}",
                skill_name=skill_name or "",
                command=command or "",
            ).to_tool_text()

    @langchain_tool
    def execute_skill_script(
        skill_name: Optional[str] = None,
        command: Optional[str] = None,
        session_id: Optional[str] = None,
        commands: Optional[Union[list[dict[str, str]], str]] = None,
        parallel: bool = False,
        max_workers: int = 5,
        user_confirmed: bool = False,
    ) -> str:
        """
        执行 Skill 脚本命令（单个或批量）。由模型按意图决定是否调用。

        适用：落库/平台 CRUD、url-reader 读网页、浏览器自动化、接口自动化资源操作等副作用。
        不适用：仅根据知识库回答业务事实——请用 knowledge_search 或预检索。

        ## 调用顺序（重要）
        1. 先 read_skill_content(skill_name)，再本工具；禁止把 skill 名当 tool 名调用。
        2. 有依赖的多步必须按序：读文档 → 只读查询 →（问清用户）→ 写入；不要同一轮并行打乱顺序。
        3. 多条互相独立的只读命令可用 commands + parallel=true；凡含写入或有先后依赖，必须 parallel=false（默认）。
        4. 批量写入优先用一条 commands 列表串行组合，而不是多次并行单条调用。

        ## 写入确认
        含 add/create/update/delete/save 等落库 action 时，必须先征得用户明确同意，
        再以 user_confirmed=true 调用；未确认会返回 needs_confirmation 且不执行。

        **单个执行模式**：传入 skill_name 和 command
        **批量执行模式**：传入 commands 列表

        Args:
            skill_name: Skill 名称（单个执行时必填，如 loca-stude、url-reader）
            command: shell 命令，如 "python loca_stude_tools.py --action get_projects"（单个执行时必填）
            session_id: 可选会话ID，用于 playwright-skill 保持浏览器会话
            commands: 批量命令列表，每个元素包含 skill_name、command、session_id（可选）
                示例: [
                    {"skill_name": "loca-stude", "command": "python loca_stude_tools.py --action add_testcase ..."},
                    {"skill_name": "loca-stude", "command": "python loca_stude_tools.py --action add_testcase ..."}
                ]
            parallel: 批量是否并发；默认 False（串行）。含写入时即使传 True 也会强制串行。
            max_workers: 批量并发上限（仅 parallel=true 时生效，默认 5）
            user_confirmed: 写入类命令是否已获用户明确同意；未同意不得为 true

        Returns:
            单个模式返回命令输出；如执行中生成了文件，会追加可下载附件信息。
            Skills 可将导出文件写入 `SKILL_OUTPUT_DIR`（或 `ARTIFACT_DIR`）以便 Web 端直接下载。
            批量模式返回 JSON 格式结果汇总；写入未确认时返回 needs_confirmation
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from django.db import close_old_connections

        # 部分模型会把 commands 序列化成 JSON 字符串传入
        if isinstance(commands, str):
            try:
                commands = normalize_commands_arg(commands)
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        from orchestrator_integration.execution_policy import plan_batch_execution
        from orchestrator_integration.skill_catalog import (
            catalog_reject_unregistered_write,
        )
        from orchestrator_integration.skill_governance import (
            reject_cross_skill_batch,
            reject_zone_privilege_escalation,
        )
        from orchestrator_integration.telemetry_context import get_telemetry_context
        from orchestrator_integration.tool_telemetry import TelemetryTimer

        def _zone_id() -> str:
            return (get_telemetry_context().zone or "").strip()

        if _should_stop_execution():
            return json.dumps(
                {
                    "status": "cancelled",
                    "error_code": "cancelled_by_user",
                    "message": "任务已停止，拒绝启动新的工具动作",
                },
                ensure_ascii=False,
            )

        # 单个动作在下方扣费；批次按实际启动的动作逐条扣（R07）
        if not commands:
            budget_err = _consume_budget(1)
            if budget_err:
                return budget_err

        # 批量执行模式
        if commands:
            if not commands:
                return json.dumps({"error": "命令列表为空"}, ensure_ascii=False)

            cross = reject_cross_skill_batch(commands)
            if cross:
                logger.info("[execute_skill_script] 跨 Skill 拒绝批量: %s", cross)
                _emit_telemetry(
                    tool_name="execute_skill_script",
                    skill_name="batch",
                    success=False,
                    rejected_by_allowlist=True,
                    error_kind="cross_skill",
                    result_text=cross,
                )
                return cross

            plan_err, batch_plan = plan_batch_execution(
                commands, want_parallel=bool(parallel), channel=_channel()
            )
            if plan_err:
                _emit_telemetry(
                    tool_name="execute_skill_script",
                    skill_name="batch",
                    success=False,
                    error_kind="batch_plan_reject",
                    result_text=plan_err,
                )
                return plan_err
            commands = batch_plan.ordered_commands
            from orchestrator_integration.step_deps import (
                resolve_output_refs,
                validate_batch_deps,
            )
            from orchestrator_integration.tool_result import classify_skill_output

            dep_err = validate_batch_deps(commands)
            if dep_err:
                _emit_telemetry(
                    tool_name="execute_skill_script",
                    skill_name="batch",
                    success=False,
                    error_kind="batch_plan_reject",
                    result_text=dep_err,
                )
                return dep_err
            for note in batch_plan.notes:
                logger.info("[execute_skill_script] 批次策略: %s", note)

            for c in commands:
                if not isinstance(c, dict):
                    continue
                blocked = _assert_skill_allowed(c.get("skill_name"))
                if blocked:
                    logger.info("[execute_skill_script] 分区拒绝批量: %s", blocked)
                    _emit_telemetry(
                        tool_name="execute_skill_script",
                        skill_name=str(c.get("skill_name") or ""),
                        success=False,
                        rejected_by_allowlist=True,
                        error_kind="allowlist",
                        result_text=blocked,
                    )
                    return blocked
                catalog_block = catalog_reject_unregistered_write(
                    c.get("skill_name"), c.get("command")
                )
                if catalog_block:
                    logger.info(
                        "[execute_skill_script] Catalog 拒绝批量: %s", catalog_block
                    )
                    _emit_telemetry(
                        tool_name="execute_skill_script",
                        skill_name=str(c.get("skill_name") or ""),
                        success=False,
                        rejected_by_catalog=True,
                        error_kind="unregistered_write",
                        result_text=catalog_block,
                    )
                    return catalog_block
                priv = reject_zone_privilege_escalation(
                    zone_id=_zone_id(),
                    skill_name=c.get("skill_name"),
                    command=c.get("command"),
                )
                if priv:
                    logger.info("[execute_skill_script] 越权拒绝批量: %s", priv)
                    _emit_telemetry(
                        tool_name="execute_skill_script",
                        skill_name=str(c.get("skill_name") or ""),
                        success=False,
                        rejected_by_allowlist=True,
                        error_kind="privilege",
                        result_text=priv,
                    )
                    return priv

            mutating_cmds = [
                c
                for c in commands
                if isinstance(c, dict) and is_mutating_skill_command(c.get("command"))
            ]
            readonly_cmds = [
                c
                for c in commands
                if isinstance(c, dict) and not is_mutating_skill_command(c.get("command"))
            ]
            write_blocked = bool(mutating_cmds) and not _write_allowed(
                user_confirmed, user_write_approved
            )

            outputs_by_step: dict[str, dict] = {}

            def execute_single(idx: int, cmd: dict[str, str]) -> dict[str, object]:
                cmd_skill_name = cmd.get("skill_name")
                cmd_command = cmd.get("command")
                cmd_session_id = cmd.get("session_id")
                step_id = str(cmd.get("step_id") or "").strip()

                if _should_stop_execution():
                    return {
                        "index": idx,
                        "step_id": step_id or None,
                        "skill_name": cmd_skill_name,
                        "command": cmd_command,
                        "status": "cancelled",
                        "error": "任务已停止，跳过剩余动作",
                    }

                if not cmd_skill_name or not cmd_command:
                    return {
                        "index": idx,
                        "step_id": step_id or None,
                        "skill_name": cmd_skill_name,
                        "command": cmd_command,
                        "error": "缺少 skill_name 或 command",
                    }

                ref_err, resolved_cmd = resolve_output_refs(
                    str(cmd_command), outputs_by_step
                )
                if ref_err:
                    return {
                        "index": idx,
                        "step_id": step_id or None,
                        "skill_name": cmd_skill_name,
                        "command": cmd_command,
                        "status": "skipped",
                        "skip_reason": ref_err,
                        "error": ref_err,
                    }
                cmd_command = resolved_cmd

                redirect = prefer_url_reader_instead(cmd_skill_name, cmd_command)
                if redirect:
                    logger.info(
                        "[execute_skill_script] 拦截浏览器误用→url-reader skill=%s",
                        cmd_skill_name,
                    )
                    return {
                        "index": idx,
                        "step_id": step_id or None,
                        "skill_name": cmd_skill_name,
                        "command": cmd_command,
                        "result": redirect,
                    }

                budget_err = _consume_budget(1, prefer_reserved=True)
                if budget_err:
                    return {
                        "index": idx,
                        "step_id": step_id or None,
                        "skill_name": cmd_skill_name,
                        "command": cmd_command,
                        "status": "skipped",
                        "skip_reason": "budget_exhausted",
                        "error": budget_err,
                    }

                try:
                    result = _execute_single_skill_script(
                        cmd_skill_name, cmd_command, cmd_session_id
                    )
                    classified = classify_skill_output(
                        text=str(result or ""),
                        skill_name=str(cmd_skill_name or ""),
                        command=str(cmd_command or ""),
                    )
                    if step_id:
                        outputs_by_step[step_id] = {
                            "status": classified.status,
                            "data": classified.data,
                            "resource_id": classified.resource_id,
                            "resource_type": classified.resource_type,
                        }
                    return {
                        "index": idx,
                        "step_id": step_id or None,
                        "skill_name": cmd_skill_name,
                        "command": cmd_command,
                        "result": result,
                        "status": classified.status,
                    }
                except Exception as e:
                    if step_id:
                        outputs_by_step[step_id] = {"status": "failed", "data": {}}
                    return {
                        "index": idx,
                        "step_id": step_id or None,
                        "skill_name": cmd_skill_name,
                        "command": cmd_command,
                        "error": str(e),
                    }
                finally:
                    close_old_connections()

            # 写入未确认：先放行只读/执行类（如 execute_testcase），写入另等确认
            if write_blocked:
                executed_readonly: list[dict[str, object]] = []
                if readonly_cmds:
                    logger.info(
                        "[execute_skill_script] 批量写入未确认：先执行只读/执行 %s 条，挂起写入 %s 条",
                        len(readonly_cmds),
                        len(mutating_cmds),
                    )
                    for idx, cmd in enumerate(commands):
                        if not isinstance(cmd, dict):
                            continue
                        if is_mutating_skill_command(cmd.get("command")):
                            continue
                        executed_readonly.append(execute_single(idx, cmd))
                    _emit_telemetry(
                        tool_name="execute_skill_script",
                        skill_name="batch_readonly_then_confirm",
                        success=True,
                        error_kind="needs_confirmation_partial",
                    )
                else:
                    logger.info(
                        "[execute_skill_script] 批量写入未确认，拒绝执行 count=%s",
                        len(mutating_cmds),
                    )
                    _emit_telemetry(
                        tool_name="execute_skill_script",
                        skill_name="batch",
                        success=False,
                        error_kind="needs_confirmation",
                    )
                return _needs_write_confirmation_payload(
                    skill_name=None,
                    command=None,
                    pending_commands=mutating_cmds,
                    executed_readonly=executed_readonly or None,
                )

            run_parallel = bool(batch_plan.parallel)
            logger.info(
                f"[execute_skill_script] 批量模式: {len(commands)} 条命令, "
                f"parallel={run_parallel}, max_workers={max_workers}, "
                f"user_confirmed={user_confirmed}, notes={list(batch_plan.notes)}"
            )
            # FR07：批次预留额度；结束时释放未启动预留（不计实耗）
            _reserve_batch_budget(len(commands))

            results: list[Optional[dict[str, object]]] = [None] * len(commands)
            from orchestrator_integration.tool_result import (
                ToolResult,
                summarize_batch_statuses,
            )

            def _result_failed(item: Optional[dict[str, object]]) -> bool:
                if item is None:
                    return True
                if "error" in item:
                    return True
                text = str(item.get("result") or "")
                classified = classify_skill_output(
                    text=text,
                    skill_name=str(item.get("skill_name") or ""),
                    command=str(item.get("command") or ""),
                )
                return classified.status in {"failed", "unknown"}

            # R08/R09：按 depends_on / on_failure 调度；有依赖时并行改为拓扑就绪队列
            def _step_meta(cmd, idx: int) -> dict:
                if not isinstance(cmd, dict):
                    return {
                        "idx": idx,
                        "step_id": f"__idx_{idx}",
                        "depends_on": [],
                        "on_failure": "stop_dependents",
                    }
                sid = str(cmd.get("step_id") or "").strip() or f"__idx_{idx}"
                return {
                    "idx": idx,
                    "step_id": sid,
                    "depends_on": [str(d) for d in (cmd.get("depends_on") or []) if d],
                    "on_failure": str(cmd.get("on_failure") or "stop_dependents"),
                }

            metas = [_step_meta(cmd, idx) for idx, cmd in enumerate(commands)]
            id_to_meta = {m["step_id"]: m for m in metas}
            has_explicit_deps = any(m["depends_on"] for m in metas)
            if run_parallel and has_explicit_deps:
                run_parallel = False
                logger.info(
                    "[execute_skill_script] 存在 depends_on，已强制串行拓扑调度"
                )

            failed_ids: set[str] = set()
            succeeded_ids: set[str] = set()
            skipped_ids: set[str] = set()

            def _deps_ready(meta: dict) -> bool:
                for dep in meta["depends_on"]:
                    if dep in failed_ids or dep in skipped_ids:
                        return False
                    if dep not in succeeded_ids:
                        return False
                return True

            def _should_skip(meta: dict) -> Optional[str]:
                blocked = [
                    d
                    for d in meta["depends_on"]
                    if d in failed_ids or d in skipped_ids
                ]
                if not blocked:
                    return None
                # 依赖失败：默认跳过依赖方；来源步骤 on_failure=continue 不传播到无依赖方
                return f"dependency_failed:{','.join(blocked)}"

            if run_parallel:
                # 无显式依赖时可并行；仍按失败集合避免误启
                pending = set(range(len(commands)))
                with ThreadPoolExecutor(
                    max_workers=min(max_workers, len(commands))
                ) as executor:
                    while pending:
                        ready = [
                            i
                            for i in list(pending)
                            if _deps_ready(metas[i]) and not _should_skip(metas[i])
                        ]
                        skip_now = [
                            i for i in list(pending) if _should_skip(metas[i])
                        ]
                        for i in skip_now:
                            reason = _should_skip(metas[i]) or "dependency_failed"
                            results[i] = {
                                "index": i,
                                "step_id": metas[i]["step_id"],
                                "skill_name": commands[i].get("skill_name")
                                if isinstance(commands[i], dict)
                                else None,
                                "command": commands[i].get("command")
                                if isinstance(commands[i], dict)
                                else None,
                                "status": "skipped",
                                "skip_reason": reason,
                                "error": "前置步骤失败，已跳过",
                            }
                            skipped_ids.add(metas[i]["step_id"])
                            pending.discard(i)
                        if not ready:
                            # 死锁保护：剩余全部跳过
                            for i in list(pending):
                                results[i] = {
                                    "index": i,
                                    "step_id": metas[i]["step_id"],
                                    "status": "skipped",
                                    "skip_reason": "dependency_unresolved",
                                    "error": "依赖未就绪，已跳过",
                                }
                                skipped_ids.add(metas[i]["step_id"])
                                pending.discard(i)
                            break
                        future_to_idx = {
                            executor.submit(execute_single, i, commands[i]): i
                            for i in ready
                        }
                        for i in ready:
                            pending.discard(i)
                        for future in as_completed(future_to_idx):
                            idx = future_to_idx[future]
                            results[idx] = future.result()
                            sid = metas[idx]["step_id"]
                            if _result_failed(results[idx]):
                                failed_ids.add(sid)
                            else:
                                succeeded_ids.add(sid)
            else:
                # R08：按依赖图传播；on_failure=continue 不阻断无 depends_on 的独立步骤
                stop_rest = False
                for idx, cmd in enumerate(commands):
                    meta = metas[idx]
                    sid = meta["step_id"]
                    skip_reason = _should_skip(meta)
                    if stop_rest:
                        results[idx] = {
                            "index": idx,
                            "step_id": sid,
                            "skill_name": cmd.get("skill_name")
                            if isinstance(cmd, dict)
                            else None,
                            "command": cmd.get("command")
                            if isinstance(cmd, dict)
                            else None,
                            "status": "skipped",
                            "skip_reason": "stop_rest",
                            "error": "前置步骤失败，已跳过剩余动作",
                        }
                        skipped_ids.add(sid)
                        continue
                    if skip_reason:
                        results[idx] = {
                            "index": idx,
                            "step_id": sid,
                            "skill_name": cmd.get("skill_name")
                            if isinstance(cmd, dict)
                            else None,
                            "command": cmd.get("command")
                            if isinstance(cmd, dict)
                            else None,
                            "status": "skipped",
                            "skip_reason": skip_reason,
                            "error": "前置步骤失败，已跳过",
                        }
                        skipped_ids.add(sid)
                        continue

                    results[idx] = execute_single(idx, cmd)
                    if _result_failed(results[idx]):
                        failed_ids.add(sid)
                        if meta["on_failure"] == "stop_rest":
                            stop_rest = True
                    else:
                        status = str((results[idx] or {}).get("status") or "")
                        if status == "skipped":
                            skipped_ids.add(sid)
                        else:
                            succeeded_ids.add(sid)

            tool_results: list[ToolResult] = []
            for r in results:
                if r is None:
                    continue
                if r.get("status") == "skipped":
                    tool_results.append(
                        ToolResult(
                            status="skipped",
                            operation_id=f"skip-{r.get('index')}",
                            summary=str(r.get("skip_reason") or "skipped"),
                        )
                    )
                    continue
                if "error" in r and "result" not in r:
                    tool_results.append(
                        ToolResult(
                            status="failed",
                            operation_id=f"err-{r.get('index')}",
                            error_code="batch_item_error",
                            summary=str(r.get("error") or "error"),
                        )
                    )
                    continue
                tool_results.append(
                    classify_skill_output(
                        text=str(r.get("result") or ""),
                        skill_name=str(r.get("skill_name") or ""),
                        command=str(r.get("command") or ""),
                    )
                )
            batch_summary = summarize_batch_statuses(tool_results)
            success_count = int(batch_summary["counts"].get("succeeded", 0))
            error_count = int(
                batch_summary["counts"].get("failed", 0)
            ) + int(batch_summary["counts"].get("unknown", 0))
            skipped_count = int(batch_summary["counts"].get("skipped", 0))

            logger.info(
                f"[execute_skill_script] 批量完成: {success_count} 成功, "
                f"{error_count} 失败, {skipped_count} 跳过"
            )
            out = json.dumps(
                {
                    "summary": {
                        "total": len(commands),
                        "success": success_count,
                        "error": error_count,
                        "skipped": skipped_count,
                        "all_success": batch_summary.get("ok", False),
                        "message": batch_summary.get("message"),
                        "parallel": run_parallel,
                        "user_confirmed": user_confirmed,
                        "policy_notes": list(batch_plan.notes),
                    },
                    "results": [r for r in results if r is not None],
                },
                ensure_ascii=False,
                indent=2,
            )
            _record_result_snippet(out)
            _emit_telemetry(
                tool_name="execute_skill_script",
                skill_name="batch",
                success=bool(batch_summary.get("ok")),
                error_kind="" if batch_summary.get("ok") else "batch_partial_error",
                result_text=out[:800],
            )
            _release_batch_reservation()
            return out

        # 单个执行模式
        if not skill_name or not command:
            return "错误: 单个执行模式需要提供 skill_name 和 command 参数"

        blocked = _assert_skill_allowed(skill_name)
        if blocked:
            logger.info("[execute_skill_script] 分区拒绝: %s", blocked)
            _emit_telemetry(
                tool_name="execute_skill_script",
                skill_name=skill_name,
                success=False,
                rejected_by_allowlist=True,
                error_kind="allowlist",
                result_text=blocked,
            )
            return blocked

        catalog_block = catalog_reject_unregistered_write(skill_name, command)
        if catalog_block:
            logger.info("[execute_skill_script] Catalog 拒绝: %s", catalog_block)
            _emit_telemetry(
                tool_name="execute_skill_script",
                skill_name=skill_name,
                success=False,
                rejected_by_catalog=True,
                error_kind="unregistered_write",
                result_text=catalog_block,
            )
            return catalog_block

        priv = reject_zone_privilege_escalation(
            zone_id=_zone_id(),
            skill_name=skill_name,
            command=command,
        )
        if priv:
            logger.info("[execute_skill_script] 越权拒绝: %s", priv)
            _emit_telemetry(
                tool_name="execute_skill_script",
                skill_name=skill_name,
                success=False,
                rejected_by_allowlist=True,
                error_kind="privilege",
                result_text=priv,
            )
            return priv

        from orchestrator_integration.execution_policy import command_side_effect
        from orchestrator_integration.task_commission import CHANNEL_LIGHT

        side = command_side_effect(command, skill_name)
        if side == "irreversible" and _channel() == CHANNEL_LIGHT:
            msg = (
                "轻量通道禁止 irreversible 动作；请走重量通道/草稿确认后再执行。"
            )
            logger.info(
                "[execute_skill_script] 轻量通道拒 irreversible skill=%s", skill_name
            )
            _emit_telemetry(
                tool_name="execute_skill_script",
                skill_name=skill_name,
                success=False,
                error_kind="light_channel_irreversible",
                result_text=msg,
            )
            return msg

        if is_mutating_skill_command(command) and not _write_allowed(
            user_confirmed, user_write_approved
        ):
            logger.info(
                "[execute_skill_script] 写入未确认，拒绝执行 skill=%s", skill_name
            )
            _emit_telemetry(
                tool_name="execute_skill_script",
                skill_name=skill_name,
                success=False,
                error_kind="needs_confirmation",
            )
            return _needs_write_confirmation_payload(
                skill_name=skill_name,
                command=command,
            )

        redirect = prefer_url_reader_instead(skill_name, command)
        if redirect:
            logger.info(
                "[execute_skill_script] 拦截浏览器误用→url-reader skill=%s",
                skill_name,
            )
            _emit_telemetry(
                tool_name="execute_skill_script",
                skill_name=skill_name,
                success=False,
                error_kind="prefer_url_reader",
                result_text=redirect,
            )
            return redirect

        with TelemetryTimer() as timer:
            try:
                result = _execute_single_skill_script(skill_name, command, session_id)
                ok = not (
                    isinstance(result, str)
                    and result.startswith("错误")
                )
                # 轻量通道：失败再试 1 次
                from orchestrator_integration.task_commission import CHANNEL_LIGHT

                if not ok and _channel() == CHANNEL_LIGHT:
                    logger.info(
                        "[execute_skill_script] 轻量通道失败，重试 1 次 skill=%s",
                        skill_name,
                    )
                    result = _execute_single_skill_script(
                        skill_name, command, session_id
                    )
                    ok = not (
                        isinstance(result, str) and result.startswith("错误")
                    )
                _record_result_snippet(str(result))
                _emit_telemetry(
                    tool_name="execute_skill_script",
                    skill_name=skill_name,
                    success=ok,
                    duration_ms=timer.elapsed_ms,
                    error_kind="" if ok else "execute_error",
                    result_text=str(result)[:800],
                )
                return result
            except Exception as exc:
                _emit_telemetry(
                    tool_name="execute_skill_script",
                    skill_name=skill_name,
                    success=False,
                    duration_ms=timer.elapsed_ms,
                    error_kind="exception",
                    result_text=str(exc)[:800],
                )
                raise exc

    if not allow_execute:
        return [read_skill_content]
    return [read_skill_content, execute_skill_script]
