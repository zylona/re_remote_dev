from pathlib import Path

from remote_dev import preview_open


def test_markdown_prefers_configured_local_viewer(monkeypatch, tmp_path: Path):
    launched: list[list[str]] = []
    monkeypatch.setenv("RDO_MARKDOWN_APP", "typora --safe-mode")
    monkeypatch.setattr(preview_open.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(preview_open.subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    command = preview_open.open_local(tmp_path / "report.md")
    assert command == ["typora", "--safe-mode", str(tmp_path / "report.md")]
    assert launched == [command]


def test_missing_viewer_reports_downloaded_path(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("RDO_PDF_APP", raising=False)
    monkeypatch.setattr(preview_open.shutil, "which", lambda _name: None)
    try:
        preview_open.open_local(tmp_path / "report.pdf")
    except preview_open.PreviewOpenError as exc:
        assert str(tmp_path / "report.pdf") in str(exc)
    else:
        raise AssertionError("missing viewer was not reported")
