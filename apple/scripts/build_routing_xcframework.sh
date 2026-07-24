#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUST_ROOT="$REPO_ROOT/rust"
PACKAGE_ROOT="$REPO_ROOT/apple/Packages/WayfinderRoutingBridge"
BUILD_ROOT="${WAYFINDER_APPLE_BRIDGE_BUILD_ROOT:-$REPO_ROOT/apple/.build/routing-bridge}"
CARGO_TARGET_DIR="$BUILD_ROOT/cargo-target"
GENERATED_ROOT="$BUILD_ROOT/generated"
HEADERS_ROOT="$GENERATED_ROOT/Headers"
SWIFT_ROOT="$GENERATED_ROOT/Swift"
ARTIFACT_ROOT="$PACKAGE_ROOT/Artifacts"
PACKAGE_GENERATED_ROOT="$PACKAGE_ROOT/Sources/WayfinderRoutingBridge/Generated"
XCFRAMEWORK="$ARTIFACT_ROOT/WayfinderRoutingFFI.xcframework"
HOST_TARGET="aarch64-apple-darwin"
DEVICE_TARGET="aarch64-apple-ios"
SIMULATOR_TARGET="aarch64-apple-ios-sim"

for command_name in cargo rustup xcodebuild xcrun; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "error: missing required command: $command_name" >&2
        exit 1
    fi
done

for target in "$HOST_TARGET" "$DEVICE_TARGET" "$SIMULATOR_TARGET"; do
    if ! rustup target list --installed | grep -Fx "$target" >/dev/null; then
        echo "error: missing Rust target $target" >&2
        echo "install it with: rustup target add $target" >&2
        exit 1
    fi
done

rm -rf "$BUILD_ROOT" "$ARTIFACT_ROOT" "$PACKAGE_GENERATED_ROOT"
mkdir -p \
    "$CARGO_TARGET_DIR" \
    "$HEADERS_ROOT" \
    "$SWIFT_ROOT" \
    "$ARTIFACT_ROOT" \
    "$PACKAGE_GENERATED_ROOT"

export CARGO_TARGET_DIR
export IPHONEOS_DEPLOYMENT_TARGET="${IPHONEOS_DEPLOYMENT_TARGET:-18.0}"

cd "$RUST_ROOT"
for target in "$HOST_TARGET" "$DEVICE_TARGET" "$SIMULATOR_TARGET"; do
    cargo build \
        --release \
        --locked \
        --package wayfinder-apple-ffi \
        --target "$target"
done

HOST_LIBRARY="$CARGO_TARGET_DIR/$HOST_TARGET/release/libwayfinder_apple_ffi.a"
DEVICE_LIBRARY="$CARGO_TARGET_DIR/$DEVICE_TARGET/release/libwayfinder_apple_ffi.a"
SIMULATOR_LIBRARY="$CARGO_TARGET_DIR/$SIMULATOR_TARGET/release/libwayfinder_apple_ffi.a"

cargo run \
    --locked \
    --package wayfinder-apple-ffi \
    --features bindgen \
    --bin uniffi-bindgen-swift \
    -- "$HOST_LIBRARY" "$SWIFT_ROOT" --swift-sources
cargo run \
    --locked \
    --package wayfinder-apple-ffi \
    --features bindgen \
    --bin uniffi-bindgen-swift \
    -- "$HOST_LIBRARY" "$HEADERS_ROOT" --headers
cargo run \
    --locked \
    --package wayfinder-apple-ffi \
    --features bindgen \
    --bin uniffi-bindgen-swift \
    -- "$HOST_LIBRARY" "$HEADERS_ROOT" \
    --modulemap \
    --xcframework \
    --module-name WayfinderRoutingFFI \
    --modulemap-filename module.modulemap

# UniFFI's XCFramework mode emits a framework module map. This package wraps
# static libraries, so SwiftPM requires a plain Clang module declaration.
sed -i '' 's/^framework module /module /' "$HEADERS_ROOT/module.modulemap"

cp "$SWIFT_ROOT/WayfinderRoutingBridge.swift" "$PACKAGE_GENERATED_ROOT/"

xcodebuild -create-xcframework \
    -library "$HOST_LIBRARY" \
    -headers "$HEADERS_ROOT" \
    -library "$DEVICE_LIBRARY" \
    -headers "$HEADERS_ROOT" \
    -library "$SIMULATOR_LIBRARY" \
    -headers "$HEADERS_ROOT" \
    -output "$XCFRAMEWORK"

test -f "$XCFRAMEWORK/Info.plist"
test -f "$PACKAGE_GENERATED_ROOT/WayfinderRoutingBridge.swift"

echo "Built $XCFRAMEWORK"
