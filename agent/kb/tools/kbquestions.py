"""Вопросы: в журнал — только по «задаём», «кто отвечает» вместо типа, дубли, закрытие, списки по шагам.

Формат — kb/rules/formats.md, раздел 3. Файлы вопросов лежат плоско в journal/questions/ и не переезжают.
"""
from __future__ import annotations

import re
from pathlib import Path

import kbbase
import kbcheck
import kbfacts
import kbglossary

WHO = kbcheck.WHO_ANSWERS
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WORD = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
STOP = {"какой", "какая", "какие", "каких", "когда", "который", "которые", "сколько", "почему", "зачем",
        "чтобы", "если", "или", "это", "для", "как", "что", "кто", "где", "при", "над", "под", "без"}


# ---------- общее ----------

def load(path: Path) -> tuple[list[str], dict[str, str], int]:
    lines = kbbase.read_lines(path)
    head, body = kbbase.front_matter(lines)
    return lines, head, body


def title(lines: list[str], body: int) -> str:
    for line in lines[body:]:
        if line.startswith("# "):
            return re.sub(r"^#\s+\S+\s+", "", line).strip()
    return ""


def steps_of(head: dict[str, str]) -> list[str]:
    return re.findall(r"PM-\d+", head.get("шаг", ""))


def set_field(lines: list[str], key: str, value: str) -> None:
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            lines.insert(i, f"{key}: {value}")
            return
        if line.split(":", 1)[0].strip() == key:
            lines[i] = f"{key}: {value}"
            return


def section_end(lines: list[str], body: int, name: str) -> int | None:
    for sec, start, end in kbbase.sections(lines, body):
        if sec == name:
            last = start
            for i in range(start + 1, end):
                if lines[i].strip():
                    last = i
            return last + 1
    return None


def save(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def all_questions(root: Path) -> list[tuple[Path, list[str], dict[str, str], int]]:
    folder = root / "journal" / "questions"
    result = []
    for path in sorted(folder.glob("Q-*.md")):
        lines, head, body = load(path)
        result.append((path, lines, head, body))
    return result


def fix_who(value: str, fixes: list[str]) -> str | None:
    v = value.strip()
    if v in WHO:
        return v
    if v.lower() in kbcheck.WHO_ENGLISH:
        new = kbcheck.WHO_ENGLISH[v.lower()]
        fixes.append(f"ПОПРАВЛЕНО: `{v}` → `{new}`")
        return new
    return None


def yes_no(value: str, fixes: list[str], key: str) -> str | None:
    v = value.strip().lower()
    table = {"да": "да", "нет": "нет", "yes": "да", "no": "нет", "true": "да", "false": "нет"}
    if key == "MVP":
        table.update({"mvp": "да", "позже": "позже", "later": "позже"})
    if v not in table:
        return None
    if table[v] != value.strip():
        fixes.append(f"ПОПРАВЛЕНО: `{value.strip()}` → `{table[v]}`")
    return table[v]


# ---------- похожие ----------

def stems(text: str) -> set[str]:
    return {w.lower()[:5] for w in WORD.findall(text) if len(w) > 3 and w.lower() not in STOP}


def similarity(a: str, b: str) -> float:
    sa, sb = stems(a), stems(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def similar(root: Path, text: str, limit: int = 5) -> list[tuple[float, str, str]]:
    """Кандидаты: вопросы и факты базы, похожие по словам. Дубль ли это — решает человек."""
    found = []
    text = kbglossary.expand(root, text)
    for path, lines, head, body in all_questions(root):
        score = similarity(text, kbglossary.expand(root, title(lines, body)))
        if score >= 0.25:
            found.append((score, head.get("id", path.stem), f"вопрос, {head.get('статус', '')}: {title(lines, body)}"))
    for path in sorted((root / "context" / "process").glob("PM-*.md")):
        lines = kbbase.read_lines(path)
        ident = path.stem.split("-")[0] + "-" + path.stem.split("-")[1]
        for line in lines:
            if "(ист:" in line:
                score = similarity(text, kbglossary.expand(root, line.split("(ист:")[0]))
                if score >= 0.3:
                    found.append((score, ident, f"факт: {line.strip()}"))
    found.sort(key=lambda x: -x[0])
    return found[:limit]


# ---------- команды ----------

def parse_fields(args: list[str]) -> dict[str, str]:
    fields = {}
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            fields[key.strip().lower()] = value.strip()
    return fields


def ask_send(root: Path, team: kbbase.Team, person: str, slug: str, args: list[str]) -> int:
    """Вопрос в журнал. Вызов — только после «задаём»: окно OpenCode (ask) на этой команде."""
    fields = parse_fields(args)
    fixes: list[str] = []
    errors = []
    if not SLUG.match(slug):
        errors.append(f"Короткое имя «{slug}» — латиница, цифры и дефис: `porog-dolga`")
    question = fields.get("вопрос", "")
    if not question:
        errors.append('Нет текста вопроса: вопрос="…"')
    steps = re.findall(r"PM-\d+", fields.get("шаг", ""))
    if not steps:
        errors.append("Нет шага: шаг=PM-02 или шаг=PM-02,PM-05")
    for step in steps:
        if kbbase.find_object(root, step) is None:
            errors.append(f"Шага {step} нет в базе")
    who = fix_who(fields.get("кто", fields.get("кто отвечает", "")), fixes)
    if who is None:
        errors.append(f"«кто={fields.get('кто', '')}» — неизвестно. Значения: {', '.join(WHO)}")
    mvp = yes_no(fields.get("mvp", "да"), fixes, "MVP")
    blocks = yes_no(fields.get("блокирует", "нет"), fixes, "блокирует")
    if mvp is None:
        errors.append("MVP — да или позже")
    if blocks is None:
        errors.append("блокирует — да или нет")
    origin = fields.get("откуда", "")
    if not origin:
        errors.append("Откуда вопрос: строка материала или «со слов кто, дата»")
    else:
        origin = kbfacts.fix_source(origin, fixes)
        fact = kbfacts.Fact("вопрос", origin, "оценка")
        errors += [p.message for p in kbfacts.check_source(root, team, fact)]
    errors += [p.message for p in kbfacts.check_text(question + fields.get("контекст", ""))]
    ident = f"Q-new-{slug}"
    if (root / "journal" / "questions" / f"{ident}.md").exists():
        errors.append(f"{ident} уже есть. Возьмите другое короткое имя")
    for f in fixes:
        print(f)
    if errors:
        for e in errors:
            print(f"ОШИБКА: {e}")
        print("ВОПРОС НЕ ЗАВЕДЁН")
        return 1
    today = kbbase.today().isoformat()
    text = [
        "---", f"id: {ident}", "статус: открыт", f"кто отвечает: {who}",
        f"шаг: {steps[0] if len(steps) == 1 else '[' + ', '.join(steps) + ']'}",
        f"MVP: {mvp}", f"блокирует: {blocks}", f"автор: {person}", f"задаём: {person}, {today}",
        "рассортировал: —", f"откуда: {origin}", "---",
        f"# {ident} {question}", "", "## Контекст", fields.get("контекст", ""), "",
        "## Что уже знаем", "", "## Подписчики", "", "## Ответ", "",
    ]
    folder = root / "journal" / "questions"
    folder.mkdir(parents=True, exist_ok=True)
    save(folder / f"{ident}.md", text)
    print(f"ВОПРОС ЗАВЕДЁН: journal/questions/{ident}.md")
    print(f"{ident} {question}")
    print(f"Кто отвечает: {who}. Аналитик увидит его в «Новые, не рассортированы».")
    for score, other, what in similar(root, question):
        if other != ident and what.startswith("вопрос"):
            print(f"ПРЕДУПРЕЖДЕНИЕ: похож на {other}. Это тот же вопрос? Можно стать подписчиком: {what}")
    write_views(root)
    return 0


def find(root: Path, ident: str) -> Path | None:
    path = kbbase.find_object(root, ident)
    if path is None:
        print(f"ОШИБКА: {ident} нет в журнале")
    return path


def is_analyst(team: kbbase.Team, person: str) -> bool:
    return "аналитик" in team.roles.get(person, "")


def sort(root: Path, team: kbbase.Team, person: str, ident: str, args: list[str]) -> int:
    """Аналитик сортирует новый вопрос: кто отвечает."""
    if not is_analyst(team, person):
        print("ОШИБКА: сортирует вопросы аналитик")
        return 1
    path = find(root, ident)
    if path is None:
        return 1
    fixes: list[str] = []
    fields = parse_fields(args)
    who = fix_who(fields.get("кто", fields.get("кто отвечает", "")), fixes)
    for f in fixes:
        print(f)
    if who is None:
        print(f"ОШИБКА: кто= — одно из: {', '.join(WHO)}")
        return 1
    role = fields.get("роль", "")
    if role and role.lower() not in team.business:
        print(f"ОШИБКА: роли «{role}» нет в `team.md`, раздел «Роли бизнеса»")
        return 1
    lines, _, _ = load(path)
    set_field(lines, "кто отвечает", who)
    if role:
        set_field(lines, "роль бизнеса", role)
    set_field(lines, "рассортировал", f"{person}, {kbbase.today().isoformat()}")
    save(path, lines)
    print(f"РАССОРТИРОВАН: {ident} — кто отвечает: {who}")
    write_views(root)
    return 0


def answer(root: Path, team: kbbase.Team, ident: str, args: list[str]) -> int:
    """Ответ с источником. Статус — «ответ найден»; закрывает автор: kb q close."""
    fields = {k: v for k, v in parse_fields(args).items() if k == "записан"}
    rest = [a for a in args if not a.startswith("записан=")]
    path = find(root, ident)
    if path is None:
        return 1
    line = rest[0] if len(rest) == 1 else (f"- {rest[0]} (ист: {rest[1]}; {rest[2]})" if len(rest) == 3 else "")
    if not line:
        print('ОШИБКА: нужно `kb q answer Q-… "<ответ>" "<источник>" <статус> [записан=PM-02#Вход]`')
        return 1
    fact, fixes, problems = kbfacts.check(root, team, line)
    for f in fixes:
        print(f)
    for p in problems:
        print(f"{p.level}: {p.message}")
    if fact is None or any(p.level == "ОШИБКА" for p in problems):
        print("НЕ ЗАПИСАНО")
        return 1
    lines, head, body = load(path)
    if head.get("статус") in kbcheck.CLOSED:
        print(f"ОШИБКА: {ident} уже закрыт ({head.get('статус')})")
        return 1
    at = section_end(lines, body, "Ответ")
    if at is None:
        print(f"ОШИБКА: в {ident} нет раздела «Ответ»")
        return 1
    new = [fact.line()] + ([f"Записан в: {fields['записан']}"] if "записан" in fields else [])
    lines[at:at] = new
    set_field(lines, "статус", "ответ найден")
    save(path, lines)
    print(f"ОТВЕТ ЗАПИСАН: {ident} — статус «ответ найден». Закрывает автор ({head.get('автор', '—')}): "
          f"python3 kb/tools/kb.py q close {ident}")
    write_views(root)
    return 0


def close(root: Path, person: str, ident: str) -> int:
    """Автор подтверждает: ответ его устраивает."""
    path = find(root, ident)
    if path is None:
        return 1
    lines, head, body = load(path)
    author = head.get("автор", "")
    if person != author:
        print(f"ОШИБКА: закрытие подтверждает автор вопроса: {author}")
        return 1
    if head.get("статус") != "ответ найден":
        print(f"ОШИБКА: у {ident} нет ответа. Сначала: kb q answer {ident} …")
        return 1
    at = section_end(lines, body, "Ответ")
    lines.insert(at, f"Закрыл: {person}, {kbbase.today().isoformat()}")
    set_field(lines, "статус", "отвечен")
    save(path, lines)
    print(f"ЗАКРЫТ: {ident}")
    write_views(root)
    return 0


def out(root: Path, team: kbbase.Team, person: str, args: list[str]) -> int:
    """Вне системы: пачкой, с одной причиной. Решает аналитик."""
    if not is_analyst(team, person):
        print("ОШИБКА: «вне системы» ставит аналитик")
        return 1
    idents = [a for a in args if re.match(r"^Q-[\w-]+$", a)]
    reason = " ".join(a for a in args if a not in idents).strip()
    if not idents or not reason:
        print('ОШИБКА: нужно `kb q out Q-… Q-… "причина"`')
        return 1
    paths = []
    for ident in idents:
        path = find(root, ident)
        if path is None:
            return 1
        paths.append((ident, path))
    today = kbbase.today().isoformat()
    for ident, path in paths:
        lines, head, body = load(path)
        at = section_end(lines, body, "Ответ")
        lines[at:at] = [f"- Вне системы: {reason} (ист: со слов {person}, {today}; подтверждено)",
                        f"Закрыл: {person}, {today}, пачкой: {', '.join(idents)}"]
        set_field(lines, "статус", "вне системы")
        save(path, lines)
    print(f"ВНЕ СИСТЕМЫ: {', '.join(idents)} — {reason}")
    write_views(root)
    return 0


# ---------- списки ----------

def is_new(head: dict[str, str]) -> bool:
    return head.get("рассортировал", "—") in ("", "—")


def render_views(root: Path) -> dict[str, str]:
    """journal/views/*.md: имя файла → текст. Без дат — чтобы списки не менялись зря."""
    questions = all_questions(root)
    by_step: dict[str, list] = {}
    for item in questions:
        for step in steps_of(item[2]) or ["без шага"]:
            by_step.setdefault(step, []).append(item)
    views = {}
    note = "Строит `kb views`. Руками не править: правка пропадёт при следующей сборке."

    def row(item):
        _, lines, head, body = item
        return (f"| {head.get('id', '')} | {title(lines, body)} | {head.get('кто отвечает', '')} | "
                f"{head.get('блокирует', '')} | {head.get('MVP', '')} | {head.get('автор', '')} |")

    header = ["| ID | Вопрос | Кто отвечает | Блокирует | MVP | Автор |", "|---|---|---|---|---|---|"]
    for step, items in sorted(by_step.items()):
        if step == "без шага":
            continue
        step_path = kbbase.find_object(root, step)
        name = kbbase.front_matter(kbbase.read_lines(step_path))[0].get("шаг", "") if step_path else ""
        groups = [
            ("Открытые", [i for i in items if i[2].get("статус") == "открыт"]),
            ("Ответ найден — ждёт подтверждения автора", [i for i in items if i[2].get("статус") == "ответ найден"]),
            ("Закрытые", [i for i in items if i[2].get("статус") not in ("открыт", "ответ найден")]),
        ]
        blocking = sum(1 for i in groups[0][1] + groups[1][1] if i[2].get("блокирует") == "да")
        text = [f"# Вопросы {step} {name}".rstrip(), "", note, ""]
        if blocking:
            text += [f"Ждёт ответов: блокирующих {blocking}", ""]
        for label, group in groups:
            text += [f"## {label} — {len(group)}", ""]
            if group:
                if label == "Закрытые":
                    text += ["| ID | Вопрос | Как закрыт |", "|---|---|---|"]
                    text += [f"| {h.get('id', '')} | {title(l, b)} | {h.get('статус', '')} |" for _, l, h, b in group]
                else:
                    text += header + [row(i) for i in group]
                text.append("")
        views[f"{step}.md"] = "\n".join(text)

    open_items = [i for i in questions if i[2].get("статус") in ("открыт", "ответ найден")]
    new_items = [i for i in open_items if is_new(i[2])]
    text = ["# Все вопросы", "", note, "", f"## Новые, не рассортированы — {len(new_items)}", ""]
    if new_items:
        text += ["Аналитик: `python3 kb/tools/kb.py q sort Q-… кто=<бизнес|смежник|команда|аналитик|сами>`", ""]
        text += header + [row(i) for i in new_items] + [""]
    text += [f"## Открытые по «кто отвечает» — {len(open_items)}", ""]
    for who in WHO:
        group = [i for i in open_items if i[2].get("кто отвечает") == who]
        if group:
            text += [f"### {who} — {len(group)}", ""] + header + [row(i) for i in group] + [""]
    text += ["## По шагам", "", "| Шаг | Открыто | Блокирует | Закрыто |", "|---|---|---|---|"]
    for step, items in sorted(by_step.items()):
        opened = [i for i in items if i[2].get("статус") in ("открыт", "ответ найден")]
        block = sum(1 for i in opened if i[2].get("блокирует") == "да")
        text.append(f"| {step} | {len(opened)} | {block} | {len(items) - len(opened)} |")
    views["all.md"] = "\n".join(text) + "\n"
    return {k: v if v.endswith("\n") else v + "\n" for k, v in views.items()}


def write_views(root: Path) -> list[str]:
    folder = root / "journal" / "views"
    folder.mkdir(parents=True, exist_ok=True)
    wanted = render_views(root)
    changed = []
    for name, text in wanted.items():
        path = folder / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(name)
    for path in folder.glob("*.md"):
        if path.name not in wanted:
            path.unlink()
            changed.append(path.name)
    return changed
