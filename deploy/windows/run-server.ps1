# Creator Agent Studio —— 前台运行包装（Windows 任务计划的服务动作）
#
# 职责：切到项目根目录、把 stdout/stderr 追加到 data\logs\service.log
# （超过 10MB 时轮转为 .old，单份保留），然后运行 python -m app.main。
#
# 为什么需要包装而不是直接把 python 注册进任务计划：
#   * 任务计划的动作**不会重定向输出**，日志会直接丢失 —— 而排障（尤其
#     「断点续跑不可用」这类静默降级）必须能看到服务端日志；
#   * 工作目录与 PYTHONUNBUFFERED 由本脚本固定，不依赖任务计划的调用环境。
#
# 由 deploy/windows/install-task.ps1 注册的任务调用；也可手工前台运行：
#   powershell -NoProfile -ExecutionPolicy Bypass -File deploy\windows\run-server.ps1 -PythonExe "C:\Python312\python.exe"

param(
    # python.exe 全路径。服务由 SYSTEM 账户运行，不能依赖 PATH 查找。
    [Parameter(Mandatory = $true)]
    [string]$PythonExe
)

$ErrorActionPreference = "Stop"

# 固定到项目根（本脚本位于 <项目根>\deploy\windows\）
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

# 日志实时性：应用日志自带 flush，这里兜底第三方库的输出缓冲
$env:PYTHONUNBUFFERED = "1"

$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "service.log"

if ((Test-Path $log) -and ((Get-Item $log).Length -gt 10MB)) {
    Move-Item -Force $log "$log.old"
}

"`n==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 启动 ====" | Out-File -Append -Encoding utf8 $log

& $PythonExe -m app.main *>> $log