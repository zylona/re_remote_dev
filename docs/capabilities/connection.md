# P1 SSH 连接与临时代理能力契约

## 目标

在任何远端写操作前，验证目标 Linux、Inventory schema、目标用户、包管理器和 SSH remote forwarding 能力；当执行端有 HTTP 代理时，为本轮运行提供仅监听目标 loopback 的临时出口。

## 输入

- `inventory/production.yml` 中的 `ansible_host`、端口、用户和 key 路径；
- `remote_dev.target_user`；
- `remote_dev.proxy.mode`：`auto`、`required` 或 `off`；
- `remote_dev.proxy.host/port`：执行端已有 HTTP 代理，不是远端持久配置。

## 登录实现（参考 re_debian）

目标设备必须预先配置与执行端匹配的 SSH 公钥；连接用户必须是具备 sudo 权限的目标用户。本项目不负责密码引导、公钥下发、软件安装或本地密码管理。

- `ansible_user` 定义 SSH 登录用户，`remote_dev.target_user` 定义最终配置用户；两者不同的切换必须由用户预先保证权限和公钥。
- `remote_dev.ssh_private_key_file` 或主机级 `ansible_ssh_private_key_file` 指向执行端私钥。私钥内容不复制到仓库、不上传目标机。
- 严格校验 SSH host key；连接失败直接报告，不尝试密码 fallback。
- 项目不维护 `.local/credentials.yml`；sudo 密码只通过 Ansible `--ask-become-pass` 在 TTY 中交互输入。
- 目标机和执行端均不需要 sshpass。
- tmux 是 fallback 组件，必须由用户预先安装；项目只部署其用户级配置，不负责跨发行版安装。
- 当探测到 TCP forwarding 被禁用且代理模式为 `required`/自动启用时，脚本创建 `/etc/ssh/sshd_config.d/99-remote-dev-forwarding.conf`，写入 `AllowTcpForwarding yes`，同时支持代理 `-R 4227` 和临时 Codex OAuth 回调 `-L 1455`；先执行 `sshd -t` 校验，再 reload sshd；失败时恢复备份并报告 `FORWARDING_DISABLED`。
- 普通 SSH 会话不再自动配置任何转发。远端 `rd-help proxy` 根据 `SSH_CONNECTION` 实时打印本机一次性后台 `ssh -fNT -R 4227...` 命令，可被同一主机的多个会话复用；`rd-help codex` 另行打印一次性的 OAuth `-L 1455` 命令。
- 提权流程为交互式：验证目标用户存在 sudo 且可通过 `--ask-become-pass` 提权。sudo 不存在或不可用时，明确列出缺失能力并停止。密码只驻留内存。

## 运行时契约

- 隧道使用 `SSH -R 127.0.0.1:<随机端口>:<执行端代理>`；
- 端口范围默认 40000–60000；`ExitOnForwardFailure=yes`；
- 临时转发禁用 SSH multiplexing，避免旧 master 和用户切换造成端口冲突；
- 通过环境变量将 `http_proxy/https_proxy/no_proxy` 传给需要下载的 Role；
- 成功、失败、断线、Ctrl-C 都清理隧道和临时配置；
- 不支持密码写入命令行，不写入 Inventory，不持久化代理 URL。

## 状态

`READY`、`READY_WITH_WARNINGS`、`FORWARDING_DISABLED`、`UNREACHABLE`、`FAILED`。P1 verify 只读，不会修复 host key、开启 sshd 转发或安装包。

## 手动 Codex OAuth 转发

用户完成恢复后可在远端直接运行 `rd-help proxy` 或 `rd-help codex`。脚本不建立隧道、不修改 SSH 配置，
只读取当前 `SSH_CONNECTION` 并打印带远端用户和地址的本地命令。用户在本机另开终端
执行命令即可建立共享后台隧道；登录完成后按脚本给出的 `pgrep`/`kill` 步骤清理。
`rd-help status` 可检查远端端口和会话地址状态。

## 测试

覆盖代理监听/不可用、随机端口范围、loopback 绑定、multiplexing 禁用、sshd 拒绝转发、进程退出清理和无代理模式。
