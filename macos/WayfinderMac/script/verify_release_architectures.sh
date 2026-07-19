#!/usr/bin/env bash
set -euo pipefail

EXPECTED_ARCH="arm64"
LIPO_BIN="${LIPO_BIN:-/usr/bin/lipo}"

if [[ $# -eq 0 ]]; then
  echo "usage: verify_release_architectures.sh <Mach-O>..." >&2
  exit 2
fi

for binary in "$@"; do
  if [[ ! -f "$binary" ]]; then
    echo "error: release executable is missing: $binary" >&2
    exit 1
  fi

  actual_archs="$($LIPO_BIN -archs "$binary")"
  if [[ "$actual_archs" != "$EXPECTED_ARCH" ]]; then
    echo "error: $binary must contain exactly $EXPECTED_ARCH; found: $actual_archs" >&2
    exit 1
  fi
done
