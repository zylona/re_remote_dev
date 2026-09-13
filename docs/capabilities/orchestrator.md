# 本地编排层能力契约（P0–P3）

P0 只定义本地 SSH/Codex 编排层的身份、状态、事件和错误协议，不建立 SSH 连接，不修改用户 SSH 配置，不写入远端，不启动 systemd 服务。

## 本地服务安装入口

用户可通过 `./install-local-service`（等价于 `./re-remote local-service-install`）独立安装或刷新
本地编排器，不触发任何远端恢复。安装器复用 `setup/bootstrap` 的同一实现，将受控 unit 写入
`~/.config/systemd/user/`，启用 `remote-dev-orchestrator.socket`，刷新服务进程并通过 Unix socket
执行健康检查。重复执行会加载当前仓库实现，不创建第二套服务。

服务始终以当前用户身份运行，不请求 root，不监听公网。systemd user manager 存续期间，socket
activation 保证服务可用；是否在完全退出登录后继续运行由系统的 user lingering 策略决定，项目
不自动修改该系统策略。

## P3 Codex 生命周期 Shim

Codex 安装由 `roles/codex` 提供一个薄包装命令：真实二进制位于
`~/.local/libexec/codex`，用户继续使用熟悉的 `codex` 和 `codex login` 命令。包装器只在
启动和退出时尝试发送事件，不等待编排器、不改变 Codex 的标准输入输出和退出码；编排器不可用
时事件会静默丢弃，因此不会阻塞或破坏普通 Codex 使用。

事件通过目标机 `127.0.0.1:4228` 发送，经 SSH remote forwarding 到本机编排器的
`127.0.0.1:4230`。本机服务同时保留 Unix socket 供 CLI 查询。每次会话的 nonce 由后续
ControlMaster 生命周期写入目标机用户目录；未建立受管会话时包装器不会发送事件。事件只包含
目标摘要、nonce、PID 和退出码，不包含命令行、OAuth 内容、代理凭据或工作区数据。正式
会话由 `SessionManager` 计算目标摘要、创建/复用 ControlMaster、写入每次轮换的 nonce，
并在编排器中注册 ControlPath；停止时按相反顺序注销并清理。

## P4 OAuth callback 转发

`OAuthManager` 只操作已经建立的 SSH ControlMaster，通过 `ssh -O forward` 临时添加
`127.0.0.1:1455 → 127.0.0.1:1455`，再通过 `ssh -O cancel` 清理。它不解析或改写
Codex 生成的 OAuth URL，浏览器仍访问原始 localhost callback。

同一编排器最多维护一个活跃 OAuth callback；第二个目标返回 `OAUTH_BUSY`，避免本机 1455
被多个远端争抢。清理失败报告 `CLEANUP_FAILED`，而不是误报成功。普通已登录 Codex 不会
自动开启 1455，4227 代理和 4228 事件转发不受该临时生命周期影响。

收到已注册目标的 `CODEX_START` 后，编排器会自动调用该目标 ControlMaster 的
`ssh -O forward`；最后一个同目标 Codex 进程发送 `CODEX_EXIT` 时调用 `ssh -O cancel`。
因此用户不需要额外执行登录转发命令。若 1455 已被其他程序占用或 master 不可用，状态
记为失败但 Codex 本身继续启动，便于用户稍后重试。

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
