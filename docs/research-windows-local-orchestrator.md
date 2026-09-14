# Windows 本地编排器等效实现调研

## 结论摘要

Windows 端可以实现本项目的核心能力：用户继续执行普通的 `ssh user@host`，本机后台自动维护到目标设备的 SSH 反向转发，将本机 `127.0.0.1:4227` 暴露为目标机的 `127.0.0.1:4227`。Windows 10 1809 及以后、Windows Server 2019 及以后已经提供 OpenSSH Client；密钥和 `ssh-agent` 也有系统级支持。[Microsoft OpenSSH 概览](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-overview)、[Windows 密钥管理](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)

但是，当前 Linux 实现不能原样移植。Win32-OpenSSH 的设计文档明确指出，`ControlMaster` 依赖的 Unix ancillary data/文件描述符传递在 Windows 上尚未完整支持，相关能力仍是限制项。[Win32-OpenSSH 设计说明](https://github.com/PowerShell/Win32-OpenSSH/wiki/About-Win32-OpenSSH-and-Design-Details) 因此 Windows 版本应保留业务协议和端点选主逻辑，替换 Linux 专属的 systemd、Unix socket 和 ControlMaster。

推荐的产品形态是：

```text
Windows 用户登录会话
  └─ remote-dev-orchestrator.exe（用户上下文常驻进程）
       ├─ Named Pipe IPC（本机 hook/CLI）
       ├─ endpoint 状态与并发锁
       ├─ 每个 endpoint 一个独立 ssh.exe -N -R 隧道
       └─ 指数退避、断线重连、owner 接管

ssh user@host
  └─ %USERPROFILE%\\.ssh\\config 的 LocalCommand
       └─ PowerShell hook：通知编排器 reconcile，随后立即返回
```

第一版不需要 Windows 防火墙入站规则：SSH 隧道是由 Windows 主动向目标机建立的出站连接，远端监听地址固定为 `127.0.0.1:4227`。只有产品未来需要让其他局域网设备访问 Windows 本地端口时，才需要显式创建 Windows Defender Firewall 规则。

## 1. 当前 Linux 实现与 Windows 的差异

| 能力 | 当前 Linux | Windows 等效实现 | 影响 |
|---|---|---|---|
| SSH 客户端 | OpenSSH | 系统 OpenSSH Client 或 Win32-OpenSSH | 命令参数基本兼容 |
| 长期进程 | systemd `--user` | 用户登录任务、WinSW 或原生 Windows Service | 用户上下文和 UAC 需要明确 |
| IPC | Unix socket | Named Pipe；兼容模式可用 loopback TCP | 需要权限边界设计 |
| 连接复用 | ControlMaster/ControlPath | 不应依赖 ControlMaster | 改为编排器持有独立隧道 |
| 并发锁 | `fcntl.flock` | Windows file lock 或 Named Mutex | 端点级锁仍可保留 |
| hook | `LocalCommand` + Python | `LocalCommand` + PowerShell/EXE | 必须异步启动，不能阻塞 SSH |
| 凭据 | 私钥/ssh-agent | `%USERPROFILE%\.ssh`、Windows `ssh-agent` | 推荐密钥，不保存密码 |
| 日志 | journald | `%LOCALAPPDATA%\remote-dev\logs` 或 Event Log | 需滚动和脱敏 |
| 网络策略 | 仅 loopback 反向转发 | 同样仅 loopback 反向转发 | 默认无需防火墙变更 |

Windows OpenSSH Client 的默认配置路径是 `%USERPROFILE%\.ssh\config`。[Microsoft OpenSSH 配置说明](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration) 这使现有“全局 Host 块 + LocalCommand hook”的用户体验可以保留，但配置写入和命令行转义必须采用 Windows 路径及 PowerShell 规则。

## 2. SSH 反向代理能力

Windows OpenSSH Client 支持标准远程转发参数，核心命令仍然是：

```text
ssh.exe -N -T `
  -o ExitOnForwardFailure=yes `
  -o ServerAliveInterval=30 `
  -o ServerAliveCountMax=3 `
  -R 127.0.0.1:4227:127.0.0.1:4227 user@host
```

`-R` 的监听端在目标服务器，连接发起端和代理源端在 Windows；因此 Windows Defender Firewall 不需要放行目标端口的入站流量。目标 SSH 服务仍必须允许 `AllowTcpForwarding`，且目标机上的 4227 必须没有其他进程占用。

SSH 配置仍可使用：

```sshconfig
Host * !github.com
  ExitOnForwardFailure yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
  PermitLocalCommand yes
  LocalCommand powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\\.local\\bin\\remote-dev-ssh-hook.ps1" %h %p %r
```

但 `LocalCommand` 是同步执行的本地命令；OpenSSH 文档说明它在成功连接后执行，且不共享父 SSH 会话的标准输入输出。[OpenSSH ssh_config 说明](https://manpages.debian.org/testing/openssh-client/ssh_config.5.en.html) Windows hook 必须使用 `Start-Process` 或一个短命令客户端向编排器发送请求后立即退出，不能在 hook 内前台运行 `ssh -N`。

## 3. ControlMaster 缺失带来的架构调整

当前 Linux 方案将普通 SSH 会话与代理/事件/OAuth forwarder 隔离，ControlMaster 只承担持久连接和控制路径。Windows 不应依赖这条路径：Win32-OpenSSH 文档指出 ControlMaster 所需的 ancillary data 支持在 Windows 上不完整，且 Windows 端会使用命名管道替代部分 Unix socket 行为。[Win32-OpenSSH 设计说明](https://github.com/PowerShell/Win32-OpenSSH/wiki/About-Win32-OpenSSH-and-Design-Details)

Windows 版本应改为：

1. 每个 `endpoint = hostname:port` 只有一个由编排器直接持有的 `ssh.exe -N -T -R` 进程。
2. 普通 `ssh user@host` 保持独立连接，不加入该隧道，不共享 stdin/stdout，不影响输入延迟。
3. 多用户只共享 endpoint 代理隧道；Codex/OAuth 或用户专属能力使用 `endpoint + remote_user` 状态键。
4. 端点锁由 Named Mutex 或独占 lock file 实现，创建隧道前再次检查进程和目标健康状态。
5. 隧道死亡后由编排器指数退避重建；若原 owner 不可用，从仍有活动会话的用户中选择一个可用密钥 owner。

这套设计与 VS Code Remote-SSH 的原则一致：本地客户端负责端口分配和转发生命周期，远端只提供 SSH 通道；VS Code 文档也建议用 `LocalForward` 固定端口或在客户端动态管理转发。[VS Code Remote-SSH 端口转发](https://github.com/microsoft/vscode-docs/blob/main/docs/remote/ssh.md#forwarding-a-port-creating-ssh-tunnel)

## 4. Windows 后台生命周期选型

### 4.1 首选：用户登录任务（Task Scheduler）

本项目的编排器需要读取当前用户的 SSH 私钥、ssh-agent 和桌面会话，首选以当前 Windows 用户身份运行的登录任务，而不是 SYSTEM 服务。Windows Task Scheduler 原生支持 `ONLOGON`、`ONSTART`、延迟启动和任务查询。[schtasks 创建文档](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create)

安装器可创建：

```text
任务名：RemoteDev\Orchestrator
触发器：当前用户登录后，延迟 5 秒
运行身份：当前用户
执行：remote-dev-orchestrator.exe serve --user
重启：任务设置中的失败后重启，指数退避由进程内部负责
```

优点是无需管理员权限、可访问用户密钥和桌面浏览器、不会让多个 Windows 用户共享密钥。缺点是用户未登录时服务不运行；这与本项目的“用户级 SSH”定位一致。

### 4.2 可选：WinSW 包装 Windows Service

WinSW 是 MIT 许可的 Windows Service Wrapper，可包装任意可执行文件，提供 install/start/status 和日志能力；但其安装和管理通常需要管理员权限。[WinSW 项目](https://github.com/winsw/winsw)

不建议第一版默认使用 WinSW：以 SYSTEM 或服务账户运行时无法自然访问交互用户的 ssh-agent 和浏览器。只有明确需要“用户未登录也维护隧道”、并接受凭据以机器服务账户管理时，才提供 WinSW profile。

### 4.3 不推荐：仅靠 Startup 文件夹

Startup 可以快速启动，但缺少可靠的失败重启、状态查询、延迟和权限模型。它只能作为开发模式 fallback，不能作为产品默认。

## 5. IPC 与安全边界

### 推荐：Windows Named Pipe

Linux 的 Unix socket 文件权限不能直接等价到 Windows。Windows Named Pipe 可以绑定 ACL，仅允许当前用户访问，并避免监听 TCP 端口。实现可使用 Python `pywin32`，或在现有标准库实现上增加 Windows 原生 `ctypes` 适配层。

协议继续复用当前 JSON 请求/响应：`health`、`status`、`register`、`unregister`、`CODEX_*` 事件。协议层不应知道 systemd 或 Windows Service。

### 兼容方案：loopback TCP

如果第一阶段不引入 pywin32，可监听 `127.0.0.1` 的随机端口，并把端口和随机认证 token 放在当前用户 ACL 保护的文件中。该方案实现简单，但本机其他用户或恶意进程可能尝试连接，必须加入 token、请求大小限制和 peer 校验；因此不作为长期方案。

### 浏览器与 OAuth

Windows 编排器应通过 `Start-Process` 或 `explorer.exe <url>` 在当前用户桌面会话打开浏览器，不应让 SYSTEM 服务调用浏览器。1455 回调仍由编排器短生命周期建立 `ssh.exe -L 127.0.0.1:1455:127.0.0.1:1455`，完成或退出后清理。浏览器回调和 4227 代理使用不同生命周期，不共享普通 SSH stdin/stdout。

## 6. 防火墙、权限和 UAC

### 不需要放行的情况

- Windows 只作为 SSH 客户端；
- 反向转发目标是远端 `127.0.0.1:4227`；
- 编排器 IPC 使用 Named Pipe；
- OAuth 本地监听只绑定 `127.0.0.1:1455`。

这种模式只需要 Windows 对外建立 SSH 连接。不要为了 4227 或 1455 创建 `Any/Any` 入站规则。

### 需要显式处理的情况

- 使用 loopback TCP IPC 时，应只绑定 `127.0.0.1`，不能绑定 `0.0.0.0`；
- 若将服务安装为机器级 Windows Service，安装、服务恢复和防火墙变更需要 UAC/管理员权限；
- 若 Windows 本机代理只监听特定接口，应在安装前验证 `127.0.0.1:4227` 可连接；
- 企业组策略可能禁止任务创建、出站 SSH 或执行 PowerShell，需要在预检中报告而非静默修改。

Microsoft 的 OpenSSH Server 安装会创建 22 端口入站规则；这只适用于 Windows 作为 SSH Server 的场景，不是本项目 Windows 控制端的必需步骤。[Microsoft OpenSSH 首次配置](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)

## 7. 认证与多用户模型

Windows 版本应延续“密码不持久化”的约束：

- 默认使用 `C:\Users\\<user>\\.ssh\\` 私钥或 Windows `ssh-agent`；
- 微软文档建议使用 `ssh-agent` 保存当前 Windows 用户的私钥。[Windows 密钥管理](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
- 密码模式只能在当前终端交互建立一次隧道；不得把密码写入 Task Scheduler、服务 XML、环境变量或日志；
- 无密钥时不能承诺无人值守断线接管；状态中显示 `PASSWORD_SESSION_ONLY`；
- 不同 Windows 用户各自安装用户任务、IPC 和日志目录，不能跨用户共享私钥；
- 同一 Windows 用户连接多个远端设备时，以 endpoint digest 隔离进程、锁和状态文件。

## 8. 现有代码可复用边界

### 可以直接复用

- `TargetKey`、endpoint digest 和多用户 owner 选择逻辑；
- forwarder 命令模型、`-R 127.0.0.1:4227:127.0.0.1:4227` 参数；
- `ExitOnForwardFailure`、保活、退避、熔断和故障状态模型；
- JSON 协议、事件去重、OAuth URL 捕获规则；
- CLI 的 `status`、`doctor`、安装确认和脱敏日志契约。

### 必须抽象或重写

- `systemd --user` 安装/启停/重置限流；
- Unix socket client/server；
- `fcntl` 文件锁；
- POSIX 路径、shell quoting、`chmod`；
- Linux `xdg-open`/Omarchy 浏览器启动；
- PTY、信号和子进程组清理；Windows ConPTY 可支持交互 SSH，但不能假设 POSIX TTY 行为完全一致。[Win32-OpenSSH TTY/PTY 说明](https://github.com/PowerShell/Win32-OpenSSH/wiki/TTY-PTY-support-in-Windows)

建议新增适配接口：

```text
ProcessSupervisor
IpcTransport
EndpointLock
UserServiceManager
BrowserLauncher
PathLayout
```

Linux 继续使用现有实现，Windows 提供 PowerShell/Win32 实现；核心 orchestrator 不再直接调用 systemd、Unix socket 或 POSIX lock。

## 9. 推荐落地方案

### 推荐默认架构

1. Python 编排器作为单文件/目录制品发布，Windows 使用 `python.exe` 或打包后的 `remote-dev-orchestrator.exe`。
2. 首次安装检测 `ssh.exe`、`ssh-keygen.exe`、`ssh-agent`、PowerShell 版本和用户目录权限。
3. 创建当前用户 Task Scheduler 登录任务，延迟 5 秒启动编排器。
4. 编排器使用 Named Pipe；若依赖包不可用，明确降级为带 token 的 loopback TCP，而不是静默开放端口。
5. SSH 配置只增加一个受管全局块和一个 PowerShell hook；不为每个目标写 Host 条目。
6. hook 只发送 `RECONCILE(host, port, user)`，立即返回；隧道由编排器创建和维护。
7. 远端固定监听 `127.0.0.1:4227`，同 endpoint 只有一个代理隧道；多用户 owner 接管由本地编排器完成。
8. 服务日志写入 `%LOCALAPPDATA%\\remote-dev\\logs`，按大小滚动，不记录密码、完整 URL token 或私钥内容。

### 不推荐的方案

- 依赖 WSL systemd：需要用户启用 WSL、发行版和额外网络层，无法覆盖原生 PowerShell/CMD SSH；
- 依赖 `sshuttle`：它是透明 VPN，Windows 端实现和管理员权限复杂，与“只转发 4227”目标过重。[sshuttle 项目](https://github.com/sshuttle/sshuttle)
- 依赖 PuTTY/Plink 作为唯一客户端：Plink/WinSW 组合可维护隧道，但引入 `.ppk`、额外密钥格式和另一套 SSH 行为；可作为兼容 provider，而非默认；
- 直接把每个 `LocalForward/RemoteForward` 写进用户 SSH config：多窗口和多用户会在固定端口上产生重复监听，不能完成 owner 接管。

## 10. 分阶段实现建议

### P0：Windows 兼容性探测

验证 Windows 10/11 和 Server 2019+ 的 OpenSSH Client、PowerShell、ssh-agent、用户目录、目标 host key 和出站 SSH。对缺失 OpenSSH Client，提示通过 Optional Features/WinGet 安装；不要默认修改防火墙。

### P1：Windows 本地服务与 IPC

实现 Named Pipe、Windows 路径布局、用户级 Task Scheduler 安装/卸载、健康检查和日志滚动。先不接入自动 SSH hook，允许 CLI 手动 `reconcile` 验证。

### P2：端点级代理隧道

接入 `ssh.exe -N -T -R`，覆盖首次启动、端口冲突、目标拒绝 forwarding、断网、代理不可用、进程退出和指数退避。确认普通 SSH 的 stdin/stdout 不经编排器。

### P3：普通 SSH 无感 hook

向 `%USERPROFILE%\\.ssh\\config` 的现有全局块合并受管规则。hook 必须是异步、幂等、失败不阻塞 SSH；覆盖多设备、多窗口、多用户和并发竞态。

### P4：OAuth/浏览器可选能力

仅在需要 Codex 的产品 profile 中启用 1455 短生命周期 forwarder、Windows 桌面浏览器启动和回调清理；基础代理产品不依赖 Codex。

### P5：真实 Windows 回归

至少覆盖 Windows 11 普通用户、Windows Server 2022 用户会话和一个没有 OpenSSH Client 的初始环境；验证睡眠/唤醒、网络切换、防火墙策略、用户注销、服务重启和多个目标。

## 11. 验收标准

- 普通 `ssh user@host` 首次连接后，目标 `curl -x http://127.0.0.1:4227` 可访问本机代理；
- 多窗口不出现远端 4227 端口占用；
- 同设备多用户只建立一个代理隧道，owner 断开后 30 秒内由其他活动用户接管；
- 编排器退出或 hook 失败时，普通 SSH 仍能登录；
- Windows 睡眠/网络断开后自动恢复，且不会产生无限快速重连；
- 4227、1455 和 IPC 都只绑定 loopback/Named Pipe，不产生公网监听；
- 密码、私钥内容、OAuth state/token 不进入服务参数、任务 XML、日志或报告；
- 无管理员权限时，用户级安装仍可用；需要管理员权限的选项明确提示并可取消；
- `status` 能区分 `READY`、`DEGRADED`、`FAILED`、`PASSWORD_SESSION_ONLY` 和 `NOT_APPLICABLE`。

## 12. 最终建议

Windows 等效服务可行，且核心 SSH 反向转发并不需要复杂的 Windows 防火墙配置。真正的工程难点不是 `-R` 参数，而是替换 Linux 的 ControlMaster、systemd user unit、Unix socket、锁和桌面会话集成。

建议先做 Windows 专用的“用户登录任务 + Named Pipe + 独立 ssh.exe forwarder”版本，不引入 WSL，不把 WinSW 作为默认依赖，不修改系统级防火墙。这样可以最大限度复用当前端点级 owner/退避模型，同时保持普通 SSH 的原生输入路径和多设备、多窗口体验。

## Sources

1. Microsoft Learn — [OpenSSH for Windows overview](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-overview)
2. Microsoft Learn — [OpenSSH server configuration](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)
3. Microsoft Learn — [Key-based authentication in OpenSSH for Windows](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
4. Microsoft Learn — [Get started with OpenSSH Server for Windows](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
5. Microsoft Learn — [schtasks create](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create)
6. PowerShell/Win32-OpenSSH — [About Win32-OpenSSH and design details](https://github.com/PowerShell/Win32-OpenSSH/wiki/About-Win32-OpenSSH-and-Design-Details)
7. PowerShell/Win32-OpenSSH — [TTY/PTY support](https://github.com/PowerShell/Win32-OpenSSH/wiki/TTY-PTY-support-in-Windows-OpenSSH)
8. WinSW — [Windows Service Wrapper](https://github.com/winsw/winsw)
9. Microsoft VS Code Docs — [Remote-SSH port forwarding](https://github.com/microsoft/vscode-docs/blob/main/docs/remote/ssh.md#forwarding-a-port-creating-ssh-tunnel)
10. OpenSSH client manual — [ssh_config](https://manpages.debian.org/testing/openssh-client/ssh_config.5.en.html)
11. sshuttle — [Transparent proxy over SSH](https://github.com/sshuttle/sshuttle)
