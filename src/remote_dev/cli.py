"""remote-dev 薄 CLI：校验配置并调用固定 Ansible 工作流。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import uuid
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from ruamel.yaml import YAML

app = typer.Typer(no_args_is_help=True, help="恢复和验证 SSH 远程开发环境")
orchestrator_app = typer.Typer(no_args_is_help=True, help="管理本地 SSH/Codex 编排器")
console = Console()
RUN_ID = uuid.uuid4().hex[:12]
_BOOTSTRAP_TARGET_DIGEST: str | None = None


def _detect_become_method(destination: str, identity_file: str) -> str:
    """Choose the installed escalation path without consuming a password."""
    probe = [
        "ssh", "-F", "/dev/null", "-i", str(Path(identity_file).expanduser()),
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", destination,
        "sh", "-c", "'if command -v sudo >/dev/null 2>&1; then echo sudo; elif command -v su >/dev/null 2>&1; then echo su; else echo unavailable; fi'",
    ]
    result = subprocess.run(probe, check=False, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "SSH 连接失败").strip().splitlines()[-1]
        raise RuntimeError(f"无法通过 SSH 探测目标提权能力：{detail}；请先确认用户名、私钥和 host key。")
    method = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "unavailable"
    if method not in {"sudo", "su"}:
        raise RuntimeError("目标机没有可用的 sudo 或 su 提权通道；请提供 root 密码并确认 su 可执行。")
    return method


def _detect_become_for_setup(destination: str, identity_file: str, auth_password: str | None = None) -> str:
    """Select sudo when installed and distinguish password-required from denial.

    ``sudo -n`` returns non-zero both when a password is needed and when the
    account is not authorized.  The former must still select sudo so Ansible
    can prompt for the user's sudo password; only an explicit policy denial
    falls back to ``su``.
    """
    command = ["ssh", "-F", "/dev/null", "-i", str(Path(identity_file).expanduser()), "-o", "ConnectTimeout=8", destination,
               "sh", "-c", "'if ! command -v sudo >/dev/null 2>&1; then command -v su >/dev/null 2>&1 && echo su || echo unavailable; elif sudo -n true >/dev/null 2> /tmp/remote-dev-sudo-probe; then echo sudo; elif grep -qiE \"not in the sudoers|not allowed\" /tmp/remote-dev-sudo-probe; then command -v su >/dev/null 2>&1 && echo su || echo unavailable; else echo sudo; fi; rm -f /tmp/remote-dev-sudo-probe'"]
    env = os.environ.copy()
    if auth_password is not None:
        command = ["sshpass", "-e", *command]
        env["SSHPASS"] = auth_password
    result = subprocess.run(command, check=False, capture_output=True, text=True, env=env, timeout=15)
    method = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "unavailable"
    if method not in {"sudo", "su"}:
        raise RuntimeError("目标机没有可用的 sudo 或 su 提权通道。")
    return method


def _load_inventory(path: Path) -> dict[str, Any]:
    yaml = YAML(typ="safe")
    with path.open(encoding="utf-8") as stream:
        data = yaml.load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError("Inventory 顶层必须是 YAML mapping")
    return data


def _log_event(event: str, **fields: Any) -> None:
    run_dir = Path(".local/runs") / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    (run_dir / "events.jsonl").chmod(0o600)


def _validate(path: Path) -> dict[str, Any]:
    data = _load_inventory(path)
    remote = data.get("all", {}).get("vars", {}).get("remote_dev")
    if not isinstance(remote, dict):
        raise ValueError("缺少 all.vars.remote_dev")
    for key in ("schema_version", "target_user", "ssh_private_key_file"):
        if not remote.get(key):
            raise ValueError(f"缺少 remote_dev.{key}")
    groups = data.get("all", {}).get("children", {}).get("remote_dev_targets", {})
    hosts = groups.get("hosts", {}) if isinstance(groups, dict) else {}
    if not hosts:
        raise ValueError("未配置 remote_dev_targets 主机")
    for name, host in hosts.items():
        if not isinstance(host, dict) or not host.get("ansible_host"):
            raise ValueError(f"主机 {name} 缺少 ansible_host")
    return data


def _run(playbook: str, inventory: Path, *, ask_become: bool = False, ask_pass: bool = False, extra_vars: dict[str, str] | None = None, env: dict[str, str] | None = None) -> None:
    command = ["ansible-playbook", "-i", str(inventory), playbook]
    if ask_become:
        command.append("--ask-become-pass")
    if ask_pass:
        command.append("--ask-pass")
    for key, value in (extra_vars or {}).items():
        command.extend(["-e", f"{key}={value}"])
    _log_event("ansible_start", playbook=playbook, inventory=str(inventory))
    result = subprocess.run(command, check=False, env=env)
    _log_event("ansible_finish", playbook=playbook, returncode=result.returncode)
    if result.returncode:
        raise typer.Exit(result.returncode)


def _install_local_orchestrator(*, confirm: bool = True, restore_flow: bool = True) -> None:
    """Install or refresh the local socket-activated user service."""
    heading = "阶段 1/2：本地编排器服务安装" if restore_flow else "本地编排器服务安装"
    console.print(f"[bold cyan]{heading}[/bold cyan]")
    if confirm and not typer.confirm("是否安装或刷新本机 tssh user 服务？", default=True):
        suffix = "，远程恢复流程也未执行" if restore_flow else ""
        console.print(f"[yellow]已取消：本地服务未安装{suffix}。[/yellow]")
        raise typer.Exit(0)

    project_root = Path(__file__).resolve().parents[2]
    service_dir = Path.home() / ".config/systemd/user"
    service_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    executable = project_root / "re-remote"
    if not executable.is_file():
        raise RuntimeError("未找到项目统一入口 re-remote，请确认仓库文件完整。")
    hook_source = project_root / "scripts/remote_dev_ssh_hook.py"
    hook_target = Path.home() / ".local/bin/remote-dev-ssh-hook"
    hook_target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    hook_target.write_text(hook_source.read_text(encoding="utf-8"), encoding="utf-8")
    hook_target.chmod(0o755)
    # Keep the source-tree installer behavior identical to the standalone
    # release installer: merge one marked, idempotent SSH integration block.
    from .ssh_integration import install as install_ssh

    ssh_config = install_ssh()
    console.print(f"[green]已清理旧版全局 SSH hook：{ssh_config}；VS Code 使用普通 SSH 配置[/green]")
    service = service_dir / "tssh.service"
    service_content = (project_root / "systemd/tssh.service").read_text(encoding="utf-8")
    service_content = service_content.replace("ExecStart=tssh-server", f"ExecStart={executable} orchestrator-server")
    service_changed = not service.exists() or service.read_text(encoding="utf-8") != service_content
    service.write_text(service_content, encoding="utf-8")
    service.chmod(0o600)
    socket_unit = service_dir / "tssh.socket"
    socket_content = (project_root / "systemd/tssh.socket").read_text(encoding="utf-8")
    socket_changed = not socket_unit.exists() or socket_unit.read_text(encoding="utf-8") != socket_content
    socket_unit.write_text(socket_content, encoding="utf-8")
    socket_unit.chmod(0o600)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "disable", "--now", "remote-dev-orchestrator.socket", "remote-dev-orchestrator.service"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", "tssh.socket"], check=True)
    service_active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "tssh.service"], check=False).returncode == 0
    if service_changed or socket_changed or service_active:
        subprocess.run(["systemctl", "--user", "restart", "tssh.service"], check=True)
        console.print("[cyan]本地编排器预检：已刷新服务进程，加载当前实现[/cyan]")
    else:
        console.print("[cyan]本地编排器预检：服务尚未运行，将由 socket activation 启动[/cyan]")
    from .orchestrator.client import request

    deadline = time.monotonic() + 15
    health: dict[str, Any] | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            health = request({"op": "health"}, timeout=2)
            break
        except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
            last_error = exc
            time.sleep(0.25)
    if health is None:
        raise RuntimeError(f"本地编排器启动后未能通过健康检查：{last_error}") from last_error
    if not health.get("ok"):
        raise RuntimeError(f"本地编排器健康检查失败：{health}")
    console.print(f"[green]本地编排器健康检查通过：pid={health.get('pid', 'unknown')}[/green]")
    console.print("[green]本地编排器服务阶段完成[/green]")


@app.command()
def config_validate(inventory: Path = typer.Option(Path("inventory/production.yml"), "--inventory", "-i")) -> None:
    """校验 Inventory，不连接目标机。"""
    try:
        _validate(inventory)
    except (OSError, ValueError) as exc:
        typer.echo(f"配置错误：{exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"配置有效：{inventory}")


@app.command()
def plan(inventory: Path = typer.Option(Path("inventory/production.yml"), "--inventory", "-i")) -> None:
    """显示固定执行计划。"""
    _validate(inventory)
    typer.echo("P0 preflight → P2 prerequisites → P3 user shell → P4 tools/workspace → verify")


@app.command()
def connect(
    destination: str = typer.Argument(..., help="SSH 目标，格式为 user@host 或 host"),
    identity: Path | None = typer.Option(None, "--identity", "-i", help="可选 SSH 私钥路径"),
    port: int = typer.Option(22, "--port", "-p"),
) -> None:
    """申请 endpoint 代理 lease，然后启动原生 SSH 会话。"""
    from .orchestrator.client import request

    if "@" in destination:
        user, host = destination.split("@", 1)
    else:
        user, host = "", destination
    if not host or not 1 <= port <= 65535:
        raise typer.BadParameter("目标或 SSH 端口无效")
    if not user:
        # Keep SSH's configured User behavior while using a stable protocol
        # identity for the lease; explicit user is recommended for ownership.
        user = os.environ.get("USER", "unknown")
    session_id = uuid.uuid4().hex
    candidate: dict[str, str] = {"user": user}
    if identity is not None:
        candidate["identity_file"] = str(identity.expanduser())
    endpoint = {"hostname": host, "port": port}
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None

    def heartbeat() -> None:
        # Keep the lease alive without touching the SSH PTY or its stdin/stdout.
        while not heartbeat_stop.wait(30.0):
            try:
                response = request({"op": "heartbeat", "endpoint": endpoint, "session_id": session_id}, timeout=5)
                if not response.get("ok"):
                    return
            except (OSError, TimeoutError, ValueError, ConnectionError):
                # A transient orchestrator restart must never interrupt the
                # native SSH process; release/fresh acquire handles recovery.
                continue

    try:
        acquired = request({"op": "acquire", "endpoint": endpoint, "candidate": candidate, "session_id": session_id}, timeout=5)
        if not acquired.get("ok"):
            raise RuntimeError(f"无法申请代理会话：{acquired.get('error', 'unknown')}")
        heartbeat_thread = threading.Thread(target=heartbeat, name="remote-dev-lease-heartbeat", daemon=True)
        heartbeat_thread.start()
        ssh_args = ["ssh"]
        if identity is not None:
            ssh_args.extend(["-i", str(identity.expanduser())])
        if port != 22:
            ssh_args.extend(["-p", str(port)])
        ssh_args.append(f"{user}@{host}")
        result = subprocess.run(ssh_args, check=False)
        raise typer.Exit(result.returncode)
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        try:
            request({"op": "release", "endpoint": endpoint, "session_id": session_id}, timeout=2)
        except (OSError, TimeoutError, ValueError):
            pass


@app.command("local-service-install")
def local_service_install(
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过交互确认"),
) -> None:
    """单独安装或刷新本地 systemd user 编排器服务。"""
    _install_local_orchestrator(confirm=not yes, restore_flow=False)
    console.print("[green]安装完成；服务已启用，并会随当前用户的 systemd user manager 运行。[/green]")


@app.command()
def apply(inventory: Path = typer.Option(Path("inventory/production.yml"), "--inventory", "-i")) -> None:
    """执行完整恢复流程；sudo 密码由 Ansible 在 TTY 交互读取。"""
    _validate(inventory)
    data = _validate(inventory)
    host_name, host = next(iter(data["all"]["children"]["remote_dev_targets"]["hosts"].items()))
    remote = data["all"]["vars"]["remote_dev"]
    destination = f"{host.get('ansible_user', remote['target_user'])}@{host['ansible_host']}"
    from .proxy import ProxySettings, ReverseProxyTunnel
    settings = ProxySettings(**{
        "mode": remote.get("proxy", {}).get("mode", "auto"),
        "host": remote.get("proxy", {}).get("host", "127.0.0.1"),
        "port": int(remote.get("proxy", {}).get("port", 4227)),
        "remote_port_min": int(remote.get("proxy", {}).get("remote_port_range", [40000, 60000])[0]),
        "remote_port_max": int(remote.get("proxy", {}).get("remote_port_range", [40000, 60000])[1]),
    })
    key = host.get("ansible_ssh_private_key_file", remote["ssh_private_key_file"])
    become_method = host.get("ansible_become_method") or _detect_become_method(destination, key)
    with ReverseProxyTunnel(
        settings=settings,
        destination=destination,
        identity_file=key,
        ssh_extra_args=(["-F", "/dev/null"] if __import__("os").environ.get("REMOTE_DEV_SSH_CONFIG") == "/dev/null" else []),
    ) as tunnel:
        # Ansible does not consistently expand ``~`` in connection variables
        # after a reset_connection.  Normalize the selected key once and pass
        # the absolute path as an extra var so every Play (including the
        # post-bootstrap reconnect) uses the same credential.
        extra = {"ansible_ssh_private_key_file": str(Path(key).expanduser())}
        if tunnel.process:
            extra["remote_dev_temp_proxy_url"] = tunnel.proxy_url
        if _BOOTSTRAP_TARGET_DIGEST:
            extra["codex_target_digest"] = _BOOTSTRAP_TARGET_DIGEST
        extra["ansible_become_method"] = become_method
        # The local managed SSH include forwards fixed port 4227 for ordinary
        # sessions; the apply tunnel uses a random port and must not inherit
        # that forwarding (otherwise the target sees a port collision).
        if tunnel.process:
            # Keep -F and its value in one token; Ansible's SSH argument
            # tokenizer otherwise drops the value and treats the next option
            # as the config path.
            extra["ansible_ssh_common_args"] = "-F/dev/null"
        console.print(f"[cyan]run {RUN_ID}[/cyan] applying {host_name} (become={become_method})")
        # Both sudo and su may require an interactive credential; Ansible owns
        # the prompt and never exposes it to command arguments or logs.
        _run("playbooks/site.yml", inventory, ask_become=True, extra_vars=extra)


@app.command()
def setup() -> None:
    """交互式恢复单台设备；凭据仅存在于本次运行。"""
    _install_local_orchestrator()
    console.print("[bold cyan]阶段 2/2：远程目标交互式恢复[/bold cyan]")
    host = typer.prompt("目标 IP 或主机名")
    login_user = typer.prompt("SSH 登录用户名")
    auth = typer.prompt("认证方式（key/password）", default="key").strip().lower()
    if auth not in {"key", "password"}:
        raise typer.BadParameter("认证方式必须是 key 或 password")
    key = "/dev/null"
    password: str | None = None
    if auth == "key":
        key = typer.prompt("私钥路径", default="~/.ssh/id_ed25519")
    else:
        if not __import__("shutil").which("sshpass"):
            raise typer.BadParameter("密码 SSH 需要控制端 sshpass；请先安装后重试")
        password = typer.prompt("SSH 密码", hide_input=True, confirmation_prompt=False)
    target_user = typer.prompt("最终配置用户名", default=login_user if login_user != "root" else "dev")
    target_password = typer.prompt("目标用户密码（可留空，使用 SSH 公钥）", default="", hide_input=True, confirmation_prompt=False)
    become_method = "su" if login_user == "root" else _detect_become_for_setup(destination=f"{login_user}@{host}", identity_file=key, auth_password=password)
    destination = f"{login_user}@{host}"
    # The remote Codex shim must use the same stable target digest as the
    # local orchestrator.  The temporary inventory name (``interactive``)
    # is not an identity and caused OAuth events to be discarded.
    from .orchestrator.model import TargetKey
    target_digest = TargetKey(host, 22, target_user).digest
    run_dir = Path(".local/runs") / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    inventory = run_dir / "interactive.yml"
    data = {
        "all": {
            "vars": {"remote_dev": {
                "schema_version": 1,
                "target_user": target_user,
                "target_user_password": target_password,
                "initial_user": login_user,
                "ssh_private_key_file": key,
                "profile": "default",
                "proxy": {"mode": "auto", "host": "127.0.0.1", "port": 4227, "remote_port_range": [40000, 60000]},
            }},
            "children": {"remote_dev_targets": {"hosts": {"interactive": {
                "ansible_host": host,
                "ansible_user": login_user,
            }}}},
        }
    }
    if password is not None:
        data["all"]["children"]["remote_dev_targets"]["hosts"]["interactive"]["ansible_password"] = password
    else:
        data["all"]["children"]["remote_dev_targets"]["hosts"]["interactive"]["ansible_ssh_private_key_file"] = key
    yaml = YAML()
    with inventory.open("w", encoding="utf-8") as stream:
        yaml.dump(data, stream)
    inventory.chmod(0o600)
    try:
        from .proxy import ProxySettings, ReverseProxyTunnel
        settings = ProxySettings()
        with ReverseProxyTunnel(settings=settings, destination=destination, identity_file=key, auth_password=password) as tunnel:
            extra = {
                "ansible_become_method": become_method,
                "ansible_ssh_common_args": "-F/dev/null",
                "codex_target_digest": target_digest,
            }
            if tunnel.process:
                extra["remote_dev_temp_proxy_url"] = tunnel.proxy_url
            if become_method == "sudo":
                console.print("[yellow]下一步 BECOME password 请填写 SSH 登录用户的 sudo 密码（不是 root 密码）。[/yellow]")
            else:
                console.print("[yellow]下一步 BECOME password 请填写 root 密码（su 提权）。[/yellow]")
            env = os.environ.copy()
            if password is not None:
                env["ANSIBLE_SSH_PASSWORD_MECHANISM"] = "sshpass"
            _run("playbooks/site.yml", inventory, ask_become=True, extra_vars=extra, env=env)
    finally:
        try:
            inventory.unlink()
            run_dir.rmdir()
        except OSError:
            pass


@app.command()
def bootstrap(inventory: Path = typer.Option(Path("inventory/production.yml"), "--inventory", "-i")) -> None:
    """一次性恢复远端、安装本地编排器并建立可复用 SSH 会话。"""
    data = _validate(inventory)
    host_name, host = next(iter(data["all"]["children"]["remote_dev_targets"]["hosts"].items()))
    remote = data["all"]["vars"]["remote_dev"]
    target_host = str(host["ansible_host"])
    target_user = str(remote["target_user"])
    key = Path(host.get("ansible_ssh_private_key_file", remote["ssh_private_key_file"])).expanduser()
    from .orchestrator.model import TargetKey
    from .orchestrator.master import MasterManager
    from .orchestrator.persistent import install as install_persistent
    from .ssh_integration import install as install_ssh, install_target
    _install_local_orchestrator()
    console.print("[bold cyan]阶段 2/2：远程目标恢复[/bold cyan]")
    target = TargetKey(target_host, int(host.get("ansible_port", 22)), target_user)
    existing_master = MasterManager().check(target)
    console.print(f"[cyan]目标连接预检：{'复用现有 ControlMaster' if existing_master else '未发现可复用 ControlMaster'}[/cyan]")
    global _BOOTSTRAP_TARGET_DIGEST
    _BOOTSTRAP_TARGET_DIGEST = target.digest
    try:
        apply(inventory)
    finally:
        _BOOTSTRAP_TARGET_DIGEST = None
    install_persistent(target, key)
    control_path = MasterManager().control_path(target)
    install_ssh()
    install_target(host=target_host, control_path=control_path, identity_file=key)
    console.print(f"[green]bootstrap 完成：{host_name}，digest={target.digest}，长期会话已启用[/green]")


@app.command()
def verify(inventory: Path = typer.Option(Path("inventory/production.yml"), "--inventory", "-i")) -> None:
    """执行只读验收。"""
    data = _validate(inventory)
    host = next(iter(data["all"]["children"]["remote_dev_targets"]["hosts"].values()))
    remote = data["all"]["vars"]["remote_dev"]
    key = Path(host.get("ansible_ssh_private_key_file", remote["ssh_private_key_file"])).expanduser()
    _run("playbooks/verify.yml", inventory, ask_become=True, extra_vars={
        "ansible_ssh_common_args": "-F/dev/null",
        "ansible_ssh_private_key_file": str(key),
    })


@app.command()
def doctor(inventory: Path = typer.Option(Path("inventory/production.yml"), "--inventory", "-i")) -> None:
    """区分配置、执行端和 Ansible 依赖问题。"""
    try:
        _validate(inventory)
    except (OSError, ValueError) as exc:
        typer.echo(f"CONFIG_ERROR: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo("CONFIG_OK")
    typer.echo("ANSIBLE_OK" if subprocess.run(["ansible-playbook", "--version"], check=False, stdout=subprocess.DEVNULL).returncode == 0 else "ANSIBLE_MISSING")


@app.command("ssh-integration-install")
def ssh_integration_install(
    proxy_port: int = typer.Option(4227, "--proxy-port"),
    enable_hook: bool = typer.Option(False, "--enable-hook", help="显式启用旧版全局 LocalCommand hook"),
) -> None:
    """迁移旧版 SSH 集成；默认不写入全局 hook。"""
    if not 1 <= proxy_port <= 65535:
        typer.echo("配置错误：代理端口必须位于 1-65535", err=True)
        raise typer.Exit(2)
    from .ssh_integration import install, install_legacy_hook

    config = install_legacy_hook(proxy_port=proxy_port) if enable_hook else install()
    typer.echo(f"SSH 配置已迁移：{config}")
    if enable_hook:
        typer.echo("已显式启用旧版全局 hook；它可能影响 VS Code、Git 和其他 SSH 客户端。")
    else:
        typer.echo("已移除全局 hook；普通 SSH、VS Code、Git、scp 和 rsync 保持原生连接。")


@app.command("ssh-integration-uninstall")
def ssh_integration_uninstall() -> None:
    """移除本机 remote-dev SSH 代理转发集成。"""
    from .ssh_integration import uninstall

    config = uninstall()
    typer.echo(f"SSH 代理集成已移除：{config}")
    typer.echo("受管标记区块和旧版外部片段已清理；用户自行配置的规则不会删除。")


@app.command("codex-login")
def codex_login(
    inventory: Path = typer.Option(Path("inventory/production.yml"), "--inventory", "-i"),
) -> None:
    """在远端启动官方 device-auth，并让用户用本机浏览器完成登录。"""
    data = _validate(inventory)
    host_name, host = next(iter(data["all"]["children"]["remote_dev_targets"]["hosts"].items()))
    remote = data["all"]["vars"]["remote_dev"]
    destination = f"{host.get('ansible_user', remote['target_user'])}@{host['ansible_host']}"
    key = host.get("ansible_ssh_private_key_file", remote["ssh_private_key_file"])
    from .proxy import ProxySettings, local_proxy_available
    proxy = ProxySettings(
        host=remote.get("proxy", {}).get("host", "127.0.0.1"),
        port=int(remote.get("proxy", {}).get("port", 4227)),
    )
    if not local_proxy_available(proxy):
        console.print(
            f"[red]本机 HTTP 代理 {proxy.host}:{proxy.port} 不可用，Codex 登录需要外网代理；"
            "请先启动代理后重试。[/red]"
        )
        raise typer.Exit(2)
    local_callback = False
    callback_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        callback_probe.bind(("127.0.0.1", 1455))
        local_callback = True
    except OSError:
        console.print("[yellow]本机 1455 已被占用，将复用已有 OAuth 回调转发。[/yellow]")
    finally:
        callback_probe.close()
    forward_options = [
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ControlMaster=no", "-o", "ControlPath=none",
        "-o", "RemoteForward=127.0.0.1:4227:127.0.0.1:4227",
    ]
    if local_callback:
        forward_options.extend(["-o", "LocalForward=127.0.0.1:1455:127.0.0.1:1455"])
    command = [
        "ssh", "-tt", "-i", str(Path(key).expanduser()),
        "-F", "/dev/null",
        *forward_options,
        destination,
        "sh", "-lc",
        f"export http_proxy=http://127.0.0.1:{proxy.port} https_proxy=http://127.0.0.1:{proxy.port} "
        "HTTP_PROXY=\"$http_proxy\" HTTPS_PROXY=\"$https_proxy\"; "
        "exec codex login --device-auth",
    ]
    console.print(f"[cyan]连接 {host_name}[/cyan]，请在本机浏览器打开远端输出的 URL 并输入设备码。")
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise typer.Exit(result.returncode)


@app.command("orchestrator-server", hidden=True)
def orchestrator_server() -> None:
    """供 systemd --user socket activation 调用的本地服务入口。"""
    from .orchestrator.server import run_server

    run_server()


@orchestrator_app.command("status")
def orchestrator_status() -> None:
    """显示编排器当前状态。"""
    from .orchestrator.client import request

    try:
        result = request({"op": "status"})
    except (OSError, TimeoutError, ValueError) as exc:
        typer.echo(f"ORCHESTRATOR_UNAVAILABLE: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@orchestrator_app.command("doctor")
def orchestrator_doctor() -> None:
    """检查本地编排器健康状态。"""
    from .orchestrator.client import request

    try:
        result = request({"op": "health"})
    except (OSError, TimeoutError, ValueError) as exc:
        typer.echo(f"ORCHESTRATOR_UNAVAILABLE: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False))


@orchestrator_app.command("stop")
def orchestrator_stop() -> None:
    """请求编排器退出，不关闭普通 SSH 会话。"""
    from .orchestrator.client import request

    try:
        result = request({"op": "shutdown"})
    except (OSError, TimeoutError, ValueError) as exc:
        typer.echo(f"ORCHESTRATOR_UNAVAILABLE: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False))


@orchestrator_app.command("connect")
def orchestrator_connect(
    host: str = typer.Argument(..., help="目标主机名或 IP"),
    user: str = typer.Argument(..., help="SSH 用户"),
    identity: Path = typer.Option(Path("~/.ssh/id_ed25519"), "--identity", "-i"),
    port: int = typer.Option(22, "--port"),
    persistent: bool = typer.Option(False, "--persistent", help="保持前台运行，供 systemd --user 使用"),
) -> None:
    """建立受管 ControlMaster、写入 nonce 并注册目标。"""
    from .orchestrator.model import TargetKey
    from .orchestrator.session import SessionManager

    try:
        session = SessionManager().start(TargetKey(host, port, user), identity)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        typer.echo(f"ORCHESTRATOR_CONNECT_FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps({"ok": True, "target": session.target.digest, "control_path": str(session.control_path)}))
    if persistent:
        SessionManager().serve(session)


@app.command("ssh-hook", hidden=True)
def ssh_hook(host: str, port: int = 22, user: str = "") -> None:
    """SSH LocalCommand hook: ensure a proxy-only target unit, then return."""
    if os.environ.get("REMOTE_DEV_SSH_HOOK") == "1" or not user:
        return
    from .orchestrator.model import TargetKey
    from .orchestrator.proxy_persistent import ensure
    # Resolve the same identity SSH will use.  BatchMode deliberately avoids
    # turning a normal interactive login into a second password prompt.
    probe = subprocess.run(["ssh", "-G", host], check=False, capture_output=True, text=True, timeout=5)
    identity = None
    resolved_host, resolved_user, resolved_port = host, user, port
    if probe.returncode == 0:
        for line in probe.stdout.splitlines():
            key, _, value = line.partition(" ")
            if key == "hostname": resolved_host = value.strip()
            elif key == "user" and not user: resolved_user = value.strip()
            elif key == "port": resolved_port = int(value.strip())
            elif key == "identityfile" and identity is None and value.strip() != "none": identity = Path(value.strip()).expanduser()
    if identity is None or not identity.exists():
        return
    target = TargetKey(resolved_host, resolved_port, resolved_user)
    try:
        ensure(target, identity)
    except (OSError, subprocess.SubprocessError, ValueError):
        # Never break the user's original SSH login because proxy setup failed.
        return


@orchestrator_app.command("enable-persistent")
def orchestrator_enable_persistent(
    host: str = typer.Argument(...), user: str = typer.Argument(...),
    identity: Path = typer.Option(Path("~/.ssh/id_ed25519"), "--identity", "-i"),
    port: int = typer.Option(22, "--port"),
) -> None:
    """显式启用 systemd --user 长期会话（默认不启用）。"""
    from .orchestrator.model import TargetKey
    from .orchestrator.persistent import install
    target = TargetKey(host, port, user)
    try:
        path = install(target, identity)
    except (OSError, subprocess.SubprocessError) as exc:
        typer.echo(f"PERSISTENT_ENABLE_FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"已启用长期会话：{path}")


@orchestrator_app.command("disable-persistent")
def orchestrator_disable_persistent(
    host: str = typer.Argument(...), user: str = typer.Argument(...),
    port: int = typer.Option(22, "--port"),
) -> None:
    """撤销 systemd --user 长期会话及其项目资源。"""
    from .orchestrator.model import TargetKey
    from .orchestrator.persistent import remove
    try:
        path = remove(TargetKey(host, port, user))
    except (OSError, subprocess.SubprocessError) as exc:
        typer.echo(f"PERSISTENT_DISABLE_FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"已禁用长期会话：{path}")


@orchestrator_app.command("disconnect")
def orchestrator_disconnect(
    host: str = typer.Argument(..., help="目标主机名或 IP"),
    user: str = typer.Argument(..., help="SSH 用户"),
    port: int = typer.Option(22, "--port"),
) -> None:
    """注销目标、清理 nonce 并关闭受管 ControlMaster。"""
    from .orchestrator.model import TargetKey
    from .orchestrator.session import ManagedSession, SessionManager

    manager = SessionManager()
    target = TargetKey(host, port, user)
    try:
        manager.stop(ManagedSession(target, manager.master.control_path(target), ""))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        typer.echo(f"ORCHESTRATOR_DISCONNECT_FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps({"ok": True, "target": target.digest}))


app.add_typer(orchestrator_app, name="orchestrator")


@app.command()
def uninstall(inventory: Path = typer.Option(Path("inventory/production.yml"), "--inventory", "-i")) -> None:
    """移除受管 Shell 配置并恢复首次备份。"""
    _validate(inventory)
    _run("playbooks/uninstall.yml", inventory)


if __name__ == "__main__":
    app()
