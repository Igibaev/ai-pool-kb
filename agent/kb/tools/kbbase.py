"""Общее для команд kb: где база, чтение файлов, шапка, разделы, команда."""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

STEP_STATUSES = ["Черновик", "Готов к взятию", "Разбор", "Сверка с бизнесом", "Нарезка", "Приёмка",
                 "Декомпозиты в работе", "Готово"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def kb_root() -> Path:
    """Папка kb/. KB_ROOT — для тестов и нестандартных раскладок."""
    env = os.environ.get("KB_ROOT")
    if env:
        return Path(env).resolve()
    here = Path.cwd().resolve()
    for d in [here, *here.parents]:
        if (d / "kb" / "team.md").is_file():
            return d / "kb"
        if (d / "team.md").is_file() and (d / "context").is_dir():
            return d
    raise SystemExit("ОШИБКА: не нашёл базу — нет папки kb/ с team.md. Запустите команду из репозитория базы.")


def today() -> dt.date:
    env = os.environ.get("KB_TODAY")
    return dt.date.fromisoformat(env) if env else dt.date.today()


def parse_date(text: str) -> dt.date | None:
    if not DATE_RE.match(text):
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    """Шапка между строками `---`. Возвращает поля и номер первой строки тела (с нуля)."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    fields: dict[str, str] = {}
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            return fields, i + 1
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.split("  #", 1)[0].strip()
    return fields, len(lines)


def sections(lines: list[str], start: int = 0) -> list[tuple[str, int, int]]:
    """Разделы `## Имя`: (имя, номер строки заголовка, номер строки после раздела)."""
    heads = [(i, lines[i][3:].strip()) for i in range(start, len(lines)) if lines[i].startswith("## ")]
    result = []
    for n, (i, name) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        result.append((name, i, end))
    return result


def table_rows(lines: list[str], start: int, end: int) -> list[tuple[int, list[str]]]:
    """Строки таблицы без шапки и разделителя: (номер строки, ячейки)."""
    rows = []
    seen_header = False
    for i in range(start, end):
        line = lines[i].strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not seen_header:
            seen_header = True
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append((i, cells))
    return rows


def table_header(lines: list[str], start: int, end: int) -> list[str]:
    for i in range(start, end):
        line = lines[i].strip()
        if line.startswith("|"):
            return [c.strip() for c in line.strip("|").split("|")]
    return []


class Team:
    def __init__(self, root: Path):
        self.people: dict[str, str] = {}      # имя → почта или git-имя
        self.roles: dict[str, str] = {}       # имя → роли в пуле
        self.business: set[str] = set()       # роли бизнеса
        path = root / "team.md"
        if not path.is_file():
            return
        lines = read_lines(path)
        _, body = front_matter(lines)
        secs = sections(lines, body)
        first_end = secs[0][1] if secs else len(lines)
        for _, cells in table_rows(lines, body, first_end):
            if cells and cells[0] and not cells[0].startswith("<"):
                self.people[cells[0]] = cells[1] if len(cells) > 1 else ""
                self.roles[cells[0]] = cells[2] if len(cells) > 2 else ""
        for name, start, end in secs:
            if name.lower().startswith("роли бизнеса"):
                for _, cells in table_rows(lines, start, end):
                    if cells and cells[0] and not cells[0].startswith("<"):
                        self.business.add(cells[0].lower())

    def is_person(self, name: str) -> bool:
        return name in self.people

    def knows(self, who: str) -> bool:
        return who in self.people or who.lower() in self.business

    def by_git(self, email: str, name: str) -> str | None:
        for person, git in self.people.items():
            ids = [x.strip().lower() for x in git.split(",")]
            if email and email.lower() in ids:
                return person
        if name in self.people:
            return name
        return None


def find_object(root: Path, ident: str) -> Path | None:
    """Файл объекта по ID: PM-XX, Q-NNN, D-NNN, A-NNN."""
    if ident.startswith("PM-"):
        folder, pattern = root / "context" / "process", f"{ident}-*.md"
    elif ident[:2] in ("Q-", "D-", "A-", "R-"):
        sub = {"Q": "questions", "D": "decisions", "A": "assumptions", "R": "risks"}[ident[0]]
        folder, pattern = root / "journal" / sub, f"{ident}*.md"
    else:
        return None
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob(pattern)):
        stem = path.stem
        if stem == ident or stem.startswith(ident + "-"):
            return path
    return None
