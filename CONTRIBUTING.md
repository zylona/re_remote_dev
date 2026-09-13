# Contributing

感谢参与 `remote-dev`。提交修改前请先阅读根目录 `AGENTS.md`，并确认没有把真实 Inventory、SSH 配置、密码、Token、私钥或运行报告加入 Git。

## 本地检查

```bash
uv sync --locked
./scripts/release_check.sh
git diff --check
```

功能修改应同时包含 Ansible apply、只读 verify、自动化测试和必要的用户文档。远端行为必须保持幂等；涉及 SSH 转发、systemd 或发行版差异时，请说明实际验证平台和未覆盖风险。

## 分支和发布

- `dev` 用于开发、调研和集成验证；
- `master` 只接受经过审阅的可发布变更；
- Release 仅从 `master` 的版本 tag 构建；
- 不要在 Pull Request 中上传任何真实凭据或生产 Inventory。
