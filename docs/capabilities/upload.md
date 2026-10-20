# `tssh put` 上传能力

`tssh put` 是与 `rget`/`rdo` 配套的低频文件上传入口。它只负责把本地文件放到
目标用户的约定目录，不改变远端项目配置，也不依赖远端安装额外的常驻 Agent。

## 使用

```bash
tssh put ./report.pdf user@host
tssh put ./a.txt ./b.txt user@host
tssh put -r ./docs user@host
tssh put './reports/*.pdf' user@host -i ~/.ssh/id_ed25519
```

文件会写入目标用户的：

```text
~/Uploads/remote-dev/<本地文件名>
```

目标目录由制品固定管理，避免每次重复输入远程路径。目标参数必须放在最后，格式为
`user@host` 或 `host`。多个普通文件直接写入目标目录；`-r/--recursive` 上传目录时保留
顶层目录名和其内部结构，例如 `-r ./docs` 会写入 `~/Uploads/remote-dev/docs/`。
本地 shell 未展开的引号通配符也由 `tssh` 展开；没有匹配项会立即失败。标准输入不支持。
同一批源文件映射到相同远程路径时会在连接前拒绝，避免静默覆盖。

## 传输与稳定性

- 每次 `put` 使用一次临时、按请求隔离的 SSH ControlMaster；批量源文件的目录创建、SFTP
  传输和进度查询复用同一条 SSH 连接，不会为每个文件或进度采样重新握手，也不会占用
  tssh 的 4227/4228 转发端口。
- 首次上传使用 SFTP 的普通写入；目标端存在同名未完成 `.tssh-part` 文件时，从已有字节
  位置续传。完成后通过远端原子 rename 替换正式文件，避免查看到半个文件。
- 命令前台显示已传字节、总大小、速度和 ETA，传输成功后才退出；失败会清理临时连接，
  保留 `.tssh-part` 以便下次继续。
- 续传依据临时文件大小，不是内容哈希校验；如果本地源文件发生变化，应重新上传并让
  旧分片被覆盖或清理。

## 认证与边界

认证沿用普通 SSH 配置、`ssh-agent` 或显式 `-i` 私钥，不保存密码。密码登录仍可使用
普通 `ssh`，但不适合无人值守恢复。目标 SSH 必须允许 SFTP 和用户在 `~/Uploads` 下
创建目录；网络代理不是上传数据通道，上传走目标 SSH 连接本身。
