#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${RELEASE_VERSION:-$(tr -d '[:space:]' < "$root_dir/VERSION")}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$ ]] || { echo "invalid release version: $version" >&2; exit 2; }
dist_dir="$root_dir/dist"
stage_dir="$(mktemp -d)"
trap 'rm -rf "$stage_dir"' EXIT
pkg="remote-dev-orchestrator-v${version}-linux"
mkdir -p "$stage_dir/$pkg/lib/remote_dev" "$stage_dir/$pkg/bin" "$dist_dir"
cp -R "$root_dir/src/remote_dev/orchestrator" "$stage_dir/$pkg/lib/remote_dev/"
cp "$root_dir/src/remote_dev/ssh_integration.py" "$root_dir/src/remote_dev/tssh.py" "$stage_dir/$pkg/lib/remote_dev/"
cp "$root_dir/scripts/remote_dev_ssh_hook.py" "$stage_dir/$pkg/bin/remote-dev-ssh-hook"
find "$stage_dir/$pkg" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$stage_dir/$pkg" -type f -name '*.pyc' -delete
cp "$root_dir/release/install" "$root_dir/release/uninstall" "$root_dir/release/rollback" "$stage_dir/$pkg/"
cp "$root_dir/systemd/tssh.service" "$root_dir/systemd/tssh.socket" "$stage_dir/$pkg/"
cp "$root_dir/LICENSE" "$stage_dir/$pkg/LICENSE"
cat > "$stage_dir/$pkg/VERSION" <<EOF
$version
EOF
cat > "$stage_dir/$pkg/bin/tssh-server" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
script_path="${BASH_SOURCE[0]}"
while [[ -L "$script_path" ]]; do
  link_target="$(readlink "$script_path")"
  if [[ "$link_target" = /* ]]; then
    script_path="$link_target"
  else
    script_path="$(dirname "$script_path")/$link_target"
  fi
done
base_dir="$(cd "$(dirname "$script_path")/.." && pwd)"
export PYTHONPATH="$base_dir/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python3}" -m remote_dev.orchestrator "$@"
EOF
ln -s tssh-server "$stage_dir/$pkg/bin/remote-dev-orchestrator-server"
cat > "$stage_dir/$pkg/bin/tssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
script_path="${BASH_SOURCE[0]}"
while [[ -L "$script_path" ]]; do
  link_target="$(readlink "$script_path")"
  if [[ "$link_target" = /* ]]; then script_path="$link_target"; else script_path="$(dirname "$script_path")/$link_target"; fi
done
base_dir="$(cd "$(dirname "$script_path")/.." && pwd)"
export PYTHONPATH="$base_dir/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python3}" -m remote_dev.tssh "$@"
EOF
chmod 0755 "$stage_dir/$pkg/bin/tssh-server" "$stage_dir/$pkg/bin/remote-dev-ssh-hook" "$stage_dir/$pkg/bin/tssh" "$stage_dir/$pkg/install" "$stage_dir/$pkg/uninstall" "$stage_dir/$pkg/rollback"
tar_args=(--sort=name --owner=0 --group=0 --numeric-owner -czf "$dist_dir/$pkg.tar.gz" -C "$stage_dir" "$pkg")
if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then tar_args+=(--mtime="@$SOURCE_DATE_EPOCH"); fi
tar "${tar_args[@]}"
(cd "$dist_dir" && sha256sum "$pkg.tar.gz" > "$pkg.tar.gz.sha256")
echo "$dist_dir/$pkg.tar.gz"
