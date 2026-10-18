"""Находки не-владельца шага: context/process/notes/PM-XX/<логин>.md. Владелец переносит находку в шаг.

Формат — kb/rules/formats.md, раздел 5.
"""
from __future__ import annotations

import re
from pathlib import Path

import kbbase
import kbcheck
import kbfacts

MOVED_MARK = " · перенесено: "


def login_of(team: kbbase.Team, person: str) -> str:
    git = team.people.get(person, "").split(",")[0].strip()
    return git.split("@")[0] if "@" in git else git


def findings(lines: list[str], body: int) -> list[int]:
    """Номера строк (с нуля) находок — пункты списка в теле."""
    return [i for i in range(body, len(lines)) if lines[i].startswith("- ")]


def take(root: Path, team: kbbase.Team, person: str, ident: str, login: str, number: str, section: str) -> int:
    step = kbbase.find_object(root, ident)
    if step is None:
        print(f"ОШИБКА: шага {ident} нет в базе")
        return 1
    owner = kbbase.front_matter(kbbase.read_lines(step))[0].get("владелец", "")
    if person != owner:
        print(f"ОШИБКА: находки в шаг переносит владелец шага: {owner}")
        return 1
    path = root / "context" / "process" / "notes" / ident / f"{login}.md"
    if not path.is_file():
        print(f"ОШИБКА: находок {login} по {ident} нет: {kbbase.rel(root, path)}")
        return 1
    lines = kbbase.read_lines(path)
    head, body = kbbase.front_matter(lines)
    items = findings(lines, body)
    if not number.isdigit() or not 1 <= int(number) <= len(items):
        print(f"ОШИБКА: номер находки — от 1 до {len(items)}")
        return 1
    i = items[int(number) - 1]
    line = lines[i]
    if MOVED_MARK in line and not line.endswith(MOVED_MARK + "—"):
        print(f"ОШИБКА: находка {number} уже перенесена: {line.split(MOVED_MARK, 1)[1]}")
        return 1
    fact_line = kbcheck.MOVED.sub("", line)
    author = head.get("автор", login)
    fact_line = re.sub(r";\s*([^;()]+)\)\s*$", lambda m: f", нашёл {author}; {m.group(1)})", fact_line)
    fact, fixes, problems = kbfacts.check(root, team, fact_line)
    for p in problems:
        print(f"{p.level}: {p.message}")
    if fact is None or any(p.level == "ОШИБКА" for p in problems):
        print("НЕ ПЕРЕНЕСЕНО")
        return 1
    error = kbfacts.insert(root, step, section, fact.line(), kbcheck.STEP_FACTS)
    if error:
        print(f"ОШИБКА: {error}")
        print("НЕ ПЕРЕНЕСЕНО")
        return 1
    base = line.split(MOVED_MARK, 1)[0]
    lines[i] = f"{base}{MOVED_MARK}{ident}#{section}, {kbbase.today().isoformat()}"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"ПЕРЕНЕСЕНО: находка {number} от {author} → {ident}, раздел «{section}»")
    return 0


def foreign_notes(root: Path, team: kbbase.Team, person: str, paths: list[str]) -> list[str]:
    """Файлы находок, которые человек править не может: чужие, если он не владелец шага."""
    mine = login_of(team, person)
    problems = []
    for inner in paths:
        m = re.match(r"^context/process/notes/(PM-\d+)/([^/]+)\.md$", inner)
        if not m or m.group(2) == mine:
            continue
        step = kbbase.find_object(root, m.group(1))
        owner = kbbase.front_matter(kbbase.read_lines(step))[0].get("владелец", "") if step else ""
        if person != owner:
            problems.append(f"Находки пишет только автор: `{inner}` — не ваш файл")
    return problems
