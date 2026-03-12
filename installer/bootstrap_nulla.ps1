[CmdletBinding()]
param(
    [string]$RepoOwner = $env:NULLA_GITHUB_OWNER,
    [string]$RepoName = $env:NULLA_GITHUB_REPO,
    [string]$Ref = $env:NULLA_GITHUB_REF,
    [string]$InstallDir = $env:NULLA_INSTALL_DIR,
    [string]$ArchiveUrl = $env:NULLA_ARCHIVE_URL,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoOwner)) { $RepoOwner = "Parad0x-Labs" }
if ([string]::IsNullOrWhiteSpace($RepoName)) { $RepoName = "Decentralized_NULLA" }
if ([string]::IsNullOrWhiteSpace($Ref)) { $Ref = "main" }
if ([string]::IsNullOrWhiteSpace($InstallDir)) { $InstallDir = Join-Path $HOME "Decentralized_NULLA" }
if ([string]::IsNullOrWhiteSpace($ArchiveUrl)) { $ArchiveUrl = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$Ref.zip" }

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Test-InstallDir {
    if (-not (Test-Path -LiteralPath $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir | Out-Null
        return
    }

    if (Test-Path -LiteralPath (Join-Path $InstallDir "Install_And_Run_NULLA.bat")) {
        Write-Info "Existing NULLA install detected at $InstallDir"
        return
    }

    $items = Get-ChildItem -LiteralPath $InstallDir -Force
    if ($items.Count -gt 0) {
        throw "$InstallDir exists and is not an existing NULLA install. Use -InstallDir with an empty folder."
    }
}

function Download-And-Extract {
    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("nulla-bootstrap-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmpDir | Out-Null
    try {
        $archivePath = Join-Path $tmpDir "nulla.zip"
        $expandDir = Join-Path $tmpDir "expanded"
        Write-Info "Downloading NULLA from $ArchiveUrl"
        Invoke-WebRequest -Uri $ArchiveUrl -OutFile $archivePath -UseBasicParsing

        Write-Info "Extracting to $InstallDir"
        Expand-Archive -LiteralPath $archivePath -DestinationPath $expandDir -Force
        $root = Get-ChildItem -LiteralPath $expandDir | Select-Object -First 1
        if (-not $root) {
            throw "Downloaded archive did not contain project files."
        }
        Get-ChildItem -LiteralPath $root.FullName -Force | ForEach-Object {
            Move-Item -LiteralPath $_.FullName -Destination $InstallDir -Force
        }
    }
    finally {
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Run-Installer {
    $launcher = Join-Path $InstallDir "Install_And_Run_NULLA.bat"
    $guided = Join-Path $InstallDir "Install_NULLA.bat"
    if (-not (Test-Path -LiteralPath $launcher)) {
        throw "Bootstrap download succeeded, but $launcher is missing."
    }

    Write-Info "Running NULLA installer..."
    if ($NoStart) {
        & $guided /Y "/OPENCLAW=default"
    }
    else {
        & $launcher
    }
}

Test-InstallDir
Download-And-Extract
Run-Installer
