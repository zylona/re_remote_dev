#!/usr/bin/env python3
"""Run Codex in a PTY and forward only its localhost OAuth URL."""
from __future__ import annotations
import os, pty, re, select, subprocess, sys, termios, tty, struct, fcntl

if len(sys.argv) < 5:
    raise SystemExit(2)
sender, target, nonce, real = sys.argv[1:5]
args = sys.argv[5:]
# Codex renders the link through a TUI and may insert ANSI/OSC hyperlink
# sequences or hard-wrap it at the terminal width.  Normalize those bytes
# before matching so the callback URL is not lost at a visual line break.
url_re = re.compile(rb"https://auth\.openai\.com/[^\x00-\x20\x1b]+|http://localhost:1455/[^\x00-\x20\x1b]+")
# Fallback for TUI redraws that insert cursor/formatting bytes inside a URL;
# the callback marker and originator delimit a complete Codex authorize URL.
wrapped_url_re = re.compile(rb"https://auth\.openai\.com/.*?localhost%3A1455.*?originator=[A-Za-z0-9._~-]+", re.S)
ansi_re = re.compile(rb"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~])")
pid, fd = pty.fork()
if pid == 0:
    os.execv(real, [real, *args])
seen: set[str] = set()
url_buffer = b""
old = termios.tcgetattr(0) if os.isatty(0) else None
if old:
    tty.setraw(0)
    try:
        size = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, size)
    except OSError:
        pass
try:
  while True:
    sources = [fd] + ([0] if old else [])
    ready, _, _ = select.select(sources, [], [], 0.2)
    if old and 0 in ready:
        try:
            user_data = os.read(0, 8192)
            if user_data:
                os.write(fd, user_data)
        except OSError:
            pass
    if fd in ready:
        try:
            data = os.read(fd, 8192)
        except OSError:
            data = b""
        if data:
            os.write(1, data)
            url_buffer = (url_buffer + data).replace(b"\r", b"").replace(b"\n", b"")
            url_buffer = ansi_re.sub(b"", url_buffer).replace(b" ", b"").replace(b"\t", b"")[-16384:]
            # TUI redraws can repeat the authorize link.  Keep only the most
            # recent URL start so fragments from an earlier redraw cannot be
            # concatenated into an invalid request.
            latest = url_buffer.rfind(b"https://auth.openai.com/")
            if latest > 0:
                url_buffer = url_buffer[latest:]
            matches = url_re.findall(url_buffer) + wrapped_url_re.findall(url_buffer)
            for match in matches:
                value = match.decode("ascii", "ignore")
                # Codex wraps long URLs in the terminal.  Do not launch a
                # truncated prefix; wait until the encoded localhost callback
                # marker is present in the buffered URL.
                # Seeing the callback marker alone is not enough: the TUI
                # prints the URL in several chunks and the first chunk can
                # end at ``offline_``.  Wait for Codex's final originator
                # parameter, which is emitted only after the full URL.
                if "auth.openai.com" in value and (
                    "localhost%3A1455" not in value or "originator=codex-tui" not in value
                ):
                    continue
                if value not in seen:
                    seen.add(value)
                    subprocess.run([sys.executable, sender, "CODEX_OAUTH_URL", target, nonce, value], check=False)
                    url_buffer = b""
    try:
        result_pid, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        break
    if result_pid == pid:
        raise SystemExit(os.waitstatus_to_exitcode(status))
finally:
    if old:
        termios.tcsetattr(0, termios.TCSADRAIN, old)
