# Copyright (c) 2026 Hans Valerie
"""Validate Git-selected text for encoding damage and basic whitespace hygiene."""

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def git_text_files(root: Path) -> list[Path]:
    """Select tracked and untracked nonempty text using Git's binary/ignore rules.

    Args:
        root: Repository directory in which Git should search.

    Returns:
        Text paths, including untracked files but excluding ignored files.

    Raises:
        RuntimeError: Git is missing or cannot enumerate repository text.
        OSError: The Git executable cannot be started.
        subprocess.TimeoutExpired: Git does not finish within ten seconds.
    """
    git = shutil.which("git")
    if git is None:
        msg = "Git is required for repository text checks"
        raise RuntimeError(msg)
    result = subprocess.run(  # noqa: S603 -- Resolved Git, fixed argv, no shell.
        [git, "grep", "--untracked", "--exclude-standard", "-Ilz", "-e", "", "--"],
        cwd=root,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode not in (0, 1):
        msg = "Git text-file discovery failed"
        raise RuntimeError(msg)
    return [root / os.fsdecode(name) for name in result.stdout.split(b"\0") if name]


def has_mojibake(line: str) -> bool:
    """Identify reversible legacy decoding of a single UTF-8 character.

    Args:
        line: Text to inspect without broad suspicious-character substring rules.

    Returns:
        Whether a round trip yields printable text, a BOM, or a replacement mark.
    """
    for encoding in ("cp1252", "latin1"):
        for width in (2, 3, 4):
            for start in range(len(line) - width + 1):
                segment = line[start : start + width]
                try:
                    repaired = segment.encode(encoding).decode("utf-8")
                except UnicodeError:
                    continue
                if len(repaired) == 1 and (
                    repaired.isprintable() or repaired in {"\ufeff", "\ufffd"}
                ):
                    return True
    return False


def check_text(data: bytes, *, markdown: bool = False) -> list[str]:
    """Return encoding/whitespace diagnostics without rewriting the input.

    Args:
        data: Complete contents of a Git-classified text file.
        markdown: Allow exactly two trailing spaces after Markdown content.

    Returns:
        Diagnostics with line numbers where available. Empty files are valid.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        return [f"invalid UTF-8 at byte {error.start}"]

    failures: list[str] = []
    if "\r" in text:
        failures.append("CR line ending found; use LF")
    if data and not data.endswith(b"\n"):
        failures.append("missing final newline")
    # splitlines() would hide C1 NEL characters before they can be diagnosed.
    for number, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.removesuffix("\r")
        if "\ufeff" in line:
            failures.append(f"{number}: unexpected UTF-8 BOM")
        if "\ufffd" in line:
            failures.append(f"{number}: Unicode replacement character")
        if any(0x80 <= ord(character) <= 0x9F for character in line):
            failures.append(f"{number}: suspicious C1 control character")
        if has_mojibake(line):
            failures.append(f"{number}: reversible legacy/UTF-8 mojibake")
        content = line.rstrip(" \t")
        hard_break = markdown and bool(content) and line[len(content) :] == "  "
        if content != line and not hard_break:
            failures.append(f"{number}: trailing whitespace")
    return failures


def check_repository(root: Path) -> list[str]:
    """Check repository text without following symlinks or logging file contents.

    Args:
        root: Repository directory.

    Returns:
        Path-qualified diagnostics, including read failures.
    """
    failures: list[str] = []
    for path in git_text_files(root):
        if path.is_symlink():
            continue
        label = ascii(path.relative_to(root).as_posix())
        try:
            data = path.read_bytes()
        except OSError:
            failures.append(f"{label}: unable to read file")
            continue
        failures.extend(
            f"{label}:{issue}"
            for issue in check_text(
                data, markdown=path.suffix.lower() in {".md", ".mdx"}
            )
        )
    return failures


def main() -> int:
    """Run read-only quality checks, failing closed on discovery/read errors."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        failures = check_repository(Path(__file__).resolve().parents[1])
    except (OSError, RuntimeError, subprocess.SubprocessError):
        logger.exception("Repository text discovery failed")
        return 2
    for failure in failures:
        logger.error("%s", failure)
    if failures:
        return 1
    logger.info("Repository text encoding and whitespace checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
