#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$ROOT_DIR/../.." && pwd)"
RUST_DIR="$REPO_DIR/rust"
DIST_DIR="${DIST_DIR:-$ROOT_DIR/dist-release}"
APP="$DIST_DIR/Wayfinder.app"
IDENTITY="${CODESIGN_IDENTITY:--}"
TIMESTAMP_OPTION="${CODESIGN_TIMESTAMP_OPTION:---timestamp}"
DEPLOYMENT_TARGET="14.0"
RELEASE_ARCHS="${WAYFINDER_RELEASE_ARCHS:-arm64 x86_64}"

export MACOSX_DEPLOYMENT_TARGET="$DEPLOYMENT_TARGET"

has_arch() {
  [[ " $RELEASE_ARCHS " == *" $1 "* ]]
}

for arch in $RELEASE_ARCHS; do
  if [[ "$arch" != "arm64" && "$arch" != "x86_64" ]]; then
    echo "unsupported WAYFINDER_RELEASE_ARCHS value: $arch" >&2
    exit 2
  fi
done

if ! has_arch arm64 && ! has_arch x86_64; then
  echo "WAYFINDER_RELEASE_ARCHS must include arm64, x86_64, or both" >&2
  exit 2
fi

for arch in arm64 x86_64; do
  if has_arch "$arch"; then
    mkdir -p "$DIST_DIR/thin/$arch"
  fi
done

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

if has_arch arm64; then
  build_rust_slice aarch64-apple-darwin arm64
  build_swift_slice arm64
fi
if has_arch x86_64; then
  build_rust_slice x86_64-apple-darwin x86_64
  build_swift_slice x86_64
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers" "$APP/Contents/Resources"
mkdir -p "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/MacOS"
mkdir -p "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/MacOS"

assemble_binary() {
  local name="$1"
  local output="$2"
  if has_arch arm64 && has_arch x86_64; then
    lipo -create "$DIST_DIR/thin/arm64/$name" "$DIST_DIR/thin/x86_64/$name" -output "$output"
  elif has_arch arm64; then
    cp "$DIST_DIR/thin/arm64/$name" "$output"
  else
    cp "$DIST_DIR/thin/x86_64/$name" "$output"
  fi
}

assemble_binary wayfinder-router "$APP/Contents/Helpers/wayfinder-router"
assemble_binary WayfinderMac "$APP/Contents/MacOS/WayfinderMac"
assemble_binary WayfinderCredentialBroker "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/MacOS/WayfinderCredentialBroker"
assemble_binary WayfinderFoundationModelBroker "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/MacOS/WayfinderFoundationModelBroker"

cp "$ROOT_DIR/Packaging/App-Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT_DIR/Packaging/CredentialBroker-Info.plist" "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/Info.plist"
cp "$ROOT_DIR/Packaging/FoundationModelBroker-Info.plist" "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/Info.plist"
cp "$ROOT_DIR/Resources/wayfinder-helper.json" "$APP/Contents/Resources/wayfinder-helper.json"
chmod 755 "$APP/Contents/MacOS/WayfinderMac" "$APP/Contents/Helpers/wayfinder-router"
chmod 755 "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/MacOS/WayfinderCredentialBroker"
chmod 755 "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/MacOS/WayfinderFoundationModelBroker"

for binary in \
  "$APP/Contents/Helpers/wayfinder-router" \
  "$APP/Contents/MacOS/WayfinderMac" \
  "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc/Contents/MacOS/WayfinderCredentialBroker" \
  "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc/Contents/MacOS/WayfinderFoundationModelBroker"; do
  for arch in $RELEASE_ARCHS; do
    lipo "$binary" -verify_arch "$arch"
  done
done

codesign --force "$TIMESTAMP_OPTION" --options runtime --identifier com.wayfinder.router.helper --entitlements "$ROOT_DIR/Packaging/Helper.entitlements" --sign "$IDENTITY" "$APP/Contents/Helpers/wayfinder-router"
codesign --force "$TIMESTAMP_OPTION" --options runtime --entitlements "$ROOT_DIR/Packaging/CredentialBroker.entitlements" --sign "$IDENTITY" "$APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc"
codesign --force "$TIMESTAMP_OPTION" --options runtime --entitlements "$ROOT_DIR/Packaging/FoundationModelBroker.entitlements" --sign "$IDENTITY" "$APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"
codesign --force "$TIMESTAMP_OPTION" --options runtime --entitlements "$ROOT_DIR/Packaging/App.entitlements" --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
  ditto -c -k --keepParent "$APP" "$DIST_DIR/Wayfinder.zip"
  xcrun notarytool submit "$DIST_DIR/Wayfinder.zip" --keychain-profile "$NOTARYTOOL_PROFILE" --wait
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  spctl --assess --type execute --verbose=2 "$APP"
fi

echo "$APP"
