# Neovim IDE 能力契约

## 目标

在常见 Linux 发行版上恢复当前本机 Omarchy 风格的 LazyVim Neovim 配置，并保持快捷键、主题和核心插件行为一致。

## Apply

`playbooks/plays/50_nvim.yml` 由 `playbooks/site.yml` 在 tools 后执行。Role 会：

1. 按目标发行版包管理器安装缺失的 `neovim`；
2. 备份并部署受控 LazyVim 配置快照和 `lazy-lock.json`；
3. 通过临时 HTTP(S) 代理执行 LazyVim 插件同步；
4. 在用户目录安装 JetBrainsMono Nerd Font（可关闭）；
5. 保留远程剪贴板与 Omarchy 主题兼容逻辑。

配置文件只写入目标用户目录，目标已有配置会先进入 `.remote-dev-backup`。`preserve-existing` profile 不覆盖现有配置。插件和字体下载失败不得删除可用旧缓存。
每次在线恢复都会按 `lazy-lock.json` 做一次可重复同步；交互式启动不会自动检查 GitHub
更新，也不会在缺失插件时偷偷下载。网络失败时 Nvim 保持可启动并给出重试提示，修复代理后
重新执行恢复即可。
使用远程 Nvim 时应通过 `tssh user@host` 进入，使 Nvim 继承本轮 4227 代理；普通 `ssh` 或
未配置代理的 VS Code Remote-SSH 不会自动获得该运行时代理。

## Verify

只读验收加载目标配置并报告 `READY` 或 `READY_WITH_WARNINGS`；不执行修复、不修改插件、不写入凭据。缺少字体或剪贴板后端给出 Warning；Treesitter 编译器、CLI 或声明的 parser 缺失属于恢复失败，必须先修复代理/编译环境。

## 支持边界

支持 apt、dnf/yum、pacman、zypper、apk 可识别的 Linux 发行版；未知发行版不猜测包名，直接输出所需命令。Neovim 核心要求 >=0.9；由于默认启用 nvim-treesitter，P9 会先做标准 C 头文件编译探测，并在 apt 上安装 `build-essential`（dnf/yum 安装 gcc、gcc-c++、make、glibc-devel），而不是只检查 gcc 命令。语言 LSP/运行时不在 P9 首版强制安装范围。

Treesitter 编译器和 CLI 安装均使用 900 秒总超时、30 秒 apt 下载超时及 dpkg 锁等待；发行版没有 `tree-sitter-cli` 时回退到官方固定版本二进制并校验 SHA256。超时会主动终止进程并报告可恢复提示。parser 安装使用官方异步任务的
`task:wait()` 等待所有语言完成，失败会让恢复阶段明确失败，不把未完成的 Markdown parser
留到交互式启动时才报错。

部分较旧发行版的 glibc 低于最新 Tree-sitter CLI 构建要求。此时会先探测新版二进制；若动态
链接失败，则自动回退到仍由官方发布、且固定 SHA256 的兼容版 CLI，不关闭校验，也不会把该
兼容性问题伪装成 parser 已安装。
