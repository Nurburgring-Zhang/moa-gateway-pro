#!/usr/bin/env bash
# ============================================================================
# build-apk.sh — one-command Android APK build for the MOA Gateway console.
#
# Pipeline: npm ci -> web bundle (esbuild) + typecheck (tsc) -> cap sync
#           -> gradle wrapper integrity check -> gradlew assemble<type>
#
# Usage (from the mobile/ directory):
#   ./build-apk.sh              # release APK (default)
#   ./build-apk.sh debug        # debug APK
#   SKIP_INSTALL=1 ./build-apk.sh   # reuse existing node_modules
#
# Release signing is driven ENTIRELY by environment variables (see
# android/app/build.gradle). Export them before a shippable release build:
#   export MOA_KEYSTORE_PATH=/secrets/moa-release.keystore
#   export MOA_KEYSTORE_PASSWORD=...
#   export MOA_KEY_ALIAS=moa-release
#   export MOA_KEY_PASSWORD=...
# Without MOA_KEYSTORE_PATH the release build still succeeds but is signed
# with the local debug key — never ship that artifact.
#
# Prerequisites: Node.js >= 20 (with npm), JDK 21+, Android SDK
# (ANDROID_HOME set, or sdkmanager on PATH). See README.md.
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BUILD_TYPE="${1:-release}"
case "$BUILD_TYPE" in
  release|debug) ;;
  *) echo "ERROR: unknown build type '$BUILD_TYPE' (expected: release | debug)" >&2; exit 2 ;;
esac

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
step "0/5 Toolchain pre-flight"
# ---------------------------------------------------------------------------
command -v node >/dev/null 2>&1 || die "node not found on PATH. Install Node.js >= 20 first."
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[ "$NODE_MAJOR" -ge 20 ] || die "Node.js >= 20 required (found $(node --version))."
command -v npm >/dev/null 2>&1 || die "npm not found on PATH."
echo "node $(node --version), npm $(npm --version)"

command -v java >/dev/null 2>&1 || die "java not found on PATH. Install JDK 21 and retry."
java -version 2>&1 | head -n 1

if [ -z "${ANDROID_HOME:-}" ] && [ -z "${ANDROID_SDK_ROOT:-}" ]; then
  for cand in "$HOME/Android/Sdk" "$HOME/Library/Android/sdk" /opt/android-sdk; do
    if [ -d "$cand" ]; then
      export ANDROID_HOME="$cand"
      echo "ANDROID_HOME auto-detected: $cand"
      break
    fi
  done
fi
[ -n "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ] || \
  die "Android SDK not found. Set ANDROID_HOME (or ANDROID_SDK_ROOT) and retry."

# ---------------------------------------------------------------------------
step "1/5 Installing npm dependencies (npm ci)"
# ---------------------------------------------------------------------------
if [ "${SKIP_INSTALL:-0}" = "1" ]; then
  echo "skipped (SKIP_INSTALL=1)"
else
  npm ci --no-audit --no-fund
fi

# ---------------------------------------------------------------------------
step "2/5 Building the web layer (esbuild bundle + tsc typecheck)"
# ---------------------------------------------------------------------------
npm run build

# ---------------------------------------------------------------------------
step "3/5 Syncing web assets into the Android project (cap sync android)"
# ---------------------------------------------------------------------------
npx cap sync android

# ---------------------------------------------------------------------------
step "4/5 Verifying the Gradle wrapper"
# ---------------------------------------------------------------------------
if [ ! -f android/gradle/wrapper/gradle-wrapper.jar ]; then
  echo "gradle-wrapper.jar missing — restoring from the official Gradle repository..."
  bash scripts/restore-gradle-wrapper.sh
fi

# ---------------------------------------------------------------------------
TASK="assemble$(printf '%s' "$BUILD_TYPE" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')"
step "5/5 Running Gradle ($TASK)"
# ---------------------------------------------------------------------------
if [ "$BUILD_TYPE" = "release" ]; then
  if [ -z "${MOA_KEYSTORE_PATH:-}" ]; then
    echo "WARNING: MOA_KEYSTORE_PATH is not set. The release APK will be signed"
    echo "         with the LOCAL DEBUG key and must not be shipped. Export the"
    echo "         four MOA_KEYSTORE_* variables for a real release build."
  elif [ ! -f "$MOA_KEYSTORE_PATH" ]; then
    die "MOA_KEYSTORE_PATH points to a file that does not exist: $MOA_KEYSTORE_PATH"
  fi
fi

( cd android && ./gradlew "$TASK" --stacktrace )

# ---------------------------------------------------------------------------
# Report the produced artifact(s).
# ---------------------------------------------------------------------------
APK_DIR="android/app/build/outputs/apk/$BUILD_TYPE"
APKS="$(find "$APK_DIR" -maxdepth 1 -name '*.apk' 2>/dev/null || true)"
[ -n "$APKS" ] || die "gradle reported success but no APK was found under $APK_DIR."

echo
echo "BUILD OK"
while IFS= read -r apk; do
  echo "  APK : $ROOT/$apk"
  echo "  Size: $(du -h "$apk" | cut -f1)"
done <<< "$APKS"

if [ "$BUILD_TYPE" = "release" ] && [ -z "${MOA_KEYSTORE_PATH:-}" ]; then
  echo
  echo "REMINDER: this release APK is DEBUG-signed (no MOA_KEYSTORE_PATH)."
fi
