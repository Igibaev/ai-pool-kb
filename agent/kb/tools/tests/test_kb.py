"""Тесты kb.py по oshibki.md: имя теста — номер случая (F01, S02, Q06…).

    python3 -m unittest discover -s kb/tools/tests -v
"""
from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixture"
sys.path.insert(0, str(TOOLS))

import kb  # noqa: E402

STEP = "context/process/PM-02-pervoe-uvedomlenie.md"
REGL = "materials/2026-10-01-reglament.md"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "kb"
        shutil.copytree(FIXTURE, self.root)
        self.env = {k: os.environ.get(k) for k in ("KB_ROOT", "KB_TODAY")}
        os.environ["KB_ROOT"] = str(self.root)
        os.environ["KB_TODAY"] = "2026-10-18"

    def tearDown(self):
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

    def fact(self, line: str, section: str = "Вход") -> tuple[int, str]:
        return self.run_kb("fact", "PM-02", section, line)

    def edit(self, rel: str, old: str, new: str) -> None:
        path = self.root / rel
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def step_text(self) -> str:
        return (self.root / STEP).read_text(encoding="utf-8")

    def assert_refused(self, line: str, message: str) -> None:
        before = self.step_text()
        code, out = self.fact(line)
        self.assertEqual(code, 1, out)
        self.assertIn(message, out)
        self.assertIn("НЕ ЗАПИСАНО", out)
        self.assertEqual(before, self.step_text(), "файл изменился при отказе")

    def assert_check(self, level: str, message: str) -> str:
        code, out = self.run_kb("check")
        self.assertIn(f"{level} ", out)
        self.assertIn(message, out)
        if level == "ОШИБКА":
            self.assertEqual(code, 1, out)
        return out


class Fixture(Base):
    def test_fixture_clean(self):
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)
        self.assertIn("Итог: ошибок 0, предупреждений 1", out)


class Facts(Base):
    def test_F01(self):
        self.assert_refused("- Должник — долг за 3 периода", "Факт без источника и статуса")

    def test_F02(self):
        self.assert_refused(f"- Должник — долг за 3 периода (ист: {REGL}:3)", "Нет статуса")

    def test_F03(self):
        self.assert_refused(f"- Должник (ист: {REGL}:30; подтверждено)",
                            f"В `{REGL}` 9 строк, строки 30 нет")

    def test_F04(self):
        self.assert_refused("- Должник (ист: materials/reglament.md:3; подтверждено)",
                            f"Файла `materials/reglament.md` нет. Похожие: `{REGL}`")

    def test_F05(self):
        self.assert_refused("- Должник (ист: ИИ; подтверждено)", "Ответ ИИ — не источник")

    def test_F06(self):
        ai = self.root / "materials" / "ai"
        ai.mkdir()
        (ai / "2026-10-17-portal.md").write_text("a\nb\nc\n", encoding="utf-8")
        self.assert_refused("- Должник (ист: materials/ai/2026-10-17-portal.md:3; подтверждено)",
                            "Ответ RAG — не выше «оценка»")
        code, out = self.fact("- Должник (ист: materials/ai/2026-10-17-portal.md:3; оценка)")
        self.assertEqual(code, 0, out)

    def test_F07(self):
        self.assert_refused("- Должник (ист: со слов Иванов, 2026-10-17; подтверждено)",
                            "«Иванов» нет в `team.md`")

    def test_F08(self):
        self.assert_refused("- Должник (ист: со слов Разработчик-2; подтверждено)",
                            "У «со слов» нет даты. Нужно: `со слов Разработчик-2, ГГГГ-ММ-ДД`")

    def test_F09(self):
        self.assert_refused("- Должник (ист: со слов Разработчик-2, 2026-12-01; подтверждено)",
                            "Дата 2026-12-01 в будущем")

    def test_F10(self):
        self.assert_refused("- Не слать SMS (ист: D-001; подтверждено)",
                            "D-001 — «предложено». Пока решение не принято, факт — не выше «оценка»")

    def test_F11(self):
        self.assert_refused("- Должник (ист: Q-001; подтверждено)", "У Q-001 нет ответа")

    def test_F12(self):
        self.assert_refused(f"- Должник (ист: {REGL}:3; допущение)", "У допущения нужен номер")

    def test_F13(self):
        code, out = self.fact(f"- Должник — долг за 3 периода (ист: {REGL}:3; confirmed)")
        self.assertEqual(code, 0, out)
        self.assertIn("ПОПРАВЛЕНО: `confirmed` → `подтверждено`", out)
        self.assertIn(f"- Должник — долг за 3 периода (ист: {REGL}:3; подтверждено)", self.step_text())

    def test_F14(self):
        code, out = self.fact(f"- Должник — долг за 3 периода (источник: {REGL}, стр. 3, подтверждено)")
        self.assertEqual(code, 0, out)
        self.assertIn("ПОПРАВЛЕНО", out)
        self.assertIn(f"(ист: {REGL}:3; подтверждено)", self.step_text())

    def test_F15(self):
        code, out = self.fact(f"- Должник (ист: {REGL}:3; пoдтверждено)")  # латинская «o»
        self.assertEqual(code, 0, out)
        self.assertIn("латинская буква в слове «подтверждено» заменена кириллической", out)
        self.assertIn(f"(ист: {REGL}:3; подтверждено)", self.step_text())

    def test_F16(self):
        self.assert_refused(f"- Должник 确认 (ист: {REGL}:3; подтверждено)",
                            "В тексте символы не кириллицы и не латиницы: `确认`")

    def test_F17(self):
        self.edit(STEP, "## Состояния\n", "## Состояния\n<think>подумать</think>\n")
        self.assert_check("ОШИБКА", "В файле теги рассуждений модели")

    def test_F18(self):
        self.edit(STEP, f"- Должник — абонент с долгом за 3 и более расчётных периода (ист: {REGL}:3; противоречие)\n", "")
        self.assert_check("ПРЕДУПРЕЖДЕНИЕ", "У противоречия должно быть две стороны")

    def test_F19(self):
        code, out = self.fact("- Сейчас специалист рассылает SMS руками; в продукте будет кнопка "
                              f"(ист: {REGL}:4; противоречие)")
        self.assertIn("«Как сейчас» и «как будет» — не противоречие", out)

    def test_F20(self):
        self.edit(STEP, "## Как будет\n", f"## Как будет\n- Факт не к месту (ист: {REGL}:3; подтверждено)\n")
        self.assert_check("ПРЕДУПРЕЖДЕНИЕ", "Здесь не факты, а пересказ или намерение")


class Writing(Base):
    def test_numbered_section_continues_numbering(self):
        code, out = self.fact(f"- Уведомление регистрируют в журнале (ист: {REGL}:9; подтверждено)", "Как сейчас")
        self.assertEqual(code, 0, out)
        self.assertIn(f"4. Уведомление регистрируют в журнале (ист: {REGL}:9; подтверждено)", self.step_text())

    def test_empty_section_gets_fact(self):
        code, out = self.fact(f"- Должник становится уведомлённым (ист: {REGL}:9; оценка)", "Состояния")
        self.assertEqual(code, 0, out)
        self.assertIn(f"## Состояния\n\n- Должник становится уведомлённым (ист: {REGL}:9; оценка)\n\n## Исключения",
                      self.step_text())
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_five_arguments(self):
        code, out = self.run_kb("fact", "PM-02", "Вход", "Выгрузка раз в месяц",
                                "со слов начальник абонентского отдела через Аналитик, 2026-10-18, созвон",
                                "подтверждено")
        self.assertEqual(code, 0, out)
        self.assertIn("(ист: со слов начальник абонентского отдела через Аналитик, 2026-10-18, созвон; подтверждено)",
                      self.step_text())

    def test_not_fact_section_refused(self):
        code, out = self.fact(f"- Текст (ист: {REGL}:3; подтверждено)", "Коротко")
        self.assertEqual(code, 1, out)
        self.assertIn("нет раздела для фактов «Коротко»", out)

    def test_unknown_object(self):
        code, out = self.run_kb("fact", "PM-99", "Вход", f"- Текст (ист: {REGL}:3; подтверждено)")
        self.assertEqual(code, 1)
        self.assertIn("PM-99 нет в базе", out)

    def test_answered_question_is_source(self):
        code, out = self.fact("- Шлюз принимает пакет (ист: Q-002; подтверждено)")
        self.assertEqual(code, 0, out)


class Step(Base):
    def test_S01(self):
        self.edit(STEP, "Вход: месячная выгрузка должников из биллинга.\n", "")
        self.assert_check("ПРЕДУПРЕЖДЕНИЕ", "Опишите шаг своими словами")

    def test_S02(self):
        self.edit(STEP, "| Рассылка SMS через шлюз оператора | MVP | автоматизируем |",
                  "| Рассылка SMS через шлюз оператора | | автоматизируем |")
        self.assert_check("ПРЕДУПРЕЖДЕНИЕ", "Отметьте, когда делаем")

    def test_S03(self):
        self.edit(STEP, "## Заметки\n", "## Заметки\n- просто мысль без вида\n")
        self.assert_check("ПРЕДУПРЕЖДЕНИЕ", "Формат заметки")


class Questions(Base):
    def test_Q01(self):
        self.edit("journal/questions/Q-001.md", "задаём: Разработчик-1, 2026-10-17\n", "")
        self.assert_check("ОШИБКА", "Вопрос попадает в журнал только через `kb ask send`")

    def test_Q03(self):
        self.edit("journal/questions/Q-001.md", "кто отвечает: бизнес", "кто отвечает: бизнес-правило")
        self.assert_check("ОШИБКА", "Значения: бизнес, смежник, команда, аналитик, сами")

    def test_Q04_hint(self):
        self.edit("journal/questions/Q-001.md", "кто отвечает: бизнес", "кто отвечает: business")
        self.assert_check("ОШИБКА", "Похоже на «бизнес»")

    def test_Q06(self):
        self.edit("journal/questions/Q-002.md", "Закрыл: Разработчик-1, 2026-10-17\n", "")
        self.assert_check("ОШИБКА", "Закрытие подтверждает автор")


class Whoami(Base):
    def test_unknown_person(self):
        team = kb.kbbase.Team(self.root)
        self.assertEqual(team.by_git("dev1@example.local", ""), "Разработчик-1")
        self.assertIsNone(team.by_git("nobody@example.local", "Кто-то"))


if __name__ == "__main__":
    unittest.main()
