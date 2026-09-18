# 本地编排层能力契约（现行实现与重构基线）

> P0 重构基线：普通 `ssh user@host` 必须保持原生，不再默认触发项目 hook。自动代理由
> `remote-dev connect user@host` 显式申请；VS Code Remote-SSH、Git、scp、rsync 不读取
> 项目编排配置。下文“普通 SSH 自动代理”描述的是旧版兼容实现，进入 P1 后将移除默认启用。

P0 只定义本地 SSH/Codex 编排层的身份、状态、事件和错误协议，不建立 SSH 连接，不修改用户 SSH 配置，不写入远端，不启动 systemd 服务。

## 本地服务安装入口

### 旧版普通 SSH 自动代理（已弃用）

旧版本通过以下命令启用全局 hook：

```bash
./re-remote ssh-integration-install
```

该模式会启用 `PermitLocalCommand`，在每次普通 `ssh user@host` 成功连接后调用
`~/.local/bin/remote-dev-ssh-hook`。该 hook 根据 SSH 最终解析出的主机、用户、端口和密钥，
为设备 endpoint（主机名/IP + SSH 端口）创建唯一的 systemd user proxy unit，并以独立
`ssh -N -T -R 127.0.0.1:4227` 维持隧道。同一设备的不同用户和多个窗口共享一个 unit；
endpoint 锁保证并发登录不会重复绑定远端 4227。交互 SSH 使用 `ControlMaster no`，因此代理
流量不会阻塞终端输入。优先使用密钥或 ssh-agent；没有可用密钥时，首次 hook 会尝试通过当前
TTY 让用户输入一次 SSH 密码建立临时隧道，但密码隧道断线后不能无人值守重连。

用户可通过 `./install-local-service`（等价于 `./re-remote local-service-install`）独立安装或刷新
本地编排器，不触发任何远端恢复。安装器复用 `setup/bootstrap` 的同一实现，将受控 unit 写入
`~/.config/systemd/user/`，启用 `remote-dev-orchestrator.socket`，刷新服务进程并通过 Unix socket
执行健康检查。重复执行会加载当前仓库实现，不创建第二套服务。

服务始终以当前用户身份运行，不请求 root，不监听公网。systemd user manager 存续期间，socket
activation 保证服务可用；是否在完全退出登录后继续运行由系统的 user lingering 策略决定，项目
不自动修改该系统策略。

### P1 原生 SSH 契约

P1 起本地服务安装和 bootstrap 会迁移旧版全局 hook，并在写入前创建带时间戳的
`~/.ssh/remote-dev/backups/config.*~` 备份；不会新增 `LocalCommand`、`PermitLocalCommand`、
`ControlMaster no`、`ControlPath none` 或项目固定转发规则。普通 SSH、VS Code Remote-SSH、
Git、scp 和 rsync 不再触发 remote-dev 服务。自动代理由 resolver 的显式
`remote-dev connect user@host` 申请。

## P2 Endpoint 注册协议

P2 引入本地 Unix socket 的 endpoint lease 协议，但暂不创建真实 SSH 转发。resolver 使用
`acquire` 注册一个 `hostname + port` endpoint 和当前 SSH 用户候选，使用随机 `session_id`
保证重复请求幂等；退出时调用 `release`。同一 endpoint 的多个 session 共享一个登记记录，
并返回当前候选 owner。P3 才会根据该登记启动唯一的 4227 forwarder 并实现 owner 接管。

请求不包含密码或私钥内容：

```json
{"op":"acquire","endpoint":{"hostname":"host","port":22},"candidate":{"user":"u","identity_fingerprint":"sha256:..."},"session_id":"opaque"}
```

状态查询会额外返回 `endpoints` 数组，包含 endpoint 摘要、状态、owner 和 session 数量；
状态存于编排器内存，服务重启后安全清空，不会产生远端副作用。非法 endpoint、candidate、
session 或跨 endpoint 重用 session 会返回 `PROTOCOL_INVALID`/`SESSION_CONFLICT`，不会使服务退出。

## P3 Codex 生命周期 Shim

Codex 安装由 `roles/codex` 提供一个薄包装命令：真实二进制位于
`~/.local/libexec/codex`，用户继续使用熟悉的 `codex` 和 `codex login` 命令。包装器只在
启动和退出时尝试发送事件，不等待编排器、不改变 Codex 的标准输入输出和退出码；编排器不可用
时事件会静默丢弃，因此不会阻塞或破坏普通 Codex 使用。

事件通过目标机 `127.0.0.1:4228` 发送，经 SSH remote forwarding 到本机编排器的
`127.0.0.1:4230`。本机服务同时保留 Unix socket 供 CLI 查询。每次会话的 nonce 由后续
受管 SSH 生命周期写入目标机用户目录；未建立受管会话时包装器不会发送事件。事件只包含
目标摘要、nonce、PID 和退出码，不包含命令行、OAuth 内容、代理凭据或工作区数据。正式
普通 SSH 不复用项目 ControlMaster。`SessionManager` 维护独立的受管连接，
代理由独立的 `ProxyForwarder`（`ssh -N -R`、`ControlMaster=no`）承载；P3 起事件也由独立的
`EventForwarder`（目标 4228 → 本机 4230）承载，避免代理或事件流量阻塞交互终端。两个进程
均启用 `ExitOnForwardFailure` 和保活参数，停止时按相反顺序注销并清理。

## P4 会话 Lease 与自动清理

`remote-dev connect user@host` 会向本地编排器申请随机 `session_id` lease，然后启动原生
SSH。编排器为 lease 记录 endpoint、候选用户、密钥指纹和最后 heartbeat，不记录密码或完整
命令行。连接进程每 30 秒发送一次 heartbeat；重复 acquire 使用同一 `session_id` 幂等返回，
会话退出调用 `release`，超过 120 秒未 heartbeat 的孤儿 lease 会被自动释放。只有 endpoint
的最后一个 lease 释放时，才允许停止该 endpoint 的 4227 forwarder；其他用户仍在线时不会
误删共享隧道。

## P4 OAuth callback 转发

`OAuthManager` 使用独立 SSH `-N -L` 进程临时添加
`127.0.0.1:1455 → 目标 127.0.0.1:1455`，登录结束后终止进程清理。它不操作
ControlMaster、不解析或改写 Codex 生成的 OAuth URL，浏览器仍访问原始 localhost callback。
IPv4 是必需路径，`::1` IPv6 listener 在系统支持时以 best-effort 方式额外启用。

同一编排器最多维护一个活跃 OAuth callback；第二个目标返回 `OAUTH_BUSY`，避免本机 1455
被多个远端争抢。清理失败报告 `CLEANUP_FAILED`，而不是误报成功。普通已登录 Codex 不会
自动开启 1455，4227 代理和 4228 事件转发不受该临时生命周期影响。

收到已注册目标的 `CODEX_START` 后，编排器会自动启动独立 callback forwarder；最后一个同目标
Codex 进程发送 `CODEX_EXIT` 时终止全部 callback forwarder。
因此用户不需要额外执行登录转发命令。若 1455 已被其他程序占用或 master 不可用，状态
记为失败但 Codex 本身继续启动，便于用户稍后重试。

## P5 故障恢复与诊断

endpoint forwarder 失败时不会阻塞或关闭交互 SSH。编排器记录不含凭据的故障类别，并按
5、10、20、40、80、160、300 秒的上限策略重试；状态查询只返回 `last_error` 类别和剩余
重试时间，不暴露完整 SSH 错误、密钥路径或密码。支持的主要类别包括
`SSH_AUTH_FAILED`、`FORWARDING_DENIED`、`REMOTE_PORT_BUSY`、`REMOTE_UNREACHABLE` 和
`LOCAL_PROXY_UNAVAILABLE`。systemd user unit 同时配置失败重启限流、内存上限和任务数上限，
避免代理或编排器故障造成重启风暴。

## P6 可选长期会话

`bootstrap` 默认启用长期会话；单独使用编排器 API 时，也可显式执行：

```bash
remote-dev orchestrator enable-persistent HOST USER -i ~/.ssh/id_ed25519
remote-dev orchestrator disable-persistent HOST USER
```

每个目标生成独立的 systemd `--user` unit，服务内部调用同一 `SessionManager --persistent`
路径，因而与普通连接共享 ControlPath，不会重复绑定远端 4227。禁用或服务停止时会清理
nonce、编排器注册和 master；长期策略不会自动改变普通 SSH 配置。

受管会话不能只以 `ssh -O check` 判断就绪：启动或复用 master 后还必须从目标机验证
`127.0.0.1:4227` 和 `127.0.0.1:4228` 可连接。缺少转发时通过 control socket 动态补建；同一
主机的另一登录用户已提供监听时直接共享。持久进程每 5 秒复核 master 和转发，连续三次失败才
退出，避免 Docker 重启或短暂负载误杀健康隧道；随后由 systemd 以 30 秒间隔恢复，10 分钟内
连续失败 10 次后限流，避免离线设备无限高频重试。
只有验证成功后才向编排器注册 `proxy_available=true`。

## 身份与隔离

目标身份是 `hostname + port + user + identity_fingerprint`，而不是 SSH 别名。其 SHA-256 前 32 位作为 ControlPath、状态和日志关联标识；日志不输出完整密钥路径、密码或 Token。

状态枚举为：`ABSENT`、`MASTER_STARTING`、`READY`、`CODEX_RUNNING`、`OAUTH_PENDING`、`AUTHENTICATED`、`DEGRADED`、`FAILED`。P0 只提供数据模型，后续阶段才能改变状态。

## v1 事件协议

远端 shim 与本地编排器使用 UTF-8、单行 JSON、换行结尾，单条消息最大 8 KiB：

```json
{"v":1,"event":"CODEX_START","target":"<target-digest>","pid":1234,"args_mode":"normal","nonce":"<opaque>"}
```

允许事件：`HELLO`、`CODEX_START`、`CODEX_EXIT`、`HEALTH`。消息必须包含 `v`、`event`、`target`、`nonce`；版本不兼容、字段类型错误、超长消息和无效 PID 直接拒绝。未知字段为兼容性保留，但不会进入状态或日志。

`CODEX_START` 只表示 Codex 被启动，不表示用户未登录或 OAuth 已开始；后续 OAuth 状态必须由实际 callback/forwarding 事实确认。`CODEX_EXIT` 可包含退出码。协议不传递完整命令行、OAuth URL、OAuth code/state、代理凭据或工作区内容。

## 错误码

实现应使用稳定错误码：`PROTOCOL_INVALID`、`TARGET_NOT_FOUND`、`MASTER_UNAVAILABLE`、`FORWARDING_DISABLED`、`PORT_CONFLICT`、`OAUTH_BUSY`、`OAUTH_TIMEOUT`、`CLEANUP_FAILED`。错误信息必须包含原因、影响和恢复建议，但不得包含 Secret。

## P0 验收

* 协议可稳定编码/解码，拒绝无效版本、缺字段、错误类型和超过 8 KiB 的消息；
* `TargetKey` 能区分不同用户、端口、密钥身份，别名变化不会意外合并目标；
* digest 不暴露完整目标字符串；
* 未引入 SSH、socket、systemd、远端文件或凭据写入；
* pytest、lint、syntax-check 和 `git diff --check` 通过。
