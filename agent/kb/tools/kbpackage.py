"""Пакет вопросов для бизнеса: journal/packages/<дата>-<роль>.md с колонкой «Ответ». Заполненный загружается обратно.

В пакет идут только вопросы «кто отвечает: бизнес», MVP: да, уже заданные (поле «задаём») и открытые.
"""
from __future__ import annotations

import re
from pathlib import Path

import kbbase
import kbfacts
import kbquestions

TRANSLIT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
                          "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
                          "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh",
                          "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"})
ALL = "всем"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().translate(TRANSLIT)).strip("-")


def build(root: Path, team: kbbase.Team, role: str) -> int:
    if role != ALL and role.lower() not in team.business:
        print(f"ОШИБКА: роли «{role}» нет в `team.md`, раздел «Роли бизнеса». Или: kb package {ALL}")
        return 1
    chosen: dict[str, list] = {}
    for path, lines, head, body in kbquestions.all_questions(root):
        if (head.get("кто отвечает") == "бизнес" and head.get("MVP") == "да" and head.get("статус") == "открыт"
                and head.get("задаём", "—") not in ("", "—")
                and (role == ALL or head.get("роль бизнеса", "").lower() == role.lower())):
            for step in kbquestions.steps_of(head) or ["без шага"]:
                chosen.setdefault(step, []).append((head.get("id"), kbquestions.title(lines, body)))
                break
    if not chosen:
        print(f"Вопросов для пакета нет: «{role}», кто отвечает — бизнес, MVP, открытые, заданные")
        return 1
    today = kbbase.today().isoformat()
    text = [f"# Вопросы для бизнеса: {role} — {today}", "",
            "Собрал `kb package`. Заполните колонку «Ответ». Колонку «ID» не меняйте.",
            "Загрузить ответы: `python3 kb/tools/kb.py package load <этот файл>`.", ""]
    count = 0
    for step, items in sorted(chosen.items()):
        step_path = kbbase.find_object(root, step)
        name = kbbase.front_matter(kbbase.read_lines(step_path))[0].get("шаг", "") if step_path else ""
        text += [f"## {step} {name}".rstrip(), "", "| ID | Вопрос | Ответ |", "|---|---|---|"]
        text += [f"| {ident} | {question} | |" for ident, question in items]
        text.append("")
        count += len(items)
    folder = root / "journal" / "packages"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{today}-{slug(role)}.md"
    path.write_text("\n".join(text), encoding="utf-8")
    print(f"ПАКЕТ: {kbbase.rel(root, path)} — вопросов {count}")
    return 0


def load(root: Path, team: kbbase.Team, file: str) -> int:
    path = Path(file)
    if not path.is_absolute():
        path = (Path.cwd() / file) if (Path.cwd() / file).is_file() else root / file
    if not path.is_file():
        print(f"ОШИБКА: файла пакета нет: {file}")
        return 1
    rel = kbbase.rel(root, path.resolve()) if path.resolve().is_relative_to(root.resolve()) else None
    if not rel or not rel.startswith("journal/packages/"):
        print("ОШИБКА: пакет должен лежать в `kb/journal/packages/` — ответ ссылается на него строкой")
        return 1
    lines = kbbase.read_lines(path)
    loaded, skipped, errors = [], [], []
    for i, cells in kbbase.table_rows(lines, 0, len(lines)):
        if len(cells) < 3 or not cells[2].strip():
            continue
        ident, answer = cells[0].strip(), cells[2].strip()
        q = kbbase.find_object(root, ident)
        if q is None:
            errors.append(f"строка {i + 1}: {ident} нет в журнале")
            continue
        head = kbbase.front_matter(kbbase.read_lines(q))[0]
        if head.get("статус") != "открыт":
            skipped.append(f"{ident} — уже «{head.get('статус')}»")
            continue
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = kbquestions.answer(root, team, ident, [answer, f"{rel}:{i + 1}", "подтверждено"])
        if code:
            errors.append(f"строка {i + 1}: {ident}: {buf.getvalue().strip()}")
        else:
            loaded.append(ident)
    print(f"ЗАГРУЖЕНО ответов: {len(loaded)}" + (f" ({', '.join(loaded)})" if loaded else ""))
    for s in skipped:
        print(f"ПРОПУЩЕНО: {s}")
    for e in errors:
        print(f"ОШИБКА: {e}")
    if loaded:
        print("Статус этих вопросов — «ответ найден». Закрывает автор вопроса: kb q close Q-…")
    return 1 if errors else 0
