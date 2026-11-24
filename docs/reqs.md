# 公开需求摘要

`remote-dev` 面向已能 SSH 登录的 Linux 主机，使用固定的 Ansible 工作流恢复现代远程开发环境。

## 能力契约

- 支持常见 Linux 发行版和 apt、dnf/yum、apk、pacman、zypper；目标机无需预装 Python。
- 交互式 setup 支持自定义 SSH 服务端口，默认 `22`；探测、Ansible 和临时代理隧道使用同一端口。
- setup 只接受一个已经存在、可 SSH 登录且具有 sudo 权限的目标用户。该用户名同时是 SSH 登录用户名和最终配置用户名；项目不创建用户、不改用户密码、不授予或调整 sudo 权限、不向目标用户复制 SSH 公钥。
- setup 收集目标主机、SSH 端口（默认 `22`）、用户名、SSH 认证方式（密钥或密码）及对应凭据；执行 Ansible 时通过 TTY 询问 sudo 提权密码，然后以单一 Inventory 和固定工作流恢复环境。
- 在顶层 `--help` 中以纯文字说明如何手动创建目标用户并授予 sudo；help 只显示说明，不连接或修改设备。
- `tssh` 工具及管理子命令在未传 `-p` 时使用 OpenSSH 有效配置中的端口；显式 `-p` 覆盖该值。编排器按实际 `HostName:Port` 识别 endpoint，允许不同设备及端口并发使用。
- 恢复 zsh、Antidote、Powerlevel10k、mise、fzf、zoxide、Zellij、tmux fallback、Neovim 和 Codex CLI。
- 通过 mise 管理的 Node.js/npm/npx 恢复预设的全局 Codex Skills，并由独立只读 verify 检查安装完整性。
- 在 Codex CLI 与 mise Node.js 均可用后安装 CodexSwitch，为目标用户保存多个 Codex 账号并支持按账号启动。
- 跨 Linux 发行版安装 NetBird 客户端并启动其系统服务；新安装默认使用自建管理端 `https://netbird.zylona.site:443`，已有配置不覆盖；不执行登录或加入网络操作。
- 目标机无外网时，可通过控制端 HTTP 代理建立临时 SSH 反向转发。
- 密码只在本轮交互中使用，不写入 Inventory、日志、Git 或远端配置。
- setup 明确要求用户预先准备目标用户和 sudo；交互设计见 `docs/setup-interaction-design.md`。
- 所有 apply 必须幂等，并提供独立只读 verify。
- 默认不安装远端常驻 Agent、不开放公网端口、不强制修改 SSH 安全策略。

详细历史需求和设计背景保留在 `dev` 分支的内部文档中。
