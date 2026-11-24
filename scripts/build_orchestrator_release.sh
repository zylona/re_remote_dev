#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${RELEASE_VERSION:-$(tr -d '[:space:]' < "$root_dir/VERSION")}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$ ]] || { echo "invalid release version: $version" >&2; exit 2; }
command -v uv >/dev/null || { echo "uv is required to build the self-contained Linux artifact" >&2; exit 1; }
dist_dir="$root_dir/dist"
stage_dir="$(mktemp -d)"
trap 'rm -rf "$stage_dir"' EXIT
pkg="tssh-v${version}-linux-x86_64"
product="$stage_dir/$pkg"
mkdir -p "$product/lib/remote_dev" "$product/lib/python/site-packages" "$product/bin" "$dist_dir"

# Build-time downloads only: ship CPython and all locked runtime dependencies.
if [[ -n "${TSSH_PYTHON_SOURCE_DIR:-}" ]]; then
  cp -a "$TSSH_PYTHON_SOURCE_DIR/." "$product/python/"
else
  uv python install 3.14 --install-dir "$product/python"
fi
python_bin="$(find "$product/python" -type f -path '*/bin/python3.14' -print -quit)"
[[ -n "$python_bin" ]] || { echo "could not locate bundled CPython 3.14" >&2; exit 1; }
"$python_bin" -c 'import sys; assert sys.version_info[:2] == (3, 14); print("Bundled", sys.version.split()[0])'
uv export --project "$root_dir" --locked --no-dev --group runtime --no-emit-project --format requirements-txt \
  --output-file "$stage_dir/runtime-requirements.txt"
uv pip install --python "$python_bin" --target "$product/lib/python/site-packages" \
  --requirements "$stage_dir/runtime-requirements.txt"

cp -R "$root_dir/src/remote_dev/." "$product/lib/remote_dev/"
cp -R "$root_dir/playbooks" "$root_dir/roles" "$root_dir/templates" "$product/"
mkdir -p "$product/systemd" "$product/scripts"
cp "$root_dir/systemd/tssh.service" "$root_dir/systemd/tssh.socket" "$product/systemd/"
cp "$root_dir/scripts/remote_dev_ssh_hook.py" "$root_dir/scripts/remote_preview_helper.py" "$root_dir/scripts/tdl" "$product/scripts/"
cp "$root_dir/ansible.cfg" "$product/ansible.cfg"
cp "$root_dir/scripts/remote_dev_ssh_hook.py" "$product/bin/remote-dev-ssh-hook"
cp "$root_dir/scripts/tdl" "$product/bin/tdl"
cp "$root_dir/release/install" "$root_dir/release/uninstall" "$root_dir/release/rollback" "$product/"
cp "$root_dir/systemd/tssh.service" "$root_dir/systemd/tssh.socket" "$product/"
cp "$root_dir/LICENSE" "$product/LICENSE"
printf '%s\n' "$version" > "$product/VERSION"

cat > "$product/bin/tssh-python" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
script_path="${BASH_SOURCE[0]}"
while [[ -L "$script_path" ]]; do
  link_target="$(readlink "$script_path")"
  if [[ "$link_target" = /* ]]; then script_path="$link_target"; else script_path="$(dirname "$script_path")/$link_target"; fi
done
base_dir="$(cd "$(dirname "$script_path")/.." && pwd)"
python_bin="$(find "$base_dir/python" -type f -path '*/bin/python3.14' -print -quit)"
[[ -n "$python_bin" ]] || { echo 'tssh: bundled Python runtime is missing' >&2; exit 1; }
export PYTHONPATH="$base_dir/lib/python/site-packages:$base_dir/lib${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$(dirname "$python_bin"):$base_dir/bin:$PATH"
exec "$python_bin" "$@"
EOF
cat > "$product/bin/tssh-server" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
script_path="${BASH_SOURCE[0]}"
while [[ -L "$script_path" ]]; do
  link_target="$(readlink "$script_path")"
  if [[ "$link_target" = /* ]]; then script_path="$link_target"; else script_path="$(dirname "$script_path")/$link_target"; fi
done
base_dir="$(cd "$(dirname "$script_path")/.." && pwd)"
exec "$base_dir/bin/tssh-python" -m remote_dev.orchestrator "$@"
EOF
cat > "$product/bin/tssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
script_path="${BASH_SOURCE[0]}"
while [[ -L "$script_path" ]]; do
  link_target="$(readlink "$script_path")"
  if [[ "$link_target" = /* ]]; then script_path="$link_target"; else script_path="$(dirname "$script_path")/$link_target"; fi
done
base_dir="$(cd "$(dirname "$script_path")/.." && pwd)"
exec "$base_dir/bin/tssh-python" -m remote_dev.tssh "$@"
EOF
cat > "$product/bin/remote-dev" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
script_path="${BASH_SOURCE[0]}"
while [[ -L "$script_path" ]]; do
  link_target="$(readlink "$script_path")"
  if [[ "$link_target" = /* ]]; then script_path="$link_target"; else script_path="$(dirname "$script_path")/$link_target"; fi
done
base_dir="$(cd "$(dirname "$script_path")/.." && pwd)"
export ANSIBLE_CONFIG="$base_dir/ansible.cfg"
exec "$base_dir/bin/tssh-python" -m remote_dev "$@"
EOF
chmod 0755 "$product/bin/"* "$product/install" "$product/uninstall" "$product/rollback"

export PYTHONPATH="$product/lib/python/site-packages:$product/lib"
export ANSIBLE_CONFIG="$product/ansible.cfg"
"$python_bin" -m remote_dev.tssh --help >/dev/null
"$python_bin" -m ansible.cli.playbook --version >/dev/null
find "$product" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$product" -type f -name '*.pyc' -delete

tar_args=(--sort=name --owner=0 --group=0 --numeric-owner -czf "$dist_dir/$pkg.tar.gz" -C "$stage_dir" "$pkg")
if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then tar_args+=(--mtime="@$SOURCE_DATE_EPOCH"); fi
tar "${tar_args[@]}"
(cd "$dist_dir" && sha256sum "$pkg.tar.gz" > "$pkg.tar.gz.sha256")
echo "$dist_dir/$pkg.tar.gz"
