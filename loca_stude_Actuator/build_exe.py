"""
WHartTest Actuator 打包脚本

使用方法:
    uv run python build_exe.py

依赖:
    pip install pyinstaller

生成文件:
    dist/WHartTest_Actuator.exe
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def clean_build():
    """清理之前的构建文件"""
    dirs_to_clean = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_clean:
        dir_path = Path(dir_name)
        if dir_path.exists():
            print(f"清理目录: {dir_path}")
            shutil.rmtree(dir_path)
    
    # 清理 .pyc 文件
    for pyc_file in Path('.').rglob('*.pyc'):
        pyc_file.unlink()


def build():
    """执行打包"""
    print("=" * 50)
    print("WHartTest Actuator 打包工具")
    print("=" * 50)
    
    # 检查 PyInstaller 是否安装
    try:
        import PyInstaller
        print(f"PyInstaller 版本: {PyInstaller.__version__}")
    except ImportError:
        print("错误: 未安装 PyInstaller")
        print("请运行: pip install pyinstaller")
        sys.exit(1)
    
    # 清理之前的构建
    print("\n[1/3] 清理旧的构建文件...")
    clean_build()
    
    # 执行打包
    print("\n[2/3] 开始打包...")
    result = subprocess.run(
        [sys.executable, '-m', 'PyInstaller', 'actuator.spec', '--noconfirm'],
        capture_output=False
    )
    
    if result.returncode != 0:
        print("打包失败!")
        sys.exit(1)
    
    print("\n[3/3] 检查输出文件...")
    exe_path = Path('dist/WHartTest_Actuator.exe')
    if not exe_path.exists():
        print("打包失败: 未找到 dist/WHartTest_Actuator.exe")
        sys.exit(1)
    
    print("\n" + "=" * 50)
    print("打包完成!")
    print(f"输出文件: {exe_path.absolute()}")
    print("\n使用说明:")
    print("  1. 将 dist/WHartTest_Actuator.exe 发给客户")
    print("  2. 客户双击运行 exe")
    print("  3. 首次启动会在 exe 同级目录生成 config.toml、data/、browsers/")
    print("\n首次运行说明:")
    print("  - 首次运行会自动下载 Chromium 浏览器")
    print("  - 浏览器会下载到 exe 同级 browsers/ 目录")
    print("  - 后续运行无需重复下载")
    print("=" * 50)


def create_launcher(dist_dir: Path):
    """创建启动脚本"""
    # Windows 批处理启动脚本
    bat_content = '''@echo off
chcp 65001 >nul
title WHartTest Actuator

echo ========================================
echo   WHartTest UI 自动化执行器
echo ========================================
echo.

REM 检查是否存在配置文件
if not exist "config.toml" (
    echo [警告] 未找到配置文件，使用默认配置
    echo 请编辑 config.toml 配置服务器地址
    echo.
)

REM 启动执行器（GUI 模式）
echo 启动执行器...
WHartTest_Actuator.exe --gui

echo.
echo 执行器已退出
pause
'''
    
    (dist_dir / 'start.bat').write_text(bat_content, encoding='utf-8')
    print("  创建: start.bat 启动脚本")
    
    # 无 GUI 启动脚本
    bat_nogui_content = '''@echo off
chcp 65001 >nul
title WHartTest Actuator (无GUI模式)

echo ========================================
echo   WHartTest UI 自动化执行器 (无GUI模式)
echo ========================================
echo.

REM 启动执行器（无 GUI 模式，使用配置文件中的账号密码）
WHartTest_Actuator.exe --no-gui

echo.
echo 执行器已退出
pause
'''
    
    (dist_dir / 'start_no_gui.bat').write_text(bat_nogui_content, encoding='utf-8')
    print("  创建: start_no_gui.bat 无GUI启动脚本")


if __name__ == '__main__':
    os.chdir(Path(__file__).parent)
    build()
