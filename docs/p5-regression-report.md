# P5 真实 VM 回归记录

状态：VM 真实 tssh 回归通过；端口冲突已验证为可观测 `DEGRADED`，普通 SSH 不受阻断。

## 2026-10-01 发布前回归补充

- 本地门禁：`96 passed`；Ansible syntax-check、`ansible-lint`（0 failure/0 warning）和
  `git diff --check` 均通过。
- Debian VM（`192.168.122.103`）：普通 SSH、tssh、双窗口共享 endpoint、持久代理 unit
  均通过；持久代理能够提供远端 `127.0.0.1:4227` 访问。
- openEuler 实机（`10.40.6.145`）：普通 SSH、tssh、持久代理和远端 4227 HTTP 请求通过；
  Codex/VS Code Remote-SSH 当前可正常使用。
- Omarchy VM：本轮按“本机/控制端”使用，仅安装并验证本地 `tssh` user service；它不是远端
  开发环境恢复目标，不应执行完整 `remote-dev setup`。Omarchy 默认没有本地 HTTP 代理
  `127.0.0.1:4227`，因此测试时临时使用了 loopback bridge 接到宿主机代理；bridge 只属于
  测试夹具，不是产品常驻组件。生产使用前，控制端必须自行提供本地 4227 HTTP 代理，否则
  `tssh` 可以建立 SSH 转发，但目标机上的 `curl -x 127.0.0.1:4227` 必然失败。
  早期误把 Omarchy 当作远端执行过一次完整恢复；这不是当前拓扑要求，后续应从干净快照重新
  验证控制端，避免把远端恢复结果当成 tssh 安装结果。
- 发现并修复 0.1.17 持久代理制品缺陷：systemd unit 不再直接执行 `lib/remote_dev/tssh.py`，
  改为调用 release wrapper 或带 `PYTHONPATH` 的模块入口，避免 `relative import` 失败。
- Clash/Mihomo TUN 排除和已有连接重建要求已补充到 README；仅 `DIRECT` 规则不能替代
  `route-exclude-address`。
- Debian VM 首次恢复曾暴露 Neovim 新机缺陷：配置快照的嵌套目录未预创建；已补充
  `lua/config`、`lua/plugins` 和 `plugin/after` 目录任务，并重新执行恢复成功。
- Debian VM 第二次恢复达到 `changed=0`；`curl`、`zsh`、`tmux`、Neovim、Zellij、fzf、zoxide、
  mise、Codex、`rget`、`rdo` 和 bubblewrap 均可执行，远端通过持久 4227 转发访问 Google 返回
  HTTP 200。
- 双 VM 拓扑回归已按正确角色完成：Omarchy 仅部署本地 tssh user 服务，使用测试桥接把本机
  `127.0.0.1:4227` 接到宿主机代理；Debian 作为远端开发目标。Omarchy 上的 tssh 持久代理、
  Debian 远端 4227 请求（HTTP 200）和两个并发窗口均通过。
- 故障注入：停止 Omarchy 的 4227 上游后，Debian 的代理请求失败；恢复上游后请求恢复 200，
  SSH 会话本身未被强制关闭。当前 `tssh --list` 的 `READY` 表示 SSH 转发进程存活，不代表上游
  HTTP 代理一定可用；这是后续数据面健康检查增强项。

## 测试对象

| 别名 | 系统 | 验证内容 |
| --- | --- | --- |
| `vm-debian` | Debian | helper、Nvim 配置、reverse-forward、SFTP 下载 |
| `vm-omarchy` | Omarchy（控制端） | tssh user service、到 Debian 的 forward、并发/故障注入 |

设备地址不写入仓库文档，真实地址只通过本地临时 Inventory 使用。

## 已完成

- Debian VM 可通过 SSH 公钥登录，且完成了 `rget`、`rdo`、Neovim 和远程 helper 的恢复；
- Omarchy VM 仅安装本地 `tssh` user service，没有把远端恢复角色错误地算入本项验收；
- 测试夹具 loopback bridge 可接受远端请求；生产环境不由 tssh 自动创建上游代理；
- Omarchy→Debian 使用独立动态 reverse-forward；
- Debian 远端 `rget /etc/hosts` 成功提交并下载；
- 同一文件第二次请求命中 `CACHED`，未再次执行 SFTP 下载；
- Debian endpoint 缓存目录与控制端相互隔离；
- 普通 SSH command builder 在未启用预览时保持原生参数；
- reverse-forward 建立失败时回退为普通 SSH，不阻断主会话；
- 本地自动化回归：`96 passed`；
- Ansible site syntax-check 通过。
- 本机 tssh 已刷新到当前 0.1.17 实现，systemd user service 为 `active`；
- 两台 VM 均完成真实交互式 PTY：`tssh ...` 后执行 `rget /etc/hosts`，下载文件和
  sidecar 均生成；重复请求命中缓存；
- 两个同设备窗口同时连接时 endpoint 只保留一个共享代理，两个会话均正常退出；
- Omarchy→Debian 的 4227/4228 reverse-forward 均为 `READY`，无本地端口冲突；
- 远端 4227 被临时进程占用时，SSH 仍可登录，`tssh --list` 正确显示 endpoint 和两个
  forward 为 `DEGRADED`，释放占用后会话清理正常；
- 与 VS Code Remote-SSH 同时启动同一 Debian VM，VS Code CLI 返回 0，tssh endpoint
  保持 `READY`，两者没有共享 ControlMaster 或互相注入参数。

## 2026-10-01 正确双 VM 拓扑重装与自动化兼容回归

- 删除并重新安装 Omarchy 控制端的当前 tssh 制品；安装后 `tssh.service` 和
  `tssh.socket` 均为 `active`，旧版 unit 已清理。
- Omarchy 没有原生 4227 代理；测试临时启动 loopback bridge（Omarchy
  `127.0.0.1:4227` → 虚拟化宿主机代理），验证完成后已停止 bridge，不留下常驻代理夹具。
- Debian 作为唯一远端目标，持久代理请求返回 HTTP 302；停止 bridge 后远端请求失败，
  bridge 恢复后再次返回 HTTP 302，随后持久 unit 和 lease 均清理干净。
- Codex 回调自动化：通过真实 tssh 会话发送 `CODEX_START`、`CODEX_OAUTH_URL` 和
  `CODEX_EXIT` 协议事件；当测试私钥位于 `~/.ssh` 时，OAuth `1455→1455` forward 为
  `READY`，远端临时 HTTP 服务可经本机 `127.0.0.1:1455` 返回 HTTP 200，`CODEX_EXIT`
  后回调 forward 被回收。测试私钥放在 `/tmp` 时曾因 `PrivateTmp=yes` 不可见，已记录为
  测试夹具限制，生产私钥必须位于用户 SSH 目录或 agent 中。
- 同设备双窗口和不同远端用户（临时 `zz` 账号）并发连接均成功；endpoint 只创建一个
  共享 4227/4228 forward，owner/session 状态可回收。
- VS Code Remote-SSH 自动化兼容检查：普通 `ssh -G` 配置不含 `LocalCommand`、强制
  `ControlMaster` 或 tssh 专用参数；两个独立动态 SOCKS/远端命令探针均返回 0；与 tssh
  会话并发时远端命令返回成功，tssh lease 保持 `READY`。这覆盖 SSH 层兼容性，但不等同
  于完整 GUI 窗口人工验收。

## 尚未完成的增强项

- 多用户（不同远端账号）共享端点的 owner 接管验证；
- bridge 重启、SFTP 中断、磁盘不足故障注入；
- 本地查看器缺失时的桌面与无桌面回退验证；
- GUI 中完整打开目录并等待 VS Code Server 就绪的人工验证。

Codex 官方安装器在 Debian VM 的完整恢复中出现长时间无输出，因此本次 P5
使用独立的 helper/Nvim 角色部署完成预览链路验证，未将该安装器等待误判为
预览功能失败。

## 2026-11-24 本机 Incus 平台迁移与 tssh Rich CLI 验收

- 测试平台从已关机的 Thunderobot Incus 实例迁移到本机 Incus；未启动或修改旧实例。
- 构建并安装本地测试制品 `tssh-v0.1.34-linux-x86_64.tar.gz`，SHA256：
  `572527bfd983091cf1cb1299b026cc284c0e50f0d99d6cc0ba25b06cf2b1a5a1`。
- Debian 13 控制端 VM `tssh-control-20261124` 安装成功。制品自带 CPython 3.14.7、
  Rich 15.0.0、ansible-core 2.21.3；`tssh --version`、`--help`、`--list` 可用，
  用户级 `tssh.socket` / `tssh.service` 为 active。help 的 Rich 分组面板和 list 状态表
  均正常，非 TTY 模式不会输出 ANSI 控制码。
- 全量仓库门禁：pytest 177 passed；Ansible inventory 与 site syntax-check 通过；
  ansible-lint 0 failure / 0 warning；`git diff --check` 通过。
- 本机 Incus 创建 Debian 13 目标 VM `tssh-target-vm-20261124`。最初镜像没有
  `openssh-server`；HTTP 80 不通，DNS 返回 Clash fake-IP。目标 VM 临时切换到 Debian
  官方 HTTPS 源并映射到可达 CDN IPv4 后，`apt-get update` 成功，安装了 `openssh-server`
  和 sudo。没有修改本机 Clash 或 SSH 配置。
- 真实 SSH 公钥登录、反向 4227/4228 转发通过。目标端经 4227 请求控制 VM loopback 上的
  HTTP 测试代理，返回预期响应；`tssh --list` 同时显示 proxy/event `READY`。
- 普通 `ssh` 连接成功后，`tssh --list` 仍显示 `IDLE`；SSH 有效配置为
  `permitlocalcommand no`，没有注入 `LocalCommand`。
- 两个并发 tssh 会话共享同一个 endpoint，`SESSIONS=2`；另一连接通过该共享隧道访问
  代理成功。两个会话结束后 lease 清零，目标 VM 上 4227/4228 listener 均消失。
- `tssh --put` 单文件、递归目录，以及 shell 展开的多文件/通配符上传通过，远端文件内容
  与目录结构已核对。
- Rich/tssh 相关自动化回归：`uv run --locked pytest tests/test_tssh.py tests/test_tssh_user_config.py -q`，19 passed；`git diff --check` 通过。
- 后续已用 host→control VM 反向转发连接真实本机 Clash，完成目标 VM 的完整 `tssh --setup`
  和只读 verify；Codex、CodexSwitch、Neovim、NetBird 和 12 个 Skills 均通过。完整恢复发现并
  修复 mise 下载摘要、zoxide gzip API 响应、Codex 重复迁移 changed 与 Lazy 锁文件漂移。
  最终第二次 apply recap：`ok=191 changed=0 unreachable=0 failed=0 skipped=107`。
- 最终交互回归中，通过 `tssh` 会话访问目标端 4227 并请求 `https://github.com` 得到 HTTP 200；
  普通 `ssh` 到同一 VM 不产生代理 lease；退出后的 `tssh --list` 为 IDLE。目标 VM 实测 Codex CLI
  0.171.0、Neovim 0.11.3、mise 2026.11.8、23 个 Treesitter parser 和 12 个 Skills。
- VS Code 桌面 GUI 与浏览器 OAuth 回调未在本轮执行；本轮通过的是制品安装、完整远端恢复、
  只读验收和幂等性，不将 GUI/登录链路标为已验收。
- 最终修复后重新运行本机全量门禁：pytest 179 passed，Ansible syntax-check 通过，
  ansible-lint 0 failure / 0 warning，`git diff --check` 通过。此前的 177 passed 是修复前结果。
- 诊断容器已删除；当前本机保留两台 Debian VM。测试 SSH 私钥保存在被 Git 忽略的
  `.local/incus-test/`，仅用于复测。
