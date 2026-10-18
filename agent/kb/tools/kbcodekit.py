"""kb code-kit <папка кода>: набор для репозитория кода — .kb-path, мост kbx.py, CI-проверка, правила агента кода.

База и код — одна история git, разные ветки и папки (git worktree). Ветки не сливаются.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import kbbase

KIT = Path(__file__).resolve().parent / "code-kit"
# Имена инструментов MCP GitLab в OpenCode зависят от сервера; закрываем по шаблону
MCP_DENY = ["*merge*", "*approve*"]


def closed_paths(root: Path) -> list[str]:
    head, _ = kbbase.front_matter(kbbase.read_lines(root / "project.md")) if (root / "project.md").is_file() else ({}, 0)
    raw = head.get("закрыто агенту", "[]").strip()
    return [p.strip().strip("'\"") for p in raw.strip("[]").split(",") if p.strip()]


def opencode_config(closed: list[str]) -> dict:
    bash = {
        "*": "ask",
        "python3 kbx.py *": "allow", "git status*": "allow", "git diff*": "allow", "git log*": "allow",
        "git show*": "allow", "ls*": "allow", "grep *": "allow", "./gradlew test*": "allow",
        "git commit*": "ask",
        "git push*": "deny", "git -c*": "deny", "git config*": "deny", "*--no-verify*": "deny",
        "git remote add*": "deny", "git remote set-url*": "deny", "*core.hooksPath*": "deny",
        "*&& git push*": "deny", "*; git push*": "deny",
    }
    for path in closed:
        bash[f"*{path}*"] = "deny"
    edit = {"*": "allow", "AGENTS.md": "deny", "opencode.json": "deny", ".kb-path": "deny", "kbx.py": "deny",
            "ci-no-kb.sh": "deny", ".git/*": "deny", "build.gradle*": "ask", "settings.gradle*": "ask",
            "gradlew*": "ask", "Jenkinsfile": "ask", "kb/*": "deny"}
    read = {"*": "allow"}
    for path in closed:
        edit[path] = "deny"
        read[path] = "deny"
    return {
        "$schema": "https://opencode.ai/config.json",
        "default_agent": "code",
        "agent": {
            "code": {"mode": "primary", "description": "Код и тесты по принятой спеке", "model": "{env:KB_MODEL_DIALOG}"},
            "general": {"disable": True},
        },
        "permission": {"bash": bash, "edit": edit, "read": read, "task": {"*": "deny"}, "webfetch": "ask"},
        "tools": {name: False for name in MCP_DENY},
    }


def install(root: Path, target: str) -> int:
    code = Path(target).expanduser().resolve()
    if not code.is_dir():
        print(f"ОШИБКА: папки кода нет: {code}")
        return 1
    base_repo = root.parent
    if (code / "kb").exists():
        print(f"ОШИБКА: в папке кода есть kb/. База живёт только в ветке context, в своей папке")
        return 1
    written = []
    (code / ".kb-path").write_text(os.path.relpath(base_repo, code) + "\n", encoding="utf-8")
    written.append(".kb-path")
    for name in ("kbx.py", "ci-no-kb.sh"):
        shutil.copy2(KIT / name, code / name)
        written.append(name)
    agents = code / ("AGENTS.md" if not (code / "AGENTS.md").exists() else "AGENTS.kb.md")
    shutil.copy2(KIT / "AGENTS.md", agents)
    written.append(agents.name)
    config = code / ("opencode.json" if not (code / "opencode.json").exists() else "opencode.kb.json")
    config.write_text(json.dumps(opencode_config(closed_paths(root)), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written.append(config.name)
    print(f"НАБОР ДЛЯ КОДА: {code}")
    for name in written:
        print(f"  {name}")
    if agents.name != "AGENTS.md" or config.name != "opencode.json":
        print("В папке уже были AGENTS.md или opencode.json — новые лежат рядом с суффиксом .kb, объедините вручную")
    print("Добавьте в CI кода шаг: sh ci-no-kb.sh. Имена инструментов MCP GitLab сверьте: opencode mcp list")
    return 0
