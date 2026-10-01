"""Local application selection for completed remote preview files."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path


class PreviewOpenError(RuntimeError):
    """No suitable local viewer is available."""


def _configured(name: str) -> list[str] | None:
    value = os.environ.get(name, "").strip()
    return shlex.split(value) if value else None


def _candidates(path: Path) -> list[list[str] | None]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".mdown", ".mkd"}:
        return [_configured("RDO_MARKDOWN_APP"), ["typora"], ["xdg-open"]]
    if suffix == ".pdf":
        return [_configured("RDO_PDF_APP"), ["zathura"], ["evince"], ["xdg-open"]]
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
        return [_configured("RDO_IMAGE_APP"), ["imv"], ["nsxiv"], ["xdg-open"]]
    return [_configured("RDO_DEFAULT_APP"), ["xdg-open"]]


def open_local(path: Path) -> list[str]:
    """Launch the first installed viewer and return its argv."""
    for candidate in _candidates(path):
        if not candidate or shutil.which(candidate[0]) is None:
            continue
        command = [*candidate, str(path)]
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            continue
        return command
    raise PreviewOpenError(f"未找到可打开 {path.suffix or '该文件'} 的本地查看器；文件已下载到：{path}")
