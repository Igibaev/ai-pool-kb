"""Вопросы (порция 4): только по «задаём», «кто отвечает», дубли, закрытие, списки по шагам.

Имя теста — номер случая из oshibki.md (Q02–Q07) или что проверяется.
"""
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

ZAPROS = "materials/2026-10-01-zapros.md"


class Base(unittest.TestCase):
    person = ("Разработчик-1", "dev1@example.local")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "kb"
        shutil.copytree(FIXTURE, self.root)
        subprocess.run(["git", "init", "-q", str(self.tmp)], check=True)
        self.as_person(*self.person)
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

    def as_person(self, name: str, email: str) -> None:
        subprocess.run(["git", "-C", str(self.tmp), "config", "user.name", name], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "config", "user.email", email], check=True)

    def run_kb(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = kb.main(list(args))
        return code, out.getvalue()

    def send(self, slug="stoimost-sms", **over) -> tuple[int, str]:
        fields = {"шаг": "PM-02", "кто": "смежник", "MVP": "да", "блокирует": "нет",
                  "откуда": f"{ZAPROS}:7", "вопрос": "Сколько стоит одна SMS у оператора?"}
        fields.update(over)
        args = [f"{k}={v}" for k, v in fields.items() if v is not None]
        return self.run_kb("ask", "send", slug, *args)

    def q_text(self, ident: str) -> str:
        return (self.root / "journal/questions" / f"{ident}.md").read_text(encoding="utf-8")

    def view(self, name: str) -> str:
        return (self.root / "journal/views" / name).read_text(encoding="utf-8")


class Ask(Base):
    def test_send_creates_question_with_zadaem(self):
        code, out = self.send()
        self.assertEqual(code, 0, out)
        self.assertIn("ВОПРОС ЗАВЕДЁН: journal/questions/Q-new-stoimost-sms.md", out)
        text = self.q_text("Q-new-stoimost-sms")
        self.assertIn("задаём: Разработчик-1, 2026-10-18", text)
        self.assertIn("рассортировал: —", text)
        self.assertIn("автор: Разработчик-1", text)
        self.assertIn("Q-new-stoimost-sms", self.view("all.md").split("## Открытые")[0])
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_Q02(self):
        code, out = self.send(откуда=None)
        self.assertEqual(code, 1)
        self.assertIn("Откуда вопрос: строка материала или «со слов кто, дата»", out)
        self.assertIn("ВОПРОС НЕ ЗАВЕДЁН", out)
        self.assertFalse((self.root / "journal/questions/Q-new-stoimost-sms.md").exists())

    def test_Q02_origin_checked_like_source(self):
        code, out = self.send(откуда="со слов Иванов, 2026-10-17")
        self.assertEqual(code, 1)
        self.assertIn("«Иванов» нет в `team.md`", out)

    def test_Q03(self):
        code, out = self.send(кто="бизнес-правило")
        self.assertEqual(code, 1)
        self.assertIn("Значения: бизнес, смежник, команда, аналитик, сами", out)

    def test_Q04(self):
        code, out = self.send(кто="business")
        self.assertEqual(code, 0, out)
        self.assertIn("ПОПРАВЛЕНО: `business` → `бизнес`", out)
        self.assertIn("кто отвечает: бизнес", self.q_text("Q-new-stoimost-sms"))

    def test_Q05(self):
        code, out = self.send("porog", вопрос="С какого долга абонент попадает в список должников?")
        self.assertEqual(code, 0, out)
        self.assertIn("ПРЕДУПРЕЖДЕНИЕ: похож на Q-001", out)

    def test_similar_finds_question_and_fact(self):
        code, out = self.run_kb("similar", "в какой срок уходит первое уведомление должникам")
        self.assertEqual(code, 0)
        self.assertIn("PM-02", out)
        self.assertIn("Дубль ли это, решает человек", out)

    def test_bad_slug(self):
        code, out = self.send("Стоимость SMS")
        self.assertEqual(code, 1)
        self.assertIn("латиница, цифры и дефис", out)

    def test_unknown_step(self):
        code, out = self.send(шаг="PM-77")
        self.assertEqual(code, 1)
        self.assertIn("Шага PM-77 нет в базе", out)


class Lifecycle(Base):
    def test_answer_then_author_closes(self):
        self.send()
        code, out = self.run_kb("q", "answer", "Q-new-stoimost-sms", "Одна SMS стоит 4 тенге",
                                "со слов Разработчик-2, 2026-10-18", "подтверждено", "записан=PM-02#Вход")
        self.assertEqual(code, 0, out)
        self.assertIn("статус «ответ найден»", out)
        self.assertIn("статус: ответ найден", self.q_text("Q-new-stoimost-sms"))
        self.assertIn("Ответ найден — ждёт подтверждения автора — 1", self.view("PM-02.md"))
        code, out = self.run_kb("q", "close", "Q-new-stoimost-sms")
        self.assertEqual(code, 0, out)
        text = self.q_text("Q-new-stoimost-sms")
        self.assertIn("статус: отвечен", text)
        self.assertIn("Закрыл: Разработчик-1, 2026-10-18", text)
        self.assertIn("Записан в: PM-02#Вход", text)
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_answer_needs_source(self):
        code, out = self.run_kb("q", "answer", "Q-001", "- Три периода")
        self.assertEqual(code, 1)
        self.assertIn("Факт без источника и статуса", out)

    def test_only_author_closes(self):
        self.send()
        self.run_kb("q", "answer", "Q-new-stoimost-sms", "4 тенге", "со слов Разработчик-2, 2026-10-18", "подтверждено")
        self.as_person("Тестировщик-1", "qa1@example.local")
        code, out = self.run_kb("q", "close", "Q-new-stoimost-sms")
        self.assertEqual(code, 1)
        self.assertIn("закрытие подтверждает автор вопроса: Разработчик-1", out)

    def test_close_without_answer_refused(self):
        code, out = self.run_kb("q", "close", "Q-001")
        self.assertEqual(code, 1)
        self.assertIn("нет ответа", out)

    def test_only_analyst_sorts(self):
        self.send()
        code, out = self.run_kb("q", "sort", "Q-new-stoimost-sms", "кто=бизнес")
        self.assertEqual(code, 1)
        self.assertIn("сортирует вопросы аналитик", out)
        self.as_person("Аналитик", "analyst@example.local")
        code, out = self.run_kb("q", "sort", "Q-new-stoimost-sms", "кто=бизнес")
        self.assertEqual(code, 0, out)
        self.assertIn("рассортировал: Аналитик, 2026-10-18", self.q_text("Q-new-stoimost-sms"))
        self.assertIn("Новые, не рассортированы — 0", self.view("all.md"))

    def test_out_of_system_batch(self):
        self.send("a", вопрос="Как ML считает вероятность?")
        self.send("b", вопрос="Когда ML обновит модель?")
        self.as_person("Аналитик", "analyst@example.local")
        code, out = self.run_kb("q", "out", "Q-new-a", "Q-new-b", "считает ML, не наш контур")
        self.assertEqual(code, 0, out)
        for ident in ("Q-new-a", "Q-new-b"):
            text = self.q_text(ident)
            self.assertIn("статус: вне системы", text)
            self.assertIn("- Вне системы: считает ML, не наш контур (ист: со слов Аналитик, 2026-10-18; подтверждено)", text)
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_out_only_analyst(self):
        code, out = self.run_kb("q", "out", "Q-001", "причина")
        self.assertEqual(code, 1)
        self.assertIn("«вне системы» ставит аналитик", out)


class Views(Base):
    def test_views_are_stable(self):
        code, out = self.run_kb("views")
        self.assertIn("Списки не изменились", out)

    def test_step_view_shows_waiting(self):
        self.assertIn("Ждёт ответов: блокирующих 1", self.view("PM-02.md"))
        self.assertIn("## Закрытые — 2", self.view("PM-02.md"))

    def test_Q07(self):
        sub = self.root / "journal/questions/PM-02"
        sub.mkdir()
        shutil.move(str(self.root / "journal/questions/Q-002.md"), str(sub / "Q-002.md"))
        code, out = self.run_kb("check")
        self.assertEqual(code, 1)
        self.assertIn("Вопросы лежат в `journal/questions/` плоско", out)


if __name__ == "__main__":
    unittest.main()
