$ErrorActionPreference = "Stop"

$InstallRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$Parts = @()
if ($UserPath) {
    $Parts = $UserPath -split ';' | Where-Object { $_ -and $_.Trim() }
}

$AlreadyInstalled = $false
foreach ($Part in $Parts) {
    if ([string]::Equals($Part.TrimEnd('\'), $InstallRoot.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
        $AlreadyInstalled = $true
        break
    }
}

if (-not $AlreadyInstalled) {
    $Updated = (@($Parts) + $InstallRoot) -join ';'
    [Environment]::SetEnvironmentVariable("Path", $Updated, "User")
}

$env:Path = (@($env:Path -split ';') + $InstallRoot | Select-Object -Unique) -join ';'

Write-Output "DiaEvo launcher directory: $InstallRoot"
if ($AlreadyInstalled) {
    Write-Output "User PATH already contains the DiaEvo launcher directory."
} else {
    Write-Output "Added DiaEvo launcher directory to the user PATH."
    Write-Output "Open a new PowerShell window to use the persisted PATH."
}
Write-Output "Use from any workspace folder: diaevo tools"
Write-Output "Do not use .\diaevo outside the install directory; .\ means current directory in PowerShell."
Write-Output "Immediate fallback in this terminal: & `"$InstallRoot\diaevo.ps1`" tools"
