#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "用法: $0 user@host [ssh-config]" >&2
  exit 2
fi

destination="$1"
config_path="${2:-}"
ssh_args=(-o BatchMode=yes -o ConnectTimeout=8 -o ControlMaster=no -o ControlPath=none)
if [[ -n "$config_path" ]]; then
  ssh_args+=(-F "$config_path")
fi

start_ns="$(date +%s%N)"
error_file="$(mktemp "${TMPDIR:-/tmp}/remote-dev-ssh-baseline.XXXXXX")"
trap 'rm -f "$error_file"' EXIT
if /usr/bin/ssh "${ssh_args[@]}" "$destination" true >/dev/null 2>"$error_file"; then
  rc=0
else
  rc=$?
fi
end_ns="$(date +%s%N)"
elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))

printf 'ssh_connect_rc=%s\n' "$rc"
printf 'ssh_connect_ms=%s\n' "$elapsed_ms"
if [[ "$rc" -ne 0 ]]; then
  printf 'ssh_error_class=connection_failed\n'
fi
exit "$rc"
