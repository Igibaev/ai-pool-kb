"""Шаг (порция 5): критерий глубины, «мне достаточно», статусы, взятие. Имена — S04–S10 из oshibki.md."""
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

STEP = "context/process/PM-02-pervoe-uvedomlenie.md"
PEOPLE = {"Разработчик-1": "dev1@example.local", "Разработчик-2": "dev2@example.local",
          "Аналитик": "analyst@example.local", "Тестировщик-1": "qa1@example.local"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "kb"
        shutil.copytree(FIXTURE, self.root)
        subprocess.run(["git", "init", "-q", str(self.tmp)], check=True)
        self.as_person("Разработчик-1")
        self.env = {k: os.environ.get(k) for k in ("KB_ROOT", "KB_TODAY")}
        os.environ["KB_ROOT"] = str(self.root)
        os.environ["KB_TODAY"] = "2026-10-18"
        self.cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp)

    def as_person(self, name: str) -> None:
        for key, value in (("user.name", name), ("user.email", PEOPLE[name])):
            subprocess.run(["git", "-C", str(self.tmp), "config", key, value], check=True)

    def run_kb(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = kb.main(list(args))
        return code, out.getvalue()

    def step(self) -> str:
        return (self.root / STEP).read_text(encoding="utf-8")

    def set_head(self, key: str, value: str, rel: str = STEP) -> None:
        path = self.root / rel
        lines = path.read_text(encoding="utf-8").splitlines()
        lines = [f"{key}: {value}" if l.split(":", 1)[0] == key else l for l in lines]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def new_step(self, ident: str, status: str, owner: str = "—", people: str = "1") -> str:
        text = self.step().replace("id: PM-02", f"id: {ident}").replace("# PM-02", f"# {ident}")
        rel = f"context/process/{ident}-novyj.md"
        (self.root / rel).write_text(text, encoding="utf-8")
        self.set_head("статус", status, rel)
        self.set_head("владелец", owner, rel)
        self.set_head("людей", people, rel)
        return rel

    def close_q001(self) -> None:
        path = self.root / "journal/questions/Q-001.md"
        path.write_text(path.read_text(encoding="utf-8").replace("блокирует: да", "блокирует: нет"), encoding="utf-8")


class Gaps(Base):
    def test_gaps_names_empty_and_unsourced(self):
        code, out = self.run_kb("step", "gaps", "PM-02")
        self.assertEqual(code, 0)
        self.assertIn("✗ Состояния: пусто", out)
        self.assertIn("~ Сущности и их поля: строк 4, без источника 1", out)
        self.assertIn("Открытые блокирующие вопросы: Q-001", out)
        self.assertIn("Это подсказка, а не ворота", out)
        self.assertNotIn("тебе достаточно", out)

    def test_no_gaps_suggests_owner_question(self):
        text = self.step().replace("## Состояния\n", "## Состояния\n- Должник уведомлён (ист: materials/2026-10-01-reglament.md:9; подтверждено)\n")
        text = text.replace("| Должник | лицевой счёт | выгрузка биллинга | — |",
                            "| Должник | лицевой счёт | выгрузка биллинга | materials/2026-10-01-zapros.md:5 |")
        (self.root / STEP).write_text(text, encoding="utf-8")
        code, out = self.run_kb("step", "gaps", "PM-02")
        self.assertIn("Пробелов: 0", out)
        self.assertIn("тебе достаточно, чтобы писать спецификацию?", out)


class Enough(Base):
    def test_owner_says_enough(self):
        code, out = self.run_kb("step", "enough", "PM-02")
        self.assertEqual(code, 0, out)
        self.assertIn("достаточно для спеки: Разработчик-1, 2026-10-18", self.step())
        self.assertIn("- 2026-10-18 достаточно для спеки — Разработчик-1 (kb step enough)", self.step())
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_S08(self):
        self.as_person("Разработчик-2")
        code, out = self.run_kb("step", "enough", "PM-02")
        self.assertEqual(code, 1)
        self.assertIn("«Достаточно» говорит владелец шага: Разработчик-1", out)
        self.assertIn("достаточно для спеки: —", self.step())

    def test_S09(self):
        self.set_head("достаточно для спеки", "Разработчик-1, 2026-10-18")
        code, out = self.run_kb("check")
        self.assertEqual(code, 1)
        self.assertIn("ставит только `kb step enough`", out)


class Status(Base):
    def test_S04(self):
        self.close_q001()
        self.run_kb("step", "status", "PM-02", "Сверка с бизнесом")
        code, out = self.run_kb("step", "status", "PM-02", "Нарезка")
        self.assertEqual(code, 1)
        self.assertIn("в нарезку — после слов владельца «мне достаточно»", out)
        self.run_kb("step", "enough", "PM-02")
        code, out = self.run_kb("step", "status", "PM-02", "Нарезка")
        self.assertEqual(code, 0, out)

    def test_S05(self):
        code, out = self.run_kb("step", "status", "PM-02", "Нарезка")
        self.assertEqual(code, 1)
        self.assertIn("Вперёд — только на один статус. Следующий: «Сверка с бизнесом»".lower(), out.lower())

    def test_S06(self):
        self.close_q001()
        self.run_kb("step", "enough", "PM-02")
        self.run_kb("step", "status", "PM-02", "Сверка с бизнесом")
        self.run_kb("step", "status", "PM-02", "Нарезка")
        code, out = self.run_kb("step", "status", "PM-02", "Разбор")
        self.assertEqual(code, 1)
        self.assertIn("назад — только с причиной", out)
        code, out = self.run_kb("step", "status", "PM-02", "Разбор", "--reason", "бизнес поправил вход")
        self.assertEqual(code, 0, out)
        self.assertIn("Нарезка → Разбор — Разработчик-1, причина: бизнес поправил вход", self.step())

    def test_S07(self):
        code, out = self.run_kb("step", "status", "PM-02", "Сверка с бизнесом")
        self.assertEqual(code, 1)
        self.assertIn("открыт блокирующий вопрос Q-001", out)
        code, out = self.run_kb("step", "status", "PM-02", "Сверка с бизнесом", "--reason", "бизнес ответит на сверке")
        self.assertEqual(code, 0, out)
        self.assertIn("причина: бизнес ответит на сверке", self.step())

    def test_only_owner_changes_status(self):
        self.close_q001()
        self.as_person("Разработчик-2")
        code, out = self.run_kb("step", "status", "PM-02", "Сверка с бизнесом")
        self.assertEqual(code, 1)
        self.assertIn("Статус шага меняет владелец: Разработчик-1", out)

    def test_ready_only_by_analyst(self):
        rel = self.new_step("PM-05", "Черновик")
        code, out = self.run_kb("step", "status", "PM-05", "Готов к взятию")
        self.assertEqual(code, 1)
        self.assertIn("аналитик", out)
        self.as_person("Аналитик")
        code, out = self.run_kb("step", "status", "PM-05", "Готов к взятию")
        self.assertEqual(code, 0, out)


class Take(Base):
    def test_S10(self):
        self.new_step("PM-05", "Готов к взятию")
        code, out = self.run_kb("take", "PM-05")
        self.assertEqual(code, 1)
        self.assertIn("лимит — 1 шаг на человека. У вас PM-02 в «Разбор»".lower(), out.lower())

    def test_take_free_step(self):
        rel = self.new_step("PM-05", "Готов к взятию")
        self.as_person("Разработчик-2")
        code, out = self.run_kb("take", "PM-05")
        self.assertEqual(code, 0, out)
        text = (self.root / rel).read_text(encoding="utf-8")
        self.assertIn("владелец: Разработчик-2", text)
        self.assertIn("статус: Разбор", text)
        self.assertIn("Готов к взятию → Разбор — Разработчик-2 (kb take)", text)

    def test_partner_joins_two_person_step(self):
        self.set_head("людей", "2")
        self.as_person("Тестировщик-1")
        code, out = self.run_kb("take", "PM-02")
        self.assertEqual(code, 0, out)
        self.assertIn("напарник: Тестировщик-1", self.step())

    def test_status_cannot_replace_take(self):
        self.new_step("PM-05", "Готов к взятию")
        self.as_person("Разработчик-2")
        code, out = self.run_kb("step", "status", "PM-05", "Разбор")
        self.assertEqual(code, 1)
        self.assertIn("kb.py take PM-05", out)


if __name__ == "__main__":
    unittest.main()
