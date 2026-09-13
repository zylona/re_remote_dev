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

## Verify

只读验收加载目标配置并报告 `READY` 或 `READY_WITH_WARNINGS`；不执行修复、不修改插件、不写入凭据。缺少可选 Treesitter parser、编译器、字体或剪贴板后端应给出具体 Warning。

## 支持边界

支持 apt、dnf/yum、pacman、zypper、apk 可识别的 Linux 发行版；未知发行版不猜测包名，直接输出所需命令。Neovim 核心要求 >=0.9；由于默认启用 nvim-treesitter，P9 会先做标准 C 头文件编译探测，并在 apt 上安装 `build-essential`（dnf/yum 安装 gcc、gcc-c++、make、glibc-devel），而不是只检查 gcc 命令。语言 LSP/运行时不在 P9 首版强制安装范围。

Treesitter 编译器和 CLI 安装均使用 900 秒总超时、30 秒 apt 下载超时及 dpkg 锁等待；超时会主动终止进程并报告可恢复提示。parser 验收检查实际 `site/parser/*.so` 文件数量，避免异步安装尚未完成时误报 READY。
