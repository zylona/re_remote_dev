# NetBird 客户端恢复

恢复流程在远端安装 NetBird CLI，并启动客户端系统服务。全新安装会在首次启动前将自建管理端 `https://netbird.zylona.site:443` 写入 NetBird 系统默认 profile，之后 `netbird up` 默认使用该管理端。已有 NetBird CLI、profile 或旧版配置的机器会保留当前配置。恢复流程不会执行登录或加入网络操作。

## 支持的平台

| 包管理器 | 安装方式 | 服务管理器 |
| --- | --- | --- |
| apt | NetBird 官方 Debian 仓库及签名密钥 | systemd |
| dnf / yum | NetBird 官方 RPM 仓库，启用 RPM/GPG 校验 | systemd |
| zypper | NetBird 官方 RPM 仓库 | systemd |
| apk | Alpine Community 的 `netbird` 与 `netbird-openrc` 包 | OpenRC |
| pacman | NetBird 官方固定版本二进制；校验 SHA256 | systemd |

二进制版本、下载地址和 SHA256 清单集中在 `roles/netbird/defaults/main.yml`。目前 pacman 安装支持 x86_64、aarch64 和 32 位 x86；仓库包按发行版自身支持的架构安装。服务管理支持 apt/RPM/zypper/pacman 系的 systemd，以及 Alpine 的 OpenRC。

安装仓库包使用发行版包管理器，后续更新也由该包管理器负责。pacman 平台使用 NetBird 官方 release 二进制，升级版本时需要在项目中同步更新版本及校验值。

## 登录边界

本项目只安装并启动 NetBird 服务，不运行登录命令，不申请登录码、不保存账号或 setup key，也不配置路由、DNS、出口节点或防火墙策略。全新安装预置的管理 URL 可由正常 NetBird CLI 参数覆盖；已有配置不会被强制迁移。

需要加入网络时，用户在目标设备上自行运行：

```bash
sudo netbird up --setup-key-file /run/netbird-setup-key
```

纯命令行下使用控制面 setup key 的创建选项、登录、开机重连与节点生命周期说明见[setup key 使用教程](../guides/netbird-setup-key.md)。

查看客户端与登录状态：

```bash
sudo netbird status
systemctl status netbird     # systemd
rc-service netbird status    # Alpine/OpenRC
```

独立只读验收检查 `netbird version` 和服务是否运行，不要求客户端已经登录。

## 代理与离线行为

仓库元数据、签名密钥和官方二进制下载会继承本轮临时 SSH HTTP 代理。代理不写入 NetBird 的长期配置。

`tools_offline: true` 时不会添加仓库或下载 NetBird；如果客户端未预装，恢复会给出明确错误。已安装的客户端仍会启动并由 verify 检查。

实施参考：[NetBird 官方 Linux 安装说明](https://docs.netbird.io/get-started/install/linux)及[官方 v0.82.0 release](https://github.com/netbirdio/netbird/releases/tag/v0.82.0)。
