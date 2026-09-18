# Remote-SSH 思路借鉴与端口转发架构重设计

状态：架构调研基线（2026-09-17）

对应的实施计划见：[Remote-SSH 风格端口转发重构计划](remote-ssh-refactor-plan.md)。

本文只讨论本项目的本地端口转发与连接编排。远端基础工具恢复、Shell、编辑器和 Codex
安装已经属于已实现能力，不在本次设计范围内。

## 1. 执行摘要

当前实现的主要问题不是 SSH `-R` 参数本身，而是连接生命周期的边界错误：项目通过全局
`~/.ssh/config` 的 `LocalCommand` 接管所有 SSH 连接，再由 hook 同步执行密钥探测、
systemd 操作和代理隧道启动。这会让普通终端连接变慢，并可能干扰 VS Code Remote-SSH、
Git、scp、rsync、跳板机和其他 SSH 客户端。

VS Code Remote-SSH 的成熟点在于：

1. 连接由本地 resolver 按目标和窗口管理，而不是由远端 Agent 管理；
2. 远端 VS Code Server 只监听 loopback 随机端口；
3. 本地 SSH 进程使用动态转发（`-D`），本地 forwarding server 再把本地请求路由到
   远端 Server；
4. 安装/启动连接和实际端口隧道是明确的阶段，失败可以单独诊断；
5. 不要求把项目的 `LocalCommand`、固定 `RemoteForward` 或 `ControlMaster no` 写入所有
   SSH 主机的全局配置。

本项目应吸收这种“本地 resolver + 目标会话 + 隔离 forwarder + 明确生命周期”的思想，
而不是继续扩大全局 SSH hook。重构后的推荐基线是：

```text
普通 ssh user@host
  └─ 完全原生、无项目副作用

remote-dev connect host [--user USER]
  ├─ 本地 resolver（按目标/会话隔离）
  ├─ 独立 SSH -N -R 127.0.0.1:4227:127.0.0.1:4227
  ├─ 可选独立 OAuth/事件转发
  └─ 会话退出或失效时清理
```

如果产品必须保留字面意义上的 `ssh user@ip` 自动触发，那么只能提供显式的、用户选择的
SSH wrapper 或 per-host 配置；一个独立后台服务无法从操作系统层面可靠拦截任意 SSH 客户端
并注入转发，而不改变 SSH 配置或替换 `ssh` 命令。默认不应再用全局 `LocalCommand`。

## 2. 用户需求与不可同时满足的约束

目标需求是：

- 远端 `127.0.0.1:4227` 能访问本机 HTTP 代理；
- 多设备、多用户、多窗口互不冲突；
- 普通终端输入保持原生延迟；
- 不影响 VS Code Remote-SSH、Git、scp、rsync 和跳板机；
- 代理断线后自动恢复，目标离线时不产生重启风暴；
- 不部署远端常驻 Agent，不开放公网端口。

其中“完全不修改 SSH 配置”“继续直接执行任意 `ssh user@ip`”“自动启动本项目转发”三者
不能同时由一个普通后台服务实现。SSH 客户端不会向外部服务广播“我即将连接某主机”的事件。
必须选择一个触发面：

| 触发方式 | 是否修改 SSH 配置 | 是否影响其他 SSH 客户端 | 自动性 | 结论 |
| --- | --- | --- | --- | --- |
| 全局 `LocalCommand` | 是 | 高 | 高 | 当前方案，默认淘汰 |
| 每主机 `LocalForward/RemoteForward` | 是 | 中 | 高 | 仅适合明确主机 |
| `ssh` wrapper/函数 | 替换 PATH 或 Shell | 高，可能影响 VS Code | 高 | 仅作为显式 opt-in |
| `remote-dev connect` | 否 | 低 | 中 | 推荐默认入口 |
| 常驻后台服务但无触发器 | 否 | 低 | 无法感知 | 不能单独实现 |

## 3. VS Code Remote-SSH 的真实连接模型

### 3.1 两阶段连接

Remote-SSH 不是让用户的交互 Shell 去承载 VS Code 流量。它先通过 SSH 执行远端脚本，
安装或启动远端 VS Code Server；远端 Server 监听 loopback 的随机端口并返回端口和连接令牌。
随后本地 resolver 启动另一条 SSH 连接，通过动态转发连接该随机端口。官方故障排查文档明确
提到 Remote-SSH 会使用第二条连接建立 SSH port tunnel，并要求远端 sshd 允许
`AllowTcpForwarding yes`。

来源：[Remote-SSH 故障排查](https://code.visualstudio.com/docs/remote/troubleshooting)

这与当前项目把固定 4227 代理隧道、Codex 事件和普通终端混入全局 SSH hook 完全不同：
VS Code 的端口是 resolver 为当前会话动态分配的，且端口转发连接不负责交互终端输入。

### 3.2 本地 forwarding server

Remote-SSH 日志可见类似以下关系：

```text
ssh -T -D <socksPort> host
localPort -> socksPort -> remotePort
```

本地 forwarding server 接收 VS Code 客户端连接，再通过 SOCKS 动态转发到远端 Server；
它不是远端常驻代理，也不要求把端口写入远端 Shell 配置。

来源：[Remote-SSH issue 中的 resolver 日志示例](https://github.com/microsoft/vscode-remote-release/issues/5007)

### 3.3 ControlMaster 的使用边界

Remote-SSH 在某些场景使用 ControlMaster 来保证两次连接落到同一台动态节点，但这属于
resolver 的私有连接优化，不是全局 SSH 行为。官方文档也把 ControlMaster 作为动态节点
场景的可选 workaround，而不是所有主机的强制设置。

来源：[Remote-SSH 动态主机说明](https://code.visualstudio.com/docs/remote/troubleshooting)

本项目应把 ControlMaster 限定在 resolver 自己创建的连接目录中，不能通过全局
`Host *` 改写所有 SSH 客户端。

### 3.4 失败恢复与诊断

Remote-SSH 提供 `Kill VS Code Server on Host`、登录终端和详细 resolver 日志，以便区分：

- 远端 Server 没启动；
- SSH 认证失败；
- TCP forwarding 被 sshd 拒绝；
- 本地 forwarding server 失效；
- 连接缓存过期。

来源：[Remote-SSH troubleshooting](https://code.visualstudio.com/docs/remote/troubleshooting)

本项目也必须把“SSH 进程存活”“远端 4227 可连接”“本机代理可用”拆成独立状态，不能只
用 systemd unit 的 `active` 代表端到端可用。

## 4. 当前实现的问题定位

### 4.1 全局 `LocalCommand` 是最大副作用

当前受管块类似：

```ssh
Host * !github.com !gitee.com
  ControlMaster no
  ControlPath none
  ExitOnForwardFailure yes
  PermitLocalCommand yes
  LocalCommand ~/.local/bin/remote-dev-ssh-hook %h %p %r
```

它会匹配几乎所有目标。`LocalCommand` 在 SSH 连接成功后同步运行；当前 hook 还会：

1. 执行 `ssh -G`；
2. 重新发起一次密钥探测 SSH；
3. 调用 `systemctl --user`；
4. 创建/启动代理 unit；
5. 等待隧道稳定。

这解释了“刚打开 SSH 窗口时卡顿”。更重要的是，VS Code、Git、scp、rsync 等客户端也会
触发同一个 hook。即使 hook 最终不创建 4227 隧道，副作用仍然存在。

### 4.2 固定端口不是根因，但固定端口必须有所有权模型

不同设备可以各自使用远端 4227，因为 loopback 位于不同网络命名空间；同一设备的不同用户
不能各自创建固定 4227。必须由本地 resolver 按“设备 endpoint”选出一个 owner，并让所有
用户窗口共享该 endpoint 隧道。owner 断线时，resolver 从仍活跃且可用密钥认证的用户中选
一个接管，而不是为每个用户重复创建 4227。

### 4.3 独立服务仍然需要触发边界

本地 systemd user 服务适合持有隧道生命周期，但它不能知道用户何时执行了任意 `ssh`。当前
通过全局 hook 弥补这一点，代价就是污染所有 SSH 客户端。重构需要把触发器变成：

- `remote-dev connect` 明确启动一个会话；或
- 一个仅由用户显式启用的 wrapper；或
- VS Code 等客户端自己的 resolver 集成。

后台服务本身只负责已注册目标的健康检查、owner 选举和恢复，不负责猜测外部 SSH 进程。

## 5. 推荐目标架构

### 5.1 组件

```text
remote-dev connect
        │
        ▼
本地 Resolver（短命命令/轻量客户端）
        │ Unix socket 请求
        ▼
本地 Orchestrator（systemd --user，可按需 socket activation）
        ├─ EndpointRegistry：host:port 的目标登记
        ├─ OwnerSelector：同设备多用户选一个隧道 owner
        ├─ ProxyForwarder：独立 ssh -N -R 4227
        ├─ OAuthForwarder：仅登录期间独立 ssh -N -L 1455
        └─ HealthSupervisor：端到端探测、退避、清理
        │
        ▼
普通 ssh / VS Code / Git（不读取项目 hook）
```

### 5.2 EndpointRegistry

目标键只使用 endpoint 身份：

```text
endpoint = canonical_host + ssh_port
```

用户身份作为 owner 候选，而不是端口资源的唯一键：

```text
candidate = endpoint + user + identity fingerprint
```

状态持久化在本机用户目录，内容只包括摘要、owner、unit 名称、最近错误类别和时间戳，不保存
密码、私钥内容或完整 OAuth URL。

### 5.3 Owner 选举

对同一 endpoint：

1. 已有健康隧道直接复用；
2. owner 仍在线且 SSH keepalive 正常，不切换；
3. owner 断开后，从活跃候选中按“密钥可 BatchMode 登录、最近成功时间、稳定性”排序选一个；
4. 新 owner 只创建一个固定远端 4227 隧道；
5. 没有候选时停止隧道；
6. 新窗口到来时重新 reconcile。

纯密码登录不参与无人值守接管，这是认证凭据边界，不应通过保存密码解决。密码用户仍可
通过显式 `remote-dev connect --password` 建立一次性会话，但不会注册为自动接管 owner。

### 5.4 普通 SSH 与项目隧道彻底隔离

普通 SSH 必须使用独立原生 TCP 连接：

- 不注入 `LocalCommand`；
- 不注入 `RemoteForward`；
- 不强制 `ControlMaster no`；
- 不设置项目专用 `ControlPath`；
- 不等待项目 resolver、systemd 或 HTTP 探测。

代理隧道单独使用：

```text
ssh -F /dev/null -N -T \
  -o BatchMode=yes \
  -o ControlMaster=no \
  -o ControlPath=none \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -R 127.0.0.1:4227:127.0.0.1:4227 \
  user@host
```

这条命令只由 resolver/systemd forwarder 执行，不出现在用户普通 SSH 配置中。

### 5.5 VS Code 集成方式

VS Code 使用独立的 `config-vscode` 可作为旧环境兼容手段，但目标架构不应依赖它。更好的方式是：

- VS Code 使用其自身 Remote‑SSH resolver；
- 项目不向 VS Code 的 SSH 配置注入 hook；
- 如果 VS Code 需要远端访问 4227，则使用 VS Code 自己的端口转发能力，或由用户显式调用
  resolver API；
- 不让 4227 代理隧道承担 VS Code Server 的动态 SOCKS 隧道。

VS Code 官方支持临时端口转发和配置中的显式 `LocalForward`，并会为远端 Server 管理随机
端口；这两类转发应与本项目固定 4227 代理分开。

来源：[VS Code SSH forwarding 文档](https://code.visualstudio.com/docs/remote/ssh)

## 6. 用户入口设计

### 6.1 推荐入口

```bash
remote-dev connect user@host
```

其行为：

1. 检查本地编排器是否运行，未运行则 socket activation 启动；
2. 注册 endpoint 和当前用户；
3. 让 orchestrator reconcile 4227 owner；
4. 等待“远端监听 + HTTP 代理探测”就绪，超时给出明确警告；
5. `exec` 原生 `ssh user@host`，不经过项目代理连接；
6. 当前 shell 退出时发送 session release，但即使 release 丢失，supervisor 也会通过
   keepalive/TTL 清理。

该入口不能伪装成普通 `ssh`，但可通过用户自行选择的 shell alias 使用：

```bash
alias rdssh='remote-dev connect'
```

### 6.2 普通 `ssh user@ip` 的兼容选项

如果用户坚持字面命令不变，只支持两种 opt-in 模式：

1. 为明确的主机别名写 `Match exec`/`LocalCommand` 规则，并由用户承担与其他 SSH 客户端
   的兼容测试；
2. 安装一个显式 wrapper，并只在专用 Shell 环境启用，不修改系统级 `/usr/bin/ssh`。

默认发行包不启用这两种模式，避免再次影响 VS Code 和其他 SSH 工具。

## 7. 状态、失败与资源控制

### 7.1 状态机

```text
ABSENT → STARTING → SSH_READY → REMOTE_LISTENING → PROXY_READY
                         │              │                │
                         └──────────────┴──── DEGRADED ───┘
                                           │
                                         FAILED
```

必须区分：

- SSH 认证失败；
- sshd 拒绝 remote forwarding；
- 本机 4227 不可用；
- 远端 4227 无监听；
- HTTP 代理请求失败；
- 连接半断开；
- 本地端口或 unit 重复占用。

### 7.2 重连策略

- 首次失败：5 秒；
- 后续：10、20、40、80 秒，上限 5 分钟；
- 连续失败进入 `DEGRADED`，不继续高频重启；
- 新的显式连接请求立即触发一次 reconcile；
- 网络恢复后由 timer/health probe 恢复，不需要用户重启终端。

### 7.3 资源约束

每个 endpoint 最多一个 4227 owner forwarder；OAuth 1455 为会话级短命 forwarder；事件
通道不能和代理共用 TCP 连接。每个 unit 设置 `MemoryMax`、`TasksMax`、`RestartSec` 和
`StartLimit*`。日志只记录 endpoint 摘要、状态和错误类别。

## 8. 迁移与回滚

### 8.1 从当前实现迁移

1. 停止旧版 `remote-dev-proxy-*`、`remote-dev-master-*` unit；
2. 备份并移除受管 SSH `LocalCommand` 区块；
3. 删除旧 ControlPath socket 和过期状态；
4. 清理旧版全局 hook 后直接复用普通 `~/.ssh/config`，不再自动生成 `config-vscode`；
5. 安装新 resolver/orchestrator；
6. 用 `remote-dev connect` 显式验证代理和普通 SSH；
7. 再验证 VS Code、Git、scp、rsync。

### 8.2 回滚保证

失败时只停止项目创建的 forwarder，恢复 SSH 配置备份，不触碰用户自己的 Host、密钥、
ProxyCommand 或 VS Code 配置。普通 `ssh` 必须在编排器完全停止时仍可用。

## 9. 验收标准

### 基本体验

- 普通 `ssh user@host` 不触发项目进程，不等待 4227 探测；
- 首次输入延迟与未安装项目的基线一致；
- VS Code Remote‑SSH 使用独立配置或自身配置可以连接；
- Git、scp、rsync、ProxyJump 不受影响。

### 转发

- 同 endpoint 多窗口只有一个远端 4227 listener；
- 同 endpoint 多用户可共享并完成 owner 接管；
- 多 endpoint 可并行使用相同远端 4227；
- 本机代理停止、目标断网、SSH 进程被杀后进入退避并恢复；
- 端口冲突、认证失败和 sshd 拒绝转发给出可执行提示。

### 发布门禁

- fresh、重复、升级、回滚四类安装测试；
- 普通 SSH/VS Code/项目 resolver 三类连接并行测试；
- 至少一个 Debian VM、一个 openEuler/真实设备；
- 30 分钟连续观察无重启风暴、无普通 SSH 断开；
- 测试报告不包含密码、私钥、完整 OAuth URL 或真实设备信息。

## 10. 结论与决策建议

1. **接受推倒当前端口转发触发层的设计**：删除全局 `LocalCommand` 作为默认机制。
2. 保留现有 Ansible 远端 forwarding 检查；它只负责保证 sshd 允许 forwarding，不负责创建
   本地隧道。
3. 将本地服务重构为 VS Code 风格的 resolver/orchestrator：按 endpoint 管理短命会话、
   隧道、owner 和健康状态。
4. 默认入口改为 `remote-dev connect`；普通 `ssh` 保持纯原生。
5. 将“直接 `ssh user@ip` 也自动触发”降级为显式可选兼容模式，不能作为默认产品契约。
6. 固定远端 4227 继续保留，但只允许 endpoint owner forwarder 持有；VS Code 动态转发、
   OAuth 1455 和事件转发使用各自的会话生命周期。

这套边界才能同时满足稳定性、输入延迟、VS Code 兼容性、多用户接管和安全可回滚，而不再
依赖一个影响所有 SSH 客户端的全局 hook。

## 参考来源

- [VS Code Remote Development over SSH](https://code.visualstudio.com/docs/remote/ssh)
- [VS Code Remote-SSH troubleshooting](https://code.visualstudio.com/docs/remote/troubleshooting)
- [VS Code Remote-SSH resolver 日志示例](https://github.com/microsoft/vscode-remote-release/issues/5007)
- [VS Code Remote-SSH 动态转发失败案例](https://github.com/microsoft/vscode-remote-release/issues/11831)
- [OpenSSH `ssh_config` 手册](https://man.openbsd.org/ssh_config)
- [systemd service 重启策略](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
