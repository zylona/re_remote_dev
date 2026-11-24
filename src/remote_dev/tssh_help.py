"""Human-oriented tssh help rendered with Rich when available."""

from __future__ import annotations


TITLE = "tssh"
SUBTITLE = "带会话级代理转发的 SSH；普通 ssh 和 VS Code Remote-SSH 保持独立。"

COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("连接", "tssh user@host", "连接目标并按需复用 4227/4228 转发"),
    ("恢复", "tssh --setup", "交互式恢复远程开发环境"),
    ("状态", "tssh --list", "查看活跃 endpoint、会话及真实转发端口"),
    ("下载状态", "tssh --downloads", "查看 rget / rdo 下载状态"),
    ("上传", "tssh --put [-r] SOURCE... user@host", "上传文件或目录到 ~/Uploads/remote-dev"),
    ("持久代理", "tssh --persist user@host", "在本次开机周期保持代理"),
    ("停止代理", "tssh --stop user@host", "停止指定目标的持久代理"),
    ("清理", "tssh --cleanup", "清理本机记录的代理会话"),
    ("回收", "tssh --reclaim user@host", "扫描并安全回收已识别的残留转发"),
    ("代理设置", "tssh --proxy [show|set HOST PORT]", "读取或设置代理来源；不探测连通性"),
    ("版本", "tssh --version", "显示安装版本"),
)

EXAMPLES: tuple[tuple[str, str], ...] = (
    ("使用 SSH config 中的别名", "tssh work-vm"),
    ("覆盖 SSH 端口和密钥", "tssh -p 30112 -i ~/.ssh/work_key zylona@example.net"),
    ("恢复目标环境", "tssh --setup"),
    ("设置本机代理入口", "tssh --proxy set 127.0.0.1 4227"),
    ("递归上传目录", "tssh --put -r ./project zylona@work-vm"),
    ("检查并回收残留转发", "tssh --reclaim zylona@work-vm --dry-run"),
)

PRECONDITIONS = (
    "目标用户必须已存在、能通过 SSH 登录并具有 sudo 权限；tssh --setup 不创建或修改账号。",
    "省略 -p/-i 时遵循 OpenSSH 配置和 ssh-agent；显式参数覆盖对应值。",
    "普通 ssh 和 VS Code Remote-SSH 不触发 tssh 编排。",
    "代理默认是 127.0.0.1:4227。代理连通性由用户负责，tssh 不做探测。",
    "远端文件预览：登录后使用 rget 下载，或使用 rdo 下载并打开本地文件。",
)

ACCOUNT_SETUP = (
    ("Debian / Ubuntu", "sudo adduser USER\nsudo usermod -aG sudo USER"),
    ("RHEL / Rocky / AlmaLinux / openEuler", "sudo useradd -m -s /bin/bash USER\nsudo passwd USER\nsudo usermod -aG wheel USER\nsudo visudo  # 启用 %wheel ALL=(ALL:ALL) ALL"),
    ("Arch Linux", "sudo useradd -m -s /bin/bash USER\nsudo passwd USER\nsudo usermod -aG wheel USER\nsudo visudo  # 启用 %wheel ALL=(ALL:ALL) ALL"),
)


def print_help(*, color: bool | None = None) -> None:
    """Print the styled help page; Rich is loaded only for help requests."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text
    except ImportError:  # Defensive fallback for a damaged/partial installation.
        print(_plain_help())
        return

    console = Console(force_terminal=color)
    console.print(Panel.fit(
        f"[bold cyan]{TITLE}[/bold cyan]\n[white]{SUBTITLE}[/white]",
        border_style="bright_blue",
        padding=(1, 2),
    ))

    commands = Text()
    for name, command, summary in COMMANDS:
        commands.append(f"{name}  ", style="bold cyan")
        commands.append(f"{command}\n", style="bright_green")
        commands.append(f"    {summary}\n\n")
    console.print(Panel(commands, title="命令", border_style="blue", padding=(1, 2)))

    examples = Text()
    for label, example in EXAMPLES:
        examples.append(f"{label}  ", style="cyan")
        examples.append(f"{example}\n")
    console.print(Panel(examples, title="快速开始", border_style="blue", padding=(0, 1)))

    accounts = Text()
    for platform, commands_text in ACCOUNT_SETUP:
        accounts.append(f"{platform}\n", style="bold cyan")
        accounts.append(f"  {commands_text.replace(chr(10), chr(10) + '  ')}\n\n", style="bright_green")
    accounts.append("验证权限\n", style="bold cyan")
    accounts.append("  su - USER\n  sudo -v\n  sudo whoami  # 应输出 root", style="bright_green")
    console.print(Panel(accounts, title="Setup 前置：准备已有 sudo 用户", border_style="yellow", padding=(0, 1)))

    notes = Text()
    for index, line in enumerate(PRECONDITIONS):
        if index:
            notes.append("\n")
        notes.append("• ", style="cyan")
        notes.append(line)
    console.print(Panel(notes, title="使用前须知", border_style="dim", padding=(1, 2)))
    console.print("[dim]详细参数：tssh --put --help、tssh --reclaim --help。普通 SSH 命令不经过 tssh 管理命令。[/dim]")


def _plain_help() -> str:
    lines = [TITLE, SUBTITLE, "", "命令："]
    lines.extend(f"  {command:<48} {summary}" for _, command, summary in COMMANDS)
    lines.extend(("", "快速开始："))
    lines.extend(f"  {label:<24} {example}" for label, example in EXAMPLES)
    lines.extend(("", "Setup 前置：准备已有 sudo 用户"))
    lines.extend(f"  {platform}:\n    {commands_text.replace(chr(10), chr(10) + '    ')}" for platform, commands_text in ACCOUNT_SETUP)
    lines.extend(("  验证权限：", "    su - USER", "    sudo -v", "    sudo whoami  # 应输出 root"))
    lines.extend(("", "使用前须知："))
    lines.extend(f"  • {line}" for line in PRECONDITIONS)
    return "\n".join(lines)
