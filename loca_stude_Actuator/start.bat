@echo off
chcp 65001 >nul
title loca_stude Actuator
cd /d "%~dp0"

if not exist config.toml (
    copy config.example.toml config.toml >nul
    echo 已生成 config.toml，请先修改其中的 api_username / api_password 后重新运行。
    notepad config.toml
    pause
    exit /b 1
)

if not exist .venv (
    echo 首次运行：创建虚拟环境并安装依赖...
    python -m venv .venv || goto :err
    call .venv\Scripts\activate.bat
    python -m pip install -r requirements.txt || goto :err
) else (
    call .venv\Scripts\activate.bat
)

if "%ACTUATOR_ID%"=="" set ACTUATOR_ID=actuator-%COMPUTERNAME%
python main.py --id %ACTUATOR_ID% %*
pause
exit /b 0

:err
echo 启动失败，请检查 Python 3.11+ 是否已安装并加入 PATH。
pause
exit /b 1
