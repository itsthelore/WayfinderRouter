#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNNER="$ROOT_DIR/script/run_with_timeout.sh"

output="$("$RUNNER" 2 /usr/bin/printf 'ready')"
if [[ "$output" != "ready" ]]; then
  echo "expected bounded runner to preserve stdout" >&2
  exit 1
fi

if "$RUNNER" 2 /bin/sh -c 'exit 7'; then
  echo "expected bounded runner to preserve a nonzero status" >&2
  exit 1
else
  status="$?"
fi
if [[ "$status" != "7" ]]; then
  echo "expected exit 7; found $status" >&2
  exit 1
fi

started="$(date +%s)"
if "$RUNNER" 1 /bin/sleep 30 >/dev/null 2>&1; then
  echo "expected hanging command to time out" >&2
  exit 1
else
  status="$?"
fi
elapsed=$(($(date +%s) - started))
if [[ "$status" != "124" ]]; then
  echo "expected timeout exit 124; found $status" >&2
  exit 1
fi
if (( elapsed > 3 )); then
  echo "bounded runner exceeded its termination allowance" >&2
  exit 1
fi

echo "bounded command tests passed"
