#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$ROOT_DIR/../.." && pwd)"
RUST_DIR="$REPO_DIR/rust"
DIST_DIR="${DIST_DIR:-$ROOT_DIR/dist-release}"
APP="$DIST_DIR/Wayfinder.app"
IDENTITY="${CODESIGN_IDENTITY:--}"
DEPLOYMENT_TARGET="14.0"

export MACOSX_DEPLOYMENT_TARGET="$DEPLOYMENT_TARGET"
mkdir -p "$DIST_DIR/thin/arm64" "$DIST_DIR/thin/x86_64"

build_rust_slice() {
  local rust_target="$1"
  local output_arch="$2"
  cargo build --manifest-path "$RUST_DIR/Cargo.toml" --locked --release --target "$rust_target" -p wayfinder-cli
  cp "$RUST_DIR/target/$rust_target/release/wayfinder-router" "$DIST_DIR/thin/$output_arch/wayfinder-router"
}

build_swift_slice() {
  local swift_arch="$1"
  swift build --package-path "$ROOT_DIR" -c release --arch "$swift_arch"
  local bin_path
  bin_path="$(swift build --package-path "$ROOT_DIR" -c release --arch "$swift_arch" --show-bin-path)"
  cp "$bin_path/WayfinderMac" "$DIST_DIR/thin/$swift_arch/WayfinderMac"
  cp "$bin_path/WayfinderCredentialBroker" "$DIST_DIR/thin/$swift_arch/WayfinderCredentialBroker"
  cp "$bin_path/WayfinderFoundationModelBroker" "$DIST_DIR/thin/$swift_arch/WayfinderFoundationModelBroker"
}

build_rust_slice aarch64-apple-darwin arm64
build_rust_slice x86_64-apple-darwin x86_64
build_swift_slice arm64
build_swift_slice x86_64

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers" "$APP/Contents/Resources"
mkdir -p "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/MacOS"
mkdir -p "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/MacOS"

lipo -create "$DIST_DIR/thin/arm64/wayfinder-router" "$DIST_DIR/thin/x86_64/wayfinder-router" -output "$APP/Contents/Helpers/wayfinder-router"
lipo -create "$DIST_DIR/thin/arm64/WayfinderMac" "$DIST_DIR/thin/x86_64/WayfinderMac" -output "$APP/Contents/MacOS/WayfinderMac"
lipo -create "$DIST_DIR/thin/arm64/WayfinderCredentialBroker" "$DIST_DIR/thin/x86_64/WayfinderCredentialBroker" -output "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/MacOS/WayfinderCredentialBroker"
lipo -create "$DIST_DIR/thin/arm64/WayfinderFoundationModelBroker" "$DIST_DIR/thin/x86_64/WayfinderFoundationModelBroker" -output "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/MacOS/WayfinderFoundationModelBroker"

cp "$ROOT_DIR/Packaging/App-Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT_DIR/Packaging/CredentialBroker-Info.plist" "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/Info.plist"
cp "$ROOT_DIR/Packaging/FoundationModelBroker-Info.plist" "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/Info.plist"
cp "$ROOT_DIR/Resources/wayfinder-helper.json" "$APP/Contents/Resources/wayfinder-helper.json"
chmod 755 "$APP/Contents/MacOS/WayfinderMac" "$APP/Contents/Helpers/wayfinder-router"
chmod 755 "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/MacOS/WayfinderCredentialBroker"
chmod 755 "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/MacOS/WayfinderFoundationModelBroker"

lipo -verify_arch arm64 x86_64 "$APP/Contents/Helpers/wayfinder-router"
lipo -verify_arch arm64 x86_64 "$APP/Contents/MacOS/WayfinderMac"
lipo -verify_arch arm64 x86_64 "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/MacOS/WayfinderCredentialBroker"
lipo -verify_arch arm64 x86_64 "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/MacOS/WayfinderFoundationModelBroker"

codesign --force --timestamp --options runtime --identifier com.wayfinder.router.helper --entitlements "$ROOT_DIR/Packaging/Helper.entitlements" --sign "$IDENTITY" "$APP/Contents/Helpers/wayfinder-router"
codesign --force --timestamp --options runtime --entitlements "$ROOT_DIR/Packaging/CredentialBroker.entitlements" --sign "$IDENTITY" "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc"
codesign --force --timestamp --options runtime --entitlements "$ROOT_DIR/Packaging/FoundationModelBroker.entitlements" --sign "$IDENTITY" "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"
codesign --force --timestamp --options runtime --entitlements "$ROOT_DIR/Packaging/App.entitlements" --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
  ditto -c -k --keepParent "$APP" "$DIST_DIR/Wayfinder.zip"
  xcrun notarytool submit "$DIST_DIR/Wayfinder.zip" --keychain-profile "$NOTARYTOOL_PROFILE" --wait
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  spctl --assess --type execute --verbose=2 "$APP"
fi

echo "$APP"
