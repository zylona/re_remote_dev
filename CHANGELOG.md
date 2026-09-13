# Changelog

本项目遵循语义化版本号。每个 GitHub Release 同时提供源码快照、本地编排器制品和 SHA256 校验文件。

## [Unreleased]

- 清理公开文档中的真实测试地址、个人路径、用户名和私钥文件名。
- 新增 MIT License 和开源贡献说明。
- README 重构为面向使用者的快速开始、架构、安全边界、发布和故障排查指南。
- 本地编排器发布包携带许可证文件，便于下游项目合规分发。

## [0.1.4]

- 修复通过 `~/.local/bin` 符号链接直接启动编排器时无法定位内置 Python 包的问题。

## [0.1.1]

- 发布可独立安装的 `remote-dev-orchestrator` Linux tarball。
- 增加 systemd user socket activation、ControlMaster 复用和隧道故障恢复。
- 完成 Debian、openEuler、Arch Linux 测试机上的 Ansible 恢复与 verify 回归。

## 发布说明模板

每次发布时请在 GitHub Release 中说明：

1. 版本号和兼容范围；
2. 用户可见的新增、修复和已知限制；
3. 制品文件及 SHA256 校验方式；
4. 是否需要重新运行远端 bootstrap；
5. CI 检查结果和未覆盖的平台。
