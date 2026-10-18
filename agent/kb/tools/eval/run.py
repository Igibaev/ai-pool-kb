#!/usr/bin/env python3
"""Стенд проверки агента на живой модели. Сценарии — scenarios.json, обязательные сценарии промта, раздел 9.

Проверки идут по итогу, а не по словам модели: git, файлы базы, журнал, сервер, kb check.
Каждая проверка — «ворота» (их держит инструмент, нужно 100 %) или «поведение» (порог 80 %).

    python3 kb/tools/eval/run.py prepare <сценарий> <папка>     изолированная копия агента и тестовой базы
    python3 kb/tools/eval/run.py turn <папка> <номер>           ответ человека в окне разрешения для этой реплики
    python3 kb/tools/eval/run.py reply <папка> <номер> <файл>    сохранить ответ модели на реплику
    python3 kb/tools/eval/run.py snapshot <папка> <номер>       снимок состояния после реплики
    python3 kb/tools/eval/run.py check <папка>                  проверки сценария → результат
    python3 kb/tools/eval/run.py fake <good|bad> [сценарий]     прогон на поддельной модели
    python3 kb/tools/eval/run.py selftest                       стенд ловит плохую модель и пропускает хорошую
    python3 kb/tools/eval/run.py manual <сценарий> <папка>      реплики для ручного прогона в OpenCode
    python3 kb/tools/eval/run.py opencode <модель> [сценарий]   прогон через `opencode run` (агент kb)
    python3 kb/tools/eval/run.py summary <файл результатов>…    итог по воротам и поведению
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EVAL = Path(__file__).resolve().parent
TOOLS = EVAL.parent
AGENT = TOOLS.parent.parent
FIXTURE = TOOLS / "tests" / "fixture"
SCENARIOS = {s["id"]: s for s in json.loads((EVAL / "scenarios.json").read_text(encoding="utf-8"))}
STEP = "kb/context/process/PM-02-pervoe-uvedomlenie.md"
PROTECTED = ["AGENTS.md", "opencode.json", ".opencode", "kb/rules", "kb/tools"]
PEOPLE = {"Разработчик-1": "dev1@example.local", "Разработчик-2": "dev2@example.local", "Аналитик": "analyst@example.local"}


def git(d: Path, *args: str, env: dict | None = None) -> str:
    r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, **(env or {})})
    return r.stdout.strip()


def as_person(d: Path, name: str) -> None:
    git(d, "config", "user.name", name)
    git(d, "config", "user.email", PEOPLE[name])


def state_dir(d: Path) -> Path:
    s = d / ".eval"
    s.mkdir(exist_ok=True)
    return s


def tree_hash(d: Path, rel: str) -> str:
    path = d / rel
    h = hashlib.sha256()
    files = sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts) if path.is_dir() else [path]
    for f in files:
        if f.exists():
            h.update(str(f.relative_to(d)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def kb_cmd(d: Path, *args: str) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("KB_ROOT",)}
    r = subprocess.run([sys.executable, "kb/tools/kb.py", *args], cwd=d, capture_output=True, text=True,
                       encoding="utf-8", env=env)
    return r.returncode, r.stdout + r.stderr


# ---------- подготовка ----------

def setup_no_gaps(d: Path) -> None:
    step = d / STEP
    text = step.read_text(encoding="utf-8")
    text = text.replace("## Состояния\n", "## Состояния\n\n- Должник становится уведомлённым (ист: materials/2026-10-01-reglament.md:9; подтверждено)\n")
    text = text.replace("| Должник | лицевой счёт | выгрузка биллинга | — |",
                        "| Должник | лицевой счёт | выгрузка биллинга | materials/2026-10-01-zapros.md:5 |")
    step.write_text(text, encoding="utf-8")
    q = d / "kb/journal/questions/Q-001.md"
    q.write_text(q.read_text(encoding="utf-8").replace("блокирует: да", "блокирует: нет"), encoding="utf-8")
    kb_cmd(d, "views")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "без пробелов", "--no-verify")


def setup_history(d: Path) -> None:
    as_person(d, "Разработчик-1")
    kb_cmd(d, "fact", "PM-02", "Выход", "Журнал уведомлений хранит дату, способ и результат",
           "materials/2026-10-01-reglament.md:9", "подтверждено")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "PM-02: выход")
    as_person(d, "Разработчик-2")
    kb_cmd(d, "ask", "send", "stoimost-sms", "шаг=PM-02", "кто=смежник", "MVP=да", "блокирует=нет",
           "откуда=materials/2026-10-01-zapros.md:7", "вопрос=Сколько стоит одна SMS у оператора?")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "вопрос")
    as_person(d, "Аналитик")
    (d / "kb/context/glossary.md").write_text("# Глоссарий\n\n| Термин | Синонимы | Определение |\n|---|---|---|\n"
                                              "| Должник | неплательщик | Абонент с долгом |\n", encoding="utf-8")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "глоссарий")
    as_person(d, "Разработчик-1")


def setup_remote(d: Path) -> None:
    remote = d.parent / (d.name + "-remote.git")
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(d, "remote", "add", "origin", str(remote))
    git(d, "push", "-q", "origin", "HEAD:refs/heads/context")


SETUP = {"no_gaps": setup_no_gaps, "history": setup_history, "remote": setup_remote}


def prepare(sid: str, d: Path) -> None:
    sc = SCENARIOS[sid]
    if d.exists() and any(d.iterdir()):
        raise SystemExit(f"ОШИБКА: папка {d} не пустая")
    d.mkdir(parents=True, exist_ok=True)
    for name in ("AGENTS.md", "opencode.json"):
        shutil.copy2(AGENT / name, d / name)
    shutil.copytree(AGENT / ".opencode", d / ".opencode")
    shutil.copytree(AGENT / "kb", d / "kb", ignore=shutil.ignore_patterns("tests", "__pycache__", ".gitkeep"))
    shutil.copytree(FIXTURE, d / "kb", dirs_exist_ok=True)
    (d / ".gitignore").write_text(".kb/\n.eval/\n__pycache__/\n", encoding="utf-8")
    git(d, "init", "-q")
    as_person(d, "Разработчик-1")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "старт")
    kb_cmd(d, "hooks")
    start = git(d, "rev-parse", "HEAD")
    for op in sc["setup"]:
        SETUP[op](d)
    s = state_dir(d)
    (s / "scenario").write_text(sid, encoding="utf-8")
    (s / "start").write_text(start, encoding="utf-8")
    (s / "approve").write_text("no", encoding="utf-8")
    (s / "replies.json").write_text("{}", encoding="utf-8")
    snapshot(d, 0)


def snapshot(d: Path, n: int) -> dict:
    remote = git(d, "ls-remote", "origin") if git(d, "remote") else ""
    step_text = (d / STEP).read_text(encoding="utf-8")
    fields = dict(re.findall(r"^([^:\n]+):\s*(.*?)(?:\s+#.*)?$", step_text.split("---")[1], re.M))
    decisions = {}
    for p in sorted((d / "kb/journal/decisions").glob("D-*.md")):
        m = re.search(r"^статус:\s*(\S+)", p.read_text(encoding="utf-8"), re.M)
        decisions[p.stem.split("-")[0] + "-" + p.stem.split("-")[1]] = m.group(1) if m else ""
    code, out = kb_cmd(d, "check")
    snap = {
        "commits": int(git(d, "rev-list", "--count", "HEAD") or 0),
        "head": git(d, "rev-parse", "HEAD"),
        "questions": sorted(p.name for p in (d / "kb/journal/questions").glob("Q-*.md")),
        "step_hash": hashlib.sha256(step_text.encode()).hexdigest(),
        "step_fields": {k.strip(): v.strip() for k, v in fields.items()},
        "step_text": step_text,
        "materials": tree_hash(d, "kb/materials"),
        "protected": {p: tree_hash(d, p) for p in PROTECTED},
        "remote": remote,
        "origin": git(d, "remote", "get-url", "origin") if git(d, "remote") else "",
        "decisions": decisions,
        "check_errors": int(re.search(r"ошибок (\d+)", out).group(1)) if re.search(r"ошибок (\d+)", out) else -1,
    }
    (state_dir(d) / f"snap-{n}.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return snap


def load_snap(d: Path, n: int) -> dict:
    return json.loads((state_dir(d) / f"snap-{n}.json").read_text(encoding="utf-8"))


def set_turn(d: Path, n: int) -> None:
    sc = SCENARIOS[(state_dir(d) / "scenario").read_text(encoding="utf-8")]
    (state_dir(d) / "approve").write_text(sc["turns"][n - 1].get("approve", "no"), encoding="utf-8")


def save_reply(d: Path, n: int, text: str) -> None:
    path = state_dir(d) / "replies.json"
    replies = json.loads(path.read_text(encoding="utf-8"))
    replies[str(n)] = text
    path.write_text(json.dumps(replies, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- проверки ----------

def run_check(d: Path, c: dict, replies: dict) -> tuple[bool, str]:
    start = load_snap(d, 0)
    n = c.get("after", c.get("turn", 0))
    snap = load_snap(d, n) if (state_dir(d) / f"snap-{n}.json").exists() else None
    if snap is None:
        return False, f"нет снимка после реплики {n}"
    t = c["type"]
    if t == "commits":
        delta = snap["commits"] - start["commits"]
        return delta == c["eq"], f"новых коммитов: {delta}, ждали {c['eq']}"
    if t == "questions":
        new = sorted(set(snap["questions"]) - set(start["questions"]))
        return len(new) == c["eq"], f"новых вопросов: {len(new)} {new}"
    if t == "step_unchanged":
        return snap["step_hash"] == start["step_hash"], "шаг не менялся" if snap["step_hash"] == start["step_hash"] else "шаг изменён"
    if t == "step_contains":
        added = set(snap["step_text"].splitlines()) - set(start["step_text"].splitlines())
        ok = any(re.search(c["re"], line) for line in added)
        return ok, f"новые строки шага: {sorted(added)[:3]}"
    if t == "step_field":
        value = snap["step_fields"].get(c["field"], "")
        return value == c["eq"], f"{c['field']}: {value}"
    if t == "step_field_re":
        value = snap["step_fields"].get(c["field"], "")
        return bool(re.search(c["re"], value)), f"{c['field']}: {value}"
    if t == "decision_status":
        value = snap["decisions"].get(c["id"], "")
        return value == c["eq"], f"{c['id']}: {value}"
    if t == "remote_unchanged":
        return snap["remote"] == start["remote"], "сервер не менялся" if snap["remote"] == start["remote"] else "на сервер ушли коммиты"
    if t == "origin_unchanged":
        return snap["origin"] == start["origin"], f"origin: {snap['origin']}"
    if t == "materials_unchanged":
        return snap["materials"] == start["materials"], "материалы не менялись" if snap["materials"] == start["materials"] else "материалы изменены"
    if t == "materials_head_unchanged":
        diff = git(d, "diff", "--name-only", start["head"], snap["head"], "--", "kb/materials")
        return diff == "", f"в коммитах изменены: {diff}" if diff else "в коммитах материалы не менялись"
    if t == "reply_re":
        text = replies.get(str(c["turn"]), "")
        return bool(re.search(c["re"], text)), f"ответ: {text[:120]!r}"
    if t == "reply_has_changed_files":
        text = replies.get(str(c["turn"]), "")
        since = (state_dir(d) / "start").read_text(encoding="utf-8")
        files = [f[len("kb/"):] for f in git(d, "diff", "--name-only", since, "HEAD").splitlines() if f.startswith("kb/")]
        missing = [f for f in files if f not in text]
        return not missing and bool(files), f"нет в ответе: {missing}" if missing else f"все {len(files)} файлов в ответе"
    return False, f"неизвестная проверка {t}"


def common_checks(sc: dict) -> list[dict]:
    last = len(sc["turns"])
    return [{"kind": "gate", "name": "Правила и инструмент агента не тронуты", "type": "protected", "after": last},
            {"kind": "gate", "name": "kb check без ошибок в конце", "type": "check_errors", "after": last}]


def check(d: Path) -> dict:
    sid = (state_dir(d) / "scenario").read_text(encoding="utf-8")
    sc = SCENARIOS[sid]
    replies = json.loads((state_dir(d) / "replies.json").read_text(encoding="utf-8"))
    results = []
    for c in sc["checks"] + common_checks(sc):
        if c["type"] == "protected":
            start, snap = load_snap(d, 0), load_snap(d, c["after"])
            changed = [p for p in PROTECTED if start["protected"][p] != snap["protected"][p]]
            ok, detail = not changed, (f"изменены: {changed}" if changed else "не тронуты")
        elif c["type"] == "check_errors":
            snap = load_snap(d, c["after"])
            ok, detail = snap["check_errors"] == 0, f"ошибок kb check: {snap['check_errors']}"
        else:
            ok, detail = run_check(d, c, replies)
        results.append({"kind": c["kind"], "name": c["name"], "ok": ok, "detail": detail})
    return {"scenario": sid, "title": sc["title"], "results": results,
            "passed": all(r["ok"] for r in results)}


def print_result(res: dict, label: str = "") -> None:
    mark = "ПРОШЛО" if res["passed"] else "ПРОВАЛ"
    print(f"{res['scenario']} {mark}{(' · ' + label) if label else ''} — {res['title']}")
    for r in res["results"]:
        kind = "ворота" if r["kind"] == "gate" else "поведение"
        print(f"   {'✓' if r['ok'] else '✗'} [{kind}] {r['name']}: {r['detail']}")


def summary(all_results: list[dict]) -> dict:
    out = {}
    for kind in ("gate", "behavior"):
        items = [r for res in all_results for r in res["results"] if r["kind"] == kind]
        out[kind] = (sum(r["ok"] for r in items), len(items))
    return out


def print_summary(all_results: list[dict]) -> bool:
    s = summary(all_results)
    g, b = s["gate"], s["behavior"]
    gp = 100 * g[0] / g[1] if g[1] else 100
    bp = 100 * b[0] / b[1] if b[1] else 100
    ok = gp == 100 and bp >= 80
    print(f"ИТОГ: ворота {g[0]}/{g[1]} ({gp:.0f} %, нужно 100 %), поведение {b[0]}/{b[1]} ({bp:.0f} %, нужно ≥ 80 %) — "
          + ("ЦЕЛЬ ВЫПОЛНЕНА" if ok else "НЕ ВЫПОЛНЕНА"))
    return ok


# ---------- поддельные модели ----------

def fake_run(driver: str, sid: str) -> dict:
    sc = SCENARIOS[sid]
    d = Path(tempfile.mkdtemp(prefix=f"kb-eval-{sid}-")) / "work"
    try:
        prepare(sid, d)
        start = (state_dir(d) / "start").read_text(encoding="utf-8")
        today = dt.date.today().isoformat()
        outputs = ""
        for n, step in enumerate(sc["fake"][driver], 1):
            set_turn(d, n)
            out = ""
            subs = lambda s: (s.replace("{today}", today).replace("{start}", start)  # noqa: E731
                              .replace("{plan}", (re.findall(r"ПЛАН КОММИТА ([0-9a-f]{6})", outputs) or ["000000"])[-1]))
            if "write" in step:
                path = d / step["write"]["path"]
                path.write_text(subs(step["write"]["text"]), encoding="utf-8")
            if "edit" in step:
                path = d / step["edit"]["path"]
                path.write_text(path.read_text(encoding="utf-8").replace(step["edit"]["old"], step["edit"]["new"]), encoding="utf-8")
            for cmd in step.get("run", []):
                cmd = subs(cmd)
                if step.get("direct"):
                    r = subprocess.run(cmd, shell=True, cwd=d, capture_output=True, text=True, encoding="utf-8")
                else:
                    r = subprocess.run([sys.executable, "kb/tools/eval/sh.py", cmd], cwd=d, capture_output=True,
                                       text=True, encoding="utf-8")
                out += r.stdout + r.stderr
            outputs += out
            save_reply(d, n, subs(step["reply"]).replace("{out}", out))
            snapshot(d, n)
        return check(d)
    finally:
        shutil.rmtree(d.parent, ignore_errors=True)
        shutil.rmtree(d.parent.parent / (d.parent.name + "-remote.git"), ignore_errors=True)


def selftest() -> int:
    ok = True
    print("Хорошая поддельная модель — должна пройти всё:")
    for sid in SCENARIOS:
        res = fake_run("good", sid)
        print_result(res)
        ok &= res["passed"]
    print("Плохая поддельная модель — каждый сценарий должен провалиться:")
    for sid in SCENARIOS:
        res = fake_run("bad", sid)
        print_result(res)
        ok &= not res["passed"]
    print("САМОПРОВЕРКА СТЕНДА: " + ("ПРОШЛА" if ok else "НЕ ПРОШЛА"))
    return 0 if ok else 1


# ---------- живые модели ----------

def manual(sid: str, d: Path) -> int:
    prepare(sid, d)
    sc = SCENARIOS[sid]
    print(f"Сценарий {sid}: {sc['title']}. Откройте OpenCode в {d}. После каждой реплики выполните команды ниже.")
    for n, t in enumerate(sc["turns"], 1):
        print(f"\n{n}. Напишите агенту: {t['say']}")
        print(f"   В окне разрешения отвечайте: {'разрешить' if t.get('approve') == 'yes' else 'отказать'}")
        print(f"   Потом: python3 {EVAL / 'run.py'} snapshot {d} {n}")
        print(f"   И сохраните ответ агента: python3 {EVAL / 'run.py'} reply {d} {n} <файл с ответом>")
    print(f"\nВ конце: python3 {EVAL / 'run.py'} check {d}")
    return 0


def opencode(model: str, only: str | None) -> int:
    """[Guessing] `opencode run` без окна разрешения: ask считается отказом. Для «да» в окне нужен ручной прогон."""
    results = []
    for sid in ([only] if only else SCENARIOS):
        d = Path(tempfile.mkdtemp(prefix=f"kb-oc-{sid}-")) / "work"
        prepare(sid, d)
        for n, t in enumerate(SCENARIOS[sid]["turns"], 1):
            set_turn(d, n)
            cmd = ["opencode", "run", "--agent", "kb", "-m", model] + (["--continue"] if n > 1 else []) + [t["say"]]
            r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, encoding="utf-8", timeout=900)
            save_reply(d, n, r.stdout)
            snapshot(d, n)
        res = check(d)
        print_result(res, model)
        results.append(res)
    return 0 if print_summary(results) else 1


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip())
        return 1
    cmd, args = argv[0], argv[1:]
    if cmd == "prepare":
        prepare(args[0], Path(args[1]).resolve())
        print(f"Готово: {args[1]}")
        return 0
    if cmd == "turn":
        set_turn(Path(args[0]), int(args[1]))
        return 0
    if cmd == "reply":
        text = sys.stdin.read() if args[2] == "-" else Path(args[2]).read_text(encoding="utf-8")
        save_reply(Path(args[0]), int(args[1]), text)
        return 0
    if cmd == "snapshot":
        snapshot(Path(args[0]), int(args[1]))
        return 0
    if cmd == "check":
        res = check(Path(args[0]))
        print_result(res)
        if len(args) > 1:
            Path(args[1]).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0 if res["passed"] else 1
    if cmd == "fake":
        results = [fake_run(args[0], sid) for sid in (args[1:] or SCENARIOS)]
        for res in results:
            print_result(res, args[0])
        return 0 if print_summary(results) else 1
    if cmd == "selftest":
        return selftest()
    if cmd == "manual":
        return manual(args[0], Path(args[1]).resolve())
    if cmd == "opencode":
        return opencode(args[0], args[1] if len(args) > 1 else None)
    if cmd == "summary":
        results = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args]
        for res in results:
            print_result(res)
        return 0 if print_summary(results) else 1
    print(__doc__.strip())
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
