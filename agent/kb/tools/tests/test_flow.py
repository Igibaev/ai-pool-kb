"""Сквозной тест (условие 3 цели): шаг от «Готов к взятию» до «Декомпозиты в работе» только командами kb.

Каждый этап коммитится через kb commit plan → apply при подключённом хуке, как в работе команды.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
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

PEOPLE = {"Аналитик": "analyst@example.local", "Разработчик-1": "dev1@example.local",
          "Разработчик-2": "dev2@example.local", "Тестировщик-1": "qa1@example.local"}
STEP = "kb/context/process/PM-02-pervoe-uvedomlenie.md"
REGL = "materials/2026-10-01-reglament.md"


class Flow(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        shutil.copytree(FIXTURE, self.repo / "kb")
        shutil.copytree(TOOLS, self.repo / "kb" / "tools", ignore=shutil.ignore_patterns("tests", "__pycache__"))
        (self.repo / ".gitignore").write_text(".kb/\n__pycache__/\n", encoding="utf-8")
        step = self.repo / STEP
        text = step.read_text(encoding="utf-8")
        text = text.replace("статус: Разбор", "статус: Черновик").replace("владелец: Разработчик-1", "владелец: —")
        text = re.sub(r"## История статуса\n.*", "## История статуса\n", text, flags=re.S)
        step.write_text(text, encoding="utf-8")
        self.env = {k: os.environ.get(k) for k in ("KB_ROOT", "KB_TODAY")}
        os.environ["KB_ROOT"] = str(self.repo / "kb")
        os.environ["KB_TODAY"] = "2026-10-18"
        self.cwd = os.getcwd()
        os.chdir(self.repo)
        self.git("init", "-q")
        self.as_person("Аналитик")
        self.git("add", "-A")
        self.git("commit", "-qm", "старт")
        self.kb("hooks")

    def tearDown(self):
        os.chdir(self.cwd)
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.repo)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, encoding="utf-8").stdout

    def as_person(self, name: str) -> None:
        self.git("config", "user.name", name)
        self.git("config", "user.email", PEOPLE[name])

    def kb(self, *args: str, ok: bool = True) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = kb.main(list(args))
        if ok:
            self.assertEqual(code, 0, f"kb {' '.join(args)}\n{out.getvalue()}")
        return out.getvalue()

    def commit(self, who: str) -> None:
        self.as_person(who)
        plan = self.kb("commit", "plan")
        plan_id = next(l for l in plan.splitlines() if l.startswith("ПЛАН КОММИТА")).split()[2]
        self.kb("commit", "apply", plan_id)
        self.assertEqual(self.git("status", "--porcelain").strip(), "", "после коммита остались изменения")

    def head(self, field: str, rel: str = STEP) -> str:
        for line in (self.repo / rel).read_text(encoding="utf-8").splitlines():
            if line.startswith(field + ":"):
                return line.split(":", 1)[1].split("  #")[0].strip()
        return ""

    def test_step_from_ready_to_decomposites_in_work(self):
        # 1. Аналитик готовит шаг к взятию
        self.as_person("Аналитик")
        self.kb("step", "status", "PM-02", "Готов к взятию")
        self.commit("Аналитик")
        # 2. Разработчик берёт шаг сам
        self.as_person("Разработчик-1")
        self.kb("take", "PM-02")
        self.assertEqual(self.head("статус"), "Разбор")
        self.commit("Разработчик-1")
        # 3. Разбор: факты со слов и из материалов, пробелы по критерию глубины
        gaps = self.kb("step", "gaps", "PM-02")
        self.assertIn("✗ Состояния: пусто", gaps)
        self.kb("fact", "PM-02", "Состояния", "Должник становится уведомлённым", f"{REGL}:9", "подтверждено")
        step = self.repo / STEP
        step.write_text(step.read_text(encoding="utf-8").replace(
            "| Должник | лицевой счёт | выгрузка биллинга | — |",
            "| Должник | лицевой счёт | выгрузка биллинга | materials/2026-10-01-zapros.md:5 |"), encoding="utf-8")
        # 4. Вопрос — только по «задаём»
        self.kb("ask", "send", "kanal", "шаг=PM-02", "кто=бизнес", "MVP=да", "блокирует=да",
                "откуда=materials/2026-10-01-zapros.md:17", "вопрос=Можно ли слать уведомление в мессенджер вместо SMS?")
        self.commit("Разработчик-1")
        # 5. Аналитик сортирует, собирает пакет, бизнес отвечает, ответы загружаются
        self.as_person("Аналитик")
        role = "роль=начальник абонентского отдела"
        self.kb("q", "sort", "Q-new-kanal", "кто=бизнес", role)
        self.kb("q", "sort", "Q-001", "кто=бизнес", role)
        self.kb("package", "начальник абонентского отдела")
        pkg = self.repo / "kb/journal/packages/2026-10-18-nachalnik-abonentskogo-otdela.md"
        text = pkg.read_text(encoding="utf-8")
        text = text.replace("расчётных периода? | |", "расчётных периода? | 3 и более расчётных периода |")
        text = text.replace("вместо SMS? | |", "вместо SMS? | Нет, только SMS |")
        pkg.write_text(text, encoding="utf-8")
        loaded = self.kb("package", "load", "journal/packages/2026-10-18-nachalnik-abonentskogo-otdela.md")
        self.assertIn("ЗАГРУЖЕНО ответов: 2", loaded)
        self.commit("Аналитик")
        # 6. Автор вопросов подтверждает закрытие
        self.as_person("Разработчик-1")
        self.kb("q", "close", "Q-001")
        self.kb("q", "close", "Q-new-kanal")
        self.assertEqual(self.head("статус", "kb/journal/questions/Q-001.md"), "отвечен")
        # 7. Пробелов нет — решает владелец: «мне достаточно»
        gaps = self.kb("step", "gaps", "PM-02")
        self.assertIn("Пробелов: 0", gaps)
        self.assertIn("тебе достаточно, чтобы писать спецификацию?", gaps)
        self.kb("step", "enough", "PM-02")
        self.kb("step", "status", "PM-02", "Сверка с бизнесом")
        self.commit("Разработчик-1")
        # 8. После сверки — нарезка и декомпозит
        self.kb("step", "status", "PM-02", "Нарезка")
        self.kb("dec", "new", "PM-02", "otbor", "название=Отбор должников по свежим платежам",
                "боль=Претензии уходят тем, кто уже заплатил")
        self.kb("step", "status", "PM-02", "Приёмка")
        self.commit("Разработчик-1")
        # 9. Архитектор недели принимает нарезку
        self.as_person("Разработчик-2")
        self.kb("step", "status", "PM-02", "Декомпозиты в работе")
        self.commit("Разработчик-2")

        self.assertEqual(self.head("статус"), "Декомпозиты в работе")
        self.assertEqual(self.head("достаточно для спеки"), "Разработчик-1, 2026-10-18")
        history = step.read_text(encoding="utf-8").split("## История статуса")[1]
        for move in ("Черновик → Готов к взятию — Аналитик", "Готов к взятию → Разбор — Разработчик-1 (kb take)",
                     "достаточно для спеки — Разработчик-1", "Разбор → Сверка с бизнесом",
                     "Сверка с бизнесом → Нарезка", "Нарезка → Приёмка", "Приёмка → Декомпозиты в работе — Разработчик-2"):
            self.assertIn(move, history)
        self.assertIn("Решил: Разработчик-2", self.git("log", "-1", "--format=%B"))
        self.assertEqual(int(self.git("rev-list", "--count", "HEAD").strip()), 8)   # старт + 7 этапов
        check = self.kb("check")
        self.assertIn("Итог: ошибок 0", check)
        report = self.kb("report", "morning", "--since", self.git("rev-list", "--max-parents=0", "HEAD").strip())
        self.assertIn("коммитов 7 · людей 3", report)
        self.assertIn("### Закрыты — 2", report)
        self.assertIn("статус Приёмка → Декомпозиты в работе", report)


if __name__ == "__main__":
    unittest.main()
