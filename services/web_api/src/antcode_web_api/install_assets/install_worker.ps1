$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$MinPythonMinor = 11
$DefaultWorkerPort = 8001
$DefaultWorkerName = 'Worker-001'

function Fail-Install {
    param([string]$Message)
    throw "AntCode Worker install failed: $Message"
}

function Require-Environment {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        Fail-Install "required environment variable $Name is missing"
    }
}

function Assert-Inputs {
    foreach ($name in @(
        'ANTCODE_WORKER_KEY',
        'WORKER_API_BASE_URL',
        'WORKER_GATEWAY_ENDPOINT',
        'WORKER_INSTALL_SOURCE_URL',
        'WORKER_INSTALL_SOURCE_REF',
        'WORKER_INSTALL_UV_VERSION'
    )) {
        Require-Environment $name
    }
    $sourceUri = [Uri]$env:WORKER_INSTALL_SOURCE_URL
    if ($sourceUri.Scheme -ne 'https' -or -not $sourceUri.Host) {
        Fail-Install 'source URL must use HTTPS'
    }
    if ($env:WORKER_INSTALL_SOURCE_REF -notmatch '^[0-9a-fA-F]{40}$') {
        Fail-Install 'source ref must be a full Git commit'
    }
    if ($env:WORKER_INSTALL_UV_VERSION -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
        Fail-Install 'uv version must be pinned'
    }
    $apiUri = [Uri]$env:WORKER_API_BASE_URL
    $localHosts = @('localhost', '127.0.0.1', '::1')
    if ($apiUri.Scheme -ne 'https' -and $localHosts -notcontains $apiUri.Host) {
        Fail-Install 'remote API base URL must use HTTPS'
    }
}

function Resolve-Python {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if (-not $command) {
        Fail-Install 'required command python was not found'
    }
    & $command.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, $MinPythonMinor) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Fail-Install "Python 3.$MinPythonMinor or newer is required"
    }
    & $command.Source -m pip --version | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail-Install 'Python pip is required' }
    return $command.Source
}

function Install-Uv {
    param([string]$Python, [string]$BootstrapDir)
    $uv = Join-Path $BootstrapDir 'Scripts\uv.exe'
    if (-not (Test-Path -LiteralPath $uv)) {
        & $Python -m venv $BootstrapDir
        $bootstrapPython = Join-Path $BootstrapDir 'Scripts\python.exe'
        & $bootstrapPython -m pip install "uv==$env:WORKER_INSTALL_UV_VERSION" | Out-Host
        if ($LASTEXITCODE -ne 0) { Fail-Install 'uv installation failed' }
    }
    & $uv --version | Out-Null
    return $uv
}

function Checkout-Source {
    param([string]$InstallDir)
    if (Test-Path -LiteralPath $InstallDir) {
        Fail-Install "install directory already exists: $InstallDir"
    }
    $tempDir = "$InstallDir.tmp-$([Guid]::NewGuid().ToString('N'))"
    try {
        & git init --quiet $tempDir
        if ($LASTEXITCODE -ne 0) { Fail-Install 'git init failed' }
        & git -C $tempDir remote add origin $env:WORKER_INSTALL_SOURCE_URL
        if ($LASTEXITCODE -ne 0) { Fail-Install 'git remote configuration failed' }
        & git -C $tempDir fetch --quiet --depth 1 origin $env:WORKER_INSTALL_SOURCE_REF
        if ($LASTEXITCODE -ne 0) { Fail-Install 'git fetch failed' }
        $actualRef = (& git -C $tempDir rev-parse FETCH_HEAD).Trim()
        if ($LASTEXITCODE -ne 0) { Fail-Install 'git rev-parse failed' }
        if ($actualRef -ne $env:WORKER_INSTALL_SOURCE_REF.ToLowerInvariant()) {
            Fail-Install 'fetched source commit does not match pinned ref'
        }
        & git -C $tempDir checkout --quiet --detach FETCH_HEAD
        if ($LASTEXITCODE -ne 0) { Fail-Install 'git checkout failed' }
        Move-Item -LiteralPath $tempDir -Destination $InstallDir
    }
    finally {
        if (Test-Path -LiteralPath $tempDir) {
            Remove-Item -LiteralPath $tempDir -Recurse -Force
        }
    }
}

function Write-WorkerConfig {
    param([string]$InstallDir)
    $config = [ordered]@{
        WORKER_API_BASE_URL = $env:WORKER_API_BASE_URL
        WORKER_GATEWAY_ENDPOINT = $env:WORKER_GATEWAY_ENDPOINT
        WORKER_GATEWAY_TLS = $env:WORKER_GATEWAY_TLS
        WORKER_CREDENTIAL_STORE = 'persistent'
        WORKER_NAME = $env:WORKER_NAME
        WORKER_PORT = $env:WORKER_PORT
    }
    $configPath = Join-Path $InstallDir '.antcode-worker.json'
    $config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
}

function Start-Worker {
    param([string]$InstallDir, [string]$Uv)
    $workerName = if ($env:WORKER_NAME) { $env:WORKER_NAME } else { $DefaultWorkerName }
    $workerPort = if ($env:WORKER_PORT) { [int]$env:WORKER_PORT } else { $DefaultWorkerPort }
    if ($workerPort -lt 1 -or $workerPort -gt 65535) {
        Fail-Install 'WORKER_PORT must be between 1 and 65535'
    }
    Set-Location -LiteralPath $InstallDir
    & $Uv run --frozen python -m antcode_worker run `
        --name $workerName `
        --port $workerPort `
        --transport gateway `
        --gateway-endpoint $env:WORKER_GATEWAY_ENDPOINT `
        --worker-key $env:ANTCODE_WORKER_KEY
    exit $LASTEXITCODE
}

function Main {
    Assert-Inputs
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail-Install 'required command git was not found'
    }
    if (-not $env:WORKER_GATEWAY_TLS) { $env:WORKER_GATEWAY_TLS = 'false' }
    $env:WORKER_CREDENTIAL_STORE = 'persistent'
    if (-not $env:WORKER_NAME) { $env:WORKER_NAME = $DefaultWorkerName }
    if (-not $env:WORKER_PORT) { $env:WORKER_PORT = [string]$DefaultWorkerPort }
    $installRoot = if ($env:WORKER_INSTALL_ROOT) { $env:WORKER_INSTALL_ROOT } else { Join-Path $HOME '.antcode' }
    $installDir = Join-Path $installRoot 'worker-src'
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    $python = Resolve-Python
    $uv = Install-Uv $python (Join-Path $installRoot 'uv-bootstrap')
    Checkout-Source $installDir
    & $uv sync --directory $installDir --all-packages --frozen
    if ($LASTEXITCODE -ne 0) { Fail-Install 'uv workspace sync failed' }
    Write-WorkerConfig $installDir
    Start-Worker $installDir $uv
}

Main
