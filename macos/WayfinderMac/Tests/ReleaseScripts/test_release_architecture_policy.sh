#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/wayfinder-arch-policy.XXXXXX")"
trap 'rm -rf "$temporary_directory"' EXIT

binary="$temporary_directory/example"
fake_lipo="$temporary_directory/lipo"
touch "$binary"
cat > "$fake_lipo" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "-archs" ]]
printf '%s\n' "${FAKE_LIPO_ARCHS:?}"
SCRIPT
chmod +x "$fake_lipo"

FAKE_LIPO_ARCHS="arm64" LIPO_BIN="$fake_lipo" \
  "$ROOT_DIR/script/verify_release_architectures.sh" "$binary"

if FAKE_LIPO_ARCHS="arm64 x86_64" LIPO_BIN="$fake_lipo" \
  "$ROOT_DIR/script/verify_release_architectures.sh" "$binary" >/dev/null 2>&1; then
  echo "error: architecture policy accepted a universal binary" >&2
  exit 1
fi

if FAKE_LIPO_ARCHS="x86_64" LIPO_BIN="$fake_lipo" \
  "$ROOT_DIR/script/verify_release_architectures.sh" "$binary" >/dev/null 2>&1; then
  echo "error: architecture policy accepted an Intel-only binary" >&2
  exit 1
fi

echo "release architecture policy tests passed"
