# tssh 产品统一与制品化落地计划

## 产品目标

交付一个用户级 tssh 产品和安装制品。`tssh user@host` 保持为 SSH 日常入口；`tssh --setup` 通过同一制品中的私有 Python/Ansible 运行时恢复目标开发环境。普通 `ssh`、VS Code Remote-SSH、Git、scp 和 rsync 不触发 tssh 编排，不被安装器改写 SSH 配置。

制品不使用容器。Python、ansible-core、CLI 运行依赖、Playbook、Roles 和模板均在发布构建阶段准备并随制品分发；安装器将其部署在用户目录，不要求安装后另行联网下载依赖。

## 已确认的用户边界

- 用户预先准备已存在、可 SSH 登录且有 sudo 权限的目标用户；项目不创建用户、不修改密码、不授予 sudo。
- SSH 配置由用户管理。省略覆盖参数时，目标解析遵循用户的 OpenSSH 配置；显式命令行参数具有清晰、可预测的覆盖优先级。
- 用户提供本机 HTTP 代理入口并负责其可用性；tssh 不探测代理连通性或外网可用性。
- 目标地址、用户认证信息和 sudo 密码仅用于本轮 setup，不持久保存。
- 代理默认地址为 `127.0.0.1:4227`，用户可通过 tssh 配置命令修改；运行配置仅在用户目录保存。

## 命令组织原则

- `tssh user@host [SSH options]` 保留原生连接形式。
- 产品管理命令使用 `--` 前缀，避免管理命令与 SSH 主机别名冲突。例如 `tssh --help`、`tssh --setup`、`tssh --list`、`tssh --proxy`。
- 文件传输、持久代理、端口回收、状态和诊断命令使用同一管理命名空间；参数解析必须保留 SSH `-p`、`-i` 等兼容行为。
- 未识别的管理选项显示帮助并返回非零；不得将拼错的管理命令误当作 SSH 主机。
- `tssh --help` 与状态类管理命令使用 Rich 输出分组、颜色和面板；非 TTY 时自动退回无 ANSI 的纯文本。
- 这里的 UI 指终端内联 CLI 展示，不启动全屏 TUI，也不改变 `tssh user@host` 的 SSH 快速路径。
- Rich 在 release runtime 中随同制品提供；执行器由 `tssh` 启动脚本固定到同一版本目录中的 CPython 和 `site-packages`。

## 阶段

### P0：建立产品契约与基线

- 本文作为统一产品计划；记录现有命令、SSH 配置行为、安装副作用、依赖和用户数据位置。
- 固化测试：单元测试、Ansible inventory/syntax/lint、`git diff --check`、现有 release 包安装/卸载验证。
- 在开发机本地 Incus 创建隔离的控制端 VM 和目标 VM；不再依赖 Thunderobot，也不复用或改动旧实例。

验收：命令和依赖清单可审阅；两个新 VM 均能 SSH 登录；现有测试基线有结果。

状态：本机 Incus 平台已建立。旧 Thunderobot 实例保持关机且不作为本轮验收依赖。控制端 Debian 13 VM `tssh-control-20261124` 与目标 Debian 13 VM `tssh-target-vm-20261124` 已建立。控制端安装、Rich CLI 输出、SSH 密钥连接、4227/4228 转发、并发复用、隧道清理和文件上传已验收；2026-11-24 使用真实本机代理桥完成了完整 `tssh --setup` 与只读 verify。

### P1：tssh 管理命令命名空间与本机代理配置

- 管理命令统一迁移到 `--` 前缀，并更新帮助、README、测试和 shell 示例。
- 设置、读取、校验用户级代理地址/端口；默认 `127.0.0.1:4227`。
- 保持“用户负责代理可用性，tssh 不做连通性检查”的约束。
- 同一用户配置供普通 tssh 转发、setup 临时隧道和 Codex 回调功能读取；不影响 SSH 目标配置。

验收：修改代理后无需改代码即可在下一次连接/setup 使用；未配置时使用默认值；无效格式给出说明；不发起代理探测。

状态：已落地。用户配置位于 `~/.config/tssh/config`；隧道、setup、持久转发和状态输出读取同一代理端点。单元回归覆盖代理配置、CLI 前缀及未知管理参数。

### P2：统一 SSH 有效配置解析

- 形成单一 SSH target 解析结果，供交互 SSH、隧道、文件传输、setup、持久和回收命令复用。
- 覆盖 `HostName`、`User`、`Port`、`IdentityFile`；逐项确认是否支持并传递 `ProxyJump`、`ProxyCommand`、`HostKeyAlias`、`Include` 和其他 SSH 配置。
- 命令行显式参数优先于配置；其他未覆盖的参数保持 OpenSSH 语义。
- 不全局写入、重排或合并 `~/.ssh/config`。

验收：别名、非默认端口、代理跳板和密钥配置在交互连接及所有 tssh 管理操作中一致；普通 SSH 和 VS Code 读取原配置且行为不变。

状态：基础别名解析已在 VM 验收：`target-vm` 从普通 `~/.ssh/config` 解析为目标地址、用户名和密钥；普通 SSH 成功，配置中 `PermitLocalCommand=no` 且无 `LocalCommand`。本轮未覆盖 ProxyJump、ProxyCommand 和 VS Code GUI 自动化。

### P3：setup 纳入 tssh

- 将现有 `./re-remote setup` 固定 Ansible 流程包装为 `tssh --setup`。
- 从 tssh 已解析的 SSH 目标和代理配置生成本轮临时 Inventory；连接用户名与最终配置用户相同。
- 认证、sudo、临时代理、日志脱敏、运行目录清理沿用已验证的安全边界。
- setup 复用现有 Playbook/Role；暂时保留旧 `./re-remote setup` 兼容入口。

验收：用户仅需 tssh 命令即可从已有 sudo 用户完成交互式恢复；Ctrl-C、失败和成功路径均清除临时凭据。

状态：已由安装在控制端 VM 的完整制品触发目标 VM 恢复。通过真实本机代理桥，Codex、CodexSwitch、Skills、Neovim 与 Treesitter parser 安装以及最后的只读 verify 均通过；修复 mise 下载摘要来源和 GitHub API gzip 响应处理后，完整流程通过。

### P4：构建完整、离线依赖齐全的用户级制品

- 在构建端固定并安装 Python、ansible-core 和项目 CLI 所需运行依赖，随制品打包；安装阶段不访问 PyPI、不运行 `uv sync`。
- 控制器 Python、Ansible 入口、系统服务、命令包装器、Playbook/Role 路径统一指向版本目录。
- 安装到用户目录，原子切换版本；支持升级、回滚、卸载；不修改系统 Python。
- 明确首期发布架构和最低 glibc/发行版要求；为每种架构分别构建和测试。

验收：干净控制端 VM 断开外网仍可安装本地制品并运行 `tssh --help`、普通 tssh 连接和 setup；安装后没有缺失 Python/Ansible 包。

状态：Linux x86_64 制品已实际安装到隔离 control VM；自带 CPython 3.14.7、Rich 15.0.0 和 ansible-core 2.21.3，无需在安装后下载 Python 依赖。在线安装和升级旧版本清理已验收；断网安装未单独验收。

### P5：VM 端到端验收

在开发机本地 Incus 上创建全新的隔离控制端 VM 和 Debian 目标实例，通过已安装制品执行：

1. 制品安装、版本查询、升级/卸载基本检查；
2. `~/.ssh/config` 别名、指定端口、用户名和密钥优先级；
3. tssh 连接、4227 代理转发、多窗口共享和退出清理；
4. `tssh --setup` 通过本地打包的 Ansible 在另一 VM 恢复环境；
5. `tssh --list`、持久代理、cleanup/reclaim、上传/下载等既有能力回归；
6. 普通 SSH 与 VS Code Remote-SSH 不触发 tssh、不被安装器修改配置；
7. 失败、Ctrl-C 和重试后的临时凭据/隧道清理。

验收：保留 VM 命令输出、制品 SHA256、目标环境 verify 结果和 VS Code 自动化/人工可替代检查记录；不提交凭据或真实密钥。

状态：本机 Incus 上的制品安装、完整目标恢复、只读验收及第二次 apply 幂等性通过。VS Code GUI 自动化、浏览器 OAuth 回调和制品断网安装尚未在本轮验证，不据此宣称 GUI/交互登录验收完成。

- 最终 VM 验收制品 `tssh-v0.1.34-linux-x86_64.tar.gz` 的 SHA256 为 `572527bfd983091cf1cb1299b026cc284c0e50f0d99d6cc0ba25b06cf2b1a5a1`；包含 Rich 15.0.0、CPython 3.14.7、ansible-core 2.21.3 和 `tssh_help`。
- 本机全量门禁：`uv run --locked pytest -q`，179 passed；Ansible inventory 与 `playbooks/site.yml --syntax-check` 通过；`uv run --locked ansible-lint` 为 0 failure、0 warning；`git diff --check` 通过。
- 已在 `tssh-control-20261124` 使用制品自带的 `release/install` 安装；`tssh --version` 返回 0.1.34，用户级 `tssh.socket` 与 `tssh.service` 均为 active。私有 Python 能导入制品内的 Rich 和 ansible-core。
- 在控制 VM 中验证 `tssh --help` 的 Rich 分组面板、`tssh --list` 的状态表和 IDLE 状态；非 TTY 输出不包含 ANSI 转义序列。默认 SSH 配置未注入 tssh `LocalCommand`/转发 hook。
- 本机 Incus 已有两个 Debian 13 VM。目标镜像不含 `openssh-server`；检查发现 HTTP 80 出口被阻断、DNS 返回 Clash fake-IP。仅在隔离目标 VM 中切换到 Debian 官方 HTTPS 源，并用可达 CDN IPv4 做临时 hosts 映射后，`apt-get update` 和 `openssh-server` / sudo 安装成功。没有改动本机 Clash 配置。
- 真实 SSH 密钥登录通过；最终验收阶段将本机 Clash 代理桥接到控制 VM，目标端经 tssh 反向转发访问 GitHub 返回 HTTP 200；`tssh --list` 曾显示 4227 proxy 和 4228 event 均 READY。
- 两个并发 tssh 会话共享同一个 endpoint 隧道，状态为 `SESSIONS=2`；第三个连接在共享隧道存活时成功访问代理。会话结束后 `--list` 回到 IDLE，目标机不再监听 4227/4228。
- `tssh --put` 单文件、递归目录和展开后的多文件/通配符上传均成功。目标端文件内容和目录结构已核对。
- 更早的阶段曾使用 mock responder 做转发路径测试；最终阶段改用真实本机 Clash 代理完成 `tssh --setup`、GitHub 连通和幂等性验收。VS Code GUI 与 Codex 浏览器 OAuth 回调仍未验收。

本机 Incus 实例：`tssh-control-20261124`（Debian VM，已安装制品）、`tssh-target-vm-20261124`（Debian VM，已安装 sshd）。隔离测试代理运行在控制 VM 的 loopback，仅用于转发测试；专用测试密钥保存在被 Git 忽略的 `.local/incus-test/`。诊断容器已删除；旧 Thunderobot 实例保持关机，不会启动或修改。

## 首期范围与后续整理

首期优先完成 P1–P5 的产品主链路，原 `re-remote` 内部模块可以继续作为实现细节。旧 Inventory 批量模式、apply/verify 等高级运维能力先不删除，暂时从普通 tssh 用户主路径隐藏；有测试和迁移说明后再决定是否移除。

## 尚未验收的范围

- VS Code 桌面 GUI 自动打开远端工作区，以及 Codex 浏览器 OAuth 回调。
- 断网安装制品；当前已验证在线安装与升级。
- `ProxyJump`、`ProxyCommand` 等复杂 SSH 配置组合；本轮验收普通 SSH 配置、主机、端口、用户和密钥路径。

## 2026-11-24 最终验收结果

- 最终制品 `tssh-v0.1.34-linux-x86_64.tar.gz` SHA256：`572527bfd983091cf1cb1299b026cc284c0e50f0d99d6cc0ba25b06cf2b1a5a1`。
- 真实本机 Clash 代理经 host→control VM 桥接；目标 VM 的完整 `tssh --setup` 与内置只读 verify 成功。第二次 apply recap：`ok=191 changed=0 unreachable=0 failed=0 skipped=107`。
- 真实 tssh 命令通过目标端 4227 请求 GitHub 返回 HTTP 200；普通 ssh 连接不创建代理 lease，tssh 结束后 `--list` 显示 IDLE。
- VM 安装的命令和工具包括 Codex CLI 0.171.0、Neovim 0.11.3、mise 2026.11.8、23 个 Treesitter parser 和 12 个 Codex Skills；CodexSwitch、NetBird、Node/npm/npx、Zellij、tmux、fzf、zoxide、rget 和 rdo 均可用。
- 本轮没有执行 VS Code 桌面 GUI 或浏览器 OAuth；此部分仍需独立验收。
