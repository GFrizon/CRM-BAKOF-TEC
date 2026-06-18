from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_EXTENSIONS = {
    ".py",
    ".html",
    ".css",
    ".js",
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".json",
    ".sql",
    ".env",
}
IGNORE_DIRS = {
    ".git",
    ".venv",
    ".venv_local",
    "__pycache__",
    "node_modules",
    "uploads",
}
SUSPECT_MARKERS = ("Ã", "Â", "�")


def is_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    return path.name in {".env", ".env.example", ".env.oracle", ".env.oracle.example"}


def should_ignore(path: Path) -> bool:
    if any(part in IGNORE_DIRS for part in path.parts):
        return True
    if path.name == "check_utf8_mojibake.py":
        return True
    return False


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except Exception:
        pass

    issues: list[tuple[Path, str]] = []

    for path in ROOT.rglob("*"):
        if not path.is_file() or should_ignore(path) or not is_text_file(path):
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append((path, "arquivo não está em UTF-8"))
            continue
        except Exception as exc:
            issues.append((path, f"erro ao ler: {exc}"))
            continue

        if any(marker in content for marker in SUSPECT_MARKERS):
            issues.append((path, "possível mojibake (Ã/Â/�)"))

    if issues:
        print("Foram encontrados problemas de encoding/mojibake:")
        for path, reason in issues:
            rel = path.relative_to(ROOT)
            print(f"- {rel}: {reason}")
        return 1

    print("OK: sem mojibake e arquivos UTF-8 válidos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
