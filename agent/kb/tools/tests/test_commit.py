"""Коммит только по «да» (порция 3): kb commit plan / apply, pre-commit, права OpenCode.

Имя теста — номер случая из oshibki.md (C01–C07).
"""
from __future__ import annotations

import contextlib
import fnmatch
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixture"
AGENT = TOOLS.parent.parent
sys.path.insert(0, str(TOOLS))

import kb  # noqa: E402

STEP = "kb/context/process/PM-02-pervoe-uvedomlenie.md"
REGL = "materials/2026-10-01-reglament.md"


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    return r.stdout + r.stderr


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        shutil.copytree(FIXTURE, self.repo / "kb")
        (self.repo / ".gitignore").write_text(".kb/\n", encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Разработчик-1")
        git(self.repo, "config", "user.email", "dev1@example.local")
        git(self.repo, "config", "core.hooksPath", str(TOOLS / "hooks"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "старт")
        self.env = {k: os.environ.get(k) for k in ("KB_ROOT", "KB_TODAY")}
        os.environ["KB_ROOT"] = str(self.repo / "kb")
        os.environ["KB_TODAY"] = "2026-10-18"
        self.cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self.cwd)
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp)

    def run_kb(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = kb.main(list(args))
        return code, out.getvalue()

    def add_fact(self) -> None:
        code, out = self.run_kb("fact", "PM-02", "Вход", f"- Должник — долг за 3 периода (ист: {REGL}:3; подтверждено)")
        self.assertEqual(code, 0, out)

    def plan_id(self, out: str) -> str:
        line = next(l for l in out.splitlines() if l.startswith("ПЛАН КОММИТА "))
        return line.split()[2]

    def commits(self) -> int:
        return int(git(self.repo, "rev-list", "--count", "HEAD").strip())


class Commit(Repo):
    def test_plan_then_apply(self):
        self.add_fact()
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 0, out)
        self.assertIn("Новые факты:", out)
        self.assertIn("PM-02 «Вход»: - Должник — долг за 3 периода", out)
        self.assertIn("Решил: Разработчик-1", out)
        self.assertEqual(self.commits(), 1, "plan не должен коммитить")
        code, out = self.run_kb("commit", "apply", self.plan_id(out))
        self.assertEqual(code, 0, out)
        self.assertIn("ЗАКОММИЧЕНО", out)
        self.assertEqual(self.commits(), 2)
        self.assertIn("Решил: Разработчик-1", git(self.repo, "log", "-1", "--format=%B"))
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "")

    def test_plan_shows_status_change_and_new_question(self):
        path = self.repo / STEP
        path.write_text(path.read_text(encoding="utf-8").replace("статус: Разбор", "статус: Сверка с бизнесом"),
                        encoding="utf-8")
        q = (self.repo / "kb/journal/questions/Q-001.md").read_text(encoding="utf-8")
        (self.repo / "kb/journal/questions/Q-new-shlyuz.md").write_text(
            q.replace("id: Q-001", "id: Q-new-shlyuz").replace("# Q-001 С какого", "# Q-new-shlyuz С какого"),
            encoding="utf-8")
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 0, out)
        self.assertIn("Смена статуса:\n  PM-02: Разбор → Сверка с бизнесом", out)
        self.assertIn("Новые вопросы:\n  Q-new-shlyuz С какого", out)

    def test_C02(self):
        self.add_fact()
        _, out = self.run_kb("commit", "plan")
        plan = self.plan_id(out)
        path = self.repo / STEP
        path.write_text(path.read_text(encoding="utf-8") + "\nдописано после показа\n", encoding="utf-8")
        code, out = self.run_kb("commit", "apply", plan)
        self.assertEqual(code, 1, out)
        self.assertIn("с момента показа изменилось", out)
        self.assertIn("PM-02-pervoe-uvedomlenie.md", out)
        self.assertEqual(self.commits(), 1)

    def test_C03(self):
        local = self.repo / ".kb" / "questions"
        local.mkdir(parents=True)
        (local / "q-porog-dolga.md").write_text("черновик\n", encoding="utf-8")
        git(self.repo, "add", "-f", ".kb/questions/q-porog-dolga.md")
        out = git(self.repo, "commit", "-m", "личное")
        self.assertIn("Личные файлы в git не попадают", out)
        self.assertEqual(self.commits(), 1)

    def test_C04(self):
        path = self.repo / "kb" / REGL
        path.write_text(path.read_text(encoding="utf-8") + "правка\n", encoding="utf-8")
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 1, out)
        self.assertIn("Материалы не правятся", out)
        git(self.repo, "add", "-A")
        out = git(self.repo, "commit", "-m", "правка материала")
        self.assertIn("Материалы не правятся", out)
        self.assertEqual(self.commits(), 1)

    def test_C04_new_material_allowed(self):
        (self.repo / "kb/materials/2026-10-18-novyj.md").write_text("новый материал\n", encoding="utf-8")
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 0, out)

    def test_C05(self):
        views = self.repo / "kb/journal/views"
        views.mkdir(parents=True)
        (views / "PM-02.md").write_text("руками\n", encoding="utf-8")
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 1, out)
        self.assertIn("Списки строит инструмент", out)

    def test_plan_refused_on_check_errors(self):
        path = self.repo / STEP
        path.write_text(path.read_text(encoding="utf-8").replace("## Состояния\n", "## Состояния\n- Без источника\n"),
                        encoding="utf-8")
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 1, out)
        self.assertIn("`kb check` нашёл ошибки", out)
        self.assertIn("Факт без источника и статуса", out)

    def test_junk_files_not_in_plan(self):
        junk = self.repo / "kb/tools/__pycache__"
        junk.mkdir(parents=True)
        (junk / "kb.cpython-313.pyc").write_bytes(b"x")
        (self.repo / "kb/.DS_Store").write_bytes(b"x")
        self.add_fact()
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 0, out)
        self.assertIn("файлов: 1", out)
        self.assertNotIn("pyc", out)

    def test_apply_unknown_plan(self):
        code, out = self.run_kb("commit", "apply", "abcdef")
        self.assertEqual(code, 1)
        self.assertIn("плана abcdef нет", out)

    def test_nothing_to_commit(self):
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 1)
        self.assertIn("Нечего коммитить", out)

    def test_unknown_person_cannot_plan(self):
        git(self.repo, "config", "user.email", "nobody@example.local")
        git(self.repo, "config", "user.name", "Кто-то")
        self.add_fact()
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 1)
        self.assertIn("вас нет в `team.md`", out)

    def test_hook_stops_human_commit_with_errors(self):
        path = self.repo / STEP
        path.write_text(path.read_text(encoding="utf-8").replace("## Состояния\n", "## Состояния\n- Без источника\n"),
                        encoding="utf-8")
        git(self.repo, "add", "-A")
        out = git(self.repo, "commit", "-m", "руками")
        self.assertIn("kb: коммит остановлен", out)
        self.assertEqual(self.commits(), 1)


def decide(rules: dict[str, str], value: str) -> str:
    """Как OpenCode: шаблоны со * и ?, побеждает последнее совпавшее правило."""
    result = None
    for pattern, action in rules.items():
        if fnmatch.fnmatchcase(value, pattern):
            result = action
    return result


class OpenCodePermissions(unittest.TestCase):
    perms = json.loads((AGENT / "opencode.json").read_text(encoding="utf-8"))["permission"]

    def bash(self, command: str) -> str:
        return decide(self.perms["bash"], command)

    def edit(self, path: str) -> str:
        return decide(self.perms["edit"], path)

    def test_C01(self):
        for cmd in ("git commit -m x", "git commit -am x", "git add . && git commit -m x",
                    "git -c core.hooksPath=/dev/null commit -m x", "git commit --no-verify -m x"):
            self.assertEqual(self.bash(cmd), "deny", cmd)

    def test_C06(self):
        for cmd in ("git push", "git push origin context", "git push --force", "git status; git push"):
            self.assertEqual(self.bash(cmd), "deny", cmd)

    def test_C07(self):
        for cmd in ("git remote set-url origin x", "git remote add other x", "git config user.email x"):
            self.assertEqual(self.bash(cmd), "deny", cmd)

    def test_apply_needs_human_yes(self):
        self.assertEqual(self.bash("python3 kb/tools/kb.py commit apply 1a2b3c"), "ask")
        self.assertEqual(self.bash("python3 kb/tools/kb.py ask send q-porog"), "ask")
        self.assertEqual(self.bash("python3 kb/tools/kb.py step enough PM-02"), "ask")

    def test_reading_commands_allowed(self):
        for cmd in ("python3 kb/tools/kb.py check", "python3 kb/tools/kb.py commit plan",
                    "python3 kb/tools/kb.py fact PM-02 Вход x", "git status", "git diff"):
            self.assertEqual(self.bash(cmd), "allow", cmd)

    def test_unknown_commands_ask(self):
        for cmd in ("rm -rf kb", "python3 script.py", "curl http://x", "KB_ROOT=x python3 kb/tools/kb.py commit apply 1"):
            self.assertEqual(self.bash(cmd), "ask", cmd)

    def test_agent_cannot_edit_own_rules_and_tools(self):
        for path in ("AGENTS.md", "opencode.json", "kb/rules/gates.md", "kb/tools/kb.py",
                     "kb/tools/hooks/pre-commit", "kb/journal/views/PM-02.md", ".git/hooks/pre-commit"):
            self.assertEqual(self.edit(path), "deny", path)
        self.assertEqual(self.edit("kb/materials/2026-10-18-novyj.md"), "ask")
        self.assertEqual(self.edit("kb/context/process/PM-02-x.md"), "allow")

    def test_only_own_subagents(self):
        self.assertEqual(decide(self.perms["task"], "general"), "deny")
        self.assertEqual(decide(self.perms["task"], "kb-vstrecha"), "allow")


if __name__ == "__main__":
    unittest.main()
