# Security Policy

## 报告安全问题

请不要在公开 Issue 中粘贴密码、私钥、API Token、完整 SSH 日志或真实设备配置。优先使用 GitHub Security Advisories；若仓库未启用该功能，请先提交不含敏感信息的 Issue，说明希望进行私下沟通。

## 凭据边界

`remote-dev` 只在本轮交互中读取 SSH、sudo 或 root 凭据，不将其写入 Inventory、日志、Git 或远端配置。真实 Inventory 和 `.local/` 文件必须保持本地并由 `.gitignore` 排除。

如果凭据曾经误提交，请立即撤销/轮换凭据，再联系维护者处理 Git 历史清理；仅删除工作区文件不足以修复泄露。
