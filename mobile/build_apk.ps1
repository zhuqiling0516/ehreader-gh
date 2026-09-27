# Windows 上准备 APK 构建（打 APK 必须在 Linux/WSL/Docker 里做，Windows 原生不支持）
#
#   .\build_apk.ps1              整理 vendor/ + 自测 + 打印构建命令
#   .\build_apk.ps1 -Test        只跑自测（数据层 + 界面冒烟）
#   .\build_apk.ps1 -Docker      本机有 Docker 时直接用 buildozer 镜像构建
#   .\build_apk.ps1 -Wsl         本机有 WSL2(Ubuntu) 时在 WSL 里构建
param(
    [switch]$Test,
    [switch]$Docker,
    [switch]$Wsl,
    [string]$Python = 'py -3.13'
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host '==> 1/3 收集共享模块到 vendor/' -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path 'vendor' | Out-Null
Copy-Item '../pages.py', '../browse.py' 'vendor' -Force
Get-ChildItem 'vendor' | Select-Object Name, Length | Format-Table -AutoSize

Write-Host '==> 2/3 自测（模拟站点，不访问真实网站）' -ForegroundColor Cyan
if ($Test) {
    & cmd /c "$Python -X utf8 selftest.py ui"
    Write-Host '自测报告： selftest_report.txt' -ForegroundColor Green
    return
}

Write-Host '==> 3/3 选择构建方式' -ForegroundColor Cyan
$hasDocker = $null -ne (Get-Command docker -ErrorAction SilentlyContinue)
$hasWsl = $false
try { $hasWsl = (wsl -l -q 2>$null | Where-Object { $_ -match 'Ubuntu|Debian' } | Measure-Object).Count -gt 0 } catch { }

if ($Docker -or ($hasDocker -and -not $Wsl)) {
    if (-not $hasDocker) {
        Write-Host '本机没有 docker，无法用 -Docker 构建' -ForegroundColor Yellow
        return
    }
    Write-Host '使用 Docker 里的 buildozer 构建（首次会下载 Android SDK/NDK，几个 GB）' -ForegroundColor Green
    docker run --rm -v "${PWD}:/home/user/hostcwd" kivy/buildozer android debug
    Write-Host 'APK 应该在 mobile\bin\ 下' -ForegroundColor Green
    return
}

if ($Wsl -or $hasWsl) {
    $unixPath = '/' + ($PWD.Path -replace '^([A-Za-z]):', '$1' -replace '\\', '/').ToLower()
    $unixPath = $unixPath -replace '^/([a-z])', '/mnt/$1'
    Write-Host "使用 WSL 构建：$unixPath" -ForegroundColor Green
    wsl bash -lc "cd '$unixPath' && chmod +x build_apk.sh && ./build_apk.sh"
    return
}

Write-Host @'

本机既没有 Docker 也没有 WSL2，无法在 Windows 上打 APK（python-for-android 只支持 Linux）。

三种可选做法：
  A. 装 WSL2 + Ubuntu，然后在本目录执行：
       wsl --install -d Ubuntu
       # 进入 Ubuntu 后：
       sudo apt update && sudo apt install -y git zip unzip openjdk-17-jdk python3-pip \
            autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev \
            libtinfo6 cmake libffi-dev libssl-dev
       pip install --user buildozer cython
       cd /mnt/d/.../mobile && ./build_apk.sh

  B. 用 Docker Desktop：
       .\build_apk.ps1 -Docker

  C. 用 GitHub Actions（不需要本机环境）：
       把 ci\android-build.yml 复制到仓库的 .github\workflows\android-build.yml，
       push 之后在 Actions 页面下载 ehreader-apk 工件。

在装好之前，可以直接在 Windows 上预览同一套界面：
    py -3.13 main.py
'@ -ForegroundColor Yellow
