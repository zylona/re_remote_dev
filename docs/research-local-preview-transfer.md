# 远程文件本地预览与下载调研

状态：设计调研，尚未实现

## 1. 问题定义

当前项目已经解决了远程开发环境恢复、`tssh` 会话级代理和 Codex 回调。剩余痛点集中在文件离开远程终端后的本地体验：

1. 在远程 Nvim 文件树中选中 Markdown、PDF 或图片时，希望直接使用本机 Typora、PDF 阅读器或图片查看器打开。
2. 希望把选中的远程文件下载到本机，不再重复输入 `rsync/scp user@host:/path`。
3. 不能破坏普通 `ssh user@host`、VS Code Remote-SSH 或现有 tssh 端点；不能要求远端安装常驻 Agent。
4. 同时连接多台设备、同一设备多个用户和多个窗口时，文件来源必须明确，不能把不同设备的同名文件混在一起。

本调研只讨论本地端的文件桥接和预览，不改变已经稳定的远程恢复 Roles，也不把预览功能塞进远端 shell 配置。`tssh` 仍然只负责连接和端口生命周期；预览/下载不作为远端的 `tssh` 子命令。

## 2. VS Code Remote-SSH 能借鉴什么

VS Code Remote-SSH 的核心是：在远端运行 VS Code Server，由本地 VS Code 负责 UI 和协议编排；远程工作区中的文件不是简单复制到本地。官方文档同时明确说明，Remote-SSH 不直接提供“把远程内容同步给本地工具”的能力，若需要本地工具，应使用 SSHFS 挂载或 rsync 同步：SSHFS 适合少量文件，批量读写则更适合 rsync（[Remote-SSH 文档](https://code.visualstudio.com/docs/remote/ssh)）。

这对本项目有三个启示：

- 预览应用必须在本机启动，远程端只提供 SSH/SFTP 文件访问。
- 文件桥接应是独立的本地客户端能力，而不是修改 VS Code 的 SSH 配置或复用 VS Code Server。
- “浏览远程文件”和“打开本地应用”不一定使用同一种传输方式：小文件可暂存，目录浏览才考虑 SSHFS。

纯 SSH 终端没有 VS Code 的本地 Extension Host、`workspace.fs` 和 URI handler，因此不能仅靠远端 Nvim 配置达到完全相同的“本地 GUI 自动打开”效果。要保留 Ctrl+Shift+O 这类体验，必须由 tssh 会话在本地启动桥接器，并把一个短生命周期的请求通道带入远端会话；这属于本地编排层能力，不是远端开发环境的命令行工具。

Remote-SSH 的端口转发视图也说明了另一种可选路径：把远程 HTTP 预览服务转发到本机浏览器（[Remote-SSH 教程](https://code.visualstudio.com/docs/remote/ssh-tutorial)）。但这只能解决浏览器预览，不能自然地调用 Typora、PDF 阅读器或图片查看器。

VS Code 的关键能力来自客户端/远端双端扩展模型：UI 扩展运行在本机，Workspace 扩展运行在远端；UI 扩展可以通过 `vscode.workspace.fs` 访问远程工作区，`vscode.env.asExternalUri` 还可以把远端 HTTP 服务解析成本地可访问 URI（[Extension Host](https://code.visualstudio.com/api/advanced-topics/extension-host)、[Remote Extensions](https://code.visualstudio.com/api/advanced-topics/remote-extensions)）。这正是 VS Code 能在本机提供预览 UI 的原因，而不是把本机应用从 SSH 终端直接启动。

## 3. 候选方案

| 方案 | 远端要求 | 本地体验 | 性能/稳定性 | 适合范围 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 一次性暂存后用本地应用打开 | 仅 SSH/SFTP | 选中文件即打开 Typora/PDF/图片 | 文件级，故障面小 | Markdown、PDF、图片 | **默认方案** |
| SSHFS 挂载 + 本地 Nvim/文件树 | SSH SFTP 子系统、 本机 FUSE/sshfs | 像本地目录一样浏览、可交给任意应用 | 元数据多时明显变慢；断线需卸载/重连 | 小目录、少量编辑 | 可选方案 |
| Nvim `scp://`/SFTP 浏览 | 仅 SSH | 不需要挂载；依赖 Nvim 文件浏览器 | 适合单文件，目录树体验和插件兼容性有限 | 临时打开单文件 | 低成本 fallback |
| rsync 同步工作区 | 远端需要 rsync | 批量同步好，适合本地编辑 | 需要维护方向、冲突和排除规则 | 大目录、批量读写 | 可选增强，不作为预览前置依赖 |
| 远端 Markdown HTTP 预览 + tssh 转发 | 远端 Node/渲染器或插件 | 本地浏览器实时预览 | 要维护服务、端口和生命周期 | Markdown 浏览器预览 | 非主路径 |
| rclone SFTP mount/copy | 本机 rclone 配置 | 功能多，可扩展对象存储 | 额外依赖和配置较多 | 多种远端/云存储统一 | 不优先 |

### 总体边界：“不下载”到底意味着什么

严格来说，远程文件要在本机屏幕上显示，文件内容或渲染后的像素总要经过网络传输；不存在完全不传输数据的预览协议。可以避免的是“先把完整文件复制成一个普通本地文件并长期缓存”。有三种不同层次：

1. **虚拟文件系统**：SSHFS/SFTP 挂载在本机提供一个路径，本地应用按需读取远端内容，不产生完整的普通文件副本。对用户来说接近“不下载”，但应用的读取、缩略图和随机访问仍会产生网络往返。
2. **流式浏览器预览**：远端 Markdown/PDF 服务通过 SSH 转发到本机浏览器，数据流经隧道但不落成本地文件。它适合浏览器，不保证 Typora 或桌面 PDF 应用能够打开。
3. **远端渲染后传输**：远端把 Markdown/PDF/图片转换成终端可显示的文本或像素，通过 SSH 输出到本地终端；本地不保存文件，但显示能力受终端协议限制。

因此，“不落盘”是可行的，“不传输”不可行；还要区分“本地原生应用预览”和“远程终端内预览”两个目标。

### 3.1 SSHFS

SSHFS 通过 SSH 的 SFTP 子系统把远端目录挂载成本地文件系统，通常不需要远端额外安装软件；官方项目说明其基本命令为 `sshfs user@host:/dir mountpoint`，并建议以普通用户运行（[libfuse/sshfs](https://github.com/libfuse/sshfs)）。它非常适合让本地 Nvim、Typora、PDF 阅读器直接看到远程路径。

但 SSHFS 是网络文件系统，不是缓存同步器。目录树的 `stat/readdir`、插件扫描和大文件随机读取都可能产生大量往返；项目本身也提示存在已知问题且维护资源有限。因而不能默认把整个远程 Home 或工作区挂载到本机，尤其不能让 Nvim 的后台索引依赖它。

### 3.2 Nvim 原生远程路径

Nvim/netrw 原生支持 `scp://host/path` 形式的远程文件和目录浏览（[Neovim 用户文档](https://neovim.io/doc/user/usr_22/)）。`remote-sshfs.nvim` 和 `seamless.nvim` 则分别提供 SSHFS 挂载、远程文件树和 `scp://` 工作流（[remote-sshfs.nvim](https://github.com/nosduco/remote-sshfs.nvim)、[seamless.nvim](https://github.com/pzehrel/seamless.nvim)）。

这些插件可以作为 Nvim 集成参考，但不应直接成为项目的硬依赖：插件版本、文件树实现和 SSHFS/FUSE 在不同发行版上的可用性差异较大。核心下载/预览能力应由独立的本地文件桥接器提供，Nvim 映射只是向桥接器发送请求；远端不安装 tssh。

### 3.3 scp、SFTP 与 rsync

OpenSSH 自带 `sftp` 客户端和 `scp`，远端通常只需启用 SFTP 子系统（[OpenSSH 手册](https://www.openssh.org/manual.html)）。这使“下载一个 Markdown/PDF/图片并打开”可以不安装 rsync，也不需要远端 Agent。

rsync 更适合大量文件的增量同步，但它要求远端具备 rsync，并且必须定义单向/双向同步、删除策略和冲突处理。项目当前的远端基础依赖刻意保持精简，因此 rsync 应作为可选能力，不能成为单文件预览的前置条件。

### 3.4 浏览器渲染

某些 Markdown 预览插件可以在远端启动 HTTP 预览并打印 URL，再通过 SSH 转发到本机浏览器；例如 `markdown-preview.nvim` 支持本地浏览器实时渲染（[markdown-preview.nvim](https://github.com/selimacerbas/markdown-preview.nvim)）。这条链路适合 Mermaid、LaTeX 等浏览器渲染场景，但它不能替代本机 Typora，也不能统一处理 PDF/图片，因此只作为备用方案。

### 3.5 远端安装渲染工具并在终端内显示

如果目标是“纯命令行里看一眼”，可以在远端安装可选渲染工具：

- 图片：`chafa`、`viu`，或直接使用支持 Kitty Graphics/Sixel 的终端协议；Kitty 官方协议允许远端程序通过 SSH 发送图像数据，终端负责显示（[Kitty Graphics Protocol](https://github.com/kovidgoyal/kitty/blob/master/docs/graphics-protocol.rst)）。
- Markdown：`glow`、`mdcat` 或项目自带的终端 Markdown 渲染器，输出 ANSI 文本。
- PDF：`pdftotext` 用于文本阅读，或 `pdftoppm`/ImageMagick 将页面转成图片后再用终端图形协议显示。

这类工具的优点是完全不需要本地文件缓存，也不需要启动桌面 GUI；缺点是无法复刻 Typora 的排版、PDF 阅读器的分页交互或完整图片查看器。Kitty Graphics 也要求本地终端支持对应协议，tmux、终端模拟器和多路复用器可能需要额外 passthrough 配置。因此它应作为远程 CLI 的快速预览 fallback，而不是唯一方案。

远端工具可以由恢复项目作为“可选能力”安装，但不能成为基础恢复的硬依赖：不同发行版包名、终端能力和字体环境差异很大；安装失败不能影响 zsh、Nvim、Zellij 或普通 SSH。

### 3.5.1 图片方案比较

| 工具/协议 | 显示质量 | 终端要求 | 适合场景 | 主要限制 |
| --- | --- | --- | --- | --- |
| `chafa` | Kitty/Sixel 时较高；否则 ANSI/Unicode 彩色块 | Kitty、Ghostty、WezTerm、Sixel 或普通 ANSI | 跨终端默认 fallback | 复杂图片在纯 ANSI 下是近似渲染 |
| `viu` | Kitty/iTerm 原生；可选 Sixel；无图形协议时半块字符 | Kitty/iTerm 或 ANSI | 简单命令和 Nvim 文件树预览 | 官方说明 Kitty 图形协议与 tmux 组合有限；Sixel 仍是实验支持（[viu](https://github.com/atanunq/viu)） |
| Kitty Graphics Protocol | 原生像素级 | Kitty 及兼容终端 | 图片、PDF 页面截图、动画 | 不是所有终端支持；tmux/Zellij 需要验证 passthrough |
| Sixel | 原生像素级 | 支持 Sixel 的终端 | 老牌终端图形链路 | 终端支持不统一，能力探测必须有 fallback |

推荐顺序是：先探测 Kitty/Sixel，再用 `chafa` 选择最佳输出；不支持图形协议时退回彩色块或 ANSI，而不是失败。`viu` 适合轻量安装，但不应作为唯一跨发行版实现。

### 3.5.2 PDF 方案比较

| 工具 | 类型 | 分页/缩放 | 依赖和兼容性 | 结论 |
| --- | --- | --- | --- | --- |
| `pdftotext` | 文本抽取 | 无视觉分页 | Poppler 工具普遍可用 | 最可靠的纯文本 fallback |
| `pdftoppm`/`pdftocairo` + `chafa`/Kitty | 页面转图片后显示 | 可按页生成，交互需自行包装 | 依赖 Poppler + 图片渲染协议 | 最容易跨发行版落地，可管道化避免临时文件 |
| `termpdf` | 终端 PDF 阅读器 | 有分页、缩放 | Kitty/iTerm，依赖 Ghostscript | 适合作为 Kitty 用户的可选工具（[termpdf](https://github.com/dsanson/termpdf)） |
| `pdfterm` | 交互式 PDF 阅读器 | Vim 风格翻页、跳页、链接 | Kitty，内置 PDFium；当前不支持 tmux passthrough | 体验最好但终端范围窄（[pdfterm](https://github.com/jrf/pdfterm)） |
| `tdf`/同类 TUI | 终端 PDF 阅读器 | 取决于实现 | 各项目维护状态和图形协议不同 | 需要逐项目验证，不作为基础依赖 |

生产级默认应采用 `pdftotext` + `pdftoppm` 管线：用户需要快速阅读时显示文本，检测到 Kitty/Sixel 后按页转换并渲染图片。`pdftoppm` 输出到标准输出或受控临时目录，能够避免把整个 PDF 复制成第二份；交互式分页可以逐页生成并在退出后清理。Kitty-only 的 `pdfterm`/`termpdf` 作为增强 profile，而不是所有发行版都强制安装。

## 4. 推荐架构：虚拟挂载优先，暂存作为兜底

### 4.1 原生应用的无普通文件副本模式

如果用户明确要求 Typora、PDF 阅读器或图片查看器，并且不希望先产生本地缓存，推荐让本地桥接器为当前 endpoint 建立按需 SSHFS 挂载：

```text
远程 Nvim 选中路径
        │  rdo 请求
        ▼
本地 tssh-rdo-bridge
        ├─ 确认/创建 ~/.cache/tssh/mounts/<digest> 挂载点
        ├─ sshfs user@host:/ 映射为本地虚拟路径
        ├─ 将虚拟路径交给 Typora/PDF/图片查看器
        └─ 会话结束或显式关闭时卸载
```

SSHFS 官方实现基于 SSH 的 SFTP 子系统，通常不需要远端额外安装软件（[libfuse/sshfs](https://github.com/libfuse/sshfs)）。这比“下载到临时目录再打开”更接近用户想要的效果，但本地应用必须支持普通文件路径，且大文件随机读取仍然受网络延迟影响。

### 4.2 一次性暂存兜底模式

```text
远程 Nvim 文件树选中路径
        │  本地映射/命令（不在远端执行 GUI）
        ▼
rdo DEST:PATH
        │
        ├─ 通过 ssh -G 解析用户、端口、密钥和 Host 别名
        ├─ 使用 SFTP（必要时 scp）下载到本地 0700 缓存
        ├─ 按扩展名选择本机应用（Typora/PDF/图片/浏览器）
        └─ 返回本地路径、来源和清理提示
```

缓存建议放在 `~/.cache/tssh/preview/<endpoint-digest>/`，文件名使用远端路径的安全编码或内容哈希，不把密码、Token 写入路径。不同主机、端口和用户必须使用不同 endpoint digest，避免多设备同名文件覆盖。

### 4.3 为什么默认不用整目录 SSHFS

如果本机没有 FUSE/SSHFS、远端 SFTP 不可用，或应用无法稳定读取挂载点，则退回一次性暂存。一次性暂存只产生一次文件传输，不会让普通 SSH 输入、VS Code Remote-SSH 或 Nvim 后台扫描共享网络文件系统。它也不需要额外本地监听端口，不会参与 4227/4228 的代理和回调生命周期。

SSHFS 仍然只对小目录和预览场景启用；不默认挂载整个 Home 或大型工作区。挂载点按 endpoint 隔离，例如 `~/.cache/tssh/mounts/<digest>/`；退出、断线或 `remote-dev-unmount` 时清理，不做开机自动挂载。

## 5. 远程 Nvim 如何调用本地程序

这是本需求最容易被低估的边界：如果 Nvim 是通过 SSH 在远端运行的，Nvim 的 Lua 映射执行的是远端进程，不能直接调用本机的 `tssh`、Typora 或 PDF 阅读器。因此“在远程 Nvim 按一个键直接打开本机应用”不能只写一段远端 Lua 配置，必须有一条回到本机的请求通道。

推荐在 **tssh 会话建立时** 创建一个独立的、按会话隔离的本地预览桥。远端安装的是很小的 `rdo` 请求 helper，而不是 tssh：

```text
远程 Nvim / rdo
        │  HTTP/Unix 风格的一次请求，带 digest + nonce
        ▼
SSH -R 127.0.0.1:<remote-preview-port>:
       127.0.0.1:<local-preview-port>
        │
        ▼
本地 tssh-rdo-bridge
        ├─ 校验端点、用户和一次性 nonce
        ├─ 通过本地 SSH/SFTP 下载远程路径
        └─ 调用 Typora/PDF/图片查看器
```

该通道使用每个会话的动态端口，不占用 4227 代理端口或 4228 事件端口，也不需要修改用户的全局 SSH 配置。tssh 通过会话初始化向 helper 提供端点摘要、动态远端端口和一次性 nonce；这些信息只属于当前会话，不写入全局 `.zshrc`。tssh 退出时撤销反向转发并清理 nonce；普通 `ssh user@host` 不注入此桥，因此不会改变普通 SSH 或 VS Code Remote-SSH 的行为。远端 helper 只发送端点、路径和操作类型，下载、应用选择和 GUI 启动全部发生在本机。

对于不经过 tssh 建立的普通 SSH 会话，不能承诺远程 Nvim 按键自动调用本机程序；这是 SSH 终端进程边界的客观限制。可以在本机直接执行 `rdo user@host:/path` 作为手动 fallback，但它不是 tssh 子命令。

### 5.1 为什么不直接复用 4228

4228 当前承担 Codex/OAuth 事件回调。把文件预览请求混入该协议会让 Codex 登录、断线恢复和预览生命周期互相影响，难以排查，也会让长期代理/临时会话的权限边界混淆。预览桥应当是独立、短生命周期、可选的通道。

## 6. 面向用户的命令设计

以下是建议的接口契约，当前仅写入设计，不代表本版本已实现。用户在远端和本机都使用 `rdo`；它根据当前是否存在 tssh 会话上下文选择请求桥或直接 SFTP：

```bash
# 远端 Nvim 映射实际调用（tssh 会话中）
rdo /srv/docs/design.md
rdo --app pdf /srv/docs/manual.pdf
rdo --app image /srv/images/diagram.png

# 本机 fallback：显式提供目标，直接 SFTP 下载
rdo user@host:/srv/docs/design.md
rget user@host:/srv/build/report.pdf ./downloads/report.pdf

# 可选：小目录 SSHFS 挂载（由本机桥接器管理）
remote-dev-mount user@host:/srv/project
remote-dev-mounts
remote-dev-unmount user@host
```

本机工具应复用 tssh 已有的 SSH 身份解析和 endpoint digest，而不是要求用户再次输入 IP、端口或私钥。默认只支持密钥/ssh-agent；密码可由底层 SSH 交互询问，但不保存密码，也不承诺后台无人值守重连。

## 7. Nvim 体验设计

### 7.1 远程 Nvim 内选中文件

恢复到远端的 Nvim 只负责知道当前文件路径和远程端点，并向本会话的预览桥发送结构化请求。项目可以提供一个轻量 Lua 映射：

- `Ctrl+Shift+O`：通过预览桥调用本地应用，按扩展名打开 Typora、PDF 阅读器或图片查看器；
- `<leader>rd`：请求本地桥下载当前文件，弹出本地保存目录或使用预设下载目录；
- `<leader>rm`：对当前端点执行显式 SSHFS 挂载（可选）。

映射需要通过环境变量或 tssh 会话元数据获得“当前远程主机 + 远程绝对路径”。不能猜测当前目录，也不能从文件名反推主机。对于无法识别端点的普通远程 Nvim，应给出明确提示并允许用户输入 `user@host`。

### 7.2 本地 Nvim 浏览已挂载目录

如果用户选择 `remote-dev-mount`，本地 Nvim 文件树直接打开挂载点，Ctrl+Shift+O 可以对挂载点下的文件使用本地 `xdg-open`/应用关联。该模式适合小目录；大工作区仍建议继续在远程 Nvim 编辑，避免把整个项目变成高延迟网络文件系统。

### 7.3 编辑回写策略

PDF 和图片默认只读预览。Markdown 默认也采用“只读暂存预览”，防止用户在 Typora 中修改后不知不觉覆盖远端文件。

若未来提供编辑回写，应显式使用 `tssh edit`，记录下载时的远端 `mtime/size/hash`，保存时先检查远端是否发生变化，再通过 SFTP 原子上传临时文件并 rename；冲突必须停止并提示，而不是静默覆盖。

## 8. 安全、性能与故障边界

- **凭据**：调用系统 SSH 配置、ssh-agent 和 known_hosts；禁止把密码、私钥和代理 Token 放进命令日志、缓存名或 Nvim 状态文件。
- **本地缓存**：目录权限 `0700`，可按 endpoint 或 TTL 清理；`rdo --clean` 清理临时文件。
- **不可信文件**：Markdown 可能包含外链/脚本，PDF 可能利用阅读器漏洞；首次打开可提示来源，默认交给用户选择的本地应用，不在远端自动执行。
- **断线**：单文件下载失败只影响当前预览，不应影响 SSH 会话；SSHFS 断线显示为挂载不可用，不能让 shell 或 VS Code 连接一起阻塞。
- **多设备/多用户**：缓存、挂载点和锁键都包含 endpoint digest；同一设备不同用户视为不同源。不能使用单一全局 `/tmp/preview.md`。
- **代理和端口**：预览/下载使用已有 SSH/SFTP 通道，不新增 4227/4228 监听；浏览器渲染模式才按需申请动态转发端口。
- **性能**：单文件采用一次 SFTP 传输；大目录由用户明确选择 SSHFS；批量同步才考虑 rsync。这样不会把本地 GUI 预览流量或远程文件树扫描塞进 tssh 的持久代理通道。

## 9. 分阶段落地建议

### P0：命令契约与本地传输核心

实现 `rdo` 的远端请求和本机直连两种模式，包含路径解析、SSH 身份复用、SFTP/SSHFS 访问、缓存权限、默认应用探测和清理。优先尝试虚拟挂载；无法挂载时退回一次性 SFTP 暂存。支持 Markdown/PDF/常见图片；没有对应应用时给出安装提示并打印本地文件路径。

### P1：Nvim 映射

在远程恢复的 Nvim 配置中加入可选映射、远端 `rdo` helper 和 tssh 会话预览桥。映射失败不得影响 Nvim 启动；非 tssh 会话显示“缺少远程端点上下文”的友好提示，并允许本机 `rdo user@host:/path` fallback。

### P2：可靠性与多端点测试

在 Debian、openEuler、Omarchy VM 上验证密钥登录、密码交互、路径含空格、非 ASCII 文件名、断网、重复下载、多用户同名路径和普通 SSH/VS Code 独立性。分别验证 Kitty/Sixel、tmux/Zellij passthrough 和纯 ANSI fallback；验证缓存清理和文件权限，不把测试文件写入仓库。

### P3：可选 SSHFS

仅在本机检测到 `sshfs`/FUSE 时启用 `remote-dev-mount`；增加挂载状态、超时、断线卸载和 `remote-dev-unmount --all`。不自动安装远端包，不默认挂载，不修改 VS Code SSH 配置。

### P4：可选终端渲染、浏览器预览与批量同步

在用户明确需要时增加远端终端渲染工具、动态端口的浏览器预览，以及 rsync 目录同步。终端渲染按能力探测选择 Kitty/Sixel/ANSI；浏览器预览和 rsync 都不能成为单文件预览的前置依赖，并应单独显示生命周期和风险。

## 10. 最终建议

本项目最匹配的路线不是复制 VS Code Server，而是把 VS Code Remote-SSH 的“本地 UI、远程文件访问、传输与界面解耦”思想缩小到终端场景：

1. **默认：远端 `rdo` + 本地桥接器按需 SSHFS 虚拟挂载 + 原生应用打开**。不产生完整普通文件副本，远端不安装 tssh，不影响 SSH 输入和 VS Code。
2. **兜底：本地桥接器一次性 SFTP 暂存**。当 FUSE/SSHFS 或应用兼容性不足时，保证预览仍可用。
3. **默认：`rget` 一键下载**。下载是明确的用户动作，不与预览缓存混为一谈。
4. **可选：远端 CLI 渲染**。图片使用 Kitty/Sixel，Markdown/PDF 使用终端文本或像素渲染，适合没有桌面 GUI 的纯命令行场景。
5. **可选：rsync、浏览器 Markdown 服务**。分别服务于批量同步和浏览器渲染，保持与核心预览能力解耦。

低频高保真审查场景建议把用户入口收敛为远端 `rdo`：Nvim `<S-o>` 调用 `rdo`，本地 tssh 桥完成一次 SFTP 下载并打开本地查看器；用户不需要记忆 IP、远程路径或本地缓存目录。

该设计不需要新的远端常驻 Agent，不需要在本地全局 SSH 配置中添加规则，不占用 4227/4228，也不会把 VS Code Remote-SSH 纳入 tssh 生命周期；它只在用户主动预览、下载或挂载时创建短生命周期的本地操作。

## 11. 针对“低频但必须高保真”的产品决策

你的实际需求不是日常浏览，而是在文档、报告或设计稿最终审查阶段，偶尔集中查看 PDF、图片和 Markdown 的最终渲染效果。这会改变优先级：可靠的显示结果比“完全不落盘”或“纯终端内完成”更重要。

### 11.1 推荐方案：SSHFS 直读 + 本地原生查看器

对于 PDF 和图片，推荐使用本地真实查看器（PDF 阅读器、Typora、图片查看器）打开 SSHFS 挂载路径：

```text
远程最终文件
      │ SSH/SFTP 按需读取
      ▼
本地 SSHFS 虚拟路径
      │ 普通文件路径
      ▼
本地原生查看器
```

这个方案不需要把完整文件复制成普通本地副本，且本地查看器使用原始 PDF/图片数据，能够保留字体、分页、颜色、矢量图、注释和打印布局等最终效果。SSHFS 本身通过 SFTP 工作，远端通常不需要安装额外 Agent（[libfuse/sshfs](https://github.com/libfuse/sshfs)）。

它的缺点是首次打开、跳页和大文件随机访问仍然受网络延迟影响；但你的预览不是持续编辑或频繁浏览，这个代价是可以接受的。挂载一个文件所在的小目录即可，不要挂载整个 Home 或项目树。

### 11.2 最稳妥的 fallback：显式下载后打开

如果 SSHFS、FUSE 或本地查看器对挂载路径兼容性不好，使用一次性下载反而是最稳的工程方案：

```text
远程文件 → SFTP 下载到 0700 临时目录 → 本地原生查看器
```

这不是持续同步，也不是后台缓存；只在用户主动审查时传输一次，退出后清理。对于低频高保真需求，下载时间通常小于排查虚拟文件系统兼容问题的成本。可以保留“打开后不自动删除”的选项，便于连续审查同一份最终产物。

### 11.3 不推荐作为主方案的路径

- **Kitty/Sixel/PDF 转图片**：适合快速检查，但不适合作为“最终效果”依据；字体、分页、交互、颜色管理和终端兼容性都可能改变结果。
- **`pdftotext`**：只能验证文本内容，无法验证排版和视觉效果。
- **远程 HTTP/PDF.js**：可以不产生本地文件副本，但仍需要浏览器预览链路，且和 Typora、桌面 PDF 阅读器的行为不完全一致。
- **持续 SSHFS 挂载整个工作区**：会引入元数据延迟、断线和应用索引问题，与你低频查看的目标不匹配。

### 11.4 是否值得实现

建议实现，但只实现一个很窄的“低频审查通道”，不要把它扩展成第二套远程文件系统：

1. 远端只提供 `rdo` 请求 helper，不安装 tssh，也不运行 GUI。
2. 本地桥接器优先为当前文件建立小范围 SSHFS 虚拟路径。
3. SSHFS 不可用时自动退回一次性 SFTP 下载。
4. 最终打开动作始终交给用户指定的本地原生应用。
5. 终端内 `chafa`、Kitty Graphics、PDF 文本渲染只作为“快速查看”可选能力，不参与最终验收。

这样项目仍然是命令行优先：连接、鉴权、文件定位、挂载、下载和应用选择都由命令行编排；只有最后的高保真展示交给本地 GUI 查看器。这不是目标冲突，而是把“终端自动化”和“视觉验收”放在各自最可靠的层。

## 12. 更简单的替代路线：一键下载后本地预览

如果不要求“本地应用直接打开远程路径”，可以进一步简化为：**远程 Nvim 只发送当前文件路径，本地桥接器使用 SFTP 下载到本地，随后由本地查看器打开**。这是本项目更容易做到稳定的第一版。

### 12.1 推荐工作流

```text
远程 Nvim 文件树选中文件
        │ Ctrl+Shift+O / <leader>rd
        ▼
远端 rget / 当前 tssh 会话桥
        │ 仅发送绝对路径和操作类型
        ▼
本地 bridge 通过 SFTP 读取远程文件
        │ .part 临时文件 →（持久下载时 fsync）→ 原子 rename
        ▼
~/Downloads/remote-dev/<endpoint-digest>/<timestamp>-<basename>
        │
        └─ 可选：自动调用 Typora/PDF 阅读器/图片查看器
```

### 12.2 为什么优先使用 SFTP，而不是让远端执行 scp

- 文件传输由本地桥接器完成，远端不需要知道本地路径，也不需要反向 SSH 登录本机。
- OpenSSH 自带 SFTP 客户端/子系统，远端不需要额外安装 rsync 或 Agent（[OpenSSH Manual Pages](https://www.openssh.org/manual.html)）。
- 可复用当前 tssh 已认证的 SSH Master/Agent，避免每次输入密码。
- 下载失败、断线或取消只影响当前文件，不会影响交互式 SSH 会话。

### 12.3 用户体验建议

建议远端下载只保持一个简单入口，例如：

```bash
rget /workspace/docs/report.pdf
```

它不是 tssh 命令，也不需要远端安装 tssh；它只是向当前 tssh 会话的本地桥发送请求。没有 tssh 上下文时，应明确提示“请使用本机 `rget user@host:/path`”，而不是尝试隐式建立第二条连接。

本地侧应提供以下行为：

- 默认下载到 `~/Downloads/remote-dev/<endpoint-digest>/`，不同设备、端口和用户隔离；
- 同名文件不覆盖，自动增加时间戳或序号；
- 先写 `.part` 文件，校验大小后原子改名，避免查看器打开半个文件；
- `rdo` 只调用本地应用，不在远端启动 GUI；`rget` 永远只负责下载；
- 默认限制单文件大小并在超限前询问；目录下载必须显式使用 `--archive`，避免误传整个工作区；
- 输出远程来源、最终本地路径、文件大小和校验摘要，便于审查和复现；
- 提供 `--keep`/`--clean`，让用户决定审查文件何时删除。

### 12.4 这个方案的取舍

优点是实现简单、故障边界清晰、对 PDF/图片/Markdown 的最终效果完全交给本地成熟应用，不需要处理 SSHFS 的缓存、FUSE、挂载断线和应用兼容性。缺点是每次打开前都要传输一次文件，并且远程 Nvim 的快捷键仍需要 tssh 会话级请求桥才能触发本地下载。

对于当前“低频预览、高保真审查”的需求，这个缺点可以接受。建议先实现下载桥，暂不实现 SSHFS；以后只有在真实使用中确认频繁重复下载成为痛点，再增加可选挂载能力。

## 13. 短命令与 Nvim 快捷键设计

### 13.1 命令选择：`rget` + `rdo`

需求拆成两个层次更清晰：

- **`rget`**：底层文件下载命令，只负责把当前远程会话中的文件传回本地；
- **`rdo`**：上层预览命令，调用 `rget` 的传输能力，下载完成后打开本地应用。

`rget` 只有 4 个字母，含义为 **remote get**，适合成为远程开发环境中的常用基础命令；`rdo` 只有 3 个字母，含义为 **remote document open**，专注于预览。

```bash
# 远程会话中：只下载，不打开应用
rget /workspace/docs/report.pdf
rget docs/report.pdf

# 远程会话中：下载并打开本地应用
rdo /workspace/docs/report.pdf
rdo docs/design.md
```

命令不要求用户再次输入本地设备地址、远程 IP、用户名或私钥。目标设备和用户来自当前 tssh 会话上下文。

`rget` 和 `rdo` 都不是 tssh 子命令，也不负责 SSH 登录、代理或密码保存；它们只是远端开发环境中的请求 helper。

```bash
# 远端 Nvim 或 shell 中：下载并按类型打开本地应用
rdo /workspace/docs/report.pdf
rdo /workspace/docs/design.md
rdo /workspace/images/diagram.png

# 只下载，不启动应用
rget /workspace/docs/report.pdf

# 最终审查时执行远端 SHA256 强校验
rget --verify /workspace/docs/report.pdf

# 指定本地应用类型
rdo --app typora /workspace/docs/design.md
rdo --app pdf /workspace/docs/report.pdf
```

`rget`/`rdo` 不负责 SSH 登录、不保存密码、不实现代理，也不在远端启动 GUI。它们只读取当前 tssh 会话注入的短期上下文，并把“端点、远程路径、操作类型”发送给本地桥。没有 tssh 上下文时应立即给出提示，而不是偷偷新建第二条 SSH 连接。

### 13.2 本地桥与 tssh 的职责

不建议为了预览再创建一个常驻远端服务。现有 tssh 本地编排器已经适合承载这一能力：

- tssh 建立会话时，为当前 endpoint 分配一个随机的预览桥端口和 session nonce；
- 通过一个独立的 SSH reverse forward 把远端 loopback 请求送回本地桥；
- 在远端会话上下文中提供 `RDO_ENDPOINT`、`RDO_PORT`、`RDO_NONCE` 等短期信息；
- `rdo` 发起一次请求后立即返回，本地桥负责 SFTP 下载和启动应用；
- tssh 退出、断线或超时后撤销该通道并清理 nonce。

预览桥不复用 4227 代理端口或 4228 Codex 事件端口，避免文件下载影响代理和登录回调。普通 `ssh user@host` 不注入 `rdo` 上下文，因此不会影响 VS Code Remote-SSH 或其他 SSH 客户端。

### 13.3 Nvim 文件树快捷键

在远程恢复的 Nvim 文件树中，将 `<S-o>`（Shift+O）绑定为：

```text
选中文件
   ↓ Shift+O
rdo <remote-path>
   ↓
本地 SFTP 下载到受控缓存
   ↓
Typora / PDF 阅读器 / 图片查看器
```

映射必须只对普通文件生效；目录显示提示使用 `rget --archive`，避免误传整个工作区。映射失败不得阻塞 Nvim，错误信息应包含“当前会话没有 tssh 预览桥”“文件不存在”“本地查看器未配置”等可操作原因。

### 13.4 路径解析

`rget`/`rdo` 接受绝对路径和相对路径：

- 以 `/` 开头的参数按远程绝对路径处理；
- 相对路径相对于当前远程 shell 的 `$PWD` 解析；
- 解析前使用远端 `pwd -P`/`realpath` 规范化 `.`、`..` 和符号链接，最终请求必须携带规范化后的绝对路径；
- 不允许把本地路径、`~` 或用户输入中的控制字符直接拼接到本地文件名；
- 无法解析时在远端立即返回明确错误，不创建下载任务。

### 13.5 缓存和下载目录

预览缓存与用户明确保存的下载文件分开：

```text
~/.cache/tssh/rdo/
└── <endpoint-digest>/
    └── <content-hash>-<safe-basename>/

~/Downloads/remote-dev/
└── <endpoint-digest>/
    └── YYYY/MM/DD/<timestamp>-<safe-basename>
```

- 缓存目录 `0700`，默认 TTL 24 小时；`rdo --clean` 清理缓存；
- `rget` 下载到 `~/Downloads/remote-dev`，默认保留；`rdo` 使用同一份缓存后再打开应用；
- endpoint digest 必须包含主机、SSH 端口、用户和身份指纹，防止多设备同名文件覆盖；
- 先写 `.part`，完成后校验大小/可选 SHA256，再原子重命名；
- 文件名只用于展示，不能承载密码、Token 或原始远程路径中的危险控制字符；
- 下载过程中断不留下可被查看器误打开的半成品。

### 13.6 是否需要新的常驻用户服务

第一版不新增远端常驻服务，也不新增本地独立守护进程：预览桥作为现有 tssh user service 的按会话子任务运行。这样可以复用已有的 endpoint 生命周期、SSH Master、清理和多窗口接管逻辑。

只有在未来出现“没有 tssh 前台会话但仍要远程 Nvim 自动触发本地预览”的需求时，才重新评估本地常驻桥；届时也应保持 socket activation 和按 endpoint 的短生命周期，而不是让每台远程主机安装长期 Agent。

## 14. 同路径文件的去重与一致性校验

远程文件的唯一身份不能只使用 basename。应使用以下组合键：

```text
endpoint-digest + canonical-remote-absolute-path
```

因此，`203.0.113.10:22/alice:/workspace/report.pdf` 和 `198.51.100.20:22/bob:/workspace/report.pdf` 即使远程绝对路径相同，也必须是两个独立的本地对象。

### 14.1 确定性本地路径

默认下载路径不使用时间戳作为唯一目录，而是保持稳定：

```text
~/Downloads/remote-dev/<endpoint-digest>/workspace/report.pdf
```

路径中的 `..`、控制字符和不可安全显示的字符必须编码；远程根目录、用户和原始绝对路径写入 sidecar，而不是依赖文件名推断来源。预览缓存仍使用：

```text
~/.cache/tssh/rdo/<endpoint-digest>/<path-hash>/<basename>
```

### 14.2 下载前检查顺序

每次 `rdo` 请求按以下顺序执行：

1. 通过 SFTP `stat` 获取远程文件类型、大小、修改时间（优先纳秒精度）。目录、设备文件和符号链接按策略单独处理。
2. 读取本地 sidecar，例如 `report.pdf.rdo.json`，确认 endpoint、远程绝对路径、本地目标路径和上次校验信息都匹配。
3. 如果远程大小和修改时间与 sidecar 一致，同时本地文件大小和本地 SHA256 与 sidecar 一致，则直接返回“已是最新”，**不下载文件**。
4. 如果元数据变化、sidecar 缺失或本地文件被修改，则进入一致性校验或重新下载流程。

### 14.3 SHA256 校验策略

为了避免“大小和 mtime 恰好相同但内容已变”的极端情况，支持两种模式：

- **快速模式（默认）**：以 `size + mtime_ns + 本地 SHA256` 作为增量判断。适合日常重复按 `<S-o>`，远程构建工具正常更新 mtime 时无需额外计算远程哈希。
- **严格模式（`rget --verify` 或最终审查）**：通过远端可用的 `sha256sum`/`shasum`/`openssl dgst` 计算远程哈希，或使用 SFTP `check-file` 扩展；将远程哈希与 sidecar 和本地哈希比较。只有哈希不同才传输。

远端没有哈希工具且不支持 SFTP 扩展时，必须明确提示“无法进行远端强校验”。可选择保守地下载到 `.part` 后计算本地哈希，再决定是否替换；不能伪称已经完成远程强校验。

### 14.4 变化文件的原子覆盖

发现内容变化后：

1. 下载到同目录的随机 `.part` 文件；
2. 完成后计算本地 SHA256，并与已知远程哈希（若有）比较；
3. 校验通过后使用原子 rename 覆盖目标文件；
4. 更新 sidecar，记录 endpoint、远程绝对路径、大小、mtime、远程 SHA256（若取得）、本地 SHA256 和更新时间；
5. 校验失败或中断时删除 `.part`，保留原文件和旧 sidecar。

不建议默认生成带时间戳的第二份同名文件。只有用户显式传入 `--keep-copy` 或指定不同输出路径时，才保留历史版本。

### 14.5 并发和缓存清理

- 同一 endpoint/path 使用文件锁，避免两个 Nvim 窗口同时下载同一个文件；后到请求等待后重新检查 sidecar。
- sidecar 和目标文件必须一起替换，不能出现“文件是新版本、元数据是旧版本”的状态。
- `rdo --clean` 只能删除 TTL 到期的缓存；默认下载目录中的用户保留文件不自动删除。
- endpoint digest 变化（例如用户、端口或 SSH 身份变化）时视为新来源，不能复用旧文件而跳过校验。

## 15. 预览耗时优化

预览命令和归档下载不是同一种操作。`<S-o>` 的首要目标是尽快打开查看器，因此默认路径必须避免远程哈希、重复连接、重复传输和不必要的同步落盘。

### 15.1 两级校验策略

默认 `rdo` 使用快速路径：

```text
本地 sidecar + 远程 SFTP stat(size, mtime)
        │
        ├─ 未变化 → 直接打开已有本地文件，0 字节传输
        └─ 有变化 → 单次 SFTP 下载到 .part，再本地计算哈希并替换
```

不要在每次预览前默认执行远端 `sha256sum`：它会增加一次 SSH 命令往返和远端 CPU 扫描，且对已经由 size/mtime 判断未变化的文件没有收益。`--verify` 只用于最终审查或用户怀疑远程工具没有正确更新 mtime 的情况。

当文件发生变化时，预览模式只做一次传输；下载完成后在本地计算 SHA256，更新 sidecar。这样“变化检测 + 文件传输”不会变成 `stat → 远端 hash → 再下载` 的三段式流程。

### 15.2 复用现有 SSH 会话

- tssh bridge 应复用当前 endpoint 的 ControlMaster/ssh-agent，不重新进行 TCP、密钥交换和 host-key 检查；
- 本地 bridge 在一个 tssh 会话内复用 SFTP 连接或至少复用 SSH master，减少每次 `<S-o>` 的进程启动成本；
- 预览请求只发送路径和操作类型，不重新探测发行版、代理或远端工具；
- 不为每个文件创建新的长期 systemd unit，文件请求完成后释放短生命周期资源。

### 15.3 本地文件操作优化

- 预览缓存和目标文件使用同一文件系统，`.part → rename` 为 O(1) 原子切换；
- 预览模式默认不执行昂贵的 `fsync`，只在显式 `rget --durable` 时保证断电持久性；
- sidecar 更新可以在文件原子替换后立即写入，不阻塞查看器启动；
- 查看器使用 `xdg-open`/应用命令异步启动，`rdo` 不等待 GUI 退出；
- 已经存在且校验通过的文件直接打开，不复制到第二个临时目录，也不生成时间戳副本。

### 15.4 大文件和用户反馈

- 开始传输前输出文件大小和预计动作，超过配置阈值时询问；
- 传输期间显示进度、速率和已传字节，取消时立即删除 `.part`；
- PDF 预览始终传输并打开原始 PDF，不提供页面截图或降级渲染模式，确保最终审查看到的就是原件效果；
- 不默认启用 SSH 压缩：PDF、PNG、JPEG 等已经压缩，压缩会增加 CPU 和延迟；Markdown/纯文本可提供 `--compress` 显式选项。

### 15.5 可接受的性能目标

在已有 tssh 会话和本地 sidecar 存在时：

- 未变化文件：一次 SFTP `stat`，不传输文件；
- 变化文件：一次 SFTP 文件传输，不执行远端哈希；
- 首次文件：一次 bridge 请求 + 一次 SFTP 传输 + 一次本地应用启动；
- 多次重复按键：命中缓存后只保留 stat 和本地打开成本。

这比每次都执行“新建 SSH、远程计算哈希、下载到新目录、再启动查看器”低得多，也更符合低频但需要快速连续审查的场景。

### 15.6 异步打开策略

原生 PDF/图片/Typora 必须能够读取完整文件后才能稳定打开，因此“首次文件完全不等待传输”在物理上不可行；可以优化的是不阻塞远程 Nvim 和 SSH 会话，并把传输、校验、应用启动放到本地后台队列。

#### 已有缓存文件

提供两个模式：

- **正确模式（默认）**：先执行一次远程 `stat`，确认未变化后立即打开本地文件；如果变化，进入后台下载并在完成后打开新版本。
- **快速模式（`rdo --fast`）**：如果本地存在最近一次已校验的文件，立即打开缓存，同时后台执行 `stat`；发现远程已变化时再下载并提示“已刷新”。该模式必须明确标记“可能暂时显示旧版本”，不作为最终审查默认模式。

这样重复审查同一个未变化文件时，用户只感受到本地应用启动时间，而不是 SSH 往返时间。

#### 首次文件或变化文件

```text
远程 Nvim 执行 rdo
        │
        ├─ 请求写入本地 bridge 队列后立即返回
        ├─ bridge 复用 endpoint 的 SFTP/SSH master
        ├─ 后台下载 .part → 本地校验 → 原子 rename
        └─ 下载完成后异步启动/刷新本地查看器
```

远端 `rdo` 不应等待本地应用退出，也不应等待整个下载过程；只需返回 `queued`、任务 ID 和本地目标路径。用户可以通过 `rdo status` 或 Nvim 通知查看进度，失败时给出可操作的错误，而不是让远端 shell 卡住。

#### 请求合并与连接复用

- 同一 endpoint/path 的并发请求合并为一个任务，后续请求订阅同一任务结果；
- 同一 endpoint 的不同文件使用一个短生命周期 worker，复用 SFTP 连接；
- 下载队列有界，默认每个 endpoint 一个活动传输，避免多个大文件争用 SSH 链路；
- 查看器启动使用 `setsid`/异步子进程，关闭查看器不会杀掉 tssh 或队列 worker；
- sidecar 写入和缓存清理放到任务尾部，不阻塞首次打开通知。

#### 不应做的“伪优化”

- 不在文件未完整下载前把 `.part` 路径交给 PDF 阅读器；
- 不默认启用远端 SHA256、SSH 压缩或全目录预取；
- 不为了预览启动新的常驻远端服务或额外 SSH 登录；
- 不通过持续 SSHFS 扫描整个项目来“预热”缓存。

## 参考资料

- [VS Code Remote-SSH](https://code.visualstudio.com/docs/remote/ssh)
- [VS Code Remote-SSH 教程与端口转发](https://code.visualstudio.com/docs/remote/ssh-tutorial)
- [VS Code Remote-SSH Troubleshooting](https://code.visualstudio.com/docs/remote/troubleshooting)
- [Microsoft vscode-remote-release](https://github.com/microsoft/vscode-remote-release)
- [libfuse/sshfs](https://github.com/libfuse/sshfs)
- [Neovim Remote Files / netrw](https://neovim.io/doc/user/usr_22/)
- [remote-sshfs.nvim](https://github.com/nosduco/remote-sshfs.nvim)
- [seamless.nvim](https://github.com/pzehrel/seamless.nvim)
- [OpenSSH Manual Pages](https://www.openssh.org/manual.html)
- [rclone SFTP](https://rclone.org/sftp/)
- [markdown-preview.nvim](https://github.com/selimacerbas/markdown-preview.nvim)
- [chafa terminal graphics](https://github.com/hpjansson/chafa)
- [viu terminal image viewer](https://github.com/atanunq/viu)
- [pistol file previewer](https://github.com/doronbehar/pistol)
- [pdfterm](https://github.com/jrf/pdfterm)
- [termpdf](https://github.com/dsanson/termpdf)
