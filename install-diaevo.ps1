$ErrorActionPreference = "Stop"

$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $InstallRoot ".venv\Scripts\python.exe"
$ShimRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "DiaEvo\bin"

New-Item -ItemType Directory -Force -Path $ShimRoot | Out-Null

function Write-DiaEvoShim {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Module
    )

    $ShimPath = Join-Path $ShimRoot "$Name.cmd"
    $Content = @"
@echo off
setlocal
set "DIAEVO_INSTALL_ROOT=$InstallRoot"
set "DIAEVO_WORKSPACE=%CD%"
set "PYTHONPATH=%DIAEVO_INSTALL_ROOT%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%DIAEVO_INSTALL_ROOT%\.venv\Scripts\python.exe" (
  echo Python venv not found at "%DIAEVO_INSTALL_ROOT%\.venv\Scripts\python.exe".
  echo Create it with: cd /d "%DIAEVO_INSTALL_ROOT%" ^&^& uv venv .venv
  exit /b 1
)
"%DIAEVO_INSTALL_ROOT%\.venv\Scripts\python.exe" -m $Module %*
exit /b %ERRORLEVEL%
"@

    Set-Content -Encoding ASCII -Path $ShimPath -Value $Content
    return $ShimPath
}

$DiaEvoShim = Write-DiaEvoShim -Name "diaevo" -Module "diaevo.cli"
$HomeShim = Write-DiaEvoShim -Name "diaevo-home" -Module "ui.terminal_home"

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Parts = @()
if ($UserPath) {
    $Parts = $UserPath -split ';' | Where-Object { $_ -and $_.Trim() }
}

$NormalizedInstallRoot = $InstallRoot.TrimEnd('\')
$NormalizedShimRoot = $ShimRoot.TrimEnd('\')
$CleanParts = @(
    foreach ($Part in $Parts) {
        $NormalizedPart = $Part.Trim().TrimEnd('\')
        if (-not [string]::Equals($NormalizedPart, $NormalizedInstallRoot, [StringComparison]::OrdinalIgnoreCase) -and
            -not [string]::Equals($NormalizedPart, $NormalizedShimRoot, [StringComparison]::OrdinalIgnoreCase)) {
            $Part.Trim()
        }
    }
)
$UpdatedParts = @($CleanParts) + $ShimRoot
[Environment]::SetEnvironmentVariable("Path", ($UpdatedParts -join ';'), "User")

$SessionParts = @($env:Path -split ';' | Where-Object {
    $_ -and
    -not [string]::Equals($_.Trim().TrimEnd('\'), $NormalizedInstallRoot, [StringComparison]::OrdinalIgnoreCase) -and
    -not [string]::Equals($_.Trim().TrimEnd('\'), $NormalizedShimRoot, [StringComparison]::OrdinalIgnoreCase)
})
$env:Path = (@($SessionParts) + $ShimRoot) -join ';'

Write-Output "DiaEvo 安装目录: $InstallRoot"
Write-Output "命令入口目录: $ShimRoot"
Write-Output "已生成: $DiaEvoShim"
Write-Output "已生成: $HomeShim"
Write-Output "已把命令入口目录写入当前用户 PATH，并从用户 PATH 中移除旧的项目根目录入口。"
if (-not (Test-Path -LiteralPath $Python)) {
    Write-Output "提示: 当前还没有找到 $Python。创建环境后命令入口会自动使用它。"
}
Write-Output "当前终端可直接运行: diaevo tools"
Write-Output "新开的 PowerShell/CMD 也可以在任意 workspace 运行: diaevo"
