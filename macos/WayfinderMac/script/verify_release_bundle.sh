#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$ROOT_DIR/dist-release/Wayfinder.app}"
GATEWAY_APP="$APP/Contents/Helpers/WayfinderGateway.app"
HELPER="$GATEWAY_APP/Contents/MacOS/wayfinder-router"
CREDENTIAL_XPC="$GATEWAY_APP/Contents/XPCServices/com.wayfinder.CredentialBroker.xpc"
FOUNDATION_XPC="$GATEWAY_APP/Contents/XPCServices/com.wayfinder.FoundationModelBroker.xpc"
MANIFEST="$APP/Contents/Resources/wayfinder-helper.json"
EXPECTED_VERSION="${WAYFINDER_DESKTOP_VERSION:-$(tr -d '[:space:]' < "$ROOT_DIR/Packaging/DESKTOP_VERSION")}"
REQUIRE_DISTRIBUTION_SIGNATURE="${WAYFINDER_REQUIRE_DISTRIBUTION_SIGNATURE:-0}"
REQUIRE_NOTARIZATION="${WAYFINDER_REQUIRE_NOTARIZATION:-0}"
HELPER_VERIFY_TIMEOUT_SECONDS="${WAYFINDER_HELPER_VERIFY_TIMEOUT_SECONDS:-10}"

if [[ ! -d "$APP" ]]; then
  echo "error: Wayfinder app bundle is missing: $APP" >&2
  exit 1
fi
if [[ ! "$HELPER_VERIFY_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || (( HELPER_VERIFY_TIMEOUT_SECONDS > 300 )); then
  echo "error: WAYFINDER_HELPER_VERIFY_TIMEOUT_SECONDS must be between 1 and 300" >&2
  exit 2
fi

binaries=(
  "$APP/Contents/MacOS/WayfinderMac"
  "$HELPER"
  "$CREDENTIAL_XPC/Contents/MacOS/WayfinderCredentialBroker"
  "$FOUNDATION_XPC/Contents/MacOS/WayfinderFoundationModelBroker"
)

"$ROOT_DIR/script/verify_release_architectures.sh" "${binaries[@]}"

for binary in "${binaries[@]}"; do
  minimum_os="$(/usr/bin/xcrun vtool -show-build "$binary" | /usr/bin/awk '/minos/{print $2; exit}')"
  if [[ "$minimum_os" != "14.0" && "$minimum_os" != "14.0.0" ]]; then
    echo "error: $binary has minimum macOS $minimum_os, expected 14.0" >&2
    exit 1
  fi
done

plists=(
  "$APP/Contents/Info.plist"
  "$GATEWAY_APP/Contents/Info.plist"
  "$CREDENTIAL_XPC/Contents/Info.plist"
  "$FOUNDATION_XPC/Contents/Info.plist"
)

expected_build_number=""
for plist in "${plists[@]}"; do
  version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$plist")"
  build_number="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$plist")"
  if [[ "$version" != "$EXPECTED_VERSION" ]]; then
    echo "error: $plist reports version $version, expected $EXPECTED_VERSION" >&2
    exit 1
  fi
  if [[ ! "$build_number" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: $plist has invalid build number: $build_number" >&2
    exit 1
  fi
  if [[ -z "$expected_build_number" ]]; then
    expected_build_number="$build_number"
  elif [[ "$build_number" != "$expected_build_number" ]]; then
    echo "error: $plist build number $build_number does not match $expected_build_number" >&2
    exit 1
  fi
done

plist_extract_typed() {
  local plist="$1"
  local key="$2"
  local expected_type="$3"
  /usr/bin/plutil -extract "$key" raw -expect "$expected_type" "$plist"
}

bounded_file_size() {
  local path="$1"
  local maximum_bytes="$2"
  local label="$3"
  local byte_count=""
  byte_count="$(/usr/bin/stat -f '%z' "$path")"
  if [[ ! "$byte_count" =~ ^[0-9]+$ ]] || (( byte_count == 0 || byte_count > maximum_bytes )); then
    echo "error: $label must contain between 1 and $maximum_bytes bytes; found: $byte_count" >&2
    exit 1
  fi
}

bounded_file_size "$MANIFEST" 65536 "helper manifest"

manifest_schema_version="$(plist_extract_typed "$MANIFEST" schema_version integer)"
manifest_implementation="$(plist_extract_typed "$MANIFEST" implementation string)"
manifest_version="$(plist_extract_typed "$MANIFEST" version string)"
manifest_architecture="$(plist_extract_typed "$MANIFEST" target_architecture string)"
manifest_wire_contract_version="$(plist_extract_typed "$MANIFEST" wire_contract_version integer)"
manifest_config_schema_minimum="$(plist_extract_typed "$MANIFEST" config_schema_minimum integer)"
manifest_config_schema_maximum="$(plist_extract_typed "$MANIFEST" config_schema_maximum integer)"
plist_extract_typed "$MANIFEST" required_commands array >/dev/null
plist_extract_typed "$MANIFEST" required_native_commands array >/dev/null
plist_extract_typed "$MANIFEST" credential_mechanisms array >/dev/null
if [[ "$manifest_schema_version" != "1" ]]; then
  echo "error: unsupported helper manifest schema: $manifest_schema_version" >&2
  exit 1
fi
if [[ "$manifest_implementation" != "rust" ]]; then
  echo "error: helper manifest implementation must be rust; found: $manifest_implementation" >&2
  exit 1
fi
if [[ "$manifest_version" != "$EXPECTED_VERSION" ]]; then
  echo "error: helper manifest version $manifest_version does not match $EXPECTED_VERSION" >&2
  exit 1
fi
if [[ "$manifest_architecture" != "arm64" ]]; then
  echo "error: helper manifest architecture must be arm64; found: $manifest_architecture" >&2
  exit 1
fi
if [[ "$manifest_wire_contract_version" != "1" ]]; then
  echo "error: helper manifest wire contract must be 1; found: $manifest_wire_contract_version" >&2
  exit 1
fi
if [[ "$manifest_config_schema_minimum" != "1" || "$manifest_config_schema_maximum" != "1" ]]; then
  echo "error: helper manifest config schema range must be 1...1" >&2
  exit 1
fi

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/wayfinder-release-verify.XXXXXX")"
trap 'rm -rf "$temporary_directory"' EXIT

/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP"

signed_components=(
  "$APP"
  "$GATEWAY_APP"
  "$HELPER"
  "$CREDENTIAL_XPC"
  "$FOUNDATION_XPC"
)
expected_identifiers=(
  "com.wayfinder.router.mac"
  "com.wayfinder.router.helper"
  "com.wayfinder.router.helper"
  "com.wayfinder.CredentialBroker"
  "com.wayfinder.FoundationModelBroker"
)
expected_entitlements=(
  "$ROOT_DIR/Packaging/App.entitlements"
  "$ROOT_DIR/Packaging/Helper.entitlements"
  "$ROOT_DIR/Packaging/Helper.entitlements"
  "$ROOT_DIR/Packaging/CredentialBroker.entitlements"
  "$ROOT_DIR/Packaging/FoundationModelBroker.entitlements"
)

for index in "${!signed_components[@]}"; do
  component="${signed_components[$index]}"
  signature_details="$(/usr/bin/codesign --display --verbose=4 "$component" 2>&1)"
  identifier="$(printf '%s\n' "$signature_details" | /usr/bin/awk -F= '/^Identifier=/{print $2; exit}')"
  if [[ "$identifier" != "${expected_identifiers[$index]}" ]]; then
    echo "error: signing identifier for $component is $identifier; expected ${expected_identifiers[$index]}" >&2
    exit 1
  fi
  code_directory_flags="$(
    printf '%s\n' "$signature_details" |
      /usr/bin/sed -n 's/^CodeDirectory .*flags=[^(]*(\([^)]*\)).*/\1/p'
  )"
  if [[ ",$code_directory_flags," != *",runtime,"* ]]; then
    echo "error: hardened runtime is missing for $component" >&2
    exit 1
  fi

  actual_entitlements="$temporary_directory/actual-entitlements-$index.plist"
  normalized_actual="$temporary_directory/normalized-actual-entitlements-$index.plist"
  normalized_expected="$temporary_directory/normalized-expected-entitlements-$index.plist"
  /usr/bin/codesign --display --entitlements :- "$component" > "$actual_entitlements" 2>/dev/null
  /usr/bin/plutil -convert xml1 -o "$normalized_actual" "$actual_entitlements"
  /usr/bin/plutil -convert xml1 -o "$normalized_expected" "${expected_entitlements[$index]}"
  if ! /usr/bin/cmp -s "$normalized_actual" "$normalized_expected"; then
    echo "error: signed entitlements for $component do not match ${expected_entitlements[$index]}" >&2
    exit 1
  fi
done

signing_team() {
  /usr/bin/codesign --display --verbose=4 "$1" 2>&1 | /usr/bin/awk -F= '/^TeamIdentifier=/{print $2; exit}'
}

expected_team="$(signing_team "$APP")"
if [[ "$REQUIRE_DISTRIBUTION_SIGNATURE" == "1" && -z "$expected_team" ]]; then
  echo "error: distribution release has no signing TeamIdentifier" >&2
  exit 1
fi
if [[ "$REQUIRE_DISTRIBUTION_SIGNATURE" == "1" ]]; then
  for component in "${signed_components[@]}"; do
    signature_details="$(/usr/bin/codesign --display --verbose=4 "$component" 2>&1)"
    first_authority="$(printf '%s\n' "$signature_details" | /usr/bin/awk -F= '/^Authority=/{print substr($0, index($0, "=") + 1); exit}')"
    if [[ "$first_authority" != "Developer ID Application:"* ]]; then
      echo "error: $component is not signed by a Developer ID Application identity" >&2
      exit 1
    fi
    if ! printf '%s\n' "$signature_details" | /usr/bin/grep -q '^Timestamp='; then
      echo "error: secure timestamp is missing for $component" >&2
      exit 1
    fi
  done
fi
if [[ -n "$expected_team" ]]; then
  for component in "$HELPER" "$GATEWAY_APP" "$CREDENTIAL_XPC" "$FOUNDATION_XPC"; do
    component_team="$(signing_team "$component")"
    if [[ "$component_team" != "$expected_team" ]]; then
      echo "error: signing TeamIdentifier mismatch for $component" >&2
      exit 1
    fi
  done
fi

if [[ "$REQUIRE_NOTARIZATION" == "1" ]]; then
  /usr/bin/xcrun stapler validate "$APP"
  /usr/sbin/spctl --assess --type execute --verbose=2 "$APP"
fi

# Execute the embedded helper only after its containing app and every nested component have passed
# the required signature, entitlement, identity, Team ID, and notarization checks above.
helper_version_file="$temporary_directory/helper-version.txt"
if ! "$ROOT_DIR/script/run_with_timeout.sh" "$HELPER_VERIFY_TIMEOUT_SECONDS" \
  "$HELPER" --version > "$helper_version_file" 2>/dev/null; then
  echo "error: embedded gateway version check failed or timed out" >&2
  exit 1
fi
bounded_file_size "$helper_version_file" 256 "embedded gateway version output"
if [[ "$(<"$helper_version_file")" != "wayfinder-router $EXPECTED_VERSION" ]]; then
  echo "error: embedded gateway version does not match desktop version $EXPECTED_VERSION" >&2
  exit 1
fi

capabilities="$temporary_directory/capabilities.json"
if ! "$ROOT_DIR/script/run_with_timeout.sh" "$HELPER_VERIFY_TIMEOUT_SECONDS" \
  "$HELPER" capabilities --json > "$capabilities" 2>/dev/null; then
  echo "error: embedded gateway capability check failed or timed out" >&2
  exit 1
fi
bounded_file_size "$capabilities" 65536 "embedded gateway capability output"
capability_schema_version="$(plist_extract_typed "$capabilities" schema_version string)"
capability_implementation="$(plist_extract_typed "$capabilities" implementation string)"
capability_version="$(plist_extract_typed "$capabilities" version string)"
capability_architecture="$(plist_extract_typed "$capabilities" target_architecture string)"
plist_extract_typed "$capabilities" commands array >/dev/null
plist_extract_typed "$capabilities" native_commands array >/dev/null
plist_extract_typed "$capabilities" credential_mechanisms array >/dev/null
if [[ "$capability_schema_version" != "1" ]]; then
  echo "error: unsupported embedded gateway capability schema: $capability_schema_version" >&2
  exit 1
fi
if [[ "$capability_implementation" != "$manifest_implementation" ]]; then
  echo "error: embedded gateway implementation does not match its manifest" >&2
  exit 1
fi
if [[ "$capability_version" != "$EXPECTED_VERSION" ]]; then
  echo "error: embedded gateway capability version $capability_version does not match $EXPECTED_VERSION" >&2
  exit 1
fi
if [[ "$capability_architecture" != "$manifest_architecture" ]]; then
  echo "error: embedded gateway architecture $capability_architecture does not match manifest $manifest_architecture" >&2
  exit 1
fi

plist_array_contains() {
  local plist="$1"
  local key="$2"
  local expected="$3"
  local count=""
  local index=0
  local item=""
  count="$(plist_extract_typed "$plist" "$key" array)"
  if [[ ! "$count" =~ ^[0-9]+$ ]] || (( count > 128 )); then
    return 1
  fi
  for ((index = 0; index < count; index += 1)); do
    item="$(plist_extract_typed "$plist" "$key.$index" string)"
    if [[ "$item" == "$expected" ]]; then
      return 0
    fi
  done
  return 1
}

required_command_count="$(plist_extract_typed "$MANIFEST" required_commands array)"
if [[ ! "$required_command_count" =~ ^[0-9]+$ ]] || (( required_command_count > 64 )); then
  echo "error: helper manifest has an invalid required_commands list" >&2
  exit 1
fi
for ((index = 0; index < required_command_count; index += 1)); do
  required_command="$(plist_extract_typed "$MANIFEST" "required_commands.$index" string)"
  if ! plist_array_contains "$capabilities" commands "$required_command"; then
    echo "error: embedded gateway is missing required command: $required_command" >&2
    exit 1
  fi
done

required_native_command_count="$(plist_extract_typed "$MANIFEST" required_native_commands array)"
if [[ ! "$required_native_command_count" =~ ^[0-9]+$ ]] || (( required_native_command_count > 64 )); then
  echo "error: helper manifest has an invalid required_native_commands list" >&2
  exit 1
fi
for ((index = 0; index < required_native_command_count; index += 1)); do
  required_native_command="$(plist_extract_typed "$MANIFEST" "required_native_commands.$index" string)"
  if ! plist_array_contains "$capabilities" native_commands "$required_native_command"; then
    echo "error: embedded gateway does not implement required native command: $required_native_command" >&2
    exit 1
  fi
done

desktop_native_commands=(
  "route"
  "serve"
  "service"
  "capabilities"
  "app-setup-init"
  "config read-routing"
  "config apply-routing"
)
for required_native_command in "${desktop_native_commands[@]}"; do
  if ! plist_array_contains "$MANIFEST" required_native_commands "$required_native_command"; then
    echo "error: helper manifest omits required Desktop-native command: $required_native_command" >&2
    exit 1
  fi
done

credential_mechanism_count="$(plist_extract_typed "$MANIFEST" credential_mechanisms array)"
if [[ ! "$credential_mechanism_count" =~ ^[0-9]+$ ]] || (( credential_mechanism_count > 32 )); then
  echo "error: helper manifest has an invalid credential_mechanisms list" >&2
  exit 1
fi
for ((index = 0; index < credential_mechanism_count; index += 1)); do
  required_mechanism="$(plist_extract_typed "$MANIFEST" "credential_mechanisms.$index" string)"
  if ! plist_array_contains "$capabilities" credential_mechanisms "$required_mechanism"; then
    echo "error: embedded gateway is missing required credential mechanism: $required_mechanism" >&2
    exit 1
  fi
done

echo "verified Wayfinder $EXPECTED_VERSION ($expected_build_number), arm64"
