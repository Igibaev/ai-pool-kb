"""Коммит только по «да»: kb commit plan → человек смотрит → kb commit apply <id>.

plan показывает, что войдёт, и запоминает отпечаток каждого файла в .kb/commit-plans/.
apply коммитит, только если с момента показа ничего не изменилось. «Да» — окно OpenCode
на команде apply (opencode.json, ask). git commit и git push агенту закрыты (deny).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import kbbase
import kbcheck
import kbquestions

FACT_LINE = re.compile(r"^(?:-|\d+\.)\s+.*\(ист:")
GENERATED = ("context/process/_deps.md",)     # строителя пока нет — не трогать
VIEWS = "journal/views/"
JUNK = re.compile(r"(^|/)(__pycache__/|\.DS_Store$)|\.pyc$")


class GitError(Exception):
    pass


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise GitError(r.stderr.strip() or r.stdout.strip())
    return r.stdout


def repo_root(kb: Path) -> Path:
    return Path(git(kb, "rev-parse", "--show-toplevel").strip())


def changed_files(repo: Path, kb: Path) -> list[tuple[str, str]]:
    """(код git, путь от корня репозитория) для изменений внутри kb/, включая новые файлы."""
    prefix = kb.relative_to(repo).as_posix()
    out = git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--", prefix)
    items, parts = [], out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if not entry:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":          # переименование: следующий элемент — старый путь
            i += 1
        if not JUNK.search(path):
            items.append((code.strip() or "?", path))
    return items


def digest(repo: Path, path: str) -> str:
    full = repo / path
    return hashlib.sha256(full.read_bytes()).hexdigest() if full.exists() else "удалён"


def kb_path(repo: Path, kb: Path, path: str) -> str:
    """Путь внутри kb/: materials/…, journal/…"""
    try:
        return (repo / path).resolve().relative_to(kb.resolve()).as_posix()
    except ValueError:
        return path


def forbidden(repo: Path, kb: Path, files: list[tuple[str, str]]) -> list[str]:
    """Что нельзя коммитить никогда: правка материалов, личное из .kb/, ручная правка сгенерированного."""
    problems = []
    views = None
    for code, path in files:
        inner = kb_path(repo, kb, path)
        if path.startswith(".kb/") or "/.kb/" in path:
            problems.append(f"Личные файлы в git не попадают: `{path}`")
        elif inner.startswith("materials/") and code not in ("?", "??", "A"):
            problems.append(f"Материалы не правятся: `{inner}`. Новая версия — новый файл с датой")
        elif inner.startswith(VIEWS):
            views = views if views is not None else kbquestions.render_views(kb)
            name = inner[len(VIEWS):]
            full = repo / path
            if full.exists() and views.get(name) != full.read_text(encoding="utf-8"):
                problems.append(f"Списки строит инструмент: `{inner}`. Руками не правят — пересоберите: kb views")
        elif inner.startswith(GENERATED):
            problems.append(f"Этот файл строит инструмент: `{inner}`. Руками не правят")
    return problems


def head_text(repo: Path, path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{path}"], capture_output=True, text=True,
                       encoding="utf-8")
    return r.stdout if r.returncode == 0 else None


def added_lines(repo: Path, path: str) -> list[int]:
    """Номера (с нуля) добавленных строк в рабочей копии по сравнению с HEAD."""
    if head_text(repo, path) is None:
        full = repo / path
        return list(range(len(kbbase.read_lines(full)))) if full.exists() else []
    out = git(repo, "diff", "-U0", "HEAD", "--", path)
    result = []
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", out, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        result += range(start - 1, start - 1 + count)
    return result


def describe(repo: Path, kb: Path, files: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Человеческое описание изменений: новые факты, новые вопросы, смена статусов, прочее."""
    out = {"факты": [], "вопросы": [], "статусы": [], "прочее": []}
    for code, path in files:
        inner = kb_path(repo, kb, path)
        full = repo / path
        if not full.exists():
            out["прочее"].append(f"удалён {inner}")
            continue
        if full.suffix != ".md":
            out["прочее"].append(inner)
            continue
        lines = kbbase.read_lines(full)
        head, body = kbbase.front_matter(lines)
        ident = head.get("id") or Path(inner).stem
        if inner.startswith("journal/questions/") and head_text(repo, path) is None:
            title = next((l[2:].strip() for l in lines[body:] if l.startswith("# ")), ident)
            out["вопросы"].append(title)
        old = head_text(repo, path)
        if old is not None:
            old_head, _ = kbbase.front_matter(old.splitlines())
            if old_head.get("статус") != head.get("статус"):
                out["статусы"].append(f"{ident}: {old_head.get('статус', '—')} → {head.get('статус', '—')}")
        secs = kbbase.sections(lines, body)
        facts = 0
        for n in added_lines(repo, path):
            if n < len(lines) and FACT_LINE.match(lines[n]):
                section = next((name for name, s, e in secs if s < n < e), "")
                text = lines[n] if len(lines[n]) <= 110 else lines[n][:107] + "…"
                out["факты"].append(f"{ident} «{section}»: {text}")
                facts += 1
        if not facts and not (inner.startswith("journal/questions/") and old is None):
            out["прочее"].append(inner)
    return out


def subject(described: dict[str, list[str]], files: list[tuple[str, str]], repo: Path, kb: Path) -> str:
    ids = []
    for _, path in files:
        stem = Path(path).stem
        m = re.match(r"^(PM-\d+|[QDAR]-[\w]+?)(?:-|$)", stem)
        ident = m.group(1) if m else None
        if ident and ident not in ids:
            ids.append(ident)
    what = []
    if described["факты"]:
        what.append(f"факты ({len(described['факты'])})")
    if described["вопросы"]:
        what.append(f"вопросы ({len(described['вопросы'])})")
    if described["статусы"]:
        what.append("статус")
    head = ", ".join(ids[:4]) + (" и др." if len(ids) > 4 else "")
    return f"{head or 'kb'}: {', '.join(what) or 'правки'}"


def plans_dir(repo: Path) -> Path:
    return repo / ".kb" / "commit-plans"


def plan(kb: Path, only: list[str], message: str | None, person: str) -> int:
    repo = repo_root(kb)
    rebuilt = kbquestions.write_views(kb)
    if rebuilt:
        print(f"Списки вопросов пересобраны: {', '.join(rebuilt)}")
    files = changed_files(repo, kb)
    if only:
        wanted = {str((Path.cwd() / p).resolve()) for p in only}
        files = [(c, p) for c, p in files if str((repo / p).resolve()) in wanted]
    if not files:
        print("Нечего коммитить: в kb/ нет изменений")
        return 1
    problems = forbidden(repo, kb, files)
    rep = kbcheck.run(kb)
    if rep.errors():
        problems.append(f"`kb check` нашёл ошибки: {rep.errors()}. Исправьте их и покажите план заново")
    if problems:
        print("ПЛАН НЕ СОСТАВЛЕН:")
        for p in problems:
            print(f"- {p}")
        if rep.errors():
            rep.items = [x for x in rep.items if x[2].level == "ОШИБКА"]
            rep.print()
        return 1
    described = describe(repo, kb, files)
    msg = message or subject(described, files, repo, kb)
    record = {"files": {p: digest(repo, p) for _, p in files}, "message": msg, "person": person}
    plan_id = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:6]
    plans_dir(repo).mkdir(parents=True, exist_ok=True)
    (plans_dir(repo) / f"{plan_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
    print(f"ПЛАН КОММИТА {plan_id} — файлов: {len(files)}")
    labels = [("Новые вопросы", "вопросы"), ("Смена статуса", "статусы"), ("Новые факты", "факты"),
              ("Другие файлы", "прочее")]
    for label, key in labels:
        if described[key]:
            print(f"{label}:")
            for item in described[key]:
                print(f"  {item}")
    print(f"Сообщение: {msg}")
    print(f"Решил: {person}")
    print(f"Покажите это человеку. После его «да»: python3 kb/tools/kb.py commit apply {plan_id}")
    return 0


def apply(kb: Path, plan_id: str) -> int:
    repo = repo_root(kb)
    path = plans_dir(repo) / f"{plan_id}.json"
    if not re.match(r"^[0-9a-f]{6}$", plan_id) or not path.is_file():
        print(f"ОШИБКА: плана {plan_id} нет. Покажите план: python3 kb/tools/kb.py commit plan")
        return 1
    record = json.loads(path.read_text(encoding="utf-8"))
    changed = [p for p, h in record["files"].items() if digest(repo, p) != h]
    current = {p for _, p in changed_files(repo, kb)}
    extra = sorted(current - set(record["files"]))
    if changed:
        names = ", ".join(f"`{p}`" for p in changed)
        print(f"ОШИБКА: с момента показа изменилось: {names}. Покажите план заново: python3 kb/tools/kb.py commit plan")
        return 1
    files = list(record["files"])
    git(repo, "add", "-A", "--", *files)
    try:
        git(repo, "commit", "-m", record["message"], "-m", f"Решил: {record['person']}", "--", *files)
    except GitError as e:
        git(repo, "reset", "-q", "--", *files, check=False)
        print(f"ОШИБКА: коммит не прошёл: {e}")
        return 1
    path.unlink()
    print(f"ЗАКОММИЧЕНО: {git(repo, 'log', '-1', '--format=%h %s').strip()}")
    if extra:
        print(f"Не вошло в коммит (изменено после показа плана): {', '.join(extra)}")
    print("Отправку на сервер делает человек.")
    return 0


def precommit(kb: Path) -> int:
    """pre-commit: то, что нельзя коммитить никому — ни агенту, ни человеку."""
    repo = repo_root(kb)
    out = git(repo, "diff", "--cached", "--name-status", "-z")
    parts = [p for p in out.split("\0") if p]
    files, i = [], 0
    while i < len(parts):
        code = parts[i]
        if code[0] in "RC":
            files.append((code[0], parts[i + 2]))
            files.append(("D", parts[i + 1]))
            i += 3
        else:
            files.append((code, parts[i + 1]))
            i += 2
    problems = forbidden(repo, kb, files)
    rep = kbcheck.run(kb)
    if rep.errors():
        problems.append(f"`kb check`: ошибок {rep.errors()}. Подробно: python3 kb/tools/kb.py check")
    if problems:
        print("kb: коммит остановлен")
        for p in problems:
            print(f"- {p}")
        return 1
    return 0


def install_hooks(kb: Path) -> int:
    repo = repo_root(kb)
    hooks_dir = Path(__file__).resolve().parent / "hooks"
    try:
        hooks = hooks_dir.relative_to(repo.resolve()).as_posix()
    except ValueError:
        hooks = str(hooks_dir)
    git(repo, "config", "core.hooksPath", hooks)
    print(f"Хуки подключены: core.hooksPath = {hooks}")
    return 0
