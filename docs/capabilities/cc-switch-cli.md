# CC Switch TUI（远端多 Codex 账号）

> **当前限制（重要）**：本项目当前集成的 CC Switch CLI/TUI 还不能满足 GUI
> 同等的原生 Codex 多账号快速切换需求。它可以托管多个账号、登录、查看状态和查询用量，
> 但 `Codex → OpenAI Official` 目前不能分别绑定账号 A/B；该能力暂不作为本项目的已完成能力。

远程恢复会把固定版本的 [cc-switch-cli](https://github.com/SaladDay/cc-switch-cli)
安装到目标用户的 `~/.local/bin/cc-switch`。项目使用官方 CLI 制品中的终端 TUI，不是
项目自定义的账号管理器，也不会复制本机 GUI 的数据库、浏览器 Cookie 或 `~/.codex/auth.json`。

首次登录、账号切换和 Codex 使用都应在 `tssh user@host` 会话中完成，这样 OAuth 和外网
访问可以复用本项目的代理转发。

## 启动 TUI

直接执行：

```bash
cc-switch
```

进入终端界面后，使用方向键、Enter 和界面提示操作。重点进入 Codex 相关页面：

```text
Accounts / Authentication   管理登录账号
Codex Providers              管理 Codex 配置
Usage / Quota                查看用量和限额（版本支持时）
```

退出 TUI 通常使用 `q` 或 `Ctrl+C`。

## 添加两个 Codex 账号

### 添加账号 A

1. 执行 `cc-switch`；
2. 进入 `Accounts` 或 `Authentication`；
3. 选择 `Login` / `Add account`；
4. 按提示打开设备登录页面；
5. 在本机浏览器完成账号 A 登录；
6. 返回 TUI，确认账号状态为 `Ready`。

### 添加账号 B

在同一个账号页面再次选择 `Login` / `Add account`，重复登录流程即可。不要删除账号 A，
两个账号会同时保存在当前远端用户的 CC Switch 数据库中。

命令行只读检查账号状态可以使用：

```bash
cc-switch auth list
cc-switch auth status
```

账号 ID 用于区分多个账号；即使两个账号显示相同邮箱，也应以 account ID 为准。

## 当前账号使用边界

TUI 中的 `Set default` / `Use account` 只设置 CC Switch 的托管账号默认值。当前发布版
的 `Codex → OpenAI Official` 页面没有“绑定托管账号”字段，因此不能像 GUI 预期那样把
账号 A、B 分别绑定成两个原生 Codex 供应商；直接运行 `codex` 仍读取目标用户自己的
`~/.codex` 登录状态。

托管账号目前可用于账号状态、用量/配额查询，以及 CC Switch 支持托管认证的代理供应商。
如果需要使用原生 Codex 的某个账号，仍需退出当前会话并通过官方 Codex 登录流程重新登录：

```bash
codex logout
codex
```

`tcodex` 不改变这一账号边界；它只负责项目的 OAuth 回调转发和本地浏览器打开。

## 查看当前配置和限额

TUI 中可以查看当前 Codex provider、账号状态、用量和限额。CLI 也提供只读检查：

```bash
cc-switch auth status
cc-switch --app codex provider list
cc-switch --app codex provider current
cc-switch --app codex provider quota <provider-id>
```

部分官方 Codex provider 可能返回 `Quota: not available`。这表示当前上游没有向 CLI
提供可查询的额度接口，不代表登录或账号切换失败。

## 保留会话记录

如果使用 TUI 的单次启动功能，默认可能使用临时 `CODEX_HOME`，看不到原来的会话记录。
需要隔离账号但保留 Codex 历史时，使用：

```bash
cc-switch start codex default --shared-sessions --
```

`--shared-sessions` 是官方 CLI 提供的共享历史模式；它保留 Codex 历史，同时隔离不同
provider 的认证配置。不同 provider 可以在不同终端并行运行，但同一个 provider 不应同时
启动多个共享会话。

## 官方命令与项目入口的边界

- `codex`：官方 Codex CLI，项目不修改其二进制；
- `cc-switch`：官方 CC Switch CLI 的 TUI 和命令行入口；
- `tcodex`：项目提供的 OAuth 回调入口。它动态调用官方 `codex`，捕获首次登录 URL，
  请求本地编排器启动 1455 回调转发并打开本机浏览器；它不接管 CC Switch 的账号管理。

项目不会把账号令牌写入 Inventory、命令行参数或日志，也不会自动复制本机 GUI 的账号数据。
如果账号切换后仍显示旧账号，优先在 TUI 中确认当前账号和 Codex provider，再重启 Codex
app-server 和客户端。
