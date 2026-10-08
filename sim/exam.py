#!/usr/bin/env python3
"""Экзамен модели на симуляции: два контрольных шага и автоматическая проверка результата.

    python3 exam.py prepare explore <папка>   состояние перед шагом 6: Мадина разбирает PM-04
    python3 exam.py prepare sync <папка>      состояние перед шагом 10: утреннее дежурство Гульнары
    python3 exam.py grade explore <папка>     проверить, что сделала модель
    python3 exam.py grade sync <папка>

Нужна собранная симуляция (python3 build.py). Задания для модели — в выводе prepare.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIM = HERE / "out" / "repo"

TASKS = {
    "explore": dict(
        commit_grep="PM-04: взяла рут", branch="pm04-razbor", user="Мадина", today="2026-10-12",
        command="/kb-explore PM-04. Я владелец, разработчик. Разбираю скрипт match_payments.py и заметки интервью бухгалтерии",
        answers=["«Раскидываем как получится» — только факт, вопрос заведёт тестировщик.",
                 "Вопрос про источник правды по остатку долга — «заводи, пока ничего не блокирует».",
                 "Остальное — на усмотрение агента."],
    ),
    "sync": dict(
        commit_grep="kb: номера и сводки", branch="main", user="Гульнара", today="2026-10-13",
        command="/kb-sync утро",
        answers=["Склеивать дубль — «да, нюанс автора позднего вопроса сохрани».",
                 "Формулировка нового вопроса — «да».",
                 "Остальное — на усмотрение агента."],
    ),
}


def git(repo, *args, check=True):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise SystemExit(r.stderr)
    return r.stdout


def prepare(task, target: Path):
    t = TASKS[task]
    if not SIM.exists():
        raise SystemExit("Сначала соберите симуляцию: python3 build.py")
    log = git(SIM, "log", "--reverse", "--format=%h %s").splitlines()
    start = next(l.split()[0] for l in log if t["commit_grep"] in l)
    if target.exists():
        raise SystemExit(f"{target} уже есть — укажите новую папку")
    subprocess.run(["git", "clone", "-q", str(SIM), str(target)], check=True)
    git(target, "checkout", "-q", "-B", t["branch"], start)
    git(target, "remote", "remove", "origin")
    git(target, "config", "user.name", t["user"])
    git(target, "tag", "exam-start")
    print(f"Готово: {target}  (git user.name = {t['user']}, ветка {t['branch']}, метка exam-start)")
    print(f"\nОткройте папку в OpenCode. Дата сценария — {t['today']}: перед запуском задайте KB_TODAY={t['today']}.")
    print(f"Наберите:\n  {t['command']}")
    print("На вопросы агента отвечайте так:\n  - " + "\n  - ".join(t["answers"]))
    print(f"\nПосле работы агента: python3 {Path(__file__).name} grade {task} {target}")


def kb(repo, *args):
    env = dict(os.environ, KB_TODAY=TASKS["sync"]["today"])
    return subprocess.run([sys.executable, "tools/kb.py", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", env=env).stdout


def read(repo, rel):
    p = repo / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def fm(text, key):
    m = re.search(rf"^{re.escape(key)}:\s*(.*?)\s*(#.*)?$", text, re.M)
    return m.group(1) if m else ""


def questions(repo):
    out = []
    for p in sorted((repo / "journal/questions").glob("Q-*.md")):
        t = p.read_text(encoding="utf-8")
        h = re.search(r"^# \S+\s+(.*)$", t, re.M)
        out.append(dict(path=p, text=t, id=fm(t, "id"), status=fm(t, "статус"), section=fm(t, "раздел"),
                        title=h.group(1) if h else ""))
    return out


def grade(task, repo: Path):
    checks = []

    def ok(name, cond, note=""):
        checks.append((name, bool(cond), note))

    check_out = kb(repo, "check")
    errors = re.search(r"ошибок (\d+)", check_out)
    ok("kb.py check — ошибок 0", errors and errors.group(1) == "0", check_out.strip().splitlines()[-1] if check_out else "")
    changed = [l for l in git(repo, "diff", "--name-only", "exam-start", "HEAD").splitlines()]
    ok("Есть коммит после старта", changed, f"изменено файлов: {len(changed)}")
    dirty = git(repo, "status", "--porcelain").strip()
    ok("Всё закоммичено", not dirty, dirty[:120])

    if task == "explore":
        step = read(repo, "context/process/PM-04-sopostavlenie-oplat.md")
        ok("Статус PM-04 — «Разбор»", fm(step, "статус") == "Разбор", fm(step, "статус"))
        now = re.search(r"^## Как сейчас\n(.*?)(?=^## )", step, re.M | re.S)
        items = [l for l in (now.group(1) if now else "").splitlines() if re.match(r"^\d+\.\s", l)]
        ok("«Как сейчас» — не меньше 4 действий", len(items) >= 4, f"{len(items)}")
        ok("Факты ссылаются на match_payments.py", "match_payments.py" in step)
        lint = [l for l in check_out.splitlines() if "факт без статуса" in l]
        ok("Нет фактов без статуса", not lint, f"{len(lint)} шт.")
        ok("Противоречие ФИО+дата рождения / ФИО+сумма записано",
           re.search(r"противоречие", step) and re.search(r"сумм", step, re.I))
        qs = [q for q in questions(repo) if q["id"].startswith("Q-new")]
        ok("Вопрос про источник правды по остатку",
           any(re.search(r"остат|источник правды", q["title"] + q["text"], re.I) for q in qs), f"новых вопросов: {len(qs)}")
        ok("Не больше 8 новых вопросов", len(qs) <= 8, f"{len(qs)}")
        allowed = ("context/process/PM-04", "journal/", "context/systems/SYS-03", "datasets/PM-04", "context/process/_index.md",
                   "context/process/_map")
        foreign = [c for c in changed if not c.startswith(allowed)]
        ok("Чужие файлы не тронуты", not foreign, ", ".join(foreign))
        ok("Ветка pm04-razbor", git(repo, "branch", "--show-current").strip() == "pm04-razbor")
    else:
        qs = {q["id"]: q for q in questions(repo)}
        q4 = next((q for q in qs.values() if q["path"].name.startswith("Q-004")), None)
        q2 = next((q for q in qs.values() if q["path"].name.startswith("Q-002")), None)
        ok("Q-004 склеен как дубль", q4 and q4["status"] == "дубль")
        ok("Тимур — подписчик Q-002", q2 and re.search(r"^## Подписчики\n(?:.*\n)*?- Тимур", q2["text"], re.M))
        new = [q for q in qs.values() if q["path"].name[:5] not in ("Q-001", "Q-002", "Q-003", "Q-004")]
        ok("Противоречие ФИО+дата рождения / ФИО+сумма вынесено вопросом",
           any(re.search(r"ФИО|без номера", q["title"] + q["text"]) and re.search(r"сумм", q["text"]) for q in new),
           f"новых вопросов: {len(new)}")
        dig = read(repo, "journal/digest/2026-10-13.md")
        ok("Дайджест 2026-10-13 с «Синхронизировано до»", "Синхронизировано до" in dig)
        ok("Утром статусы «ждёт бизнес» не ставились", not any(q["status"] == "ждёт бизнес" for q in qs.values()))
        steps_changed = [c for c in changed if c.startswith("context/process/PM-")]
        ok("Файлы шагов не тронуты (дежурный их не правит)", not steps_changed, ", ".join(steps_changed))

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, note in checks:
        print(f"{'✓' if c else '✗'} {name}" + (f" — {note}" if note and not c else ""))
    print(f"\nИтог: {passed} из {len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("prepare", "grade") or sys.argv[2] not in TASKS:
        print(__doc__)
        sys.exit(2)
    _, action, task, folder = sys.argv
    sys.exit(prepare(task, Path(folder).resolve()) if action == "prepare" else grade(task, Path(folder).resolve()))
