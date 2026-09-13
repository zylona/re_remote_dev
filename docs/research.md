# 公开技术摘要

项目采用 **Ansible-first + 薄 CLI + 用户目录安装**：Ansible 负责远端执行、权限和幂等模板，CLI 只负责校验、交互读取凭据和调用固定 Playbook。

默认组合为 zsh + Antidote + Powerlevel10k、mise、fzf/zoxide、Zellij（tmux fallback）和 LazyVim 风格 Neovim。目标机没有外网时，控制端通过 SSH `-R` 将 HTTP 代理暴露到目标机 loopback；端口随机、失败清理，不写入长期 shell 配置。

详细选型比较、跨发行版调研、代理方案和历史验证记录保留在 `dev` 分支。
