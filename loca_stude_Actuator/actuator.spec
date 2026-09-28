# -*- mode: python ; coding: utf-8 -*-
"""
WHartTest Actuator PyInstaller 打包配置

使用方法:
    pyinstaller actuator.spec

生成文件:
    dist/WHartTest_Actuator.exe  - 单文件主程序
"""

import sys
import glob
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# 收集 playwright 相关文件
playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all('playwright')

# 收集 pydantic 相关文件
pydantic_datas, pydantic_binaries, pydantic_hiddenimports = collect_all('pydantic')
pydantic_core_datas, pydantic_core_binaries, pydantic_core_hiddenimports = collect_all('pydantic_core')

# 收集 CustomTkinter（GUI 功能）
customtkinter_datas, customtkinter_binaries, customtkinter_hiddenimports = collect_all('customtkinter')

# 收集 httpx/httpcore 相关（包含 mypyc 编译的模块）
httpx_datas, httpx_binaries, httpx_hiddenimports = collect_all('httpx')
httpcore_datas, httpcore_binaries, httpcore_hiddenimports = collect_all('httpcore')

# 手动收集 site-packages 根目录的 mypyc 编译模块
import site
site_packages_list = site.getsitepackages()
mypyc_binaries = []
for sp in site_packages_list:
    for pyd in glob.glob(f"{sp}/*mypyc*.pyd"):
        mypyc_binaries.append((pyd, '.'))
    for pyd in glob.glob(f"{sp}/*mypyc*.so"):
        mypyc_binaries.append((pyd, '.'))

spec_dir = Path(SPECPATH)
logo_candidates = [
    spec_dir / 'data' / 'WHartTest.png',
    spec_dir.parent / 'WHartTest_Vue' / 'public' / 'WHartTest.png',
]
logo_datas = []
for logo_path in logo_candidates:
    if logo_path.exists():
        logo_datas.append((str(logo_path), 'data'))
        break

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=(
        playwright_binaries + 
        pydantic_binaries + 
        pydantic_core_binaries + 
        customtkinter_binaries +
        httpx_binaries +
        httpcore_binaries +
        mypyc_binaries
    ),
    datas=[
        ('config.example.toml', '.'),
        *logo_datas,
        *playwright_datas,
        *pydantic_datas,
        *pydantic_core_datas,
        *customtkinter_datas,
        *httpx_datas,
        *httpcore_datas,
    ],
    hiddenimports=[
        # Playwright 相关
        'playwright',
        'playwright.async_api',
        'playwright.sync_api',
        'playwright._impl',
        'playwright._impl._driver',
        *playwright_hiddenimports,
        
        # Pydantic 相关
        'pydantic',
        'pydantic_core',
        *pydantic_hiddenimports,
        *pydantic_core_hiddenimports,
        
        # CustomTkinter / tkinter GUI
        'customtkinter',
        'tkinter',
        '_tkinter',
        *customtkinter_hiddenimports,
        
        # HTTP 相关
        *httpx_hiddenimports,
        *httpcore_hiddenimports,
        
        # 网络相关
        'websockets',
        'websockets.legacy',
        'websockets.legacy.client',
        'anyio',
        'anyio._backends._asyncio',
        'h11',
        'sniffio',
        
        # TOML 相关
        'tomli',
        'tomllib',
        'tomli_w',
        
        # 本地模块
        'browser_installer',
        'websocket_client',
        'consumer',
        'executor',
        'models',
        'data_processor',
        'gui',
        'gui.login_window',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大型模块
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    exclude_binaries=False,
    name='WHartTest_Actuator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
