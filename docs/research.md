# 公开技术摘要

项目采用 **Ansible-first + 薄 CLI + 用户目录安装**：Ansible 负责远端执行、权限和幂等模板，CLI 只负责校验、交互读取凭据和调用固定 Playbook。

交互式 setup 只面向已准备好的目标用户：该用户必须已存在、可 SSH 登录并具有 sudo 权限，SSH 登录用户名就是配置用户名。向导收集目标地址、SSH 端口、密钥或 SSH 密码；sudo 密码由 Ansible `--ask-become-pass` 在 TTY 中询问。之后使用单一 Inventory 和固定 Ansible 工作流。项目不创建用户、不改密码、不授予 sudo，也不复制公钥。SSH 密码不写入 Inventory、命令行或日志；Ansible 读取权限为 `0600` 的短期凭据文件，运行结束后立即删除。顶层 `--help` 说明如何手动创建用户和授予 sudo。

默认组合为 zsh + Antidote + Powerlevel10k、mise（含 Node.js/npm/npx）、CodexSwitch、NetBird 客户端、fzf/zoxide、Zellij（tmux fallback）、LazyVim 风格 Neovim 和全局 Codex Skills。CodexSwitch 在 Codex CLI 与 Node.js 恢复后安装。NetBird 使用发行版包管理器或固定版本官方二进制安装并启动客户端服务；全新安装在 `/var/lib/netbird/default.json` 预置自建管理端 `https://netbird.zylona.site:443`，已有客户端状态保持不变，恢复本身不登录、不执行 `netbird up`。目标机没有外网时，控制端通过 SSH `-R` 将 HTTP 代理暴露到目标机 loopback；端口随机、失败清理，不写入长期 shell 配置。

setup 不再探测 root 登录或选择用户创建分支。用户负责先准备已有 sudo 用户；项目仅收集该用户的 SSH 登录凭据和 sudo 密码。具体交互及密钥/密码边界见 `docs/setup-interaction-design.md`。

详细选型比较、跨发行版调研、代理方案和历史验证记录保留在 `dev` 分支。
