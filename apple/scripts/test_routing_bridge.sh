#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PACKAGE_ROOT="$REPO_ROOT/apple/Packages/WayfinderRoutingBridge"
DEVICE_SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
SIMULATOR_SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"

"$SCRIPT_DIR/build_routing_xcframework.sh"

swift test --package-path "$PACKAGE_ROOT"

swift build \
    --package-path "$PACKAGE_ROOT" \
    --triple arm64-apple-ios18.0-simulator \
    --sdk "$SIMULATOR_SDK"

swift build \
    --package-path "$PACKAGE_ROOT" \
    --triple arm64-apple-ios18.0 \
    --sdk "$DEVICE_SDK"
