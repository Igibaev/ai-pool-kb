#!/usr/bin/env python3
"""Строит репозиторий-симуляцию недели работы пула и журнал событий для проигрывателя."""
import json, os, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Шаблон: переменная SIM_TEMPLATE или папка project-template рядом с папкой sim.
TEMPLATE = Path(os.environ.get("SIM_TEMPLATE") or HERE.parent / "project-template")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "out"
if not (TEMPLATE / "tools" / "kb.py").exists():
    raise SystemExit(f"Не найден шаблон: {TEMPLATE}. Положите project-template рядом с папкой sim или задайте SIM_TEMPLATE.")
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
REPO = OUT / "repo"

PEOPLE = {
    "Гульнара": "Аналитик",
    "Айгерим": "Менеджер с бизнеса",
    "Мадина": "Разработчик, владелец PM-04",
    "Тимур": "Разработчик, владелец PM-01",
    "Алия": "Разработчик, владелец PM-02",
    "Арман": "Разработчик, владелец PM-06",
    "Бекзат": "Тестировщик, напарник в PM-04",
    "Ерлан": "Архитектор недели",
    "Руслан": "Техлид потока, владелец PM-00",
    "Пул": "Разработчики пула",
    "CI": "Автоматика репозитория",
}
EMAIL = {n: f"user{i}@pool.local" for i, n in enumerate(PEOPLE)}

EVENTS = []
IDS = {}          # временный ID -> постоянный
DATE = {}         # текущая дата для kb.py


def run(*args, env=None, check=True):
    e = os.environ.copy()
    if env:
        e.update(env)
    r = subprocess.run(args, cwd=REPO, capture_output=True, text=True, encoding="utf-8", env=e)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED {args}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def git(*args, who="CI", when=None, check=True):
    env = {"GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": EMAIL[who],
           "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": EMAIL[who]}
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when + " +0500"
    return run("git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args, env=env, check=check)


def kb(*args, who="CI"):
    run("git", "config", "user.name", who)
    r = subprocess.run([sys.executable, "tools/kb.py", *args], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", env=dict(os.environ, KB_TODAY=DATE["d"]))
    if "Traceback" in r.stderr:
        raise SystemExit(f"kb.py {' '.join(args)} упал:\n{r.stderr}")
    return r.stdout


def p(rel):
    return REPO / rel


def write(rel, text):
    p(rel).parent.mkdir(parents=True, exist_ok=True)
    p(rel).write_text(text.lstrip("\n"), encoding="utf-8")


def read(rel):
    return p(rel).read_text(encoding="utf-8")


def replace(rel, old, new, count=1):
    t = read(rel)
    if old not in t:
        raise SystemExit(f"Нет фрагмента в {rel}: {old[:60]!r}")
    write(rel, t.replace(old, new, count))


def fm_set(rel, key, value):
    t = read(rel)
    t2, n = re.subn(rf"^{re.escape(key)}:.*$", f"{key}: {value}", t, count=1, flags=re.M)
    if not n:
        raise SystemExit(f"Нет поля {key} в {rel}")
    write(rel, t2)


def section(rel, heading, body):
    """Заменяет тело раздела `## heading` до следующего `## `."""
    t = read(rel)
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", t, flags=re.M | re.S)
    if not m:
        raise SystemExit(f"Нет раздела {heading} в {rel}")
    write(rel, t[:m.start(1)] + body.strip("\n") + "\n\n" + t[m.end(1):])


def add(rel, heading, lines):
    """Дописывает строки в раздел, убирая строки-заглушки шаблона."""
    t = read(rel)
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", t, flags=re.M | re.S)
    if not m:
        raise SystemExit(f"Нет раздела {heading} в {rel}")
    body = [l for l in m.group(1).rstrip("\n").split("\n")
            if l.strip() and not re.match(r"^\s*(\d+\.|-)\s*<", l)]
    body += lines.strip("\n").split("\n")
    write(rel, t[:m.start(1)] + "\n".join(body) + "\n\n" + t[m.end(1):])


def sub_ids(text):
    for k, v in IDS.items():
        text = re.sub(rf"(?<![\w-]){re.escape(k)}(?![\w-])", v, text)
    return text


def head():
    return git("rev-parse", "HEAD").strip()


def commit(who, when, msg):
    git("add", "-A")
    git("commit", "-q", "-m", msg, who=who, when=when)
    return head()


def new(kind, slug, who, fields, title, body=None):
    path = kb("new", kind, slug, who=who).strip().split()[0]
    for k, v in fields.items():
        fm_set(path, k, v)
    t = read(path)
    t = re.sub(r"^(# [QAD]-new-\S+) <[^>]*>$", lambda m: f"{m.group(1)} {title}", t, count=1, flags=re.M)
    write(path, t)
    for h, b in (body or {}).items():
        section(path, h, b)
    return path


def ci(when):
    """CI после слияния в main: номера и сводки."""
    out = kb("assign-ids")
    for line in out.splitlines():
        m = re.match(r"^(\S+) → (\S+)", line)
        if m:
            IDS[m.group(1)] = m.group(2)
    kb("index")
    status = git("status", "--porcelain")
    if status.strip():
        return commit("CI", when, "kb: номера и сводки [skip ci]"), out
    return None, out


def checkout(branch, create=False):
    git("checkout", "-q", *(["-b"] if create else []), branch)


def merge(branch, who, when, msg):
    checkout("main")
    git("merge", "-q", "--no-ff", branch, "-m", msg, who=who, when=when)
    return head()


def event(day, time, who, skill, title, story, agent, commits, branch="main", note=None, outputs=None):
    chk = kb("check").strip().splitlines()
    if not chk[-1].startswith("Итог: ошибок 0"):
        raise SystemExit(f"Проверка упала после «{title}»:\n" + "\n".join(chk))
    snap = {rel: run("git", "show", f"main:{rel}", check=False) for rel in
            ["journal/_index.md", "context/process/_index.md"]}
    EVENTS.append(dict(day=day, date=DATE["d"], time=time, who=who, role=PEOPLE[who], skill=skill,
                       title=title, story=sub_ids(story), agent=[sub_ids(a) for a in agent],
                       commits=[c for c in commits if c], branch=branch, note=sub_ids(note) if note else None,
                       outputs=[(sub_ids(t), o) for t, o in (outputs or [])], check=chk[-1], snapshot=snap))


exec(open(Path(__file__).with_name("story.py"), encoding="utf-8").read())
exec(open(Path(__file__).with_name("dialogs.py"), encoding="utf-8").read())
for ev in EVENTS:
    if ev["title"] not in DIALOGS:
        raise SystemExit(f"Нет диалога: {ev['title']}")
    ev["dialog"] = [list(t) for t in DIALOGS[ev["title"]]]
extra = set(DIALOGS) - {ev["title"] for ev in EVENTS}
if extra:
    raise SystemExit(f"Лишние диалоги: {extra}")
exec(open(Path(__file__).with_name("phrases.py"), encoding="utf-8").read())
for ev in EVENTS:
    if ev["who"] != "CI" and ev["title"] not in ALT:
        raise SystemExit(f"Нет примеров фраз: {ev['title']}")
    ev["alt"] = [list(a) for a in ALT.get(ev["title"], [])]
extra = set(ALT) - {ev["title"] for ev in EVENTS}
if extra:
    raise SystemExit(f"Лишние примеры фраз: {extra}")

# ---------- экспорт ----------
def show(c):
    parents = git("rev-list", "--parents", "-n1", c).split()[1:]
    base = parents[0] if parents else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    stat = git("diff", "--name-status", "-M", base, c).strip().splitlines()
    patch = git("diff", "-M", "--unified=2", base, c, "--", ".", ":(exclude)journal/_index.md",
                ":(exclude)context/process/_index.md",
                ":(exclude)context/process/_map.svg", ":(exclude)context/process/_map.html")
    meta = git("show", "-s", "--format=%an|%ad|%s", "--date=format:%Y-%m-%d %H:%M", c).strip().split("|", 2)
    lines = patch.splitlines()
    if len(lines) > 260:
        lines = lines[:260] + ["… (дальше обрезано)"]
    return dict(merge=len(parents) > 1, hash=c[:7], author=meta[0], date=meta[1], msg=meta[2], files=stat, patch="\n".join(lines))

for ev in EVENTS:
    ev["commits"] = [show(c) for c in ev["commits"]]

final = {}
for rel in ["context/process/_index.md", "journal/_index.md"]:
    final[rel] = read(rel)
SHOW_FILES = ["product.md", "context/process/_map.md", "context/process/_index.md",
              "context/process/PM-04-sopostavlenie-oplat.md", "context/process/PM-01-registraciya-nadpisey.md",
              "context/process/PM-00-karkas.md", "context/systems/SYS-01-1c-mfo.md", "context/glossary.md",
              "journal/_index.md", "journal/questions/Q-002-istochnik-dolga.md", "journal/questions/Q-004-summa-dlya-paketa.md",
              "journal/questions/Q-005-pravilo-bez-nomera.md", "journal/assumptions/A-001-ostatok-iz-mfo.md",
              "journal/decisions/D-001-master-id.md", "journal/digest/2026-10-13.md", "journal/digest/2026-10-16.md",
              "datasets/PM-04/GD-PM-04-001.md", "materials/answers/2026-10-13-vzyskanie.md",
              "materials/interviews/2026-10-14-sverka-pm04.md"]
files = {rel: read(rel) for rel in SHOW_FILES}
tree = run("git", "ls-files").splitlines()
log = git("log", "--graph", "--format=%h %an: %s", "--all").strip()
(OUT / "events.json").write_text(json.dumps(dict(events=EVENTS, people=PEOPLE, final=final, files=files, tree=tree, log=log),
                                            ensure_ascii=False, indent=1), encoding="utf-8")
print(f"Событий: {len(EVENTS)}")

# ---------- страница-проигрыватель ----------
import html as _html
d = json.loads((OUT / "events.json").read_text(encoding="utf-8"))
for k in ("tree", "log", "final"):
    d.pop(k, None)
d["catalog"] = [[k, w, [list(x) for x in ex]] for k, (w, ex) in CATALOG.items()]
mm = re.search(r"```mermaid\n(.*?)```", d["files"]["context/process/_map.md"], re.S).group(1)
page = (HERE / "player.tpl.html").read_text(encoding="utf-8")
page = page.replace("/*DATA*/null", json.dumps(d, ensure_ascii=False).replace("</", "<\\/"))
page = page.replace("<!--MERMAID-->", _html.escape(mm.strip(), quote=False))
mapsvg = (REPO / "context/process/_map.svg").read_text(encoding="utf-8") if (REPO / "context/process/_map.svg").exists() else ""
mapsvg = re.sub(r"<style>.*?</style>", "", mapsvg, flags=re.S)
sys.path.insert(0, str(REPO / "tools"))
import kb_map as _km
def _scope(css, prefix):
    out = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        sels = ", ".join(f"{prefix} {x.strip()}" for x in sel.split(",") if x.strip())
        out.append(f"{sels}{{{body}}}")
    return "\n".join(out)
page = page.replace("<!--MAPSVG-->", mapsvg).replace("/*MAPCSS*/", _scope(_km.LIGHT, ".mapsvg") +
    "\n@media (prefers-color-scheme: dark){" + _scope(_km.DARK, ":root:not([data-theme=light]) .mapsvg") + "}\n" +
    _scope(_km.DARK, ":root[data-theme=dark] .mapsvg"))
(OUT / "nedelya-v-repozitorii.html").write_text(page, encoding="utf-8")
print(f"Репозиторий: {REPO}")
print(f"Страница:    {OUT / 'nedelya-v-repozitorii.html'}")
