# ============================================================================
# build-apk.ps1 — one-command Android APK build for the MOA Gateway console.
#
# Pipeline: npm ci -> web bundle (esbuild) + typecheck (tsc) -> cap sync
#           -> gradle wrapper integrity check -> gradlew assemble<BuildType>
#
# Usage (from the mobile/ directory):
#   .\build-apk.ps1                    # release APK (default)
#   .\build-apk.ps1 -BuildType debug   # debug APK
#   .\build-apk.ps1 -SkipInstall       # reuse existing node_modules
#
# Release signing is driven ENTIRELY by environment variables (see
# android/app/build.gradle). Set them before running a shippable release:
#   $env:MOA_KEYSTORE_PATH      = "C:\secrets\moa-release.keystore"
#   $env:MOA_KEYSTORE_PASSWORD  = "..."
#   $env:MOA_KEY_ALIAS          = "moa-release"
#   $env:MOA_KEY_PASSWORD       = "..."
# Without MOA_KEYSTORE_PATH the release build still succeeds but is signed
# with the local debug key — gradle prints a loud warning and this script
# repeats it. Never ship a debug-signed artifact.
#
# Prerequisites: Node.js >= 20 (with npm), JDK 21+, Android SDK
# (ANDROID_HOME set, or Android Studio installed). See README.md.
# ============================================================================
#Requires -Version 5.1
param(
    [ValidateSet('release', 'debug')]
    [string]$BuildType = 'release',
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Die([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
Step "0/5 Toolchain pre-flight"
# ---------------------------------------------------------------------------
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { Die "node not found on PATH. Install Node.js >= 20 first." }
$nodeMajor = [int]((& node --version) -replace '^v', '' -split '\.')[0]
if ($nodeMajor -lt 20) { Die "Node.js >= 20 required (found $(& node --version))." }
Write-Host "node $(& node --version), npm $(& npm --version)"

$java = Get-Command java -ErrorAction SilentlyContinue
if (-not $java) {
    Die "java not found on PATH. Install JDK 21 (e.g. Microsoft Build of OpenJDK 21) and retry."
}
Write-Host "java: $((& java -version 2>&1 | Select-Object -First 1))"

if (-not $env:ANDROID_HOME -and -not $env:ANDROID_SDK_ROOT) {
    $candidates = @(
        "$env:LOCALAPPDATA\Android\Sdk",
        "$env:USERPROFILE\AppData\Local\Android\Sdk"
    )
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) {
        $env:ANDROID_HOME = $found
        Write-Host "ANDROID_HOME auto-detected: $found"
    } else {
        Die "Android SDK not found. Set ANDROID_HOME (or install Android Studio) and retry."
    }
}

# ---------------------------------------------------------------------------
Step "1/5 Installing npm dependencies (npm ci)"
# ---------------------------------------------------------------------------
if ($SkipInstall) {
    Write-Host "skipped (-SkipInstall)"
} else {
    & npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Die "npm ci failed (exit $LASTEXITCODE)." }
}

# ---------------------------------------------------------------------------
Step "2/5 Building the web layer (esbuild bundle + tsc typecheck)"
# ---------------------------------------------------------------------------
& npm run build
if ($LASTEXITCODE -ne 0) { Die "web build failed (exit $LASTEXITCODE)." }

# ---------------------------------------------------------------------------
Step "3/5 Syncing web assets into the Android project (cap sync android)"
# ---------------------------------------------------------------------------
& npx cap sync android
if ($LASTEXITCODE -ne 0) { Die "cap sync failed (exit $LASTEXITCODE)." }

# ---------------------------------------------------------------------------
Step "4/5 Verifying the Gradle wrapper"
# ---------------------------------------------------------------------------
$wrapperJar = Join-Path $Root 'android\gradle\wrapper\gradle-wrapper.jar'
if (-not (Test-Path $wrapperJar)) {
    Write-Host "gradle-wrapper.jar missing — restoring from the official Gradle repository..."
    & (Join-Path $Root 'scripts\restore-gradle-wrapper.ps1')
    if ($LASTEXITCODE -ne 0) { Die "could not restore gradle-wrapper.jar." }
}

# ---------------------------------------------------------------------------
$GradleTask = 'assembleRelease'
if ($BuildType -eq 'debug') { $GradleTask = 'assembleDebug' }
Step "5/5 Running Gradle ($GradleTask)"
# ---------------------------------------------------------------------------
if ($BuildType -eq 'release') {
    if (-not $env:MOA_KEYSTORE_PATH) {
        Write-Host "WARNING: MOA_KEYSTORE_PATH is not set. The release APK will be signed" -ForegroundColor Yellow
        Write-Host "         with the LOCAL DEBUG key and must not be shipped. Set the four" -ForegroundColor Yellow
        Write-Host "         MOA_KEYSTORE_* environment variables for a real release build." -ForegroundColor Yellow
    } elseif (-not (Test-Path $env:MOA_KEYSTORE_PATH)) {
        Die "MOA_KEYSTORE_PATH points to a file that does not exist: $env:MOA_KEYSTORE_PATH"
    }
}

Set-Location (Join-Path $Root 'android')
& .\gradlew.bat $GradleTask --stacktrace
$gradleExit = $LASTEXITCODE
Set-Location $Root
if ($gradleExit -ne 0) { Die "gradle build failed (exit $gradleExit). See the stack trace above." }

# ---------------------------------------------------------------------------
# Report the produced artifact(s).
# ---------------------------------------------------------------------------
$apkDir = Join-Path $Root "android\app\build\outputs\apk\$BuildType"
$apks = @()
if (Test-Path $apkDir) { $apks = Get-ChildItem $apkDir -Filter '*.apk' }
if ($apks.Count -eq 0) { Die "gradle reported success but no APK was found under $apkDir." }

Write-Host ""
Write-Host "BUILD OK" -ForegroundColor Green
foreach ($apk in $apks) {
    Write-Host "  APK : $($apk.FullName)"
    Write-Host "  Size: $( '{0:N2}' -f ($apk.Length / 1MB) ) MB"
}
if ($BuildType -eq 'release' -and -not $env:MOA_KEYSTORE_PATH) {
    Write-Host ""
    Write-Host "REMINDER: this release APK is DEBUG-signed (no MOA_KEYSTORE_PATH)." -ForegroundColor Yellow
}
