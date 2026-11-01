# CodexSwitch：远端 Codex 多账号

恢复流程在官方 Codex CLI 与 mise 管理的 Node.js 可用后，安装 npm 包
`@carjms/codexswitch`，并提供 `codexswitch` 和短命令 `cxs`。当前固定到已发布的
`0.7.0`，安装到用户目录 `~/.local/share/remote-dev/codexswitch`；命令入口位于
`~/.local/bin`。独立只读 verify 会检查两个入口是否能启动。

`tcodex` 提供面向日常使用的统一入口；底层命令仍由官方 CodexSwitch 执行。
运行 `tcodex help` 可查看账号管理命令摘要。

## 登录与账号列表

用 `tcodex login <name>` 登录并保存账号。它调用 CodexSwitch 的官方 `login`，并临时指定
`tcodex` 作为 Codex 登录程序，因此仍能通过本机浏览器完成回调；认证数据仍由 CodexSwitch
保存。重复执行可添加多个账号。用 `tcodex list` 查看账号：

```sh
tcodex login personal
tcodex login work
tcodex list
tcodex remove old-account
```

兼容入口 `tcodex cxs login <name>` 仍可使用。直接运行 `cxs login <name>` 是 CodexSwitch
原生行为，不经过本机浏览器回调。`tcodex` 尊重 CodexSwitch 登录时传入的临时 `CODEX_HOME`，
不会把账号登录数据写入默认 `~/.codex/auth.json`。

如果已有官方 Codex 登录状态需要保存到 CodexSwitch，运行 `tcodex import [name]`。
`tcodex remove <name>` 删除 CodexSwitch 保存的指定账号；它不会重启 daemon。删除前请确认账号名，
并留意 CodexSwitch 的活动认证文件可能由它自己的删除逻辑管理。

## 切换账号

用 `tcodex use <name>` 将账号设为官方 Codex 当前账号。切换成功后，`tcodex` 会重启当前用户
及当前 `CODEX_HOME` 下的官方 Codex daemon，使新开的 Codex 会话读取切换后的认证。daemon 重启
失败时会明确提示；账号切换本身不会回滚。

```sh
tcodex use work
codex login status
codex
```

不要在仍有重要任务运行的 Codex 窗口中切换账号；daemon 重启会中断该 daemon 管理的会话。

## 按账号启动隔离会话

推荐用 `cxs run` 启动独立账号会话：

```sh
cxs run personal
cxs run work
```

它会为该账号选择隔离的 `CODEX_HOME`，并保留 Codex 配置和会话历史的共享行为。不同终端可
分别启动不同账号，无需替换全局 `~/.codex/auth.json`。启动前可用 `cxs list` 查看已保存的
账号名称。

多账号并行或避免切换全局账号影响其他 Codex 窗口时，使用 `cxs run <name>`；`tcodex use`
管理的是全局默认认证，不会更改 CodexSwitch 为 `cxs run` 创建的隔离账号数据。

## 认证数据

CodexSwitch 会在当前远端用户目录保存账号认证信息（默认位于 `~/.codex-switch/`）。这些数据
包含 OAuth 凭据，应像 `~/.codex/auth.json` 一样保护，不要提交到 Git 或复制到共享目录。
