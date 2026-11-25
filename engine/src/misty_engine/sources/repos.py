"""One summary text per code repo: README plus the file tree. Source files are never read."""
from __future__ import annotations

from pathlib import Path

SKIP_DIRS = {"node_modules", ".venv", "venv", "build", "dist", "__pycache__", ".cache", ".npm", ".pnpm-store",
             ".gradle", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".turbo", ".parcel-cache",
             "Caches", ".Trash", "DerivedData"}
README_NAMES = ("README.md", "README.rst", "README.txt", "README")


def _readme(path: Path, max_words: int) -> str:
    for name in README_NAMES:
        p = path / name
        if p.is_file() and not p.is_symlink():
            words = p.read_bytes().decode("utf-8", errors="replace").split()
            return " ".join(words[:max_words])
    return ""


def _tree(root: Path, depth: int, max_lines: int) -> list[str]:
    lines: list[str] = []

    def walk(d: Path, level: int) -> None:
        if level > depth or len(lines) >= max_lines:
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for e in entries:
            if len(lines) >= max_lines:
                return
            if e.name.startswith(".") or e.name in SKIP_DIRS or e.is_symlink():
                continue
            rel = e.relative_to(root).as_posix()
            if e.is_dir():
                lines.append(rel + "/")
                walk(e, level + 1)
            else:
                lines.append(rel)

    walk(root, 1)
    return lines


def repo_summary_text(path: Path, max_readme_words: int = 3000, max_tree_lines: int = 400) -> str:
    path = Path(path)
    parts = [f"Repository: {path.name}"]
    readme = _readme(path, max_readme_words)
    if readme:
        parts.append(readme)
    parts.append("File tree (depth 3):")
    parts.extend(_tree(path, 3, max_tree_lines))
    return "\n".join(parts)
