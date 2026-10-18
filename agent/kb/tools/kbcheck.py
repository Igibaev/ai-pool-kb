"""kb check: проверка всей базы. Факты, шапки шагов и вопросов, язык.

Ошибка — запись не проходит (код выхода 1). Предупреждение — видно, но не мешает.
"""
from __future__ import annotations

import re
from pathlib import Path

import kbbase
import kbfacts
from kbfacts import Problem

STEP_FACTS = {"Зачем бизнесу", "Вход", "Выход", "Состояния", "Исключения", "Как сейчас"}
STEP_TABLES = {"Объём", "Сущности"}
STEP_NOT_FACTS = {"Коротко", "Как будет"}
QUESTION_FACTS = {"Что уже знаем", "Ответ"}
WHO_ANSWERS = ("бизнес", "смежник", "команда", "аналитик", "сами")
WHO_ENGLISH = {"business": "бизнес", "team": "команда", "analyst": "аналитик", "self": "сами",
               "vendor": "смежник", "neighbor": "смежник"}
QUESTION_STATUSES = ("открыт", "ответ найден", "отвечен", "вне системы")
CLOSED = ("отвечен", "вне системы")
WHEN = ("MVP", "позже", "—")
HOW = ("ручной", "автоматизируем", "вне системы")
NOTE = re.compile(r"^- \[(к спеке|к нарезке|гипотеза|риск)\] .+\(.+, \d{4}-\d{2}-\d{2}\) → дальше: .+")
ITEM = re.compile(r"^(?:-|\d+\.)\s+")
MOVED = re.compile(r"\s+·\s+перенесено:.*$")


class Report:
    def __init__(self, root: Path):
        self.root = root
        self.items: list[tuple[str, int, Problem]] = []

    def add(self, path: Path, line_no: int, problem: Problem) -> None:
        self.items.append((kbbase.rel(self.root, path), line_no, problem))

    def errors(self) -> int:
        return sum(1 for _, _, p in self.items if p.level == "ОШИБКА")

    def print(self) -> None:
        for path, line_no, p in self.items:
            where = f"{path}:{line_no}" if line_no else path
            print(f"{p.level} {where}: {p.message}")
        warnings = len(self.items) - self.errors()
        print(f"Итог: ошибок {self.errors()}, предупреждений {warnings}")


def check_fact_line(rep: Report, team: kbbase.Team, path: Path, n: int, line: str) -> kbfacts.Fact | None:
    fact, _, problems = kbfacts.check(rep.root, team, MOVED.sub("", line))
    for p in problems:
        rep.add(path, n + 1, p)
    return fact


def check_fact_section(rep, team, path, lines, start, end) -> None:
    contradictions = []
    for i in range(start + 1, end):
        if ITEM.match(lines[i]):
            fact = check_fact_line(rep, team, path, i, lines[i])
            if fact and fact.status == "противоречие":
                contradictions.append(i)
    if len(contradictions) == 1:
        rep.add(path, contradictions[0] + 1, Problem(
            "ПРЕДУПРЕЖДЕНИЕ", "У противоречия должно быть две стороны. Запишите второй факт со своим источником"))


def check_table_sources(rep, team, path, lines, start, end) -> None:
    header = kbbase.table_header(lines, start, end)
    if "Источник" not in header:
        return
    col = header.index("Источник")
    for i, cells in kbbase.table_rows(lines, start, end):
        src = cells[col] if col < len(cells) else ""
        if src in ("", "—", "-"):
            rep.add(path, i + 1, Problem("ПРЕДУПРЕЖДЕНИЕ", "Нет источника в строке таблицы"))
            continue
        fact = kbfacts.Fact("строка таблицы", kbfacts.fix_source(src, []), "оценка")
        for p in kbfacts.check_source(rep.root, team, fact):
            rep.add(path, i + 1, p)


def check_step(rep: Report, team: kbbase.Team, path: Path) -> None:
    lines = kbbase.read_lines(path)
    head, body = kbbase.front_matter(lines)
    for key in ("id", "шаг", "статус", "владелец"):
        if not head.get(key):
            rep.add(path, 0, Problem("ОШИБКА", f"В шапке нет поля «{key}»"))
    status = head.get("статус", "")
    if status and status not in kbbase.STEP_STATUSES:
        rep.add(path, 0, Problem("ОШИБКА", f"Неизвестный статус «{status}». Статусы: {', '.join(kbbase.STEP_STATUSES)}"))
    enough = head.get("достаточно для спеки", "—")
    if enough != "—" and not re.match(r"^.+, \d{4}-\d{2}-\d{2}$", enough):
        rep.add(path, 0, Problem("ОШИБКА", "Поле «достаточно для спеки» — «—» или «<имя>, <ГГГГ-ММ-ДД>». "
                                           "Ставит только `kb step enough`"))
    names = set()
    for name, start, end in kbbase.sections(lines, body):
        names.add(name)
        if name in STEP_FACTS:
            check_fact_section(rep, team, path, lines, start, end)
        elif name in STEP_TABLES:
            check_table_sources(rep, team, path, lines, start, end)
        if name in STEP_NOT_FACTS:
            for i in range(start + 1, end):
                if "(ист:" in lines[i]:
                    rep.add(path, i + 1, Problem("ПРЕДУПРЕЖДЕНИЕ", "Здесь не факты, а пересказ или намерение. "
                                                                   "Источник не нужен"))
        if name == "Коротко":
            filled = [l for l in lines[start + 1:end] if l.strip() and not l.strip().startswith("<")]
            if len(filled) < 4:
                rep.add(path, start + 1, Problem("ПРЕДУПРЕЖДЕНИЕ", "Опишите шаг своими словами: "
                                                                   "что делает, зачем бизнесу, вход, выход"))
        if name == "Объём":
            header = kbbase.table_header(lines, start, end)
            for i, cells in kbbase.table_rows(lines, start, end):
                row = dict(zip(header, cells))
                if row.get("Когда") not in WHEN or row.get("Как") not in HOW:
                    rep.add(path, i + 1, Problem("ПРЕДУПРЕЖДЕНИЕ", "Отметьте, когда делаем (MVP / позже) "
                                                 "и как (ручной / автоматизируем / вне системы)"))
        if name == "Заметки":
            for i in range(start + 1, end):
                if lines[i].startswith("- ") and not NOTE.match(lines[i]):
                    rep.add(path, i + 1, Problem("ПРЕДУПРЕЖДЕНИЕ", "Формат заметки: "
                                                 "`- [к спеке] текст (кто, дата) → дальше: …`"))
    if "Коротко" not in names:
        rep.add(path, 0, Problem("ПРЕДУПРЕЖДЕНИЕ", "Нет раздела «Коротко». Опишите шаг своими словами: "
                                                   "что делает, зачем бизнесу, вход, выход"))


def check_question(rep: Report, team: kbbase.Team, path: Path) -> None:
    lines = kbbase.read_lines(path)
    head, body = kbbase.front_matter(lines)
    who = head.get("кто отвечает", "")
    if who not in WHO_ANSWERS:
        hint = f" Похоже на «{WHO_ENGLISH[who.lower()]}»." if who.lower() in WHO_ENGLISH else ""
        rep.add(path, 0, Problem("ОШИБКА", f"«кто отвечает: {who}» — неизвестно.{hint} "
                                           f"Значения: {', '.join(WHO_ANSWERS)}"))
    asked = head.get("задаём", "")
    if not asked or asked == "—":
        rep.add(path, 0, Problem("ОШИБКА", "Вопрос попадает в журнал только через `kb ask send` — "
                                           "после «задаём» разработчика"))
    if not head.get("откуда"):
        rep.add(path, 0, Problem("ОШИБКА", "Откуда вопрос: строка материала или «со слов кто, дата»"))
    status = head.get("статус", "")
    if not (status in QUESTION_STATUSES or status.startswith(("стал допущением A-", "дубль Q-"))):
        rep.add(path, 0, Problem("ОШИБКА", f"Неизвестный статус вопроса «{status}». "
                                           f"Статусы: {', '.join(QUESTION_STATUSES)}, стал допущением A-NNN, дубль Q-NNN"))
    if status in CLOSED and not any(l.startswith("Закрыл:") for l in lines):
        rep.add(path, 0, Problem("ОШИБКА", f"Закрытие подтверждает автор: `kb q close {head.get('id', '')}`"))
    for name, start, end in kbbase.sections(lines, body):
        if name in QUESTION_FACTS:
            check_fact_section(rep, team, path, lines, start, end)


def check_body_facts(rep: Report, team: kbbase.Team, path: Path) -> None:
    """Решения и находки: все пункты списка в теле — факты."""
    lines = kbbase.read_lines(path)
    _, body = kbbase.front_matter(lines)
    for name, start, end in kbbase.sections(lines, body) or [("", body - 1, len(lines))]:
        check_fact_section(rep, team, path, lines, start, end)


def check_language(rep: Report, path: Path) -> None:
    for i, line in enumerate(kbbase.read_lines(path)):
        for p in kbfacts.check_text(line):
            rep.add(path, i + 1, p)


def run(root: Path) -> Report:
    rep = Report(root)
    team = kbbase.Team(root)
    process = root / "context" / "process"
    journal = root / "journal"
    for path in sorted(process.glob("PM-*.md")):
        check_step(rep, team, path)
    for path in sorted((journal / "questions").glob("Q-*.md")):
        check_question(rep, team, path)
    for path in sorted((journal / "questions").glob("*/**/Q-*.md")):
        rep.add(path, 0, Problem("ОШИБКА", "Вопросы лежат в `journal/questions/` плоско. "
                                            "Меняется статус, файл не двигается"))
    for path in sorted((journal / "decisions").glob("D-*.md")):
        check_body_facts(rep, team, path)
    for path in sorted((process / "notes").glob("PM-*/*.md")):
        check_body_facts(rep, team, path)
    content = [root / "product.md", root / "team.md"]
    content += sorted((root / "context").rglob("*.md")) + sorted(journal.rglob("*.md"))
    for path in content:
        if path.is_file():
            check_language(rep, path)
    # Язык фактов уже проверен при проверке строк — убираем повторы
    seen, unique = set(), []
    for item in rep.items:
        key = (item[0], item[1], item[2].message)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    rep.items = unique
    return rep
