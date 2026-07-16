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

source_gateway="$source_app/Contents/Helpers/WayfinderGateway.app"
source_helper="$source_gateway/Contents/MacOS/wayfinder-router"
source_broker="$source_gateway/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"
if [[ ! -x "$source_helper" ]] || [[ ! -d "$source_broker" ]]; then
  echo "error: source app is missing its containing gateway app or Foundation Models XPC service" >&2
  exit 2
fi

rm -rf "$output"
ditto "$source_gateway" "$output"

codesign --force "$timestamp_option" --options runtime \
  --identifier com.wayfinder.router.helper \
  --entitlements "$(dirname "$0")/../Packaging/Helper.entitlements" \
  --sign "$identity" "$output"
codesign --verify --deep --strict --verbose=2 "$output"

echo "$output"
