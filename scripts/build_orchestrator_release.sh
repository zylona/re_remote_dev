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
find "$stage_dir/$pkg" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$stage_dir/$pkg" -type f -name '*.pyc' -delete
cp "$root_dir/release/install" "$root_dir/release/uninstall" "$stage_dir/$pkg/"
cp "$root_dir/systemd/remote-dev-orchestrator.service" "$root_dir/systemd/remote-dev-orchestrator.socket" "$stage_dir/$pkg/"
cp "$root_dir/LICENSE" "$stage_dir/$pkg/LICENSE"
cat > "$stage_dir/$pkg/VERSION" <<EOF
$version
EOF
cat > "$stage_dir/$pkg/bin/remote-dev-orchestrator-server" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$base_dir/lib${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python3}" -m remote_dev.orchestrator "$@"
EOF
chmod 0755 "$stage_dir/$pkg/bin/remote-dev-orchestrator-server" "$stage_dir/$pkg/install" "$stage_dir/$pkg/uninstall"
tar_args=(--sort=name --owner=0 --group=0 --numeric-owner -czf "$dist_dir/$pkg.tar.gz" -C "$stage_dir" "$pkg")
if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then tar_args+=(--mtime="@$SOURCE_DATE_EPOCH"); fi
tar "${tar_args[@]}"
sha256sum "$dist_dir/$pkg.tar.gz" > "$dist_dir/$pkg.tar.gz.sha256"
echo "$dist_dir/$pkg.tar.gz"
