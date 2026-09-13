#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/remote-dev-uv-cache}"
uv run --locked ansible-inventory -i inventory/example.yml --list >/dev/null
uv run --locked ansible-playbook -i inventory/example.yml playbooks/site.yml --syntax-check
uv run --locked ansible-playbook -i inventory/example.yml playbooks/verify.yml --syntax-check
uv run --locked ansible-lint roles playbooks
uv run --locked pytest -q
echo "release checks passed"
