"""Работа с ветками: каждый в своей ветке → MR → основная ветка (обычно develop) → уведомление в группе.

kb.py branch <ID|тема> [слово]   новая ветка <логин>/<ID>-<слово> от свежей основной ветки
kb.py mr                          проверка перед MR и слиянием: отставание, check, чужие ветки с теми же файлами,
                                  push, заголовок и описание MR; сохраняет текст уведомления
kb.py announce [--since <хеш>]    текст для группы: что вы влили в базу знаний

Основная ветка — поле «основная ветка» в шапке kb/team.md; нет поля — develop, если она есть, иначе main.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

GENERATED = ("kb/context/process/_index.md", "kb/journal/_index.md", "kb/context/process/_map.svg",
             "kb/context/process/_map.html", "kb/journal/_renames.tsv")
FACT_RE = re.compile(r"\((?:[^()]|\([^()]*\))+,\s*(подтверждено|оценка|допущение|противоречие)")
JOURNAL = {"questions": "Вопросы", "assumptions": "Допущения", "decisions": "Решения", "risks": "Риски"}


# ---------- git ----------

def _git(k, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=k.ROOT, capture_output=True,
                       text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r


def _out(k, *args: str) -> str:
    r = _git(k, *args)
    return r.stdout.strip() if r.returncode == 0 else ""


def has_remote(k) -> bool:
    return "origin" in _out(k, "remote").split()


def current(k) -> str:
    return _out(k, "rev-parse", "--abbrev-ref", "HEAD")


def _exists(k, ref: str) -> bool:
    return _git(k, "rev-parse", "--verify", "-q", ref).returncode == 0


def team_mode(k) -> str:
    try:
        return k.parse_frontmatter((k.KB / "team.md").read_text(encoding="utf-8")).get("режим", "команда").strip()
    except OSError:
        return "команда"


def base_branch(k) -> str:
    try:
        v = k.parse_frontmatter((k.KB / "team.md").read_text(encoding="utf-8")).get("основная ветка", "").strip()
    except OSError:
        v = ""
    if v and "<" not in v:
        return v
    for b in ("develop", "main", "master"):
        if _exists(k, b) or _exists(k, f"origin/{b}"):
            return b
    return "develop"


def base_ref(k) -> str:
    b = base_branch(k)
    return f"origin/{b}" if has_remote(k) and _exists(k, f"origin/{b}") else b


def fetch(k) -> bool:
    if not has_remote(k):
        return False
    try:
        r = subprocess.run(["git", "fetch", "--quiet", "--prune", "origin"], cwd=k.ROOT, capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def dirty(k) -> list[str]:
    return [l for l in _out(k, "status", "--porcelain").splitlines()
            if l.strip() and not l[3:].startswith((".kb/", "kb-report-"))]


def login(k, user: str) -> str:
    return k.slugify(user) or k.slugify(_out(k, "config", "user.name")) or "user"


def other_branches(k, mine: str = "") -> list[dict]:
    """Ветки на сервере, которых ещё нет в основной: кто, когда, сколько коммитов, какие файлы."""
    base, bref = base_branch(k), base_ref(k)
    out = []
    fmt = "%(refname:short)\t%(committerdate:short)\t%(authorname)"
    for line in _out(k, "for-each-ref", f"--format={fmt}", "refs/remotes/origin").splitlines():
        ref, date, who = (line.split("\t") + ["", ""])[:3]
        name = ref.removeprefix("origin/")
        if name in ("HEAD", base, "main", "master", "develop", mine) or ref == "origin":
            continue
        ahead = _out(k, "rev-list", "--count", f"{bref}..{ref}")
        if not ahead or ahead == "0":
            continue
        files = _out(k, "diff", "--name-only", f"{bref}...{ref}").splitlines()
        out.append(dict(ref=ref, name=name, date=date, who=who, ahead=int(ahead), files=files))
    return out


# ---------- взятие: чужие ветки ----------

def taken_elsewhere(k, relpath: str, user: str, fields: tuple[str, ...]) -> list[str]:
    """Кто уже взял этот файл в своей ветке, которую ещё не влили: [«Имя в ветке origin/…»]."""
    if not has_remote(k):
        return []
    fetch(k)
    bref = base_ref(k)
    base_txt = _out(k, "show", f"{bref}:{relpath}")
    base_fm = k.parse_frontmatter(base_txt) if base_txt else {}
    hits = []
    for b in other_branches(k, current(k)):
        if relpath not in b["files"]:
            continue
        txt = _out(k, "show", f"{b['ref']}:{relpath}")
        fm = k.parse_frontmatter(txt) if txt else {}
        for f in fields:
            v = str(fm.get(f, "")).strip()
            if v and v != str(base_fm.get(f, "")).strip() and v not in k.UNASSIGNED and v not in ("—", user):
                hits.append(f"{v} в ветке {b['ref']} ({b['date']})")
    return hits


def on_base_in_team(k) -> bool:
    return team_mode(k) == "команда" and has_remote(k) and current(k) == base_branch(k)


def commit_hint(k, msg: str, files: str) -> str:
    """Готовая команда коммита и push для текущей ветки."""
    b = current(k)
    push = f"git push -u origin {b}" if has_remote(k) else "(remote нет — push не нужен)"
    return (f"git commit -m \"{msg}\" -m \"Скилл: kb-take\" -- {files} && {push}. "
            "Ветку отправьте сразу: по ней другие увидят, что рут занят.")


# ---------- branch ----------

def cmd_branch(k, args: list[str]) -> int:
    if not args:
        print("Как: python3 kb/tools/kb.py branch <PM-04 | PM-04-K02 | Q-… | тема> [слово]\n"
              "Пример: branch PM-04 pravila → ветка ira/PM-04-pravila от свежей основной ветки")
        return 2
    base = base_branch(k)
    if team_mode(k) == "соло":
        print(f"Режим соло: ветки не нужны, работайте прямо в {base}.")
        return 0
    d = dirty(k)
    if d:
        print("ОШИБКА  есть незакоммиченные файлы — сначала закоммитьте их в текущую ветку "
              f"({current(k)}) или отложите: git stash\n  " + "\n  ".join(d[:15]))
        return 1
    remote = has_remote(k)
    if remote:
        fetch(k)
    if _exists(k, base):
        r = _git(k, "checkout", "--quiet", base)
    elif remote and _exists(k, f"origin/{base}"):
        r = _git(k, "checkout", "--quiet", "-b", base, f"origin/{base}")
    else:
        print(f"ОШИБКА  нет ветки {base}. Её создаёт техлид один раз: "
              f"git checkout -b {base} && git push -u origin {base}. Другое имя — поле «основная ветка» в kb/team.md")
        return 1
    if r.returncode != 0:
        print("ОШИБКА  " + (r.stderr or r.stdout).strip())
        return 1
    if remote and _exists(k, f"origin/{base}"):
        r = _git(k, "merge", "--ff-only", "--quiet", f"origin/{base}")
        if r.returncode != 0:
            print(f"ОШИБКА  локальная {base} разошлась с сервером. В {base} руками не коммитят: "
                  f"покажите человеку git log origin/{base}..{base} и спросите, что с этими коммитами делать")
            return 1
    ident = args[0]
    word = k.slugify(" ".join(args[1:]))[:30].strip("-")
    if re.fullmatch(r"(?i)(pm|sys)-\d{2}(-k\d{2})?|[qadr]-(\d{3}|new-[\w-]+)", ident):
        tail = ident.upper() if not ident.lower().startswith(("q-new", "a-new", "d-new", "r-new")) else ident
        tail = tail + (f"-{word}" if word else "")
    else:
        tail = k.slugify(" ".join(args))[:40].strip("-") or "work"
    name = f"{login(k, k.git_user())}/{tail}"
    n, cand = 2, name
    while _exists(k, cand) or _exists(k, f"origin/{cand}"):
        cand, n = f"{name}-{n}", n + 1
    r = _git(k, "checkout", "--quiet", "-b", cand)
    if r.returncode != 0:
        print("ОШИБКА  " + (r.stderr or r.stdout).strip())
        return 1
    print(f"Ветка {cand} от свежей {base}. Работайте и коммитьте в неё; "
          f"в конце — python3 kb/tools/kb.py mr")
    return 0


# ---------- сводка изменений ----------

def _show(k, rev: str, path: str) -> str:
    return _out(k, "show", f"{rev}:{path}") if rev else ""


def _added_by_section(k, a: str, b: str, path: str, text_b: str) -> tuple[int, list[str]]:
    """Сколько фактов добавлено и в какие разделы (по заголовкам ## в новой версии)."""
    lines = text_b.split("\n")
    heads = {}
    cur = ""
    for i, l in enumerate(lines, 1):
        if l.startswith("## "):
            cur = l[3:].strip()
        heads[i] = cur
    n, secs = 0, []
    diff = _out(k, "diff", "-U0", "--no-color", a, b, "--", path) if a else ""
    new_no = 0
    for l in diff.splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", l)
        if m:
            new_no = int(m.group(1))
            continue
        if l.startswith("+") and not l.startswith("+++"):
            if FACT_RE.search(l):
                n += 1
                s = heads.get(new_no, "")
                if s and s not in secs:
                    secs.append(s)
            new_no += 1
    if not a:
        n = sum(1 for l in lines if FACT_RE.search(l))
    return n, secs


def _title(k, text: str) -> str:
    fm = k.parse_frontmatter(text)
    t = fm.get("шаг") or fm.get("система") or fm.get("заголовок") or k.first_heading(text) or ""
    return re.sub(r"^[QADR]-(\d{3}|new-[\w-]+)\s*", "", str(t)).strip()


def summary(k, a: str, b: str = "HEAD") -> list[str]:
    """Строки сводки «что влито» между ревизиями a..b."""
    renames = {old: v[0] for old, v in k.known_renames().items()} if hasattr(k, "known_renames") else {}
    st = _out(k, "diff", "--name-status", "-M", a, b, "--", "kb").splitlines()
    steps, cards, jour, other = {}, [], {v: [] for v in JOURNAL.values()}, []
    counts = {"datasets": 0, "materials": 0, "meetings": [], "specs": 0}
    for line in st:
        parts = line.split("\t")
        code, path = parts[0], parts[-1]
        old = parts[1] if code.startswith("R") else path
        if path in GENERATED or "/_template" in path or path.endswith("README.md"):
            continue
        pb = _show(k, a, old) if not code.startswith("A") else ""
        nb = _show(k, b, path) if not code.startswith("D") else ""
        fa, fb = k.parse_frontmatter(pb) if pb else {}, k.parse_frontmatter(nb) if nb else {}
        if re.match(r"kb/context/process/cards/PM-\d{2}-K\d{2}", path):
            ident = fb.get("id") or Path(path).stem[:9]
            if code.startswith("A"):
                cards.append(f"{ident} «{_title(k, nb)}» — новая карточка")
            elif fa.get("статус") != fb.get("статус"):
                cards.append(f"{ident}: {fa.get('статус')} → {fb.get('статус')}")
            else:
                cards.append(f"{ident}: находки")
        elif re.match(r"kb/context/(process|systems)/(PM|SYS)-\d{2}", path):
            ident = fb.get("id") or fa.get("id") or Path(path).stem[:6]
            n, secs = _added_by_section(k, a if not code.startswith("A") else "", b, path, nb)
            bits = []
            if code.startswith("A"):
                bits.append("новый шаг" if ident.startswith("PM") else "новая система")
            if n:
                bits.append(f"+{n} факт(ов)" + (f" ({', '.join(secs[:4])})" if secs else ""))
            for f in ("владелец рута", "напарник", "владелец раздела в пуле"):
                if fb.get(f) and fb.get(f) != fa.get(f) and fb.get(f) not in ("не назначен", "—"):
                    bits.append(f"{f}: {fb.get(f)}")
            if fa.get("статус") and fb.get("статус") and fa.get("статус") != fb.get("статус"):
                bits.append(f"статус {fa.get('статус')} → {fb.get('статус')}")
            steps[ident] = (f"{ident} «{_title(k, nb)}»: " + ("; ".join(bits) or "правки"))
        elif m := re.match(r"kb/journal/(questions|assumptions|decisions|risks)/", path):
            group = JOURNAL[m.group(1)]
            ident = str(fb.get("id") or fa.get("id") or "")
            ident = renames.get(ident, ident)
            if code.startswith("A"):
                extra = ", ".join(x for x in (fb.get("тип"), fb.get("раздел")) if x and "<" not in str(x))
                jour[group].append(f"{ident} «{_title(k, nb)}»" + (f" ({extra})" if extra else "") + " — новый")
            elif fa.get("статус") != fb.get("статус"):
                jour[group].append(f"{ident} «{_title(k, nb)}»: {fa.get('статус')} → {fb.get('статус')}")
            else:
                jour[group].append(f"{ident}: дополнен")
        elif path.startswith("kb/materials/meetings/") and code.startswith("A") and not path.endswith(".summary.md") \
                and not Path(path).name.startswith("_"):
            counts["meetings"].append(Path(path).stem)
        elif path.startswith("kb/materials/") and code.startswith("A") and not Path(path).name.startswith("_"):
            counts["materials"] += 1
        elif path.startswith("kb/datasets/") and code.startswith("A") and "/GD-" in path:
            counts["datasets"] += 1
        elif path.startswith("kb/specs/"):
            counts["specs"] += 1
        elif path == "kb/context/process/_map.md":
            other.append("карта процессов (_map.md)")
        elif path in ("kb/product.md", "kb/team.md", "kb/context/glossary.md"):
            other.append(path.removeprefix("kb/"))
        elif path.startswith("kb/core/"):
            other.append(f"ядро: {path.removeprefix('kb/core/')}")
    out = [f"• {v}" for v in steps.values()]
    if cards:
        out.append("• Карточки: " + "; ".join(cards))
    for g, items in jour.items():
        if items:
            out.append(f"• {g}:" + "".join(f"\n   – {i}" for i in items))
    if counts["meetings"]:
        out.append("• Встречи: " + ", ".join(counts["meetings"]))
    if counts["materials"]:
        out.append(f"• Материалы: +{counts['materials']} файл(ов)")
    if counts["datasets"]:
        out.append(f"• Датасеты: +{counts['datasets']} случай(ев)")
    if counts["specs"]:
        out.append(f"• Спеки: изменено файлов — {counts['specs']}")
    if other:
        out.append("• Ещё: " + ", ".join(dict.fromkeys(other)))
    return out


def _ids_in(lines: list[str]) -> list[str]:
    ids = []
    for l in lines:
        for m in re.finditer(r"\b(PM-\d{2}(?:-K\d{2})?|SYS-\d{2})\b", l):
            if m.group(1) not in ids:
                ids.append(m.group(1))
    return ids


def _announce_text(k, lines: list[str], branch: str) -> str:
    base = base_branch(k)
    who = k.git_user()
    head = f"Влил(а) в {base} — {who}, ветка {branch}"
    body = "\n".join(lines) if lines else "• правки инструмента или оформления, знания не менялись"
    return (f"{head}\n{body}\n"
            f"Обновитесь перед работой: git checkout {base} && git pull. "
            "Временные номера Q-new-… CI заменит на постоянные в течение пары минут.")


def _saved(k, branch: str) -> Path:
    return k.ROOT / ".kb" / "sessions" / f"mr-{k.slugify(branch) or 'branch'}.txt"


# ---------- mr ----------

def _host(k) -> str:
    url = _out(k, "remote", "get-url", "origin")
    return "github" if "github.com" in url else ("gitlab" if url else "")


def cmd_mr(k, args: list[str]) -> int:
    base = base_branch(k)
    if team_mode(k) == "соло":
        print(f"Режим соло: MR не нужен — коммиты прямо в {base}, затем python3 kb/tools/kb.py after-merge.")
        return 0
    cur = current(k)
    if cur in (base, "HEAD", ""):
        print(f"ОШИБКА  вы в {base or 'отсоединённом HEAD'}. Работа идёт в своей ветке: "
              "python3 kb/tools/kb.py branch <ID> [слово], перенесите туда коммиты")
        return 1
    problems, notes = [], []
    d = dirty(k)
    if d:
        problems.append("незакоммиченные файлы — закоммитьте свои по именам или отложите:\n    " + "\n    ".join(d[:12]))
    remote = has_remote(k)
    if remote and not fetch(k):
        notes.append("не удалось обновиться с сервера (git fetch) — данные о чужих ветках могут быть старыми")
    bref = base_ref(k)
    behind = int(_out(k, "rev-list", "--count", f"HEAD..{bref}") or 0)
    ahead_log = _out(k, "log", "--format=%h %s", f"{bref}..HEAD").splitlines()
    if not ahead_log:
        print(f"В ветке {cur} нет коммитов, которых нет в {base}: вливать нечего.")
        sv = _saved(k, cur)
        if sv.exists():
            print("Уже влили? Текст для группы: python3 kb/tools/kb.py announce")
        return 0
    if behind:
        problems.append(f"ветка отстаёт от {base} на {behind} коммит(ов). Обновите: git pull --rebase origin {base} "
                        "(конфликт — раздел «Конфликты» в kb/docs/git-flow.md), затем снова kb.py mr")
    mine = _out(k, "diff", "--name-only", f"{bref}...HEAD").splitlines()
    gen = [f for f in mine if f in GENERATED]
    if gen:
        problems.append("в ваших коммитах сгенерированные файлы — их коммитит только CI: "
                        + ", ".join(gen) + f". Уберите: git checkout {bref} -- {' '.join(gen)} && git commit -m \"убрал сгенерированные\"")
    r = subprocess.run([sys.executable, str(k.KB / "tools" / "kb.py"), "check"],
                       cwd=k.ROOT, capture_output=True, text=True, encoding="utf-8")
    last = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode != 0 or re.search(r"ошибок [1-9]", last):
        problems.append(f"kb.py check: {last}. Исправьте ошибки и закоммитьте")
    others = other_branches(k, cur) if remote else []
    overlap = [(o, sorted(set(o["files"]) & set(mine) - set(GENERATED))) for o in others]
    overlap = [(o, f) for o, f in overlap if f]
    pushed = remote and _exists(k, f"origin/{cur}") \
        and _out(k, "rev-list", "--count", f"origin/{cur}..HEAD") == "0" \
        and _out(k, "rev-list", "--count", f"HEAD..origin/{cur}") == "0"

    lines = summary(k, _out(k, "merge-base", "HEAD", bref) or bref, "HEAD")
    ids = _ids_in(lines) or [cur.split("/")[-1].upper()[:9]]
    first = ahead_log[-1].split(" ", 1)[-1]
    first = re.sub(r"^(?:(?:PM|SYS)-\d{2}(?:-K\d{2})?[,:]?\s*)+", "", first)
    title = f"{', '.join(ids[:3])}: {first[:70]}"
    body = "\n".join(lines) or "правки без изменений знаний"
    text = _announce_text(k, lines, cur)
    sv = _saved(k, cur)
    sv.parent.mkdir(parents=True, exist_ok=True)
    sv.write_text(text + "\n", encoding="utf-8")
    body_file = sv.with_suffix(".body.md")
    body_file.write_text(body + "\n", encoding="utf-8")

    print(f"== Ветка {cur} → {base}: коммитов {len(ahead_log)}, отстаёт на {behind}")
    for c in ahead_log[:15]:
        print(f"  {c}")
    print("\n== Что войдёт\n" + body)
    print("\n== Другие открытые ветки (ещё не влиты в " + base + ")")
    if not others:
        print("  нет — вы единственный в очереди")
    for o in others:
        f = next((f for oo, f in overlap if oo is o), [])
        print(f"  {o['name']} — {o['who']}, {o['date']}, коммитов {o['ahead']}"
              + (f"; ТЕ ЖЕ ФАЙЛЫ: {', '.join(f[:5])}" if f else ""))
    if overlap:
        notes.append("есть ветки с теми же файлами. Кто первый открыл MR — тот первый вливает; "
                     "второй после этого обновляет ветку (git pull --rebase origin " + base + ") и вливает следом")
    if not pushed and remote:
        flag = " --force-with-lease" if _exists(k, f"origin/{cur}") else ""
        notes.append(f"ветка не отправлена или отличается от сервера: git push -u origin {cur}{flag}")
    print("\n== MR\nЗаголовок: " + title)
    host = _host(k)
    if host == "github":
        print(f"Создать: gh pr create --base {base} --head {cur} --title \"{title}\" --body-file {body_file}"
              "\n  (или кнопка «Compare & pull request» на GitHub, ветка назначения — " + base + ")")
    elif host:
        print(f"Создать вместе с push: git push -u origin {cur} -o merge_request.create -o merge_request.target={base} "
              f"-o merge_request.title=\"{title}\" -o merge_request.remove_source_branch"
              "\n  (или кнопка «Create merge request» в GitLab, ветка назначения — " + base + ")")
    print("Описание MR — текст из блока «Что войдёт».")
    print("\n== Итог")
    for p in problems:
        print(f"ОШИБКА  {p}")
    for n in notes:
        print(f"ВНИМАНИЕ  {n}")
    if problems:
        print("Вливать пока нельзя: исправьте ошибки и запустите kb.py mr снова.")
        return 1
    print(f"Можно открывать MR и вливать в {base}, когда CI зелёный и ревью (если нужно) пройдено.\n"
          "Перед кнопкой «Merge» откройте список MR в " + base + ": если чей-то MR уже одобрен и ждёт слияния — "
          "пропустите его вперёд, затем снова kb.py mr.\n"
          "После слияния: python3 kb/tools/kb.py announce — текст для группы; затем "
          f"git checkout {base} && git pull && git branch -d {cur}")
    return 0


# ---------- announce ----------

def cmd_announce(k, args: list[str]) -> int:
    cur = current(k)
    since = args[args.index("--since") + 1] if "--since" in args and args.index("--since") + 1 < len(args) else ""
    if since:
        lines = summary(k, since, "HEAD")
        print(_announce_text(k, lines, cur))
        return 0
    if has_remote(k):
        fetch(k)
    bref = base_ref(k)
    if cur != base_branch(k) and _out(k, "rev-list", "--count", f"{bref}..HEAD") not in ("", "0"):
        lines = summary(k, _out(k, "merge-base", "HEAD", bref) or bref, "HEAD")
        print(_announce_text(k, lines, cur))
        return 0
    sv = _saved(k, cur)
    if sv.exists():
        print(sv.read_text(encoding="utf-8").strip())
        return 0
    print("Не нашёл, что вы вливали: запустите announce в своей ветке до удаления или укажите начало: "
          "python3 kb/tools/kb.py announce --since <хеш коммита до ваших правок>")
    return 1
