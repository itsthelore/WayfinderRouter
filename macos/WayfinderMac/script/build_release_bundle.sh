#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$ROOT_DIR/../.." && pwd)"
RUST_DIR="$REPO_DIR/rust"
DIST_DIR="${DIST_DIR:-$ROOT_DIR/dist-release}"
APP="$DIST_DIR/Wayfinder.app"
GATEWAY_APP="$APP/Contents/Helpers/WayfinderGateway.app"
HELPER="$GATEWAY_APP/Contents/MacOS/wayfinder-router"
CREDENTIAL_XPC="$GATEWAY_APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc"
FOUNDATION_XPC="$GATEWAY_APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"
IDENTITY="${CODESIGN_IDENTITY:--}"
TIMESTAMP_OPTION="${CODESIGN_TIMESTAMP_OPTION:---timestamp}"
DEPLOYMENT_TARGET="14.0"
RELEASE_ARCHS="${WAYFINDER_RELEASE_ARCHS:-arm64 x86_64}"
SWIFT_BUILD_FLAGS=()
if [[ "${WAYFINDER_DISABLE_SWIFTPM_SANDBOX:-0}" == "1" ]]; then
  SWIFT_BUILD_FLAGS+=(--disable-sandbox)
fi
DESKTOP_VERSION_FILE="$ROOT_DIR/Packaging/DESKTOP_VERSION"
DESKTOP_VERSION="${WAYFINDER_DESKTOP_VERSION:-$(tr -d '[:space:]' < "$DESKTOP_VERSION_FILE")}"
DESKTOP_BUILD_NUMBER="${WAYFINDER_DESKTOP_BUILD_NUMBER:-1}"

if [[ ! "$DESKTOP_VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "WAYFINDER_DESKTOP_VERSION must be a SemVer core such as 0.1.0" >&2
  exit 2
fi
if [[ ! "$DESKTOP_BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
  echo "WAYFINDER_DESKTOP_BUILD_NUMBER must be a positive integer" >&2
  exit 2
fi

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
  WAYFINDER_PRODUCT_VERSION="$DESKTOP_VERSION" cargo build --manifest-path "$RUST_DIR/Cargo.toml" --locked --release --target "$rust_target" -p wayfinder-cli
  cp "$RUST_DIR/target/$rust_target/release/wayfinder-router" "$DIST_DIR/thin/$output_arch/wayfinder-router"
}

build_swift_slice() {
  local swift_arch="$1"
  swift build --package-path "$ROOT_DIR" -c release --arch "$swift_arch" "${SWIFT_BUILD_FLAGS[@]}"
  local bin_path
  bin_path="$(swift build --package-path "$ROOT_DIR" -c release --arch "$swift_arch" --show-bin-path "${SWIFT_BUILD_FLAGS[@]}")"
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
mkdir -p "$GATEWAY_APP/Contents/MacOS"
mkdir -p "$CREDENTIAL_XPC/Contents/MacOS"
mkdir -p "$FOUNDATION_XPC/Contents/MacOS"

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

assemble_binary wayfinder-router "$HELPER"
assemble_binary WayfinderMac "$APP/Contents/MacOS/WayfinderMac"
assemble_binary WayfinderCredentialBroker "$CREDENTIAL_XPC/Contents/MacOS/WayfinderCredentialBroker"
assemble_binary WayfinderFoundationModelBroker "$FOUNDATION_XPC/Contents/MacOS/WayfinderFoundationModelBroker"

cp "$ROOT_DIR/Packaging/App-Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT_DIR/Packaging/Gateway-Info.plist" "$GATEWAY_APP/Contents/Info.plist"
cp "$ROOT_DIR/Packaging/CredentialBroker-Info.plist" "$CREDENTIAL_XPC/Contents/Info.plist"
cp "$ROOT_DIR/Packaging/FoundationModelBroker-Info.plist" "$FOUNDATION_XPC/Contents/Info.plist"
cp "$ROOT_DIR/Resources/wayfinder-helper.json" "$APP/Contents/Resources/wayfinder-helper.json"
sed -i '' "s/@WAYFINDER_DESKTOP_VERSION@/$DESKTOP_VERSION/g" "$APP/Contents/Resources/wayfinder-helper.json"

set_bundle_version() {
  local plist="$1"
  /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $DESKTOP_VERSION" "$plist"
  /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $DESKTOP_BUILD_NUMBER" "$plist"
}

set_bundle_version "$APP/Contents/Info.plist"
set_bundle_version "$GATEWAY_APP/Contents/Info.plist"
set_bundle_version "$CREDENTIAL_XPC/Contents/Info.plist"
set_bundle_version "$FOUNDATION_XPC/Contents/Info.plist"
chmod 755 "$APP/Contents/MacOS/WayfinderMac" "$HELPER"
chmod 755 "$CREDENTIAL_XPC/Contents/MacOS/WayfinderCredentialBroker"
chmod 755 "$FOUNDATION_XPC/Contents/MacOS/WayfinderFoundationModelBroker"

if [[ "$("$HELPER" --version)" != "wayfinder-router $DESKTOP_VERSION" ]]; then
  echo "embedded gateway version does not match desktop version $DESKTOP_VERSION" >&2
  exit 1
fi
if ! grep -q "\"version\": \"$DESKTOP_VERSION\"" "$APP/Contents/Resources/wayfinder-helper.json"; then
  echo "embedded helper manifest version does not match desktop version $DESKTOP_VERSION" >&2
  exit 1
fi

for binary in \
  "$HELPER" \
  "$APP/Contents/MacOS/WayfinderMac" \
  "$CREDENTIAL_XPC/Contents/MacOS/WayfinderCredentialBroker" \
  "$FOUNDATION_XPC/Contents/MacOS/WayfinderFoundationModelBroker"; do
  for arch in $RELEASE_ARCHS; do
    lipo "$binary" -verify_arch "$arch"
  done
done

codesign --force "$TIMESTAMP_OPTION" --options runtime --entitlements "$ROOT_DIR/Packaging/CredentialBroker.entitlements" --sign "$IDENTITY" "$CREDENTIAL_XPC"
codesign --force "$TIMESTAMP_OPTION" --options runtime --entitlements "$ROOT_DIR/Packaging/FoundationModelBroker.entitlements" --sign "$IDENTITY" "$FOUNDATION_XPC"
codesign --force "$TIMESTAMP_OPTION" --options runtime --identifier com.wayfinder.router.helper --entitlements "$ROOT_DIR/Packaging/Helper.entitlements" --sign "$IDENTITY" "$HELPER"
codesign --force "$TIMESTAMP_OPTION" --options runtime --entitlements "$ROOT_DIR/Packaging/Helper.entitlements" --sign "$IDENTITY" "$GATEWAY_APP"
codesign --force "$TIMESTAMP_OPTION" --options runtime --entitlements "$ROOT_DIR/Packaging/App.entitlements" --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

if [[ "$IDENTITY" != "-" ]]; then
  signing_team() {
    codesign --display --verbose=4 "$1" 2>&1 | awk -F= '/^TeamIdentifier=/{print $2; exit}'
  }
  expected_team="$(signing_team "$APP")"
  if [[ -z "$expected_team" ]]; then
    echo "error: signed Wayfinder.app has no TeamIdentifier" >&2
    exit 1
  fi
  for component in "$HELPER" "$GATEWAY_APP" "$CREDENTIAL_XPC" "$FOUNDATION_XPC"; do
    component_team="$(signing_team "$component")"
    if [[ "$component_team" != "$expected_team" ]]; then
      echo "error: signing TeamIdentifier mismatch for $component" >&2
      exit 1
    fi
  done
fi

if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
  ditto -c -k --keepParent "$APP" "$DIST_DIR/Wayfinder.zip"
  xcrun notarytool submit "$DIST_DIR/Wayfinder.zip" --keychain-profile "$NOTARYTOOL_PROFILE" --wait
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"
  spctl --assess --type execute --verbose=2 "$APP"
fi

echo "$APP"
