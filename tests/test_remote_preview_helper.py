from __future__ import annotations

from pathlib import Path

import pytest

from scripts.remote_preview_helper import _expand_remote_sources, _parse_rget_args


def test_rget_parses_multiple_paths_recursive_and_dash_path():
    recursive, paths = _parse_rget_args(["-r", "--", "-generated.txt", "a.txt"])

    assert recursive is True
    assert paths == ["-generated.txt", "a.txt"]


def test_rget_expands_glob_and_recursive_directory(tmp_path: Path):
    tree = tmp_path / "tree"
    nested = tree / "nested"
    nested.mkdir(parents=True)
    (tree / "a.md").write_text("a", encoding="utf-8")
    (nested / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "one.txt").write_text("one", encoding="utf-8")
    (tmp_path / "two.txt").write_text("two", encoding="utf-8")

    selected = _expand_remote_sources(
        ["tree", "*.txt", "tree/**/*.md"], recursive=True, cwd=str(tmp_path)
    )

    assert selected == [
        str(tree / "a.md"),
        str(nested / "b.md"),
        str(tmp_path / "one.txt"),
        str(tmp_path / "two.txt"),
    ]


def test_rget_requires_recursive_for_directory(tmp_path: Path):
    directory = tmp_path / "folder"
    directory.mkdir()
    (directory / "file.txt").write_text("content", encoding="utf-8")

    with pytest.raises(ValueError, match="递归下载请使用"):
        _expand_remote_sources(["folder"], recursive=False, cwd=str(tmp_path))


def test_rget_rejects_unmatched_glob(tmp_path: Path):
    with pytest.raises(ValueError, match="没有匹配项"):
        _expand_remote_sources(["missing-*.txt"], recursive=False, cwd=str(tmp_path))
