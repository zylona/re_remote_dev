# 纯命令行使用 setup key 加入 NetBird

本教程适用于已经安装 NetBird 客户端、没有桌面浏览器的 Linux 服务器。目标是在控制面创建 peer 后，用远端终端完成认证，并让节点在重启后自动重新连接。

## 1. 在控制面创建 setup key

在 NetBird 管理控制台打开 **Settings → Setup Keys**，选择 **Create Setup Key**。也可以从 **Peers → Add Peer** 开始创建；不同控制台版本的页面名称可能略有差异。

只注册一台服务器时，建议：

- 给 key 设置可识别的名称，例如 `debian-221`。
- 类型选择 **One-off**，只允许这台机器注册一次。
- 设置较短的有效期，例如 24 小时。有效期限制 key 的注册时间，不会让已经加入的 peer 到期断开。
- 关闭 **Ephemeral peer**。临时 peer 离线一段时间后会被自动移除，不适合长期服务器。
- 按最小权限原则选择 **Auto-assign groups**。加入组网本身不等于允许访问所有设备，实际访问还由组和网络策略决定。
- 创建后立即复制 key，并像密码一样保管。

需要用同一个 key 注册多台机器时，才选择 **Reusable**；同时设置明确的使用次数上限。不要为了让单台机器长期在线而创建永不过期、无限次使用的 key。

## 2. 在远端终端登录

先确认服务可运行，并设置开机启动。

systemd 系统：

```bash
sudo systemctl enable --now netbird.service
sudo netbird status
```

如果状态显示 `NeedsLogin`，使用 setup key 注册 peer。项目在全新安装时已将 `https://netbird.zylona.site:443` 预置为默认管理端，因此登录命令不必重复指定 URL。推荐通过权限为 `0600` 的临时文件传 key，避免 key 出现在 shell history 或进程参数中：

```bash
sudo install -m 600 /dev/null /run/netbird-setup-key
read -rsp 'Setup key: ' NB_SETUP_KEY; echo
printf '%s' "$NB_SETUP_KEY" | sudo tee /run/netbird-setup-key >/dev/null
unset NB_SETUP_KEY
sudo netbird up --setup-key-file /run/netbird-setup-key
sudo rm -f /run/netbird-setup-key
```

如果目标机此前已启动过 NetBird，并缓存了 NetBird Cloud 或其他管理端地址，显式指定自建管理端：

```bash
sudo netbird up --management-url https://netbird.zylona.site:443 --setup-key-file /run/netbird-setup-key
```

`--management-url` 会让客户端连接到对应控制面；setup key 也必须由同一个控制面创建。官方默认管理端是 NetBird Cloud，因此 Cloud 创建的 key 与自建控制面的 key 不能互换。官方也支持直接用 `--setup-key` 参数传 key，但该方式可能让 key 出现在 shell history 或进程参数列表中，不建议用于日常操作：

```bash
sudo netbird up --setup-key '<SETUP_KEY>'
```

不要把真实 key 粘贴到共享终端记录、脚本、Inventory、Git 或工单中。不要把 setup key 配置为项目的通用 Ansible 变量。

Alpine/OpenRC：

```bash
sudo rc-update add netbird default
sudo rc-service netbird start
```

随后按上面的临时 key 文件步骤运行 `netbird up --setup-key-file /run/netbird-setup-key`。旧安装首次加入时，请加上 `--management-url https://netbird.zylona.site:443`。若当前 shell 已是 root，命令前不需要 `sudo`。

## 3. 确认 peer 已加入并会在重启后重连

```bash
sudo netbird status
sudo systemctl is-enabled netbird.service   # systemd
sudo systemctl is-active netbird.service    # systemd
ip addr show wt0
```

确认 `netbird status` 显示 Management/Signal 已连接，并在控制台 **Peers** 页面确认该节点出现、分配到了预期组。连接策略还需要允许该组访问目标资源。

systemd 服务已 enabled 后，机器重启时 daemon 会启动，并使用本机保存的 peer 身份重新连接。Alpine 上的 `rc-update add` 会让服务随默认 runlevel 启动。不要把 setup key 当作每次启动都需要的密码：key 用于首次注册，后续连接使用客户端保存的身份。

## 4. key 到期、撤销与节点生命周期

- setup key 到期或被撤销后，不能再用它注册新 peer；已经注册的 peer 不会因此断开。
- **Ephemeral peer** 决定 peer 是否会在离线后被自动清理；长期服务器应关闭此选项。
- 需要移除已加入的机器时，在控制台 **Peers** 页面删除该 peer。仅撤销 setup key 不会删除已经注册的 peer。
- 若机器重装或本地 NetBird 身份数据丢失，需要重新注册时，在控制台创建新 key，再重新运行 `netbird up --setup-key-file ...`。

本项目恢复流程只安装并启动客户端服务，不执行登录、不创建或保存 setup key。更多平台安装说明见[NetBird 客户端恢复能力](../capabilities/netbird.md)。

参考：[NetBird 官方 setup key 指南](https://docs.netbird.io/manage/peers/register-machines-using-setup-keys)、[Linux CLI 指南](https://docs.netbird.io/get-started/cli)、[default.json bootstrap 指南](https://docs.netbird.io/manage/peers/bootstrap-via-config-file)。
