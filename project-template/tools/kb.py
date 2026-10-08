#!/usr/bin/env python3
"""Инструмент базы знаний: проверка, индексы, новые объекты журнала, нумерация, поиск ссылок.

Только стандартная библиотека Python 3.9+. Запуск из корня репозитория:

    python3 tools/kb.py check                 проверить шапки, ID, ссылки
    python3 tools/kb.py index                 пересобрать сгенерированные сводки и картинку карты
    python3 tools/kb.py new question <slug>   создать вопрос с временным ID Q-new-<slug>
    python3 tools/kb.py new assumption <slug> создать допущение A-new-<slug>
    python3 tools/kb.py new decision <slug>   создать решение D-new-<slug>
    python3 tools/kb.py new risk <slug>       создать риск R-new-<slug>
    python3 tools/kb.py new question <slug> поле=значение … Раздел=текст …
                                              создать и сразу заполнить: тип=… раздел=PM-04 заголовок="…" Контекст="…"
    python3 tools/kb.py ready                 руты, готовые к взятию, по приоритету
    python3 tools/kb.py take <PM-XX|SYS-XX>   взять рут или систему себе (проверяет готовность и лимит)
    python3 tools/kb.py release <PM-XX> "<почему>"
                                              вернуть рут в «Готов к взятию»
    python3 tools/kb.py ready-check <PM-XX>   готов ли шаг к взятию: что ещё должен заполнить аналитик
    python3 tools/kb.py whoami                кто я: роли, режим, мои руты, вопросы, допущения, риски
    python3 tools/kb.py set <ID|файл> поле=значение …
                                              поменять поле шапки с проверкой допустимых значений (статус, тип…)
    python3 tools/kb.py append <ID|файл> "<Раздел>" "<строка>"
                                              дописать строку в раздел (Подписчики, Уточнения, История, Ответ, разделы шага)
    python3 tools/kb.py fact <PM-XX|SYS-XX> "<Раздел>" "<текст>" "<источник>" <статус>
                                              дописать факт в правильном формате: «текст (источник, статус)»
    python3 tools/kb.py assign-ids            присвоить номера временным ID (только в основной ветке, в CI)
    python3 tools/kb.py refs <ID>             найти все места, где упоминается ID
    python3 tools/kb.py context <PM-XX|SYS-XX> всё нужное для работы с шагом одной выжимкой: кто работает, место на карте,
                                              файл шага, открытые вопросы, допущения, риски, решения, глоссарий, нарезка других рутов
    python3 tools/kb.py similar "<текст>" ["<другая формулировка>" …]
                                              похожие вопросы, допущения, решения, риски и строки фактов — для поиска ответа и дублей
    python3 tools/kb.py changes --since <хеш|дата> [--only <путь>] что изменилось в базе знаний с коммита: коммиты и добавленные/удалённые строки
    python3 tools/kb.py peek <файл> [строк]   паспорт большого файла: размер, колонки, частые значения, первые строки с номерами
    python3 tools/kb.py map                   нарисовать карту процессов: _map.svg (картинка) и _map.html (интерактивная схема)
                                              со статусами, владельцами, вопросами и декомпозитами каждого шага
    python3 tools/kb.py after-merge           assign-ids + index: то, что делает CI после слияния (для соло-режима без CI)
    python3 tools/kb.py report [--with-materials]
                                              архив kb-report-<дата>.zip: база знаний, FEEDBACK.md и история git — чтобы отдать на разбор

На Windows вместо python3 может быть python или py -3.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path
import zipfile

# Windows: консоль может быть не в UTF-8 — печатаем кириллицу без ошибок.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def write(path: Path, text: str) -> None:
    """Пишет UTF-8 с переводами строк LF на любой ОС."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

ROOT = Path(__file__).resolve().parent.parent
# KB_TODAY=ГГГГ-ММ-ДД подменяет сегодняшнюю дату (для тестов и симуляций).
TODAY = dt.date.fromisoformat(os.environ["KB_TODAY"]) if os.environ.get("KB_TODAY") else dt.date.today()

SLUG = r"[0-9a-zа-яё]+(?:-[0-9a-zа-яё]+)*"
ID_RE = re.compile(
    r"(?<![\w-])("
    r"GD-PM-\d{2}-\d{3}"
    r"|PM-\d{2}-\d{2}"
    r"|PM-\d{2}"
    r"|SYS-\d{2}"
    r"|[QADR]-\d{3}"
    r"|[QADR]-new-" + SLUG +
    r")(?![\w-])"
)
TEMP_ID_RE = re.compile(r"^([QADR])-new-(" + SLUG + r")$")

KINDS = {
    "step": {
        "glob": "context/process/**/PM-*.md",
        "id": r"^PM-\d{2}$",
        "required": ["id", "шаг", "владелец рута", "статус"],
        "statuses": ["Черновик", "Готов к взятию", "Разбор", "Сверка с бизнесом", "Нарезка", "Приёмка",
                     "Декомпозиты в работе", "Готово"],
    },
    "system": {
        "glob": "context/systems/SYS-*.md",
        "id": r"^SYS-\d{2}$",
        "required": ["id", "система"],
        "statuses": None,
    },
    "question": {
        "glob": "journal/questions/*.md",
        "id": r"^Q-(\d{3}|new-" + SLUG + r")$",
        "required": ["id", "статус", "тип", "раздел", "автор", "создан"],
        "statuses": ["открыт", "ждёт бизнес", "возможно отвечен", "отвечен", "стал допущением", "дубль"],
    },
    "assumption": {
        "glob": "journal/assumptions/*.md",
        "id": r"^A-(\d{3}|new-" + SLUG + r")$",
        "required": ["id", "статус", "из вопроса", "принял", "проверить до", "кто проверяет"],
        "statuses": ["открыто", "подтверждено", "опровергнуто", "просрочено"],
    },
    "decision": {
        "glob": "journal/decisions/*.md",
        "id": r"^D-(\d{3}|new-" + SLUG + r")$",
        "required": ["id", "статус", "тип", "принял"],
        "statuses": ["предложено", "принято", "заменено"],
    },
    "risk": {
        "glob": "journal/risks/*.md",
        "id": r"^R-(\d{3}|new-" + SLUG + r")$",
        "required": ["id", "статус", "раздел", "автор", "создан", "следит", "вероятность", "влияние"],
        "statuses": ["открыт", "снижен", "случился", "закрыт"],
    },
    "spec": {
        "glob": "specs/PM-*/PM-*/spec.md",
        "id": r"^PM-\d{2}-\d{2}$",
        "required": ["id", "рут", "статус", "владелец"],
        "statuses": ["Опции", "Спека", "Ревью спеки", "Сборка", "Приёмка", "Канарейка", "Готово"],
    },
}

TEMPLATES = {
    "question": ("journal/questions/_template.md", "journal/questions", "Q"),
    "assumption": ("journal/assumptions/_template.md", "journal/assumptions", "A"),
    "decision": ("journal/decisions/_template.md", "journal/decisions", "D"),
    "risk": ("journal/risks/_template.md", "journal/risks", "R"),
}

OPEN_Q = {"открыт", "ждёт бизнес", "возможно отвечен"}
GENERATED = {"context/process/_index.md", "journal/_index.md", "context/process/_map.svg", "context/process/_map.html"}
SKIP_DIRS = {".git", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__"}
# Документация и инструкции содержат примеры ID — их нумерация не трогает.
DOC_DIRS = {".claude", ".agents", ".opencode", ".gitlab", "tools", "docs"}


# ---------- чтение файлов ----------

def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    data = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^\s*([^:#][^:]*?)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2)
        value = re.sub(r"\s+#.*$", "", value).strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            value = [v.strip() for v in inner.split(",") if v.strip()] if inner else []
        data[key] = value
    return data


def duplicate_keys(text: str) -> list[str]:
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    seen, dups = set(), []
    for line in text[3:end].splitlines():
        m = re.match(r"^\s*([^:#][^:]*?)\s*:", line)
        if m:
            key = m.group(1).strip()
            if key in seen:
                dups.append(key)
            seen.add(key)
    return dups


def body_of(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4:] if end != -1 else text


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def load(kind: str) -> list[dict]:
    items = []
    for p in sorted(ROOT.glob(KINDS[kind]["glob"])):
        if p.name.startswith("_") or "_template" in p.parts:
            continue
        text = p.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        items.append({"path": p, "fm": fm, "text": text, "kind": kind})
    return items


def text_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                yield p, p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue


def meaningful_lines(text: str):
    """Строки с номерами, без комментариев: `# …` в шапке и <!-- … --> в тексте."""
    lines = text.splitlines()
    in_fm = bool(lines) and lines[0].strip() == "---"
    in_comment = False
    for n, line in enumerate(lines, 1):
        if in_fm:
            if n > 1 and line.strip() == "---":
                in_fm = False
                continue
            yield n, re.sub(r"\s+#.*$", "", line)
            continue
        out = ""
        rest = line
        while rest:
            if in_comment:
                end = rest.find("-->")
                if end == -1:
                    rest = ""
                else:
                    rest, in_comment = rest[end + 3:], False
            else:
                start = rest.find("<!--")
                if start == -1:
                    out, rest = out + rest, ""
                else:
                    out, rest, in_comment = out + rest[:start], rest[start + 4:], True
        yield n, out


def parse_date(value) -> dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


# ---------- check ----------

def cmd_check() -> int:
    errors, warnings = [], []
    defined: dict[str, str] = {}
    all_items = {k: load(k) for k in KINDS}

    for kind, items in all_items.items():
        spec = KINDS[kind]
        for it in items:
            p, fm = it["path"], it["fm"]
            where = rel(p)
            if not fm:
                errors.append(f"{where}: нет шапки (frontmatter)")
                continue
            for key in duplicate_keys(it["text"]):
                errors.append(f"{where}: поле «{key}» в шапке встречается дважды — "
                              f"скорее всего, две правки слились автоматически, оставьте одно значение")
            for key in spec["required"]:
                if key not in fm or fm[key] in ("", []):
                    errors.append(f"{where}: не заполнено поле «{key}»")
            ident = fm.get("id", "")
            if isinstance(ident, str) and ident:
                if not re.match(spec["id"], ident):
                    errors.append(f"{where}: ID «{ident}» не в формате {kind}")
                if kind != "spec" and not p.name.startswith(ident):
                    errors.append(f"{where}: имя файла должно начинаться с {ident}")
                if ident in defined:
                    errors.append(f"{where}: ID {ident} уже занят в {defined[ident]}")
                defined[ident] = where
            status = fm.get("статус")
            if spec["statuses"] and isinstance(status, str) and status:
                base = status.split()[0] if kind == "decision" else status
                if base not in spec["statuses"] and status not in spec["statuses"]:
                    errors.append(f"{where}: статус «{status}» не из списка: {', '.join(spec['statuses'])}")

    # ссылки на несуществующие объекты
    roots = {i for i in defined if re.match(r"^PM-\d{2}$", i)}
    check_globs = [KINDS[k]["glob"] for k in KINDS] + [
        "core/*.md", "context/glossary.md", "product.md", "context/process/**/_map.md"]
    seen_files = set()
    for g in check_globs:
        for p in ROOT.glob(g):
            if (p.name.startswith("_") and p.name != "_map.md") or "_template" in p.parts or p in seen_files:
                continue
            seen_files.add(p)
            text = p.read_text(encoding="utf-8")
            for n, line in meaningful_lines(text):
                for ref in ID_RE.findall(line):
                    if ref in defined:
                        continue
                    if re.match(r"^PM-\d{2}-\d{2}$", ref):
                        if ref[:5] not in roots:
                            warnings.append(f"{rel(p)}:{n}: {ref} — нет рута {ref[:5]}")
                    elif ref.startswith("GD-"):
                        if not list(ROOT.glob(f"datasets/**/{ref}*.md")):
                            warnings.append(f"{rel(p)}:{n}: случай датасета {ref} не найден")
                    else:
                        errors.append(f"{rel(p)}:{n}: ссылка на {ref}, а такого объекта нет")

    # похоже на персональные данные — предупреждение
    for base in ("materials", "datasets", "context", "journal", "product.md"):
        for p in ([ROOT / base] if (ROOT / base).is_file() else (ROOT / base).rglob("*")):
            if not p.is_file() or p.suffix.lower() not in (".md", ".csv", ".tsv", ".txt", ".json", ".py", ".sql", ".xml") \
                    or p.name.startswith("_template"):
                continue
            try:
                txt = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for kind_pd, rx in PD_PATTERNS:
                m = rx.search(txt)
                if m:
                    line = txt.count("\n", 0, m.start()) + 1
                    warnings.append(f"{rel(p)}:{line}: похоже на персональные данные ({kind_pd}) — обезличьте: "
                                    f"замените на вымышленные или маску, например 3140590*******")
                    break

    # формат фактов в шагах: «текст (источник, статус)»
    for it in all_items["step"]:
        for sec in FACT_SECTIONS:
            body = section_text(it["text"], sec)
            for line in body.splitlines():
                t = line.strip()
                if not re.match(r"^(-|\d+\.)\s+\S", t) or "<" in t:
                    continue
                if not FACT_STATUS_RE.search(t):
                    warnings.append(f"{rel(it['path'])} «{sec}»: факт без статуса в скобках — «{t[:70]}»")

    # карта процессов: шаги на схеме и файлы шагов совпадают
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import kb_map
        steps_ids = {it["fm"].get("id") for it in all_items["step"]}
        on_maps = set()
        for mp in (ROOT / "context/process").glob("**/_map.md"):
            nodes, edges = kb_map.parse_mermaid(mp.read_text(encoding="utf-8"))
            on_maps |= {n["step"] for n in nodes.values() if n.get("step")}
            for n in nodes.values():
                if n.get("step") and n["step"] not in steps_ids:
                    warnings.append(f"{rel(mp)}: на схеме {n['step']}, а файла шага нет")
        if on_maps:
            for sid in sorted(steps_ids - on_maps - {"PM-00"}):
                warnings.append(f"{sid}: файл шага есть, а на схеме в _map.md его нет")
    except ImportError:
        pass

    # просрочки — предупреждения
    for it in all_items["assumption"]:
        fm = it["fm"]
        due = parse_date(fm.get("проверить до"))
        if fm.get("статус") == "открыто" and due and due < TODAY:
            warnings.append(f"{rel(it['path'])}: допущение просрочено (проверить до {due})")

    # люди: владельцы и наблюдатели должны быть в team.md
    team = team_names()
    if team is None:
        warnings.append("team.md не найден — агенты не смогут проверять роли")
    else:
        checks = [("step", "владелец рута"), ("step", "напарник"), ("system", "владелец раздела в пуле"), ("risk", "следит")]
        for kind, field in checks:
            for it in all_items[kind]:
                who = it["fm"].get(field, "")
                if not isinstance(who, str) or not who or who.startswith("<") or who in ("не назначен", "—", "-"):
                    continue
                if who not in team:
                    warnings.append(f"{rel(it['path'])}: «{field}: {who}» — нет такого человека в team.md "
                                    f"(имя должно совпадать с git config user.name)")

    for it in all_items["step"]:
        fm = it["fm"]
        n = str(fm.get("людей", "")).strip()
        if n and not is_placeholder(n) and n not in ("1", "2"):
            errors.append(f"{rel(it['path'])}: «людей: {n}» — только 1 или 2. Больше двух на рут — делите шаг")
        mate = str(fm.get("напарник", "")).strip()
        if mate and mate not in UNASSIGNED + ("—", "-") and not is_placeholder(mate):
            if n != "2":
                errors.append(f"{rel(it['path'])}: есть напарник, а «людей: {n or '?'}» — напарник бывает только при «людей: 2»")
            if mate == fm.get("владелец рута"):
                errors.append(f"{rel(it['path'])}: напарник совпадает с владельцем")

    for w in warnings:
        print(f"ПРЕДУПРЕЖДЕНИЕ  {w}")
    for e in errors:
        print(f"ОШИБКА          {e}")
    print(f"Итог: ошибок {len(errors)}, предупреждений {len(warnings)}")
    return 1 if errors else 0


def first_table(text: str) -> list[list[str]]:
    """Строки данных первой таблицы в тексте (без шапки и разделителя)."""
    rows, started = [], False
    for line in text.splitlines():
        if line.startswith("|"):
            started = True
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif started:
            break
    return rows[2:]


PD_PATTERNS = [
    ("ПИНФЛ — 14 цифр", re.compile(r"(?<![\d*])\d{14}(?![\d*])")),
    ("ИИН/БИН — 12 цифр", re.compile(r"(?<![\d*-])\d{12}(?![\d*-])")),
    ("номер карты", re.compile(r"(?<!\d)(?:\d{4}[ -]){3}\d{4}(?!\d)")),
    ("телефон", re.compile(r"(?<!\d)\+?(?:7|998)[ -(]*\d{2,3}[ -)]*\d{3}[ -]?\d{2}[ -]?\d{2}(?!\d)")),
    ("паспорт", re.compile(r"(?i)паспорт[^\n]{0,15}\b[A-ZА-Я]{2}\s?\d{7}\b")),
]
FACT_SECTIONS = ["Как сейчас", "Объём и время", "Правила", "Исключения", "Боль"]
FACT_STATUS_RE = re.compile(r"[,(]\s*(подтверждено|оценка|допущение\s+A-[\w-]+|противоречие)\b")
FACT_STATUSES = ("подтверждено", "оценка", "противоречие")


def team_names() -> set[str] | None:
    p = ROOT / "team.md"
    if not p.exists():
        return None
    return {r[0] for r in first_table(body_of(p.read_text(encoding="utf-8"))) if r and r[0] and not r[0].startswith("<")}


# ---------- index ----------

def decomposits_of(step_text: str, root_id: str) -> list[str]:
    ids = []
    for line in body_of(step_text).splitlines():
        m = re.match(r"^\|\s*(" + re.escape(root_id) + r"-\d{2})\s*\|", line)
        if m:
            ids.append(m.group(1))
    return ids


def cmd_index() -> int:
    steps = load("step")
    questions = load("question")
    assumptions = load("assumption")
    decisions = load("decision")
    risks = load("risk")
    specs = {it["fm"].get("id"): it["fm"].get("статус", "") for it in load("spec")}
    q_by_id = {q["fm"].get("id"): q for q in questions}

    def q_root(q) -> str:
        return q["fm"].get("раздел", "") if isinstance(q["fm"].get("раздел"), str) else ""

    def a_root(a) -> str:
        q = q_by_id.get(a["fm"].get("из вопроса", ""))
        return q_root(q) if q else ""

    header = "<!-- Сгенерировано: python3 tools/kb.py index. Не править руками. -->\n\n"

    # карта рутов
    pic = "![Карта процессов](_map.svg)\n\nИнтерактивная схема — `context/process/_map.html` (открыть в браузере).\n" \
        if (ROOT / "context/process/_map.md").exists() else ""
    lines = [header + "# Руты: сводка\n", pic,
             "| Рут | Шаг | Владелец | Статус | Открытые вопросы | Открытые допущения | Декомпозиты (готово / всего) |",
             "|---|---|---|---|---|---|---|"]
    for s in sorted(steps, key=lambda x: x["fm"].get("id", "")):
        fm = s["fm"]
        rid = fm.get("id", "")
        oq = sum(1 for q in questions if q_root(q) == rid and q["fm"].get("статус") in OPEN_Q)
        oa = sum(1 for a in assumptions if a_root(a) == rid and a["fm"].get("статус") == "открыто")
        dec = decomposits_of(s["text"], rid)
        done = sum(1 for d in dec if specs.get(d) == "Готово")
        lines.append(f"| {rid} | {fm.get('шаг', '')} | {' + '.join(pair_of(fm)) or fm.get('владелец рута', '')} | {fm.get('статус', '')} "
                     f"| {oq} | {oa} | {done} / {len(dec)} |")
    write(ROOT / "context/process/_index.md", "\n".join(lines) + "\n")

    # журнал
    out = [header + "# Журнал: сводка\n", "## Открытые вопросы\n",
           "| Вопрос | Тип | Раздел | Статус | Автор | Создан | Блокирует |", "|---|---|---|---|---|---|---|"]
    for q in sorted(questions, key=lambda x: (x["fm"].get("создан", ""), x["fm"].get("id", ""))):
        fm = q["fm"]
        if fm.get("статус") not in OPEN_Q:
            continue
        title = first_heading(q["text"])
        blocks = fm.get("блокирует") or []
        out.append(f"| [{fm.get('id')}]({rel(q['path']).split('/', 1)[1]}) {title} | {fm.get('тип', '')} "
                   f"| {fm.get('раздел', '')} | {fm.get('статус', '')} | {fm.get('автор', '')} "
                   f"| {fm.get('создан', '')} | {', '.join(blocks) if isinstance(blocks, list) else blocks} |")
    out += ["", "## Открытые допущения\n",
            "| Допущение | Из вопроса | Проверить до | Кто проверяет | Просрочено |", "|---|---|---|---|---|"]
    for a in sorted(assumptions, key=lambda x: x["fm"].get("проверить до", "")):
        fm = a["fm"]
        if fm.get("статус") not in ("открыто", "просрочено"):
            continue
        due = parse_date(fm.get("проверить до"))
        late = "да" if (fm.get("статус") == "просрочено" or (due and due < TODAY)) else "нет"
        out.append(f"| [{fm.get('id')}]({rel(a['path']).split('/', 1)[1]}) {first_heading(a['text'])} "
                   f"| {fm.get('из вопроса', '')} | {fm.get('проверить до', '')} | {fm.get('кто проверяет', '')} | {late} |")
    out += ["", "## Открытые риски\n",
            "| Риск | Раздел | Вероятность | Влияние | Следит | Сигнал |", "|---|---|---|---|---|---|"]
    order = {"высокая": 0, "высокое": 0, "средняя": 1, "среднее": 1, "низкая": 2, "низкое": 2}
    for r in sorted(risks, key=lambda x: (order.get(x["fm"].get("влияние", ""), 3),
                                          order.get(x["fm"].get("вероятность", ""), 3), x["fm"].get("id", ""))):
        fm = r["fm"]
        if fm.get("статус") not in ("открыт", "случился"):
            continue
        mark = " — случился" if fm.get("статус") == "случился" else ""
        out.append(f"| [{fm.get('id')}]({rel(r['path']).split('/', 1)[1]}) {first_heading(r['text'])}{mark} "
                   f"| {fm.get('раздел', '')} | {fm.get('вероятность', '')} | {fm.get('влияние', '')} "
                   f"| {fm.get('следит', '')} | {fm.get('сигнал', '')} |")
    out += ["", "## Решения\n", "| Решение | Тип | Статус | Дата |", "|---|---|---|---|"]
    for d in sorted(decisions, key=lambda x: x["fm"].get("id", "")):
        fm = d["fm"]
        out.append(f"| [{fm.get('id')}]({rel(d['path']).split('/', 1)[1]}) {first_heading(d['text'])} "
                   f"| {fm.get('тип', '')} | {fm.get('статус', '')} | {fm.get('дата', '')} |")
    write(ROOT / "journal/_index.md", "\n".join(out) + "\n")
    print("Обновлено: context/process/_index.md, journal/_index.md")
    if list((ROOT / "context/process").glob("**/_map.md")):
        cmd_map(quiet=True)
        print("Обновлено: карта процессов _map.svg, _map.html")
    return 0


def first_heading(text: str) -> str:
    for line in body_of(text).splitlines():
        if line.startswith("# "):
            return re.sub(r"^#\s+[QADRPMSY0-9-]+(new-\S+)?\s*", "", line).strip()
    return ""


# ---------- new ----------

def git_user() -> str:
    try:
        return subprocess.check_output(["git", "config", "user.name"], cwd=ROOT, text=True, encoding="utf-8").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "<кто>"


def cmd_new(kind: str, slug: str) -> int:
    if kind not in TEMPLATES:
        print(f"Неизвестный вид: {kind}. Можно: {', '.join(TEMPLATES)}")
        return 2
    slug = slug.strip().lower()
    if not re.fullmatch(SLUG, slug):
        print("Короткое имя — это часть ID, а не имя человека: строчные буквы, цифры и дефисы, например «istochnik-dolga»")
        return 2
    tpl, folder, prefix = TEMPLATES[kind]
    temp_id = f"{prefix}-new-{slug}"
    target = ROOT / folder / f"{temp_id}.md"
    if target.exists():
        print(f"Уже есть: {rel(target)}")
        return 1
    text = (ROOT / tpl).read_text(encoding="utf-8")
    nnn = f"{prefix}-NNN"
    text = text.replace(f"id: {nnn}", f"id: {temp_id}", 1)
    text = text.replace(f"# {nnn}", f"# {temp_id}", 1)
    user, today = git_user(), TODAY.isoformat()
    text = re.sub(r"^(автор|предложил):.*$", lambda m: f"{m.group(1)}: {user}", text, count=1, flags=re.M)
    text = re.sub(r"^(создан|дата):.*$", lambda m: f"{m.group(1)}: {today}", text, count=1, flags=re.M)
    write(target, text)
    print(f"{rel(target)}  (временный ID {temp_id})")
    return 0


# ---------- assign-ids ----------

def cmd_assign_ids() -> int:
    renames: dict[str, tuple[Path, str, Path]] = {}
    for kind, (_, folder, prefix) in TEMPLATES.items():
        items = load(kind)
        used = [int(m.group(1)) for it in items
                if (m := re.match(rf"^{prefix}-(\d{{3}})$", str(it["fm"].get("id", ""))))]
        nxt = max(used, default=0) + 1
        temps = [it for it in items if TEMP_ID_RE.match(str(it["fm"].get("id", "")))]
        temps.sort(key=lambda it: (str(it["fm"].get("создан") or it["fm"].get("дата") or ""), it["path"].name))
        for it in temps:
            old = it["fm"]["id"]
            slug = TEMP_ID_RE.match(old).group(2)
            new = f"{prefix}-{nxt:03d}"
            nxt += 1
            renames[old] = (it["path"], new, it["path"].with_name(f"{new}-{slug}.md"))
    if not renames:
        print("Временных ID нет")
        return 0
    pattern = re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in sorted(renames, key=len, reverse=True)) + r")(?![\w-])")
    changed = 0
    for p, text in text_files():
        parts = p.relative_to(ROOT).parts
        if parts[0] in DOC_DIRS or p.name in ("README.md", "AGENTS.md") or "_template" in parts or p.name.startswith("_template"):
            continue
        new_text = pattern.sub(lambda m: renames[m.group(1)][1], text)
        if new_text != text:
            write(p, new_text)
            changed += 1
    for old, (path, new, target) in renames.items():
        path.rename(target)
        print(f"{old} → {new}  ({rel(target)})")
    print(f"Обновлено файлов со ссылками: {changed}")
    return 0


# ---------- refs ----------

def cmd_refs(ident: str) -> int:
    pattern = re.compile(r"(?<![\w-])" + re.escape(ident) + r"(?![\w-])")
    found = 0
    for p, text in text_files():
        if rel(p) in GENERATED or p.name in ("_map.svg", "_map.html"):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                print(f"{rel(p)}:{n}: {line.strip()}")
                found += 1
    print(f"Найдено упоминаний: {found}")
    return 0


# ---------- context ----------

def team_info() -> tuple[dict, dict[str, str]]:
    p = ROOT / "team.md"
    if not p.exists():
        return {}, {}
    text = p.read_text(encoding="utf-8")
    roles = {r[0]: (r[1] if len(r) > 1 else "") for r in first_table(body_of(text)) if r and r[0] and not r[0].startswith("<")}
    return parse_frontmatter(text), roles


def section_text(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


def cmd_context(ident: str) -> int:
    ident = ident.upper()
    kind = "step" if ident.startswith("PM-") else "system" if ident.startswith("SYS-") else None
    if not kind:
        print("Укажите рут PM-XX или систему SYS-XX")
        return 2
    items = {it["fm"].get("id"): it for it in load(kind)}
    if ident not in items:
        print(f"{ident} не найден")
        return 1
    it = items[ident]
    out = []
    fm_team, roles = team_info()
    user = git_user()
    out.append(f"== Кто работает: {user} — роли: {roles.get(user, 'НЕТ В team.md')}; режим: {fm_team.get('режим', '?')}; "
               f"архитектор недели: {fm_team.get('архитектор недели', '?')}")
    owner_field = "владелец рута" if kind == "step" else "владелец раздела в пуле"
    owner = it["fm"].get(owner_field, "")
    if kind == "step" and user != owner and user in pair_of(it["fm"]):
        verdict = "вы напарник: пишете в файл сами, в своих разделах; статус и нарезку ведёт владелец"
    else:
        verdict = "вы владелец, пишете в файл сами" if owner == user else "вы НЕ владелец: правки через MR владельцу или вопрос"
    out.append(f"== {owner_field}: {owner}" + (f", напарник: {it['fm'].get('напарник')}" if kind == "step" and len(pair_of(it["fm"])) > 1 else "") + f" → {verdict}")
    if kind == "step":
        n, why = size_hint(it)
        out.append(f"== Людей на рут: {it['fm'].get('людей', '?')}; подсказка по признакам: {n} ({why})")

    prod = ROOT / "product.md"
    if prod.exists():
        pt = prod.read_text(encoding="utf-8")
        ten = section_text(pt, "10 слов")
        metric = [l for l in pt.splitlines() if l.startswith(f"| {ident} |")]
        out.append("\n== Продукт: " + (ten.splitlines()[0] if ten else "—"))
        out += [f"Метрика шага: {l}" for l in metric]

    for mp in sorted((ROOT / "context/process").glob("**/_map.md")):
        mt = mp.read_text(encoding="utf-8")
        compact = ident.replace("-", "")
        rows = [l for l in mt.splitlines() if l.startswith(f"| {ident} |")]
        links = [l.strip() for l in mt.splitlines() if ("--" in l or ".->" in l) and re.search(rf"\b{compact}\b", l)]
        if rows or links or kind == "system":
            obj = section_text(mt, "Главный объект")
            out.append(f"\n== Карта ({rel(mp)})\nГлавный объект: " + " ".join(obj.splitlines()))
            out += rows
            out += [f"Связь: {l}" for l in links]

    if kind == "step":
        fill = []
        for sec in ["Материалы и контакты", "Вход и выход", "Участники", "Как сейчас", "Объём и время", "Правила",
                    "Исключения", "Боль", "Идентификаторы", "Данные"]:
            body = section_text(it["text"], sec)
            n = len([l for l in body.splitlines() if l.strip() and not is_placeholder(l)
                     and not l.startswith(("|---", "| Роль", "| Система", "Как главный", "С чего начать"))])
            fill.append(f"{sec}: {n if n else 'пусто'}")
        out.append("\n== Заполненность (строк по разделам)\n" + "; ".join(fill))
    out.append(f"\n== Файл {rel(it['path'])}\n" + it["text"].rstrip())

    if kind == "system":
        users = [s["fm"].get("id") for s in load("step") if ident in (s["fm"].get("системы") or [])]
        out.append(f"\n== Шаги, где участвует: {', '.join(users) or '—'}")

    questions = load("question")
    qmap = {q["fm"].get("id"): q for q in questions}
    jl = []
    for q in questions:
        fm = q["fm"]
        if fm.get("раздел") == ident and fm.get("статус") in OPEN_Q:
            jl.append(f"{fm.get('id')} [{fm.get('статус')}, {fm.get('тип')}] {first_heading(q['text'])}")
    for a in load("assumption"):
        fm = a["fm"]
        q = qmap.get(fm.get("из вопроса", ""))
        mentioned = re.search(rf"(?<![\w-]){re.escape(str(fm.get('id')))}(?![\w-])", it["text"])
        if fm.get("статус") in ("открыто", "просрочено") and ((q and q["fm"].get("раздел") == ident) or mentioned):
            jl.append(f"{fm.get('id')} [{fm.get('статус')}, проверить до {fm.get('проверить до')}] {first_heading(a['text'])}")
    for r in load("risk"):
        fm = r["fm"]
        if fm.get("раздел") == ident and fm.get("статус") in ("открыт", "случился"):
            jl.append(f"{fm.get('id')} [риск {fm.get('статус')}, влияние {fm.get('влияние')}] {first_heading(r['text'])}")
    for d in load("decision"):
        fm = d["fm"]
        if ident in (fm.get("источники") or []):
            jl.append(f"{fm.get('id')} [решение {fm.get('статус')}] {first_heading(d['text'])}")
    out.append("\n== Журнал по " + ident + "\n" + ("\n".join(jl) if jl else "открытых вопросов, допущений, рисков и решений нет"))

    gl = ROOT / "context/glossary.md"
    if gl.exists():
        rows = [l for l in gl.read_text(encoding="utf-8").splitlines() if l.startswith("|")][2:]
        rows = [r for r in rows if r.strip("| ").strip()]
        if len(rows) > 40:
            body = it["text"].lower()
            rows = [r for r in rows if r.split("|")[1].strip().lower() in body]
            out.append("\n== Глоссарий (термины, которые есть в файле шага)")
        else:
            out.append("\n== Глоссарий")
        out += rows or ["—"]

    if kind == "step":
        sl = []
        for s in load("step"):
            if s["fm"].get("id") == ident:
                continue
            for l in body_of(s["text"]).splitlines():
                m = re.match(r"^\|\s*(PM-\d{2}-\d{2})\s*\|([^|]*)\|", l)
                if m and m.group(2).strip():
                    sl.append(f"{m.group(1)} {m.group(2).strip()}")
        out.append("\n== Нарезка других рутов\n" + ("\n".join(sl) if sl else "—"))
    print("\n".join(out))
    return 0


# ---------- similar ----------

STOP = set("и в во на по с со к ко о об от до из за для не ни но а или ли же бы то это как что кто где когда какой какая какие "
           "ли при без над под про у все всё так уже ещё или есть нет быть был была были".split())


def stem(w: str) -> str:
    return w if len(w) <= 4 else w[:4] if len(w) <= 6 else w[:5]


def stems(text: str) -> set[str]:
    return {stem(w) for w in re.findall(r"[0-9a-zа-яё]+", text.lower()) if len(w) >= 3 and w not in STOP}


def cmd_similar(queries: list[str], top: int = 6) -> int:
    import math
    units = []   # (вид, метка, текст для поиска, строка вывода)
    for kind in ("question", "assumption", "decision", "risk"):
        for it in load(kind):
            fm = it["fm"]
            title = first_heading(it["text"])
            search = title + " " + section_text(it["text"], "Контекст") + " " + section_text(it["text"], "Что приняли") \
                + " " + section_text(it["text"], "Ответ")
            label = f"{fm.get('id')} [{fm.get('статус')}{', ' + str(fm.get('раздел')) if fm.get('раздел') else ''}] {title}"
            units.append(("journal", label, search))
    fact_globs = ["context/**/*.md", "core/*.md", "product.md", "materials/**/*.md", "datasets/**/*.md"]
    seen = set()
    for g in fact_globs:
        for p in ROOT.glob(g):
            if p in seen or rel(p) in GENERATED or p.name.startswith("_template") or p.name == "README.md":
                continue
            seen.add(p)
            for n, line in meaningful_lines(p.read_text(encoding="utf-8")):
                t = line.strip()
                if len(t) > 12 and not t.startswith(("#", "|---", "---")):
                    units.append(("fact", f"{rel(p)}:{n}: {t[:220]}", t))
    df: dict[str, int] = {}
    unit_stems = []
    for u in units:
        st = stems(u[2])
        unit_stems.append(st)
        for x in st:
            df[x] = df.get(x, 0) + 1
    N = max(len(units), 1)
    q_stems = set().union(*(stems(q) for q in queries))
    if not q_stems:
        print("Пустой запрос")
        return 2
    scored = []
    for u, st in zip(units, unit_stems):
        hit = q_stems & st
        if not hit:
            continue
        score = sum(math.log(1 + N / df[x]) for x in hit)
        scored.append((score, len(hit), u))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    for title, kind in (("Вопросы, допущения, решения, риски", "journal"), ("Строки фактов и материалов", "fact")):
        rows = [f"{s:5.1f}  {u[1][:170]}" for s, h, u in scored if u[0] == kind][:top]
        print(f"== {title}")
        print("\n".join(rows) if rows else "ничего похожего")
    print("Похожесть по словам, а не по смыслу: сравните кандидатов сами. Синонимы ищите отдельными формулировками.")
    return 0


# ---------- changes ----------

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def resolve_since(since: str) -> str:
    """Хеш коммита или дата («2026-10-12 09:00»): для даты — последний коммит до неё, иначе пустое дерево."""
    r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", since + "^{commit}"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    r = subprocess.run(["git", "rev-list", "-1", f"--before={since}", "HEAD"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    return r.stdout.strip() or EMPTY_TREE


def cmd_changes(since: str, only: str | None = None) -> int:
    since = resolve_since(since)
    try:
        rng = "HEAD" if since == EMPTY_TREE else f"{since}..HEAD"
        log = git_out("log", "--reverse", "--date=format:%Y-%m-%d %H:%M", "--format=%h %ad %an: %s", rng)
        added = git_out("diff", "--name-status", "--diff-filter=AR", since, "HEAD", "--", "journal")
        diff = git_out("diff", "--unified=0", "--no-color", since, "HEAD", "--",
                       "context", "core", "journal", "product.md", "datasets", "materials/answers",
                       ":(exclude)context/process/_index.md", ":(exclude)journal/_index.md", ":(exclude)journal/digest",
                       ":(glob,exclude)**/README.md", ":(glob,exclude)**/_template*", ":(glob,exclude)**/_template/**",
                       ":(exclude)context/process/_map.svg", ":(exclude)context/process/_map.html")
        if only:
            diff = git_out("diff", "--unified=0", "--no-color", since, "HEAD", "--", only)
    except subprocess.CalledProcessError:
        print(f"Не найден коммит {since}")
        return 1
    print("== Коммиты\n" + (log.strip() or "нет"))
    print("\n== Новые объекты журнала\n" + (added.strip() or "нет"))
    out, cur = [], None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            out.append(f"\n# {cur}")
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            # заглушки шаблонов «<…>» и комментарии — шум
            if line[1:].strip() and not re.search(r"<[^>]{2,}>|<!--", line):
                out.append(line[:300])
    if len(out) > 900:
        files = [l[2:] for l in out if l.startswith("\n# ")]
        out = out[:900] + [f"… обрезано, всего строк изменений: {len(out)}. Остальное — по частям: "
                           f"python3 tools/kb.py changes --since <хеш|дата> --only <папка или файл>. Файлы: {', '.join(files)}"]
    print("\n== Изменённые строки (+ добавлено, - удалено)" + ("\n".join(out) if out else "\nнет"))
    return 0


# ---------- peek ----------

def cmd_peek(path: str, rows: int = 10) -> int:
    import csv
    p = Path(path)
    if not p.is_absolute():
        p = (Path.cwd() / p)
    if not p.exists():
        print(f"Нет файла {path}")
        return 1
    size = p.stat().st_size
    print(f"== {path}: {size / 1024:.1f} КБ")
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            print("Для Excel нужен openpyxl (pip install openpyxl) или сохраните лист в CSV")
            return 1
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        for ws in wb.worksheets:
            data = [["" if v is None else str(v) for v in r] for r in ws.iter_rows(values_only=True)]
            print(f"\n-- лист «{ws.title}»")
            _peek_table(data, rows)
        return 0
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if p.suffix.lower() in (".csv", ".tsv"):
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
        except csv.Error:
            dialect = csv.excel
        data = list(csv.reader(lines, dialect))
        print(f"Разделитель: {dialect.delimiter!r}")
        _peek_table(data, rows)
        return 0
    print(f"Строк: {len(lines)}")
    marks = [(n, l.strip()) for n, l in enumerate(lines, 1)
             if re.match(r"^\s*(def |class |function |async def |public |private |##? )", l)]
    if marks:
        print("== Структура (номер строки: заголовок, функция, класс)")
        print("\n".join(f"{n}: {t[:160]}" for n, t in marks[:80]))
    print(f"== Первые {rows} строк")
    print("\n".join(f"{n}: {l[:200]}" for n, l in enumerate(lines[:rows], 1)))
    return 0


def _peek_table(data: list[list[str]], rows: int) -> None:
    if not data:
        print("пусто")
        return
    head, body = data[0], data[1:]
    print(f"Строк данных: {len(body)} (строка 1 — заголовок)")
    print("== Колонки: заполнено / разных значений / частые значения")
    for i, name in enumerate(head):
        col = [r[i] for r in body if i < len(r)]
        filled = [v for v in col if v.strip()]
        freq: dict[str, int] = {}
        for v in filled:
            freq[v] = freq.get(v, 0) + 1
        topv = sorted(freq.items(), key=lambda x: -x[1])[:3]
        tops = "; ".join(f"{v[:30]}×{c}" for v, c in topv)
        print(f"{i + 1}. {name}: {len(filled)} / {len(freq)} / {tops}")
    print(f"== Первые {rows} строк (номер строки файла)")
    for n, r in enumerate(data[1:rows + 1], 2):
        print(f"{n}: " + " | ".join(c[:40] for c in r))


# ---------- map ----------

def roots_data() -> dict:
    questions = load("question")
    q_by_id = {q["fm"].get("id"): q for q in questions}
    specs = {it["fm"].get("id"): it["fm"].get("статус", "") for it in load("spec")}
    out = {}
    for st in load("step"):
        fm = st["fm"]
        rid = fm.get("id", "")
        dec = decomposits_of(st["text"], rid)
        slicing = []
        for l in body_of(st["text"]).splitlines():
            m = re.match(r"^\|\s*(PM-\d{2}-\d{2})\s*\|([^|]*)\|", l)
            if m and m.group(2).strip():
                slicing.append([m.group(1), m.group(2).strip()])
        pain = [re.sub(r"^\s*[-\d.]+\s*", "", l).strip() for l in section_text(st["text"], "Боль").splitlines()
                if l.strip().startswith(("-", "1", "2", "3", "4", "5", "6", "7", "8", "9")) and "<" not in l]
        why = " ".join(l for l in section_text(st["text"], "Зачем этот шаг бизнесу").splitlines() if "<" not in l)
        out[rid] = {
            "шаг": fm.get("шаг", ""), "владелец": " + ".join(pair_of(fm)) or fm.get("владелец рута", ""), "статус": fm.get("статус", ""),
            "path": rel(st["path"]), "зачем": why, "боль": pain, "нарезка": slicing,
            "q": [[q["fm"].get("id"), first_heading(q["text"]), q["fm"].get("статус")] for q in questions
                  if q["fm"].get("раздел") == rid and q["fm"].get("статус") in OPEN_Q],
            "a": [], "r": [],
            "dec_total": len(dec), "dec_done": sum(1 for d in dec if specs.get(d) == "Готово"),
        }
    for a in load("assumption"):
        fm = a["fm"]
        q = q_by_id.get(fm.get("из вопроса", ""))
        rid = q["fm"].get("раздел") if q else None
        if rid in out and fm.get("статус") in ("открыто", "просрочено"):
            out[rid]["a"].append([fm.get("id"), first_heading(a["text"]), fm.get("проверить до", "")])
    for r in load("risk"):
        fm = r["fm"]
        if fm.get("раздел") in out and fm.get("статус") in ("открыт", "случился"):
            out[fm["раздел"]]["r"].append([fm.get("id"), first_heading(r["text"]), fm.get("влияние", "")])
    return out


def cmd_map(quiet: bool = False) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kb_map
    roots = roots_data()
    maps = sorted((ROOT / "context/process").glob("**/_map.md"))
    if not maps:
        print("Нет context/process/_map.md")
        return 1
    for mp in maps:
        text = mp.read_text(encoding="utf-8")
        nodes, edges = kb_map.parse_mermaid(text)
        if not any(n.get("step") for n in nodes.values()):
            if not quiet:
                print(f"{rel(mp)}: на схеме нет шагов вида PM-XX — рисовать нечего")
            continue
        geo, w, h, back = kb_map.layout(nodes, edges)
        title_m = re.search(r"^# (.+)$", text, re.M)
        title = title_m.group(1).strip() if title_m else "Карта процессов"
        svg = kb_map.svg_markup(nodes, edges, geo, w, h, back, roots, title=title)
        on_map = {n["step"] for n in nodes.values() if n.get("step")}
        unplaced = sorted(r for r in roots if r not in on_map and r != "PM-00")
        counts = {}
        for rid in on_map:
            if rid in roots:
                counts[roots[rid]["статус"]] = counts.get(roots[rid]["статус"], 0) + 1
        obj = " ".join(l for l in section_text(text, "Главный объект").splitlines() if "<" not in l)
        pm00 = roots.get("PM-00")
        meta = "Сгенерировано python3 tools/kb.py map из _map.md, файлов шагов и журнала. Не править руками."
        if pm00:
            meta += f" PM-00 «Каркас и общие части»: {pm00['статус']}, владелец {pm00['владелец']}."
        write(mp.with_suffix(".svg"), kb_map.standalone_svg(svg) + "\n")
        write(mp.with_suffix(".html"), kb_map.page(title, svg, roots, counts, unplaced, meta, obj))
        if not quiet:
            print(f"Нарисовано: {rel(mp.with_suffix('.svg'))}, {rel(mp.with_suffix('.html'))}"
                  + (f"; нет на схеме: {', '.join(unplaced)}" if unplaced else ""))
    return 0


# ---------- команды записи (для любых моделей: меньше ручной правки markdown) ----------

def resolve(ident: str) -> Path | None:
    p = ROOT / ident
    if p.is_file():
        return p
    if re.match(r"^(PM-\d{2}|SYS-\d{2})$", ident):
        kind = "step" if ident.startswith("PM") else "system"
        for it in load(kind):
            if it["fm"].get("id") == ident:
                return it["path"]
    hits = [x for x in ROOT.glob(f"journal/*/{ident}*.md") if not x.name.startswith("_")]
    exact = [x for x in hits if parse_frontmatter(x.read_text(encoding="utf-8")).get("id") == ident]
    return (exact or hits or [None])[0]


def kind_of(path: Path) -> str | None:
    r = rel(path)
    for k, spec in KINDS.items():
        if path in ROOT.glob(spec["glob"]):
            return k
    return None


def unescape(v: str) -> str:
    return v.replace("\\n", "\n")


def set_fields(text: str, kind: str | None, pairs: dict[str, str]) -> tuple[str, list[str]]:
    errs = []
    end = text.find("\n---", 3)
    head, rest = text[:end], text[end:]
    for k, v in pairs.items():
        spec = KINDS.get(kind or "", {})
        if k == "статус" and spec.get("statuses") and v.split()[0] not in spec["statuses"] and v not in spec["statuses"]:
            errs.append(f"статус «{v}» не из списка: {', '.join(spec['statuses'])}")
            continue
        if k == "тип" and kind == "question" and v not in QUESTION_TYPES:
            errs.append(f"тип «{v}» не из списка: {', '.join(QUESTION_TYPES)}")
            continue
        if k in ("вероятность",) and v not in ("низкая", "средняя", "высокая"):
            errs.append("вероятность: низкая / средняя / высокая")
            continue
        if k in ("влияние",) and v not in ("низкое", "среднее", "высокое"):
            errs.append("влияние: низкое / среднее / высокое")
            continue
        if k == "людей" and kind == "step" and v not in ("1", "2"):
            errs.append("людей: 1 или 2. Больше двух на рут не бывает — делите шаг")
            continue
        if re.search(rf"^{re.escape(k)}:.*$", head, flags=re.M):
            head = re.sub(rf"^{re.escape(k)}:.*$", lambda m: f"{k}: {v}", head, count=1, flags=re.M)
        elif kind == "step" and k in ("напарник", "людей", "приоритет"):
            head += f"\n{k}: {v}"
        else:
            errs.append(f"в шапке нет поля «{k}»")
    return head + rest, errs


def put_section(text: str, heading: str, value: str, append: bool) -> tuple[str, bool]:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    if not m:
        return text, False
    lines = [l for l in m.group(1).rstrip("\n").split("\n") if l.strip()]
    if heading in LABEL_SECTIONS:  # «Рождается: <…>» — подписи полей, их не трогаем
        keep = [l for l in lines if not re.match(r"^\s*(-|\d+\.)\s*<", l)]
    else:
        keep = [l for l in lines if not re.search(r"<[^>]{1,}>", l)]
    if not append:
        keep = [l for l in keep if not re.match(r"^\s*(-|\d+\.)\s", l)] if heading in ("Подписчики", "Уточнения", "История") else \
            [l for l in keep if l.startswith(("|", "Все места", "Заполняет", "Как главный", "Кто тоже", "Нюансы", "Одна строка"))]
    body = "\n".join(keep + [value.rstrip()]).strip("\n")
    return text[:m.start(1)] + body + "\n\n" + text[m.end(1):], True


LABEL_SECTIONS = {"Данные", "Вход и выход"}
QUESTION_TYPES = ("понимание", "бизнес-правило", "факт о системе", "решение", "скоуп")


def parse_pairs(args: list[str]) -> tuple[dict, dict, str | None, list[str]]:
    fields, sections, title, bad = {}, {}, None, []
    for a in args:
        if "=" not in a:
            bad.append(a)
            continue
        k, v = a.split("=", 1)
        k, v = k.strip(), unescape(v.strip())
        if k == "заголовок":
            title = v
        elif k[:1].isupper():
            sections[k] = v
        else:
            fields[k] = v
    return fields, sections, title, bad


def cmd_new_filled(kind: str, slug: str, extra: list[str]) -> int:
    slug = slug.strip().lower()
    if not extra:
        return cmd_new(kind, slug)
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_new(kind, slug)
    if rc != 0:
        print(buf.getvalue(), end="")
        return rc
    _, folder, prefix = TEMPLATES[kind]
    path = ROOT / folder / f"{prefix}-new-{slug}.md"
    fields, sections, title, bad = parse_pairs(extra)
    text = path.read_text(encoding="utf-8")
    if "блокирует" in fields and not fields["блокирует"].startswith("["):
        fields["блокирует"] = "[" + fields["блокирует"] + "]"
    if "источники" in fields and not fields["источники"].startswith("["):
        fields["источники"] = "[" + fields["источники"] + "]"
    text, errs = set_fields(text, kind, fields)
    if title:
        text = re.sub(rf"^(# {prefix}-new-{re.escape(slug)}).*$", lambda m: f"{m.group(1)} {title}", text, count=1, flags=re.M)
    for h, v in sections.items():
        text, ok = put_section(text, h, v, append=False)
        if not ok:
            errs.append(f"нет раздела «{h}»")
    if errs or bad:
        path.unlink()
        for e in errs + [f"не понял аргумент «{b}» — нужен вид поле=значение" for b in bad]:
            print(f"ОШИБКА  {e}")
        print(f"Файл {rel(path)} не создан — исправьте и повторите команду целиком.")
        return 1
    write(path, text)
    print(buf.getvalue(), end="")
    return 0


def cmd_set(ident: str, extra: list[str]) -> int:
    path = resolve(ident)
    if not path:
        print(f"Не найден {ident}")
        return 1
    fields, _, _, bad = parse_pairs(extra)
    if fields.get("статус") == "Готов к взятию" and kind_of(path) == "step":
        miss = ready_missing(path.read_text(encoding="utf-8"))
        if miss:
            print("ОШИБКА  шаг не готов к взятию:\n" + "\n".join(f"  - {m}" for m in miss))
            return 1
    text, errs = set_fields(path.read_text(encoding="utf-8"), kind_of(path), fields)
    if errs or bad:
        for e in errs + [f"не понял «{b}»" for b in bad]:
            print(f"ОШИБКА  {e}")
        return 1
    write(path, text)
    print(f"{rel(path)}: " + ", ".join(f"{k} = {v}" for k, v in fields.items()))
    return 0


def owner_note(path: Path) -> str:
    fm = parse_frontmatter(path.read_text(encoding="utf-8"))
    owner = fm.get("владелец рута") or fm.get("владелец раздела в пуле")
    if owner and owner != git_user() and git_user() not in pair_of(fm):
        return f" ВНИМАНИЕ: владелец — {owner}, не вы. Такая правка — только в ветке для MR владельцу."
    return ""


def cmd_append(ident: str, heading: str, line: str) -> int:
    path = resolve(ident)
    if not path:
        print(f"Не найден {ident}")
        return 1
    line = unescape(line)
    if not re.match(r"^\s*(-|\d+\.|\|)", line) and heading in ("Подписчики", "Уточнения", "История"):
        line = "- " + line
    text, ok = put_section(path.read_text(encoding="utf-8"), heading, line, append=True)
    if not ok:
        print(f"ОШИБКА  в {rel(path)} нет раздела «{heading}»")
        return 1
    write(path, text)
    print(f"{rel(path)} «{heading}»: дописано.{owner_note(path)}")
    return 0


def cmd_fact(ident: str, heading: str, fact: str, source: str, status: str) -> int:
    path = resolve(ident)
    if not path or not re.match(r"^(PM|SYS)-", ident):
        print("Факт пишется в шаг PM-XX или систему SYS-XX")
        return 1
    if status not in FACT_STATUSES and not re.match(r"^допущение\s+A-[\w-]+$", status):
        print("ОШИБКА  статус: подтверждено / оценка / противоречие / допущение A-…")
        return 1
    if not source.strip():
        print("ОШИБКА  нужен источник: путь к материалу и место, или ID ответа")
        return 1
    text = path.read_text(encoding="utf-8")
    body = section_text(text, heading)
    fact = unescape(fact).strip().rstrip(".")
    if heading == "Как сейчас":
        nums = [int(m.group(1)) for m in re.finditer(r"^(\d+)\.\s(.*)$", body, flags=re.M) if "<" not in m.group(2)]
        line = f"{(max(nums) + 1) if nums else 1}. {fact} ({source}, {status})"
    else:
        line = f"- {fact} ({source}, {status})"
    text, ok = put_section(text, heading, line, append=True)
    if not ok:
        print(f"ОШИБКА  в {rel(path)} нет раздела «{heading}»")
        return 1
    write(path, text)
    print(f"{rel(path)} «{heading}»: {line}{owner_note(path)}")
    return 0


def cmd_whoami() -> int:
    user = git_user()
    fm_team, roles = team_info()
    print(f"Имя: {user}; роли: {roles.get(user, 'НЕТ В team.md — попросите аналитика добавить')}")
    print(f"Режим: {fm_team.get('режим', '?')}; архитектор недели: {fm_team.get('архитектор недели', '?')}; "
          f"рабочие часы: {fm_team.get('рабочие часы', '?')}")
    mine = [f"{it['fm'].get('id')} ({it['fm'].get('статус')}{', напарник' if it['fm'].get('владелец рута') != user else ''})"
            for it in load("step") if user in pair_of(it["fm"])]
    my_roots = {it["fm"].get("id") for it in load("step") if user in pair_of(it["fm"])}
    sysm = [it["fm"].get("id") for it in load("system") if it["fm"].get("владелец раздела в пуле") == user]
    print("Руты: " + (", ".join(mine) or "—") + "; системы: " + (", ".join(sysm) or "—"))
    wip = wip_of(user)
    ready_n = len([it for it in load("step") if it["fm"].get("статус") == "Готов к взятию"])
    print(f"В работе: {len(wip)} из лимита {wip_limit()}; готовых к взятию рутов: {ready_n}"
          + (" — посмотреть: python3 tools/kb.py ready" if ready_n and len(wip) < wip_limit() else ""))
    qs = []
    for q in load("question"):
        fm = q["fm"]
        if fm.get("статус") not in OPEN_Q:
            continue
        subs = section_text(q["text"], "Подписчики")
        if fm.get("автор") == user or re.search(rf"^-\s*{re.escape(user)}\b", subs, flags=re.M):
            qs.append(f"{fm.get('id')} [{fm.get('статус')}] {first_heading(q['text'])}")
    print("Мои открытые вопросы: " + ("\n  " + "\n  ".join(qs) if qs else "—"))
    moved = [f"{q['fm'].get('id')} {first_heading(q['text'])}" for q in load("question")
             if q["fm"].get("раздел") in my_roots and q["fm"].get("статус") == "отвечен"
             and AWAIT_OWNER in section_text(q["text"], "Ответ")]
    if moved:
        print("Ответы ждут переноса в ваш шаг (/kb-answer): \n  " + "\n  ".join(moved))
    al = [f"{a['fm'].get('id')} до {a['fm'].get('проверить до')} [{a['fm'].get('статус')}]" for a in load("assumption")
          if a["fm"].get("статус") in ("открыто", "просрочено") and (user in str(a["fm"].get("принял", "")) or user in str(a["fm"].get("кто проверяет", "")))]
    print("Мои допущения: " + (", ".join(al) or "—"))
    rl = [f"{r['fm'].get('id')} [{r['fm'].get('статус')}]" for r in load("risk") if r["fm"].get("следит") == user and r["fm"].get("статус") in ("открыт", "случился")]
    print("Риски, за которыми слежу: " + (", ".join(rl) or "—"))
    return 0


# ---------- готовность, взятие рутов ----------

WIP_STATUSES = ("Разбор", "Сверка с бизнесом", "Нарезка")
UNASSIGNED = ("", "не назначен")


def is_placeholder(text: str) -> bool:
    return not text.strip() or bool(re.search(r"<[^>]+>", text))


AWAIT_OWNER = "Записан в: ждёт владельца"


def pair_of(fm: dict) -> list[str]:
    """Кто ведёт рут: владелец и напарник, если он есть."""
    out = []
    for f in ("владелец рута", "напарник"):
        v = str(fm.get(f, "")).strip()
        if v and v not in UNASSIGNED + ("—", "-") and not is_placeholder(v):
            out.append(v)
    return out


def people_on(fm: dict) -> int:
    v = str(fm.get("людей", "1")).strip()
    return 2 if v == "2" else 1


def size_hint(it) -> tuple[int, str]:
    """Сколько людей нужно на рут — по признакам сложности шага."""
    fm, text = it["fm"], it["text"]
    systems = fm.get("системы") or []
    systems = [s for s in systems if re.match(r"SYS-\d{2}", str(s))] if isinstance(systems, list) else re.findall(r"SYS-\d{2}", str(systems))
    mat = section_text(text, "Материалы и контакты")
    heavy = [l for l in mat.splitlines() if re.search(r"materials/(scripts|files)/", l)]
    roles = [r for r in first_table(section_text(text, "Участники")) if any(c.strip() for c in r)]
    compact = str(fm.get("id", "")).replace("-", "")
    links = 0
    for mp in (ROOT / "context/process").glob("**/_map.md"):
        links += sum(1 for l in mp.read_text(encoding="utf-8").splitlines()
                     if ("--" in l or ".->" in l) and re.search(rf"\b{compact}\b", l))
    signs = []
    if len(systems) >= 2:
        signs.append(f"систем {len(systems)}")
    if len(heavy) >= 2:
        signs.append(f"скриптов и выгрузок {len(heavy)}")
    if len(roles) >= 3:
        signs.append(f"ролей бизнеса {len(roles)}")
    if links >= 3:
        signs.append(f"связей на карте {links}")
    if len(signs) >= 4:
        return 2, "; ".join(signs) + " — это много даже для двоих: подумайте, не разделить ли шаг"
    if len(signs) >= 2:
        return 2, "; ".join(signs)
    return 1, ("; ".join(signs) or "один участок, мало систем") + " — хватит одного"


def ready_missing(step_text: str) -> list[str]:
    miss = []
    why = [l for l in section_text(step_text, "Зачем этот шаг бизнесу").splitlines() if l.strip() and not is_placeholder(l)]
    if not why:
        miss.append("«Зачем этот шаг бизнесу» — одна-две фразы с источником")
    io = section_text(step_text, "Вход и выход")
    for label in ("Вход:", "Выход:"):
        line = next((l for l in io.splitlines() if l.strip().startswith(label)), "")
        if not line or is_placeholder(line) or len(line.strip()) < len(label) + 5:
            miss.append(f"«Вход и выход» — строка «{label}»")
    rows = [r for r in first_table(section_text(step_text, "Участники")) if any(c.strip() for c in r)]
    if not rows:
        miss.append("«Участники» — хотя бы одна роль бизнеса и что она делает")
    mat = section_text(step_text, "Материалы и контакты")
    items = [l for l in mat.splitlines() if l.strip().startswith("- ") and not is_placeholder(l)]
    if not items:
        miss.append("«Материалы и контакты» — хотя бы один материал: «- materials/… — что в нём»")
    for l in items:
        m = re.search(r"(materials/\S+?)(?=[\s,;—)]|$)", l)
        if m and not (ROOT / m.group(1)).exists():
            miss.append(f"«Материалы и контакты» — нет файла {m.group(1)}")
    contact = next((l for l in mat.splitlines() if l.startswith("Кто отвечает в бизнесе")), "")
    if not contact or is_placeholder(contact):
        miss.append("«Материалы и контакты» — «Кто отвечает в бизнесе: <роль> — через <кого>»")
    pr = parse_frontmatter(step_text).get("приоритет", "")
    if not str(pr).strip().isdigit():
        miss.append("шапка — «приоритет: <число>»")
    if str(parse_frontmatter(step_text).get("людей", "")).strip() not in ("1", "2"):
        miss.append("шапка — «людей: 1» или «людей: 2» (подсказка — в выводе ready-check)")
    return miss


def step_by_id(ident: str):
    return next((it for it in load("step") if it["fm"].get("id") == ident), None)


def cmd_ready_check(ident: str) -> int:
    it = step_by_id(ident)
    if not it:
        print(f"Нет шага {ident}")
        return 1
    miss = ready_missing(it["text"])
    n, why = size_hint(it)
    print(f"Людей на рут — подсказка: {n} ({why}). В шапке сейчас: {it['fm'].get('людей', '—')}. Решает аналитик, максимум 2.")
    if miss:
        print(f"{ident} не готов к взятию. Аналитику дописать:")
        print("\n".join(f"  - {m}" for m in miss))
        return 1
    print(f"{ident} готов к взятию. Поставить статус: python3 tools/kb.py set {ident} статус=\"Готов к взятию\"")
    return 0


def wip_of(user: str) -> list[str]:
    return [it["fm"].get("id") for it in load("step") if user in pair_of(it["fm"])
            and it["fm"].get("статус") in WIP_STATUSES and it["fm"].get("id") != "PM-00"]


def wip_limit() -> int:
    fm_team, _ = team_info()
    v = str(fm_team.get("лимит рутов на человека", "1")).strip()
    return int(v) if v.isdigit() else 1


def cmd_ready() -> int:
    items = [it for it in load("step") if it["fm"].get("статус") == "Готов к взятию"]
    def prio(it):
        v = str(it["fm"].get("приоритет", "")).strip()
        return (int(v) if v.isdigit() else 999, it["fm"].get("id", ""))
    user = git_user()
    mine = wip_of(user)
    print(f"У вас в работе: {', '.join(mine) or 'ничего'} (лимит {wip_limit()})")
    if not items:
        print("Готовых к взятию рутов нет.")
    else:
        print("Готовы к взятию — берите сверху, если нет причины взять другой:")
    for it in sorted(items, key=prio):
        fm = it["fm"]
        why = " ".join(l for l in section_text(it["text"], "Зачем этот шаг бизнесу").splitlines() if l.strip())
        why = re.sub(r"\s*\([^()]*\)\s*$", "", why)[:110]
        mats = len([l for l in section_text(it["text"], "Материалы и контакты").splitlines() if l.startswith("- ")])
        two = " · вдвоём" if people_on(fm) == 2 else ""
        print(f"  {fm.get('приоритет', '?'):>2}. {fm.get('id')} {fm.get('шаг')} — {why} · материалов: {mats}{two}")
    seek = [it for it in load("step") if people_on(it["fm"]) == 2 and len(pair_of(it["fm"])) == 1
            and it["fm"].get("статус") in WIP_STATUSES]
    if seek:
        print("Ищут напарника (рут на двоих, второй ещё не взял):")
        for it in sorted(seek, key=prio):
            fm = it["fm"]
            print(f"  {fm.get('приоритет', '?'):>2}. {fm.get('id')} {fm.get('шаг')} — владелец {fm.get('владелец рута')}")
    return 0


def cmd_take(ident: str, force: bool) -> int:
    user = git_user()
    _, roles = team_info()
    if user not in roles:
        print(f"ОШИБКА  {user} нет в team.md — попросите аналитика добавить")
        return 1
    if ident.startswith("SYS-"):
        path = resolve(ident)
        if not path:
            print(f"Нет системы {ident}")
            return 1
        text = path.read_text(encoding="utf-8")
        owner = parse_frontmatter(text).get("владелец раздела в пуле", "")
        if owner not in UNASSIGNED and not is_placeholder(owner) and owner != user:
            print(f"ОШИБКА  {ident} уже у {owner}")
            return 1
        text, _ = set_fields(text, "system", {"владелец раздела в пуле": user})
        write(path, text)
        print(f"{ident}: владелец раздела — {user}. Коммит: «{ident}: взял(а) раздел» и сразу push.")
        return 0
    it = step_by_id(ident)
    if not it:
        print(f"Нет шага {ident}")
        return 1
    fm = it["fm"]
    owner = fm.get("владелец рута", "")
    pair = pair_of(fm)
    if user in pair:
        print(f"{ident} уже ваш")
        return 0
    mine = wip_of(user)
    over = ident != "PM-00" and len(mine) >= wip_limit() and not force
    limit_msg = (f"ОШИБКА  у вас уже в работе {', '.join(mine)} — лимит {wip_limit()}. Сначала доведите до приёмки "
                 f"или верните: python3 tools/kb.py release <ID> \"почему\"")
    push_msg = ("Сразу коммит прямо в основную ветку и push, чтобы другие видели: "
                f"git commit -am \"{ident}: взял(а) рут\" && git push. "
                "Если push отклонён и после git pull --rebase конфликт в шапке — рут уже взяли: "
                "git rebase --abort и возьмите следующий.")
    if pair:
        if people_on(fm) == 2 and len(pair) == 1 and fm.get("статус") in WIP_STATUSES:
            if over:
                print(limit_msg)
                return 1
            text, _ = set_fields(it["text"], "step", {"напарник": user})
            write(it["path"], text)
            print(f"{ident}: вы напарник, владелец — {owner}. Договоритесь, кто какие разделы ведёт; "
                  "статус и нарезку ведёт владелец.")
            print(push_msg.replace("взял(а) рут", "напарник"))
            return 0
        if people_on(fm) == 2:
            print(f"ОШИБКА  {ident} уже ведут двое: {' и '.join(pair)}. Больше двух на рут не берут — "
                  "третий начинает делать за других. Возьмите другой рут или помогите вопросами и MR.")
        else:
            print(f"ОШИБКА  {ident} уже взял(а) {owner} (рут на одного)")
        return 1
    if fm.get("статус") != "Готов к взятию" and not force and ident != "PM-00":
        print(f"ОШИБКА  {ident} в статусе «{fm.get('статус')}», а брать можно только «Готов к взятию». "
              f"Аналитик готовит шаг: python3 tools/kb.py ready-check {ident}")
        return 1
    if over:
        print(limit_msg)
        return 1
    text, _ = set_fields(it["text"], "step", {"владелец рута": user, "статус": "Разбор"})
    write(it["path"], text)
    print(f"{ident}: владелец — {user}, статус «Разбор»." +
          (" Рут на двоих: второй возьмёт его той же командой take." if people_on(fm) == 2 else ""))
    print(push_msg)
    return 0


def cmd_release(ident: str, reason: str) -> int:
    it = step_by_id(ident)
    if not it:
        print(f"Нет шага {ident}")
        return 1
    fm, user = it["fm"], git_user()
    mate = fm.get("напарник", "")
    if user == mate:
        text, _ = set_fields(it["text"], "step", {"напарник": "—"})
        write(it["path"], text)
        print(f"{ident}: вы больше не напарник, рут остаётся у {fm.get('владелец рута')}. "
              f"Коммит: «{ident}: вышел(ла) из пары — {reason}»")
        return 0
    if fm.get("владелец рута") != user:
        print(f"ОШИБКА  вернуть может только владелец или напарник: {' и '.join(pair_of(fm)) or 'не назначен'}")
        return 1
    if mate in pair_of(fm):
        text, _ = set_fields(it["text"], "step", {"владелец рута": mate, "напарник": "—"})
        write(it["path"], text)
        print(f"{ident}: владелец теперь {mate}, место напарника свободно. Коммит: «{ident}: передал(а) рут {mate} — {reason}»")
        return 0
    text, _ = set_fields(it["text"], "step", {"владелец рута": "не назначен", "статус": "Готов к взятию"})
    write(it["path"], text)
    print(f"{ident} снова «Готов к взятию». Факты остаются. Коммит: «{ident}: вернул(а) рут — {reason}»")
    return 0


# ---------- after-merge, report ----------

def cmd_after_merge() -> int:
    rc = cmd_assign_ids()
    cmd_index()
    return rc


def git_out(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8")


def cmd_report(with_materials: bool) -> int:
    try:
        files = [f for f in git_out("ls-files", "-z").split("\0") if f]
        log = git_out("log", "--stat", "--date=format:%Y-%m-%d %H:%M", "--format=%n== %h %ad %an: %s")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Нужен git-репозиторий")
        return 1
    if (ROOT / "FEEDBACK.md").exists() and "FEEDBACK.md" not in files:
        files.append("FEEDBACK.md")
    name = f"kb-report-{TODAY.isoformat()}.zip"
    skipped = 0
    with zipfile.ZipFile(ROOT / name, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if not with_materials and f.startswith("materials/"):
                skipped += 1
                continue
            if (ROOT / f).is_file():
                z.write(ROOT / f, f)
        z.writestr("git-log.txt", log)
        z.writestr("kb-check.txt", subprocess.run([sys.executable, __file__, "check"], cwd=ROOT,
                                                  capture_output=True, text=True, encoding="utf-8").stdout)
    print(f"Готово: {name}")
    if skipped:
        print(f"Материалы бизнеса не включены, файлов: {skipped}. Включить: report --with-materials")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd == "check":
        return cmd_check()
    if cmd == "index":
        return cmd_index()
    if cmd == "new" and len(args) >= 2:
        return cmd_new_filled(args[0], args[1], args[2:])
    if cmd == "ready":
        return cmd_ready()
    if cmd == "ready-check" and len(args) == 1:
        return cmd_ready_check(args[0])
    if cmd == "take" and args:
        return cmd_take(args[0], "--force" in args)
    if cmd == "release" and len(args) == 2:
        return cmd_release(args[0], args[1])
    if cmd == "whoami":
        return cmd_whoami()
    if cmd == "set" and len(args) >= 2:
        return cmd_set(args[0], args[1:])
    if cmd == "append" and len(args) == 3:
        return cmd_append(*args)
    if cmd == "fact" and len(args) == 5:
        return cmd_fact(*args)
    if cmd == "assign-ids":
        return cmd_assign_ids()
    if cmd == "refs" and len(args) == 1:
        return cmd_refs(args[0])
    if cmd == "context" and len(args) == 1:
        return cmd_context(args[0])
    if cmd == "similar" and args:
        return cmd_similar(args)
    if cmd == "changes" and len(args) in (2, 4) and args[0] == "--since":
        return cmd_changes(args[1], args[3] if len(args) == 4 and args[2] == "--only" else None)
    if cmd == "peek" and args:
        return cmd_peek(args[0], int(args[1]) if len(args) > 1 and args[1].isdigit() else 10)
    if cmd == "map":
        return cmd_map()
    if cmd == "after-merge":
        return cmd_after_merge()
    if cmd == "report":
        return cmd_report("--with-materials" in args)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
