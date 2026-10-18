"""ПД, находки не-владельца, связи шагов, «устарело», глоссарий. Имена — P, N, G, L из oshibki.md."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_steps import Base  # noqa: E402

STEP = "context/process/PM-02-pervoe-uvedomlenie.md"
NOTES = "context/process/notes/PM-02/qa1.md"
PM01 = """---
id: PM-01
шаг: Загрузка должников
статус: Разбор
владелец: Разработчик-2
напарник: —
людей: 1
приоритет: 1
системы: []
достаточно для спеки: —
---
# PM-01 Загрузка должников

## Коротко
Что делает: забирает выгрузку должников из биллинга.
Зачем бизнесу: единый список должников.
Вход: выгрузка биллинга.
Выход: список должников.

## Объём
| Часть | Когда | Как | Источник |
|---|---|---|---|

## Зачем бизнесу

## Вход
- Биллинг выгружает должников раз в месяц (ист: materials/2026-10-01-zapros.md:5; подтверждено)

## Выход
- Список должников из выгрузки биллинга раз в месяц (ист: materials/2026-10-01-zapros.md:5; подтверждено)

## Сущности
| Сущность | Поле | Откуда | Источник |
|---|---|---|---|
| Должник | лицевой счёт | выгрузка биллинга | materials/2026-10-01-zapros.md:5 |

## Состояния

## Исключения

## Как сейчас

## Как будет

## Заметки

## Нарезка

## Приёмка

## История статуса
"""


class PersonalData(Base):
    def test_P01(self):
        path = self.root / STEP
        path.write_text(path.read_text(encoding="utf-8").replace(
            "## Состояния\n", "## Состояния\nПИНФЛ должника 31405901234567\n"), encoding="utf-8")
        code, out = self.run_kb("check")
        self.assertEqual(code, 1)
        self.assertIn("Похоже на персональные данные (идентификатор): «31405901234567»", out)

    def test_P02_masked_is_fine(self):
        path = self.root / STEP
        path.write_text(path.read_text(encoding="utf-8").replace(
            "## Состояния\n", "## Состояния\nПИНФЛ должника 3140590******* и телефон +99890***\n"), encoding="utf-8")
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_P03_material_is_warning(self):
        (self.root / "materials/2026-10-18-spisok.md").write_text("Тел. +998 90 123 45 67\n", encoding="utf-8")
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)
        self.assertIn("ПРЕДУПРЕЖДЕНИЕ materials/2026-10-18-spisok.md:1: Похоже на персональные данные (телефон)", out)


class Notes(Base):
    def test_owner_takes_finding(self):
        code, out = self.run_kb("notes", "take", "PM-02", "qa1", "1", "Исключения")
        self.assertEqual(code, 0, out)
        step = (self.root / STEP).read_text(encoding="utf-8")
        self.assertIn("(ист: materials/2026-10-01-reglament.md:6, нашёл Тестировщик-1; подтверждено)", step)
        notes = (self.root / NOTES).read_text(encoding="utf-8")
        self.assertIn("· перенесено: PM-02#Исключения, 2026-10-18", notes)
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)

    def test_N01(self):
        self.as_person("Разработчик-2")
        code, out = self.run_kb("notes", "take", "PM-02", "qa1", "1", "Исключения")
        self.assertEqual(code, 1)
        self.assertIn("находки в шаг переносит владелец шага: Разработчик-1", out)

    def test_N02(self):
        self.run_kb("notes", "take", "PM-02", "qa1", "1", "Исключения")
        code, out = self.run_kb("notes", "take", "PM-02", "qa1", "1", "Исключения")
        self.assertEqual(code, 1)
        self.assertIn("уже перенесена", out)

    def test_N03(self):
        code, out = self.run_kb("notes", "take", "PM-02", "qa1", "7", "Исключения")
        self.assertEqual(code, 1)
        self.assertIn("номер находки — от 1 до 2", out)

    def test_N04_foreign_notes_not_committed(self):
        subprocess.run(["git", "-C", str(self.tmp), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "commit", "-qm", "старт"], check=True)
        self.as_person("Разработчик-2")
        path = self.root / NOTES
        path.write_text(path.read_text(encoding="utf-8") + "- чужая правка (ист: materials/2026-10-01-reglament.md:3; оценка)\n",
                        encoding="utf-8")
        code, out = self.run_kb("commit", "plan")
        self.assertEqual(code, 1)
        self.assertIn("Находки пишет только автор", out)


class Links(Base):
    def setUp(self):
        super().setUp()
        (self.root / "context/process/PM-01-zagruzka.md").write_text(PM01, encoding="utf-8")

    def test_L01_links_and_blocking(self):
        code, out = self.run_kb("deps")
        text = (self.root / "context/process/_deps.md").read_text(encoding="utf-8")
        self.assertIn("| PM-02 Первое уведомление должника | PM-01 |", text)
        self.assertIn("- PM-01 → PM-02:", text)
        self.assertIn("сущность Должник", text)
        self.assertIn("- PM-01 «Разбор» блокирует PM-02", text)

    def test_L02_twins(self):
        twin = PM01.replace("PM-01", "PM-07").replace("Загрузка должников", "Приём выгрузки")
        (self.root / "context/process/PM-07-priyom.md").write_text(twin, encoding="utf-8")
        self.run_kb("deps")
        text = (self.root / "context/process/_deps.md").read_text(encoding="utf-8")
        self.assertIn("- PM-01 и PM-07: похожи и вход, и выход", text)

    def test_L03_stale(self):
        env = dict(os.environ)
        def commit(msg, when):
            subprocess.run(["git", "-C", str(self.tmp), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(self.tmp), "commit", "-qm", msg], check=True,
                           env={**env, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
        commit("старт", "2026-10-15T10:00:00")
        path = self.root / "context/process/PM-01-zagruzka.md"
        path.write_text(path.read_text(encoding="utf-8").replace("раз в месяц (ист", "раз в неделю (ист", 1), encoding="utf-8")
        commit("PM-01: выход поменялся", "2026-10-17T10:00:00")
        code, out = self.run_kb("deps")
        self.assertIn("УСТАРЕЛО: PM-02: вход берёт у PM-01, а PM-01 изменён позже (2026-10-17 > 2026-10-15)", out)
        code, out = self.run_kb("where")
        self.assertIn("Устарело: PM-02", out)

    def test_L04_hand_edit_of_deps_refused(self):
        self.run_kb("deps")
        subprocess.run(["git", "-C", str(self.tmp), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "commit", "-qm", "старт"], check=True)
        path = self.root / "context/process/_deps.md"
        path.write_text(path.read_text(encoding="utf-8") + "руками\n", encoding="utf-8")
        code, out = self.run_kb("commit", "plan")
        self.assertIn("Связи шагов пересобраны", out)
        self.assertNotIn("руками", path.read_text(encoding="utf-8"))

    def test_L04_hook_stops_hand_edit_of_deps(self):
        # Человек коммитит сам, мимо `kb commit plan` — хук pre-commit останавливает
        self.run_kb("deps")
        subprocess.run(["git", "-C", str(self.tmp), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "commit", "-qm", "старт"], check=True)
        path = self.root / "context/process/_deps.md"
        path.write_text(path.read_text(encoding="utf-8") + "руками\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.tmp), "add", "-A"], check=True)
        code, out = self.run_kb("precommit")
        self.assertEqual(code, 1, out)
        self.assertIn("Связи шагов строит инструмент", out)


class Glossary(Base):
    def setUp(self):
        super().setUp()
        (self.root / "context/glossary.md").write_text(
            "# Глоссарий\n\n| Термин | Синонимы | Определение |\n|---|---|---|\n"
            "| Должник | неплательщик, дебитор | Абонент с долгом |\n", encoding="utf-8")

    def test_G01_synonym_warning(self):
        path = self.root / STEP
        path.write_text(path.read_text(encoding="utf-8").replace(
            "## Состояния\n", "## Состояния\n- Неплательщик уведомлён (ист: materials/2026-10-01-reglament.md:9; оценка)\n"),
            encoding="utf-8")
        code, out = self.run_kb("check")
        self.assertEqual(code, 0, out)
        self.assertIn("«Неплательщик» — синоним «Должник» по глоссарию. Пишите основной термин", out)

    def test_G02_term_twice(self):
        path = self.root / "context/glossary.md"
        path.write_text(path.read_text(encoding="utf-8") + "| Дебитор | | Тот, кто должен |\n", encoding="utf-8")
        code, out = self.run_kb("check")
        self.assertEqual(code, 1)
        self.assertIn("«Дебитор» в глоссарии дважды", out)

    def test_similar_uses_synonyms(self):
        code, out = self.run_kb("similar", "с какого долга дебитор попадает в список")
        self.assertIn("Q-001", out)


if __name__ == "__main__":
    unittest.main()
