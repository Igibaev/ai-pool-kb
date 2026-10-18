"""kb report morning: полный утренний отчёт аналитику по истории git. Строит инструмент, модель только пересказывает.

Полный — значит каждый изменённый файл периода есть в отчёте, а не выборка.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from pathlib import Path

import kbbase
import kbdeps
import kbgit
import kbquestions

STALE_DAYS = 2
FACT = re.compile(r"^(?:-|\d+\.)\s+.*\(ист:")
SEP = "\x1e"


class Change:
    def __init__(self):
        self.commits: set[str] = set()
        self.facts: dict[str, int] = defaultdict(int)     # раздел → число
        self.statuses: list[str] = []
        self.enough = False


def commits_since(repo: Path, kb: Path, since: str) -> list[tuple[str, str, str, str]]:
    """(хеш, почта, имя, дата) коммитов, которые меняли kb/, от старых к новым."""
    prefix = kb.relative_to(repo).as_posix()
    if re.match(r"^[0-9a-f]{6,40}$", since):
        rng = [f"{since}..HEAD"]
    else:
        rng = [f"--since={since}"]
    out = kbgit.git(repo, "log", "--reverse", f"--format=%H{SEP}%ae{SEP}%an{SEP}%cs", *rng, "--", prefix, check=False)
    return [tuple(l.split(SEP)) for l in out.split("\n") if l.count(SEP) == 3]   # splitlines() режет по \x1e


def commit_diff(repo: Path, sha: str, prefix: str) -> dict[str, list[str]]:
    """Файл → строки диффа коммита (без контекста)."""
    out = kbgit.git(repo, "show", "--format=", "-U0", "--no-renames", sha, "--", prefix, check=False)
    files: dict[str, list[str]] = {}
    current = minus = None
    header = False
    for line in out.splitlines():
        if line.startswith("diff --git"):
            current, minus, header = None, None, True
        elif header and line.startswith("--- "):
            minus = line[6:] if line[4:6] == "a/" else None
        elif header and line.startswith("+++ "):
            header = False
            if line[4:6] == "b/":
                current = line[6:]
                files.setdefault(current, [])
            else:                                   # +++ /dev/null — файл удалён
                current = minus
                files.setdefault(current, []).append("__удалён__")
        elif not header and current is not None and line[:1] in "+-":
            files[current].append(line)
    return files


def section_of(text_lines: list[str], fact_line: str) -> str:
    _, body = kbbase.front_matter(text_lines)
    for name, start, end in kbbase.sections(text_lines, body):
        if fact_line in text_lines[start:end]:
            return name
    return ""


def build(kb: Path, since: str) -> str:
    repo = kbgit.repo_root(kb)
    prefix = kb.relative_to(repo).as_posix()
    team = kbbase.Team(kb)
    commits = commits_since(repo, kb, since)

    def person(email: str, name: str) -> str:
        return team.by_git(email, name) or name

    steps: dict[str, dict[str, Change]] = defaultdict(lambda: defaultdict(Change))
    people: dict[str, dict[str, set | int]] = defaultdict(lambda: {"commits": set(), "steps": set(), "facts": 0,
                                                                   "asked": 0, "answers": 0, "closed": 0})
    q_events: dict[str, list[tuple[str, str]]] = defaultdict(list)   # вид → (ID, кто)
    files_by: dict[str, set[str]] = defaultdict(set)
    dates = [c[3] for c in commits]

    for sha, email, name, date in commits:
        who = person(email, name)
        people[who]["commits"].add(sha)
        for path, diff in commit_diff(repo, sha, prefix).items():
            inner = path[len(prefix) + 1:] if path.startswith(prefix + "/") else path
            files_by[inner].add(who)
            stem = Path(inner).stem
            added = [l[1:] for l in diff if l.startswith("+")]
            removed = [l[1:] for l in diff if l.startswith("-")]
            if inner.startswith("context/process/PM-") and "/notes/" not in inner:
                ident = "-".join(stem.split("-")[:2])
                ch = steps[ident][who]
                ch.commits.add(sha)
                people[who]["steps"].add(ident)
                full = repo / path
                current = kbbase.read_lines(full) if full.exists() else []
                for line in added:
                    if FACT.match(line):
                        ch.facts[section_of(current, line) or "?"] += 1
                        people[who]["facts"] += 1
                    if line.startswith("статус:"):
                        old = next((r.split(":", 1)[1].strip() for r in removed if r.startswith("статус:")), "—")
                        ch.statuses.append(f"{old} → {line.split(':', 1)[1].strip()}")
                    if line.startswith("достаточно для спеки:") and "—" not in line:
                        ch.enough = True
            elif inner.startswith("journal/questions/Q-"):
                ident = re.match(r"^(Q-[\w-]+?)(?:\.md)?$", Path(inner).name).group(1)
                if "__удалён__" in diff:
                    q_events["удалены"].append((ident, who))
                    continue
                if any(l.startswith("задаём:") for l in added) and not any(l.startswith("задаём:") for l in removed):
                    q_events["новые"].append((ident, who))
                    people[who]["asked"] += 1
                for line in added:
                    if line.startswith("статус:"):
                        state = line.split(":", 1)[1].strip()
                        if state == "ответ найден":
                            q_events["ответ найден"].append((ident, who))
                            people[who]["answers"] += 1
                        elif state == "отвечен":
                            q_events["закрыты"].append((ident, who))
                            people[who]["closed"] += 1
                        elif state == "вне системы":
                            q_events["вне системы"].append((ident, who))
                        elif state.startswith("стал допущением"):
                            q_events["стали допущением"].append((ident, who))
                    if line.startswith("рассортировал:") and "—" not in line:
                        q_events["рассортированы"].append((ident, who))

    questions = {h.get("id", p.stem): (l, h, b) for p, l, h, b in kbquestions.all_questions(kb)}

    def q_line(ident: str, who: str) -> str:
        if ident not in questions:
            return f"- {ident} — {who}"
        l, h, b = questions[ident]
        return (f"- {ident} — {kbquestions.title(l, b)} — {who}; кто отвечает: {h.get('кто отвечает', '—')}; "
                f"сейчас: {h.get('статус', '—')}")

    today = kbbase.today()
    period = f"с {dates[0]} по {dates[-1]}" if dates else f"с {since}"
    text = [f"# Утренний отчёт {today.isoformat()}", "",
            f"Период: {period} · коммитов {len(commits)} · людей {len(people)}",
            "Строит `kb report morning` по истории git. Это полный список изменений, а не выборка.", ""]

    text += ["## Вопросы", ""]
    unsorted = [i for i, (l, h, b) in questions.items()
                if h.get("статус") in ("открыт", "ответ найден") and kbquestions.is_new(h)]
    labels = [("новые", "Новые"), ("рассортированы", "Рассортированы"), ("ответ найден", "Ответ найден — ждёт подтверждения автора"),
              ("закрыты", "Закрыты"), ("вне системы", "Вне системы"), ("стали допущением", "Стали допущением"),
              ("удалены", "Удалены")]
    for key, label in labels:
        events = q_events.get(key, [])
        if events or key in ("новые", "ответ найден", "закрыты"):
            text += [f"### {label} — {len(events)}", ""] + ([q_line(i, w) for i, w in events] or ["Нет."]) + [""]
    text += [f"### Ждут сортировки аналитиком сейчас — {len(unsorted)}", ""]
    text += ([q_line(i, questions[i][1].get("автор", "")) for i in unsorted] or ["Нет."]) + [""]
    stale = []
    for ident, (l, h, b) in questions.items():
        if h.get("статус") not in ("открыт", "ответ найден"):
            continue
        path = kbbase.find_object(kb, ident)
        last = kbgit.git(repo, "log", "-1", "--format=%cs", "--", str(path), check=False).strip()
        if last and (today - dt.date.fromisoformat(last)).days > STALE_DAYS:
            stale.append(f"- {ident} — {kbquestions.title(l, b)} — без движения с {last}; "
                         f"кто отвечает: {h.get('кто отвечает', '—')}")
    text += [f"### Без движения больше {STALE_DAYS} дней — {len(stale)}", ""] + (stale or ["Нет."]) + [""]

    text += ["## По шагам", ""]
    if not steps:
        text += ["Шаги не менялись.", ""]
    for ident in sorted(steps):
        path = kbbase.find_object(kb, ident)
        name = kbbase.front_matter(kbbase.read_lines(path))[0].get("шаг", "") if path else ""
        text += [f"### {ident} {name}".rstrip(), ""]
        for who, ch in sorted(steps[ident].items()):
            parts = []
            if ch.facts:
                parts.append("фактов +" + str(sum(ch.facts.values())) + " ("
                             + ", ".join(f"«{s}» {n}" for s, n in sorted(ch.facts.items())) + ")")
            parts += [f"статус {s}" for s in ch.statuses]
            if ch.enough:
                parts.append("сказал «мне достаточно»")
            text.append(f"- {who}: {'; '.join(parts) or 'правки без новых фактов'}")
        text.append("")

    old = kbdeps.stale(kb)
    text += [f"## Устарело — {len(old)}", ""] + ([f"- {line}" for line in old] or ["Нет."]) + [""]
    text += ["## По людям", "", "| Человек | Коммитов | Шаги | Фактов | Вопросов задал | Ответов | Закрыл |",
             "|---|---|---|---|---|---|---|"]
    for who, p in sorted(people.items()):
        text.append(f"| {who} | {len(p['commits'])} | {', '.join(sorted(p['steps'])) or '—'} | {p['facts']} | "
                    f"{p['asked']} | {p['answers']} | {p['closed']} |")
    text += ["", f"## Все изменённые файлы — {len(files_by)}", ""]
    text += [f"- {path} — {', '.join(sorted(who))}" for path, who in sorted(files_by.items())]
    return "\n".join(text) + "\n"


def default_since(kb: Path) -> str:
    reports = sorted((kb / "journal" / "reports").glob("*.md"))
    if reports:
        return reports[-1].stem
    return (kbbase.today() - dt.timedelta(days=1)).isoformat()


def morning(kb: Path, since: str | None, save: bool) -> int:
    since = since or default_since(kb)
    text = build(kb, since)
    print(text, end="")
    if save:
        folder = kb / "journal" / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{kbbase.today().isoformat()}.md"
        path.write_text(text, encoding="utf-8")
        print(f"\nСохранено: {kbbase.rel(kb, path)}")
    return 0
