"""База знаний живёт в одной ветке — context. Агент сам ветки не переключает.
Отдельная ветка — только по просьбе человека: kb.py branch <имя>.
Отправка — только через агента и только с согласия человека: kb.py push.

kb.py branch <имя>         отдельная ветка для разработки контекста — только когда человек попросил
kb.py push                 в context: check, свежие правки коллег в ваших файлах, обновление (rebase),
                           конфликты, отправка, текст для группы. В отдельной ветке — то же, но в эту ветку
kb.py push --into context  влить отдельную ветку в context (по просьбе человека)
kb.py push --yes           то же, когда человек посмотрел свежие правки коллег и согласен
kb.py push --continue      после того как человек решил конфликт, который агент сам не решает
kb.py push --abort         отменить обновление, вернуть всё как было до push
kb.py announce [--since]   текст для группы: что вы отправили в базу знаний

Агент решает сам:
  - чужие правки в других местах ваших файлов — git сливает сам, агент только показывает;
  - обе стороны дописали строки в одно место (подписчики, история, факты) — остаются обе;
  - сгенерированные файлы (_index.md, карта, _renames.tsv) — берётся версия с сервера.
Агент показывает человеку и ждёт решения:
  - одна и та же строка или поле шапки изменены по-разному.
Конфликт в «владелец рута», «напарник», «взял» — рут или карточку уже взяли: взятие отменяется.

Основная ветка — поле «основная ветка» в шапке kb/team.md; нет поля — context, если она есть.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

GENERATED = ("kb/context/process/_index.md", "kb/journal/_index.md", "kb/context/process/_map.svg",
             "kb/context/process/_map.html", "kb/journal/_renames.tsv", "kb/materials/meetings/_index.md")
LOG = "kb/journal/context-log.md"
LOG_HEAD = ("# Журнал изменений контекста\n\n"
            "Пишет только `kb.py push`: запись на каждую отправку — кто, куда, что изменилось, что дальше. "
            "Руками не правят. Новая сессия — `python3 kb/tools/kb.py resume` или `kb.py log`.\n")
OWNER_FIELDS = ("владелец рута", "напарник", "взял", "владелец раздела в пуле")
CI_SUBJECT = "kb: номера и сводки"
FACT_RE = re.compile(r"\((?:[^()]|\([^()]*\))+,\s*(подтверждено|оценка|допущение|противоречие)")
JOURNAL = {"questions": "Вопросы", "assumptions": "Допущения", "decisions": "Решения", "risks": "Риски"}
_T = ["context"]   # куда идёт текущая отправка — для сообщений


def T() -> str:
    return _T[0]


ENV = dict(os.environ, GIT_EDITOR="true", GIT_SEQUENCE_EDITOR="true", GIT_TERMINAL_PROMPT="0")


# ---------- git ----------

def _git(k, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-c", "merge.conflictStyle=diff3", *args],
                          cwd=k.ROOT, capture_output=True, text=True, encoding="utf-8", env=ENV)


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
    for b in ("context", "develop", "main", "master"):
        if _exists(k, b) or _exists(k, f"origin/{b}"):
            return b
    return "context"


def fetch(k) -> bool:
    if not has_remote(k):
        return False
    try:
        r = subprocess.run(["git", "fetch", "--quiet", "origin"], cwd=k.ROOT, capture_output=True,
                           text=True, encoding="utf-8", timeout=90, env=ENV)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def dirty(k) -> list[str]:
    return [l for l in _out(k, "status", "--porcelain").splitlines()
            if l.strip() and not l[3:].startswith((".kb/", "kb-report-"))]


def off_base(k) -> str:
    """Команда с сервером: вы не в основной ветке — имя текущей ветки, иначе пусто."""
    cur = current(k)
    return cur if team_mode(k) == "команда" and has_remote(k) and cur and cur != base_branch(k) else ""


def _rebasing(k) -> bool:
    g = Path(_out(k, "rev-parse", "--git-dir") or ".git")
    g = g if g.is_absolute() else k.ROOT / g
    return (g / "rebase-merge").exists() or (g / "rebase-apply").exists()


def _check(k) -> str:
    """Пусто — check без ошибок; иначе его последняя строка."""
    r = subprocess.run([sys.executable, str(k.KB / "tools" / "kb.py"), "check"], cwd=k.ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    last = (r.stdout.strip().splitlines() or [""])[-1]
    return last if r.returncode != 0 or re.search(r"ошибок [1-9]", last) else ""


# ---------- сводка изменений ----------

def _show(k, rev: str, path: str) -> str:
    return _out(k, "show", f"{rev}:{path}") if rev else ""


def _added_by_section(k, a: str, b: str, path: str, text_b: str) -> tuple[int, list[str]]:
    lines = text_b.split("\n")
    heads, cur = {}, ""
    for i, l in enumerate(lines, 1):
        if l.startswith("## "):
            cur = l[3:].strip()
        heads[i] = cur
    if not a:
        return sum(1 for l in lines if FACT_RE.search(l)), []
    n, secs, new_no = 0, [], 0
    for l in _out(k, "diff", "-U0", "--no-color", a, b, "--", path).splitlines():
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
    return n, secs


def _title(k, text: str) -> str:
    fm = k.parse_frontmatter(text)
    t = fm.get("шаг") or fm.get("система") or fm.get("тема") or fm.get("заголовок") or k.first_heading(text) or ""
    return re.sub(r"^([QADR]-(\d{3}|new-[\w-]+)|PM-\d{2}-K\d{2})\s*[·:—-]?\s*", "", str(t)).strip()


def summary(k, a: str, b: str = "HEAD") -> list[str]:
    """Строки сводки «что отправлено» между ревизиями a..b."""
    renames = {old: v[0] for old, v in k.known_renames().items()}
    st = _out(k, "diff", "--name-status", "-M", a, b, "--", "kb").splitlines()
    steps, cards, new_cards, ready, other = {}, [], [], [], []
    jour = {v: [] for v in JOURNAL.values()}
    counts = {"datasets": 0, "materials": 0, "meetings": [], "specs": 0}
    for line in st:
        parts = line.split("\t")
        code, path = parts[0], parts[-1]
        old = parts[1] if code.startswith("R") else path
        if path in GENERATED or path == LOG or "/_template" in path or path.endswith("README.md"):
            continue
        pb = _show(k, a, old) if not code.startswith("A") else ""
        nb = _show(k, b, path) if not code.startswith("D") else ""
        fa = k.parse_frontmatter(pb) if pb else {}
        fb = k.parse_frontmatter(nb) if nb else {}
        if re.match(r"kb/context/process/cards/PM-\d{2}-K\d{2}", path):
            ident = fb.get("id") or Path(path).stem[:9]
            if code.startswith("A"):
                new_cards.append(f"{ident} «{_title(k, nb)}»")
            elif fa.get("статус") != fb.get("статус"):
                who = f" ({fb.get('взял')})" if fb.get("статус") == "В работе" and fb.get("взял") else ""
                cards.append(f"{ident}: {fa.get('статус')} → {fb.get('статус')}{who}")
            else:
                cards.append(f"{ident}: находки")
        elif re.match(r"kb/context/(process|systems)/(PM|SYS)-\d{2}", path):
            ident = fb.get("id") or fa.get("id") or Path(path).stem[:6]
            n, secs = _added_by_section(k, "" if code.startswith("A") else a, b, path, nb)
            bits = []
            if code.startswith("A"):
                bits.append("новый шаг" if str(ident).startswith("PM") else "новая система")
            if n:
                bits.append(f"+{n} факт(ов)" + (f" ({', '.join(secs[:4])})" if secs else ""))
            for f in ("владелец рута", "напарник", "владелец раздела в пуле"):
                if fb.get(f) and fb.get(f) != fa.get(f) and fb.get(f) not in ("не назначен", "—"):
                    bits.append(f"{f}: {fb.get(f)}")
            if fa.get("статус") and fb.get("статус") and fa.get("статус") != fb.get("статус"):
                bits.append(f"статус {fa.get('статус')} → {fb.get('статус')}")
                if fb.get("статус") == "Готов к взятию":
                    ready.append(f"{ident} «{_title(k, nb)}»")
            steps[ident] = f"{ident} «{_title(k, nb)}»: " + ("; ".join(bits) or "правки")
        elif m := re.match(r"kb/journal/(questions|assumptions|decisions|risks)/", path):
            group = JOURNAL[m.group(1)]
            ident = str(fb.get("id") or fa.get("id") or "")
            ident = renames.get(ident, ident)
            if code.startswith("A"):
                extra = ", ".join(str(x) for x in (fb.get("тип"), fb.get("раздел")) if x and "<" not in str(x))
                jour[group].append(f"{ident} «{_title(k, nb)}»" + (f" ({extra})" if extra else "") + " — новый")
            elif fa.get("статус") != fb.get("статус"):
                jour[group].append(f"{ident} «{_title(k, nb)}»: {fa.get('статус')} → {fb.get('статус')}")
            else:
                jour[group].append(f"{ident}: дополнен")
        elif path.startswith("kb/materials/meetings/") and code.startswith("A") \
                and not path.endswith(".summary.md") and not Path(path).name.startswith("_"):
            counts["meetings"].append(Path(path).stem)
        elif path.startswith("kb/materials/") and code.startswith("A") and not Path(path).name.startswith("_"):
            counts["materials"] += 1
        elif path.startswith("kb/datasets/") and code.startswith("A") and "/GD-" in path:
            counts["datasets"] += 1
        elif path.startswith("kb/specs/"):
            counts["specs"] += 1
        elif path == "kb/context/process/_map.md":
            other.append("карта процессов (_map.md)")
        elif path in ("kb/product.md", "kb/team.md", "kb/context/glossary.md", "kb/FEEDBACK.md"):
            other.append(path.removeprefix("kb/"))
        elif path.startswith("kb/core/"):
            other.append(f"ядро: {path.removeprefix('kb/core/')}")
    out = [f"• {v}" for v in steps.values()]
    if new_cards:
        out.append("• НОВЫЕ КАРТОЧКИ — можно брать:" + "".join(f"\n   – {c}" for c in new_cards)
                   + "\n   Взять: /kb-take <ID карточки>; весь список — python3 kb/tools/kb.py cards")
    if ready:
        out.append("• РУТЫ ГОТОВЫ К ВЗЯТИЮ: " + ", ".join(ready) + ". Взять: /kb-take")
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


def _announce_text(k, lines: list[str], target: str = "") -> str:
    target = target or base_branch(k)
    body = "\n".join(lines) if lines else "• правки оформления, знания не менялись"
    return (f"Отправил(а) в {target} — {k.git_user()}\n{body}\n"
            f"Перед работой обновитесь: попросите агента «обнови {T()}» (git pull --rebase).")


def _saved(k) -> Path:
    return k.ROOT / ".kb" / "sessions" / f"push-{k.slugify(k.git_user()) or 'user'}.txt"


# ---------- конфликты ----------

HUNK_RE = re.compile(r"^<<<<<<< [^\n]*\n(.*?)^\|\|\|\|\|\|\| [^\n]*\n(.*?)^=======\n(.*?)^>>>>>>> [^\n]*(?:\n|\Z)",
                     re.S | re.M)


def _fm_end(text: str) -> int:
    """Позиция конца шапки (---…---) или 0."""
    if not text.startswith("---\n"):
        return 0
    m = re.search(r"^---[ \t]*$", text[4:], re.M)
    return 4 + m.end() if m else 0


def _val(lines: str, field: str) -> str:
    m = re.search(rf"^{re.escape(field)}:[ \t]*([^#\n]*)", lines, re.M)
    return m.group(1).strip() if m else ""


def resolve_file(k, path: str) -> dict:
    """Разрулить конфликт в файле. В rebase верхняя часть — то, что уже на сервере; нижняя — ваш коммит.
    Возвращает {"state": auto|manual|taken, "hunks": [...], "who": …}."""
    full = k.ROOT / path
    if path in GENERATED:
        _git(k, "checkout", "--ours", "--", path)
        _git(k, "add", "--", path)
        return {"state": "auto", "hunks": [f"сгенерированный файл — взята версия {T()}, CI пересоберёт"]}
    if not full.exists():
        return {"state": "manual", "hunks": ["файл удалён с одной стороны и изменён с другой — решите, нужен ли он"]}
    raw = full.read_bytes().decode("utf-8", errors="replace")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    if "<<<<<<< " not in text:
        return {"state": "manual", "hunks": ["конфликт без отметок (переименование или удаление) — смотрите git status"]}
    fm_end = _fm_end(text)
    manual, auto, who = [], [], ""

    def fix(m: re.Match) -> str:
        nonlocal who
        dev, base, mine = m.group(1), m.group(2), m.group(3)
        if m.start() < fm_end:
            for f in OWNER_FIELDS:
                dv, mv = _val(dev, f), _val(mine, f)
                if dv and mv and dv != mv and dv not in k.UNASSIGNED and dv != "—":
                    who = f"{f}: {dv}"
            manual.append(("шапка", dev, base, mine))
            return m.group(0)
        if dev == mine:
            auto.append("одинаковая правка с двух сторон")
            return mine
        # пусто или только заглушки шаблона «<кто>», «<дата>» — обе стороны дописали строки
        if all(not l.strip() or k.has_placeholder(l) for l in base.splitlines()):
            dev_lines = dev.splitlines(keepends=True)
            add = [l for l in mine.splitlines(keepends=True) if l not in dev_lines]
            merged = "".join(dev_lines) + "".join(add)
            if merged and not merged.endswith("\n"):
                merged += "\n"
            auto.append(f"обе стороны дописали строки в одно место — оставлены обе ({len(dev_lines)} из {T()}, {len(add)} ваших)")
            return merged
        manual.append(("текст", dev, base, mine))
        return m.group(0)

    new = HUNK_RE.sub(fix, text)
    if who:
        return {"state": "taken", "who": who, "hunks": []}
    full.write_bytes((new.replace("\n", "\r\n") if crlf else new).encode("utf-8"))
    if manual:
        return {"state": "manual", "hunks": auto + [
            f"{where}: одна и та же строка изменена по-разному\n"
            f"      было:       {b.strip()[:300] or '—'}\n"
            f"      в {T()}:  {d.strip()[:300] or '—'}\n"
            f"      у вас:      {mn.strip()[:300] or '—'}" for where, d, b, mn in manual]}
    _git(k, "add", "--", path)
    return {"state": "auto", "hunks": auto}


def _conflicted(k) -> list[str]:
    return [f for f in _out(k, "diff", "--name-only", "--diff-filter=U").splitlines() if f]


def _drive_rebase(k) -> int:
    """Идти по rebase, разруливая конфликты. 0 — готово, 4 — нужен человек, 5 — рут уже взят."""
    for _ in range(200):
        if not _rebasing(k):
            return 0
        need, taken = [], []
        for f in _conflicted(k):
            r = resolve_file(k, f)
            for h in r["hunks"]:
                print(f"  {f}: {h}")
            if r["state"] == "taken":
                taken.append((f, r["who"]))
            elif r["state"] == "manual":
                need.append(f)
        if taken:
            _git(k, "rebase", "--abort")
            for f, who in taken:
                print(f"ВЗЯТО ДРУГИМ  {f} — в {T()} уже {who}. Ваше взятие не отправлено.")
            return 5
        if need:
            print("\nНУЖЕН ЧЕЛОВЕК  агент не выбирает сам между двумя версиями одной строки.\n"
                  f"Покажи человеку «было / в {T()} / у вас» по каждому месту выше и спроси, что оставить: "
                  f"версию {T()}, свою, обе или новую формулировку. Исправь файл — убери отметки "
                  "<<<<<<< ||||||| ======= >>>>>>> — покажи человеку итог и спроси «Отправить?»; после «да»: python3 kb/tools/kb.py push --continue. "
                  "Передумали — push --abort.\nФайлы: " + ", ".join(need))
            return 4
        r = _git(k, "rebase", "--continue")
        if r.returncode != 0 and _rebasing(k) and not _conflicted(k):
            r = _git(k, "rebase", "--skip")   # коммит стал пустым: ваша правка уже есть на сервере
            if r.returncode != 0 and not _conflicted(k):
                print("ОШИБКА  обновление не продолжилось: " + (r.stderr or r.stdout).strip()[:400])
                return 4
    return 4


def _incoming(k, bref: str) -> tuple[list[str], dict[str, list[str]]]:
    """Свежие коммиты на сервере, которых у вас нет, и какие из них трогают ваши файлы (без коммитов CI)."""
    mine = set(_out(k, "diff", "--name-only", f"{bref}...HEAD").splitlines()) - set(GENERATED) - {LOG}
    commits = _out(k, "log", "--format=%h\t%an\t%ad\t%s", "--date=format:%d.%m %H:%M", f"HEAD..{bref}").splitlines()
    touched: dict[str, list[str]] = {}
    for c in commits:
        h, who, when, subj = (c.split("\t") + ["", "", ""])[:4]
        if subj.startswith(CI_SUBJECT):
            continue
        for f in _out(k, "diff-tree", "--no-commit-id", "--name-only", "-r", h).splitlines():
            if f in mine:
                touched.setdefault(f, []).append(f"{h} {who} {when}: {subj}")
    return commits, touched


def _owner_clash(k, bref: str) -> list[str]:
    """Вы и коллега записали разных людей в «владелец рута», «напарник», «взял» одного файла."""
    mb = _out(k, "merge-base", "HEAD", bref)
    out = []
    for f in _out(k, "diff", "--name-only", f"{mb}..HEAD").splitlines():
        if not f.endswith(".md"):
            continue
        b, me, dev = (k.parse_frontmatter(_show(k, r, f)) for r in (mb, "HEAD", bref))
        for fld in OWNER_FIELDS:
            mv, dv, bv = str(me.get(fld, "")), str(dev.get(fld, "")), str(b.get(fld, ""))
            if mv != bv and dv != bv and dv != mv and dv not in k.UNASSIGNED and dv != "—":
                out.append(f"{f} — в {T()} уже {fld}: {dv}")
    return out


def _preview_conflicts(k, bref: str) -> list[str]:
    """Файлы, где будет конфликт, — без изменения рабочей папки (git 2.38+)."""
    r = _git(k, "merge-tree", "--write-tree", "--name-only", "--no-messages", bref, "HEAD")
    return [l for l in r.stdout.splitlines()[1:] if l.strip()] if r.returncode == 1 else []


# ---------- push ----------

def _target_file(k) -> Path:
    g = Path(_out(k, "rev-parse", "--git-dir") or ".git")
    return (g if g.is_absolute() else k.ROOT / g) / "kb-push-target"


def cmd_push(k, args: list[str]) -> int:
    base = base_branch(k)
    if not has_remote(k):
        print("Сервера (remote) нет — отправлять некуда. Соло без сервера: коммиты остаются у вас, "
              "затем python3 kb/tools/kb.py after-merge.")
        return 0
    tf = _target_file(k)
    if "--abort" in args:
        if _rebasing(k):
            _git(k, "rebase", "--abort")
            print("Обновление отменено, ваши коммиты как до push. Ничего не отправлено.")
        else:
            print("Отменять нечего: обновление не идёт.")
        return 0
    if "--continue" in args:
        if not _rebasing(k):
            print("Продолжать нечего: обновление не идёт. Отправить: python3 kb/tools/kb.py push")
            return 1
        target = tf.read_text(encoding="utf-8").strip() if tf.exists() else base
        _T[0] = target
        conf = _conflicted(k)
        left = [f for f in conf if (k.ROOT / f).exists()
                and "<<<<<<< " in (k.ROOT / f).read_text(encoding="utf-8", errors="replace")]
        if left:
            print("ОШИБКА  в файлах остались отметки конфликта: " + ", ".join(left))
            return 4
        for f in conf:
            _git(k, "add", "--", f)
        rc = _drive_rebase(k)
        if rc == 5:
            _undo_take(k, f"origin/{target}")
        return rc or _finish(k, target)
    if _rebasing(k):
        print("Идёт обновление с конфликтом. Решите его и: python3 kb/tools/kb.py push --continue; "
              "или отмените: push --abort. Файлы: " + ", ".join(_conflicted(k)))
        return 4
    cur = current(k)
    if cur in ("HEAD", ""):
        print("ОШИБКА  вы не в ветке (отсоединённый HEAD). Спросите человека, в какую ветку вернуться")
        return 1
    into = args[args.index("--into") + 1] if "--into" in args and args.index("--into") + 1 < len(args) else ""
    if into:
        if into == cur:
            print(f"ОШИБКА  вы и так в {cur}: отправка — просто python3 kb/tools/kb.py push")
            return 1
        target = into
    else:
        target = cur          # context — в context; отдельная ветка — в неё же
    _T[0] = target
    bref = f"origin/{target}"
    tf.write_text(target, encoding="utf-8")
    if target != base and not into:
        print(f"Отдельная ветка {cur}: отправка уходит в неё, не в {base}. "
              f"Влить в {base} — только по просьбе человека: python3 kb/tools/kb.py push --into {base}")
    d = dirty(k)
    if d:
        print("ОШИБКА  есть незакоммиченные файлы. Закоммитьте свои по именам (git commit … -- <файлы>), "
              "чужие не трогайте:\n  " + "\n  ".join(d[:15]))
        return 1
    bad = _check(k)
    if bad:
        print(f"ОШИБКА  kb.py check: {bad}. В {target} с ошибками не отправляем — исправьте и закоммитьте.")
        return 1
    if not fetch(k):
        print(f"ОШИБКА  не удалось получить свежий {T()} с сервера (git fetch). Проверьте сеть и повторите.")
        return 1
    if not _exists(k, bref):
        if into:
            print(f"ОШИБКА  ветки {target} на сервере нет — вливать некуда")
            return 1
        r = _git(k, "push", "-u", "origin", target)
        print(f"Ветки {target} на сервере не было — создана и отправлена." if r.returncode == 0 else
              "ОШИБКА  " + (r.stderr or r.stdout).strip()[:400])
        return 0 if r.returncode == 0 else 1
    if _out(k, "rev-list", "--count", f"{bref}..HEAD") in ("", "0"):
        if not into:
            _git(k, "merge", "--ff-only", "--quiet", bref)
        print(f"Отправлять нечего: ваших новых коммитов нет относительно {target}."
              + ("" if into else f" {target} обновлена до свежей."))
        return 0
    commits, touched = _incoming(k, bref)
    clash = _owner_clash(k, bref)
    if clash:
        for c in clash:
            print(f"ВЗЯТО ДРУГИМ  {c}. Ваше взятие не отправлено.")
        _undo_take(k, bref)
        return 5
    if touched and "--yes" not in args:
        print(f"== В {target} свежие правки коллег в ваших файлах")
        for f, cs in touched.items():
            print(f"\n# {f}")
            for c in cs[:5]:
                print(f"  {c}")
            diff = _out(k, "diff", "--no-color", "-U1", f"HEAD...{bref}", "--", f).splitlines()
            body = [l for l in diff if l.startswith(("+", "-", "@@")) and not l.startswith(("+++", "---"))]
            for l in body[:25]:
                print("  " + l[:200])
            if len(body) > 25:
                print(f"  … ещё строк: {len(body) - 25}")
        pred = _preview_conflicts(k, bref)
        print("\n== Что будет при обновлении")
        if pred:
            print("Конфликт в файлах: " + ", ".join(pred) + ". Дописанные строки агент сольёт сам; "
                  "одну и ту же строку, изменённую по-разному, покажет вам.")
        else:
            print("Конфликтов нет: git сольёт правки сам.")
        print("\nПОКАЖИ ЧЕЛОВЕКУ эти правки коллег и спроси, отправлять ли. Только после его «да» — python3 kb/tools/kb.py push --yes")
        return 3
    others = [c for c in commits if not c.split("\t")[-1].startswith(CI_SUBJECT)]
    if commits:
        print(f"Обновляюсь: в {target} новых коммитов — {len(commits)}"
              + (f", из них коллег — {len(others)}" if others else ", все от CI"))
    r = _git(k, "rebase", bref)
    if r.returncode != 0 and not _rebasing(k):
        print("ОШИБКА  " + (r.stderr or r.stdout).strip()[:400])
        return 1
    rc = _drive_rebase(k)
    if rc == 5:
        _undo_take(k, bref)
    return rc or _finish(k, target)


def _undo_take(k, bref: str) -> None:
    mine = _out(k, "log", "--format=%h %s", f"{bref}..HEAD").splitlines()
    if len(mine) == 1 and re.search(r"взял|напарник", mine[0]):
        _git(k, "reset", "--hard", bref)
        print(f"Ваш единственный неотправленный коммит — взятие: он отменён, {T()} обновлена. "
              "Возьмите другой рут или карточку: /kb-take")
    else:
        print("Кроме взятия, у вас есть другие неотправленные коммиты. Отмените только взятие: "
              f"git log {bref}..HEAD — найдите коммит «… взял(а) …», git revert <хеш>, затем снова push.")


def _finish(k, target: str) -> int:
    bref = f"origin/{target}"
    cur = current(k)
    p = None
    if not _out(k, "log", "-1", "--format=%s", "HEAD").startswith("kb: журнал контекста"):
        _write_log(k, bref, target, "" if cur == target else f"влита ветка {cur}")
    for attempt in range(3):
        bad = _check(k)
        if bad:
            print(f"ОШИБКА  после обновления kb.py check: {bad}. Исправьте, закоммитьте и снова push.")
            return 1
        start = _out(k, "rev-parse", bref)
        p = _git(k, "push", "origin", f"HEAD:{target}")
        if p.returncode == 0:
            text = _announce_text(k, summary(k, start, "HEAD"), target)
            sv = _saved(k)
            sv.parent.mkdir(parents=True, exist_ok=True)
            sv.write_text(text + "\n", encoding="utf-8")
            fetch(k)
            _target_file(k).unlink(missing_ok=True)
            if cur == target:
                _git(k, "branch", f"--set-upstream-to={bref}")
                print(f"Отправлено в {target}.")
            else:
                print(f"Ветка {cur} влита в {target}. Вы остаётесь в {cur}: сам агент ветки не переключает. "
                      f"Вернуться в {target} — по просьбе человека: git checkout {target} && git pull --rebase. "
                      f"Ветка {cur} больше не нужна — удалить её может человек.")
            print(f"\n== Текст для группы — скопируйте в чат команды\n{text}")
            return 0
        if attempt == 2:
            break
        print("Пока обновлялись, кто-то отправил ещё — обновляюсь снова…")
        fetch(k)
        _git(k, "rebase", bref)
        rc = _drive_rebase(k)
        if rc == 5:
            _undo_take(k, bref)
        if rc:
            return rc
    print("ОШИБКА  не удалось отправить за 3 попытки: " + ((p.stderr or p.stdout).strip()[:300] if p else ""))
    return 1


# ---------- журнал контекста ----------

def _next_step(k) -> str:
    """«Дальше: …» из последней заметки сессии (kb.py note "…; дальше: …")."""
    f = k._session_file(k.git_user())
    if not f.exists():
        return ""
    lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return ""
    m = re.search(r"дальше:\s*(.+)$", lines[-1], re.I)
    return m.group(1).strip() if m else ""


def _write_log(k, bref: str, target: str, note: str = "") -> bool:
    """Запись в журнал контекста отдельным коммитом. False — записывать нечего."""
    lines = summary(k, bref, "HEAD")
    if not lines and not note:
        return False
    import datetime as dt
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = [f"### {now} · {k.git_user()} → {target}"]
    if note:
        entry.append(f"- {note}")
    entry += [l.replace("• ", "- ", 1) for l in lines]
    nxt = _next_step(k)
    entry.append(f"- Дальше: {nxt or '—'}")
    path = k.ROOT / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else LOG_HEAD
    path.write_text(old.rstrip("\n") + "\n\n" + "\n".join(entry) + "\n", encoding="utf-8", newline="\n")
    r = _git(k, "commit", "-q", "-m", f"kb: журнал контекста ({target})", "-m", "Скилл: kb-push", "--", LOG)
    if r.returncode != 0:
        _git(k, "add", "--", LOG)
        _git(k, "commit", "-q", "-m", f"kb: журнал контекста ({target})", "-m", "Скилл: kb-push", "--", LOG)
    return True


def entries(k, rev: str = "") -> list[dict]:
    """Записи журнала: [{"head": …, "who": …, "when": …, "body": […]}], старые сверху."""
    text = _show(k, rev, LOG) if rev else ((k.ROOT / LOG).read_text(encoding="utf-8") if (k.ROOT / LOG).exists() else "")
    out = []
    for block in re.split(r"(?m)^### ", text)[1:]:
        head, *body = block.strip().split("\n")
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (.+?) → (\S+)", head)
        out.append({"head": head.strip(), "when": m.group(1) if m else "", "who": m.group(2) if m else "",
                    "to": m.group(3) if m else "", "body": [b for b in body if b.strip()]})
    return out


def recap(k, user: str, mine_n: int = 3, others_n: int = 10) -> list[str]:
    """Для новой сессии: мои последние записи и что коллеги отправили после моей последней."""
    es = entries(k)
    if not es:
        return ["Журнал контекста пуст: отправок через kb.py push ещё не было."]
    my = [i for i, e in enumerate(es) if e["who"] == user]
    out = []
    if my:
        out.append(f"Ваши последние отправки ({min(mine_n, len(my))} из {len(my)}):")
        for i in my[-mine_n:]:
            out.append("  ### " + es[i]["head"])
            out += ["    " + b for b in es[i]["body"]]
        after = [e for e in es[my[-1] + 1:] if e["who"] != user]
    else:
        out.append("Ваших отправок в журнале нет.")
        after = es[-others_n:]
    out.append(f"Коллеги после вашей последней отправки: {len(after)}" + (":" if after else ""))
    for e in after[-others_n:]:
        out.append("  ### " + e["head"])
        out += ["    " + b for b in e["body"] if not b.startswith("- Дальше:")]
    return out


def cmd_log(k, args: list[str]) -> int:
    n = int(args[0]) if args and args[0].isdigit() else 10
    who = args[args.index("--who") + 1] if "--who" in args and args.index("--who") + 1 < len(args) else ""
    es = [e for e in entries(k) if not who or e["who"] == who]
    if not es:
        print("Записей нет." if (k.ROOT / LOG).exists() else f"Журнала ещё нет: {LOG} появится после первой отправки.")
        return 0
    for e in es[-n:]:
        print("### " + e["head"])
        print("\n".join(e["body"]))
        print()
    return 0


# ---------- отдельная ветка ----------

def cmd_branch(k, args: list[str]) -> int:
    """Отдельная ветка для разработки контекста — только когда человек сам попросил и назвал имя."""
    base = base_branch(k)
    if not args or args[0].startswith("-"):
        print(f"Как: python3 kb/tools/kb.py branch <имя, которое назвал человек>. "
              f"Без просьбы человека ветки не создаём: все работают в {base}.")
        return 2
    name = args[0].strip()
    if _git(k, "check-ref-format", "--branch", name).returncode != 0:
        print(f"ОШИБКА  «{name}» не годится как имя ветки: без пробелов, без «..», «~», «^», «:». Спросите другое имя")
        return 1
    if name == base:
        print(f"{base} — основная ветка, создавать не нужно")
        return 1
    d = dirty(k)
    if d:
        print("ОШИБКА  есть незакоммиченные файлы — сначала закоммитьте их там, где вы сейчас:\n  " + "\n  ".join(d[:15]))
        return 1
    if _exists(k, name) or (has_remote(k) and fetch(k) and _exists(k, f"origin/{name}")):
        print(f"ОШИБКА  ветка {name} уже есть. Переключиться на неё — только если человек этого хочет: git checkout {name}")
        return 1
    start = f"origin/{base}" if _exists(k, f"origin/{base}") else base
    if not _exists(k, start):
        print(f"ОШИБКА  нет ветки {base}, отделяться не от чего")
        return 1
    r = _git(k, "checkout", "--quiet", "-b", name, "--no-track", start)
    if r.returncode != 0:
        print("ОШИБКА  " + (r.stderr or r.stdout).strip()[:300])
        return 1
    print(f"Ветка {name} создана от свежей {base}, вы в ней. Отправка — python3 kb/tools/kb.py push "
          f"(с согласия человека) уходит в {name}. Влить в {base} — по просьбе человека: "
          f"python3 kb/tools/kb.py push --into {base}. Брать руты и карточки — только в {base}.")
    return 0


# ---------- announce ----------

def cmd_announce(k, args: list[str]) -> int:
    since = args[args.index("--since") + 1] if "--since" in args and args.index("--since") + 1 < len(args) else ""
    if since:
        print(_announce_text(k, summary(k, k.resolve_since(since), "HEAD")))
        return 0
    sv = _saved(k)
    if sv.exists():
        print(sv.read_text(encoding="utf-8").strip())
        return 0
    print("Последней отправки не нашёл. Текст за период: python3 kb/tools/kb.py announce --since <хеш или дата ГГГГ-ММ-ДД>")
    return 1
