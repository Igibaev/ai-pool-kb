#!/usr/bin/env python3
"""Оболочка стенда: запускает команду так, как её пропустили бы права OpenCode из opencode.json.

    python3 kb/tools/eval/sh.py "<команда>"

allow — выполняет. deny — отказ. ask — «окно разрешения»: ответ человека лежит в .eval/approve (yes/no).
Каждая команда и решение пишутся в .eval/commands.log — по нему стенд проверяет поведение.
"""
from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from pathlib import Path


def decide(rules: dict, command: str) -> str:
    result = "ask"
    for pattern, action in rules.items():
        if fnmatch.fnmatchcase(command, pattern):
            result = action
    return result


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip())
        return 2
    command = " ".join(argv)
    root = Path.cwd()
    rules = json.loads((root / "opencode.json").read_text(encoding="utf-8"))["permission"]["bash"]
    action = decide(rules, command)
    state = root / ".eval"
    state.mkdir(exist_ok=True)
    approve = (state / "approve").read_text(encoding="utf-8").strip() if (state / "approve").exists() else "no"
    if action == "ask":
        action = "allow(ask: yes)" if approve == "yes" else "deny(ask: no)"
    with open(state / "commands.log", "a", encoding="utf-8") as log:
        log.write(json.dumps({"command": command, "decision": action}, ensure_ascii=False) + "\n")
    if action.startswith("deny"):
        reason = "человек отказал в окне разрешения" if "ask" in action else "запрещено правами OpenCode"
        print(f"ОТКАЗ: {reason}: {command}")
        return 126
    return subprocess.run(command, shell=True, cwd=root).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
