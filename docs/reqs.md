# 公开需求摘要

`remote-dev` 面向已能 SSH 登录的 Linux 主机，使用固定的 Ansible 工作流恢复现代远程开发环境。

## 能力契约

- 支持常见 Linux 发行版和 apt、dnf/yum、apk、pacman、zypper；目标机无需预装 Python。
- 恢复 zsh、Antidote、Powerlevel10k、mise、fzf、zoxide、Zellij、tmux fallback、Neovim 和 Codex CLI。
- 通过 mise 管理的 Node.js/npm/npx 恢复预设的全局 Codex Skills，并由独立只读 verify 检查安装完整性。
- 在 Codex CLI 与 mise Node.js 均可用后安装 CodexSwitch，为目标用户保存多个 Codex 账号并支持按账号启动。
- 目标机无外网时，可通过控制端 HTTP 代理建立临时 SSH 反向转发。
- 密码只在本轮交互中使用，不写入 Inventory、日志、Git 或远端配置。
- 所有 apply 必须幂等，并提供独立只读 verify。
- 默认不安装远端常驻 Agent、不开放公网端口、不强制修改 SSH 安全策略。

详细历史需求和设计背景保留在 `dev` 分支的内部文档中。
