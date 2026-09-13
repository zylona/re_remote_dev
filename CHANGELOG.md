# Changelog

## Unreleased

- P2：代理转发改为独立 SSH forwarder，不再与普通终端共享 ControlMaster。
- P3：Codex 事件回传改为独立 SSH forwarder；编排器不再通过 ControlMaster 探测或补建事件转发，降低终端输入延迟和复用连接故障影响。
- P4：OAuth localhost:1455 回调改为独立短生命周期 SSH `-N -L` forwarder，避免登录期间操作 ControlMaster；完成或退出时自动清理 IPv4/IPv6 转发。
- P5：supervisor 现在同时监控代理/事件 forwarder 进程；任一转发异常都会按退避策略重建整组连接，避免单个隧道退出后状态假 READY。

## [0.1.6]

- 将代理、Codex 事件和 OAuth 回调全部隔离为独立 SSH forwarder，避免影响交互式 SSH 输入。
- 增加 forwarder 存活监控、自动恢复、资源限制和状态心跳。
- 修复 OAuth 回调转发清理及 forwarder 故障后的状态恢复。
- 完成 Debian、Arch/Omarchy、openEuler 实机回归，以及超过 30 分钟连续观察。

本项目遵循语义化版本号。每个 GitHub Release 同时提供源码快照、本地编排器制品和 SHA256 校验文件。

## [Unreleased]

- 清理公开文档中的真实测试地址、个人路径、用户名和私钥文件名。
- 新增 MIT License 和开源贡献说明。
- README 重构为面向使用者的快速开始、架构、安全边界、发布和故障排查指南。
- 本地编排器发布包携带许可证文件，便于下游项目合规分发。

## [0.1.5]

- 修复 systemd socket activation 下 Unix socket 无法接受连接的问题。
- 恢复本地编排器的 Codex 生命周期事件、OAuth 浏览器启动和状态查询链路。

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
