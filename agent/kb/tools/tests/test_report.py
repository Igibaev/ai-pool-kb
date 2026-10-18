"""Утренний отчёт (порция 7): полный, по шагам и людям, все вопросы и ответы."""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixture"
sys.path.insert(0, str(TOOLS))

import kb  # noqa: E402

PEOPLE = {"Разработчик-1": "dev1@example.local", "Разработчик-2": "dev2@example.local",
          "Аналитик": "analyst@example.local"}
REGL = "materials/2026-10-01-reglament.md"


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp
        self.root = self.repo / "kb"
        shutil.copytree(FIXTURE, self.root)
        self.env = {k: os.environ.get(k) for k in ("KB_ROOT", "KB_TODAY", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")}
        os.environ["KB_ROOT"] = str(self.root)
        os.environ["KB_TODAY"] = "2026-10-18"
        self.cwd = os.getcwd()
        os.chdir(self.repo)
        self.git("init", "-q")
        self.commit_as("Аналитик", "старт", "2026-10-10T09:00:00")
        self.start = self.git("rev-parse", "HEAD").strip()
        self.history()

    def tearDown(self):
        os.chdir(self.cwd)
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True,
                              encoding="utf-8").stdout

    def as_person(self, name: str) -> None:
        self.git("config", "user.name", name)
        self.git("config", "user.email", PEOPLE[name])

    def commit_as(self, name: str, message: str, when: str = "2026-10-18T08:00:00") -> None:
        self.as_person(name)
        os.environ["GIT_AUTHOR_DATE"] = os.environ["GIT_COMMITTER_DATE"] = when
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run_kb(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = kb.main(list(args))
        return code, out.getvalue()

    def history(self) -> None:
        self.as_person("Разработчик-1")
        self.run_kb("fact", "PM-02", "Вход", f"- Должник — долг за 3 периода (ист: {REGL}:3; подтверждено)")
        self.run_kb("fact", "PM-02", "Выход", f"- Журнал уведомлений: дата, способ, результат (ист: {REGL}:9; подтверждено)")
        self.commit_as("Разработчик-1", "PM-02: факты")
        self.as_person("Разработчик-2")
        self.run_kb("ask", "send", "stoimost-sms", "шаг=PM-02", "кто=смежник", "MVP=да", "блокирует=нет",
                    "откуда=materials/2026-10-01-zapros.md:7", "вопрос=Сколько стоит одна SMS?")
        self.commit_as("Разработчик-2", "вопрос")
        self.as_person("Аналитик")
        self.run_kb("q", "sort", "Q-new-stoimost-sms", "кто=бизнес")
        (self.root / "context/glossary.md").write_text("| Должник | Абонент с долгом |\n", encoding="utf-8")
        self.commit_as("Аналитик", "сортировка и глоссарий")
        self.as_person("Разработчик-1")
        self.run_kb("step", "status", "PM-02", "Сверка с бизнесом", "--reason", "бизнес ответит на сверке")
        self.commit_as("Разработчик-1", "PM-02: в сверку")

    def report(self) -> str:
        code, out = self.run_kb("report", "morning", "--since", self.start)
        self.assertEqual(code, 0, out)
        return out

    def test_every_changed_file_is_in_report(self):
        out = self.report()
        changed = self.git("diff", "--name-only", f"{self.start}..HEAD").split()
        self.assertTrue(changed)
        for path in changed:
            self.assertIn(path[len("kb/"):], out, f"{path} нет в отчёте")
        self.assertIn(f"## Все изменённые файлы — {len(changed)}", out)

    def test_steps_and_people(self):
        out = self.report()
        self.assertIn("коммитов 4 · людей 3", out)
        self.assertIn("### PM-02 Первое уведомление должника", out)
        self.assertIn("- Разработчик-1: фактов +2 («Вход» 1, «Выход» 1); статус Разбор → Сверка с бизнесом", out)
        self.assertIn("| Разработчик-1 | 2 | PM-02 | 2 | 0 | 0 | 0 |", out)
        self.assertIn("| Разработчик-2 | 1 | — | 0 | 1 | 0 | 0 |", out)

    def test_questions(self):
        out = self.report()
        self.assertIn("### Новые — 1", out)
        self.assertIn("- Q-new-stoimost-sms — Сколько стоит одна SMS? — Разработчик-2; кто отвечает: бизнес", out)
        self.assertIn("### Рассортированы — 1", out)
        self.assertIn("### Ждут сортировки аналитиком сейчас — 0", out)

    def test_stale_questions(self):
        out = self.report()
        self.assertIn("### Без движения больше 2 дней — 1", out)
        self.assertIn("- Q-001 — С какого долга", out)
        self.assertIn("без движения с 2026-10-10", out)

    def test_save_writes_report_file(self):
        code, out = self.run_kb("report", "morning", "--since", self.start, "--save")
        self.assertEqual(code, 0)
        self.assertTrue((self.root / "journal/reports/2026-10-18.md").is_file())

    def test_deleted_file_reported(self):
        (self.root / "context/glossary.md").unlink()
        self.commit_as("Аналитик", "удалил глоссарий")
        out = self.report()
        self.assertIn("- context/glossary.md — Аналитик", out)


if __name__ == "__main__":
    unittest.main()
