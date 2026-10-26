# Copyright (c) 2026 Hans Valerie
"""Check repository text quality without rejecting legitimate Unicode."""

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.check_repository import check_repository, check_text, git_text_files, main


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Plain ASCII\n",
        "Selamat pagi! Pengujian gambar berjalan dengan baik.\n",
        "café, déjà vu, naïve, São Paulo, Ângela, Straße\n",
        "日本語 한국어 العربية हिन्दी\n",
        "Images 🖼️ ready 🚀 — healthy ✓\n",
        "Standalone Ã, Â, â, ð, and © are legitimate.\n",
        "Math: x \u00d7 y, 10 ± 2, 90°\n",
    ],
)
def test_valid_unicode_is_preserved(text: str) -> None:
    assert check_text(text.encode("utf-8")) == []


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"invalid \xff\n", "invalid UTF-8"),
        (b"truncated \xe2\x82\n", "invalid UTF-8"),
        (b"\xef\xbb\xbfhello\n", "BOM"),
        ("one\ufefftwo\n".encode(), "BOM"),
        ("bad \ufffd data\n".encode(), "replacement character"),
        ("bad \u0085 data\n".encode(), "C1 control"),
        ("bad \u009f data\n".encode(), "C1 control"),
        (b"hello\r\n", "LF"),
        (b"hello\rworld\n", "LF"),
        (b"missing newline", "final newline"),
        (b"extra space \n", "trailing whitespace"),
        (b"extra tab\t\n", "trailing whitespace"),
    ],
)
def test_invalid_text_is_rejected(data: bytes, message: str) -> None:
    assert any(message in issue for issue in check_text(data))


@pytest.mark.parametrize("text", ["café", "—", "🚀", "©", "\ufeff", "\ufffd"])
@pytest.mark.parametrize("encoding", ["cp1252", "latin1"])
def test_reversible_corruption_is_rejected(text: str, encoding: str) -> None:
    corrupted = text.encode("utf-8").decode(encoding)
    assert check_text(f"mixed 日本語 {corrupted}\n".encode())


def test_markdown_hard_breaks_are_preserved() -> None:
    assert check_text(b"Intentional hard break  \nnext line\n", markdown=True) == []
    assert check_text(b"Accidental space \n", markdown=True)
    assert check_text(b"Whitespace-only line\n  \n", markdown=True)
    assert check_text(b"Too many spaces   \n", markdown=True)
    assert check_text(b"Not Markdown  \n")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git = shutil.which("git")
    assert git is not None
    subprocess.run(  # noqa: S603 -- Resolved Git, fixed argv, isolated pytest directory.
        [git, "init", "--quiet"], cwd=tmp_path, check=True, timeout=10
    )
    (tmp_path / ".gitignore").write_text(
        ".cache/\nnode_modules/\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / ".gitattributes").write_text(
        "*.bin binary\n", encoding="utf-8", newline="\n"
    )
    return tmp_path


def test_git_selection_includes_untracked_text_and_ignores_binary_and_cache(
    repository: Path,
) -> None:
    (repository / "read me.md").write_text(
        "Valid Unicode: café 🚀\n", encoding="utf-8", newline="\n"
    )
    (repository / "invalid.unknown").write_bytes(b"\xff\n")
    (repository / "opaque.bin").write_bytes(b"\xff\n")
    (repository / "nul.data").write_bytes(b"\x00\xff\n")
    for name in (".cache", "node_modules"):
        (repository / name).mkdir()
        (repository / name / "bad.txt").write_bytes(b"\xff\n")

    selected = {
        path.relative_to(repository).as_posix() for path in git_text_files(repository)
    }

    assert selected == {".gitignore", ".gitattributes", "read me.md", "invalid.unknown"}
    failures = check_repository(repository)
    assert len(failures) == 1
    assert "invalid.unknown" in failures[0]


def test_clean_checkout_text_is_checked_without_a_diff(repository: Path) -> None:
    (repository / "bad.txt").write_bytes(b"trailing space \n")
    git = shutil.which("git")
    assert git is not None
    subprocess.run(  # noqa: S603 -- Resolved Git, fixed argv, isolated pytest directory.
        [git, "add", "."], cwd=repository, check=True, timeout=10
    )

    assert any("trailing whitespace" in issue for issue in check_repository(repository))


def test_git_failure_is_not_reported_as_success(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Git"):
        git_text_files(tmp_path)


def test_disappearing_file_is_a_failure(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def selected_files(_root: Path) -> list[Path]:
        return [repository / "gone.txt"]

    monkeypatch.setattr(
        "scripts.check_repository.git_text_files",
        selected_files,
    )
    assert any("unable to read" in issue for issue in check_repository(repository))


def test_missing_git_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing_git(_command: str) -> None:
        return None

    monkeypatch.setattr("scripts.check_repository.shutil.which", missing_git)
    with pytest.raises(RuntimeError, match="Git is required"):
        git_text_files(tmp_path)


def test_empty_git_repository_has_no_text(repository: Path) -> None:
    (repository / ".gitignore").unlink()
    (repository / ".gitattributes").unlink()
    assert git_text_files(repository) == []


def test_git_timeout_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired("git grep", 10)

    monkeypatch.setattr("scripts.check_repository.subprocess.run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        git_text_files(tmp_path)


@pytest.mark.parametrize(
    ("failures", "status"), [([], 0), (["bad.txt: invalid UTF-8"], 1)]
)
def test_cli_exit_status(
    failures: list[str], status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def checked(_root: Path) -> list[str]:
        return failures

    monkeypatch.setattr("scripts.check_repository.check_repository", checked)
    assert main() == status


def test_cli_discovery_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def discovery_error(_root: Path) -> list[str]:
        msg = "Git discovery unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr("scripts.check_repository.check_repository", discovery_error)
    assert main() == 2


def test_symlinks_are_not_read(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def selected(_root: Path) -> list[Path]:
        return [repository / "link.txt"]

    def is_link(_path: Path) -> bool:
        return True

    monkeypatch.setattr("scripts.check_repository.git_text_files", selected)
    monkeypatch.setattr(Path, "is_symlink", is_link)
    assert check_repository(repository) == []
