# ============================================================================
# restore-gradle-wrapper.ps1 — restore android/gradle/wrapper/gradle-wrapper.jar
# from the OFFICIAL Gradle source, only when it is missing or corrupt.
#
# The wrapper jar is normally committed to this repo (it is part of the
# standard Gradle wrapper layout). This script exists as the documented
# recovery path: it downloads the exact wrapper jar shipped by the Gradle
# project itself for the distribution version pinned in
# android/gradle/wrapper/gradle-wrapper.properties, then verifies the result
# is a valid zip containing org/gradle/wrapper/GradleWrapperMain.class.
#
# Sources, in order of preference:
#   1. https://raw.githubusercontent.com/gradle/gradle/v<VER>/gradle/wrapper/gradle-wrapper.jar
#   2. https://github.com/gradle/gradle/raw/v<VER>/gradle/wrapper/gradle-wrapper.jar
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\restore-gradle-wrapper.ps1
# ============================================================================
#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root  = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Props = Join-Path $Root 'android\gradle\wrapper\gradle-wrapper.properties'
$Jar   = Join-Path $Root 'android\gradle\wrapper\gradle-wrapper.jar'

function Die([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Props)) { Die "gradle-wrapper.properties not found at $Props" }

# Extract the pinned distribution version, e.g. gradle-8.14.3-all.zip -> 8.14.3
$distLine = Select-String -Path $Props -Pattern '^distributionUrl=' | Select-Object -First 1
if (-not $distLine) { Die "distributionUrl missing from $Props" }
$distUrl = ($distLine.Line -split '=', 2)[1] -replace '\\:', ':'
if ($distUrl -notmatch 'gradle-([0-9][0-9.]*)-(all|bin)\.zip') {
    Die "could not parse gradle version from distributionUrl='$distUrl'"
}
$Version = $Matches[1]
Write-Host "Pinned Gradle distribution version: $Version"

# Validate using cmdlets only (works under Constrained Language Mode too):
# copy to a .zip, Expand-Archive it, and look for the wrapper main class.
function Test-WrapperJar([string]$Path) {
    if (-not (Test-Path $Path)) { return $false }
    if ((Get-Item $Path).Length -eq 0) { return $false }
    $tmpDir = Join-Path $env:TEMP ('moawrap_' + (Get-Random))
    try {
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
        Copy-Item -LiteralPath $Path -Destination (Join-Path $tmpDir 'wrapper.zip')
        Expand-Archive -LiteralPath (Join-Path $tmpDir 'wrapper.zip') `
            -DestinationPath (Join-Path $tmpDir 'out') -Force -ErrorAction Stop
        return Test-Path (Join-Path $tmpDir 'out\org\gradle\wrapper\GradleWrapperMain.class')
    } catch {
        return $false
    } finally {
        Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (Test-WrapperJar $Jar) {
    Write-Host "gradle-wrapper.jar already present and valid — nothing to do."
    exit 0
}

# TLS 1.2 for GitHub on older Windows PowerShell runtimes. Best-effort: under
# Constrained Language Mode the static assignment is rejected, and modern
# Windows enables TLS 1.2 by default anyway.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {
    # keep the runtime default
}

$urls = @(
    "https://raw.githubusercontent.com/gradle/gradle/v$Version/gradle/wrapper/gradle-wrapper.jar",
    "https://github.com/gradle/gradle/raw/v$Version/gradle/wrapper/gradle-wrapper.jar"
)

$tmp = "$Jar.tmp"
$restored = $false
foreach ($url in $urls) {
    Write-Host "Downloading official wrapper jar from:"
    Write-Host "  $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        if (Test-WrapperJar $tmp) {
            Move-Item -Force $tmp $Jar
            $restored = $true
            break
        }
        Write-Host "downloaded file is not a valid wrapper jar — trying next source."
        Remove-Item -Force $tmp -ErrorAction SilentlyContinue
    } catch {
        Write-Host "download failed ($($_.Exception.Message)) — trying next source."
    }
}
if (-not $restored) { Die "could not obtain a valid gradle-wrapper.jar from any official source." }

Write-Host "Restored gradle-wrapper.jar ($((Get-Item $Jar).Length) bytes) for Gradle $Version."
if (-not (Test-WrapperJar $Jar)) { Die "post-restore validation failed." }
Write-Host "Validation OK: org/gradle/wrapper/GradleWrapperMain.class present."
