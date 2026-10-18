"""Связи шагов по входу и выходу: context/process/_deps.md — для роадмапа. Плюс «устарело» и похожие шаги.

Связь — кандидат, найденный по словам. Решает команда: техлид потока и аналитик.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import kbbase
import kbquestions

LINK = 0.2        # доля общих слов «выход A» ~ «вход B», с которой это кандидат в связь
TWIN = 0.5        # похожи и вход, и выход — «один это шаг или два?»
ACCEPTED = ("Приёмка", "Декомпозиты в работе", "Готово")
NOTE = "Строит `kb deps` по разделам «Вход», «Выход», «Сущности». Связь — кандидат по словам, решает команда. Руками не править."


class Step:
    def __init__(self, path: Path):
        self.path = path
        lines = kbbase.read_lines(path)
        head, body = kbbase.front_matter(lines)
        self.id = head.get("id", path.stem)
        self.name = head.get("шаг", "")
        self.status = head.get("статус", "")
        secs = {n: (s, e) for n, s, e in kbbase.sections(lines, body)}
        self.inp = self._text(lines, secs.get("Вход"))
        self.out = self._text(lines, secs.get("Выход"))
        self.entities: set[str] = set()
        if "Сущности" in secs:
            for _, cells in kbbase.table_rows(lines, *secs["Сущности"]):
                if cells and cells[0]:
                    self.entities.add(cells[0].strip())

    @staticmethod
    def _text(lines: list[str], span) -> str:
        if not span:
            return ""
        return " ".join(l.split("(ист:")[0] for l in lines[span[0] + 1:span[1]] if l.strip())


def steps(root: Path) -> list[Step]:
    return [Step(p) for p in sorted((root / "context" / "process").glob("PM-*.md"))]


def links(all_steps: list[Step]) -> list[tuple[Step, Step, str]]:
    """(от кого, к кому, почему): выход A похож на вход B или у них общая сущность на выходе A и входе B."""
    result = []
    for a in all_steps:
        for b in all_steps:
            if a is b:
                continue
            score = kbquestions.similarity(a.out, b.inp)
            common = sorted(e for e in a.entities & b.entities if e.lower() in b.inp.lower())
            if score >= LINK or common:
                why = f"общие слова {score:.0%}" if score >= LINK else ""
                if common:
                    why = (why + "; " if why else "") + "сущность " + ", ".join(common)
                result.append((a, b, why))
    return result


def twins(all_steps: list[Step]) -> list[tuple[Step, Step]]:
    result = []
    for i, a in enumerate(all_steps):
        for b in all_steps[i + 1:]:
            if (kbquestions.similarity(a.inp, b.inp) >= TWIN and kbquestions.similarity(a.out, b.out) >= TWIN):
                result.append((a, b))
    return result


def render(root: Path) -> str:
    all_steps = steps(root)
    found = links(all_steps)
    text = ["# Связи шагов", "", NOTE, "", "## Кто от кого зависит", "",
            "| Шаг | Берёт вход у | Отдаёт выход в |", "|---|---|---|"]
    for s in all_steps:
        src = ", ".join(a.id for a, b, _ in found if b is s) or "—"
        dst = ", ".join(b.id for a, b, _ in found if a is s) or "—"
        text.append(f"| {s.id} {s.name} | {src} | {dst} |")
    text += ["", "## Почему связаны", ""]
    text += [f"- {a.id} → {b.id}: {why}" for a, b, why in found] or ["Связей не найдено."]
    text += ["", "## Кто кого блокирует", "",
             "Шаг блокирует следующий, пока его нарезку не приняли (статус до «Приёмки»).", ""]
    blocks = [f"- {a.id} «{a.status}» блокирует {b.id}" for a, b, _ in found if a.status not in ACCEPTED]
    text += blocks or ["Блокировок нет."]
    text += ["", "## Можно делать параллельно", ""]
    linked = {s.id for a, b, _ in found for s in (a, b)}
    free = [s.id for s in all_steps if s.id not in linked]
    text += [f"- {', '.join(free)} — связей по входу и выходу не найдено"] if free else ["Все шаги связаны."]
    text += ["", "## Один это шаг или два?", ""]
    text += [f"- {a.id} и {b.id}: похожи и вход, и выход" for a, b in twins(all_steps)] or ["Похожих пар нет."]
    return "\n".join(text) + "\n"


def write(root: Path) -> bool:
    path = root / "context" / "process" / "_deps.md"
    text = render(root)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def last_change(path: Path) -> str:
    r = subprocess.run(["git", "-C", str(path.parent), "log", "-1", "--format=%cI", "--", path.name],
                       capture_output=True, text=True, encoding="utf-8")
    return r.stdout.strip()


def stale(root: Path) -> list[str]:
    """Шаг устарел, если шаг, от которого он берёт вход, изменён позже него."""
    result = []
    for a, b, _ in links(steps(root)):
        ta, tb = last_change(a.path), last_change(b.path)
        if ta and tb and ta > tb:
            result.append(f"{b.id}: вход берёт у {a.id}, а {a.id} изменён позже ({ta[:10]} > {tb[:10]}) — проверьте «Вход»")
    return result
