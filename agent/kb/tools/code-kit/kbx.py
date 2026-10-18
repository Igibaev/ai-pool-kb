#!/usr/bin/env python3
"""Мост из папки кода к базе знаний. Путь к базе — в файле .kb-path в корне репозитория кода.

    python3 kbx.py gate PM-XX-NN      можно ли писать код по декомпозиту: принята спека или ранний срез
    python3 kbx.py <команда kb>       команды чтения базы: where, similar, step gaps, check
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

READ = {"where", "similar", "check", "whoami", "step"}


def base_dir() -> Path:
    here = Path(__file__).resolve().parent
    for d in [here, *here.parents]:
        marker = d / ".kb-path"
        if marker.is_file():
            base = (d / marker.read_text(encoding="utf-8").strip()).resolve()
            if not (base / "kb" / "team.md").is_file():
                sys.exit(f"ОШИБКА: в .kb-path указан {base}, но там нет kb/team.md")
            return base
    sys.exit("ОШИБКА: нет файла .kb-path в корне репозитория кода")


def front(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    fields = {}
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if ":" in line:
                k, v = line.split(":", 1)
                fields[k.strip()] = v.split("  #", 1)[0].strip()
    return fields


def gate(base: Path, ident: str) -> int:
    if not re.match(r"^PM-\d+-\d{2}$", ident):
        print("ОШИБКА: нужен ID декомпозита: PM-XX-NN")
        return 1
    found = sorted((base / "kb" / "delivery").glob(f"{ident}-*.md"))
    if not found:
        print(f"ОШИБКА: декомпозита {ident} нет в базе")
        return 1
    head = front(found[0])
    if head.get("спека принята", "—") not in ("", "—"):
        print(f"МОЖНО: спека {ident} принята — {head['спека принята']}. Спека: {head.get('спека', '—')}")
        return 0
    if head.get("ранний срез", "—") not in ("", "—"):
        print(f"МОЖНО: ранний срез {ident} — {head['ранний срез']}")
        return 0
    print(f"НЕЛЬЗЯ: нет кода без принятой спеки. {ident}: спека {head.get('спека', '—')}, не принята")
    return 1


def main(argv: list[str]) -> int:
    base = base_dir()
    if argv[:1] == ["gate"] and len(argv) == 2:
        return gate(base, argv[1])
    if not argv or argv[0] not in READ or (argv[0] == "step" and argv[1:2] != ["gaps"]):
        print(__doc__.strip())
        return 1
    env = dict(os.environ, KB_ROOT=str(base / "kb"))
    return subprocess.run([sys.executable, str(base / "kb" / "tools" / "kb.py"), *argv], env=env, cwd=base).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
