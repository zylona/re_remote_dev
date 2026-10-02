# 本地编排器发布

`tssh` 是可被其他项目复用的完整本地 SSH 集成制品。它包含 `tssh.service`/`tssh.socket` 用户级服务、`tssh` 转发入口、迁移/回滚脚本和标准库 Python 实现，不携带目标机恢复逻辑，也不要求安装本项目的 Ansible 依赖。

## 发布

公开 Release 只包含 Git 跟踪文件构建出的制品。真实 Inventory、`.local/` 凭据、SSH 配置和运行报告均不会进入源码包或 tarball；发布前应执行一次脱敏审计并确认 `git archive HEAD` 中没有密钥、密码和 Token。版本变更摘要维护在 [CHANGELOG.md](../CHANGELOG.md)。

发布由 `.github/workflows/release.yml` 完成。`master`/`dev` 的每次推送都会运行检查并生成短期 Actions artifact；维护者提交并推送符合 `vMAJOR.MINOR.PATCH` 的 tag 后，GitHub Actions 会先运行 inventory/syntax/lint/pytest 门禁，再构建带固定时间戳的 tar.gz 与 SHA256 文件，最后使用 GitHub CLI 创建 Release 并上传制品。workflow 使用仓库 `GITHUB_TOKEN` 的 `contents: write` 权限，无需保存个人 Token。

Actions artifact 仅用于 CI 任务间传递，公开仓库最长保留 90 天；长期依赖必须使用 GitHub Release asset。Release 制品不会按 Actions artifact 的保留周期自动删除，可通过稳定地址获取最新版：

```text
https://github.com/zylona/re_remote_dev/releases/latest/download/remote-dev-orchestrator-v<VERSION>-linux.tar.gz
```

生产环境应固定具体版本 Tag，并同时下载 `.sha256` 校验文件，不要依赖 `latest`。

发布前必须在隔离的 Omarchy 控制端 VM 上完成多窗口 SSH、ControlMaster 自动恢复、4227/4228 转发、VS Code Remote-SSH 打开远程目录以及 Ctrl-C 清理；只看到 systemd `active` 不足以判定稳定。若 `NRestarts` 持续增加，应先修复隧道/主机密钥问题，不要打 tag。

```sh
git tag v0.1.20
git push origin v0.1.20
```

## 安装

从 Release 下载 tar.gz 和 `.sha256`，校验后解压并执行包内安装器：

```sh
sha256sum -c remote-dev-orchestrator-v0.1.20-linux.tar.gz.sha256
tar -xzf remote-dev-orchestrator-v0.1.20-linux.tar.gz
./remote-dev-orchestrator-v0.1.20-linux/install
```

安装器将版本放入 `~/.local/share/tssh/versions/`，先写入暂存目录，再原子更新 `current` 链接；旧版 `~/.local/share/remote-dev-orchestrator/` 会在首次升级时迁移到新路径。升级成功后默认删除旧的受管版本目录、旧 unit 和旧入口，避免旧实现被误用；如需保留本地回滚副本，安装前设置 `TSSH_KEEP_OLD_VERSIONS=1`。systemd user 配置统一写入 `~/.config/systemd/user/tssh.service` 和 `tssh.socket`，运行时 socket 为 `$XDG_RUNTIME_DIR/tssh/orchestrator.sock`。旧版全局 SSH hook 和旧版 `remote-dev-master-*` unit 会被迁移清理，不触碰无关 user unit。默认不会向 `~/.ssh/config` 写入新的 `LocalCommand` 或项目 forwarding 规则，普通 `ssh user@host` 和 VS Code Remote‑SSH 保持原生。需要 4227 代理转发时使用制品提供的 `tssh user@host`；它通过 `ssh -G` 解析当前 SSH 配置中的 `IdentityFile`，申请 endpoint lease 后启动或复用独立的临时 forwarder。forwarder 不加入 `default.target`，没有任何 tssh lease 时不会保留；最后一个 lease 释放后会停止并删除。显式 `-i` 仍可覆盖自动解析结果。重复安装不会追加规则。升级中断时旧 `current` 仍可用；可执行包内 `rollback VERSION` 回滚到已安装版本（仅在保留旧版本时可用）。卸载执行包内 `uninstall`，仅移除受管 unit、区块和版本目录。服务以当前用户运行，SSH 密钥、目标配置和密码不会进入制品。

回滚到已安装版本：

```sh
./remote-dev-orchestrator-v0.1.17-linux/rollback 0.1.10
```

安装后验证：

```sh
systemctl --user is-active tssh.socket
tssh-server --help # 仅检查入口；服务由 systemd 启动
tssh --help # 带 4227 转发的 SSH 入口
tssh user@host # 自动使用 ~/.ssh/config 中解析出的 IdentityFile
tssh -i ~/.ssh/id_ed25519 user@host # 显式指定密钥
tssh list # 查看所有活动 endpoint
tssh put ./report.pdf user@host # 上传到目标 ~/Uploads/remote-dev/
tssh persist user@host # 后台持久保持代理，不占用终端
tssh stop user@host # 关闭指定持久代理
tssh cleanup # 清理全部会话级代理
```

`tssh list` 会展示每个 endpoint 的主机和 SSH 端口，并逐行列出实际转发：远端 `4227` 映射到
本机 `4227`（HTTP 代理），远端 `4228` 映射到本机 `4230`（Codex OAuth/事件通道）。状态表按
主机分组，便于同时检查多个设备；`READY` 表示 endpoint 的转发单元已成功建立。

`persist` 为指定 endpoint 创建独立的 systemd user unit，不需要保持一个空的 SSH 窗口；
`stop` 只关闭该设备的持久代理。普通 `tssh user@host` 仍然是会话级生命周期。
`persist` 只建立远端 `4227` HTTP 代理；`4228→4230` 是 Codex 回调通道，仅由普通交互式
`tssh user@host` 会话按需启用。

持久 unit 只在当前开机周期启动，不再 enable 到 `default.target`；关机时由 systemd 停止，
下次开机必须重新执行 `tssh persist`。启动前会清理本机失效 unit 并探测远端 4227：健康监听
直接复用，疑似旧连接只报告 `REMOTE_PORT_BUSY`，不会无确认地杀掉未知 SSH 会话。

`tssh put` 使用每次请求独立的临时 SSH ControlMaster，目录创建、SFTP 上传和进度查询均
复用该连接，完成或失败后自动关闭。目标文件统一写入 `~/Uploads/remote-dev/`；存在匹配
的 `.tssh-part` 时按已有大小续传，成功后原子替换正式文件。上传不使用 4227/4228，也不
要求远端部署常驻 Agent。它依赖 SSH 配置、ssh-agent 或显式私钥，不保存密码；详细使用边界
见 [`docs/capabilities/upload.md`](capabilities/upload.md)。

普通会话默认每 1 秒续租；异常断开时，lease 最多保留约 3 秒以容忍瞬时网络抖动；需要立即停止全部会话级
转发时使用 `tssh cleanup`。`persist` 创建的持久代理不受会话退出影响，必须使用对应的
`tssh stop user@host` 显式关闭。

时序可以在 `~/.config/tssh/config` 中调整：

```ini
[lease]
heartbeat_interval = 1
lease_ttl = 3
```

客户端和 systemd user 编排器读取同一文件；文件只包含 heartbeat/TTL，不含任何认证信息。

首次使用本项目时，`./re-remote bootstrap` 仍负责目标机恢复和注册；已安装的独立服务可供其他项目直接复用同一 Unix socket 与 ControlMaster 生命周期。VS Code Remote-SSH 默认使用普通 `~/.ssh/config`；安装器不会生成或维护 `config-vscode`，也不会向普通配置注入 remote-dev hook、4227 或 1455 转发。
