#!/usr/bin/env python3
"""Проверка цели «агент готов к пилоту команды» — пять условий из cel.md.

Запуск из корня репозитория: python3 sborka-agenta/check_goal.py [--no-smoke]
Python 3.9 ищется через `uv python find 3.9` или переменную KB_PY39.

1. Покрытие: у каждой строки pokrytie.md есть механизм и существующий тест; у каждого случая
   oshibki.md есть тест с тем же номером; все тесты зелёные на Python 3.9 и 3.13.
2. Мутации: выключаем по одной проверку жёстких ворот в копии агента — падает хотя бы один тест.
3. Сквозной тест флоу «Готов к взятию» → «Декомпозиты в работе» зелёный.
4. Стенд: самопроверка (хорошая модель проходит, плохая проваливает) и сохранённые прогоны Haiku и Sonnet.
5. Инструкция первого запуска проходит с чистого клона (smoke.sh). Плюс: в ядре нет слов продукта и моделей.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
AGENT = REPO / "agent"
TESTS = AGENT / "kb" / "tools" / "tests"

# (что выключаем, файл в agent/, было, стало). «Было» должно встречаться в файле ровно один раз.
F = "if False:"
MUTATIONS = [
    # Коммит по «да»
    ("отпечаток плана при commit apply", "kb/tools/kbgit.py", "    if changed:\n        names", "    if False:\n        names"),
    ("личное из .kb/ в коммите", "kb/tools/kbgit.py", 'if path.startswith(".kb/") or "/.kb/" in path:', F),
    ("правка материалов в коммите", "kb/tools/kbgit.py",
     'elif inner.startswith("materials/") and code not in ("?", "??", "A"):', "elif False:"),
    ("списки вопросов руками", "kb/tools/kbgit.py",
     'if full.exists() and views.get(name) != full.read_text(encoding="utf-8"):', F),
    ("_deps.md руками", "kb/tools/kbgit.py",
     'if full.exists() and full.read_text(encoding="utf-8") != kbdeps.render(kb):', F),
    ("план коммита: ошибки kb check", "kb/tools/kbgit.py",
     '    if rep.errors():\n        problems.append(f"`kb check` нашёл ошибки', '    if False:\n        problems.append(f"`kb check` нашёл ошибки'),
    ("pre-commit: ошибки kb check", "kb/tools/kbgit.py",
     '    if rep.errors():\n        problems.append(f"`kb check`: ошибок', '    if False:\n        problems.append(f"`kb check`: ошибок'),
    ("хук pre-commit", "kb/tools/hooks/pre-commit", 'exec python3 "$(dirname "$0")/../kb.py" precommit', "exit 0"),
    ("OpenCode: git commit deny", "opencode.json", '"git commit*": "deny"', '"git commit*": "allow"'),
    ("OpenCode: git push deny", "opencode.json", '"git push*": "deny"', '"git push*": "allow"'),
    ("OpenCode: commit apply ask", "opencode.json", '"python3 kb/tools/kb.py commit apply*": "ask"',
     '"python3 kb/tools/kb.py commit apply*": "allow"'),
    ("OpenCode: git remote set-url deny", "opencode.json", '"git remote set-url*": "deny"', '"git remote set-url*": "allow"'),
    ("OpenCode: правка kb/tools deny", "opencode.json", '"kb/tools/*": "deny"', '"kb/tools/*": "allow"'),
    ("OpenCode: правка AGENTS.md deny", "opencode.json", '"AGENTS.md": "deny"', '"AGENTS.md": "allow"'),
    ("OpenCode: MCP merge выключен", "opencode.json", '"*merge*": false', '"*merge*": true'),
    ("OpenCode: чужие субагенты deny", "opencode.json", '"task": {\n      "*": "deny"', '"task": {\n      "*": "allow"'),
    # Вопрос по «задаём»
    ("OpenCode: ask send ask", "opencode.json", '"python3 kb/tools/kb.py ask send*": "ask"',
     '"python3 kb/tools/kb.py ask send*": "allow"'),
    ("поле «задаём» у вопроса", "kb/tools/kbcheck.py", 'if not asked or asked == "—":', F),
    ("закрывает только автор", "kb/tools/kbquestions.py", "    if person != author:\n        print",
     "    if False:\n        print"),
    # «Мне достаточно» — только владелец
    ("OpenCode: step enough ask", "opencode.json", '"python3 kb/tools/kb.py step enough*": "ask"',
     '"python3 kb/tools/kb.py step enough*": "allow"'),
    ("«достаточно» говорит владелец", "kb/tools/kbsteps.py",
     '    if person != owner:\n        print(f"ОШИБКА: «Достаточно»', '    if False:\n        print(f"ОШИБКА: «Достаточно»'),
    ("в нарезку — только после «достаточно»", "kb/tools/kbsteps.py",
     'if new == "Нарезка" and head.get("достаточно для спеки", "—") in ("", "—"):', F),
    ("поле «достаточно» руками", "kb/tools/kbcheck.py",
     'if f"- {date} достаточно для спеки — {name} (kb step enough)" not in lines:', F),
    ("статус шага руками", "kb/tools/kbcheck.py", "if status and moves and moves[-1].strip() != status:", F),
    ("вперёд — на один статус", "kb/tools/kbsteps.py", "if i_new > i_old + 1:", F),
    ("статус меняет владелец", "kb/tools/kbsteps.py",
     'return None if person == owner else f"Статус шага меняет владелец: {owner}"', "return None"),
    # Факт — только с источником
    ("ответ RAG не выше «оценки»", "kb/tools/kbfacts.py",
     'if path.startswith("materials/ai/") and status == "подтверждено":', F),
    ("«со слов» — человек из team.md", "kb/tools/kbfacts.py", "if not team.knows(who):", F),
    ("дата не в будущем", "kb/tools/kbfacts.py", "if date > kbbase.today():", F),
    ("D «предложено» не выше «оценки»", "kb/tools/kbfacts.py", 'if state != "принято" and status == "подтверждено":', F),
    ("строка материала существует", "kb/tools/kbfacts.py", "if int(n) < 1 or int(n) > total:", F),
    ("только русский", "kb/tools/kbfacts.py", "    found = CJK.findall(text)", "    found = []"),
    ("персональные данные", "kb/tools/kbcheck.py", "            if m:\n                rep.add(path, i + 1, Problem(level",
     "            if False:\n                rep.add(path, i + 1, Problem(level"),
    ("глоссарий: термин дважды", "kb/tools/kbglossary.py", "if key in seen and seen[key] != term:", F),
    # Находки
    ("находки переносит владелец", "kb/tools/kbnotes.py",
     '    if person != owner:\n        print(f"ОШИБКА: находки', '    if False:\n        print(f"ОШИБКА: находки'),
    ("чужой файл находок в коммите", "kb/tools/kbnotes.py", "        if person != owner:\n            problems.append",
     "        if False:\n            problems.append"),
    # Нет кода без принятой спеки
    ("Сборка без спеки", "kb/tools/kbdelivery.py", "if not accepted and not early_slice:", F),
    ("спеку принимает архитектор", "kb/tools/kbdelivery.py",
     '    if person != architect:\n        print(f"ОШИБКА: спеку принимает', '    if False:\n        print(f"ОШИБКА: спеку принимает'),
    ("ранний срез при неподтверждённом", "kb/tools/kbdelivery.py", "    if left:", "    if False:"),
    ("поле «спека принята» руками", "kb/tools/kbcheck.py",
     "if accepted not in (\"\", \"—\") and f\"спека принята — {accepted.rsplit(', ', 1)[0]} (kb spec accept)\" not in \"\\n\".join(lines):", F),
    ("Приёмка без декомпозитов", "kb/tools/kbsteps.py",
     'if new in ("Приёмка", "Декомпозиты в работе") and i_new > i_old and not has_dec:', F),
    ("мост кода: gate", "kb/tools/code-kit/kbx.py",
     "    print(f\"НЕЛЬЗЯ: нет кода без принятой спеки. {ident}: спека {head.get('спека', '—')}, не принята\")\n    return 1",
     "    return 0"),
    ("мост кода: только чтение", "kb/tools/code-kit/kbx.py", "if not argv or argv[0] not in READ or", "if not argv or"),
    ("CI кода: папка kb/", "kb/tools/code-kit/ci-no-kb.sh", "  exit 1", "  exit 0"),
    ("code-kit: папка с kb/", "kb/tools/kbcodekit.py", 'if (code / "kb").exists():', F),
    ("агент кода: git push deny", "kb/tools/kbcodekit.py", '"git push*": "deny"', '"git push*": "allow"'),
    # RAG
    ("RAG выбирает человек", "kb/tools/kbrag.py", 'if not opts.get("rag"):', F),
    ("проект портала выбирает человек", "kb/tools/kbrag.py", 'if not opts.get("proekt"):', F),
    ("ключ только из окружения", "kb/tools/kbrag.py", "    if not value:\n        raise RagError", "    if False:\n        raise RagError"),
]

# Ядро универсально: ни продукта, ни моделей. Тестовая база и стенд — не ядро.
CORE = ["AGENTS.md", "kb/rules", "kb/tools", "kb/docs", ".opencode"]
CORE_SKIP = ["kb/tools/tests", "kb/tools/eval", "__pycache__"]
PRODUCT_WORDS = re.compile(r"debt-enforcement|должник|абонент|SMS|пеня|GLM|Qwen|Instruct|Haiku|Sonnet|Claude|Ornith",
                           re.I)


class Result:
    def __init__(self):
        self.rows = []

    def add(self, cond: str, ok: bool, text: str) -> None:
        self.rows.append((cond, ok, text))
        print(f"  {'✓' if ok else '✗'} {text}", flush=True)

    def ok(self, cond: str) -> bool:
        return all(ok for c, ok, _ in self.rows if c == cond)


def run_tests(python: str, agent: Path, pattern: str = "test_*.py", timeout: int = 900) -> tuple[bool, str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([python, "-m", "unittest", "discover", "-s", str(agent / "kb/tools/tests"), "-p", pattern],
                       cwd=agent, capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)
    tail = (r.stdout + r.stderr).strip().splitlines()
    summary = " ".join(l for l in tail[-3:] if l.startswith(("Ran", "OK", "FAILED")))
    return r.returncode == 0, summary


def python39() -> str | None:
    if os.environ.get("KB_PY39"):
        return os.environ["KB_PY39"]
    try:
        r = subprocess.run(["uv", "python", "find", "3.9"], capture_output=True, text=True)
        return r.stdout.strip() or None
    except FileNotFoundError:
        return shutil.which("python3.9")


def test_ids() -> tuple[set[str], set[str]]:
    names = set()
    for f in TESTS.glob("test_*.py"):
        names |= set(re.findall(r"def (test_\w+)", f.read_text(encoding="utf-8")))
    ids = {m.group(1) for n in names for m in [re.match(r"test_([A-Z]\d{2})", n)] if m}
    return names, ids


def cond1(res: Result) -> None:
    print("1. Покрытие, тест на каждую ошибку, Python 3.9 и 3.13")
    names, ids = test_ids()
    rows = [l for l in (HERE / "pokrytie.md").read_text(encoding="utf-8").splitlines()
            if re.match(r"^\| \d+ \|", l)]
    bad = []
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        kind, tests = cells[-2], cells[-1]
        refs = [t.strip() for t in tests.split(",") if t.strip()]
        missing = [t for t in refs if t not in ids and t not in names]
        if not kind.startswith("Механизм") or not refs or missing or re.search(r"потом|не в этой порции", row, re.I):
            bad.append(f"{cells[1][:40]} — вид «{kind}», нет тестов: {missing or 'не указаны'}")
    res.add("1", not bad, f"pokrytie.md: строк {len(rows)}, без механизма или теста: {len(bad)}")
    for b in bad:
        print(f"      {b}")
    cases = re.findall(r"^\| ([A-Z]\d{2}) \|", (TESTS / "oshibki.md").read_text(encoding="utf-8"), re.M)
    lost = [c for c in cases if c not in ids]
    res.add("1", not lost and len(cases) == len(set(cases)),
            f"oshibki.md: случаев {len(cases)}, без теста: {lost or 0}")
    cel = (HERE / "cel.md").read_text(encoding="utf-8")
    scope = re.findall(r"^\| (О\d+) \|.*\| ([^|]+) \|$", cel, re.M)
    undone = [o for o, state in scope if state.strip() != "готово"]
    res.add("1", len(scope) == 17 and not undone, f"cel.md: пунктов объёма {len(scope)}, не готово: {undone or 0}")
    ok, summary = run_tests(sys.executable, AGENT)
    res.add("1", ok, f"тесты на Python {sys.version_info.major}.{sys.version_info.minor}: {summary}")
    py39 = python39()
    if py39:
        ok, summary = run_tests(py39, AGENT)
        res.add("1", ok, f"тесты на Python 3.9 ({py39}): {summary}")
    else:
        res.add("1", False, "Python 3.9 не найден: задайте KB_PY39")


def mutate_one(m: tuple[str, str, str, str]) -> tuple[str, str]:
    name, rel, old, new = m
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "agent"
        shutil.copytree(AGENT, copy, ignore=shutil.ignore_patterns("__pycache__", ".kb", ".eval"))
        path = copy / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            return name, f"УСТАРЕЛА: в {rel} «было» встречается {text.count(old)} раз"
        path.write_text(text.replace(old, new), encoding="utf-8")
        ok, summary = run_tests(sys.executable, copy)
        return name, "ПРОПУЩЕНА: тесты зелёные" if ok else f"поймана ({summary})"


def cond2(res: Result) -> None:
    print(f"2. Мутации жёстких ворот: {len(MUTATIONS)}")
    with ThreadPoolExecutor(max_workers=max(1, min(4, os.cpu_count() or 1))) as pool:
        for name, verdict in pool.map(mutate_one, MUTATIONS):
            res.add("2", verdict.startswith("поймана"), f"{name}: {verdict}")


def cond3(res: Result) -> None:
    print("3. Сквозной флоу")
    ok, summary = run_tests(sys.executable, AGENT, "test_flow.py")
    res.add("3", ok, f"test_flow: {summary}")


def cond4(res: Result) -> None:
    print("4. Стенд")
    run = [sys.executable, str(AGENT / "kb/tools/eval/run.py")]
    r = subprocess.run(run + ["selftest"], cwd=AGENT, capture_output=True, text=True, encoding="utf-8")
    res.add("4", r.returncode == 0 and "САМОПРОВЕРКА СТЕНДА: ПРОШЛА" in r.stdout,
            "самопроверка: хорошая поддельная модель проходит, плохая проваливает каждый сценарий")
    results = sorted(HERE.glob("eval-*/"))
    if not results:
        res.add("4", False, "нет сохранённых прогонов на моделях: sborka-agenta/eval-*/")
        return
    last = results[-1]
    for model in ("haiku", "sonnet"):
        files = sorted(last.glob(f"{model}-E*.json"))
        r = subprocess.run(run + ["summary", *map(str, files)], cwd=AGENT, capture_output=True, text=True, encoding="utf-8")
        line = next((l for l in r.stdout.splitlines() if l.startswith("ИТОГ")), "нет итога")
        res.add("4", len(files) == 8 and "ЦЕЛЬ ВЫПОЛНЕНА" in line, f"{model} ({last.name}, сценариев {len(files)}): {line}")


def cond5(res: Result, smoke: bool) -> None:
    print("5. Инструкция с чистого клона и универсальность ядра")
    hits = []
    for part in CORE:
        base = AGENT / part
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for f in files:
            rel = f.relative_to(AGENT).as_posix()
            if any(s in rel for s in CORE_SKIP) or f.suffix not in (".md", ".py", ".json", ".sh", ""):
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if PRODUCT_WORDS.search(line) and not line.startswith("NOT_SOURCES = "):   # общий список слов ИИ
                    hits.append(f"{rel}:{n}: {line.strip()[:80]}")
    res.add("5", not hits, f"ядро без слов продукта и моделей: совпадений {len(hits)}")
    for h in hits[:20]:
        print(f"      {h}")
    if not smoke:
        res.add("5", False, "smoke пропущен (--no-smoke)")
        return
    r = subprocess.run(["sh", str(HERE / "smoke.sh")], capture_output=True, text=True, encoding="utf-8", timeout=600)
    line = next((l for l in r.stdout.splitlines() if l.startswith("SMOKE")), (r.stdout + r.stderr).strip()[-200:])
    res.add("5", r.returncode == 0, f"smoke.sh: {line}")


def main() -> int:
    smoke = "--no-smoke" not in sys.argv
    res = Result()
    cond1(res)
    cond2(res)
    cond3(res)
    cond4(res)
    cond5(res, smoke)
    print()
    for c in "12345":
        print(f"Условие {c}: {'ВЫПОЛНЕНО' if res.ok(c) else 'НЕ ВЫПОЛНЕНО'}")
    done = all(res.ok(c) for c in "12345")
    print("ЦЕЛЬ: " + ("ВСЕ ПЯТЬ УСЛОВИЙ ВЫПОЛНЕНЫ. Прогон на GLM и Instruct — за владельцем" if done else "НЕ ДОСТИГНУТА"))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
