"""Глоссарий context/glossary.md: | Термин | Синонимы через запятую | Определение |.

Синоним в тексте базы — подсказка писать основной термин. Термин дважды — ошибка.
"""
from __future__ import annotations

import re
from pathlib import Path

import kbbase
from kbfacts import Problem


def load(root: Path) -> tuple[dict[str, str], list[Problem]]:
    """синоним (в нижнем регистре) → основной термин; и ошибки самого глоссария."""
    path = root / "context" / "glossary.md"
    synonyms: dict[str, str] = {}
    problems: list[Problem] = []
    if not path.is_file():
        return synonyms, problems
    lines = kbbase.read_lines(path)
    seen: dict[str, str] = {}
    for _, cells in kbbase.table_rows(lines, 0, len(lines)):
        if len(cells) < 2 or not cells[0] or cells[0].startswith("<"):
            continue
        term = cells[0].strip()
        names = [term] + [s.strip() for s in (cells[1].split(",") if len(cells) > 2 else []) if s.strip()]
        for name in names:
            key = name.lower()
            if key in seen and seen[key] != term:
                problems.append(Problem("ОШИБКА", f"«{name}» в глоссарии дважды: у «{seen[key]}» и у «{term}». "
                                                  "Одно понятие — одна строка"))
            seen[key] = term
            if name != term:
                synonyms[key] = term
    return synonyms, problems


def check(root: Path, path: Path, synonyms: dict[str, str]) -> list[tuple[int, Problem]]:
    result = []
    if not synonyms:
        return result
    pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(s) for s in sorted(synonyms, key=len, reverse=True))
                         + r")(?![\w-])", re.IGNORECASE)
    for i, line in enumerate(kbbase.read_lines(path)):
        for m in pattern.finditer(line):
            term = synonyms[m.group(1).lower()]
            result.append((i + 1, Problem("ПРЕДУПРЕЖДЕНИЕ", f"«{m.group(1)}» — синоним «{term}» по глоссарию. "
                                                            "Пишите основной термин")))
    return result


def expand(root: Path, text: str) -> str:
    """Текст с основными терминами вместо синонимов — для поиска похожего."""
    synonyms, _ = load(root)
    for syn, term in synonyms.items():
        text = re.sub(rf"(?<![\w-]){re.escape(syn)}(?![\w-])", term, text, flags=re.IGNORECASE)
    return text
