"""Регрессионные тесты tools/kb.py, kb_map.py, kb_activity.py.

Каждый тест получает свежую копию project-template во временной папке (фикстура repo) и гоняет
kb.py подпроцессом, как это делают люди и агенты. Тест, который упал из-за ошибки в kb.py, не удаляется,
а помечается xfail: он описывает ожидаемое поведение.
"""
from __future__ import annotations

import importlib
import re
import sys

import pytest

from conftest import commit, git, mk_question, mk_step, run_kb, snapshot


# ---------- 1. факты с «<» и «>» не принимаются за заглушки ----------

def test_fact_with_comparison_signs_is_not_a_placeholder(repo, kb):
    mk_step(repo, "PM-01", "reg")
    (repo / "materials/brief").mkdir(parents=True)
    (repo / "materials/brief/x.md").write_text("Правила сверки\n", encoding="utf-8")

    first = "Если сумма <100 и остаток >0 — не сверяем"
    rc, out = kb("fact", "PM-01", "Правила", first, "materials/brief/x.md", "подтверждено")
    assert rc == 0, out
    rc, out = kb("fact", "PM-01", "Правила", "Остальное сверяем вручную", "materials/brief/x.md", "оценка")
    assert rc == 0, out

    body = (repo / "context/process/PM-01-reg.md").read_text(encoding="utf-8")
    assert f"- {first} (materials/brief/x.md, подтверждено)" in body
    assert "- Остальное сверяем вручную (materials/brief/x.md, оценка)" in body
    # заглушка шаблона убрана, настоящие факты остались
    assert "<правило, по которому принимается решение>" not in body


# ---------- 2. set по несуществующему ID ----------

def test_set_unknown_id_fails_and_changes_nothing(repo, kb):
    mk_question(repo, "one")
    mk_question(repo, "two", заголовок="Другой")
    before = snapshot(repo)

    rc, out = kb("set", "Q", "статус=дубль")

    assert rc != 0
    assert "Не найден" in out
    assert snapshot(repo) == before


# ---------- 3. пустое значение поля ----------

def test_set_empty_status_is_a_clean_error(repo, kb):
    path = mk_step(repo, "PM-01", "reg")
    before = path.read_bytes()

    rc, out = kb("set", "PM-01", "статус=")

    assert rc != 0
    assert "ОШИБКА" in out
    assert "Traceback" not in out
    assert path.read_bytes() == before


# ---------- 4-5. assign-ids: переписывание путей и ссылок ----------

def _question_with_digest_link(repo, kb):
    mk_question(repo, "t")
    digest = repo / "journal/digest/d.md"
    digest.write_text("Вопрос: [Q-new-t](questions/Q-new-t.md)\n", encoding="utf-8")
    rc, out = kb("assign-ids")
    assert rc == 0, out
    return digest


def test_assign_ids_rewrites_paths_and_ids_in_other_files(repo, kb):
    digest = _question_with_digest_link(repo, kb)

    text = digest.read_text(encoding="utf-8")
    assert "questions/Q-001-t.md" in text
    assert "Q-new-t" not in text
    assert (repo / "journal/questions/Q-001-t.md").exists()
    assert not (repo / "journal/questions/Q-new-t.md").exists()
    renames = (repo / "journal/_renames.tsv").read_text(encoding="utf-8").splitlines()
    assert any(l.startswith("Q-new-t") for l in renames)


def test_assign_ids_applies_known_renames_to_stragglers(repo, kb):
    _question_with_digest_link(repo, kb)
    late = repo / "journal/digest/late.md"
    late.write_text("Ждём ответа на Q-new-t до пятницы\n", encoding="utf-8")

    rc, out = kb("assign-ids")

    assert rc == 0, out
    text = late.read_text(encoding="utf-8")
    assert "Q-001" in text
    assert "Q-new-t" not in text


# ---------- 6. check: заглушки в новом риске ----------

def test_check_flags_unfilled_placeholders_in_new_risk(repo, kb):
    rc, out = kb("new", "risk", "r", "раздел=PM-01", "вероятность=низкая", "влияние=низкое")
    assert rc == 0, out

    rc, out = kb("check")

    assert rc != 0
    assert "следит" in out


# ---------- 7. check: ID внутри кодовых блоков игнорируются ----------

def test_check_ignores_ids_inside_fenced_code_blocks(repo, kb):
    mk_step(repo, "PM-01", "reg")
    rc, out = kb("check")
    assert rc == 0, out   # исходное состояние чистое

    rc, out = kb("append", "PM-01", "Исключения", "```\\nQ-999\\n```")
    assert rc == 0, out
    rc, out = kb("check")
    assert rc == 0, out
    assert "Q-999" not in out

    # контроль: тот же ID вне блока check обязан поймать
    rc, out = kb("append", "PM-01", "Исключения", "- см. Q-998 (materials/x.md, оценка)")
    assert rc == 0, out
    rc, out = kb("check")
    assert rc != 0
    assert "Q-998" in out


# ---------- 8. сканер персональных данных ----------

def test_check_pd_scanner_flags_raw_numbers_and_ignores_masked(repo, kb):
    files = repo / "materials/files"
    files.mkdir(parents=True)
    (files / "phone8.txt").write_text("клиент 87777567804 звонил\n", encoding="utf-8")
    (files / "phone_plus.txt").write_text("клиент +7-777-756-78-04 звонил\n", encoding="utf-8")
    (files / "card.txt").write_text("карта 4400123456789010\n", encoding="utf-8")
    (files / "masked.txt").write_text("телефон +7 701 ***-**-**, выгрузка 20261016093000.csv\n", encoding="utf-8")

    rc, out = kb("check")

    assert rc != 0
    pd_lines = [l for l in out.splitlines() if "персональные данные" in l]
    assert len(pd_lines) == 3, out
    assert all(l.startswith("ОШИБКА") for l in pd_lines), out
    for name in ("phone8.txt", "phone_plus.txt", "card.txt"):
        assert any(name in l for l in pd_lines), out
    assert "masked.txt" not in out


# ---------- 9. changes: неизвестный коммит ----------

def test_changes_unknown_commit(repo, kb):
    rc, out = kb("changes", "--since", "deadbeef1")
    assert rc != 0
    assert "Не найден коммит" in out
    assert "Traceback" not in out


# ---------- 10-11. activity ----------

def test_activity_counts_commits_and_period_is_not_reversed(repo, kb):
    (repo / "materials").mkdir(exist_ok=True)
    (repo / "materials/note.md").write_text("заметка\n", encoding="utf-8")
    commit(repo, "PM-01: заметка")

    rc, out = kb("activity", "--since", "1д")
    assert rc == 0, out
    m = re.search(r"Коммитов людей за период: (\d+)", out)
    assert m and int(m.group(1)) >= 1, out
    assert "Коммитов людей за период: 0" not in out

    # --until включает последний день целиком, период не «перевёрнут»
    rc, out = kb("activity", "--since", "2026-10-16", "--until", "2026-10-16")
    assert rc == 0, out
    assert "# Активность пула: 16.10.2026 — 16.10.2026" in out
    assert "Коммитов людей за период: 0" not in out


def test_activity_sees_cyrillic_file_names(repo, kb):
    (repo / "materials/answers").mkdir(parents=True)
    (repo / "materials/answers/ответы.md").write_text("ответ бизнеса\n", encoding="utf-8")
    commit(repo, "материалы: ответы")

    rc, out = kb("activity", "--since", "30д")

    assert rc == 0, out
    assert "ответы.md" in out


# ---------- 12. take на файле без поля владельца ----------

def test_take_system_with_broken_header_is_refused(repo, kb):
    rc, out = kb("new", "system", "s", "система=1С")
    assert rc == 0, out
    path = repo / "context/systems/SYS-01-s.md"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    assert any(l.startswith("владелец раздела в пуле") for l in lines)
    path.write_text("".join(l for l in lines if not l.startswith("владелец раздела в пуле")), encoding="utf-8")
    before = path.read_bytes()

    rc, out = kb("take", "SYS-01")

    assert rc != 0
    assert "ОШИБКА" in out
    assert "Traceback" not in out
    assert path.read_bytes() == before


# ---------- 13. таблица людей ищется по шапке ----------

def test_team_table_is_found_by_header(repo, kb):
    team = repo / "team.md"
    text = team.read_text(encoding="utf-8")
    other = "| Что | Где |\n|---|---|\n| канал | #kb |\n\n"
    assert "| Имя |" in text
    team.write_text(text.replace("| Имя |", other + "| Имя |", 1), encoding="utf-8")

    rc, out = kb("whoami")

    assert rc == 0, out
    assert "роли: разработчик" in out
    assert "НЕТ В team.md" not in out


# ---------- 14. ready-check: пути материалов с пробелами ----------

def test_ready_check_accepts_material_paths_with_spaces(repo, kb):
    (repo / "materials/files").mkdir(parents=True)
    (repo / "materials/files/Отчёт за сентябрь.md").write_text("отчёт\n", encoding="utf-8")
    sections = {
        "Зачем этот шаг бизнесу": "Без регистрации надпись не попадёт в работу (materials/files/Отчёт за сентябрь.md)",
        "Вход и выход": "Вход: заявка от клиента из почты\\nВыход: надпись в учётной системе",
        "Участники": "| бухгалтер | заносит надпись |",
        "Материалы и контакты": "- materials/files/Отчёт за сентябрь.md — отчёт\\n"
                                "Кто отвечает в бизнесе: бухгалтер — через Гульнару",
    }
    mk_step(repo, "PM-01", "reg", приоритет="1", людей="1", **sections)

    rc, out = kb("ready-check", "PM-01")

    assert "нет файла" not in out, out
    assert rc == 0, out
    assert "готов к взятию" in out


# ---------- 15. kb_map: subgraph/end и «A --> B & C» ----------

def test_kb_map_parses_subgraph_end_and_ampersand(repo, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.syspath_prepend(str(repo / "tools"))
    sys.modules.pop("kb_map", None)
    try:
        kb_map = importlib.import_module("kb_map")
        text = (
            "# Карта\n\n```mermaid\nflowchart LR\n"
            "  subgraph X\n"
            '    A["PM-01 Первый"] --> B["PM-02 Второй"] & C["PM-03 Третий"]\n'
            "  end\n"
            "```\n"
        )
        nodes, edges = kb_map.parse_mermaid(text)
    finally:
        sys.modules.pop("kb_map", None)

    assert "end" not in nodes
    assert "X" not in nodes
    assert set(nodes) == {"A", "B", "C"}
    from_a = sorted(e["to"] for e in edges if e["from"] == "A")
    assert from_a == ["B", "C"]
    assert nodes["B"]["step"] == "PM-02" and nodes["C"]["step"] == "PM-03"


# ---------- 16. take третьим человеком на рут на двоих в «Приёмке» ----------

@pytest.mark.parametrize("mate", [
    pytest.param("—", id="владелец-один"),
    pytest.param("Тимур", id="пара-из-двух"),
])
def test_take_third_person_on_pair_root_in_acceptance(repo, kb, mate):
    mk_step(repo, "PM-01", "reg", людей="2", статус="Приёмка", **{"владелец рута": "Мадина", "напарник": mate})

    rc, out = kb("take", "PM-01", user="Гульнара")

    assert rc != 0
    assert "ведут двое" not in out
    assert "в пару входят только" in out


# ---------- 17. peek: каталог и битый xlsx ----------

@pytest.mark.parametrize("target", [
    pytest.param("materials", id="каталог"),
    pytest.param("bad.xlsx", id="битый-xlsx"),
])
def test_peek_directory_and_broken_xlsx_do_not_crash(repo, kb, tmp_path, target):
    (repo / "materials").mkdir(exist_ok=True)
    (repo / "bad.xlsx").write_text("это обычный текст, а не zip\n", encoding="utf-8")
    # настоящий openpyxl на bad.xlsx бросает zipfile.BadZipFile — воспроизводим это заглушкой,
    # чтобы тест не зависел от того, установлен ли openpyxl
    stub = tmp_path / "stub"
    (stub / "openpyxl").mkdir(parents=True)
    (stub / "openpyxl/__init__.py").write_text(
        "import zipfile\n\ndef load_workbook(path, **kw):\n    return zipfile.ZipFile(path)\n", encoding="utf-8")

    rc, out = kb("peek", target, env={"PYTHONPATH": str(stub)})

    assert rc != 0
    assert "Traceback" not in out
    assert out.strip()


# ---------- 18. activity: аргумент, похожий на опцию ----------

def test_activity_option_like_value_is_rejected(repo, kb, tmp_path):
    victim = tmp_path / "pwn_kb_test"

    rc, out = kb("activity", "--since", f"--output={victim}")

    assert rc != 0
    assert "Не понял" in out
    assert not victim.exists()


# ---------- 19. порядок статусов рута ----------

def test_step_status_flow_skip_back_and_force(repo, kb):
    path = mk_step(repo, "PM-01", "reg", статус="Разбор")

    rc, out = kb("set", "PM-01", "статус=Приёмка")
    assert rc != 0 and "через" in out
    assert "статус: Разбор" in path.read_text(encoding="utf-8")

    rc, out = kb("set", "PM-01", "статус=Сверка с бизнесом")
    assert rc == 0, out
    assert "статус: Сверка с бизнесом" in path.read_text(encoding="utf-8")

    rc, out = kb("set", "PM-01", "статус=Разбор")
    assert rc != 0 and "назад" in out
    assert "статус: Сверка с бизнесом" in path.read_text(encoding="utf-8")

    rc, out = kb("set", "PM-01", "статус=Разбор", "--force", "вернули")
    assert rc == 0, out
    text = path.read_text(encoding="utf-8")
    assert "статус: Разбор" in text
    section = re.search(r"^## Приёмка рута\n(.*?)(?=^## |\Z)", text, re.M | re.S).group(1)
    assert "вернули" in section


# ---------- 20. new step ----------

def test_new_step_creates_numbered_file_with_header_and_heading(repo, kb):
    rc, out = kb("new", "step", "sopostavlenie", "шаг=Сопоставление оплат", "порядок=1 из 1")
    assert rc == 0, out

    path = repo / "context/process/PM-01-sopostavlenie.md"
    assert path.exists(), out
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^id: PM-01$", text, re.M)
    assert re.search(r"^шаг: Сопоставление оплат$", text, re.M)
    assert "# PM-01 Сопоставление оплат" in text.splitlines()

    rc, out = kb("new", "step", "vtoroj", "шаг=Второй шаг", "порядок=2 из 2")
    assert rc == 0, out
    assert (repo / "context/process/PM-02-vtoroj.md").exists(), out


# ---------- 21. транслитерация короткого имени ----------

def test_new_question_transliterates_slug(repo, kb):
    rc, out = kb("new", "question", "источник-долга", "тип=понимание", "раздел=PM-01", "заголовок=Т")

    assert rc == 0, out
    path = repo / "journal/questions/Q-new-istochnik-dolga.md"
    assert path.exists(), out
    assert re.search(r"^id: Q-new-istochnik-dolga$", path.read_text(encoding="utf-8"), re.M)


# ---------- 22. release ----------

def test_release_from_acceptance_is_refused(repo, kb):
    path = mk_step(repo, "PM-01", "reg", статус="Приёмка", **{"владелец рута": "Мадина"})
    before = path.read_bytes()

    rc, out = kb("release", "PM-01", "устал")

    assert rc != 0
    assert "ОШИБКА" in out
    assert path.read_bytes() == before


def test_release_system_by_owner_sets_not_assigned(repo, kb):
    rc, out = kb("new", "system", "s", "система=1С")
    assert rc == 0, out
    path = repo / "context/systems/SYS-01-s.md"
    rc, out = kb("take", "SYS-01")
    assert rc == 0, out
    assert re.search(r"^владелец раздела в пуле: Мадина$", path.read_text(encoding="utf-8"), re.M)

    rc, out = kb("release", "SYS-01", "переключился")

    assert rc == 0, out
    assert re.search(r"^владелец раздела в пуле: не назначен$", path.read_text(encoding="utf-8"), re.M)


# ---------- 23. whoami сравнивает имена целиком ----------

def test_whoami_matches_names_exactly(repo, kb):
    rc, out = kb("new", "assumption", "a", "из вопроса=Q-001", "принял=Алия",
                 "проверить до=2026-10-30", "кто проверяет=Гульнара")
    assert rc == 0, out
    assert "A-new-a" in (repo / "journal/assumptions/A-new-a.md").read_text(encoding="utf-8")

    rc, out = kb("whoami", user="Али")
    assert rc == 0, out
    assert "A-new-a" not in out

    # контроль: настоящая Алия своё допущение видит
    rc, out = kb("whoami", user="Алия")
    assert rc == 0, out
    assert "A-new-a" in out
