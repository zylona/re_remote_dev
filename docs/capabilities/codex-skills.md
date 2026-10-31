# Codex Skills 恢复

完整恢复流程会为目标用户安装以下全局 Codex Skills：

- `Yuan1z0825/nature-skills`：nature-academic-search、nature-citation、nature-data、nature-figure、nature-paper2ppt、nature-polishing、nature-reader、nature-response、nature-reviewer、nature-shared、nature-writing。
- `xingtaxueshu/literature-review-skills`：literature-review-skills。

安装方式与手动命令等价：使用 mise 管理的 Node.js 运行 `npx skills add`，选择 Codex、全局安装并复制文件。无外网目标机通过本轮 setup 临时提供的 HTTP 代理访问 npm registry 和 GitHub；代理不会写入用户长期配置。

Skills 位于 `~/.agents/skills/`。独立只读验收检查每个目录下的 `SKILL.md` 是否存在；缺少项会使 verify 失败。重跑 setup 时，已存在完整的 Skills 会跳过下载；缺失时会重新从对应 GitHub 仓库恢复。

查看当前安装：

```bash
npx --yes skills list --global --agent codex
```

手动更新 Skills：

```bash
npx --yes skills update --global --yes
```

目标机需要能通过本轮代理访问 `registry.npmjs.org` 和 GitHub。若在 Codex 受限命令环境中遇到 `EPERM` 连接 `127.0.0.1:4227`，可在普通远程交互 shell 中执行安装命令，或检查 Codex 命令沙箱是否允许 loopback 网络访问。
