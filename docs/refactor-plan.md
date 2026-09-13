# SSH 转发与 Codex 会话隔离重构计划

状态：设计基线，尚未开始实施

## 1. 背景与问题定义

当前实现通过 ControlMaster 复用 SSH 传输，并在同一套连接上承载代理、Codex 事件和 OAuth 回调。虽然可以减少连接数量，但 SSH 多路复用共享同一条 TCP 连接；Codex 的持续网络流量会造成队头阻塞，表现为 Codex 输入延迟、其他 SSH 窗口变卡，严重时 supervisor 重建 master 并断开全部会话。

重构目标是恢复原生 SSH 的交互手感，同时保留自动代理、自动 OAuth 回调、多设备和多窗口能力。

## 2. 目标架构

普通终端和项目转发完全分离：

```text
普通 ssh user@host
  └─ 独立原生 SSH TCP 连接（不使用项目 ControlMaster）

代理 forwarder
  └─ 独立 ssh -N -R 127.0.0.1:<remote_proxy>:127.0.0.1:4227

事件 forwarder
  └─ 独立 ssh -N -R 127.0.0.1:<remote_event>:127.0.0.1:4228

OAuth forwarder
  └─ Codex 登录时独立 ssh -N -L 127.0.0.1:1455:127.0.0.1:1455

本地编排器
  └─ 仅管理三个 forwarder 的生命周期，不管理普通 shell
```

### 必须保持的性质

- 普通 SSH 不受 Codex 网络流量影响；
- forwarder 进程之间不共享 ControlMaster；
- 每个目标身份独立分配端口和状态；
- OAuth 只在登录期间建立，退出后清理；
- 代理断线自动重连，使用指数退避；
- forwarder 失败不得终止用户 shell 或 Codex；
- 不监听公网，不修改用户 SSH 认证方式；
- Ansible 仍是唯一远端执行引擎。

## 3. 分阶段实施计划

### P0：契约冻结与可观测性

范围：文档、配置模型、状态模型和诊断命令。

- 在 `docs/capabilities/orchestrator.md` 增加“普通 SSH 与 forwarder 隔离”契约；
- 定义目标键：`hostname + port + user + identity_fingerprint`；
- 为 proxy/event/oauth 定义统一的 `ForwardState`：`ABSENT、STARTING、READY、DEGRADED、STOPPING、FAILED`；
- 状态中记录 PID、监听端口、最近错误类别和最后成功时间，不记录密码、Token 或完整 URL；
- `orchestrator status/doctor` 显示每个 forwarder 的独立状态；
- 增加环境变量或测试注入点，便于模拟端口占用、断网和进程退出。

验收：状态输出能区分普通 SSH（不受管）和三个 forwarder；异常信息可以直接指导用户处理。

### P1：移除普通 SSH 的 ControlMaster 依赖

范围：`ssh_integration.py`、安装器、README 和迁移逻辑。

- 目标主机 SSH 条目只保留 `HostName/User/IdentityFile` 及保活参数；
- 删除项目写入的 `ControlMaster/ControlPath/ControlPersist`；
- 不再向普通 SSH 条目写入 `RemoteForward` 或 `LocalForward`；
- 兼容迁移旧配置：删除项目标记块和旧 include，但保留用户未标记的规则；
- 已有旧 master 不主动影响用户会话，提供显式 `orchestrator migrate` 或 bootstrap 清理入口；
- 文档明确：普通 `ssh user@host` 是原生独立连接。

验收：`ssh -G target` 显示 `controlmaster false`；同时打开多个终端，任一终端不复用项目 socket。

### P2：独立代理 forwarder

范围：新增 `ProxyForwardManager`，改造本地 systemd 服务。

- 每个目标启动独立 `ssh -N`，使用 `ControlMaster=no、ControlPath=none、ExitOnForwardFailure=yes`；
- 本地代理端口默认为 4227，远端端口从 40000–60000 随机选择；
- 启动前检查目标 loopback 端口，避免端口冲突；
- 远端环境只消费本轮代理 URL，不把临时端口写入持久 shell 配置；
- 断线后按 5s、10s、20s…退避重连，并设置上限；
- 停止、异常和 Ctrl-C 都清理进程与远端临时文件；
- 代理 forwarder 不与 OAuth/event forwarder 共享进程。

验收：两个目标可同时使用不同远端端口；停止代理后普通 SSH 仍可输入；恢复代理不需要重新登录 shell。

### P3：独立事件 forwarder 与远端 shim 解耦

范围：`remote-dev-codex-event.py`、协议和事件 forwarder。

- 事件通道使用独立远程端口，不依赖代理端口或普通 SSH；
- shim 发送失败立即丢弃并继续运行 Codex；
- 事件 payload 增加 forwarder/协议版本，保持大小限制和 nonce 校验；
- 编排器重启后，forwarder 重新注册目标和 nonce；
- 事件通道只传输生命周期和 OAuth URL，不代理 Codex API 数据。

验收：停止事件 forwarder 时 Codex 仍能正常运行；恢复后新会话可以重新触发状态事件。

### P4：独立 OAuth 回调 forwarder

范围：`OAuthManager` 和浏览器启动链路。

- OAuth 启动时创建独立 `ssh -N -L`，不得调用 `ssh -O forward` 或依赖 master；
- 绑定 IPv4 `127.0.0.1:1455`，若可用则同时绑定 IPv6 `::1:1455`；
- 同一目标重复事件幂等；不同目标同时登录返回明确 `OAUTH_BUSY`；
- 浏览器只打开一次完整 URL；
- 收到 `CODEX_EXIT`、成功回调、进程退出或超时都清理 1455；
- 回调转发失败时在 Codex 输出可操作提示，但不得终止 Codex。

验收：浏览器回调可完成；重复 URL 不打开多个页面；OAuth 结束后 1455 监听消失。

### P5：supervisor 稳定性与资源控制

范围：systemd user 服务和 forwarder supervisor。

- supervisor 只监控 forwarder PID/端口，不探测或重建普通 SSH；
- 每个目标限制 forwarder 数量，禁止重复启动；
- 设置 `MemoryMax、TasksMax、RestartSec、StartLimit*`；
- 使用结构化、脱敏日志，区分网络失败、认证失败、端口冲突和策略拒绝；
- 目标离线时进入 `DEGRADED`，不忙等、不高频重连；
- systemd socket activation 失败不能导致服务进程退出循环。

验收：连续运行观察中 CPU/内存稳定；目标断网时无重启风暴；恢复网络后自动恢复代理。

### P6：迁移、兼容和回滚

范围：bootstrap、安装器、旧版本状态清理。

- bootstrap 默认执行本地服务升级预检，再迁移旧 ControlMaster 配置；
- 保留旧配置备份，失败时可恢复；
- 旧远端 shim 仍可运行，但优先升级为新事件端口协议；
- 对已有 4227/4228/1455 占用输出占用者、目标和解决建议；
- 提供 `orchestrator reset-target`，仅清理项目创建的 forwarder/nonce，不删除用户文件；
- 明确版本兼容矩阵，旧客户端连接新服务时安全降级。

验收：从旧版本升级无需手工删除 socket；升级失败不破坏普通 SSH；回滚后旧代理可用。

### P7：真实环境与发布门禁

测试矩阵：

- Debian VM、openEuler 实机各一台；
- 单目标多窗口、双目标并行、多用户同主机；
- Codex 空闲、持续输出、长请求和取消；
- 代理正常、代理进程停止、目标断网、目标重启、本机睡眠恢复；
- 1455/4227/事件端口被占用；
- sshd 禁止转发、认证失败、目标无 IPv6；
- 普通 SSH 延迟与未安装项目时基线对比。

每项场景记录：输入延迟、forwarder PID、端口、状态迁移、是否影响其他窗口、清理结果。

发布门禁：单元测试、pytest-testinfra、Ansible syntax/lint、两次 apply 幂等、真实 VM 回归、至少 30 分钟连续观察，且无 supervisor 重启风暴或普通 SSH 断开。

## 4. 配置与安全边界

- 非敏感配置仍唯一来自 `inventory/production.yml`；运行时状态放在 `.local/`；
- 密码只经交互式 Ansible become/ask-pass 传递；
- forwarder 命令禁止写入密码、Token、完整 OAuth URL；
- 所有监听默认绑定 loopback；
- 远端代理变量由受管 shell/Codex 启动环境提供，退出或禁用时清除；
- 不部署常驻远端 agent，不开放公网端口。

## 5. 回滚策略

任何阶段失败时：

1. 停止本阶段创建的 forwarder；
2. 删除本阶段临时端口、nonce 和状态；
3. 恢复 SSH 配置备份；
4. 保留错误摘要和失败阶段；
5. 普通 SSH 必须仍可直接连接；
6. 不自动回退用户原有 shell、Codex 登录或工具配置。

## 6. 完成定义

当 P0–P7 全部完成，并在 Debian VM 与 openEuler 实机上证明：普通 SSH 输入延迟与未安装项目时一致、Codex 长请求不影响其他窗口、代理和 OAuth 自动恢复且不会端口冲突，才将该架构标记为稳定发布基线。
