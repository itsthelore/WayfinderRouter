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
if [[ -n "${CODESIGN_TIMESTAMP_OPTION:-}" ]]; then
  TIMESTAMP_OPTION="$CODESIGN_TIMESTAMP_OPTION"
elif [[ "$IDENTITY" == "-" ]]; then
  TIMESTAMP_OPTION="--timestamp=none"
else
  TIMESTAMP_OPTION="--timestamp"
fi
DEPLOYMENT_TARGET="14.0"
RELEASE_ARCH="arm64"
RUST_TARGET="aarch64-apple-darwin"
DISABLE_SWIFTPM_SANDBOX="${WAYFINDER_DISABLE_SWIFTPM_SANDBOX:-0}"
DESKTOP_VERSION_FILE="$ROOT_DIR/Packaging/DESKTOP_VERSION"
DESKTOP_VERSION="${WAYFINDER_DESKTOP_VERSION:-$(tr -d '[:space:]' < "$DESKTOP_VERSION_FILE")}"
DESKTOP_BUILD_NUMBER="${WAYFINDER_DESKTOP_BUILD_NUMBER:-1}"
HELPER_VERIFY_TIMEOUT_SECONDS="${WAYFINDER_HELPER_VERIFY_TIMEOUT_SECONDS:-10}"
NOTARYTOOL_TIMEOUT="${WAYFINDER_NOTARYTOOL_TIMEOUT:-45m}"

if [[ ! "$DESKTOP_VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "WAYFINDER_DESKTOP_VERSION must be a SemVer core such as 0.1.0" >&2
  exit 2
fi
if [[ ! "$DESKTOP_BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]]; then
  echo "WAYFINDER_DESKTOP_BUILD_NUMBER must be a positive integer" >&2
  exit 2
fi
if [[ ! "$HELPER_VERIFY_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || (( HELPER_VERIFY_TIMEOUT_SECONDS > 300 )); then
  echo "WAYFINDER_HELPER_VERIFY_TIMEOUT_SECONDS must be between 1 and 300" >&2
  exit 2
fi
if [[ ! "$NOTARYTOOL_TIMEOUT" =~ ^[1-9][0-9]*[smh]?$ ]]; then
  echo "WAYFINDER_NOTARYTOOL_TIMEOUT must be a positive notarytool duration such as 45m" >&2
  exit 2
fi
if [[ -n "${NOTARYTOOL_PROFILE:-}" && "$IDENTITY" == "-" ]]; then
  echo "NOTARYTOOL_PROFILE requires a real CODESIGN_IDENTITY" >&2
  exit 2
fi

export MACOSX_DEPLOYMENT_TARGET="$DEPLOYMENT_TARGET"

if [[ "$(uname -m)" != "$RELEASE_ARCH" ]]; then
  echo "Wayfinder Desktop v0.1.0 release bundles must be built on Apple Silicon" >&2
  exit 2
fi

SDK_VERSION="$(xcrun --sdk macosx --show-sdk-version)"
SDK_MAJOR="${SDK_VERSION%%.*}"
if [[ ! "$SDK_MAJOR" =~ ^[0-9]+$ ]] || (( SDK_MAJOR < 26 )); then
  echo "Wayfinder Desktop v0.1.0 release bundles require the macOS 26 SDK or later" >&2
  exit 2
fi

if [[ -n "${WAYFINDER_RELEASE_ARCHS:-}" && "$WAYFINDER_RELEASE_ARCHS" != "$RELEASE_ARCH" ]]; then
  echo "Wayfinder Desktop v0.1.0 supports only WAYFINDER_RELEASE_ARCHS=arm64" >&2
  exit 2
fi

rm -rf "$DIST_DIR/thin"
mkdir -p "$DIST_DIR/thin/$RELEASE_ARCH"

build_rust_slice() {
  local rust_target="$1"
  local output_arch="$2"
  WAYFINDER_PRODUCT_VERSION="$DESKTOP_VERSION" cargo build --manifest-path "$RUST_DIR/Cargo.toml" --locked --release --target "$rust_target" -p wayfinder-cli
  cp "$RUST_DIR/target/$rust_target/release/wayfinder-router" "$DIST_DIR/thin/$output_arch/wayfinder-router"
}

build_swift_slice() {
  local swift_arch="$1"
  local bin_path=""
  if [[ "$DISABLE_SWIFTPM_SANDBOX" == "1" ]]; then
    swift build --package-path "$ROOT_DIR" -c release --arch "$swift_arch" --disable-sandbox
    bin_path="$(swift build --package-path "$ROOT_DIR" -c release --arch "$swift_arch" --show-bin-path --disable-sandbox)"
  else
    swift build --package-path "$ROOT_DIR" -c release --arch "$swift_arch"
    bin_path="$(swift build --package-path "$ROOT_DIR" -c release --arch "$swift_arch" --show-bin-path)"
  fi
  cp "$bin_path/WayfinderMac" "$DIST_DIR/thin/$swift_arch/WayfinderMac"
  cp "$bin_path/WayfinderCredentialBroker" "$DIST_DIR/thin/$swift_arch/WayfinderCredentialBroker"
  cp "$bin_path/WayfinderFoundationModelBroker" "$DIST_DIR/thin/$swift_arch/WayfinderFoundationModelBroker"
}

build_rust_slice "$RUST_TARGET" "$RELEASE_ARCH"
build_swift_slice "$RELEASE_ARCH"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Helpers" "$APP/Contents/Resources"
mkdir -p "$GATEWAY_APP/Contents/MacOS"
mkdir -p "$CREDENTIAL_XPC/Contents/MacOS"
mkdir -p "$FOUNDATION_XPC/Contents/MacOS"

assemble_binary() {
  local name="$1"
  local output="$2"
  cp "$DIST_DIR/thin/$RELEASE_ARCH/$name" "$output"
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

if ! helper_version="$(
  "$ROOT_DIR/script/run_with_timeout.sh" "$HELPER_VERIFY_TIMEOUT_SECONDS" "$HELPER" --version
)"; then
  echo "embedded gateway version check failed or timed out" >&2
  exit 1
fi
if [[ "$helper_version" != "wayfinder-router $DESKTOP_VERSION" ]]; then
  echo "embedded gateway version does not match desktop version $DESKTOP_VERSION" >&2
  exit 1
fi
if ! grep -q "\"version\": \"$DESKTOP_VERSION\"" "$APP/Contents/Resources/wayfinder-helper.json"; then
  echo "embedded helper manifest version does not match desktop version $DESKTOP_VERSION" >&2
  exit 1
fi

"$ROOT_DIR/script/verify_release_architectures.sh" \
  "$HELPER" \
  "$APP/Contents/MacOS/WayfinderMac" \
  "$CREDENTIAL_XPC/Contents/MacOS/WayfinderCredentialBroker" \
  "$FOUNDATION_XPC/Contents/MacOS/WayfinderFoundationModelBroker"

codesign --force "$TIMESTAMP_OPTION" --options runtime \
  --entitlements "$ROOT_DIR/Packaging/CredentialBroker.entitlements" \
  --sign "$IDENTITY" "$CREDENTIAL_XPC"
codesign --force "$TIMESTAMP_OPTION" --options runtime \
  --entitlements "$ROOT_DIR/Packaging/FoundationModelBroker.entitlements" \
  --sign "$IDENTITY" "$FOUNDATION_XPC"
codesign --force "$TIMESTAMP_OPTION" --options runtime \
  --identifier com.wayfinder.router.helper \
  --entitlements "$ROOT_DIR/Packaging/Helper.entitlements" \
  --sign "$IDENTITY" "$HELPER"
codesign --force "$TIMESTAMP_OPTION" --options runtime \
  --entitlements "$ROOT_DIR/Packaging/Helper.entitlements" \
  --sign "$IDENTITY" "$GATEWAY_APP"
codesign --force "$TIMESTAMP_OPTION" --options runtime \
  --entitlements "$ROOT_DIR/Packaging/App.entitlements" \
  --sign "$IDENTITY" "$APP"
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

if [[ "$IDENTITY" == "-" ]]; then
  WAYFINDER_REQUIRE_DISTRIBUTION_SIGNATURE=0 \
    "$ROOT_DIR/script/verify_release_bundle.sh" "$APP"
else
  WAYFINDER_REQUIRE_DISTRIBUTION_SIGNATURE=1 \
    "$ROOT_DIR/script/verify_release_bundle.sh" "$APP"
fi

rm -f "$DIST_DIR/Wayfinder.zip" "$DIST_DIR/Wayfinder.zip.sha256"
ditto -c -k --keepParent "$APP" "$DIST_DIR/Wayfinder.zip"

if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
  notary_arguments=(--keychain-profile "$NOTARYTOOL_PROFILE")
  if [[ -n "${NOTARYTOOL_KEYCHAIN:-}" ]]; then
    notary_arguments+=(--keychain "$NOTARYTOOL_KEYCHAIN")
  fi
  xcrun notarytool submit "$DIST_DIR/Wayfinder.zip" "${notary_arguments[@]}" \
    --wait --timeout "$NOTARYTOOL_TIMEOUT"
  xcrun stapler staple "$APP"
  WAYFINDER_REQUIRE_DISTRIBUTION_SIGNATURE=1 WAYFINDER_REQUIRE_NOTARIZATION=1 \
    "$ROOT_DIR/script/verify_release_bundle.sh" "$APP"

  # The submitted ZIP predates stapling. Recreate it so the distributed app contains the ticket.
  rm -f "$DIST_DIR/Wayfinder.zip"
  ditto -c -k --keepParent "$APP" "$DIST_DIR/Wayfinder.zip"
fi

(
  cd "$DIST_DIR"
  shasum -a 256 Wayfinder.zip > Wayfinder.zip.sha256
  shasum -a 256 -c Wayfinder.zip.sha256
)

extracted_directory="$(mktemp -d "${TMPDIR:-/tmp}/wayfinder-release-extracted.XXXXXX")"
trap 'rm -rf "$extracted_directory"' EXIT
ditto -x -k "$DIST_DIR/Wayfinder.zip" "$extracted_directory"
if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
  WAYFINDER_REQUIRE_DISTRIBUTION_SIGNATURE=1 WAYFINDER_REQUIRE_NOTARIZATION=1 \
    "$ROOT_DIR/script/verify_release_bundle.sh" "$extracted_directory/Wayfinder.app"
elif [[ "$IDENTITY" != "-" ]]; then
  WAYFINDER_REQUIRE_DISTRIBUTION_SIGNATURE=1 \
    "$ROOT_DIR/script/verify_release_bundle.sh" "$extracted_directory/Wayfinder.app"
else
  WAYFINDER_REQUIRE_DISTRIBUTION_SIGNATURE=0 \
    "$ROOT_DIR/script/verify_release_bundle.sh" "$extracted_directory/Wayfinder.app"
fi

echo "$APP"
