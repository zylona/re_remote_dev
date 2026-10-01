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
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            connection.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
            response = bytearray()
            while not response.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
        value = json.loads(response.decode())
    except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"{command}: 无法连接本地预览桥：{exc}", file=sys.stderr)
        return 1
    if not isinstance(value, dict) or not value.get("ok"):
        print(f"{command}: {value.get('error', '请求失败') if isinstance(value, dict) else '请求失败'}", file=sys.stderr)
        return 1
    print(f"{command}: 已提交 {remote_path}（request_id={value.get('request_id', payload['request_id'])}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
