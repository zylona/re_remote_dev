# SSH 代理转发成熟方案调研

## 结论

没有发现一个第三方项目能够同时满足以下全部条件：

- 用户继续执行 `ssh user@ip`；
- 本机 `127.0.0.1:4227` 自动出现在远端 `127.0.0.1:4227`；
- 远端不运行常驻 Agent，不修改 Docker、Codex 或其他应用配置；
- 多台设备和同一设备多个窗口互不冲突；
- 隧道自动重连，且不影响交互终端输入延迟。

最匹配、风险最低的方案仍是原生 OpenSSH，但需要把“交互 SSH”和“代理隧道”拆成两个本机进程：

```text
ssh user@host                         普通交互连接
systemd --user / autossh -N -R ...    独立代理隧道
                                      ↓
                              远端 127.0.0.1:4227
```

本机服务按目标设备建立唯一隧道，所有远端窗口共享该隧道。远端只需要 SSH server 允许
TCP forwarding；不需要安装网络代理软件或常驻远端服务。

## 候选方案对比

| 方案 | SSH 原生体验 | 自动重连 | 多目标/多窗口 | 远端额外组件 | 与固定 4227 的匹配度 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| OpenSSH `-R` + systemd user | 是 | systemd 负责进程重启，需端到端探测 | 高，按目标 unit 隔离 | 无 | 高 | 推荐基础方案 |
| OpenSSH `-R` + autossh | 是 | autossh 负责 SSH 隧道监控 | 高，按目标实例隔离 | 无 | 高 | 推荐增强方案 |
| OpenSSH `ControlMaster` | 是 | 不负责失效检测和重建 | 中，容易出现端口竞争 | 无 | 中 | 只适合连接复用，不应单独承担代理 |
| VS Code Remote-SSH | 否，需要 VS Code | 本地 server/forwarding 层管理 | 高 | VS Code Server | 中 | 架构参考，不适合作为终端独立依赖 |
| sshuttle | 否，命令独立运行 | 有限 | 中 | 远端 Python 临时进程 | 低 | 方向和目标不一致 |
| rathole | 否，需要 client/server 配置 | 强 | 高 | 两端二进制和配置 | 低 | 适合长期 NAT 穿透，不适合 SSH 即用 |
| frp | 否，需要 frpc/frps | 强 | 高 | 两端服务和配置 | 低 | 基础设施过重 |
| gost | 否，需要独立命令/配置 | 取决于外部 supervisor | 高 | 通常需要两端进程 | 中 | 功能丰富但运维复杂 |

## 方案分析

### 1. OpenSSH + systemd user：最适合当前需求

OpenSSH 本身已经提供 `RemoteForward`、`ExitOnForwardFailure`、`ServerAliveInterval`、
`ServerAliveCountMax` 和 `ControlPersist`。推荐让本机 systemd user unit 运行：

```text
ssh -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -R 127.0.0.1:4227:127.0.0.1:4227 \
  user@host
```

systemd 的 `Restart=on-failure` 或 `Restart=always` 可以在 SSH 进程退出后重新启动服务；
`RestartSec` 和启动限流可避免网络断开时高频重连。systemd 官方文档说明，`Restart=` 会在
服务进程退出、被信号终止或超时后按策略重新启动服务：
<https://github.com/systemd/systemd/blob/main/man/systemd.service.xml>。

优点是组件少、性能接近原生 SSH、远端零安装、密钥认证直接复用。缺点是 systemd 只能观察
进程退出，不能自动判断“进程存在但转发已半断开”，所以必须补充端到端探测。

### 2. OpenSSH + autossh：成熟的隧道监控器

`autossh` 的职责就是启动 SSH 并在连接退出或监控流量异常时重启。官方 README 明确支持：

- 监控 SSH 隧道并自动重启；
- `-M 0` 关闭额外监控端口，仅依赖 SSH keepalive；
- 使用 `ExitOnForwardFailure=yes` 判断转发是否成功建立；
- 配合 `ServerAliveInterval` 和 `ServerAliveCountMax` 让 SSH 在失联后退出。

官方项目：<https://github.com/Autossh/autossh/blob/main/README>。

对本项目而言，最合适的组合是 `autossh -M 0` 加 systemd user unit。这样不需要额外监听
monitor port，减少端口冲突；由 SSH keepalive 触发退出，由 systemd/autossh 完成重建。

autossh 仍不能解决本机 HTTP 代理进程停止的问题，因此服务必须分别探测：

1. SSH 隧道是否存活；
2. 远端 4227 是否可连接；
3. 本机 4227 HTTP 代理是否返回有效响应。

### 3. ControlMaster/ControlPersist：连接复用，不是隧道管理器

ControlMaster 能让多个 SSH 窗口复用一条底层 SSH 连接，但固定的 `RemoteForward 4227`
在多个连接同时声明时容易产生 `EADDRINUSE` 或旧 master 状态残留。它也不提供独立的
端到端代理健康检查。

因此应将 ControlMaster 用于需要复用的 SSH 控制连接，将代理转发放在独立的 `ssh -N`
或 autossh 进程中；交互终端不应与代理流量共享同一个 TCP 连接。

### 4. VS Code Remote-SSH：值得借鉴的编排思路

VS Code Remote-SSH 在本机维护连接和端口转发，上层窗口通过本地 server 复用远程上下文；
其文档同时支持临时端口转发和在 SSH 配置中声明始终转发的端口：
<https://code.visualstudio.com/docs/remote/ssh>。

VS Code 的 Local Server 模式会让多个窗口复用一个本地 SSH 进程，而 Terminal 模式则让每个
窗口使用独立连接：<https://github.com/microsoft/vscode-remote-release/wiki/Remote-SSH-troubleshooting>。

这证明“本地编排、目标隔离、窗口复用”是成熟方向，但 VS Code 的本地 server、动态端口和
扩展协议无法直接作为终端工具依赖。当前项目应复用其架构原则，而不是引入整个 VS Code。

### 5. sshuttle：方向相反

sshuttle 是通过 SSH 建立透明代理/VPN，把本机访问远端网络的流量送到远端。官方文档说明，
它会在远端运行 Python 组件并拦截本机网络连接：
<https://github.com/sshuttle/sshuttle>。

它适合“本机访问远端内网”，而当前需求是“远端应用通过本机 HTTP 代理访问外网”。两者方向
相反；sshuttle 还会改变路由、依赖本机 root/tproxy 能力，无法提供固定远端 4227 的简单
兼容接口，因此不推荐。

### 6. rathole、frp、gost：能力强但基础设施过重

`rathole` 是 Rust 实现的高性能反向代理，支持心跳、重试、TCP_NODELAY 和低资源占用，
但需要双方运行 client/server 并维护 token/config：
<https://github.com/rathole-org/rathole>。

`frp` 是功能完整的反向代理平台，支持多种 TCP/UDP/HTTP 转发，但同样需要 frpc/frps 和
独立配置：<https://github.com/fatedier/frp>。

`gost` 支持 SSH forwarding、链式代理和多种协议：
<https://github.com/go-gost/docs/blob/master/en/docs/getting-started/quick-start.md>。
但其灵活性也带来更多配置和进程生命周期管理成本。

这些项目适合长期公网 NAT 穿透或多服务暴露，不适合本地用户已经拥有 SSH 访问权限、只需要
一个 loopback HTTP 代理端口的场景。引入它们会增加远端软件、升级、安全配置和故障面。

## 推荐的产品级实现

### 连接模型

每个 `(host, port, user, identity)` 生成一个本机 systemd user unit：

```text
remote-dev-proxy@<digest>.service
```

unit 只启动一个独立的 `ssh -N -R`（或 autossh）进程。多个 SSH 窗口不再创建转发，直接
使用同一个远端 4227。不同目标设备的远端 loopback 相互隔离，因此都可以使用 4227。

### 健康模型

状态必须分为：

- `PROCESS_READY`：SSH 进程存在；
- `REMOTE_LISTENING`：远端 4227 能建立 TCP 连接；
- `PROXY_READY`：远端通过 4227 成功完成 HTTP CONNECT/请求；
- `DEGRADED`：隧道存在但代理后端不可用；
- `FAILED`：SSH 转发无法建立。

只有 `PROXY_READY` 才能对外报告代理可用。健康探测失败时按以下顺序处理：

1. 检查本机 HTTP 代理；
2. 检查远端 4227；
3. 重启单个目标的 forwarder；
4. 连续失败进入指数退避和 systemd 限流。

### 用户体验

用户只需执行一次目标注册/引导；之后保持普通命令：

```bash
ssh user@host
```

首次注册应该检查并启动本机目标 unit。普通 SSH 不应携带 `RemoteForward`，避免窗口间端口
竞争和终端输入延迟。状态、重连和日志通过：

```bash
remote-dev orchestrator status
remote-dev orchestrator doctor
```

### 不采用远端常驻 Agent 的原因

远端 SSH `-R` 监听是 SSH server 为客户端请求创建的 channel。远端安装一个程序无法反向
创建本机到远端的 SSH channel，除非远端额外向本机发起 SSH 连接并保存本机凭据；这会引入
额外的密钥、服务端口和安全风险。因此不发布独立的远端转发制品；远端能力由 Ansible
恢复流程按需检查和配置，实际隧道始终由本机 SSH 编排器管理。

## 对当前实现的判断

当前项目已经具备独立 proxy forwarder、保活和故障重建，但仍应补齐：

1. 端到端 HTTP 健康探测，而不只是 `Popen.poll()`；
2. 每目标 systemd unit 的明确所有权；
3. 本机代理后端不可用与 SSH 隧道不可用的区分；
4. 启动/停止竞态和旧进程清理；
5. 多目标、多窗口、半断开、代理停止、主机恢复的长期测试。

最终建议：保留 OpenSSH 传输协议和当前本机服务的安全隔离设计，吸收 autossh 的重连语义，
将 forwarder 交给 systemd user 持久化管理，并增加真实代理请求探测。除非未来需求扩大到
跨 NAT 公网穿透或多服务暴露，否则不引入 frp、rathole、gost 或 sshuttle。

## 来源

1. OpenSSH/VS Code Remote-SSH：<https://code.visualstudio.com/docs/remote/ssh>
2. VS Code Remote-SSH 连接模式：<https://github.com/microsoft/vscode-remote-release/wiki/Remote-SSH-troubleshooting>
3. autossh 官方 README：<https://github.com/Autossh/autossh/blob/main/README>
4. systemd service 官方手册：<https://github.com/systemd/systemd/blob/main/man/systemd.service.xml>
5. sshuttle 官方仓库和用法：<https://github.com/sshuttle/sshuttle>
6. rathole 官方仓库：<https://github.com/rathole-org/rathole>
7. frp 官方仓库：<https://github.com/fatedier/frp>
8. GOST 官方文档：<https://github.com/go-gost/docs/blob/master/en/docs/getting-started/quick-start.md>
