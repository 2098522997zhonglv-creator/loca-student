# Windows 物理机自动更新（方案 A）

Mac / 开发机 `git push` 到 `main` 后，Windows 通过**计划任务**定时执行：

1. `git pull`
2. 有依赖变更则 `pip install`
3. 有前端变更则 `npm run build`
4. 有代码变更则重启 Django；若服务挂了也会自动拉起

不依赖 GitHub Actions SSH，适合局域网机器 `192.168.32.138`。

## 一次性设置

在 Windows 上打开 **PowerShell**（建议用能成功 `git pull` 的同一用户）：

```powershell
cd E:\loca_student
git pull origin main

# 如有需要，改 Python 路径
notepad scripts\windows\config.ps1
```

`config.ps1` 默认：

```powershell
$script:PythonExe = "D:\conda\envs\loca_stude\python.exe"
$script:BindHost = "0.0.0.0"
$script:Port = 8000
```

先手动跑通一次：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\loca_student\scripts\windows\Update-And-Restart.ps1
```

浏览器访问：http://192.168.32.138:8000

注册每 5 分钟自动执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\loca_student\scripts\windows\Register-AutoUpdateTask.ps1
```

## 常用命令

```powershell
# 仅启动
powershell -NoProfile -ExecutionPolicy Bypass -File E:\loca_student\scripts\windows\Start-KnowledgeCenter.ps1

# 仅停止
powershell -NoProfile -ExecutionPolicy Bypass -File E:\loca_student\scripts\windows\Stop-KnowledgeCenter.ps1

# 立刻拉取并更新
powershell -NoProfile -ExecutionPolicy Bypass -File E:\loca_student\scripts\windows\Update-And-Restart.ps1
```

## 日志

- `E:\loca_student\data\logs\auto_update.log` — 拉取/构建/重启记录  
- `E:\loca_student\data\logs\django.out.log` / `django.err.log` — 服务输出  

## 注意

1. 计划任务用户必须已保存 GitHub HTTPS 凭据（你平时能 `git pull` 的那个账号）。
2. 机器需已安装 Git、Node.js（或 conda 环境里有 npm）、以及 conda 环境 `loca_stude`。
3. 更新后若只改了后端 Python，会重启服务；只改文档也可能触发重启（任意 commit 变化都会重启）。
4. 不要用旧的 GitHub Actions `deploy.yml`（已改为说明性 workflow，不再误 SSH）。
