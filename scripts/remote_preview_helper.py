#!/usr/bin/env python3
"""Remote-session rget/rdo client for the tssh preview bridge."""

from __future__ import annotations

import glob
import json
import os
import posixpath
import socket
import sys
import uuid


def _usage(command: str) -> None:
    if command == "rget":
        print("用法：rget [-r|--recursive] PATH...", file=sys.stderr)
        print("支持多个文件、远端通配符；目录必须使用 -r。", file=sys.stderr)
        print("PATH 可为远程绝对路径或相对于当前目录的路径；加引号的通配符由 rget 展开。", file=sys.stderr)
    else:
        print("用法：rdo PATH", file=sys.stderr)
        print("PATH 可为远程绝对路径或相对于当前目录的单个文件路径。", file=sys.stderr)


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


def _expand_remote_sources(raw_paths: list[str], *, recursive: bool, cwd: str | None = None) -> list[str]:
    """Expand current-host paths/globs for a multi-file rget request."""
    root = cwd or os.getcwd()
    if not root.startswith("/"):
        raise ValueError("当前远程目录不是绝对路径")
    selected: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        if not raw or "\x00" in raw or raw.startswith("~"):
            raise ValueError("路径不能为空、不能含 NUL，也不支持未展开的 ~")
        pattern = posixpath.normpath(raw if raw.startswith("/") else posixpath.join(root, raw))
        matches = sorted(glob.glob(pattern, recursive=True)) if glob.has_magic(pattern) else [pattern]
        if not matches:
            raise ValueError(f"远程路径或通配符没有匹配项：{raw}")
        for match in matches:
            match = posixpath.normpath(match)
            if os.path.isdir(match):
                if not recursive:
                    raise ValueError(f"{raw} 是目录；递归下载请使用 -r/--recursive")
                for directory, subdirs, filenames in os.walk(match, followlinks=False):
                    subdirs.sort()
                    for filename in sorted(filenames):
                        candidate = posixpath.normpath(posixpath.join(directory, filename))
                        if os.path.isfile(candidate) and candidate not in seen:
                            selected.append(candidate)
                            seen.add(candidate)
            elif os.path.isfile(match) or not glob.has_magic(pattern):
                if match not in seen:
                    selected.append(match)
                    seen.add(match)
            else:
                raise ValueError(f"远程路径不存在或不是普通文件/目录：{raw}")
    if not selected:
        raise ValueError("没有找到可下载的普通文件")
    return selected


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


def _request_one(command: str, operation: str, remote_path: str, context: tuple[str, str, str, int]) -> tuple[int, dict[str, object] | None]:
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
                        return 1, None
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
        return 1, None
    if final_value is None:
        print(f"{command}: 本地预览桥提前关闭连接", file=sys.stderr)
        return 1, None
    state_labels = {
        "CACHED": "缓存命中",
        "READY": "下载完成",
        "READY_WITH_WARNING": "下载完成（打开器有提示）",
        "FAILED": "下载失败",
    }
    state = final_value.get("state")
    if state in {"QUEUED", "RUNNING"} and final_value.get("event") != "complete":
        print(f"{command}: 预览桥在传输完成前关闭连接（状态：{state}）。请刷新本地 tssh 服务后重试。", file=sys.stderr)
        return 1, final_value
    if state == "FAILED":
        print(f"{command}: {final_value.get('error', '下载失败')}", file=sys.stderr)
        return 1, final_value
    print(f"      状态：{state_labels.get(state, state or '已完成')}")
    if final_value.get("local_path"):
        print(f"      存储路径：{final_value['local_path']}")
    return 0, final_value


def _parse_rget_args(args: list[str]) -> tuple[bool, list[str]]:
    recursive = False
    paths: list[str] = []
    options = True
    for value in args:
        if options and value == "--":
            options = False
        elif options and value in {"-r", "--recursive"}:
            recursive = True
        elif options and value.startswith("-"):
            raise ValueError(f"未知选项：{value}")
        else:
            paths.append(value)
    if not paths:
        raise ValueError("至少需要一个 PATH")
    return recursive, paths


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = os.path.basename(sys.argv[0])
    operation = "open" if command == "rdo" else "get"
    if args and args[0] in {"--help", "-h"}:
        _usage(command)
        return 0
    if command == "rdo":
        if len(args) != 1:
            _usage(command)
            return 2
        try:
            remote_paths = [_canonical_path(args[0])]
        except ValueError as exc:
            print(f"rdo: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            recursive, raw_paths = _parse_rget_args(args)
            remote_paths = _expand_remote_sources(raw_paths, recursive=recursive)
        except ValueError as exc:
            print(f"rget: {exc}", file=sys.stderr)
            _usage(command)
            return 2

    context = _context()
    if context is None:
        print("当前 SSH 会话没有可用的 tssh 预览桥。请使用 tssh 重新连接。", file=sys.stderr)
        return 1
    total = len(remote_paths)
    for index, remote_path in enumerate(remote_paths, 1):
        if total > 1:
            print(f"{command}: [{index}/{total}] {remote_path}", flush=True)
        status, _ = _request_one(command, operation, remote_path, context)
        if status:
            if total > 1:
                print(
                    f"{command}: 在第 {index}/{total} 个文件处停止；"
                    "此前已完成的文件仍保存在下载缓存中。",
                    file=sys.stderr,
                )
            return status
    if command == "rget" and total > 1:
        print(f"rget: 已完成 {total} 个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
