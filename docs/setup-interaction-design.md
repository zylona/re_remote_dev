# setup 用户与权限边界

## 项目负责什么

`setup` 安装或刷新本机 tssh 服务，然后使用一组目标凭据运行固定的 Ansible 恢复流程。交互只收集：

1. 目标 IP 或主机名；
2. SSH 端口，默认 `22`；
3. 目标用户名；该用户必须已经存在、有 sudo 权限，并同时作为 SSH 登录用户和最终配置用户；
4. SSH 认证方式：密钥或密码。密钥路径可留空，以复用本机 SSH 配置、默认密钥或 ssh-agent；
5. SSH 密码（仅密码认证时）；
6. Ansible 在执行时通过 TTY 交互询问 sudo 提权密码。

项目不创建/删除目标用户、不设置或重置用户密码、不授予/修改 sudo 权限、不把公钥复制到目标机，也不接受 root 登录后再切换用户的流程。用户尚未准备好目标账号时，运行 `re-remote --help` 阅读手动准备步骤。

## 执行方式

- 生成权限为 `0600` 的短期 Inventory，SSH 用户和 `remote_dev.target_user` 使用同一个用户名。
- SSH 密码通过权限为 `0600` 的短期 Ansible extra-vars 文件传递，任务完成后删除；sudo 密码只由 Ansible `--ask-become-pass` 从 TTY 读取。两者都不放进 Inventory、命令行参数或运行日志。
- SSH 密钥路径留空时保留用户的 `~/.ssh/config` 和 ssh-agent；显式路径或密码认证使用隔离的 SSH 配置，避免继承互相冲突的认证规则。
- 临时代理隧道和 Ansible 连接使用同一目标、用户名和 SSH 端口。
- Ansible 只安装/配置远程开发环境，并在需要时通过已具备的 sudo 权限执行系统级任务。

## 手动用户准备

顶层 help 只显示说明，不连接远程设备：

```bash
./re-remote --help
```

准备完成后，先确认能以该用户 SSH 登录，并通过 `sudo -v` 验证 sudo 权限，再运行 `./re-remote setup`。
