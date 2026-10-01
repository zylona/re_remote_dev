# rget / rdo 远程文件下载与本地预览分阶段落地计划

状态：P0、P1、P2、P3、P4 已落地；P5 已完成两台 VM 的真实 tssh、并发、冲突和 VS Code 并行回归；P6 待整理制品与文档

## 1. 目标与边界

本能力服务于通过 `tssh` 连接的远程命令行开发环境：用户在远程 Nvim 文件树中选中文件并按 `<S-o>`，即可把远程 PDF、Markdown 或图片传回本机，并使用本地原生应用查看。

最终用户入口：

```bash
# 远端会话中，只下载
rget /workspace/docs/report.pdf
rget docs/report.pdf

# 远端会话中，下载并调用本地应用
rdo /workspace/docs/report.pdf
rdo docs/design.md
```

本能力必须满足：

- `rget` 是底层下载命令，`rdo` 是下载后本地预览命令；两者均不负责 SSH 登录、代理或密码保存。
- 用户不重复输入 IP、端口、用户名和私钥；目标来自当前 `tssh` 会话上下文。
- 相对路径基于远程 `$PWD` 解析为规范化绝对路径。
- 默认使用本地 SFTP 读取，不要求远端安装 tssh、GUI 或常驻 Agent。
- 同一 endpoint + 远程绝对路径使用稳定本地目标，未变化时不重复传输。
- 首次或变化文件由本次命令同步等待下载完成，并实时显示进度；不会启动额外的后台命令，
  也不会阻塞其它 SSH 窗口。
- PDF 始终以原始 PDF 交给本地查看器，不做单页截图或降级渲染。
- 普通 `ssh` 和 VS Code Remote-SSH 不注入该能力，也不受其影响。

## 2. 总体架构

```text
远程 Nvim / 远端 shell
        │
        ├─ rget / rdo：解析路径，提交 endpoint + path + operation
        │
        ▼
远端会话 helper（无 tssh、无 GUI、短命请求）
        │  session nonce + 动态预览反向通道
        ▼
本地 tssh bridge
        ├─ 校验 endpoint、用户、nonce 和路径
        ├─ 复用 ControlMaster / ssh-agent
        ├─ SFTP stat → 缓存判断 → 流式下载进度
        ├─ `.part` → 校验 → 原子 rename
        └─ rdo：下载完成后启动 Typora/PDF/图片查看器
```

现有端口职责保持隔离：4227 继续作为 HTTP 代理，4228 继续作为 Codex 事件回调；预览桥使用独立的会话级动态端口，不修改全局 SSH 配置。

## 3. 阶段总览

| 阶段 | 主题 | 主要产出 | 完成标志 |
| --- | --- | --- | --- |
| P0 | 契约与状态模型 | 命令、路径、缓存、错误和安全契约 | 已完成：契约模块与单元测试 |
| P1 | 本地 bridge 与会话通道 | tssh 会话级预览请求通道 | 已完成：loopback bridge、nonce 校验和 lease 生命周期 |
| P2 | 远端 `rget`/`rdo` helper | 远端短命令和上下文注入 | 已完成：helper、动态 reverse-forward、上下文清理 |
| P3 | SFTP 下载、去重与异步队列 | 快速、幂等、可取消的下载核心 | 已完成：stat、异步进度、SFTP 断点续传、原子替换、并发合并 |
| P4 | Nvim `<S-o>` 和本地应用 | 文件树一键预览 | 已完成基础版：查看器选择、回退和 Neo-tree 映射 |
| P5 | 多设备/多用户/故障回归 | VM 与真实设备验证 | 两台 VM 的真实链路、同设备多窗口、多设备、端口冲突和 VS Code 并行已通过；多用户与深度故障注入待补 |
| P6 | 制品、文档和发布 | 本地安装包/远端恢复集成 | 可升级、可卸载、可发布 |

## 4. P0：契约、路径和缓存模型

### 实施内容

1. 固定命令接口：
   - `rget PATH`
   - `rdo PATH`
   - `--verify`、`--durable`、`--clean` 和应用选择参数在对应阶段实现后再公开。
2. 固定远端路径解析规则：绝对路径直接使用；相对路径基于远程 `$PWD`，经 `pwd -P`/`realpath` 规范化。
3. 固定 endpoint digest 输入：主机、SSH 端口、目标用户、身份指纹；禁止只使用 IP 或 basename。
4. 固定目录布局：

   ```text
   ~/.cache/tssh/rdo/<endpoint-digest>/<path-hash>/<basename>
   ~/Downloads/remote-dev/<endpoint-digest>/<path-hash>-<basename>
   ```

5. 定义 sidecar 字段：远程端点、绝对路径、size、mtime、远端 SHA256（可选）、本地 SHA256、更新时间和版本。
6. 定义错误码和用户提示：没有 tssh 上下文、路径不存在、非普通文件、本地应用缺失、桥不可用、权限拒绝、传输中断。

### 验收

- 同一 endpoint + 路径得到稳定的缓存目标。
- 同路径不同设备/用户不会冲突。
- 路径含空格、中文、`..`、符号链接时解析结果明确。
- 不把密码、Token、私钥或原始敏感路径写入日志。

## 5. P1：本地 bridge 和 tssh 会话通道

### 实施内容

1. 在现有 tssh 会话启动阶段创建预览 bridge，不新增远端常驻服务。
2. 为每个 session 分配动态本地端口、远端 loopback 端口和一次性 nonce。
3. 使用独立 SSH reverse forward，不占用 4227/4228。
4. 向远端会话提供短期上下文：

   ```text
   RDO_ENDPOINT
   RDO_PORT
   RDO_NONCE
   RDO_SESSION
   ```

5. bridge 只接受当前 endpoint、用户和 nonce 匹配的请求。
6. tssh 退出、断线、超时和 Ctrl-C 时清理端口、nonce、任务和临时文件。
7. bridge 故障不得导致 SSH 主会话退出；预览失败只返回提示。

当前落地边界：P1 已实现本机 loopback bridge、请求校验和 tssh lease 绑定；SSH
reverse-forward 的远端端口发布与远端 helper 放在 P2 一起接入，避免在远端尚未有
`rget`/`rdo` 时引入一个无消费者的额外 SSH 连接。

### 验收

- `ssh user@host` 不会出现预览端口或上下文。
- VS Code Remote-SSH 使用独立配置时不受影响。
- 两个窗口、两个用户、两台设备的请求互不串线。
- tssh 重连后旧 nonce 失效，旧桥请求被拒绝。

## 6. P2：远端 `rget`/`rdo` helper

### 实施内容

1. 在用户目录安装短脚本：`~/.local/bin/rget` 和 `~/.local/bin/rdo`。
2. helper 只做参数解析、路径规范化和请求发送，不执行本地 GUI。
3. 无上下文时输出：

   ```text
   当前 SSH 会话没有可用的 tssh 预览桥。
   请使用 tssh 重新连接，或在本机执行 rget user@host:/path。
   ```

4. 非交互 shell 快速返回；脚本不得修改 `.zshrc`、代理变量或全局 SSH 配置。
5. 远端依赖仅使用恢复环境已有的基础能力；缺少请求工具时给出明确提示，不安装常驻服务。

### 验收

- 远程 shell 中 `rget /absolute/path` 可以提交下载任务。
- `rget relative/path` 使用当前远程工作目录。
- `rdo` 能区分 Markdown、PDF、图片和不支持类型。
- helper 不暴露 nonce、私钥或代理认证信息。

当前实现：`tssh` 为持钥会话建立独立的动态 SSH reverse-forward，并通过远端
登录 shell 的会话环境注入 `RDO_ENDPOINT`、`RDO_SESSION`、`RDO_NONCE` 和
`RDO_PORT`。`rget`/`rdo` 会保持当前命令直到缓存命中、下载完成或失败，并在终端显示
已传输字节、百分比、速度和 ETA；没有 tssh 上下文、没有密钥或 reverse-forward 建立
失败时，普通 SSH 仍继续使用，不会因此退出。

## 7. P3：SFTP 下载、去重与异步队列

### 实施内容

1. bridge 复用 tssh 的 ControlMaster/ssh-agent，避免每次重新建立 SSH 连接。
2. 默认快速判断：远程 SFTP `stat(size, mtime_ns)` + 本地 sidecar。
3. 未变化文件：零字节传输，直接返回本地路径。
4. 变化文件：一次下载到 `.part`，本地计算 SHA256，成功后原子替换。
5. `--verify` 才执行远端 SHA256 或 SFTP `check-file` 强校验。
6. 任务按 endpoint/path 去重；并发请求共享一个下载任务。
7. 每个 endpoint 限制活动传输数，防止多个大文件拖慢 SSH 输入。
8. 远端请求保持当前命令连接，bridge 按 200ms 节奏回传进度；完成后发送最终状态，
   `rdo` 再启动本地查看器。
9. 状态写入 `~/.cache/tssh/rdo/*/jobs/`，可用 `tssh downloads` 查看仍在运行或最近完成的任务。
10. 默认不执行昂贵 fsync；`--durable` 才保证持久化语义。
11. 使用稳定 `.part` 文件和 SFTP `get -a` 断点续传；远程大小或 mtime 变化时丢弃旧分片。
12. 支持超时、进度、失败重试和 `.part` 保留；Ctrl-C 不会损坏已完成的旧文件。

### 验收

- 未变化文件重复 `rget` 不产生网络传输。
- 远程文件变化后本地只保留最新稳定版本。
- 同一文件两个窗口同时请求只下载一次。
- 断网、取消、磁盘不足不会损坏旧文件。
- 首次请求只阻塞当前 `rget`/`rdo` 命令，并显示可读进度；远程 Nvim、其它 SSH 窗口不受影响。

当前实现：本地 `DownloadManager` 使用系统 OpenSSH 的 `ssh`/`sftp` 客户端，默认
最多两个 worker；同一 endpoint+路径的活动任务合并，完成后写入 SHA256 sidecar。
bridge 在当前命令连接上流式回传进度，SFTP 使用 `get -a` 复用 `.part` 断点，完成后
原子替换；`tssh downloads` 提供脱离当前终端的状态查看。

## 8. P4：Nvim `<S-o>` 和本地应用

### 实施内容

1. 在恢复的 Nvim 文件树中加入 `O`（终端中的 Shift+O）映射，并保留 `<S-o>` GUI 别名。
2. 普通文件调用 `rdo`；目录不自动递归下载，提示使用显式归档命令。
3. 应用选择顺序可配置：
   - Markdown：Typora，其次 `xdg-open`；
   - PDF：用户配置的 PDF 阅读器，其次 `xdg-open`；
   - 图片：用户配置的图片查看器，其次 `xdg-open`。
4. `rdo` 等待下载成功后异步启动本地应用，不等待应用退出；从 Nvim 文件树调用时，使用
   `on_stdout`/`on_exit` 通知显示大文件下载进度和完成状态。
5. 应用不存在时打印本地文件路径，不能让 Nvim 报错退出。
6. `rdo --fast` 明确显示可能打开旧缓存，不作为最终审查默认模式。

### 验收

- PDF 打开的是原始 PDF，不是截图或转码文件。
- Markdown、PDF、PNG/JPEG 等常见文件能调用本地应用。
- 文件路径含空格和中文时仍能正常打开。
- 在无桌面环境中给出友好提示，不破坏 Nvim。

当前实现：`rdo` 下载完成后按扩展名选择本地应用，支持
`RDO_MARKDOWN_APP`、`RDO_PDF_APP`、`RDO_IMAGE_APP` 和 `RDO_DEFAULT_APP`，
并回退到 `xdg-open`。Neo-tree 文件节点已加入 `<S-o>` 映射；目录不会递归下载。

## 9. P5：多端点和故障回归

### 场景

- Debian VM、openEuler 真实设备、Omarchy VM；
- 同一设备同一用户多窗口；
- 同一设备不同用户；
- 多设备同名绝对路径；
- tssh 会话退出、断线、重连；
- bridge 重启、端口冲突、nonce 过期；
- 远程文件未变化、内容变化、mtime 不变、权限变化；
- 本地查看器缺失、磁盘不足、下载取消；
- 普通 SSH 和 VS Code Remote-SSH 并行使用。

### 门禁

- 单元测试覆盖路径规范化、digest、sidecar、锁和状态机。
- 真实 VM 验证首次下载、命中缓存、变化覆盖和并发合并。
- `git diff --check`、pytest、Playbook syntax-check、ansible-lint 全部通过。
- 测试日志不包含密码、私钥、Token 或真实敏感路径。

## 10. P6：制品、文档和发布

### 实施内容

1. 将本地 bridge、tssh hook、远端 `rget`/`rdo` helper 和 Nvim 配置作为同一版本制品发布。
2. 安装器支持升级、旧版本清理、卸载和失败回滚。
3. 安装时不重复写入 SSH config；仅管理明确标记的 tssh 规则。
4. README 增加：
   - tssh 前置条件；
   - `rget`/`rdo` 使用教程；
   - 缓存目录和清理方式；
   - 普通 SSH 无预览上下文的限制；
   - 本地 Typora/PDF/图片查看器配置方式。
5. GitHub Release 制品提供 SHA256，并保留版本兼容说明。

### 发布验收

- 新机器安装后可以建立 tssh 会话并使用 `rget`/`rdo`。
- 升级不会重复注入 SSH 规则或破坏现有 Nvim/zsh 配置。
- 卸载后普通 SSH、VS Code Remote-SSH 和远端 shell 仍然可用。
- 制品、文档、测试和版本号一致。

## 11. 明确不纳入本轮实现

- 不实现远端 GUI、远端桌面或完整 VS Code Server 替代品。
- 不把 `rget`/`rdo` 做成 tssh 子命令。
- 不默认安装 SSHFS、Kitty/Sixel、PDF 转换工具或 rsync。
- 不默认挂载整个远程工作区。
- 不为预览新增远端常驻 Agent 或独立长期 systemd 服务。
- 不使用时间戳无限生成重复文件。
