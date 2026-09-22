# Creator Agent Studio —— Windows 任务计划安装 / 卸载 / 状态查询
#
# 为什么用任务计划而不是 Windows 服务：python.exe 不是服务程序，注册为服务
# 需要第三方包装器（WinSW / NSSM，都得额外下载）；任务计划是系统内置能力，
# 零依赖、可离线 —— 与本项目「不填一个 Key 也能完整跑通」的取向一致。
# 取舍如实说明：没有服务控制台的 start/stop 语义，开机自启 + 失败自动重启
# （3 次）由任务计划提供，够自托管场景使用。
#
# 用法（**必须以管理员身份运行 PowerShell**）：
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-task.ps1 install
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-task.ps1 install -PythonExe "C:\Python312\python.exe"
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-task.ps1 status
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install-task.ps1 uninstall
#
# 说明：
# * 任务以 SYSTEM 账户运行，工作目录与日志由 run-server.ps1 固定；
#   日志在 data\logs\service.log（超过 10MB 轮转为 .old）。
# * 首次部署前先装好依赖并构建前端（服务跑的是后端进程，页面来自 dist\）：
#   pip install -r requirements.txt  然后  npm ci && npm run build
# * 应用默认只监听 127.0.0.1（仅本机）。需要局域网直连时在项目根 .env 设
#   HOST=0.0.0.0 并放行防火墙（见 DEPLOYMENT.md「Windows」）；暴露前务必
#   设置 CREATOR_API_TOKENS。

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "uninstall", "status")]
    [string]$Action = "install",

    # python.exe 全路径；缺省时按 .venv → PATH 顺序探测
    [string]$PythonExe = "",

    [string]$TaskName = "creator-agent-studio"
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runner = Join-Path $PSScriptRoot "run-server.ps1"

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "注册 SYSTEM 计划任务需要管理员权限：请以管理员身份打开 PowerShell 后重试。"
    }
}

function Resolve-PythonExe {
    if ($PythonExe) {
        if (-not (Test-Path $PythonExe)) { throw "指定的 python.exe 不存在：$PythonExe" }
        return (Resolve-Path $PythonExe).Path
    }
    $venv = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    $found = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    throw "未找到 python.exe：请先创建虚拟环境或用 -PythonExe 指定全路径。"
}

switch ($Action) {
    "install" {
        Assert-Admin
        if (-not (Test-Path $runner)) { throw "缺少服务包装脚本：$runner" }
        $py = Resolve-PythonExe

        New-Item -ItemType Directory -Force -Path (Join-Path $root "data\logs") | Out-Null

        # 动作经 powershell 包装：工作目录在此固定，输出由 run-server.ps1 落盘
        $taskAction = New-ScheduledTaskAction `
            -Execute "powershell.exe" `
            -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -PythonExe `"$py`"" `
            -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -AtStartup
        # 不限时（服务常驻）；崩溃后 3 次每分钟重试 —— 与 systemd 的 on-failure 对应
        $settings = New-ScheduledTaskSettingsSet `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

        # -Force：幂等，重复执行即更新定义（先停旧任务再注册）
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger `
            -Settings $settings -Principal $principal -Force `
            -Description "Creator Agent Studio（多智能体内容创作台）—— 开机自启；日志见 data\logs\service.log" | Out-Null
        Start-ScheduledTask -TaskName $TaskName

        Write-Host "已注册并启动计划任务：$TaskName"
        Write-Host "  Python ：$py"
        Write-Host "  项目根：$root"
        Write-Host "  日志  ：$root\data\logs\service.log"
        Write-Host "  探活  ：Invoke-RestMethod http://127.0.0.1:8787/api/health"
    }
    "uninstall" {
        Assert-Admin
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已停止并删除计划任务：$TaskName（data\ 与代码不受影响）"
    }
    "status" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host "未注册计划任务：$TaskName"
            exit 1
        }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "任务状态：$($task.State)　上次运行：$($info.LastRunTime)　上次结果：$($info.LastTaskResult)"
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:8787/api/health" -TimeoutSec 5
            Write-Host "服务健康：正常（storage=$($health.config.storage.mode)　checkpointer=$($health.checkpointer.kind)）"
            if ($health.checkpointer.error) {
                Write-Host "注意：checkpointer 有降级错误 → $($health.checkpointer.error)"
            }
        } catch {
            Write-Host "服务健康：不可达（任务在运行但端口未响应？查看 data\logs\service.log）"
        }
    }
}