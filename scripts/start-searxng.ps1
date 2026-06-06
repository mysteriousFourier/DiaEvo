param(
    [string]$Name = "diaevo-searxng",
    [int]$Port = 8080,
    [string]$Image = "searxng/searxng:latest",
    [string]$ConfigDir = "",
    [switch]$Recreate,
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Output "[searxng] $Message"
}

function Test-CommandExists {
    param([string]$Command)
    return $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

function Get-RepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Write-DiaevoEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $Python = Join-Path (Get-RepoRoot) ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $Python) {
        & $Python -c "from diaevo.env import write_env_value; write_env_value('$Key', '$Value')" | Out-Null
        return
    }

    $EnvPath = Join-Path (Get-RepoRoot) ".env"
    $Lines = @()
    if (Test-Path -LiteralPath $EnvPath) {
        $Lines = @(Get-Content -Encoding UTF8 -Path $EnvPath)
    }

    $Updated = $false
    $Output = foreach ($Line in $Lines) {
        $Candidate = $Line.Trim()
        if ($Candidate.StartsWith("export ")) {
            $Candidate = $Candidate.Substring(7).Trim()
        }
        if (-not $Candidate.StartsWith("#") -and $Candidate.Contains("=")) {
            $ExistingKey = $Candidate.Split("=", 2)[0].Trim()
            if ($ExistingKey -eq $Key) {
                $Updated = $true
                "$Key=$Value"
                continue
            }
        }
        $Line
    }
    if (-not $Updated) {
        $Output = @($Output) + "$Key=$Value"
    }
    Set-Content -Encoding UTF8 -Path $EnvPath -Value $Output
}

function Test-SearxngJson {
    param([string]$BaseUrl)

    $Url = "$BaseUrl/search?q=diaevo&format=json"
    try {
        $Response = Invoke-RestMethod -Uri $Url -TimeoutSec 20
    } catch {
        return @{
            Ok = $false
            Error = $_.Exception.Message
        }
    }
    if ($null -eq $Response) {
        return @{
            Ok = $false
            Error = "empty response"
        }
    }
    return @{
        Ok = $true
        Error = ""
    }
}

$RepoRoot = Get-RepoRoot
if (-not $ConfigDir) {
    $ConfigDir = Join-Path $RepoRoot ".tmp\searxng"
}
$ConfigDir = (New-Item -ItemType Directory -Force -Path $ConfigDir).FullName
$SettingsPath = Join-Path $ConfigDir "settings.yml"
$BaseUrl = "http://127.0.0.1:$Port"

if (-not (Test-CommandExists "docker")) {
    throw "Docker was not found on PATH. Install Docker Desktop first."
}

try {
    docker info | Out-Null
} catch {
    throw "Docker is installed but the Docker daemon is not running. Start Docker Desktop, then run this script again."
}

$SecretKey = [Guid]::NewGuid().ToString("N")
$Settings = @"
use_default_settings: true

server:
  bind_address: "0.0.0.0"
  port: 8080
  base_url: "$BaseUrl/"
  secret_key: "$SecretKey"
  limiter: false
  public_instance: false

search:
  safe_search: 0
  formats:
    - html
    - json
"@
Set-Content -Encoding ASCII -Path $SettingsPath -Value $Settings
Write-Step "wrote SearXNG config: $SettingsPath"

Write-DiaevoEnvValue -Key "DIAEVO_WEB_SEARCH_BACKEND" -Value "searxng"
Write-DiaevoEnvValue -Key "DIAEVO_SEARXNG_URL" -Value $BaseUrl
Write-Step "updated DiaEvo .env for SearXNG at $BaseUrl"

$Existing = docker ps -a --filter "name=^/$Name$" --format "{{.Names}}"
if ($Existing -and $Recreate) {
    Write-Step "removing existing container: $Name"
    docker rm -f $Name | Out-Null
    $Existing = ""
}

if (-not $SkipPull) {
    Write-Step "pulling image: $Image"
    docker pull $Image | Out-Null
}

if ($Existing) {
    $Running = docker ps --filter "name=^/$Name$" --format "{{.Names}}"
    if ($Running) {
        Write-Step "container is already running: $Name"
    } else {
        Write-Step "starting existing container: $Name"
        docker start $Name | Out-Null
    }
} else {
    Write-Step "creating container: $Name"
    docker run -d `
        --name $Name `
        --restart unless-stopped `
        -p "127.0.0.1:$Port`:8080" `
        -v "$ConfigDir`:/etc/searxng:rw" `
        $Image | Out-Null
}

Write-Step "waiting for SearXNG to respond"
$Verified = $false
$LastError = ""
for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
    Start-Sleep -Seconds 2
    $Check = Test-SearxngJson -BaseUrl $BaseUrl
    if ($Check.Ok) {
        $Verified = $true
        break
    }
    $LastError = $Check.Error
}

if (-not $Verified) {
    Write-Output ""
    Write-Output "SearXNG container was started, but JSON verification failed."
    Write-Output "URL: $BaseUrl/search?q=diaevo&format=json"
    Write-Output "Last error: $LastError"
    Write-Output "If this reused an old container, rerun: powershell -ExecutionPolicy Bypass -File scripts\start-searxng.ps1 -Recreate"
    exit 1
}

Write-Output ""
Write-Output "SearXNG is ready: $BaseUrl"
Write-Output "DiaEvo env:"
Write-Output "  DIAEVO_WEB_SEARCH_BACKEND=searxng"
Write-Output "  DIAEVO_SEARXNG_URL=$BaseUrl"
