# remote-dev

[![CI](https://github.com/zylona/re_remote_dev/actions/workflows/check.yml/badge.svg)](https://github.com/zylona/re_remote_dev/actions/workflows/check.yml)
[![Latest release](https://img.shields.io/github/v/release/zylona/re_remote_dev?display_name=tag)](https://github.com/zylona/re_remote_dev/releases)
[![License](https://img.shields.io/github/license/zylona/re_remote_dev)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux-333?logo=linux)](#支持范围)

用一次受控运行，把一台可 SSH 登录的 Linux 主机恢复成现代远程开发环境：zsh、Antidote、Powerlevel10k、mise、fzf、zoxide、Zellij、Neovim 和 Codex CLI。项目采用 Ansible-first 设计，支持无外网目标机的临时 HTTP 代理，并保持普通 `ssh user@host` 的使用习惯。

> 当前版本适合个人和小规模团队试用。默认不安装远端常驻 Agent、不开放公网端口、不保存密码或 API Token。

## 特性

- **单台设备向导**：交互输入地址、用户、密钥或 SSH 密码，自动探测发行版和提权方式。
- **跨发行版基础恢复**：识别 apt、dnf/yum、apk、pacman、zypper；目标机无需预装 Python。
- **本机习惯**：静态 Antidote bundle、Lean 风格 p10k、mise、fzf/zoxide、LazyVim + Omarchy 风格配置。
- **多窗口复用**：Zellij 默认，tmux fallback；本地 ControlMaster 复用 SSH 传输和端口转发。
- **受控代理**：把控制端 `127.0.0.1:4227` 临时转发到目标机 loopback，失败和退出自动清理。
- **可验证、可重跑**：固定 Play 顺序、独立只读 verify、第二次 apply 幂等检查。
- **可发布制品**：本地编排器可作为 GitHub Release tarball 独立安装和升级。

## 工作方式

```text
./re-remote setup / bootstrap
        │
        ├─ 本地服务预检与安装（可选）
        ├─ SSH / sudo / 发行版 / 代理预检
        ├─ Ansible: bootstrap → shell → tools → workspace → verify
        └─ 退出时清理临时凭据、代理和连接
```

远端修改集中在固定的 Ansible Plays；CLI 只负责校验、交互读取凭据和调用 Playbook。密码仅存在于本轮 TTY/Ansible 进程内，不写入 Inventory、日志、Git 或远端 dotfiles。

## 快速开始

### 1. 安装控制端依赖

```bash
git clone git@github.com:zylona/re_remote_dev.git
cd re_remote_dev
mise trust
mise install
uv sync --locked
./re-remote doctor
```

### 2. 一键恢复一台设备

```bash
./re-remote setup
```

向导会依次询问目标地址、SSH 用户、认证方式、最终配置用户和 sudo/root 提权密码。若目标机无外网，请先在控制端启动 HTTP 代理并监听 `127.0.0.1:4227`；恢复期间项目会自动建立临时 SSH 反向转发。

### 3. 验证结果

```bash
./re-remote verify -i inventory/production.yml
```

完成后重新连接目标机即可使用：

```bash
ssh developer@203.0.113.10
zellij --layout dev
nvim
codex
```

## 配置与再次运行

批量模式尚未作为主入口，但可以用被忽略的 Inventory 重复恢复同一设备：

```bash
cp inventory/production.example.yml inventory/production.yml
chmod 600 inventory/production.yml
$EDITOR inventory/production.yml
./re-remote config-validate -i inventory/production.yml
./re-remote bootstrap -i inventory/production.yml
```

示例只使用文档保留地址和通用用户名；真实 Inventory、密码和私钥必须留在本机：

```yaml
all:
  vars:
    remote_dev:
      target_user: developer
      initial_user: developer
      ssh_private_key_file: ~/.ssh/id_ed25519
      profile: default
      proxy: {mode: auto, host: 127.0.0.1, port: 4227}
  children:
    remote_dev_targets:
      hosts:
        example-host:
          ansible_host: 203.0.113.10
          ansible_user: developer
          ansible_ssh_private_key_file: ~/.ssh/id_ed25519
```

## 本地编排器

编排器是可供其他项目复用的 systemd user socket 服务：

```bash
./install-local-service --yes
./re-remote orchestrator doctor
systemctl --user status remote-dev-orchestrator.socket
```

也可以从 [Releases](https://github.com/zylona/re_remote_dev/releases) 下载 `remote-dev-orchestrator-*-linux.tar.gz`，先校验 `.sha256`，再运行包内安装器。Release asset 用于长期依赖，Actions artifact 仅用于短期 CI 传递。详见 [docs/release.md](docs/release.md)。

## 代理与 Codex

查看当前目标专用的操作提示：

```bash
rd-help proxy
rd-help codex
```

普通 shell 代理使用远端 `127.0.0.1:4227`。Codex ChatGPT 登录额外使用 OAuth 回调端口 `1455`，浏览器始终在控制端打开；登录完成后回调转发自动结束。若没有代理，使用 `proxy.mode: off`；设为 `required` 可在代理不可用时 fail-fast。

## 常用命令

| 命令 | 作用 |
| --- | --- |
| `./re-remote setup` | 交互式单机恢复 |
| `./re-remote bootstrap -i <inventory>` | 按 Inventory 恢复并刷新本地服务 |
| `./re-remote verify -i <inventory>` | 只读验收 |
| `./re-remote config-validate -i <inventory>` | 校验配置 |
| `./re-remote orchestrator status` | 查看本地编排器 |
| `./re-remote uninstall -i <inventory>` | 移除远端受管配置 |
| `./re-remote local-service-install` | 单独安装/刷新本地服务 |

## 支持范围

目标是常见 Linux 发行版；已在 Debian 13 amd64、openEuler 24.03 amd64 和 Arch Linux amd64 做过真实验证。其他发行版、ARM、极简系统和离线缓存场景需要用户先执行 verify 并反馈差异。目标机必须能通过 SSH 登录，且存在 sudo 或可用的 su-root 提权通道。

## 安全边界

- 不提交密码、Token、私钥或真实 Inventory；`.local/` 和生产 Inventory 已加入 `.gitignore`。
- 默认严格校验 host key，只监听目标机 loopback，不开启 `GatewayPorts`。
- 上游二进制使用固定版本或校验和；不会在每次运行直接执行未经审阅的 `curl | sh`。
- Docker 代理仅在目标机已有 Docker 且配置变化时重启 daemon。

## 开发与贡献

```bash
uv sync --locked
./scripts/release_check.sh
```

提交前至少通过 inventory 校验、Playbook syntax-check、ansible-lint、pytest 和 `git diff --check`。请不要提交 `.local/`、真实 Inventory、SSH 配置或运行报告；问题反馈请附脱敏后的命令输出和发行版信息。

## 文档

- [发布与安装](docs/release.md)
- [发布验收清单](docs/release-checklist.md)
- [能力契约：连接、Neovim、编排器](docs/capabilities/)

贡献流程和安全问题处理见 [CONTRIBUTING.md](CONTRIBUTING.md) 与 [SECURITY.md](SECURITY.md)。

## 许可证

本项目采用 [MIT License](LICENSE)。
