"""Шаг карты: критерий глубины, «мне достаточно», статусы, взятие.

Правила — kb/rules/formats.md, раздел 2, и kb/rules/gates.md. Готовность шага решает владелец, не агент.
"""
from __future__ import annotations

import re
from pathlib import Path

import kbbase
import kbcheck
import kbquestions

STATUSES = kbbase.STEP_STATUSES
DEPTH = [("Зачем шаг бизнесу", "Зачем бизнесу"), ("Вход", "Вход"), ("Выход", "Выход"),
         ("Сущности и их поля", "Сущности"), ("Состояния", "Состояния"), ("Исключения", "Исключения"),
         ("MVP или позже", "Объём")]
IN_WORK = ("Разбор", "Нарезка")          # считаются в лимит шагов на человека
HISTORY = "История статуса"


def load(root: Path, ident: str) -> tuple[Path, list[str], dict[str, str], int] | None:
    path = kbbase.find_object(root, ident)
    if path is None or not ident.startswith("PM-"):
        print(f"ОШИБКА: шага {ident} нет в базе")
        return None
    lines = kbbase.read_lines(path)
    head, body = kbbase.front_matter(lines)
    return path, lines, head, body


def blocking(root: Path, ident: str) -> list[str]:
    result = []
    for path, lines, head, body in kbquestions.all_questions(root):
        if (ident in kbquestions.steps_of(head) and head.get("блокирует") == "да"
                and head.get("статус") in ("открыт", "ответ найден")):
            result.append(head.get("id", path.stem))
    return result


def add_history(lines: list[str], body: int, entry: str) -> None:
    for name, start, end in kbbase.sections(lines, body):
        if name == HISTORY:
            last = start
            for i in range(start + 1, end):
                if lines[i].strip():
                    last = i
            lines.insert(last + 1, entry)
            if last == start:
                lines.insert(last + 1, "")
            return
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ["", f"## {HISTORY}", "", entry]


def save(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- критерий глубины ----------

def gaps(root: Path, ident: str) -> int:
    loaded = load(root, ident)
    if loaded is None:
        return 1
    path, lines, head, body = loaded
    secs = {name: (start, end) for name, start, end in kbbase.sections(lines, body)}
    missing = 0
    print(f"Критерий глубины {ident} {head.get('шаг', '')}:")
    for label, section in DEPTH:
        if section not in secs:
            print(f"  ✗ {label}: нет раздела «{section}»")
            missing += 1
            continue
        start, end = secs[section]
        if section in ("Сущности", "Объём"):
            rows = kbbase.table_rows(lines, start, end)
            header = kbbase.table_header(lines, start, end)
            col = header.index("Источник") if "Источник" in header else None
            no_source = [c for _, c in rows if col is not None and (col >= len(c) or c[col] in ("", "—", "-"))]
            if not rows:
                print(f"  ✗ {label}: пусто")
                missing += 1
            elif no_source:
                print(f"  ~ {label}: строк {len(rows)}, без источника {len(no_source)}")
                missing += 1
            else:
                print(f"  ✓ {label}: строк {len(rows)}")
            continue
        statuses = []
        for i in range(start + 1, end):
            if kbcheck.ITEM.match(lines[i]) and "(ист:" in lines[i]:
                m = re.search(r";\s*([^;()]+)\)\s*$", lines[i])
                statuses.append(m.group(1).split()[0] if m else "")
        if not statuses:
            print(f"  ✗ {label}: пусто")
            missing += 1
        elif "подтверждено" not in statuses:
            print(f"  ~ {label}: фактов {len(statuses)}, подтверждённых нет")
            missing += 1
        else:
            print(f"  ✓ {label}: фактов {len(statuses)}, подтверждённых {statuses.count('подтверждено')}")
    blocks = blocking(root, ident)
    if blocks:
        print(f"Открытые блокирующие вопросы: {', '.join(blocks)}")
    print(f"Пробелов: {missing}. Это подсказка, а не ворота: решает владелец ({head.get('владелец', '—')}).")
    if not missing:
        print("Вопрос владельцу: тебе достаточно, чтобы писать спецификацию?")
    return 0


# ---------- «мне достаточно» ----------

def enough(root: Path, person: str, ident: str) -> int:
    loaded = load(root, ident)
    if loaded is None:
        return 1
    path, lines, head, body = loaded
    owner = head.get("владелец", "")
    if person != owner:
        print(f"ОШИБКА: «Достаточно» говорит владелец шага: {owner}")
        return 1
    if head.get("статус") not in ("Разбор", "Сверка с бизнесом"):
        print(f"ОШИБКА: «достаточно» говорят в «Разборе» или «Сверке с бизнесом». Сейчас: «{head.get('статус')}»")
        return 1
    today = kbbase.today().isoformat()
    kbquestions.set_field(lines, "достаточно для спеки", f"{person}, {today}")
    add_history(lines, body, f"- {today} достаточно для спеки — {person} (kb step enough)")
    save(path, lines)
    print(f"ЗАПИСАНО: {ident} — достаточно для спеки: {person}, {today}")
    return 0


# ---------- статусы ----------

def can_change(team: kbbase.Team, head: dict[str, str], person: str, old: str, new: str) -> str | None:
    """Кто может сделать этот переход. None — можно, иначе текст отказа."""
    architect = team.head.get("архитектор недели", "")
    owner = head.get("владелец", "")
    if new == "Разбор" and old == "Готов к взятию":
        return f"Шаг берут командой: python3 kb/tools/kb.py take {head.get('id', '')}"
    if old == "Черновик" and new == "Готов к взятию":
        return None if "аналитик" in team.roles.get(person, "") else "Готовит шаг к взятию аналитик"
    if new in ("Декомпозиты в работе", "Готово") or old in ("Приёмка", "Декомпозиты в работе"):
        return None if person in (architect, owner) else f"Этот переход делает архитектор недели: {architect}"
    return None if person == owner else f"Статус шага меняет владелец: {owner}"


def status(root: Path, team: kbbase.Team, person: str, ident: str, new: str, reason: str) -> int:
    loaded = load(root, ident)
    if loaded is None:
        return 1
    path, lines, head, body = loaded
    old = head.get("статус", "")
    if new not in STATUSES:
        print(f"ОШИБКА: неизвестный статус «{new}». Статусы: {', '.join(STATUSES)}")
        return 1
    if new == old:
        print(f"Статус уже «{new}»")
        return 0
    refusal = can_change(team, head, person, old, new)
    if refusal:
        print(f"ОШИБКА: {refusal}")
        return 1
    i_old, i_new = STATUSES.index(old), STATUSES.index(new)
    if i_new > i_old + 1:
        print(f"ОШИБКА: вперёд — только на один статус. Следующий: «{STATUSES[i_old + 1]}»")
        return 1
    if i_new < i_old and not reason:
        print('ОШИБКА: назад — только с причиной: --reason "…". Причину увидят архитектор недели и руководитель')
        return 1
    if new == "Нарезка" and head.get("достаточно для спеки", "—") in ("", "—"):
        print(f"ОШИБКА: в нарезку — после слов владельца «мне достаточно». Команда: python3 kb/tools/kb.py step enough {ident}")
        return 1
    if new == "Сверка с бизнесом":
        blocks = blocking(root, ident)
        if blocks and not reason:
            print(f"ОШИБКА: открыт блокирующий вопрос {', '.join(blocks)}. Закройте его, оформите допущение "
                  'или укажите причину: --reason "…"')
            return 1
    today = kbbase.today().isoformat()
    kbquestions.set_field(lines, "статус", new)
    entry = f"- {today} {old} → {new} — {person}" + (f", причина: {reason}" if reason else "")
    add_history(lines, body, entry)
    save(path, lines)
    print(f"СТАТУС: {ident} {old} → {new}" + (f" (причина: {reason})" if reason else ""))
    kbquestions.write_views(root)
    return 0


# ---------- взятие ----------

def take(root: Path, team: kbbase.Team, person: str, ident: str) -> int:
    loaded = load(root, ident)
    if loaded is None:
        return 1
    path, lines, head, body = loaded
    limit = int(team.head.get("лимит шагов на человека", "1") or 1)
    mine = []
    for other in sorted((root / "context" / "process").glob("PM-*.md")):
        h, _ = kbbase.front_matter(kbbase.read_lines(other))
        if h.get("id") != ident and h.get("статус") in IN_WORK and person in (h.get("владелец"), h.get("напарник")):
            mine.append(f"{h.get('id')} в «{h.get('статус')}»")
    if len(mine) >= limit:
        print(f"ОШИБКА: лимит — {limit} шаг на человека. У вас {', '.join(mine)}")
        return 1
    state, owner, partner = head.get("статус"), head.get("владелец", "—"), head.get("напарник", "—")
    today = kbbase.today().isoformat()
    if state == "Готов к взятию":
        kbquestions.set_field(lines, "владелец", person)
        kbquestions.set_field(lines, "статус", "Разбор")
        add_history(lines, body, f"- {today} Готов к взятию → Разбор — {person} (kb take)")
        print(f"ВЗЯТ: {ident} — владелец {person}, статус «Разбор»")
    elif state == "Разбор" and head.get("людей") == "2" and partner in ("", "—") and owner != person:
        kbquestions.set_field(lines, "напарник", person)
        add_history(lines, body, f"- {today} напарник — {person} (kb take)")
        print(f"ВЗЯТ: {ident} — напарник {person}")
    elif state == "Разбор" and owner == person:
        print(f"{ident} уже ваш")
        return 0
    else:
        print(f"ОШИБКА: взять можно шаг «Готов к взятию». {ident} — «{state}», владелец {owner}")
        return 1
    save(path, lines)
    return 0


# ---------- штурман ----------

NEXT = {
    "Разбор": None,   # считается по пробелам и «достаточно»
    "Сверка с бизнесом": ("аналитик проводит сверку шага с бизнесом; после неё — «мне достаточно» и нарезка",
                          "бизнес подтверждает «да, мы так работаем»", "medium"),
    "Нарезка": ("нарезать шаг на независимые декомпозиты от боли бизнеса, затем — «Приёмка»",
                "нарезку принимает архитектор недели", "dialog"),
    "Приёмка": ("архитектор недели принимает шаг и нарезку на встрече",
                "без приёмки декомпозиты не идут в спеку", "light"),
    "Декомпозиты в работе": ("спека по декомпозиту", "нет кода без принятой спеки", "heavy"),
    "Готово": ("шаг закрыт; можно взять следующий из другой части карты", "так растёт понимание всей цепочки", "light"),
}


def count_gaps(root: Path, lines: list[str], body: int) -> list[str]:
    secs = {name: (start, end) for name, start, end in kbbase.sections(lines, body)}
    result = []
    for label, section in DEPTH:
        if section not in secs:
            result.append(label)
            continue
        start, end = secs[section]
        if section in ("Сущности", "Объём"):
            if not kbbase.table_rows(lines, start, end):
                result.append(label)
        elif not any(kbcheck.ITEM.match(lines[i]) and "(ист:" in lines[i] for i in range(start + 1, end)):
            result.append(label)
    return result


def where(root: Path, team: kbbase.Team, person: str) -> int:
    """Ты здесь / Дальше / Почему — по каждому своему шагу. Уровень модели — из kb/ai.json."""
    roles = team.roles.get(person, "")
    mine, free = [], []
    for path in sorted((root / "context" / "process").glob("PM-*.md")):
        lines = kbbase.read_lines(path)
        head, body = kbbase.front_matter(lines)
        if person in (head.get("владелец"), head.get("напарник")) and head.get("статус") != "Готово":
            mine.append((head, lines, body))
        elif head.get("статус") == "Готов к взятию":
            free.append(head.get("id", path.stem))
    print(f"{person} ({roles or 'роль не указана'})")
    for head, lines, body in mine:
        ident, state = head.get("id"), head.get("статус", "")
        blocks = blocking(root, ident)
        waiting = f" · ждёт ответов: {len(blocks)} ({', '.join(blocks)})" if blocks else ""
        print(f"Ты здесь: {ident} {head.get('шаг', '')} — «{state}»{waiting}")
        if state == "Разбор":
            gaps_left = count_gaps(root, lines, body)
            enough_said = head.get("достаточно для спеки", "—") not in ("", "—")
            if gaps_left:
                print(f"Дальше: разобрать «{gaps_left[0]}» (пробелов по критерию глубины: {len(gaps_left)}) — /razbor {ident}")
                print("Почему: без входа, выхода и сущностей спеку не написать · модель: dialog")
            elif not enough_said:
                print("Дальше: решить, хватает ли понимания: «мне достаточно» — kb step enough, или продолжить разбор")
                print("Почему: пробелов по критерию нет, но готовность решает владелец · модель: light")
            else:
                print(f'Дальше: в сверку с бизнесом — kb step status {ident} "Сверка с бизнесом"')
                print("Почему: владелец сказал «мне достаточно»; бизнес подтверждает понимание · модель: light")
        else:
            nxt, why, model = NEXT.get(state) or ("—", "—", "light")
            print(f"Дальше: {nxt}")
            print(f"Почему: {why} · модель: {model}")
    if not mine and "разработчик" in roles or not mine and "тестировщик" in roles:
        print(f"Ты здесь: своих шагов в работе нет")
        print(f"Дальше: взять шаг — kb take PM-XX. Свободны: {', '.join(free) or 'нет'}")
        print("Почему: шаги не назначают — их берут сами · модель: light")
    if "аналитик" in roles:
        unsorted = [h.get("id") for _, _, h, _ in kbquestions.all_questions(root)
                    if h.get("статус") in ("открыт", "ответ найден") and kbquestions.is_new(h)]
        print(f"Аналитику: новых вопросов без сортировки — {len(unsorted)}"
              + (f" ({', '.join(unsorted)}) — kb q sort" if unsorted else ""))
    if team.head.get("архитектор недели") == person:
        waiting = []
        for path in sorted((root / "context" / "process").glob("PM-*.md")):
            h, _ = kbbase.front_matter(kbbase.read_lines(path))
            if h.get("статус") == "Приёмка":
                waiting.append(h.get("id"))
        print(f"Архитектору недели: ждут приёмки — {', '.join(waiting) or 'нет'}")
    return 0
