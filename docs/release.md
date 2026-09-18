# 本地编排器发布

`remote-dev-orchestrator` 是可被其他项目复用的完整本地 SSH 集成制品。它包含用户级 socket 服务、`tssh` 转发入口、迁移/回滚脚本和标准库 Python 实现，不携带目标机恢复逻辑，也不要求安装本项目的 Ansible 依赖。

## 发布

公开 Release 只包含 Git 跟踪文件构建出的制品。真实 Inventory、`.local/` 凭据、SSH 配置和运行报告均不会进入源码包或 tarball；发布前应执行一次脱敏审计并确认 `git archive HEAD` 中没有密钥、密码和 Token。版本变更摘要维护在 [CHANGELOG.md](../CHANGELOG.md)。

发布由 `.github/workflows/release.yml` 完成。`master`/`dev` 的每次推送都会运行检查并生成短期 Actions artifact；维护者提交并推送符合 `vMAJOR.MINOR.PATCH` 的 tag 后，GitHub Actions 会先运行 inventory/syntax/lint/pytest 门禁，再构建带固定时间戳的 tar.gz 与 SHA256 文件，最后使用 GitHub CLI 创建 Release 并上传制品。workflow 使用仓库 `GITHUB_TOKEN` 的 `contents: write` 权限，无需保存个人 Token。

Actions artifact 仅用于 CI 任务间传递，公开仓库最长保留 90 天；长期依赖必须使用 GitHub Release asset。Release 制品不会按 Actions artifact 的保留周期自动删除，可通过稳定地址获取最新版：

```text
https://github.com/zylona/re_remote_dev/releases/latest/download/remote-dev-orchestrator-v<VERSION>-linux.tar.gz
```

生产环境应固定具体版本 Tag，并同时下载 `.sha256` 校验文件，不要依赖 `latest`。

发布前必须在目标 VM `192.168.122.196` 上完成多窗口 SSH、ControlMaster 自动恢复、4227/4228 转发、VS Code Remote-SSH 打开远程目录以及 Ctrl-C 清理；只看到 systemd `active` 不足以判定稳定。若 `NRestarts` 持续增加，应先修复隧道/主机密钥问题，不要打 tag。

```sh
git tag v0.1.14
git push origin v0.1.14
```

## 安装

从 Release 下载 tar.gz 和 `.sha256`，校验后解压并执行包内安装器：

```sh
sha256sum -c remote-dev-orchestrator-v0.1.14-linux.tar.gz.sha256
tar -xzf remote-dev-orchestrator-v0.1.14-linux.tar.gz
./remote-dev-orchestrator-v0.1.14-linux/install
```

安装器将版本放入 `~/.local/share/remote-dev-orchestrator/versions/`，先写入暂存目录，再原子更新 `current` 链接；旧版本目录保留用于回滚。安装时会迁移旧版全局 SSH hook 和旧版 `remote-dev-master-*` unit，不触碰无关 user unit。默认不会向 `~/.ssh/config` 写入新的 `LocalCommand` 或项目 forwarding 规则，普通 `ssh user@host` 和 VS Code Remote‑SSH 保持原生。需要 4227 代理转发时使用制品提供的 `tssh user@host`；它通过 `ssh -G` 解析当前 SSH 配置中的 `IdentityFile`，申请 endpoint lease 后启动或复用独立 forwarder。显式 `-i` 仍可覆盖自动解析结果。重复安装不会追加规则，也会迁移旧版外部 Include。升级中断时旧 `current` 仍可用；可执行包内 `rollback VERSION` 回滚到已安装版本。卸载执行包内 `uninstall`，仅移除受管 unit、区块和旧版受管快照。服务以当前用户运行，SSH 密钥、目标配置和密码不会进入制品。

回滚到已安装版本：

```sh
./remote-dev-orchestrator-v0.1.14-linux/rollback 0.1.10
```

安装后验证：

```sh
systemctl --user is-active remote-dev-orchestrator.socket
remote-dev-orchestrator-server --help # 仅检查入口；服务由 systemd 启动
tssh --help # 带 4227 转发的 SSH 入口
tssh user@host # 自动使用 ~/.ssh/config 中解析出的 IdentityFile
tssh -i ~/.ssh/id_ed25519 user@host # 显式指定密钥
```

首次使用本项目时，`./re-remote bootstrap` 仍负责目标机恢复和注册；已安装的独立服务可供其他项目直接复用同一 Unix socket 与 ControlMaster 生命周期。VS Code Remote-SSH 默认使用普通 `~/.ssh/config`；安装器不会生成或维护 `config-vscode`，也不会向普通配置注入 remote-dev hook、4227 或 1455 转发。
