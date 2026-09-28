import os
from pathlib import Path, PurePosixPath


TRUE_VALUES = {'1', 'true', 'yes', 'on'}
FALSE_VALUES = {'0', 'false', 'no', 'off'}


def parse_bool_env(value: str | None) -> bool | None:
    """解析环境变量中的布尔值。"""
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return None


def is_running_in_container() -> bool:
    """检测当前是否运行在容器环境中。"""
    explicit = parse_bool_env(os.environ.get('WHARTTEST_ACTUATOR_DOCKER'))
    if explicit is not None:
        return explicit

    return (
        Path('/.dockerenv').exists()
        or os.environ.get('container') == 'docker'
        or bool(os.environ.get('KUBERNETES_SERVICE_HOST'))
    )


def has_display_server() -> bool:
    """检测是否存在可用的图形显示环境。"""
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def should_force_headless() -> bool:
    """在容器内且无显示服务时，应强制使用无头模式。"""
    return is_running_in_container() and not has_display_server()


def _normalize_posix_path(value: str) -> str:
    return value.replace('\\', '/')


def _is_relative_to(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_runtime_file_path(file_path: str) -> str:
    """将后端下发的文件路径转换为当前执行器可访问的路径。

    后端本地运行、执行器容器运行时，FileField.path 会是宿主机/WSL
    绝对路径，例如 /home/.../wharttest/data/media/xxx；容器内同一目录
    通过 compose 挂载在 /app/data。这里仅在容器模式下做路径映射。
    """
    if not file_path or not is_running_in_container():
        return file_path

    if os.path.exists(file_path):
        return file_path

    container_data_dir = os.environ.get('WHARTTEST_ACTUATOR_CONTAINER_DATA_DIR', '/app/data')
    host_data_dir = os.environ.get('WHARTTEST_ACTUATOR_HOST_DATA_DIR', '')

    normalized = _normalize_posix_path(file_path)
    source = PurePosixPath(normalized)
    target_root = PurePosixPath(_normalize_posix_path(container_data_dir))

    if host_data_dir:
        host_root = PurePosixPath(_normalize_posix_path(host_data_dir))
        if _is_relative_to(source, host_root):
            return str(target_root / source.relative_to(host_root))

    parts = source.parts
    if 'data' in parts:
        data_index = parts.index('data')
        suffix_parts = parts[data_index + 1:]
        if not suffix_parts:
            return str(target_root)
        return str(target_root / PurePosixPath(*suffix_parts))

    return file_path
