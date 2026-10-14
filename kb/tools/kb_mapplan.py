"""Пересборка карты процессов одним пакетом: kb.py map apply / map sync.

map apply <план> [--check] — применить план «Было → Станет» (kb/context/process/_map-plan.md).
map sync — после ручной правки таблицы «Шаги» в _map.md: создать недостающие шаги, обновить порядок.
Взятые руты не режет и не объединяет: заводит вопрос «скоуп» владельцу. Номера шагов не переиспользует.
"""
from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

PM_RE = re.compile(r"\bPM-(\d{2})\b")
NEW_RE = re.compile(r"\bN(\d+)\b")
PLAN = "context/process/_map-plan.md"
MAP = "context/process/_map.md"
UNASSIGNED = ("", "не назначен", "—", "-")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(text: str, first_header: str) -> list[list[str]]:
    rows, started = [], False
    for line in text.splitlines():
        if line.startswith("|"):
            c = _cells(line)
            if not started:
                if c and c[0] == first_header:
                    started = True
                continue
            if set(line.replace("|", "").strip()) <= set("-: "):
                continue
            rows.append(c)
        elif started:
            break
    return rows


def _items(cell: str) -> list[tuple[str, str]]:
    """«PM-04 Сопоставление + N1 Возвраты» → [("PM-04", "Сопоставление"), ("N1", "Возвраты")]; «—» → []"""
    cell = cell.strip()
    if cell in ("", "—", "-"):
        return []
    out = []
    for part in cell.split("+"):
        part = part.strip()
        m = re.match(r"^(PM-\d{2}|N\d+)\b\s*(.*)$", part)
        if not m:
            raise ValueError(f"«{part}»: в «Станет» нужен ID шага (PM-XX) или N1, N2… для нового")
        out.append((m.group(1), m.group(2).strip()))
    return out


def _owners(st: dict) -> list[str]:
    fm = st["fm"]
    return [str(fm.get(f, "")).split("#")[0].strip() for f in ("владелец рута", "напарник")
            if str(fm.get(f, "")).split("#")[0].strip() not in UNASSIGNED]


def _sub_new(text: str, ids: dict[str, str]) -> str:
    """N1 → PM-08 в подписях, N1 → PM08 в ID узлов mermaid."""
    def in_quotes(m):
        return '"' + NEW_RE.sub(lambda k: ids.get(k.group(0), k.group(0)), m.group(1)) + '"'
    text = re.sub(r'"([^"]*)"', in_quotes, text)
    return NEW_RE.sub(lambda k: ids.get(k.group(0), k.group(0)).replace("-", ""), text)


def _mermaid(text: str) -> str | None:
    m = re.search(r"```mermaid\s*\n.*?```", text, flags=re.S)
    return m.group(0) if m else None


def _set_order(kb, order: list[str]) -> None:
    n = len(order)
    for st in kb.load("step"):
        sid = st["fm"].get("id")
        if sid in order:
            text, _ = kb.set_fields(st["text"], "step", {"порядок": f"{order.index(sid) + 1} из {n}"})
            kb.write(st["path"], text)


def _question(kb, slug: str, section: str, title: str, ctx: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        kb.cmd_new_filled("question", slug, ["тип=скоуп", f"раздел={section}", f"заголовок={title}", f"Контекст={ctx}"])
    return f"Q-new-{kb.slugify(slug)}"


def _log(map_text: str, rows: list[str]) -> str:
    sec = re.search(r"^## Изменения структуры\n.*?(?=^## |\Z)", map_text, flags=re.M | re.S)
    if not sec:
        return map_text.rstrip() + "\n\n## Изменения структуры\n| Дата | Что изменилось | Почему | Вопрос |\n|---|---|---|---|\n" \
            + "\n".join(rows) + "\n"
    body = sec.group(0).rstrip("\n") + "\n" + "\n".join(rows) + "\n"
    rest = map_text[sec.end():]
    return map_text[:sec.start()] + body + ("\n" + rest if rest.strip() else "")


def _replace_steps_table(map_text: str, rows: list[list[str]]) -> str:
    lines = map_text.split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("| ID | Шаг"))
    except StopIteration:
        return map_text
    end = start + 2
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    head = lines[start:start + 2]
    lines[start:end] = head + ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _is_analyst(kb) -> tuple[bool, str]:
    user = kb.git_user()
    _, roles = kb.team_info()
    r = roles.get(user, "")
    return ("аналитик" in r or "руководитель" in r), user


def plan(kb, plan_path: Path) -> tuple[list[dict], str, str | None]:
    text = plan_path.read_text(encoding="utf-8")
    rows = _table(text, "Было")
    if not rows:
        raise ValueError("в плане нет таблицы «| Было | Станет | Что | Почему (источник) |»")
    base = re.search(r"^Основание:\s*(.+)$", text, flags=re.M)
    ops = []
    for r in rows:
        r = r + [""] * (4 - len(r))
        was = PM_RE.search(r[0])
        was_id = f"PM-{was.group(1)}" if was else None
        becomes = _items(r[1])
        ops.append({"was": was_id, "becomes": becomes, "what": r[2], "why": r[3]})
    return ops, (base.group(1).strip() if base else ""), _mermaid(text)


def apply(kb, args: list[str]) -> int:
    check_only = "--check" in args
    pos = [a for a in args if not a.startswith("--")]
    plan_path = Path(pos[0]) if pos else kb.KB / PLAN
    if not plan_path.is_absolute():
        plan_path = (Path.cwd() / plan_path) if (Path.cwd() / plan_path).exists() else kb.KB / kb.kb_rel(str(plan_path))
    if not plan_path.is_file():
        print(f"Нет плана {kb.rel(plan_path) if str(plan_path).startswith(str(kb.ROOT)) else plan_path}. "
              f"Агент пишет таблицу «Было → Станет» в kb/{PLAN} (шаблон — kb/context/process/_map-plan-template.md)")
        return 1
    ok, user = _is_analyst(kb)
    if not ok and not check_only:
        print(f"ОШИБКА  структуру карты меняет аналитик. Вы — {user}. Заведите вопрос «скоуп» аналитику и приложите план: "
              f"python3 kb/tools/kb.py new question <slug> тип=скоуп раздел=PM-00 заголовок=\"Пересобрать карту по плану\" "
              f"\"Контекст=план — kb/{PLAN}\"")
        return 1
    try:
        ops, base, schema = plan(kb, plan_path)
    except ValueError as e:
        print(f"ОШИБКА  {e}")
        return 1
    mp = kb.KB / MAP
    if not mp.exists():
        print("ОШИБКА  нет kb/context/process/_map.md — сначала черновик карты (kb-map)")
        return 1
    steps = {st["fm"].get("id"): st for st in kb.load("step")}
    errors, actions, questions, new_names = [], [], [], {}
    for o in ops:
        ids = [i for i, _ in o["becomes"]]
        for i, name in o["becomes"]:
            if i.startswith("N"):
                if not name:
                    errors.append(f"{i}: у нового шага нет названия")
                new_names[i] = name
            elif i not in steps:
                errors.append(f"{i} — такого шага нет; новый шаг пишите как N1, N2…")
        if o["was"] and o["was"] not in steps:
            errors.append(f"«Было» {o['was']} — такого шага нет")
    if errors:
        for e in errors:
            print(f"ОШИБКА  {e}")
        return 1

    # что делаем
    for o in ops:
        was, bec = o["was"], o["becomes"]
        ids = [i for i, _ in bec]
        if not was:
            for i, n in bec:
                actions.append(("new", i, n, o))
            continue
        st = steps[was]
        taken = _owners(st)
        if not bec:
            actions.append(("remove", was, "", o))
        elif was in ids:
            name = dict(bec)[was]
            if name and name != st["fm"].get("шаг"):
                actions.append(("rename", was, name, o))
            for i, n in bec:
                if i != was:
                    actions.append(("new", i, n, o))
            if len(bec) > 1 and taken:
                actions.append(("ask-split", was, ", ".join(i for i in ids if i != was), o))
        else:
            target = ids[0]
            if len(ids) == 1 and not target.startswith("N"):
                actions.append(("merge", was, target, o))
            else:
                errors.append(f"{was}: «Станет» без {was} — для объединения укажите один существующий шаг, для удаления «—»")
    if errors:
        for e in errors:
            print(f"ОШИБКА  {e}")
        return 1

    blocked = []
    for kind, a, b, o in actions:
        if kind in ("merge", "remove"):
            st = steps[a]
            dec = kb.decomposits_of(st["text"], a)
            owners = _owners(st) + (_owners(steps[b]) if kind == "merge" else [])
            if owners or dec:
                blocked.append((kind, a, b, o, owners, dec))
    print("== План")
    for kind, a, b, o in actions:
        label = {"new": f"новый шаг {a}: {b}", "rename": f"{a}: переименовать в «{b}»", "merge": f"{a} объединить в {b}",
                 "remove": f"{a} убрать", "ask-split": f"{a} делится — владелец переносит факты в {b}"}[kind]
        if any(x[0] == kind and x[1] == a for x in blocked):
            label += " — НЕЛЬЗЯ сейчас: рут взят или нарезан, уйдёт вопрос владельцу"
        print(f"- {label}")
    if schema is None:
        print("- схема: в плане нет блока mermaid — схему в _map.md обновите сами")
    if check_only:
        print("Проверка плана: ошибок нет. Применить: python3 kb/tools/kb.py map apply")
        return 0

    today = kb.TODAY.isoformat()
    log_rows, new_ids = [], {}
    # новые шаги
    for kind, a, b, o in actions:
        if kind != "new":
            continue
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kb.cmd_new_step_or_system("step", kb.slugify(b)[:40] or "shag", [f"шаг={b}"])
        m = PM_RE.search(buf.getvalue())
        if rc != 0 or not m:
            print(buf.getvalue())
            return 1
        new_ids[a] = f"PM-{m.group(1)}"
        log_rows.append(f"| {today} | Новый шаг {new_ids[a]} «{b}»" + (f" (из {o['was']})" if o["was"] else "")
                        + f" | {o['why'] or base} | — |")
    steps = {st["fm"].get("id"): st for st in kb.load("step")}
    # переименования
    for kind, a, b, o in actions:
        if kind == "rename":
            st = steps[a]
            text, _ = kb.set_fields(st["text"], "step", {"шаг": b})
            text = re.sub(rf"^# {a}\b.*$", f"# {a} {b}", text, count=1, flags=re.M)
            kb.write(st["path"], text)
            log_rows.append(f"| {today} | {a}: «{st['fm'].get('шаг')}» → «{b}» | {o['why'] or base} | — |")
        if kind == "ask-split":
            targets = ", ".join(new_ids.get(x.strip(), x.strip()) for x in b.split(","))
            q = _question(kb, f"perenos-{a.lower()}", a, f"Перенести факты из {a} в {targets} после разделения шага",
                          f"Аналитик разделил {a}: новая часть — {targets}. Основание: {o['why'] or base}. "
                          f"Владелец переносит свои факты сам и отвечает здесь.")
            questions.append(q)
            log_rows.append(f"| {today} | {a} разделён: новая часть {targets} | {o['why'] or base} | {q} |")
    # объединения и удаления
    for kind, a, b, o in actions:
        if kind not in ("merge", "remove"):
            continue
        blk = next((x for x in blocked if x[0] == kind and x[1] == a), None)
        if blk:
            _, _, _, _, owners, dec = blk
            why = (f"взят: {', '.join(owners)}" if owners else "") + ("; " if owners and dec else "") + \
                  (f"есть декомпозиты {', '.join(dec)}" if dec else "")
            title = f"Объединить {a} с {b}?" if kind == "merge" else f"Убрать шаг {a}?"
            q = _question(kb, f"{kind}-{a.lower()}", a, title,
                          f"План пересборки карты: {o['why'] or base}. Сам скрипт не меняет: {why}. "
                          f"Владелец решает с аналитиком; при объединении владелец второго — напарник или release.")
            questions.append(q)
            log_rows.append(f"| {today} | Предложено: {title.rstrip('?')} — ждёт владельца | {o['why'] or base} | {q} |")
            continue
        st = steps[a]
        if kind == "merge":
            pat = re.compile(rf"(?<![\w-]){a}(?![\w-])")
            compact = a.replace("-", "")
            for p, txt in kb.text_files():
                if p == st["path"] or not str(p).startswith(str(kb.KB)) or p.name == "_renames.tsv":
                    continue
                new = pat.sub(b, txt)
                if p.name == "_map.md":
                    new = re.sub(rf"\b{compact}\b", b.replace("-", ""), new)
                if new != txt:
                    kb.write(p, new)
        st["path"].unlink()
        log_rows.append(f"| {today} | {a} «{st['fm'].get('шаг')}» " + (f"объединён в {b}" if kind == "merge" else "убран")
                        + f"; номер {a} больше не используется | {o['why'] or base} | — |")

    # таблица «Шаги», порядок, схема, журнал изменений
    mtext = mp.read_text(encoding="utf-8")
    old_rows = {r[0]: r for r in _table(mtext, "ID") if r}
    steps = {st["fm"].get("id"): st for st in kb.load("step")}
    order, new_rows = [], []
    if "PM-00" in old_rows:
        new_rows.append(old_rows["PM-00"])
    gone = {a for k, a, b, o in actions if k in ("merge", "remove") and not any(x[1] == a and x[0] == k for x in blocked)}
    for o in ops:
        seq = [(new_ids.get(i, i), n) for i, n in o["becomes"]] if o["becomes"] or not o["was"] else []
        if o["was"] and not o["becomes"] and o["was"] not in gone:
            seq = [(o["was"], "")]
        for i, n in seq:
            if i in order or i == "PM-00" or i not in steps:
                continue
            order.append(i)
            r = list(old_rows.get(i, [i, "", "—", "—", "—"]))
            r = r + ["—"] * (5 - len(r))
            r[0], r[1] = i, steps[i]["fm"].get("шаг", n)
            new_rows.append(r)
    for sid in sorted(steps):   # шаги, которых нет в плане, — на своих местах в конце
        if sid not in order and sid != "PM-00":
            order.append(sid)
            r = list(old_rows.get(sid, [sid, steps[sid]["fm"].get("шаг", ""), "—", "—", "—"]))
            new_rows.append(r + ["—"] * (5 - len(r)))
    mtext = _replace_steps_table(mtext, new_rows)
    if schema:
        cur = _mermaid(mtext)
        new_schema = _sub_new(schema, new_ids)
        mtext = mtext.replace(cur, new_schema, 1) if cur else mtext
    if log_rows:
        mtext = _log(mtext, log_rows)
    kb.write(mp, mtext)
    _set_order(kb, order)
    plan_path.unlink()
    print("== Применено")
    for k, v in new_ids.items():
        print(f"- {k} → {v}")
    print(f"- строк в «Изменения структуры»: {len(log_rows)}; порядок шагов обновлён; план {PLAN} удалён")
    for q in questions:
        print(f"- вопрос владельцу: {q} (покажите его человеку: kb.py show {q})")
    print("Дальше: python3 kb/tools/kb.py check; крупные изменения — сверить структуру с бизнесом, новые шаги — kb-map режим 3.")
    return 0


def sync(kb) -> int:
    ok, user = _is_analyst(kb)
    if not ok:
        print(f"ОШИБКА  структуру карты меняет аналитик. Вы — {user}")
        return 1
    mp = kb.KB / MAP
    if not mp.exists():
        print("ОШИБКА  нет kb/context/process/_map.md")
        return 1
    mtext = mp.read_text(encoding="utf-8")
    rows = [r for r in _table(mtext, "ID") if r and r[0]]
    steps = {st["fm"].get("id"): st for st in kb.load("step")}
    new_ids, created, order = {}, [], []
    for r in rows:
        rid = r[0]
        if rid == "PM-00" or kb.has_placeholder(rid) or rid.startswith("<"):
            continue
        if rid in steps:
            order.append(rid)
            continue
        name = r[1] if len(r) > 1 else ""
        if not name or kb.has_placeholder(name):
            print(f"Пропущено: строка «{rid}» без названия шага")
            continue
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kb.cmd_new_step_or_system("step", kb.slugify(name)[:40] or "shag", [f"шаг={name}"])
        m = PM_RE.search(buf.getvalue())
        if rc != 0 or not m:
            print(buf.getvalue())
            return 1
        nid = f"PM-{m.group(1)}"
        new_ids[rid] = nid
        created.append(f"{rid} → {nid} «{name}»")
        order.append(nid)
    if new_ids:
        lines = mtext.split("\n")
        for i, l in enumerate(lines):
            c = _cells(l) if l.startswith("|") else []
            if c and c[0] in new_ids:
                lines[i] = l.replace(c[0], new_ids[c[0]], 1)
        mtext = "\n".join(lines)
        sch = _mermaid(mtext)
        if sch:
            mtext = mtext.replace(sch, _sub_new(sch, {k: v for k, v in new_ids.items() if NEW_RE.fullmatch(k)}), 1)
        mtext = _log(mtext, [f"| {kb.TODAY.isoformat()} | Новый шаг {c} (map sync) | правка таблицы «Шаги» | — |" for c in created])
        kb.write(mp, mtext)
    _set_order(kb, order)
    extra = sorted(s for s in steps if s not in order and s != "PM-00")
    print("Создано: " + (", ".join(created) if created else "—"))
    print(f"Порядок обновлён: {', '.join(order) or '—'}")
    if extra:
        print("Нет в таблице «Шаги», но есть файлы: " + ", ".join(extra)
              + " — удалить или объединить? Решите и примените планом: kb.py map apply. Сам sync ничего не удаляет.")
    return 0


def run(kb, args: list[str]) -> int:
    if args and args[0] == "apply":
        return apply(kb, args[1:])
    if args and args[0] == "sync":
        return sync(kb)
    return -1
