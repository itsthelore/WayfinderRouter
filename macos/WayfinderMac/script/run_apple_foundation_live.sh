#!/usr/bin/env bash
set -euo pipefail

if [[ "${WAYFINDER_RUN_APPLE_FOUNDATION_LIVE:-}" != "1" ]]; then
  echo "error: set WAYFINDER_RUN_APPLE_FOUNDATION_LIVE=1 to enable the live test" >&2
  exit 2
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "error: the live Apple Foundation Models test requires Apple Silicon" >&2
  exit 2
fi

os_major="$(sw_vers -productVersion | cut -d. -f1)"
if [[ ! "$os_major" =~ ^[0-9]+$ ]] || (( os_major < 26 )); then
  echo "error: the live Apple Foundation Models test requires macOS 26 or newer" >&2
  exit 2
fi

app="${WAYFINDER_APP_BUNDLE:-}"
if [[ -z "$app" ]] || [[ ! -d "$app" ]]; then
  echo "error: WAYFINDER_APP_BUNDLE must name an existing signed Wayfinder.app" >&2
  exit 2
fi

helper="$app/Contents/MacOS/wayfinder-router"
broker="$app/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"
if [[ ! -x "$helper" ]] || [[ ! -d "$broker" ]]; then
  echo "error: live harness is missing the Rust helper or Foundation Models XPC service" >&2
  exit 2
fi

if pgrep -x WayfinderMac >/dev/null; then
  echo "error: close the Wayfinder menu-bar app before running app-closed inference evidence" >&2
  exit 2
fi

codesign --verify --deep --strict "$app"
codesign --verify --strict --test-requirement='=identifier "com.wayfinder.router.helper" and anchor apple generic' "$helper"
codesign --verify --strict --test-requirement='=identifier "com.wayfinder.FoundationModelBroker" and anchor apple generic' "$broker"

"$helper" apple-foundation-live-smoke --json
