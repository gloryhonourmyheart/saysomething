#requires -Version 5.1
<#
.SYNOPSIS
  一键把本机 Streamlit 应用通过 ngrok 发布到公网，并打印可发给队友的链接。

.DESCRIPTION
  1. 检查目标端口上的 Streamlit 是否在跑，不在就后台拉起（日志写 .logs\）。
  2. 检查 ngrok 隧道是否指向该端口，不是就重建。
  3. 从 ngrok 本地 API(127.0.0.1:4040) 取当前公网地址。
  4. 通过公网地址做一次健康检查（证明回源可用），把链接复制到剪贴板。

.EXAMPLE
  .\scripts\share.ps1
  .\scripts\share.ps1 -Port 8502 -App app_ui_mock.py
  .\scripts\share.ps1 -Stable -Domain my-app.ngrok-free.app
#>
[CmdletBinding()]
param(
    [int]$Port = 8501,
    [string]$App = 'app.py',
    [string]$Stable,          # 已备案的静态域名，例如 xxx.ngrok-free.app / xxx.ngrok-free.dev
    [switch]$NoClipboard,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root '.logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

function Write-Step($msg) { Write-Host "[share] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[ ok ] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[warn] $msg" -ForegroundColor Yellow }

function Get-NgrokExe {
    $cmd = Get-Command ngrok -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fallback = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\ngrok.exe'
    if (Test-Path $fallback) { return $fallback }
    throw 'ngrok 未安装或不在 PATH 中。'
}

function Test-PortListening([int]$p) {
    return [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)
}

function Get-Tunnel {
    try { return (Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 5).tunnels } catch { return @() }
}

function Get-PythonExe {
    foreach ($cand in @(
        (Join-Path $env:USERPROFILE '.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'),
        'python', 'py')) {
        if ($cand -eq 'python' -or $cand -eq 'py') {
            if (Get-Command $cand -ErrorAction SilentlyContinue) { return $cand }
        } elseif (Test-Path $cand) { return $cand }
    }
    throw '找不到可用的 Python 解释器。'
}

# ---------- 1. 应用 ----------
if ($Restart) {
    Write-Step "重启端口 $Port 上的 Streamlit…"
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

if (Test-PortListening $Port) {
    Write-Ok "端口 $Port 已有服务在监听（复用）"
} else {
    if (-not (Test-Path (Join-Path $root $App))) { throw "找不到应用文件：$App" }
    $py = Get-PythonExe
    $appLog = Join-Path $logDir "streamlit-$Port.log"
    Write-Step "启动 Streamlit：$App -> 127.0.0.1:$Port"
    Start-Process -FilePath $py `
        -ArgumentList @('-m','streamlit','run',$App,'--server.port',"$Port",'--server.address','127.0.0.1','--server.headless','true') `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput $appLog -RedirectStandardError (Join-Path $logDir "streamlit-$Port.err.log") | Out-Null

    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-PortListening $Port)) {
        if ((Get-Date) -gt $deadline) { throw "Streamlit 启动超时，见 $appLog" }
        Start-Sleep -Milliseconds 700
    }
    Write-Ok "Streamlit 已监听 127.0.0.1:$Port"
}

# ---------- 2. 隧道 ----------
$ngrok = Get-NgrokExe
$tunnels = Get-Tunnel
$wantAddr = "http://localhost:$Port"
$match = $tunnels | Where-Object { $_.config.addr -eq $wantAddr -or $_.config.addr -eq "http://127.0.0.1:$Port" }

if ($Restart -and $match) {
    Write-Step '重建 ngrok 隧道…'
    $match.public_url | ForEach-Object { try { Invoke-RestMethod -Method Delete "http://127.0.0.1:4040/api/tunnels/$($match.name)" -TimeoutSec 5 | Out-Null } catch {} }
    $match = $null
}

if (-not $match -and (Get-Process ngrok -ErrorAction SilentlyContinue)) {
    Write-Warn2 "已有 ngrok 进程但隧道不指向 $Port，先结束它"
    Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    $tunnels = Get-Tunnel
    $match = $tunnels | Where-Object { $_.config.addr -eq $wantAddr }
}

if (-not $match) {
    $ngrokLog = Join-Path $logDir "ngrok-$Port.log"
    $args2 = @('http',"$Port",'--log','stdout','--log-format','logfmt')
    if ($Stable) { $args2 += @('--url', $Stable); Write-Step "使用静态域名：$Stable" }
    Write-Step "启动 ngrok http $Port"
    Start-Process -FilePath $ngrok -ArgumentList $args2 -WindowStyle Hidden `
        -RedirectStandardOutput $ngrokLog -RedirectStandardError (Join-Path $logDir "ngrok-$Port.err.log") | Out-Null

    $deadline = (Get-Date).AddSeconds(40)
    while (-not $match) {
        if ((Get-Date) -gt $deadline) { throw "ngrok 隧道建立超时，见 $ngrokLog" }
        Start-Sleep -Milliseconds 800
        $match = (Get-Tunnel) | Where-Object { $_.config.addr -eq $wantAddr }
    }
}

$publicUrl = ($match | Select-Object -First 1).public_url
if (-not $publicUrl) { throw '没能从 ngrok 本地 API 取到公网地址。' }

# ---------- 3. 公网回源自检 ----------
Write-Step "校验公网回源…"
$headers = @{ 'ngrok-skip-browser-warning' = '1' }
$verify = @()
foreach ($path in @('/_stcore/health','/')) {
    try {
        $r = Invoke-WebRequest -Uri ($publicUrl.TrimEnd('/') + $path) -Headers $headers -TimeoutSec 20 -UseBasicParsing
        $verify += "$path -> $($r.StatusCode)"
    } catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        $verify += "$path -> ERR($code)"
    }
}
$healthOk = $verify[0] -match '-> 200'
if ($healthOk) { Write-Ok ($verify -join ' | ') } else { Write-Warn2 ("回源自检未全绿：" + ($verify -join ' | ')) }

# ---------- 4. 输出 ----------
if (-not $NoClipboard) {
    try { Set-Clipboard -Value $publicUrl } catch { Write-Warn2 '复制到剪贴板失败（可手动复制）' }
}

Write-Host ''
Write-Host '================ 发给队友的链接 ================' -ForegroundColor Magenta
Write-Host "  $publicUrl" -ForegroundColor White
Write-Host '================================================' -ForegroundColor Magenta
Write-Host ''
Write-Host ("本地预览      : http://127.0.0.1:{0}" -f $Port)
Write-Host  'ngrok 流量面板 : http://127.0.0.1:4040'
Write-Host ("隧道日志      : {0}" -f (Join-Path $logDir "ngrok-$Port.log"))
Write-Host ''
Write-Host '提示：免费版首次访问会先出现 ngrok 提示页，点 “Visit Site” 即可进入。' -ForegroundColor DarkGray
if (-not $Stable) {
    Write-Host '提示：免费隧道重启后会换域名；想要固定链接，去 dashboard.ngrok.com → Domains 领取免费静态域名，' -ForegroundColor DarkGray
    Write-Host '      然后运行：.\scripts\share.ps1 -Stable 你的域名.ngrok-free.app' -ForegroundColor DarkGray
}

if (-not $NoClipboard) { Write-Host '（链接已复制到剪贴板）' -ForegroundColor DarkGray }
