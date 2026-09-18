# Remote-SSH 风格端口转发重构计划

状态：P0 已完成，P1 已落地，P2 协议核心已落地，P3 forwarder 核心已落地，P4 已在 10.40.6.145 完成真实验收，P5 已在 192.168.122.196 完成密钥登录、端到端代理和 forwarder 崩溃恢复验收，P6 已完成人工 VS Code Remote-SSH 打开远程目录验证，P7 安装迁移/原子切换/回滚/制品检查和 `tssh` 入口已落地，并在 192.168.122.196 完成制品安装→回滚→恢复及 tssh 代理验收（2026-09-18）

本计划承接 [Remote-SSH 思路借鉴与架构重设计](research-remote-ssh-inspired-architecture.md)。
远端基础工具恢复保持现状，本计划只改造本地端口转发、连接触发和本地服务生命周期。

## 1. 重构目标与最终契约

### 1.1 目标

- 普通 `ssh user@host` 是完全原生连接，不执行项目 hook，不等待代理探测；
- VS Code Remote-SSH、Git、scp、rsync、ProxyJump 使用原生 SSH 配置，不被项目接管；
- 项目通过本地 resolver 管理远端 `127.0.0.1:4227` 代理转发；
- 同一 endpoint 只有一个 4227 owner 隧道，多用户可接管，多窗口共享；
- 多设备可以同时使用各自的远端 4227；
- 隧道断开后自动退避重连，代理不可用或目标离线时不产生重启风暴；
- 不部署远端常驻 Agent，不保存密码，不开放公网监听。

### 1.2 明确取舍

默认入口改为：

```bash
remote-dev connect user@host
```

该命令先请求本地 resolver reconcile 代理，再 `exec` 原生 SSH。直接执行任意
`ssh user@ip` 不再默认触发项目逻辑；如用户明确接受兼容风险，另提供 opt-in wrapper，
但不覆盖 `/usr/bin/ssh`，也不作为 VS Code 的 SSH 客户端。

## 2. 分阶段实施总览

| 阶段 | 主题 | 主要产物 | 退出条件 |
| --- | --- | --- | --- |
| P0 | 契约冻结与基线 | 文档、指标、兼容矩阵 | 普通 SSH/VS Code 基线可复现 |
| P1 | 去除全局副作用 | SSH 配置迁移、禁用默认 hook | 普通 SSH 恢复原生且无卡顿 |
| P2 | Endpoint 注册协议 | resolver/orchestrator API、状态模型 | 多窗口请求幂等 |
| P3 | 独立 4227 forwarder | endpoint unit、owner 选举 | 代理端到端可用 |
| P4 | 会话生命周期 | session lease、释放和 TTL | 无会话自动清理，有会话不误删 |
| P5 | 故障恢复与观测 | 健康探测、退避、诊断 | 断网/断代理可恢复且无风暴 |
| P6 | VS Code/其他客户端兼容 | 隔离配置、兼容测试 | Remote-SSH/Git/scp 不受影响 |
| P7 | 迁移、回滚和制品 | 升级器、版本兼容、Release | 旧版本安全迁移/回滚 |
| P8 | 生产回归与发布 | VM/实机测试报告 | 满足发布门禁 |

阶段必须按顺序完成；每阶段都要同步交付代码、独立 verify、测试和文档，不能用下一阶段
的实现掩盖上一阶段的兼容性问题。

## 3. P0：契约冻结与基线

### 实施内容

1. 在能力文档中声明普通 SSH 不受项目管理；
2. 定义 endpoint 键：`canonical_host + ssh_port`；
3. 定义候选身份：`endpoint + user + public-key fingerprint`；
4. 记录当前基线：SSH 首屏时间、首次键入延迟、VS Code 连接成功率、代理请求耗时；
5. 建立兼容矩阵：OpenSSH 版本、systemd user、Debian/openEuler/Arch、密钥/密码认证；
6. 明确 4227 是远端 loopback 代理端口，不是本地 VS Code Server 端口。

基线采集脚本为 `scripts/ssh_baseline.sh`：

```bash
./scripts/ssh_baseline.sh user@host
./scripts/ssh_baseline.sh user@host ~/.ssh/config-vscode
```

脚本只输出连接返回码和耗时，不打印 SSH 错误详情、密钥路径或认证信息；失败详情应由
用户在本地按需使用 `ssh -vvv` 诊断，不纳入共享报告。

### 测试与门禁

- 在未安装项目服务的环境测量普通 SSH；
- 在当前版本测量同一指标，形成迁移前对照；
- 记录所有现有 unit、`~/.ssh/config` 标记块和状态目录；
- 文档不得包含真实设备地址、私钥文件名或密码。

### 回滚点

只新增文档和测试，不改变用户环境；P0 失败可直接停止。

## 4. P1：去除全局 SSH 副作用

### 实施内容

1. 修改 SSH 集成器：默认不再写入全局 `LocalCommand`、`PermitLocalCommand`、
   `ControlMaster no`、`ControlPath none` 和 `ExitOnForwardFailure yes`；
2. 提供迁移命令，备份并移除旧的受管块、旧 Include 和项目 target block；
3. 保留用户的 `HostName`、`User`、`IdentityFile`、`ProxyJump`、X11 和其他非项目规则；
4. 不生成额外的 `config-vscode`；VS Code 直接使用清理后的普通 SSH 配置；
5. 删除或显式禁用旧 hook 的自动启动逻辑；
6. 普通 SSH 断开时不应影响任何项目 unit。

### 验收

```bash
ssh -G host | grep -E 'localcommand|permitlocalcommand|controlmaster|controlpath'
```

普通配置不应出现项目 hook。连续打开三个终端，首屏和输入延迟应接近 P0 基线；
VS Code 使用主配置也不得触发项目服务。

### 回滚点

恢复 SSH 配置备份即可；不停止用户已有普通 SSH 进程。

## 5. P2：Endpoint 注册协议

### 实施内容

设计一个最小本地 Unix socket 协议，不引入数据库、scheduler 或远端 Agent。

请求：

```json
{
  "op": "acquire",
  "endpoint": {"host": "example", "port": 22},
  "candidate": {"user": "developer", "identity_fingerprint": "..."},
  "purpose": "proxy",
  "session_id": "random"
}
```

响应：

```json
{
  "ok": true,
  "endpoint_digest": "...",
  "owner": "developer",
  "state": "STARTING",
  "remote_port": 4227
}
```

要求：

- 请求天然幂等，重复 `acquire` 不创建第二条隧道；
- socket 权限为当前用户专属；
- endpoint digest 不暴露密码、完整主机凭据或私钥内容；
- 状态保存在 `XDG_STATE_HOME`，崩溃后可从 systemd unit 重建；
- resolver 超时只返回 `DEGRADED`，不阻塞原生 SSH 登录。

### 验收

- 50 个并发 acquire 请求只产生一个 endpoint forwarder；
- 编排器重启后状态可恢复或安全降级；
- 非法 JSON、超大 payload、未知操作不会导致服务退出。

## 6. P3：独立 4227 forwarder 与 owner 选举

### 实施内容

每个 endpoint 使用一个独立的 systemd user unit：

```text
remote-dev-proxy-<endpoint_digest>.service
```

forwarder 使用独立 `ssh -N -T -R`，禁止共享普通 SSH ControlMaster：

```text
ControlMaster=no
ControlPath=none
ExitOnForwardFailure=yes
ServerAliveInterval=15
ServerAliveCountMax=3
```

owner 选举规则：

1. 健康 owner 优先复用；
2. owner 失效后，只从仍有 lease 的候选中选择；
3. 候选必须能以密钥 BatchMode 登录；
4. 按最近成功时间和连接稳定性排序，保证同一时刻只选一个；
5. 远端固定监听 `127.0.0.1:4227`；
6. 同 endpoint 不允许第二 owner 抢占端口；
7. 多 endpoint 各自拥有独立 unit，即使远端端口相同也不冲突。

### 端到端健康检查

状态不能只依据 systemd `active`：

```text
PROCESS_READY
  → REMOTE_LISTENING
  → PROXY_READY
```

必须分别检查 SSH 进程、远端 4227 TCP 监听和 HTTP 代理请求。

### 验收

- A 用户建立隧道，B 用户 acquire 只复用不抢占；
- A 断开，B 仍有 lease 时由 B 接管；
- A/B 都无 lease 时停止 forwarder；
- 4227 已被非项目进程占用时报告冲突，不反复重启。

## 7. P4：会话 lease 与自动清理

### 实施内容

`remote-dev connect` 获得一个 session lease，退出时显式释放；异常退出依靠 TTL 和
keepalive 清理。

lease 记录：

- `session_id`；
- endpoint digest；
- candidate user/fingerprint；
- 创建时间、最后 heartbeat、TTL；
- 不记录密码和完整命令行。

流程：

```text
connect → acquire lease → reconcile → exec ssh
                                │
                         shell exit/release
                                │
                      无 lease → stop forwarder
```

如果同一 endpoint 仍有其他用户 lease，释放一个会话不能停止代理。heartbeat 丢失超过 TTL
后才允许清理孤儿 lease。

### 验收

- 正常退出、Ctrl-C、SIGTERM、终端断网均能最终释放；
- 编排器崩溃后重启不会立即删除仍有效的 owner；
- TTL 清理不会影响其他用户窗口；
- 清理动作只删除项目创建的 unit 和状态文件。

## 8. P5：故障恢复、退避与诊断

### 实施内容

实现有限状态机：

```text
ABSENT → STARTING → READY
             │         │
             └→ DEGRADED ←┘
                      │
                    FAILED
```

故障分类：

- `LOCAL_PROXY_UNAVAILABLE`：本机 4227 不可访问；
- `SSH_AUTH_FAILED`：密钥/agent 认证失败；
- `FORWARDING_DENIED`：远端 sshd 拒绝转发；
- `REMOTE_PORT_BUSY`：远端 4227 被其他进程占用；
- `REMOTE_UNREACHABLE`：网络或主机不可达；
- `HALF_OPEN`：SSH 进程存在但端到端请求失败。

重连策略：5、10、20、40、80 秒，之后上限 5 分钟；连续失败进入 degraded，新的显式
connect 请求可以立即触发一次 reconcile。systemd 设置 `RestartSec`、`StartLimit*`、
`MemoryMax` 和 `TasksMax`。

新增诊断：

```bash
remote-dev orchestrator status
remote-dev orchestrator doctor
remote-dev orchestrator logs <host>
```

输出只包含摘要、状态、端口和错误类别，不显示密码、Token 或完整 OAuth URL。

### 验收

- 停止本机代理、断开目标网络、杀掉 SSH forwarder 后自动恢复；
- 30 分钟断网不发生重启风暴；
- 恢复网络后无需重新打开终端即可恢复 4227；
- 普通 SSH 和 VS Code 连接不因 forwarder 失败而退出。

## 9. P6：VS Code 与其他 SSH 客户端兼容

### 实施内容

1. 删除默认全局 hook 后，验证 VS Code 可以直接使用用户原始 SSH 配置；
2. VS Code 直接使用普通 `~/.ssh/config`，不再生成隔离配置；
3. 不向 VS Code 的随机动态端口注入 4227；
4. 不修改 VS Code 的 `ControlMaster`、`DynamicForward` 或 `ClearAllForwardings`；
5. 测试 Git SSH、scp、rsync、ProxyJump、端口转发和 SSH agent forwarding；
6. 若用户选择 `remote-dev connect`，其内部 `exec ssh` 必须传递原始参数，不重写交互终端。

### 验收

- Remote‑SSH 能安装/启动远端 Server 并建立动态转发；
- VS Code 日志不出现项目 hook 输出；
- Git clone/fetch、scp、rsync 成功；
- VS Code Server 的随机端口与 4227 不发生冲突。

## 10. P7：迁移、回滚与发布制品

### 实施内容

- 安装器默认只安装 orchestrator/resolver，不再默认写全局 SSH hook；
- 提供 `migrate`：备份并清理旧标记块、旧 unit、旧 socket 和孤儿状态；
- 旧版本状态只读兼容一个版本周期，随后安全删除；
- 制品包含本地 resolver、orchestrator、systemd user unit 和诊断命令；
- 明确 `remote-dev connect` 与普通 `ssh` 的行为差异；
- 支持 `uninstall` 恢复用户 SSH 配置，不删除未标记内容；
- 版本升级采用原子 `current` 链接，失败保留旧版本可回滚。

### 验收

- 从当前版本升级不需要手工编辑 SSH 配置；
- 升级中断后普通 SSH 仍可用；
- 回滚后旧代理隧道可以恢复；
- Release tarball 不携带真实 Inventory、凭据或本机路径。

## 11. P8：生产级回归与发布门禁

### 测试矩阵

| 类别 | 场景 |
| --- | --- |
| 系统 | Debian VM、openEuler 实机、至少一种 Arch/Omarchy |
| 连接 | 单窗口、多窗口、多用户、多设备、密钥和密码 |
| 网络 | 本机代理停止、目标断网、主机重启、DNS 变化、SSH keepalive 超时 |
| 转发 | 4227 占用、sshd 禁止转发、IPv4-only、端口重复 acquire |
| 客户端 | 普通 SSH、VS Code Remote‑SSH、Git、scp、rsync、ProxyJump |
| 生命周期 | 正常退出、Ctrl-C、SIGTERM、终端关闭、编排器重启 |

### 必须记录

- 普通 SSH 首屏和键入延迟与 P0 基线差异；
- forwarder 状态迁移和 owner 变化；
- 端口监听与进程 PID；
- 失败分类、重试间隔和最终恢复时间；
- 服务 CPU/内存、unit 数量和日志增长；
- 配置文件变更和回滚结果。

### 发布门禁

1. `ansible-inventory`、Playbook syntax-check、ansible-lint；
2. 全部 pytest/testinfra/Molecule 测试；
3. fresh、重复、升级、回滚四类安装；
4. 两台 VM/实机完成多窗口和多用户测试；
5. 至少 30 分钟断网/恢复连续观察；
6. 普通 SSH、VS Code 和其他 SSH 客户端无回归；
7. `git diff --check`，且制品脱敏审计通过。

## 12. 依赖关系与停止条件

```text
P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8
```

出现以下任一情况必须停止推进并回滚当前阶段：

- 普通 SSH 仍执行项目 hook 或首屏明显变慢；
- VS Code 动态转发失败且无法通过独立配置复现/排除；
- 同 endpoint 出现两个 4227 owner；
- forwarder 失败导致普通 SSH 或 VS Code 退出；
- 连续失败触发 systemd 重启风暴；
- 回滚会删除用户未标记的 SSH 配置；
- 日志、状态或制品包含密码、Token、私钥内容或真实环境信息。

## 13. 最终决策

本项目不再把“任意 `ssh user@ip` 自动触发代理”作为默认稳定性契约。稳定产品应优先
保证原生 SSH 和 VS Code 兼容，再通过 `remote-dev connect` 提供自动代理。只有完成 P1
并证明普通 SSH 不受影响后，才继续实现 P2–P5 的自动编排；否则继续堆叠 hook、ControlMaster
或端口规则只会扩大当前问题。
