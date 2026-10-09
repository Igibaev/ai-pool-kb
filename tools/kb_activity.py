"""Активность пула по истории git: кто что делал и где буксует процесс.

Вызывается из kb.py:
    python3 tools/kb.py activity [--since <дата|хеш>] [--until <дата>] [--who <имя>] [--html <файл>]

Источник — только git: коммиты всех веток (сначала git fetch --all), изменения файлов шагов,
журнала, датасетов и материалов. Чтение, ничего не меняет.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import statistics
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

AUTOMATION = {"CI", "ci", "GitLab CI", "kb-bot"}
FACT_RE = re.compile(r"\((?:[^()]|\([^()]*\))*,\s*(подтверждено|оценка|допущение\s+A-[\w-]+|противоречие)\b[^()]*\)\s*$")
ID_IN_MSG = re.compile(r"\b(PM-\d{2}(-\d{2})?|SYS-\d{2}|[QADR]-(\d{3}|new-[\w-]+))\b")
SKILL_TRAILER = re.compile(r"^Скилл:\s*(kb-[\w-]+)", re.M)
STATUS_ORDER = ["Черновик", "Готов к взятию", "Разбор", "Сверка с бизнесом", "Нарезка", "Приёмка",
                "Декомпозиты в работе", "Готово"]
OPEN_Q = ("открыт", "ждёт бизнес", "возможно отвечен")


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")
    return r.stdout if r.returncode == 0 else ""


def _date(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.strip()).replace(tzinfo=None)


def _parse_period(root: Path, since: str | None, until: str | None, today: dt.date):
    def to_dt(v: str | None, default: dt.datetime, is_until: bool = False) -> dt.datetime:
        if not v:
            return default
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", v):
            d = dt.datetime.fromisoformat(v)
            return d + dt.timedelta(days=1) if is_until and len(v) == 10 else d
        if re.fullmatch(r"\d+д", v):  # «7д» — последние 7 дней
            return dt.datetime.combine(today - dt.timedelta(days=int(v[:-1])), dt.time())
        if not re.fullmatch(r"[0-9a-fA-F]{6,40}", v):
            raise SystemExit(f"Не понял дату или хеш «{v}»: нужен ГГГГ-ММ-ДД, «7д» или хеш коммита")
        out = _git(root, "show", "-s", "--format=%aI", "--end-of-options", v).strip()
        return _date(out) if out else default
    start = to_dt(since, dt.datetime.combine(today - dt.timedelta(days=7), dt.time()))
    end = to_dt(until, dt.datetime.combine(today + dt.timedelta(days=1), dt.time()), is_until=True)
    return start, end


def collect(root: Path):
    """Все коммиты со всех веток: автор, время, сообщение, добавленные и удалённые строки по файлам."""
    raw = _git(root, "-c", "core.quotepath=false", "log", "--all", "--reverse", "--date=iso-strict", "-p", "--unified=0",
               "--no-color", "--find-renames", "--format=%x1eC%x1f%H%x1f%an%x1f%ad%x1f%P%x1f%B%x1d", "--",
               "context", "journal", "datasets", "materials", "specs", "core", "product.md", "team.md",
               "FEEDBACK.md", ":(exclude)context/process/_index.md", ":(exclude)journal/_index.md",
               ":(exclude)context/process/_map.svg", ":(exclude)context/process/_map.html")
    commits = []
    for chunk in raw.split("\x1eC\x1f")[1:]:
        head, _, body = chunk.partition("\x1d")
        h, an, ad, parents, msg = head.split("\x1f", 4)
        c = dict(hash=h, author=an, when=_date(ad), merge=len(parents.split()) > 1, msg=msg.strip(),
                 files={}, new_files=set())
        cur = None
        for line in body.splitlines():
            if line.startswith("diff --git "):
                m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
                cur = m.group(2) if m else None
                if cur:
                    c["files"].setdefault(cur, {"+": [], "-": []})
            elif line.startswith("new file mode") and cur:
                c["new_files"].add(cur)
            elif line.startswith("rename from ") and cur:
                c.setdefault("renamed", {})[cur] = line[len("rename from "):].strip()
            elif cur and line.startswith("+") and not line.startswith("+++"):
                c["files"][cur]["+"].append(line[1:])
            elif cur and line.startswith("-") and not line.startswith("---"):
                c["files"][cur]["-"].append(line[1:])
        commits.append(c)
    # слияния (MR) — отдельно: в -p они без диффа
    for line in _git(root, "-c", "core.quotepath=false", "log", "--all", "--merges", "--date=iso-strict",
                     "--format=%H%x1f%an%x1f%ad%x1f%s").splitlines():
        h, an, ad, s = line.split("\x1f", 3)
        if not any(x["hash"] == h for x in commits):
            commits.append(dict(hash=h, author=an, when=_date(ad), merge=True, msg=s, files={}, new_files=set()))
    commits.sort(key=lambda x: x["when"])
    return commits


def _fm_value(lines: list[str], key: str) -> str | None:
    for l in lines:
        m = re.match(rf"^{re.escape(key)}:\s*(.*?)\s*(#.*)?$", l)
        if m:
            return m.group(1)
    return None


def classify(c: dict) -> list[tuple[str, str]]:
    """Действия коммита: (вид, деталь)."""
    acts = []
    who = c["author"]
    for path, ch in c["files"].items():
        name = Path(path).name
        if "/_template" in path or name.startswith(("_template", "README", ".gitkeep")):
            continue
        idm = re.match(r"^([QADR]-(?:\d{3}|new-[\w-]+)|PM-\d{2}|SYS-\d{2}|GD-[\w-]+)", name)
        ident = idm.group(1) if idm else name
        plus = ch["+"]
        if path.startswith("context/process/PM-") and not name.startswith("_"):
            owner = _fm_value(plus, "владелец рута")
            mate = _fm_value(plus, "напарник")
            status = _fm_value(plus, "статус")
            if owner == who:
                acts.append(("взял рут", ident))
            elif owner in ("не назначен",) and _fm_value(ch["-"], "владелец рута") == who:
                acts.append(("вернул рут", ident))
            elif owner and owner not in ("не назначен", "—") and _fm_value(ch["-"], "напарник") == owner:
                acts.append(("передал рут", ident))
            if mate == who:
                acts.append(("вошёл в пару", ident))
            if status and not owner:
                acts.append(("статус рута", f"{ident} → {status}"))
            facts = [l for l in plus if FACT_RE.search(l.strip())]
            if facts:
                acts.append(("факты", f"{ident}:{len(facts)}"))
            dec = [l for l in plus if re.match(rf"^\|\s*{ident}-\d{{2}}\s*\|", l)]
            if dec:
                acts.append(("декомпозиты", f"{ident}:{len(dec)}"))
            if "## Приёмка рута" in "\n".join(plus) or (status and status == "Декомпозиты в работе"):
                pass
        elif path.startswith("context/systems/SYS-"):
            if _fm_value(plus, "владелец раздела в пуле") == who:
                acts.append(("взял систему", ident))
            facts = [l for l in plus if FACT_RE.search(l.strip())]
            if facts:
                acts.append(("факты", f"{ident}:{len(facts)}"))
        elif path.startswith("context/process/") and name.startswith("_map.md"):
            acts.append(("карта", "изменена"))
        elif path.startswith("journal/questions/"):
            if path in c["new_files"]:
                acts.append(("вопрос заведён", ident))
            st = _fm_value(plus, "статус")
            if st == "отвечен":
                acts.append(("ответ записан", ident))
            elif st == "стал допущением":
                acts.append(("вопрос → допущение", ident))
            elif st == "дубль":
                acts.append(("дубль склеен", ident))
        elif path.startswith("journal/assumptions/"):
            if path in c["new_files"]:
                acts.append(("допущение", ident))
            st = _fm_value(plus, "статус")
            if st in ("подтверждено", "опровергнуто"):
                acts.append((f"допущение {st}", ident))
        elif path.startswith("journal/decisions/"):
            if path in c["new_files"]:
                acts.append(("решение предложено", ident))
            if _fm_value(plus, "статус") == "принято":
                acts.append(("решение принято", ident))
        elif path.startswith("journal/risks/"):
            if path in c["new_files"]:
                acts.append(("риск", ident))
            elif _fm_value(plus, "статус"):
                acts.append(("риск обновлён", ident))
        elif path.startswith("journal/digest/") and path in c["new_files"]:
            acts.append(("дайджест", name))
        elif path.startswith("datasets/") and path in c["new_files"] and not name.startswith(("README", "_")):
            acts.append(("случай датасета", ident))
        elif path.startswith("materials/") and path in c["new_files"] and not name.startswith("README"):
            acts.append(("материал", path.split("materials/", 1)[1]))
        elif path.startswith("specs/") and path in c["new_files"]:
            acts.append(("спека", name))
        elif path == "FEEDBACK.md" and path not in c["new_files"]:
            notes = [l for l in plus if l.strip() and not l.startswith("#")]
            if notes:
                acts.append(("замечание к инструменту", str(len(notes))))
    return acts


def _workdays(a: dt.datetime, b: dt.datetime) -> float:
    days, d = 0, a.date()
    while d < b.date():
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def build(root: Path, kb, since: str | None, until: str | None, who: str | None):
    today = kb.TODAY
    start, end = _parse_period(root, since, until, today)
    commits = collect(root)
    fm_team, roles = kb.team_info()
    people = [p for p in roles]
    def is_auto(c):
        return c["author"] in AUTOMATION or c["msg"].startswith("kb: номера и сводки")
    in_period = [c for c in commits if start <= c["when"] < end and not is_auto(c)]

    per = defaultdict(lambda: dict(commits=0, merges=0, days=set(), acts=Counter(), detail=defaultdict(list),
                                   no_id=0, skills=Counter(), last=None, others_roots=set()))
    owner_at = {}   # (hash, рут) -> кто вёл рут в момент коммита
    state = defaultdict(dict)
    for c in commits:
        for path, ch in c["files"].items():
            name = Path(path).name
            if path.startswith("context/process/PM-") and not name.startswith("_"):
                rid = name[:5]
                owner_at[(c["hash"], rid)] = {v for v in state[rid].values() if v and v not in ("не назначен", "—")}
                for f in ("владелец рута", "напарник"):
                    v = _fm_value(ch["+"], f)
                    if v is not None:
                        state[rid][f] = v
    for c in in_period:
        p = per[c["author"]]
        p["last"] = c["when"]
        p["days"].add(c["when"].date())
        if c["merge"]:
            p["merges"] += 1
            continue
        p["commits"] += 1
        if not ID_IN_MSG.search(c["msg"].splitlines()[0] if c["msg"] else ""):
            p["no_id"] += 1
        for s in SKILL_TRAILER.findall(c["msg"]):
            p["skills"][s] += 1
        for kind, det in classify(c):
            if kind in ("факты", "декомпозиты"):
                ident, n = det.split(":")
                p["acts"][kind] += int(n)
                if ident not in p["detail"][kind]:
                    p["detail"][kind].append(ident)
                pair = owner_at.get((c["hash"], ident), set())
                if kind == "факты" and ident.startswith("PM-") and pair and c["author"] not in pair:
                    p["others_roots"].add(ident)
            else:
                p["acts"][kind] += 1
                if det not in p["detail"][kind]:
                    p["detail"][kind].append(det)

    # руты: путь по статусам и сколько дней в текущем
    roots = []
    hist = defaultdict(list)
    for c in commits:
        for path, ch in c["files"].items():
            name = Path(path).name
            if path.startswith("context/process/PM-") and not name.startswith("_"):
                st = _fm_value(ch["+"], "статус")
                if st:
                    hist[name[:5]].append((c["when"], st))
    for it in sorted(kb.load("step"), key=lambda x: x["fm"].get("id", "")):
        fm = it["fm"]
        rid = fm.get("id")
        h = hist.get(rid, [])
        since_cur = h[-1][0] if h else None
        last_touch = max((c["when"] for c in commits if not c["merge"] and c["author"] not in AUTOMATION
                          and any(Path(p).name.startswith(rid) and p.startswith("context/process/") for p in c["files"])),
                         default=None)
        path_txt = " → ".join(f"{s} {w:%d.%m}" for w, s in h)
        roots.append(dict(id=rid, step=fm.get("шаг", ""), status=fm.get("статус", ""),
                          who=" + ".join(kb.pair_of(fm)) or "—",
                          days=max(0, (today - since_cur.date()).days) if since_cur else None,
                          idle=max(0, (today - last_touch.date()).days) if last_touch else None,
                          path=path_txt))

    # вопросы
    qs = kb.load("question")
    answered_at = {}
    for c in commits:
        for path, ch in c["files"].items():
            if path.startswith("journal/questions/") and _fm_value(ch["+"], "статус") == "отвечен":
                answered_at.setdefault(_fm_value(ch["+"], "id") or Path(path).stem[:5], c["when"])
    waits, open_q = [], []
    for q in qs:
        fm = q["fm"]
        created = kb.parse_date(fm.get("создан"))
        qid = fm.get("id", "")
        if fm.get("статус") == "отвечен" and created:
            at = answered_at.get(qid) or next((v for k, v in answered_at.items() if qid.startswith(k)), None)
            if at:
                waits.append(_workdays(dt.datetime.combine(created, dt.time()), at))
        if fm.get("статус") in OPEN_Q and created:
            open_q.append((qid, fm.get("статус"), _workdays(dt.datetime.combine(created, dt.time()),
                                                           dt.datetime.combine(today, dt.time())),
                           kb.first_heading(q["text"])))
    open_q.sort(key=lambda x: -x[2])

    # сигналы процесса
    signals = []
    limit = kb.wip_limit()
    solo = str(fm_team.get("режим", "")).strip().startswith("соло")
    free = [p for p in people if len(kb.wip_of(p)) < limit and "разработчик" in roles.get(p, "")]
    deps_on_pm00 = [it["fm"].get("id") for it in kb.load("step")
                    if it["fm"].get("id") != "PM-00" and "PM-00" in kb.section_text(it["text"], "Нарезка на декомпозиты")]
    pm00 = next((it for it in kb.load("step") if it["fm"].get("id") == "PM-00"), None)
    if pm00 and not kb.pair_of(pm00["fm"]) and deps_on_pm00:
        signals.append(f"PM-00 «Каркас и общие части» без владельца, а от него зависят декомпозиты {', '.join(deps_on_pm00)}. "
                       "Смотреть: техлид потока или архитектор недели должен взять PM-00 (kb.py take PM-00).")
    for it in kb.load("step"):
        if it["fm"].get("статус") == "Декомпозиты в работе":
            blockers = [q["fm"].get("id") for q in qs if q["fm"].get("статус") in OPEN_Q
                        and any(str(b).startswith(it["fm"].get("id") + "-") for b in (q["fm"].get("блокирует") or []))]
            if blockers:
                signals.append(f"{it['fm'].get('id')} в «Декомпозиты в работе», но его декомпозиты блокируют открытые вопросы "
                               f"{', '.join(blockers)}. Смотреть: приёмка прошла раньше ответов или допущений.")
    for r in roots:
        if solo:
            break
        if r["status"] == "Готов к взятию" and r["days"] is not None and r["days"] >= 2 and free:
            signals.append(f"{r['id']} «Готов к взятию» {r['days']} дн., а место в лимите есть у: {', '.join(free)}. "
                           "Смотреть: приоритет понятен? шаг выглядит неподъёмным? люди знают про /kb-take?")
        if r["status"] in ("Разбор", "Сверка с бизнесом", "Нарезка") and r["idle"] is not None and r["idle"] >= 3:
            signals.append(f"{r['id']} в «{r['status']}», без правок {r['idle']} дн. Смотреть: человек ждёт бизнес, "
                           "занят другим или застрял в разборе?")
    stale_q = [q for q in open_q if q[2] >= 2]
    if stale_q:
        signals.append(f"Вопросов без ответа 2+ рабочих дня: {len(stale_q)} (старший — {stale_q[0][0]}, {stale_q[0][2]} дн.). "
                       "Смотреть: созвоны аналитика с бизнесом, допущения (/kb-assume).")
    overdue = [a["fm"].get("id") for a in kb.load("assumption")
               if a["fm"].get("статус") == "открыто" and kb.parse_date(a["fm"].get("проверить до"))
               and kb.parse_date(a["fm"].get("проверить до")) < today]
    if overdue:
        signals.append(f"Просроченные допущения: {', '.join(overdue)}.")
    total_acts = sum(sum(v["acts"].values()) for v in per.values()) or 1
    for name, v in per.items():
        share = sum(v["acts"].values()) / total_acts
        if share >= 0.5 and len(per) > 2:
            signals.append(f"Больше половины действий за период — у {name} ({share:.0%}). "
                           "Смотреть: не стал ли человек узким местом (дежурство, ответы, ревью)?")
    no_id_total = sum(v["no_id"] for v in per.values())
    all_commits = sum(v["commits"] for v in per.values())
    if all_commits and no_id_total / all_commits > 0.2:
        signals.append(f"Коммитов без ID раздела в сообщении: {no_id_total} из {all_commits}. "
                       "Смотреть: агенты пропускают правило 6 AGENTS.md — скиллы или модель.")
    silent = [p for p in people if p not in per]
    if all_commits and not any(v["skills"] for v in per.values()):
        signals.append("В коммитах нет строки «Скилл: kb-…» — не видно, какие скиллы используют. "
                       "Правило 6 AGENTS.md просит её добавлять.")

    feedback = []
    fpath = root / "FEEDBACK.md"
    if fpath.exists():
        for c in in_period:
            ch = c["files"].get("FEEDBACK.md")
            if ch:
                if "FEEDBACK.md" not in c["new_files"]:
                    feedback += [(c["author"], l.strip()) for l in ch["+"] if l.strip() and not l.startswith("#")]

    # временные ID → постоянные, по журналу переименований
    ren = {k: v[0] for k, v in kb.known_renames().items()} if hasattr(kb, "known_renames") else {}
    if ren:
        for v in per.values():
            for kind, dets in v["detail"].items():
                v["detail"][kind] = list(dict.fromkeys(ren.get(d, d) for d in dets))
    if who:
        per = {k: v for k, v in per.items() if k == who}
    return dict(start=start, end=end, per=per, roles=roles, silent=silent if not who else [],
                roots=roots, waits=waits, open_q=open_q, signals=signals, feedback=feedback,
                n_commits=len(in_period))


LABELS = [("взял рут", "взял(а) рут"), ("вошёл в пару", "вошёл(ла) в пару"), ("вернул рут", "вернул(а) рут"),
          ("передал рут", "передал(а) рут"), ("взял систему", "взял(а) систему"),
          ("факты", "фактов"), ("вопрос заведён", "вопросов заведено"), ("ответ записан", "ответов записано"),
          ("дубль склеен", "дублей склеено"), ("вопрос → допущение", "вопросов → в допущение"),
          ("допущение", "допущений"), ("допущение подтверждено", "допущений подтверждено"),
          ("допущение опровергнуто", "допущений опровергнуто"), ("решение предложено", "решений предложено"),
          ("решение принято", "решений принято"), ("риск", "рисков"), ("риск обновлён", "рисков обновлено"),
          ("декомпозиты", "декомпозитов"), ("случай датасета", "случаев в датасет"), ("материал", "материалов"),
          ("дайджест", "дайджестов"), ("карта", "правок карты"), ("спека", "спек"),
          ("статус рута", "смен статуса рута"), ("замечание к инструменту", "строк в FEEDBACK.md")]


def render_text(r: dict) -> str:
    out = [f"# Активность пула: {r['start']:%d.%m.%Y} — {(r['end'] - dt.timedelta(seconds=1)):%d.%m.%Y}",
           "",
           "Источник — история git всех веток. Число действий — не оценка работы: разбор сложного шага даёт мало "
           "коммитов. Отчёт — чтобы видеть, где буксует процесс и инструмент.",
           f"Коммитов людей за период: {r['n_commits']}.", "", "## Кто что делал", ""]
    for name, v in sorted(r["per"].items(), key=lambda x: x[0]):
        role = r["roles"].get(name, "нет в team.md")
        out.append(f"### {name} — {role}")
        out.append(f"Дней с правками: {len(v['days'])}; коммитов: {v['commits']}"
                   + (f"; слил(а) MR: {v['merges']}" if v["merges"] else "")
                   + (f"; последняя правка {v['last']:%d.%m %H:%M}" if v["last"] else ""))
        for key, label in LABELS:
            n = v["acts"].get(key)
            if n:
                det = v["detail"].get(key, [])
                out.append(f"- {label}: {n}" + (f" — {', '.join(det[:8])}" + (" …" if len(det) > 8 else "") if det else ""))
        if v["others_roots"]:
            out.append(f"- помогал(а) в чужих рутах: {', '.join(sorted(v['others_roots']))}")
        if v["skills"]:
            out.append("- скиллы: " + ", ".join(f"{k} {n}" for k, n in v["skills"].most_common()))
        if v["no_id"]:
            out.append(f"- коммитов без ID в сообщении: {v['no_id']}")
        out.append("")
    if r["silent"]:
        out += ["## Без правок за период", "", ", ".join(f"{p} ({r['roles'][p]})" for p in r["silent"]),
                "Это не обязательно плохо: менеджер, архитектор и бизнес-роли работают не только в репозитории.", ""]
    out += ["## Руты", "", "| Рут | Шаг | Кто | Статус | Дней в статусе | Дней без правок | Путь |", "|---|---|---|---|---|---|---|"]
    for x in r["roots"]:
        out.append(f"| {x['id']} | {x['step']} | {x['who']} | {x['status']} | {x['days'] if x['days'] is not None else '—'} "
                   f"| {x['idle'] if x['idle'] is not None else '—'} | {x['path'] or '—'} |")
    out += ["", "## Вопросы к бизнесу", ""]
    if r["waits"]:
        out.append(f"Отвечено: {len(r['waits'])}; до ответа в среднем {statistics.mean(r['waits']):.1f} раб. дн., "
                   f"медиана {statistics.median(r['waits']):.1f}, дольше всего {max(r['waits']):.0f}.")
    out.append(f"Открыто сейчас: {len(r['open_q'])}" + (": " + "; ".join(f"{q[0]} [{q[1]}] {q[2]:.0f} дн." for q in r["open_q"][:6]) if r["open_q"] else "."))
    out += ["", "## Сигналы для настройки процесса", ""]
    out += [f"- {s}" for s in r["signals"]] or ["- Явных сигналов нет."]
    if r["feedback"]:
        out += ["", "## Новое в FEEDBACK.md", ""] + [f"- {a}: {l}" for a, l in r["feedback"][:20]]
    return "\n".join(out) + "\n"


def render_html(r: dict) -> str:
    days = []
    d = r["start"].date()
    while d < r["end"].date():
        days.append(d)
        d += dt.timedelta(days=1)
    days = days[-31:]
    e = html.escape
    rows = []
    for name, v in sorted(r["per"].items()):
        cnt = Counter(x for x in v["days"])
        cells = "".join(f'<td class="c{min(3, 1 if dd in cnt else 0)}" title="{dd:%d.%m}"></td>' for dd in days)
        acts = "".join(f"<li>{e(label)}: <b>{v['acts'][k]}</b>"
                       + (f" <span class=m>{e(', '.join(v['detail'].get(k, [])[:6]))}</span>" if v['detail'].get(k) else "")
                       + "</li>" for k, label in LABELS if v["acts"].get(k))
        rows.append(f"<section class=card><h3>{e(name)} <span class=m>{e(r['roles'].get(name, ''))}</span></h3>"
                    f"<p class=m>Дней с правками: {len(v['days'])} · коммитов: {v['commits']}"
                    + (f" · MR: {v['merges']}" if v["merges"] else "") + "</p>"
                    f"<table class=heat><tr>{cells}</tr></table><ul>{acts or '<li class=m>правок в базе знаний нет</li>'}</ul></section>")
    roots = "".join(f"<tr><td>{e(x['id'])}</td><td>{e(x['step'])}</td><td>{e(x['who'])}</td><td>{e(x['status'])}</td>"
                    f"<td>{'' if x['days'] is None else x['days']}</td><td>{'' if x['idle'] is None else x['idle']}</td></tr>"
                    for x in r["roots"])
    sig = "".join(f"<li>{e(s)}</li>" for s in r["signals"]) or "<li>Явных сигналов нет.</li>"
    return f"""<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Активность пула</title><style>
:root{{--bg:#F4F5F7;--card:#fff;--ink:#1C2033;--m:#5E6478;--line:#D5D9E2;--h1:#C9D6F5;--h2:#7F9BE0;--h3:#3A5CC4}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14161F;--card:#1C1F2B;--ink:#E6E8F0;--m:#9AA0B4;--line:#2E3242;--h1:#2A3760;--h2:#3F5BA8;--h3:#6E8EF0}}}}
body{{background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1100px;margin:0 auto}} .m{{color:var(--m);font-weight:400;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.card h3{{margin:0 0 4px;font-size:16px}} ul{{padding-left:18px;margin:8px 0 0}}
.heat{{border-collapse:separate;border-spacing:2px;margin-top:6px}} .heat td{{width:9px;height:9px;border-radius:2px;background:var(--line)}}
.heat .c1{{background:var(--h3)}} table.t{{width:100%;border-collapse:collapse;background:var(--card)}}
.t td,.t th{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;font-size:14px}}
.wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>Активность пула</h1>
<p class=m>{r['start']:%d.%m.%Y} — {(r['end'] - dt.timedelta(seconds=1)):%d.%m.%Y}. Источник — история git. Число действий — не оценка работы: отчёт показывает, где буксует процесс.</p>
<h2>Сигналы для настройки процесса</h2><ul>{sig}</ul>
<h2>Кто что делал</h2><p class=m>Клетки — дни периода, закрашены дни с правками.</p><div class=grid>{''.join(rows)}</div>
<h2>Руты</h2><div class=wrap><table class=t><tr><th>Рут</th><th>Шаг</th><th>Кто</th><th>Статус</th><th>Дней в статусе</th><th>Дней без правок</th></tr>{roots}</table></div>
</main></body></html>"""


def run(args: list[str], kb) -> int:
    opts = {"--since": None, "--until": None, "--who": None, "--html": None}
    i = 0
    while i < len(args):
        if args[i] in opts and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            print(f"Не понял аргумент «{args[i]}». Пример: kb.py activity --since 2026-10-12 --html report.html")
            return 2
    r = build(kb.ROOT, kb, opts["--since"], opts["--until"], opts["--who"])
    print(render_text(r), end="")
    if opts["--html"]:
        Path(opts["--html"]).write_text(render_html(r), encoding="utf-8")
        print(f"\nСтраница: {opts['--html']}")
    return 0
