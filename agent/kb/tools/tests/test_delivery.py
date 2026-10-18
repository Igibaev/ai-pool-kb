"""Декомпозиты, спека, ранний срез, пакет для бизнеса. Имена — K и B из oshibki.md."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_steps import Base  # noqa: E402

STEP = "context/process/PM-02-pervoe-uvedomlenie.md"


class Delivery(Base):
    def to_slicing(self):
        self.close_q001()
        self.run_kb("step", "enough", "PM-02")
        self.run_kb("step", "status", "PM-02", "Сверка с бизнесом")
        code, out = self.run_kb("step", "status", "PM-02", "Нарезка")
        self.assertEqual(code, 0, out)

    def new_dec(self):
        code, out = self.run_kb("dec", "new", "PM-02", "otbor", "название=Отбор должников", "боль=Претензии уходят оплатившим")
        self.assertEqual(code, 0, out)
        return "PM-02-01"

    def test_slice_and_spec_flow(self):
        self.to_slicing()
        ident = self.new_dec()
        self.run_kb("dec", "status", ident, "Спека")
        self.run_kb("spec", "set", ident, "openspec/changes/otbor/spec.md")
        self.as_person("Разработчик-2")
        code, out = self.run_kb("spec", "accept", ident)
        self.assertEqual(code, 0, out)
        self.as_person("Разработчик-1")
        code, out = self.run_kb("dec", "status", ident, "Сборка")
        self.assertEqual(code, 0, out)
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_K01(self):
        self.to_slicing()
        ident = self.new_dec()
        self.run_kb("dec", "status", ident, "Спека")
        code, out = self.run_kb("dec", "status", ident, "Сборка")
        self.assertEqual(code, 1)
        self.assertIn("нет кода без принятой спеки", out)

    def test_K02(self):
        self.to_slicing()
        ident = self.new_dec()
        code, out = self.run_kb("spec", "early", ident, "--reason", "бизнес торопит")
        self.assertEqual(code, 1)
        self.assertIn("неподтверждённых фактов", out)

    def test_K02_early_slice_when_all_confirmed(self):
        self.to_slicing()
        path = self.root / STEP
        text = path.read_text(encoding="utf-8").replace("; противоречие)", "; подтверждено)").replace("; оценка)", "; подтверждено)")
        text = text.replace("(ист: D-001; подтверждено)", "(ист: materials/2026-10-01-zapros.md:10; подтверждено)")
        path.write_text(text, encoding="utf-8")
        ident = self.new_dec()
        code, out = self.run_kb("spec", "early", ident, "--reason", "бизнес торопит")
        self.assertEqual(code, 0, out)
        self.run_kb("dec", "status", ident, "Спека")
        code, out = self.run_kb("dec", "status", ident, "Сборка")
        self.assertEqual(code, 0, out)

    def test_K03(self):
        self.to_slicing()
        ident = self.new_dec()
        self.run_kb("spec", "set", ident, "openspec/x.md")
        code, out = self.run_kb("spec", "accept", ident)
        self.assertEqual(code, 1)
        self.assertIn("спеку принимает архитектор недели: Разработчик-2", out)

    def test_K04(self):
        code, out = self.run_kb("dec", "new", "PM-02", "otbor", "название=x", "боль=y")
        self.assertEqual(code, 1)
        self.assertIn("декомпозиты создают в «Нарезке»", out)
        self.to_slicing()
        self.as_person("Разработчик-2")
        code, out = self.run_kb("dec", "new", "PM-02", "otbor", "название=x", "боль=y")
        self.assertEqual(code, 1)
        self.assertIn("режет шаг владелец", out)

    def test_K05(self):
        self.to_slicing()
        ident = self.new_dec()
        path = next((self.root / "delivery").glob(f"{ident}-*.md"))
        path.write_text(path.read_text(encoding="utf-8").replace("спека принята: —", "спека принята: Разработчик-2, 2026-10-18"),
                        encoding="utf-8")
        code, out = self.run_kb("check")
        self.assertEqual(code, 1)
        self.assertIn("ставит только `kb spec accept`", out)

    def test_K06(self):
        self.to_slicing()
        code, out = self.run_kb("step", "status", "PM-02", "Приёмка")
        self.assertEqual(code, 1)
        self.assertIn("нет декомпозитов", out)
        self.new_dec()
        code, out = self.run_kb("step", "status", "PM-02", "Приёмка")
        self.assertEqual(code, 0, out)


class Package(Base):
    def setUp(self):
        super().setUp()
        self.as_person("Аналитик")
        self.run_kb("q", "sort", "Q-001", "кто=бизнес", "роль=начальник абонентского отдела")

    def test_B01(self):
        code, out = self.run_kb("package", "начальник абонентского отдела")
        self.assertEqual(code, 0, out)
        path = self.root / "journal/packages/2026-10-18-nachalnik-abonentskogo-otdela.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("| Q-001 | С какого долга", text)
        self.assertNotIn("Q-002", text)
        self.assertNotIn("Q-003", text)

    def test_B02_load_answers(self):
        self.run_kb("package", "всем")
        path = self.root / "journal/packages/2026-10-18-vsem.md"
        path.write_text(path.read_text(encoding="utf-8").replace("расчётных периода? | |", "расчётных периода? | 3 и более периода |"),
                        encoding="utf-8")
        code, out = self.run_kb("package", "load", "journal/packages/2026-10-18-vsem.md")
        self.assertEqual(code, 0, out)
        self.assertIn("ЗАГРУЖЕНО ответов: 1 (Q-001)", out)
        q = (self.root / "journal/questions/Q-001.md").read_text(encoding="utf-8")
        self.assertIn("статус: ответ найден", q)
        self.assertIn("- 3 и более периода (ист: journal/packages/2026-10-18-vsem.md:", q)
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_B03(self):
        code, out = self.run_kb("package", "директор")
        self.assertEqual(code, 1)
        self.assertIn("роли «директор» нет в `team.md`", out)

    def test_B04(self):
        (self.root / "materials/paket.md").write_text("| ID | Вопрос | Ответ |\n|---|---|---|\n| Q-001 | x | y |\n",
                                                      encoding="utf-8")
        code, out = self.run_kb("package", "load", "materials/paket.md")
        self.assertEqual(code, 1)
        self.assertIn("пакет должен лежать в `kb/journal/packages/`", out)


if __name__ == "__main__":
    unittest.main()
