"""Факт: `- <текст> (ист: <источник>; <статус>)`. Разбор, поправка ввода слабых моделей, проверка.

Формат и правила — kb/rules/formats.md, раздел 1. Ответы на ошибки — kb/tools/tests/oshibki.md.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import kbbase

STATUSES = ("подтверждено", "оценка", "допущение", "противоречие")
STATUS_HELP = "Статусы: подтверждено, оценка, допущение A-NNN, противоречие"
FORMAT_HELP = "Нужно: `(ист: <материал:строка или «со слов кто, дата»>; <статус>)`"
SOURCE_FORMS = ("Формы источника: `materials/<файл>:<строка>`, `materials/<файл>#<лист>!<ячейка>`, "
                "`со слов <кто>, <ГГГГ-ММ-ДД>`, `Q-NNN`, `D-NNN`")

ENGLISH_STATUS = {"confirmed": "подтверждено", "estimate": "оценка", "assumption": "допущение",
                  "contradiction": "противоречие"}
# Латинские буквы, похожие на кириллические
HOMOGLYPHS = str.maketrans("aeopcxykAEOPCXYKMHTB", "аеорсхукАЕОРСХУКМНТВ")
NOT_SOURCES = re.compile(r"(?<![\w-])(ии|ai|агент\w*|модел\w*|нейросет\w*|glm|qwen|gpt|chatgpt|claude|"
                         r"rag|по опыту|очевидно)(?![\w-])", re.IGNORECASE)
CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
THINK = re.compile(r"</?think>", re.IGNORECASE)
SOURCE_START = re.compile(r"\((?:ист|источник|исх|source)\b\.?\s*[:.]?\s*", re.IGNORECASE)
PAGE_WORDS = re.compile(r"^(materials/[^\s,:#]+)[,\s]+(?:стр\.?|строка|строки|line)\s*(\d+)(?:\s*[-–]\s*(\d+))?$",
                        re.IGNORECASE)
MATERIAL = re.compile(r"^(materials/[^\s:#,]+)(?::(\d+)(?:\s*[-–]\s*(\d+))?|#(\S+))?(?:,\s*(.*))?$")
OBJECT = re.compile(r"^([QDA]-[\w-]+)(?:,\s*(.*))?$")


@dataclass
class Fact:
    text: str
    source: str
    status: str
    fixes: list[str] = field(default_factory=list)

    def line(self) -> str:
        return f"- {self.text} (ист: {self.source}; {self.status})"


@dataclass
class Problem:
    level: str      # ОШИБКА или ПРЕДУПРЕЖДЕНИЕ
    message: str


def fix_status(raw: str, fixes: list[str]) -> str:
    """Статус как есть или поправленный. Неизвестный статус возвращается без изменений."""
    status = " ".join(raw.split())
    word, _, rest = status.partition(" ")
    if word.lower() in ENGLISH_STATUS:
        new = ENGLISH_STATUS[word.lower()]
        fixes.append(f"ПОПРАВЛЕНО: `{word}` → `{new}`")
        word = new
    elif word not in STATUSES and word.translate(HOMOGLYPHS) in STATUSES:
        new = word.translate(HOMOGLYPHS)
        fixes.append(f"ПОПРАВЛЕНО: латинская буква в слове «{new}» заменена кириллической")
        word = new
    return f"{word} {rest}".strip()


def fix_source(raw: str, fixes: list[str]) -> str:
    source = " ".join(raw.split())
    m = PAGE_WORDS.match(source)
    if m:
        new = f"{m.group(1)}:{m.group(2)}" + (f"-{m.group(3)}" if m.group(3) else "")
        fixes.append(f"ПОПРАВЛЕНО: `{source}` → `{new}`")
        source = new
    return source


def parse(line: str) -> tuple[Fact | None, list[str]]:
    """Строка факта → Fact. Без источника — (None, []). Поправки — во втором значении и в Fact.fixes."""
    fixes: list[str] = []
    body = line.strip()
    body = re.sub(r"^(?:-|\*|\d+\.)\s+", "", body)
    starts = list(SOURCE_START.finditer(body))
    if not starts or not body.rstrip().endswith(")"):
        return None, fixes
    m = starts[-1]
    text = body[:m.start()].rstrip()
    inner = body[m.end():].rstrip()[:-1].strip()
    if not m.group(0).lower().startswith("(ист:"):
        fixes.append("ПОПРАВЛЕНО: начало источника → `(ист:`")
    if ";" in inner:
        source, status = inner.rsplit(";", 1)
    elif "," in inner and fix_status(inner.rsplit(",", 1)[1], []).split(" ")[0] in STATUSES:
        source, status = inner.rsplit(",", 1)
        fixes.append("ПОПРАВЛЕНО: статус отделяется точкой с запятой `;`")
    else:
        source, status = inner, ""
    fact = Fact(text, fix_source(source, fixes), fix_status(status, fixes) if status else "", fixes)
    return fact, fixes


def check_text(text: str) -> list[Problem]:
    problems = []
    found = CJK.findall(text)
    if found:
        problems.append(Problem("ОШИБКА", f"В тексте символы не кириллицы и не латиницы: `{''.join(found)}`. "
                                          "Только русский язык"))
    if THINK.search(text):
        problems.append(Problem("ОШИБКА", "В файле теги рассуждений модели. Удалите их"))
    return problems


def material_lines(path: Path) -> int | None:
    if path.suffix.lower() in (".xlsx", ".xls", ".pdf", ".docx", ".png", ".jpg"):
        return None
    try:
        return len(kbbase.read_lines(path))
    except UnicodeDecodeError:
        return None


def check_source(root: Path, team: kbbase.Team, fact: Fact) -> list[Problem]:
    src, status = fact.source, fact.status.split(" ")[0]
    err = lambda msg: [Problem("ОШИБКА", msg)]  # noqa: E731

    m = MATERIAL.match(src)
    if m:
        path, line_from, line_to, sheet = m.group(1), m.group(2), m.group(3), m.group(4)
        full = root / path
        if not full.is_file():
            files = {p.name: kbbase.rel(root, p) for p in (root / "materials").rglob("*") if p.is_file()}
            close = difflib.get_close_matches(Path(path).name, list(files), n=3, cutoff=0.5)
            hint = f" Похожие: {', '.join(f'`{files[c]}`' for c in close)}" if close else ""
            return err(f"Файла `{path}` нет.{hint}")
        total = material_lines(full)
        if total is not None:
            if not line_from:
                return err(f"Укажите строку: `{path}:<номер>`")
            for n in filter(None, (line_from, line_to)):
                if int(n) < 1 or int(n) > total:
                    return err(f"В `{path}` {total} строк, строки {n} нет")
        elif not (line_from or sheet):
            return err(f"Укажите место в файле: `{path}#<лист>!<ячейка>` или номер строки")
        if path.startswith("materials/ai/") and status == "подтверждено":
            return err("Ответ RAG — не выше «оценка». Поставьте `оценка` или найдите подтверждение "
                       "у человека или в материале")
        return []

    if src.lower().startswith("со слов"):
        parts = [p.strip() for p in src[len("со слов"):].split(",")]
        who = parts[0]
        via = None
        if " через " in who:
            who, via = (x.strip() for x in who.split(" через ", 1))
        if not who:
            return err("У «со слов» нет человека. Нужно: `со слов <кто>, ГГГГ-ММ-ДД`")
        if not team.knows(who):
            return err(f"«{who}» нет в `team.md`: ни среди людей пула, ни среди ролей бизнеса. "
                       "Имён бизнеса в базе нет — укажите роль")
        if via and not team.is_person(via):
            return err(f"«{via}» нет среди людей пула в `team.md`")
        if len(parts) < 2 or not kbbase.DATE_RE.match(parts[1]):
            return err(f"У «со слов» нет даты. Нужно: `со слов {who}, ГГГГ-ММ-ДД`")
        date = kbbase.parse_date(parts[1])
        if date is None:
            return err(f"Дата {parts[1]} не существует")
        if date > kbbase.today():
            return err(f"Дата {parts[1]} в будущем")
        return []

    m = OBJECT.match(src)
    if m:
        ident = m.group(1)
        path = kbbase.find_object(root, ident)
        if path is None:
            return err(f"{ident} нет в журнале")
        lines = kbbase.read_lines(path)
        head, body = kbbase.front_matter(lines)
        if ident.startswith("D-"):
            state = head.get("статус", "")
            if state == "отклонено":
                return err(f"{ident} отклонено. На отклонённое решение ссылаться нельзя")
            if state != "принято" and status == "подтверждено":
                return err(f"{ident} — «{state or 'без статуса'}». Пока решение не принято, факт — не выше «оценка»")
        if ident.startswith("Q-"):
            answer = [s for s in kbbase.sections(lines, body) if s[0] == "Ответ"]
            has = answer and any(lines[i].strip().startswith("- ") for i in range(answer[0][1] + 1, answer[0][2]))
            if not has:
                return err(f"У {ident} нет ответа. Сошлитесь на источник ответа или дождитесь его")
        return []

    if NOT_SOURCES.search(src):
        return err("Ответ ИИ — не источник. Сошлитесь на материал или на слова человека. "
                   "Ответ RAG сохраните в `materials/ai/`")
    return err(f"Непонятный источник «{src}». {SOURCE_FORMS}")


def check_status(root: Path, fact: Fact) -> list[Problem]:
    if not fact.status:
        return [Problem("ОШИБКА", f"Нет статуса. {STATUS_HELP}")]
    word, _, rest = fact.status.partition(" ")
    if word not in STATUSES:
        return [Problem("ОШИБКА", f"Неизвестный статус «{fact.status}». {STATUS_HELP}")]
    if word == "допущение":
        ident = rest.strip()
        if not re.match(r"^A-[\w-]+$", ident):
            return [Problem("ОШИБКА", "У допущения нужен номер: `допущение A-NNN`")]
        if kbbase.find_object(root, ident) is None:
            return [Problem("ОШИБКА", f"Допущения {ident} нет в журнале")]
    elif rest:
        return [Problem("ОШИБКА", f"Лишнее после статуса: «{rest}». {STATUS_HELP}")]
    return []


def check(root: Path, team: kbbase.Team, line: str) -> tuple[Fact | None, list[str], list[Problem]]:
    """Полная проверка строки факта: (факт, поправки, проблемы)."""
    fact, fixes = parse(line)
    problems = check_text(line)
    if fact is None:
        return None, fixes, problems + [Problem("ОШИБКА", f"Факт без источника и статуса. {FORMAT_HELP}")]
    if not fact.text:
        problems.append(Problem("ОШИБКА", "Пустой текст факта"))
    status_problems = check_status(root, fact)
    problems += status_problems
    if fact.status:
        problems += check_source(root, team, fact)
    if fact.status.startswith("противоречие") and re.search(r"\bбудет\b|в продукте|кнопк", fact.text, re.I):
        problems.append(Problem("ПРЕДУПРЕЖДЕНИЕ", "«Как сейчас» и «как будет» — не противоречие. "
                                                 "Намерение пишется в раздел «Как будет»"))
    return fact, fixes, problems


def insert(root: Path, path: Path, section: str, line: str, allowed: set[str] | None) -> str | None:
    """Вставить строку факта в конец раздела. Возвращает текст ошибки или None. Печатает «ЗАПИСАНО»."""
    lines = kbbase.read_lines(path)
    head, body = kbbase.front_matter(lines)
    secs = {name: (start, end) for name, start, end in kbbase.sections(lines, body)}
    if section not in secs or (allowed and section not in allowed):
        names = [n for n in secs if not allowed or n in allowed]
        return f"в {head.get('id', path.stem)} нет раздела для фактов «{section}». Разделы: {', '.join(names)}"
    start, end = secs[section]
    last = start
    for i in range(start + 1, end):
        if lines[i].strip():
            last = i
    numbered = [l for l in lines[start + 1:end] if re.match(r"^\d+\.\s+", l)]
    new_line = f"{len(numbered) + 1}. {line[2:]}" if numbered and line.startswith("- ") else line
    lines.insert(last + 1, new_line)
    if last == start:
        lines.insert(last + 1, "")
        if last + 3 < len(lines) and lines[last + 3].strip():
            lines.insert(last + 3, "")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"ЗАПИСАНО: {kbbase.rel(root, path)}, раздел «{section}»:")
    print(new_line)
    return None
