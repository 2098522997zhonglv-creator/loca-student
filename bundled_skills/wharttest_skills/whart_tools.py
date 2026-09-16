# -*- coding: utf-8 -*-
import sys
import io

# Windows 终端 UTF-8 输出
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import argparse
import json
import mimetypes
import os
import time
import requests
from pathlib import Path
from urllib.parse import unquote

# 加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

# 配置（运行时读取环境变量，避免 import 时固化导致父进程注入的 Key 失效）
_DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_DEFAULT_API_KEY = "wharttest-default-mcp-key-2025"
IMAGE_MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
}


def _base_url() -> str:
    return (os.environ.get("WHARTTEST_BACKEND_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return (os.environ.get("WHARTTEST_API_KEY") or _DEFAULT_API_KEY).strip()


def _headers() -> dict:
    return {
        "accept": "application/json, text/plain,*/*",
        "X-API-Key": _api_key(),
    }


# 兼容旧代码/测试中对模块级常量的引用
BASE_URL = _base_url()
API_KEY = _api_key()
HEADERS = _headers()


def _response_json(resp):
    """解析 JSON 响应，兼容空响应与纯文本错误。"""
    try:
        return resp.json()
    except Exception:
        text = getattr(resp, 'text', '')
        return {"message": text} if text else {}


def _parse_csv_ints(value, field_name="ids"):
    if value in (None, ''):
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_text = str(value).strip()
        try:
            loaded = json.loads(raw_text)
            raw_items = loaded if isinstance(loaded, list) else [loaded]
        except json.JSONDecodeError:
            raw_items = [item.strip() for item in raw_text.split(',') if item.strip()]

    result = []
    for item in raw_items:
        try:
            int_value = int(item)
        except (TypeError, ValueError):
            raise ValueError(f"{field_name} 包含非法 ID: {item}")
        if int_value not in result:
            result.append(int_value)
    return result


def _parse_optional_bool(value, field_name):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ('1', 'true', 'yes', 'y', 'on'):
        return True
    if normalized in ('0', 'false', 'no', 'n', 'off'):
        return False
    raise ValueError(f"{field_name} 必须是 true/false")


def _resolve_local_file_path(file_path: str):
    normalized_path = (file_path or '').strip()
    if not normalized_path:
        return normalized_path
    if os.path.exists(normalized_path):
        return normalized_path
    if os.sep not in normalized_path and '/' not in normalized_path:
        cwd_path = os.path.join(os.getcwd(), normalized_path)
        if os.path.exists(cwd_path):
            return cwd_path
    return normalized_path


def _extract_filename_from_content_disposition(content_disposition: str):
    if not content_disposition:
        return ''
    for part in content_disposition.split(';'):
        item = part.strip()
        if item.lower().startswith("filename*="):
            value = item.split('=', 1)[1].strip().strip('"')
            if "''" in value:
                value = value.split("''", 1)[1]
            return os.path.basename(unquote(value))
        if item.lower().startswith("filename="):
            value = item.split('=', 1)[1].strip().strip('"')
            return os.path.basename(unquote(value))
    return ''


def _save_response_to_file(resp, output_path: str):
    target_path = os.path.abspath(output_path)
    target_dir = os.path.dirname(target_path)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    with open(target_path, 'wb') as f:
        if hasattr(resp, 'iter_content'):
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        else:
            f.write(getattr(resp, 'content', b''))
    return target_path


def _extract_tree(nodes_list, id_key, name_key):
    """递归提取树形结构"""
    result = []
    if not isinstance(nodes_list, list):
        return result
    for node in nodes_list:
        if isinstance(node, dict):
            result.append({id_key: node.get("id"), name_key: node.get("name")})
            children = node.get("children")
            if isinstance(children, list):
                result.extend(_extract_tree(children, id_key, name_key))
    return result


def get_projects():
    """获取项目列表"""
    url = f"{_base_url()}/api/projects/"
    try:
        resp = requests.get(url, headers=_headers())
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return _extract_tree(data, "project_id", "project_name")
    except Exception as e:
        return {"error": str(e)}


def list_files(project_id: int, page: int = 1, page_size: int = 20, search: str = None,
               status: str = None, extension: str = None, mime_type: str = None,
               ordering: str = "-created_at"):
    """获取项目文件列表"""
    url = f"{_base_url()}/api/projects/{project_id}/files/"
    params = {
        "page": page,
        "page_size": page_size,
    }
    if search:
        params["search"] = search
    if status:
        params["status"] = status
    if extension:
        params["extension"] = extension
    if mime_type:
        params["mime_type"] = mime_type
    if ordering:
        params["ordering"] = ordering
    try:
        resp = requests.get(url, headers=_headers(), params=params)
        resp.raise_for_status()
        return _response_json(resp)
    except Exception as e:
        return {"error": str(e)}


def get_file_detail(project_id: int, file_id: int):
    """获取文件详情"""
    url = f"{_base_url()}/api/projects/{project_id}/files/{file_id}/"
    try:
        resp = requests.get(url, headers=_headers())
        resp.raise_for_status()
        return _response_json(resp)
    except Exception as e:
        return {"error": str(e)}


def upload_files(project_id: int, file_paths: str):
    """上传一个或多个项目文件"""
    paths = [p.strip() for p in (file_paths or '').split(',') if p.strip()]
    if not paths:
        return {"error": "未提供文件路径"}

    resolved_paths = []
    for fp in paths:
        resolved_path = _resolve_local_file_path(fp)
        if not os.path.exists(resolved_path):
            return {"error": f"文件不存在: {fp}"}
        if not os.path.isfile(resolved_path):
            return {"error": f"不是有效文件: {fp}"}
        resolved_paths.append(resolved_path)

    url = f"{_base_url()}/api/projects/{project_id}/files/"
    file_handles = []
    try:
        files = []
        for fp in resolved_paths:
            content_type = mimetypes.guess_type(fp)[0] or 'application/octet-stream'
            f = open(fp, 'rb')
            file_handles.append(f)
            files.append(('files', (os.path.basename(fp), f, content_type)))

        resp = requests.post(url, headers=_headers(), files=files)
        resp.raise_for_status()
        return _response_json(resp)
    except Exception as e:
        return {"error": str(e)}
    finally:
        for f in file_handles:
            try:
                f.close()
            except Exception:
                pass


def validate_files(project_id: int, file_ids):
    """校验 file_ids 是否存在、属于项目且状态可用"""
    try:
        parsed_ids = _parse_csv_ints(file_ids, "file_ids")
    except ValueError as e:
        return {"error": str(e)}
    url = f"{_base_url()}/api/projects/{project_id}/files/validate/"
    try:
        resp = requests.post(url, headers=_headers(), json={"file_ids": parsed_ids})
        resp.raise_for_status()
        return _response_json(resp)
    except Exception as e:
        return {"error": str(e)}


def get_file_references(project_id: int, file_id: int):
    """获取文件引用详情"""
    url = f"{_base_url()}/api/projects/{project_id}/files/{file_id}/references/"
    try:
        resp = requests.get(url, headers=_headers())
        resp.raise_for_status()
        return _response_json(resp)
    except Exception as e:
        return {"error": str(e)}


def delete_file(project_id: int, file_id: int):
    """删除项目文件；被引用文件由后端执行软删除"""
    url = f"{_base_url()}/api/projects/{project_id}/files/{file_id}/"
    try:
        resp = requests.delete(url, headers=_headers())
        resp.raise_for_status()
        if getattr(resp, 'status_code', None) == 204:
            return {"success": True, "message": f"文件ID {file_id} 删除成功"}
        data = _response_json(resp)
        if data:
            return data
        return {"success": True, "message": f"文件ID {file_id} 删除成功"}
    except Exception as e:
        return {"error": str(e)}


def get_file_settings(project_id: int):
    """获取项目文件管理设置"""
    url = f"{_base_url()}/api/projects/{project_id}/files/settings/"
    try:
        resp = requests.get(url, headers=_headers())
        resp.raise_for_status()
        return _response_json(resp)
    except Exception as e:
        return {"error": str(e)}


def update_file_settings(project_id: int, auto_delete_on_unbind=None, auto_delete_zero_refs=None):
    """更新项目文件管理设置"""
    try:
        on_unbind = _parse_optional_bool(auto_delete_on_unbind, "auto_delete_on_unbind")
        zero_refs = _parse_optional_bool(auto_delete_zero_refs, "auto_delete_zero_refs")
    except ValueError as e:
        return {"error": str(e)}

    data = {}
    if on_unbind is not None:
        data["auto_delete_on_unbind"] = on_unbind
    if zero_refs is not None:
        data["auto_delete_zero_refs"] = zero_refs
    if not data:
        return {"error": "至少需要提供 auto_delete_on_unbind 或 auto_delete_zero_refs"}

    url = f"{_base_url()}/api/projects/{project_id}/files/settings/"
    try:
        resp = requests.post(url, headers=_headers(), json=data)
        resp.raise_for_status()
        return _response_json(resp)
    except Exception as e:
        return {"error": str(e)}


def cleanup_unreferenced_files(project_id: int):
    """清理项目内无引用文件"""
    url = f"{_base_url()}/api/projects/{project_id}/files/cleanup-unreferenced/"
    try:
        resp = requests.post(url, headers=_headers())
        resp.raise_for_status()
        return _response_json(resp)
    except Exception as e:
        return {"error": str(e)}


def download_file(project_id: int, file_id: int, output_path: str = None, output_dir: str = None):
    """下载文件到本地"""
    url = f"{_base_url()}/api/projects/{project_id}/files/{file_id}/download/"
    try:
        resp = requests.get(url, headers=_headers(), stream=True)
        resp.raise_for_status()
        filename = _extract_filename_from_content_disposition(
            getattr(resp, 'headers', {}).get('Content-Disposition', '')
        ) or f"file_{file_id}"
        target_path = output_path or os.path.join(output_dir or os.getcwd(), filename)
        saved_path = _save_response_to_file(resp, target_path)
        return {
            "success": True,
            "message": "文件下载成功",
            "file_id": file_id,
            "output_path": saved_path,
            "content_type": getattr(resp, 'headers', {}).get('Content-Type', ''),
        }
    except Exception as e:
        return {"error": str(e)}


def preview_file(project_id: int, file_id: int, output_path: str = None):
    """预览文件；文本直接返回，二进制可保存到 output_path"""
    url = f"{_base_url()}/api/projects/{project_id}/files/{file_id}/preview/"
    try:
        resp = requests.get(url, headers=_headers(), stream=bool(output_path))
        resp.raise_for_status()
        content_type = getattr(resp, 'headers', {}).get('Content-Type', '')
        if output_path:
            saved_path = _save_response_to_file(resp, output_path)
            return {
                "success": True,
                "message": "文件预览内容已保存",
                "file_id": file_id,
                "output_path": saved_path,
                "content_type": content_type,
            }
        if (
            content_type.startswith('text/')
            or 'json' in content_type
            or 'xml' in content_type
            or 'javascript' in content_type
        ):
            return {
                "file_id": file_id,
                "content_type": content_type,
                "content": getattr(resp, 'text', ''),
            }
        return {
            "file_id": file_id,
            "content_type": content_type,
            "size": len(getattr(resp, 'content', b'')),
            "message": "预览内容为二进制，请使用 --output_path 保存或使用 download_file 下载",
        }
    except Exception as e:
        return {"error": str(e)}


# Action 路由
ACTIONS = {
    "get_projects": lambda args: get_projects(),
    "list_files": lambda args: list_files(
        args.project_id, args.page, args.page_size, args.search, args.status,
        args.extension, args.mime_type, args.ordering
    ),
    "get_file_detail": lambda args: get_file_detail(args.project_id, args.file_id),
    "upload_file": lambda args: upload_files(args.project_id, args.file_path),
    "upload_files": lambda args: upload_files(args.project_id, args.file_paths),
    "validate_files": lambda args: validate_files(args.project_id, args.file_ids),
    "get_file_references": lambda args: get_file_references(args.project_id, args.file_id),
    "delete_file": lambda args: delete_file(args.project_id, args.file_id),
    "get_file_settings": lambda args: get_file_settings(args.project_id),
    "update_file_settings": lambda args: update_file_settings(
        args.project_id, args.auto_delete_on_unbind, args.auto_delete_zero_refs
    ),
    "cleanup_unreferenced_files": lambda args: cleanup_unreferenced_files(args.project_id),
    "download_file": lambda args: download_file(
        args.project_id, args.file_id, args.output_path, args.output_dir
    ),
    "preview_file": lambda args: preview_file(args.project_id, args.file_id, args.output_path),
}


def main():
    parser = argparse.ArgumentParser(description="本地知识中心项目与文件管理工具")
    parser.add_argument("--action", required=True, choices=ACTIONS.keys(), help="要执行的操作")
    parser.add_argument("--project_id", type=int, help="项目ID")
    parser.add_argument("--file_path", help="文件路径（单个上传）")
    parser.add_argument("--file_paths", help="文件路径列表（批量上传，逗号分隔）")
    parser.add_argument("--file_id", type=int, help="文件ID")
    parser.add_argument("--file_ids", help="文件ID列表（JSON数组或逗号分隔）")
    parser.add_argument("--page", type=int, default=1, help="页码")
    parser.add_argument("--page_size", type=int, default=20, help="每页数量")
    parser.add_argument("--search", help="搜索关键词")
    parser.add_argument("--status", help="文件状态过滤 (available/processing/broken/deleted)")
    parser.add_argument("--extension", help="文件扩展名过滤，如 .pdf")
    parser.add_argument("--mime_type", help="MIME 类型过滤")
    parser.add_argument("--ordering", default="-created_at", help="排序字段，如 -created_at/size/original_name")
    parser.add_argument("--output_path", help="下载或预览保存路径")
    parser.add_argument("--output_dir", help="下载保存目录")
    parser.add_argument("--auto_delete_on_unbind", help="解绑时自动删除无引用文件 (true/false)")
    parser.add_argument("--auto_delete_zero_refs", help="引用为0时自动删除 (true/false)")

    args = parser.parse_args()
    result = ACTIONS[args.action](args)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # 如果结果包含 error 字段，返回非零退出码
    if isinstance(result, dict) and "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
