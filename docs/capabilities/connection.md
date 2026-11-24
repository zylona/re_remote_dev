# P1 SSH 连接与临时代理能力契约

## 目标

在任何远端写操作前，验证目标 Linux、Inventory schema、目标用户、包管理器和 SSH remote forwarding 能力；当执行端有 HTTP 代理时，为本轮运行提供仅监听目标 loopback 的临时出口。

## 输入

- `inventory/production.yml` 中的 `ansible_host`、端口、用户和 key 路径；
- `setup` 向导中的 SSH 端口，默认 `22`；
- `remote_dev.target_user`；
- `remote_dev.proxy.mode`：`auto`、`required` 或 `off`；
- `remote_dev.proxy.host/port`：执行端已有 HTTP 代理，不是远端持久配置。

## 登录实现（参考 re_debian）

目标设备必须预先存在一个具备 sudo 权限的目标用户。本项目不负责创建用户、改动用户密码、授予 sudo 或下发 SSH 公钥。`setup` 支持密钥和密码两种 SSH 认证；留空私钥路径时复用本机 SSH 配置/ssh-agent。

- `ansible_user` 与 `remote_dev.target_user` 必须指向同一个已存在的目标用户。
- `setup` 收集的 SSH 端口会同步用于临时反向代理隧道和生成的 `ansible_port`，并参与目标身份摘要；不会只让登录走自定义端口而让后续阶段回退到 `22`。
- `tssh`、`put`、`persist`、`stop` 和 `reclaim` 通过 `ssh -G` 解析 SSH 别名对应的有效 `HostName`、`User`、`Port` 和 `IdentityFile`。省略 `-p` 时服从 `~/.ssh/config`，显式 `-p` 覆盖配置；编排器以解析后的主机和端口区分 endpoint。
- `remote_dev.ssh_private_key_file` 或主机级 `ansible_ssh_private_key_file` 指向执行端私钥。私钥内容不复制到仓库、不上传目标机。
- 严格校验 SSH host key；连接失败直接报告，不尝试密码 fallback。
- 项目不维护 `.local/credentials.yml`；SSH 密码由 setup 临时收集，通过权限为 `0600` 的短期凭据文件交给 Ansible，结束后删除。sudo 密码只通过 Ansible `--ask-become-pass` 在 TTY 中输入。
- 密码 SSH 需要执行端 `sshpass`；密钥认证不需要。
- tmux 是 fallback 组件，必须由用户预先安装；项目只部署其用户级配置，不负责跨发行版安装。
- 当探测到 TCP forwarding 被禁用且代理模式为 `required`/自动启用时，脚本创建 `/etc/ssh/sshd_config.d/99-remote-dev-forwarding.conf`，写入 `AllowTcpForwarding yes`，同时支持代理 `-R 4227` 和临时 Codex OAuth 回调 `-L 1455`；先执行 `sshd -t` 校验，再 reload sshd；失败时恢复备份并报告 `FORWARDING_DISABLED`。
- 普通 SSH 会话不再自动配置任何转发。远端 `rd-help proxy` 根据 `SSH_CONNECTION` 实时打印本机一次性后台 `ssh -fNT -R 4227...` 命令，可被同一主机的多个会话复用；`rd-help codex` 另行打印一次性的 OAuth `-L 1455` 命令。
- `tssh user@host` 会把本轮已建立的 4227 代理以环境变量注入远端登录 shell（同时设置大小写两套
  `HTTP_PROXY/HTTPS_PROXY`），因此 Nvim、Git、Treesitter 等子进程直接继承代理；这不写入远端
  配置文件，也不改变普通 `ssh` 或 VS Code Remote-SSH 的行为。
- 用户负责确认目标用户已有 sudo 权限；setup 只使用用户提供的 sudo 密码，不创建用户或修改授权。sudo 缺失、授权不足或密码错误时，Ansible 失败并停止。

## 运行时契约

- 隧道使用 `SSH -R 127.0.0.1:<随机端口>:<执行端代理>`；
- SSH 可以连接自定义公网端口；项目转发监听仅绑定目标机 `127.0.0.1`，不要求目标增加公网开放端口；
- 不同目标可使用不同公网 SSH 端口并发运行；代理/事件/预览反向转发仍通过各自 SSH TCP 连接建立，远端 loopback 端口仍为 `4227/4228`；
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

远程恢复同时部署 `tdl`。请先进入 tmux，再运行 `tdl codex`；它会在当前目录创建或复用当前 tmux 会话的
三 pane `codex` 工作窗口：左上 `nvim .`、右上 Codex、下方交互式 shell（上方约 75%，Neovim
约 60%，Codex 约 40%）；优先使用 `tcodex`，
没有时回退官方 `codex`。默认使用当前 tmux 会话；已有同名窗口只切换，并自动补齐旧窗口缺失的 pane。

## 测试

覆盖代理监听/不可用、随机端口范围、loopback 绑定、multiplexing 禁用、sshd 拒绝转发、进程退出清理和无代理模式。
