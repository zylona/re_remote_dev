# P7 Debian 13 amd64 发布验收

## 自动检查

- `scripts/release_check.sh`：Inventory、Ansible syntax、ansible-lint、pytest；
- CI 在 push/pull request 上执行同一检查；
- `uv.lock` 与 `pyproject.toml` 保持同步。

## qualification 回归

1. 确认 SSH 私钥登录和目标用户 sudo；
2. 执行 `remote-dev config-validate`、`doctor`；
3. 执行 `remote-dev apply`，交互输入 sudo 密码；
4. 检查 P1 临时代理、P2 前置条件、P3 zsh、P4 工具和 Zellij 布局；
5. 重复 apply，确认无无意义变更；
6. 执行 `remote-dev verify`；
7. 执行 `remote-dev uninstall`，确认原 `.zshrc` 可恢复；
8. 保存虚拟机快照作为 Debian 13 amd64 MVP 基线。

## 范围声明

本阶段只证明 Debian 13 amd64。Fedora/RHEL、Arch、Alpine、ARM64 和其他 libc 组合必须在新增测试机后单独验收。
