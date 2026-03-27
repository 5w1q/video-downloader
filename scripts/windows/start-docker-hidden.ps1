# 无控制台窗口执行 docker compose up -d（供任务计划程序「开机启动」使用）
# 前提：已安装 Docker Desktop，且已在项目根目录执行过 docker compose build
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $Root
docker compose up -d
