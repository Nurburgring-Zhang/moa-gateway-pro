#!/usr/bin/env bash
# ============================================================================
# restore-gradle-wrapper.sh — restore android/gradle/wrapper/gradle-wrapper.jar
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
#      (the gradle/gradle repository at the matching release tag)
#   2. https://github.com/gradle/gradle/raw/v<VER>/gradle/wrapper/gradle-wrapper.jar
#      (same file through the redirect endpoint)
#
# Usage: bash scripts/restore-gradle-wrapper.sh   (from the mobile/ directory)
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROPS="$ROOT/android/gradle/wrapper/gradle-wrapper.properties"
JAR="$ROOT/android/gradle/wrapper/gradle-wrapper.jar"

die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$PROPS" ] || die "gradle-wrapper.properties not found at $PROPS"

# Extract the pinned distribution version, e.g. gradle-8.14.3-all.zip -> 8.14.3
DIST_URL="$(grep '^distributionUrl=' "$PROPS" | head -n1 | cut -d= -f2- | sed 's/\\:/:/g')"
VERSION="$(printf '%s' "$DIST_URL" | sed -n 's/.*gradle-\([0-9][0-9.]*\)-\(all\|bin\)\.zip/\1/p')"
[ -n "$VERSION" ] || die "could not parse gradle version from distributionUrl='$DIST_URL'"
echo "Pinned Gradle distribution version: $VERSION"

# True when $1 is a zip archive containing org/gradle/wrapper/GradleWrapperMain.class.
# Prefers unzip; falls back to python3's zipfile module when unzip is absent.
valid_wrapper_jar() {
  local f="$1"
  [ -f "$f" ] && [ -s "$f" ] || return 1
  if command -v unzip >/dev/null 2>&1; then
    unzip -l "$f" 2>/dev/null | grep -q 'org/gradle/wrapper/GradleWrapperMain\.class'
    return $?
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$f" <<'PY'
import sys, zipfile
try:
    with zipfile.ZipFile(sys.argv[1]) as z:
        sys.exit(0 if "org/gradle/wrapper/GradleWrapperMain.class" in z.namelist() else 1)
except Exception:
    sys.exit(1)
PY
    return $?
  fi
  echo "WARNING: neither unzip nor python3 available; cannot validate the jar contents." >&2
  return 1
}

if valid_wrapper_jar "$JAR"; then
  echo "gradle-wrapper.jar already present and valid — nothing to do."
  exit 0
fi

if command -v curl >/dev/null 2>&1; then
  FETCH() { curl -fSL --retry 3 -o "$JAR.tmp" "$1"; }
elif command -v wget >/dev/null 2>&1; then
  FETCH() { wget -O "$JAR.tmp" "$1"; }
else
  die "neither curl nor wget is available; cannot download the wrapper jar."
fi

URLS=(
  "https://raw.githubusercontent.com/gradle/gradle/v$VERSION/gradle/wrapper/gradle-wrapper.jar"
  "https://github.com/gradle/gradle/raw/v$VERSION/gradle/wrapper/gradle-wrapper.jar"
)

mkdir -p "$(dirname "$JAR")"
ok=0
for url in "${URLS[@]}"; do
  echo "Downloading official wrapper jar from:"
  echo "  $url"
  if FETCH "$url"; then
    if valid_wrapper_jar "$JAR.tmp"; then
      mv "$JAR.tmp" "$JAR"
      ok=1
      break
    fi
    echo "downloaded file is not a valid wrapper jar — trying next source."
    rm -f "$JAR.tmp"
  else
    echo "download failed — trying next source."
  fi
done
[ "$ok" = "1" ] || die "could not obtain a valid gradle-wrapper.jar from any official source."

echo "Restored gradle-wrapper.jar ($(wc -c < "$JAR") bytes) for Gradle $VERSION."
valid_wrapper_jar "$JAR" || die "post-restore validation failed."
echo "Validation OK: org/gradle/wrapper/GradleWrapperMain.class present."
