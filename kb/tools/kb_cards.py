"""Карточки шага: части рута, которые разные люди разбирают параллельно.

Карточка — задача на разбор, а не второй источник правды: находки пишет тот, кто взял карточку,
в файл карточки; в шаг их переносит владелец рута командой `card accept`.
"""
from __future__ import annotations

import re
from pathlib import Path

FOLDER = "context/process/cards"
STATUSES = ["Открыта", "В работе", "На приёмке", "Принята"]
ID_PAT = r"^PM-\d{2}-K\d{2}$"
STEP_SECTIONS = ["Как сейчас", "Объём и время", "Правила", "Исключения", "Боль", "Участники", "Идентификаторы", "Данные",
                 "Вход и выход"]
UNASSIGNED = ("", "не назначен", "—", "-")


def taker(fm: dict) -> str:
    v = str(fm.get("взял", "")).split("#")[0].strip()
    return "" if v in UNASSIGNED or v.startswith("<") else v


def cards_of(kb, root: str | None = None) -> list[dict]:
    items = kb.load("card")
    return [c for c in items if not root or c["fm"].get("рут") == root]


def card_by_id(kb, ident: str) -> dict | None:
    return next((c for c in kb.load("card") if c["fm"].get("id") == ident), None)


def limit(kb) -> int:
    fm, _ = kb.team_info()
    v = str(fm.get("лимит карточек на человека", "2")).split("#")[0].strip()
    return int(v) if v.isdigit() else 2


def mine(kb, user: str) -> list[dict]:
    return [c for c in kb.load("card") if taker(c["fm"]) == user and c["fm"].get("статус") in ("В работе", "На приёмке")]


def _owner(kb, root: str) -> tuple[dict | None, list[str]]:
    st = kb.step_by_id(root)
    return st, (kb.pair_of(st["fm"]) if st else [])


def new_card(kb, root: str, slug: str, extra: list[str]) -> int:
    root = root.upper()
    st, pair = _owner(kb, root)
    if not st:
        print(f"ОШИБКА  нет шага {root}")
        return 1
    user = kb.git_user()
    _, roles = kb.team_info()
    if user not in pair and "аналитик" not in roles.get(user, ""):
        print(f"ОШИБКА  карточки создаёт владелец рута ({', '.join(pair) or 'не назначен'}) или аналитик. Вы — {user}. "
              "Предложите карточку вопросом «скоуп» владельцу")
        return 1
    fields, sections, title, bad = kb.parse_pairs(extra)
    if bad or not title:
        print("ОШИБКА  нужно: new card PM-XX <slug> заголовок=\"<тема>\" \"Что выяснить=…\" [\"С чего начать=…\"] "
              "[\"Готово, когда=…\"] [направления=[5,8]]")
        return 2
    slug = kb.slugify(slug)
    used = [int(m.group(1)) for c in cards_of(kb, root) if (m := re.search(r"-K(\d{2})$", str(c["fm"].get("id", ""))))]
    ident = f"{root}-K{max(used, default=0) + 1:02d}"
    folder = kb.KB / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    tpl = (folder / "_template.md").read_text(encoding="utf-8")
    text = tpl.replace("PM-XX-KNN", ident).replace("рут: PM-XX", f"рут: {root}").replace("раздел=PM-XX", f"раздел={root}")
    text = re.sub(r"^создал:.*$", f"создал: {user}", text, count=1, flags=re.M)
    text = re.sub(r"^создана:.*$", f"создана: {kb.TODAY.isoformat()}", text, count=1, flags=re.M)
    text = text.replace(f"# {ident} <Тема>", f"# {ident} {title}", 1)
    if "направления" in fields:
        v = fields.pop("направления")
        text = re.sub(r"^направления:.*$", f"направления: {v if v.startswith('[') else '[' + v + ']'}", text, count=1, flags=re.M)
    for h, v in sections.items():
        text, ok = kb.put_section(text, h, v, append=False)
        if not ok:
            print(f"ОШИБКА  в карточке нет раздела «{h}»")
            return 1
    target = folder / f"{ident}-{slug}.md"
    kb.write(target, text)
    print(f"{kb.rel(target)}  ({ident}) — {title}. Берут сами: python3 kb/tools/kb.py take {ident}")
    return 0


def take(kb, ident: str) -> int:
    c = card_by_id(kb, ident)
    if not c:
        print(f"Нет карточки {ident}")
        return 1
    user = kb.git_user()
    _, roles = kb.team_info()
    if user not in roles:
        print(f"ОШИБКА  {user} нет в team.md — попросите аналитика добавить")
        return 1
    fm = c["fm"]
    st, _ = _owner(kb, fm.get("рут", ""))
    if not st or st["fm"].get("статус") != "Разбор":
        print(f"ОШИБКА  карточки берут, когда рут {fm.get('рут')} в «Разборе»; сейчас — {st['fm'].get('статус') if st else 'нет шага'}")
        return 1
    who = taker(fm)
    if who == user:
        print(f"{ident} уже ваша")
        return 0
    if who:
        print(f"ОШИБКА  {ident} уже взял(а) {who}")
        return 1
    if fm.get("статус") not in ("Открыта",):
        print(f"ОШИБКА  {ident} в статусе «{fm.get('статус')}» — брать можно только открытую")
        return 1
    if len(mine(kb, user)) >= limit(kb):
        print(f"ОШИБКА  у вас уже {len(mine(kb, user))} карточки в работе — лимит {limit(kb)}. Сдайте или верните одну")
        return 1
    text, errs = kb.set_fields(c["text"], "card", {"взял": user, "статус": "В работе"})
    if errs:
        print("ОШИБКА  " + "; ".join(errs))
        return 1
    kb.write(c["path"], text)
    f = kb.rel(c["path"])
    print(f"{ident}: ваша, «В работе». Разбор: /kb-explore {ident}. Коммит сразу в основную ветку: "
          f"git commit -m \"{ident}: взял(а) карточку\" -m \"Скилл: kb-take\" -- {f} && git push")
    return 0


def release(kb, ident: str, why: str) -> int:
    c = card_by_id(kb, ident)
    if not c:
        print(f"Нет карточки {ident}")
        return 1
    user = kb.git_user()
    if taker(c["fm"]) != user:
        print(f"ОШИБКА  {ident} не ваша")
        return 1
    text, _ = kb.set_fields(c["text"], "card", {"взял": "не назначен", "статус": "Открыта"})
    text, _ = kb.put_section(text, "Приёмка", f"- {kb.TODAY}: вернул(а) {user}: {why}", append=True)
    kb.write(c["path"], text)
    print(f"{ident}: снова открыта. Находки остались в карточке — следующий продолжит с них.")
    return 0


def fact(kb, ident: str, heading: str, text_: str, source: str, status: str) -> int:
    c = card_by_id(kb, ident)
    if not c:
        print(f"Нет карточки {ident}")
        return 1
    user = kb.git_user()
    if taker(c["fm"]) != user:
        print(f"ОШИБКА  в карточку {ident} пишет тот, кто её взял ({taker(c['fm']) or 'никто'}). Возьмите: kb.py take {ident}")
        return 1
    if heading not in STEP_SECTIONS:
        print(f"ОШИБКА  раздел шага «{heading}» — можно: {', '.join(STEP_SECTIONS)}")
        return 1
    if status not in kb.FACT_STATUSES and not re.match(r"^допущение\s+A-[\w-]+$", status):
        print("ОШИБКА  статус: подтверждено / оценка / противоречие / допущение A-…")
        return 1
    if not source.strip():
        print("ОШИБКА  нужен источник: путь к материалу и место")
        return 1
    line = f"- {kb.unescape(text_).strip().rstrip('.')} ({source}, {status})"
    t = c["text"]
    m = re.search(r"^## Находки\n(.*?)(?=^## |\Z)", t, flags=re.M | re.S)
    body = m.group(1)
    sub = re.search(rf"^### {re.escape(heading)}\n(.*?)(?=^### |\Z)", body, flags=re.M | re.S)
    if sub:
        new_body = body[:sub.end()].rstrip("\n") + "\n" + line + "\n\n" + body[sub.end():].lstrip("\n")
    else:
        new_body = body.rstrip("\n") + f"\n\n### {heading}\n{line}\n\n"
    t = t[:m.start(1)] + new_body + t[m.end(1):]
    kb.write(c["path"], t)
    print(f"{kb.rel(c['path'])} «Находки / {heading}»: {line}")
    return 0


def findings(text: str) -> dict[str, list[str]]:
    m = re.search(r"^## Находки\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    out: dict[str, list[str]] = {}
    if not m:
        return out
    for sm in re.finditer(r"^### ([^\n]+)\n(.*?)(?=^### |\Z)", m.group(1), flags=re.M | re.S):
        lines = [l.strip() for l in sm.group(2).splitlines() if re.match(r"^\s*-\s+\S", l)]
        if lines:
            out[sm.group(1).strip()] = lines
    return out


def questions_of(text: str) -> list[str]:
    m = re.search(r"^## Вопросы\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return re.findall(r"\bQ-(?:\d{3}|new-[\w-]+)", m.group(1)) if m else []


def before_status(kb, path: Path, new: str) -> str | None:
    """Проверка перед set статус=… для карточки. Возвращает ошибку или None."""
    text = path.read_text(encoding="utf-8")
    fm = kb.parse_frontmatter(text)
    user = kb.git_user()
    if new == "На приёмке":
        if taker(fm) != user:
            return f"сдаёт карточку тот, кто её взял ({taker(fm) or 'никто'})"
        if not findings(text) and not questions_of(text):
            return "в карточке нет ни одной находки и ни одного вопроса — сдавать нечего"
    if new in ("Принята",):
        return "принимает владелец рута командой: python3 kb/tools/kb.py card accept " + str(fm.get("id"))
    return None


def accept(kb, ident: str) -> int:
    c = card_by_id(kb, ident)
    if not c:
        print(f"Нет карточки {ident}")
        return 1
    root = c["fm"].get("рут", "")
    st, pair = _owner(kb, root)
    user = kb.git_user()
    if not st or user != st["fm"].get("владелец рута"):
        print(f"ОШИБКА  карточку принимает владелец рута {root} ({st['fm'].get('владелец рута') if st else '—'}). Вы — {user}")
        return 1
    if c["fm"].get("статус") != "На приёмке":
        print(f"ОШИБКА  {ident} в статусе «{c['fm'].get('статус')}» — принимают сданную, «На приёмке»")
        return 1
    found = findings(c["text"])
    text = st["text"]
    moved, missing = 0, []
    for heading, lines in found.items():
        for l in lines:
            text, ok = kb.put_section(text, heading, f"{l} [{ident}]", append=True)
            if ok:
                moved += 1
            else:
                missing.append(heading)
    kb.write(st["path"], text)
    notes = re.search(r"^## Технические заметки\n(.*?)(?=^## |\Z)", c["text"], flags=re.M | re.S)
    nlines = [l for l in (notes.group(1).splitlines() if notes else []) if re.match(r"^\s*-\s+\S", l)]
    if nlines:
        for l in nlines:
            kb.cmd_append(root, "Технические заметки", f"{l.strip()[2:]} [{ident}]")
    ctext, _ = kb.set_fields(c["text"], "card", {"статус": "Принята"})
    ctext, _ = kb.put_section(ctext, "Приёмка", f"- {kb.TODAY}: принял(а) {user}; перенесено фактов: {moved}"
                              + (f"; нет разделов в шаге: {', '.join(sorted(set(missing)))}" if missing else ""), append=True)
    kb.write(c["path"], ctext)
    print(f"{ident}: принята. Перенесено в {kb.rel(st['path'])}: фактов {moved}, технических заметок {len(nlines)}."
          + (f" Не нашёл в шаге разделы: {', '.join(sorted(set(missing)))} — перенесите руками." if missing else ""))
    print(f"Коммит: git commit -m \"{root}: приняты находки {ident}\" -m \"Скилл: kb-explore\" -- {kb.rel(st['path'])} {kb.rel(c['path'])}")
    return 0


def return_card(kb, ident: str, why: str) -> int:
    c = card_by_id(kb, ident)
    if not c:
        print(f"Нет карточки {ident}")
        return 1
    st, _ = _owner(kb, c["fm"].get("рут", ""))
    user = kb.git_user()
    if not st or user != st["fm"].get("владелец рута"):
        print("ОШИБКА  вернуть карточку может владелец рута")
        return 1
    text, _ = kb.set_fields(c["text"], "card", {"статус": "В работе"})
    text, _ = kb.put_section(text, "Приёмка", f"- {kb.TODAY}: вернул(а) {user}: {why}", append=True)
    kb.write(c["path"], text)
    print(f"{ident}: обратно «В работе» у {taker(c['fm'])}. Причина записана в «Приёмка».")
    return 0


def cmd_cards(kb, args: list[str]) -> int:
    root = args[0].upper() if args else None
    items = cards_of(kb, root)
    if not root:
        in_work = {s["fm"].get("id") for s in kb.load("step") if s["fm"].get("статус") == "Разбор"}
        items = [c for c in items if c["fm"].get("рут") in in_work and c["fm"].get("статус") == "Открыта"]
        prio = {s["fm"].get("id"): str(s["fm"].get("приоритет", "9")) for s in kb.load("step")}
        items.sort(key=lambda c: (prio.get(c["fm"].get("рут"), "9"), c["fm"].get("id")))
        title = "Открытые карточки рутов в «Разборе» (по приоритету рута)"
    else:
        title = f"Карточки {root}"
    print(f"== {title}")
    if not items:
        print("—")
        return 0
    for c in items:
        fm = c["fm"]
        what = " ".join(l.strip() for l in kb.section_text(c["text"], "Что выяснить").splitlines()
                        if l.strip() and not kb.is_placeholder(l))[:120]
        print(f"{fm.get('id')} [{fm.get('статус')}{', ' + taker(fm) if taker(fm) else ''}] {kb.first_heading(c['text'])}"
              + (f" — {what}" if what else ""))
    if not root:
        print("Взять: python3 kb/tools/kb.py take PM-XX-KNN")
    return 0


def check(kb, all_items: dict, errors: list, warnings: list) -> None:
    steps = {s["fm"].get("id") for s in all_items["step"]}
    team = kb.team_names() or set()
    cards = {c["fm"].get("id") for c in all_items.get("card", [])}
    for c in all_items.get("card", []):
        fm, where = c["fm"], kb.rel(c["path"])
        if fm.get("рут") not in steps:
            errors.append(f"{where}: рут {fm.get('рут')} не найден")
        if not str(fm.get("id", "")).startswith(str(fm.get("рут", "")) + "-K"):
            errors.append(f"{where}: ID {fm.get('id')} не из рута {fm.get('рут')}")
        who = taker(fm)
        if who and who not in team:
            warnings.append(f"{where}: «взял: {who}» — нет такого человека в team.md")
        if fm.get("статус") in ("В работе", "На приёмке") and not who:
            errors.append(f"{where}: статус «{fm.get('статус')}», а «взял» пусто")
        for heading, lines in findings(c["text"]).items():
            if heading not in STEP_SECTIONS:
                warnings.append(f"{where}: «Находки / {heading}» — нет такого раздела шага; можно: {', '.join(STEP_SECTIONS)}")
            for l in lines:
                if not kb.FACT_STATUS_RE.search(l):
                    warnings.append(f"{where}: факт без статуса в скобках — «{l[:70]}»")
    for q in all_items.get("question", []):
        v = str(q["fm"].get("карточка", "")).split("#")[0].strip()
        if v and v not in UNASSIGNED and v not in cards:
            errors.append(f"{kb.rel(q['path'])}: карточка {v} не найдена")


def summary_line(kb, root: str) -> str:
    cs = cards_of(kb, root)
    if not cs:
        return ""
    done = sum(1 for c in cs if c["fm"].get("статус") == "Принята")
    return f"{done}/{len(cs)}"
