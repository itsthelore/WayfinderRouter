#!/usr/bin/env bash
set -euo pipefail

source_app="${WAYFINDER_APP_BUNDLE:-}"
output="${WAYFINDER_LIVE_HARNESS:-/private/tmp/WayfinderFoundationLiveHarness.app}"
identity="${CODESIGN_IDENTITY:-}"
timestamp_option="${CODESIGN_TIMESTAMP_OPTION:---timestamp}"

if [[ "$output" != /private/tmp/*.app ]]; then
  echo "error: WAYFINDER_LIVE_HARNESS must be an app bundle under /private/tmp" >&2
  exit 2
fi
if [[ -z "$source_app" ]] || [[ ! -d "$source_app" ]]; then
  echo "error: WAYFINDER_APP_BUNDLE must name an existing signed Wayfinder.app" >&2
  exit 2
fi
if [[ -z "$identity" ]] || [[ "$identity" == "-" ]]; then
  echo "error: CODESIGN_IDENTITY must name a real Apple signing identity" >&2
  exit 2
fi

source_helper="$source_app/Contents/Helpers/wayfinder-router"
source_broker="$source_app/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"
if [[ ! -x "$source_helper" ]] || [[ ! -d "$source_broker" ]]; then
  echo "error: source app is missing the Rust helper or Foundation Models XPC service" >&2
  exit 2
fi

rm -rf "$output"
mkdir -p "$output/Contents/MacOS" "$output/Contents/XPCServices"
cp "$source_helper" "$output/Contents/MacOS/wayfinder-router"
cp -R "$source_broker" "$output/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"
cp "$source_app/Contents/Info.plist" "$output/Contents/Info.plist"
plutil -replace CFBundleExecutable -string wayfinder-router "$output/Contents/Info.plist"
plutil -replace CFBundleIdentifier -string com.wayfinder.router.helper "$output/Contents/Info.plist"
plutil -replace CFBundleName -string WayfinderFoundationLiveHarness "$output/Contents/Info.plist"
plutil -remove LSUIElement "$output/Contents/Info.plist" 2>/dev/null || true
plutil -remove NSPrincipalClass "$output/Contents/Info.plist" 2>/dev/null || true

codesign --force "$timestamp_option" --options runtime \
  --identifier com.wayfinder.router.helper \
  --entitlements "$(dirname "$0")/../Packaging/Helper.entitlements" \
  --sign "$identity" "$output"
codesign --verify --deep --strict --verbose=2 "$output"

echo "$output"
