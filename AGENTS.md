# remote-dev Agent 约束

本文件适用于仓库根目录及全部子目录，约束参与本项目设计、实现、测试和文档维护的 agent。

项目目标是在用户已能 SSH 登录的 Linux 主机上，以一次受控运行恢复接近本机 Omarchy 命令行习惯的远程开发环境：zsh、Antidote、Powerlevel10k、mise、fzf、zoxide、Zellij，以及 tmux fallback。目标机可能没有外网，执行端可通过 SSH 临时反向端口转发提供 HTTP 代理。

## 1. 规范来源与优先级

开始工作前必须阅读与任务相关的文档：

1. `docs/reqs.md`：用户原始需求；
2. `docs/research.md`：当前选型、代理设计和实施分期；
3. 本文件：工程实施边界。

用户当前任务的明确要求高于仓库文档。若实现改变选型、执行边界或安全假设，必须先更新相关文档并说明原因。

## 2. 核心架构

### 2.1 Ansible-first

`ansible-core` 是唯一远程执行和编排引擎。固定采用：

- 单一 YAML Inventory：`inventory/production.yml`；
- 目标设备可以使用 SSH 公钥/Agent 或密码认证；连接用户可无 sudo，但必须存在可用的 sudo 或 su-root 提权通道。密码 SSH 仅通过临时 `sshpass -e`/Ansible ask-pass 机制传递，不写入配置、命令行或日志；root/sudo 密码仅通过 TTY 交互输入。
- sudo 密码只允许通过 Ansible `--ask-become-pass` 交互输入，不得保存、写入命令行或日志。
- `playbooks/site.yml` 中显式、静态、有序的多个 Play；
- 按独立生命周期拆分的 Roles；
- 独立只读 verify；
- Molecule、pytest-testinfra 和必要的 VM 测试。

禁止自建 DAG、scheduler、worker pool、状态数据库、SSH/SCP 执行器、常驻远端 Agent，禁止在 Role 内隐式调用另一个跨主机流程。CLI 只能校验配置、管理凭据、显示摘要并调用固定 Ansible 工作流。

原生 `ansible-playbook` 路径必须始终可用；CLI 不是实现前置，也不得依赖 Ansible 未承诺稳定的 Python 内部 API。

### 2.2 固定执行顺序

默认顺序为：

```text
preflight
  → bootstrap 基础依赖
  → user_shell（zsh + Antidote + p10k + mise）
  → tools（Zellij/fzf/zoxide/可选 Starship）
  → workspace（主题、布局、启动钩子）
  → verify
```

跨节点或跨阶段依赖必须在顶层 Play 中显式可见，不依赖目录 glob、Inventory 顺序、环境变量 marker 或“再跑一次”。不向使用者暴露任意 `--tags`/`--skip-tags` 以绕过前置。

## 3. 目录与命名

```text
inventory/                 非敏感 Inventory 与示例
playbooks/                 顶层工作流和有序 Plays
roles/                     preflight、bootstrap、user_shell、tools、workspace、verify
templates/                 可复用配置模板（仅在真实需要时创建）
tests/                     单元、Molecule、testinfra 和 VM 场景
docs/                      需求、调研和能力契约
.local/                    凭据、缓存、运行报告；永不提交
```

命名使用小写 `snake_case`；Play 文件使用两位数字前缀。第一版不创建 Ansible Collection、空 plugin/module 目录或维护者脚手架。只有出现真实复用、独立发布或稳定自定义 module/plugin 时才重新评估 Collection。

## 4. Inventory、凭据与配置

- 非敏感配置唯一来源是 `inventory/production.yml`；不得另建 targets、roles、tools 等平行权威清单。
- 不维护本地密码配置；私钥路径可写入被忽略的 Inventory，但私钥内容不得提交。
- 不把密码、私钥、Token 或代理认证写入命令行参数、普通 fact、日志、diff 或报告；相关 task 使用 `no_log: true`。
- 修改 YAML 必须在内存中处理，使用同目录临时文件、校验后原子替换，保留用户注释和无关字段；失败保留原文件。
- Inventory 应支持 `profile`、`proxy.mode` 和工具版本覆盖；本机专属工具不能硬编码为所有远端必装。

## 5. 本机命令行兼容契约

远端默认 profile 应尽量复用本机当前行为：

- Omarchy env/aliases/functions 仅在远端存在时可选加载，不能硬依赖 Omarchy；非交互 zsh 必须快速返回。
- Antidote 使用静态 bundle：`.zsh_plugins.txt` 是可审阅事实源，生成 `.zsh_plugins.zsh`；插件为 OMZ `git/z/web-search`、`zsh-autosuggestions`、Powerlevel10k、`zsh-syntax-highlighting`。
- 插件加载顺序固定为补全初始化、p10k、`zsh-syntax-highlighting` 最后；不得运行交互式 `p10k configure`。
- p10k 默认采用本机 Lean、单行、少图标、transient prompt、Nerd Font v3 风格；Starship 只作为 fallback，不能与 p10k 同时初始化。
- 保持 mise 激活、zoxide init、fzf completion/key-bindings；缺少可选命令时 zsh 仍必须可启动。
- 默认保留 50000 条历史和去重设置；共享账号提供 conservative history 模式。
- Zellij 为默认复用器，配置和布局由项目生成；tmux 仅在 Zellij 不可用或用户显式选择时使用。

## 6. SSH 临时代理约束

目标机无外网时，执行端已运行的 HTTP 代理通过 SSH remote forwarding 暴露到目标机 loopback：

```text
执行端 127.0.0.1:4227
  → SSH -R 127.0.0.1:<本轮随机端口>:127.0.0.1:4227
  → 目标机 HTTP_PROXY/HTTPS_PROXY
```

- 代理属于项目级运行时机制，Role 只消费 `http_proxy/https_proxy/no_proxy`，不得自行创建长驻隧道或修改执行端代理。
- 支持 `auto`、`required`、`off`；`required` 无法建立转发时在任何远端写入前失败。
- 每轮使用随机端口（建议 40000–60000），仅监听目标机 `127.0.0.1`；禁止 `GatewayPorts` 和公网监听。
- 带转发连接必须使用 `ExitOnForwardFailure=yes`、保活参数；为避免旧 master 或 initial/target 用户冲突，启用转发时使用 `ControlMaster=no`、`ControlPath=none`。
- 每个下载/安装 task 必须显式传递代理；不能假设 controller 环境会自动传到目标机。
- APT、Antidote Git、mise、Zellij/fzf/zoxide release、可选插件下载均可消费临时代理；代理 URL 不得写入 `.zshrc`、mise 配置或长期 systemd 配置。
- 成功、失败、断线和 Ctrl-C 都必须清理 drop-in、临时环境、端口和 SSH 进程；日志只记录状态和端口，不记录凭据。
- 先探测 sshd 是否允许 remote forwarding；被 `AllowTcpForwarding` 拒绝时报告明确错误，不伪装成普通 SSH 不可达。

## 7. Ansible/Role 实现规范

- 优先使用 `ansible.builtin` 原生 module，使用 FQCN；有原生 module 时不得用 shell 模拟。
- 仅在上游安装器或过程逻辑确实更清晰时使用 `command`/`shell`/`script`，并说明原因、定义 `changed_when`/`failed_when` 和 check mode 行为。
- 模板写入前验证；服务 reload/restart 使用 Handler 且只在 changed 时触发。
- apply 必须幂等，不写 `READY` marker，不做无意义下载、重启或重建。
- verify 严格只读，不修复、不重启、不生成凭据；至少区分 `READY`、`READY_WITH_WARNINGS`、`FAILED`、`UNREACHABLE`、`NOT_APPLICABLE`。
- Role 不读取其他 Role 的内部文件，不调用顶层 Playbook，不决定跨节点顺序。
- 不强制所有需求包装成 Role；只有具有独立配置、生命周期和验收的能力才建立 Role。

## 8. 测试门禁

每项能力必须同步交付 apply、独立 verify、文档和测试，并覆盖：

- `ansible-inventory --list`、Playbook syntax-check、ansible-lint；
- fresh 首次执行、第二次幂等、partial、drift 和配置变化；
- 代理可用/不可用、端口冲突、SSH 转发被禁用、断线清理；
- 无 Nerd Font、无可选工具、非 TTY 和窄终端回退；
- 必要时使用 Molecule；systemd、SSH 重连和真实转发使用 VM，不把普通容器当成完整网络验证；
- 测试目标从显式 Inventory 动态发现，不在代码中写死主机名、数量或角色成员；真实设备测试必须显式选择并确认 Inventory。

`--check`/`plan` 只能作为预览，不能代替真实 verify 和第二次 apply。测试报告不得包含 Secret，也不能参与调度或跳过逻辑。

## 9. 依赖与安全

- Python/uv 等开发工具由 mise 管理；Python 依赖统一由 `pyproject.toml` 和提交的 `uv.lock` 管理，不使用系统 pip 或手工 `.venv/bin/*`。
- 上游二进制和脚本必须固定版本或 commit，下载后尽量校验 SHA256；不得在每次运行直接执行未经审阅的 `curl | sh`。
- 目标机默认不更换 shell、不修改 sshd 安全策略、不开放端口、不安装常驻 Agent；改变这些行为必须显式配置并先备份。
- 私钥建议 `0600`；默认严格校验 SSH host key，禁止 `StrictHostKeyChecking=no` 和清空 `known_hosts`。

## 10. Agent 工作流程

修改前阅读 `docs/reqs.md`、`docs/research.md` 和相关能力文档，确认工作区并区分 Inventory、Play、Role、CLI、代理和测试范围。实现中保持小步改动，优先复用 Ansible 原生机制，不把简单问题升级成框架。完成前运行与改动对应的最小测试集、`git diff --check`，确认 `.local/` 和真实凭据未进入 Git，并报告实际测试结果与未验证风险。

除非用户明确要求，不得自行提交、推送、切换分支或改写历史。
