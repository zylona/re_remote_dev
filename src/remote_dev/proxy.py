"""SSH 临时反向 HTTP 代理的生命周期工具。

该模块只管理隧道，不执行 Ansible task，也不读取或保存任何凭据。
"""

from __future__ import annotations

import secrets
import shlex
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ProxySettings:
    mode: str = "auto"
    host: str = "127.0.0.1"
    port: int = 4227
    remote_port_min: int = 40000
    remote_port_max: int = 60000

    def validate(self) -> None:
        if self.mode not in {"auto", "required", "off"}:
            raise ValueError("代理 mode 必须是 auto、required 或 off")
        if not self.host or not 1 <= self.port <= 65535:
            raise ValueError("代理 host/port 无效")
        if not 1024 <= self.remote_port_min <= self.remote_port_max <= 65535:
            raise ValueError("远端临时端口范围无效")


def local_proxy_available(settings: ProxySettings, timeout: float = 0.5) -> bool:
    """只读探测执行端 HTTP 代理是否监听。"""

    settings.validate()
    try:
        with socket.create_connection((settings.host, settings.port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def choose_remote_port(settings: ProxySettings) -> int:
    """随机选择本轮远端端口；最终占用检查由 SSH 的 ExitOnForwardFailure 完成。"""

    settings.validate()
    return secrets.SystemRandom().randint(settings.remote_port_min, settings.remote_port_max)


def ssh_reverse_forward_args(settings: ProxySettings, remote_port: int) -> list[str]:
    """生成可拼接到 OpenSSH 命令的安全参数。"""

    settings.validate()
    if not settings.remote_port_min <= remote_port <= settings.remote_port_max:
        raise ValueError("remote_port 不在声明的随机端口范围内")
    return [
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-R", f"127.0.0.1:{remote_port}:{settings.host}:{settings.port}",
    ]


class ReverseProxyTunnel:
    """为一个 Ansible 运行提供独立 SSH -R 隧道。"""

    def __init__(
        self,
        *,
        settings: ProxySettings,
        destination: str,
        identity_file: str | Path,
        auth_password: str | None = None,
        ssh_bin: str = "ssh",
        remote_port: int | None = None,
        ssh_extra_args: Sequence[str] = (),
    ) -> None:
        self.settings = settings
        self.destination = destination
        self.identity_file = str(identity_file)
        self.auth_password = auth_password
        self.ssh_bin = ssh_bin
        self.remote_port = remote_port or choose_remote_port(settings)
        self.ssh_extra_args = list(ssh_extra_args)
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.remote_port}"

    def command(self) -> list[str]:
        command = [self.ssh_bin,
            "-N",
            "-T",
            "-F", "/dev/null",
            *self.ssh_extra_args,
            *ssh_reverse_forward_args(self.settings, self.remote_port),
            self.destination,
        ]
        if self.identity_file and self.identity_file != "/dev/null":
            command[1:1] = ["-i", self.identity_file]
        if self.auth_password is not None:
            command = ["sshpass", "-e", *command]
        return command

    def start(self) -> None:
        self.settings.validate()
        if self.settings.mode == "off":
            return
        if not local_proxy_available(self.settings):
            if self.settings.mode == "required":
                raise RuntimeError("执行端临时 HTTP 代理不可用")
            return
        env = None
        if self.auth_password is not None:
            import os
            env = os.environ.copy()
            env["SSHPASS"] = self.auth_password
        self.process = subprocess.Popen(
            self.command(), stdin=subprocess.DEVNULL, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        # SSH 连接失败时立即报告；保持进程运行表示 -R 已通过 ExitOnForwardFailure。
        try:
            self.process.wait(timeout=0.8)
        except subprocess.TimeoutExpired:
            return
        detail = (self.process.stderr.read() if self.process.stderr else b"").decode(errors="replace").strip()
        raise RuntimeError(f"SSH 临时代理建立失败：{detail or 'ssh 已退出'}")

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)

    def __enter__(self) -> "ReverseProxyTunnel":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def shell_preview(command: Sequence[str]) -> str:
    """用于 debug 的脱敏命令预览；调用者不得传入密码参数。"""

    return shlex.join(command)
