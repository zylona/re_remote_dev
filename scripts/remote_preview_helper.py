#!/usr/bin/env python3
"""Remote-session rget/rdo client for the tssh preview bridge."""

from __future__ import annotations

import json
import os
import posixpath
import socket
import sys
import uuid


def _usage(command: str) -> None:
    print(f"用法：{command} PATH", file=sys.stderr)
    print("PATH 可为远程绝对路径或相对于当前目录的路径。", file=sys.stderr)


def _context() -> tuple[str, str, str, int] | None:
    values = (os.environ.get("RDO_ENDPOINT"), os.environ.get("RDO_SESSION"), os.environ.get("RDO_NONCE"), os.environ.get("RDO_PORT"))
    if not all(values):
        return None
    try:
        port = int(values[3])
    except (TypeError, ValueError):
        return None
    if not 1 <= port <= 65535:
        return None
    return values[0], values[1], values[2], port


def _canonical_path(value: str) -> str:
    if not value or "\x00" in value or value.startswith("~"):
        raise ValueError("路径不能为空、不能含 NUL，也不支持未展开的 ~")
    cwd = os.getcwd()
    if not cwd.startswith("/"):
        raise ValueError("当前远程目录不是绝对路径")
    return posixpath.normpath(value if value.startswith("/") else posixpath.join(cwd, value))


def _format_bytes(value: object) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{amount:.0f} B"
        amount /= 1024
    return "0 B"


def _print_progress(command: str, value: dict[str, object], *, final: bool = False) -> None:
    done = value.get("bytes_done", 0)
    total = value.get("bytes_total", 0)
    try:
        done_number = int(done or 0)
        total_number = int(total or 0)
    except (TypeError, ValueError):
        done_number = total_number = 0
    if total_number > 0:
        ratio = min(1.0, max(0.0, done_number / total_number))
        width = 28
        filled = int(width * ratio)
        bar = "#" * filled + "-" * (width - filled)
        percent = f"{ratio * 100:5.1f}%"
        progress = f"[{bar}] {percent} {_format_bytes(done_number)}/{_format_bytes(total_number)}"
    else:
        progress = f"{_format_bytes(done_number)}"
    speed = _format_bytes(value.get("speed_bps", 0)) + "/s"
    eta = value.get("eta_seconds")
    try:
        eta_text = f" ETA {float(eta):.1f}s" if eta is not None else ""
    except (TypeError, ValueError):
        eta_text = ""
    text = f"{command}: {progress} {speed}{eta_text}"
    line_mode = os.environ.get("RDO_PROGRESS_FORMAT") == "lines"
    prefix = "" if line_mode else "\r"
    suffix = "\n" if final or line_mode else "\x1b[K"
    print(prefix + text + suffix, end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = os.path.basename(sys.argv[0])
    operation = "open" if command == "rdo" else "get"
    if args and args[0] in {"--help", "-h"}:
        _usage(command)
        return 0
    if len(args) != 1:
        _usage(command)
        return 2
    context = _context()
    if context is None:
        print("当前 SSH 会话没有可用的 tssh 预览桥。请使用 tssh 重新连接。", file=sys.stderr)
        return 1
    try:
        remote_path = _canonical_path(args[0])
    except ValueError as exc:
        print(f"rdo/rget: {exc}", file=sys.stderr)
        return 2
    endpoint, session, nonce, port = context
    payload = {
        "operation": operation,
        "remote_path": remote_path,
        "endpoint_digest": endpoint,
        "session_id": session,
        "nonce": nonce,
        "request_id": uuid.uuid4().hex,
    }
    final_value: dict[str, object] | None = None
    last_value: dict[str, object] | None = None
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            connection.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
            buffer = bytearray()
            announced = False
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                while b"\n" in buffer:
                    raw, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    value = json.loads(raw.decode())
                    if not isinstance(value, dict) or not value.get("ok"):
                        print(f"{command}: {value.get('error', '请求失败') if isinstance(value, dict) else '请求失败'}", file=sys.stderr)
                        return 1
                    last_value = value
                    if not announced:
                        request_id = value.get("request_id", payload["request_id"])
                        print(f"{command}: 已提交并开始传输 {remote_path}", flush=True)
                        print(f"      request_id：{request_id}", flush=True)
                        announced = True
                    event = value.get("event")
                    if event == "progress":
                        _print_progress(command, value)
                    elif event == "complete":
                        final_value = value
                        _print_progress(command, value, final=True)
                        break
                if final_value is not None:
                    break
            # Compatibility with an older bridge that only sends one
            # acknowledgement and closes the socket.  The current bridge
            # keeps the connection open until the worker reaches a terminal
            # state, so this is only a fallback.
            if final_value is None:
                final_value = last_value
    except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"{command}: 无法连接本地预览桥：{exc}", file=sys.stderr)
        return 1
    if final_value is None:
        print(f"{command}: 本地预览桥提前关闭连接", file=sys.stderr)
        return 1
    state_labels = {
        "CACHED": "缓存命中",
        "READY": "下载完成",
        "READY_WITH_WARNING": "下载完成（打开器有提示）",
        "FAILED": "下载失败",
    }
    state = final_value.get("state")
    if state in {"QUEUED", "RUNNING"} and final_value.get("event") != "complete":
        print(f"{command}: 预览桥在传输完成前关闭连接（状态：{state}）。请刷新本地 tssh 服务后重试。", file=sys.stderr)
        return 1
    if state == "FAILED":
        print(f"{command}: {final_value.get('error', '下载失败')}", file=sys.stderr)
        return 1
    print(f"      状态：{state_labels.get(state, state or '已完成')}")
    if final_value.get("local_path"):
        print(f"      存储路径：{final_value['local_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
