# tssh Endpoint 隧道生命周期重构设计

## 结论

目标需求应采用“本地编排器 + endpoint 单隧道 + 会话 lease 引用计数”，而不是让
每个窗口创建一条 SSH 隧道，也不是让代理 unit 默认常驻。

对同一设备（`hostname + ssh_port`）：

1. 第一个 `tssh user@host` 申请 lease，并创建唯一的 4227/4228 反向隧道；
2. 后续窗口或其他用户只申请 lease，复用现有隧道；
3. 任意一个窗口退出，只释放自己的 lease；
4. 仍有其他 lease 时，隧道继续保持；
5. 最后一个 lease 释放后，停止并删除隧道；
6. 编排器重启或进程异常后，清理没有活动 lease 的孤儿隧道。

这样可以同时满足：多窗口复用、同设备多用户、单端口固定为 4227、普通 SSH/VS Code
不受影响，以及无连接时不保留远程代理。

## 与当前实现的差异

当前 `tssh.socket` 是 socket activation 入口，本身只监听本地 Unix socket，资源占用很低；
`tssh.service` 负责 lease 和 owner 管理，不是 tmux，也不承载远程终端。

当前 endpoint proxy 使用独立的 `remote-dev-proxy-<digest>.service`，并通过
`systemctl --user enable --now` 启用。这个设计能支持断线重连，但会留下一个脱离 lease 的
后台 SSH 隧道：编排器重启后内存中的 lease 清空，而已启用的 proxy unit 仍可能继续运行。

重构应保留 `tssh.socket` 的按需编排能力，但把 endpoint proxy 改为“非启用的临时 unit”，
由 lease 的首尾事件控制其生命周期。

## 状态模型

编排器维护两个层次的状态：

```text
Endpoint(host, port)
  ├─ owner: Candidate
  ├─ tunnel: STARTING | READY | DEGRADED | STOPPING | ABSENT
  └─ leases: {session_id -> Candidate + heartbeat}
```

`Candidate` 包含用户名、可用私钥路径和公钥指纹，不保存密码。`session_id` 由每个
`tssh` 进程随机生成，退出时显式 release，异常退出由 heartbeat TTL 回收。

同一 endpoint 的 tunnel key 只使用 `hostname + port`，不能包含用户，否则同设备不同用户
会错误地创建多个远端 4227 listener。

## Acquire 算法

所有操作在编排器单线程事件循环或同一把 endpoint 锁内完成：

```text
acquire(endpoint, candidate, session_id)
  1. 清理已过期 lease
  2. session_id 已存在且 endpoint 相同：刷新时间并返回 reused=true
  3. endpoint 不存在：创建 record，写入第一个 lease，选为 owner
  4. endpoint 已存在：只追加 lease，不创建新 SSH 隧道
  5. endpoint 隧道 READY：立即返回
  6. endpoint 隧道 ABSENT/DEGRADED：由 owner candidate 启动或重试隧道
  7. 启动失败：保留 lease，返回 DEGRADED；不阻塞原生 SSH 登录
```

首个窗口和后续窗口之间必须有原子检查，不能用“先检查端口、再启动 SSH”的无锁流程，
否则并发窗口会同时看到端口空闲并竞争远端 4227。

## Release 与最后一个连接清理

```text
release(endpoint, session_id)
  1. 删除 session_id 对应 lease；未知 session_id 视为幂等成功
  2. 若释放者是 owner，从剩余 lease 中确定稳定的 replacement
  3. 若仍有 lease：
       - 隧道健康：保持原 SSH 进程，不做切换
       - 隧道故障：使用 replacement 的密钥重建
  4. 若 lease 数为 0：停止 tunnel，禁用/删除临时 unit，删除 endpoint record
```

owner 只表示“负责建立或接管隧道的凭据”，不表示某个窗口拥有代理。owner 退出时不应
立即停止健康隧道；只有没有候选 lease 或隧道确实失效时才进行 owner 迁移。

## 断线、崩溃和编排器重启

### tssh 进程异常退出

heartbeat 默认 1 秒，TTL 采用 3 秒。编排器发现过期 lease 后执行与 release 相同的
owner 选举和清理逻辑。

### SSH 隧道异常退出

隧道进程使用：

```text
ExitOnForwardFailure=yes
ServerAliveInterval=15
ServerAliveCountMax=3
```

编排器将状态设为 `DEGRADED`，按 5、10、20、40、80、160、300 秒退避重试。重试候选只从
仍有 heartbeat 的 lease 中选择；没有候选时直接清理 unit。

### 编排器重启

lease 状态默认不持久化。编排器启动时必须执行一次 orphan reconcile：

1. 枚举受管的 endpoint 临时 unit；
2. 读取 unit 中的 endpoint digest 和 owner metadata；
3. 因为新进程没有任何 lease，停止并删除这些孤儿 unit；
4. 后续第一个 `tssh` 连接再重新建立隧道。

不能让旧 proxy unit 继续 `WantedBy=default.target`，否则 systemd 会在没有任何 tssh 会话时
再次拉起它。

## systemd 实现建议

使用 systemd transient service 或普通 unit 文件但不启用：

```text
systemd-run --user --unit=tssh-proxy-<digest> \
  --property=Restart=on-failure \
  --property=RestartSec=5 \
  --property=CollectMode=inactive-or-failed \
  -- /usr/bin/ssh ... -R 127.0.0.1:4227:127.0.0.1:4227
```

systemd 支持 transient unit 的 `CollectMode`、`BindsTo`、`PartOf` 等生命周期设置；
这些设置适合把隧道绑定到编排器和 lease 管理，而不是安装成默认启动服务。

如果目标发行版的 user systemd 不支持完整 transient API，则退回写入：

```text
~/.config/systemd/user/tssh-proxy-<digest>.service
```

但必须满足：

- 不创建 `.wants/` 链接；
- `enable` 永远不调用；
- release 最后一个 lease 时执行 `disable --now` 并删除 unit；
- 编排器启动时清理所有没有 lease 的受管 unit。

systemd transient unit 的能力范围见 [systemd transient settings](https://github.com/systemd/systemd/blob/main/docs/TRANSIENT-SETTINGS.md)。

## SSH 连接隔离

代理隧道必须是独立的无终端 SSH 进程：

```text
ssh -F /dev/null -N -T
  -o ControlMaster=no
  -o ControlPath=none
  -o ExitOnForwardFailure=yes
  -R 127.0.0.1:4227:127.0.0.1:4227
```

交互式 `tssh` 会话仍然使用另一条原生 SSH 连接，stdin/stdout 不经过编排器。这样不会把
代理重连、heartbeat 或大流量 HTTP 请求引入终端输入路径。

普通 `ssh user@host`、VS Code Remote‑SSH、Git、scp 和 rsync 不应自动申请 tssh lease，也
不应读取 tssh socket。VS Code 自身的动态端口转发由 Remote‑SSH 独立管理；官方文档也将
远程服务连接和端口转发作为独立连接阶段处理，见 [Remote‑SSH troubleshooting](https://code.visualstudio.com/docs/remote/troubleshooting)。

## 故障与边界

| 场景 | 预期行为 |
|---|---|
| 同 endpoint 两个窗口并发启动 | 只创建一个 tunnel，第二个 acquire 返回 reused |
| A 退出、B 仍在线 | tunnel 保持，B 不感知切换 |
| owner A 断线、B 在线 | B 成为 replacement，重建失败时进入退避 |
| 所有窗口退出 | 停止并删除 tunnel/unit |
| 编排器重启 | 清理无 lease 的全部受管 tunnel |
| 远端 4227 已被占用 | endpoint DEGRADED，返回 `REMOTE_PORT_BUSY`，不影响原生 SSH |
| 私钥不可用 | 不保存密码；代理失败，交互 SSH 仍可用 |
| 普通 SSH 或 VS Code 连接 | 不创建 tssh lease，不共享 tssh tunnel |

## 验收测试

必须覆盖：

1. 单窗口首连和退出后 4227 消失；
2. 同设备两个窗口只存在一个 SSH `-R` 进程；
3. 不同用户共享 endpoint，owner 退出后接管；
4. 最后一个 lease 释放后 unit、进程和远端 4227 均消失；
5. 隧道进程被 kill 后退避重连；
6. 编排器重启后不残留 proxy unit；
7. VS Code 使用普通 `ssh` 时不触发 tssh acquire；
8. `tssh` 隧道故障不阻塞普通 SSH、VS Code SSH 动态转发或终端输入。
