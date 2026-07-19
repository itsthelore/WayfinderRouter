#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || ! "$1" =~ ^[1-9][0-9]*$ || "$1" -gt 300 ]]; then
  echo "usage: run_with_timeout.sh <1-300 seconds> <command> [arguments...]" >&2
  exit 2
fi

timeout_seconds="$1"
shift
child_pid=""

terminate_child() {
  if [[ -n "$child_pid" ]] && /bin/kill -0 "$child_pid" 2>/dev/null; then
    /bin/kill -TERM "$child_pid" 2>/dev/null || true
    /bin/sleep 0.2
    /bin/kill -KILL "$child_pid" 2>/dev/null || true
  fi
}

trap terminate_child EXIT
trap 'terminate_child; exit 130' INT
trap 'terminate_child; exit 143' TERM

"$@" &
child_pid="$!"
deadline=$((SECONDS + timeout_seconds))

while /bin/kill -0 "$child_pid" 2>/dev/null; do
  if (( SECONDS >= deadline )); then
    terminate_child
    if wait "$child_pid" 2>/dev/null; then
      :
    fi
    child_pid=""
    echo "error: command exceeded the configured timeout" >&2
    exit 124
  fi
  /bin/sleep 0.1
done

if wait "$child_pid"; then
  status=0
else
  status="$?"
fi
child_pid=""
exit "$status"
