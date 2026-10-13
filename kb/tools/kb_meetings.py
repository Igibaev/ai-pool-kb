"""Встречи: расшифровки в kb/materials/meetings/ и краткое саммари к каждой.

Вызывается из kb.py: `new meeting`, `index`, `check`, `whoami`.
Расшифровка — материал: не правится, факты ссылаются на её строки.
Саммари — навигация: что было на встрече и куда это нести.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

FOLDER = "materials/meetings"
OLD_FOLDER = "materials/interviews"          # репозитории, начатые до переименования
REQUIRED = ("встреча", "с кем", "тема", "расшифровка")
DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b|\b(\d{2})\.(\d{2})\.(20\d{2})\b")


def folders(kb) -> list[Path]:
    return [kb.KB / FOLDER, kb.KB / OLD_FOLDER]


def transcripts(kb) -> list[Path]:
    out = []
    for d in folders(kb):
        if d.is_dir():
            out += [p for p in sorted(d.glob("*.md")) if not p.name.startswith("_")
                    and not p.name.endswith(".summary.md") and p.name != "README.md"
                    and "-plan" not in p.stem]
    return out


def summaries(kb) -> list[dict]:
    out = []
    for d in folders(kb):
        if d.is_dir():
            for p in sorted(d.glob("*.summary.md")):
                if p.name.startswith("_"):
                    continue
                text = p.read_text(encoding="utf-8")
                out.append({"path": p, "fm": kb.parse_frontmatter(text), "text": text})
    return out


def _date_from(text: str) -> str:
    # шапка convert («Оригинал: … Конвертировано: <дата>») — не дата встречи
    text = "\n".join(l for l in text.splitlines() if not l.startswith(("Оригинал:", "Статус:", "Тип:")))
    m = DATE_RE.search(text[:4000])
    if not m:
        return ""
    if m.group(1):
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return f"{m.group(6)}-{m.group(5)}-{m.group(4)}"


def cmd_new_meeting(kb, slug: str, extra: list[str]) -> int:
    fields, _, _, bad = kb.parse_pairs(extra)
    if bad:
        print("ОШИБКА  " + "; ".join(f"не понял «{b}» — нужен вид поле=значение" for b in bad))
        return 2
    src_arg = fields.get("из", "")
    if not src_arg:
        print("ОШИБКА  нужен из=<файл расшифровки>, обычно kb/inbox/_md/<файл>.md (после kb.py convert)")
        return 2
    src = Path(src_arg)
    if not src.is_absolute():
        src = (Path.cwd() / src)
    if not src.is_file():
        print(f"ОШИБКА  нет файла {src_arg}")
        return 1
    text = src.read_text(encoding="utf-8", errors="replace")
    date = fields.get("дата") or _date_from(text) or _date_from(src.name)
    if not date or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date):
        print("ОШИБКА  не нашёл дату встречи в файле — укажите дата=ГГГГ-ММ-ДД. Дату копирования не подставляйте")
        return 2
    with_whom = fields.get("с кем", "")
    topic = fields.get("тема", "")
    if not with_whom or not topic:
        print("ОШИБКА  нужны «с кем=<роль или команда>» и тема=\"<одной фразой>\"")
        return 2
    dest_dir = kb.KB / FOLDER
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = f"{date}-{kb.slugify(slug)}"
    tr, sm = dest_dir / f"{name}.md", dest_dir / f"{name}.summary.md"
    if tr.exists() or sm.exists():
        print(f"ОШИБКА  уже есть {kb.rel(tr)} — другое короткое имя или это та же встреча")
        return 1
    shutil.copyfile(src, tr)
    steps = fields.get("шаги", "")
    steps_list = [s for s in re.findall(r"(?:PM|SYS)-\d{2}", steps.upper())]
    tpl = (kb.KB / FOLDER / "_template.summary.md").read_text(encoding="utf-8")
    fm = (f"---\nвстреча: {date}\nс кем: {with_whom}\nтема: {topic}\n"
          f"длительность: {fields.get('длительность', '—')}\nрасшифровка: {kb.rel(tr)}\n"
          f"шаги: [{', '.join(steps_list)}]\n---\n")
    body = tpl.split("---\n", 2)[2].replace("<Тема> — <ГГГГ-ММ-ДД>", f"{topic} — {date}", 1)
    kb.write(sm, fm + body)
    orig = re.search(r"^Оригинал: (\S.*?) · ", text, flags=re.M)
    log = kb.KB / "inbox" / "_razobrano.md"
    if log.exists():
        src_name = orig.group(1) if orig else kb.rel(src) if str(src).startswith(str(kb.ROOT)) else src_arg
        with open(log, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"| {src_name} | {kb.rel(tr)} | {kb.TODAY.isoformat()} |\n")
    print(f"Расшифровка: {kb.rel(tr)} (не правится, факты ссылаются на её строки)")
    print(f"Саммари: {kb.rel(sm)} — ЗАПОЛНИ разделы по шаблону: Кратко, Цифры, Договорились, Не ясно, Что дальше. "
          "Каждый пункт — с номером строки расшифровки. Покажи саммари человеку.")
    return 0


def index(kb) -> Path | None:
    items = summaries(kb)
    if not items and not (kb.KB / FOLDER).is_dir():
        return None
    out = ["# Встречи", "", "Генерирует `kb.py index` из шапок саммари. Руками не править.", "",
           "| Дата | С кем | Тема | Шаги | Саммари |", "|---|---|---|---|---|"]
    for it in sorted(items, key=lambda x: str(x["fm"].get("встреча", "")), reverse=True):
        fm = it["fm"]
        steps = fm.get("шаги") or []
        steps = ", ".join(steps) if isinstance(steps, list) else str(steps)
        out.append(f"| {fm.get('встреча', '')} | {fm.get('с кем', '')} | {fm.get('тема', '')} | {steps} "
                   f"| [{it['path'].name}]({it['path'].name}) |")
    target = kb.KB / FOLDER / "_index.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    kb.write(target, "\n".join(out) + "\n")
    return target


def check(kb, errors: list, warnings: list) -> None:
    have = {s["path"].name[:-len(".summary.md")] for s in summaries(kb)}
    for t in transcripts(kb):
        if t.stem not in have and "sverka" not in t.stem:
            warnings.append(f"{kb.rel(t)}: встреча без саммари — {t.stem}.summary.md по шаблону {FOLDER}/_template.summary.md")
    for s in summaries(kb):
        fm, where = s["fm"], kb.rel(s["path"])
        miss = [f for f in REQUIRED if not str(fm.get(f, "")).strip() or kb.is_placeholder(str(fm.get(f, "")))]
        if miss:
            errors.append(f"{where}: в шапке саммари не заполнено: {', '.join(miss)}")
            continue
        tr = kb.ROOT / str(fm.get("расшифровка"))
        if not tr.is_file() and not (kb.KB / kb.kb_rel(str(fm.get("расшифровка")))).is_file():
            errors.append(f"{where}: расшифровка {fm.get('расшифровка')} не найдена")
        body = kb.body_of(s["text"])
        if re.search(r"<[^<>]{3,}>", body):
            warnings.append(f"{where}: саммари не заполнено — остались заглушки «<…>»")


def recent_for(kb, ids: set, days: int = 7) -> list[str]:
    out = []
    since = kb.TODAY - dt.timedelta(days=days)
    for s in summaries(kb):
        fm = s["fm"]
        d = kb.parse_date(fm.get("встреча"))
        steps = fm.get("шаги") or []
        steps = set(steps if isinstance(steps, list) else re.findall(r"(?:PM|SYS)-\d{2}", str(steps)))
        hit = sorted(steps & ids)
        if d and d >= since and hit:
            out.append(f"встреча {d} «{fm.get('тема', '')}» касается {', '.join(hit)} — саммари: {kb.rel(s['path'])}")
    return out
