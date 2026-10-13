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
    (repo / "kb" / "materials/brief").mkdir(parents=True)
    (repo / "kb" / "materials/brief/x.md").write_text("Правила сверки\n", encoding="utf-8")

    first = "Если сумма <100 и остаток >0 — не сверяем"
    rc, out = kb("fact", "PM-01", "Правила", first, "materials/brief/x.md", "подтверждено")
    assert rc == 0, out
    rc, out = kb("fact", "PM-01", "Правила", "Остальное сверяем вручную", "materials/brief/x.md", "оценка")
    assert rc == 0, out

    body = (repo / "kb" / "context/process/PM-01-reg.md").read_text(encoding="utf-8")
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
    digest = repo / "kb" / "journal/digest/d.md"
    digest.write_text("Вопрос: [Q-new-t](questions/Q-new-t.md)\n", encoding="utf-8")
    rc, out = kb("assign-ids")
    assert rc == 0, out
    return digest


def test_assign_ids_rewrites_paths_and_ids_in_other_files(repo, kb):
    digest = _question_with_digest_link(repo, kb)

    text = digest.read_text(encoding="utf-8")
    assert "questions/Q-001-t.md" in text
    assert "Q-new-t" not in text
    assert (repo / "kb" / "journal/questions/Q-001-t.md").exists()
    assert not (repo / "kb" / "journal/questions/Q-new-t.md").exists()
    renames = (repo / "kb" / "journal/_renames.tsv").read_text(encoding="utf-8").splitlines()
    assert any(l.startswith("Q-new-t") for l in renames)


def test_assign_ids_applies_known_renames_to_stragglers(repo, kb):
    _question_with_digest_link(repo, kb)
    late = repo / "kb" / "journal/digest/late.md"
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
    files = repo / "kb" / "materials/files"
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
    (repo / "kb" / "materials").mkdir(exist_ok=True)
    (repo / "kb" / "materials/note.md").write_text("заметка\n", encoding="utf-8")
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
    (repo / "kb" / "materials/answers").mkdir(parents=True)
    (repo / "kb" / "materials/answers/ответы.md").write_text("ответ бизнеса\n", encoding="utf-8")
    commit(repo, "материалы: ответы")

    rc, out = kb("activity", "--since", "30д")

    assert rc == 0, out
    assert "ответы.md" in out


# ---------- 12. take на файле без поля владельца ----------

def test_take_system_with_broken_header_is_refused(repo, kb):
    rc, out = kb("new", "system", "s", "система=1С")
    assert rc == 0, out
    path = repo / "kb" / "context/systems/SYS-01-s.md"
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
    team = repo / "kb" / "team.md"
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
    (repo / "kb" / "materials/files").mkdir(parents=True)
    (repo / "kb" / "materials/files/Отчёт за сентябрь.md").write_text("отчёт\n", encoding="utf-8")
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
    monkeypatch.syspath_prepend(str(repo / "kb" / "tools"))
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
    (repo / "kb" / "materials").mkdir(exist_ok=True)
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

    path = repo / "kb" / "context/process/PM-01-sopostavlenie.md"
    assert path.exists(), out
    text = path.read_text(encoding="utf-8")
    assert re.search(r"^id: PM-01$", text, re.M)
    assert re.search(r"^шаг: Сопоставление оплат$", text, re.M)
    assert "# PM-01 Сопоставление оплат" in text.splitlines()

    rc, out = kb("new", "step", "vtoroj", "шаг=Второй шаг", "порядок=2 из 2")
    assert rc == 0, out
    assert (repo / "kb" / "context/process/PM-02-vtoroj.md").exists(), out


# ---------- 21. транслитерация короткого имени ----------

def test_new_question_transliterates_slug(repo, kb):
    rc, out = kb("new", "question", "источник-долга", "тип=понимание", "раздел=PM-01", "заголовок=Т")

    assert rc == 0, out
    path = repo / "kb" / "journal/questions/Q-new-istochnik-dolga.md"
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
    path = repo / "kb" / "context/systems/SYS-01-s.md"
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
    assert "A-new-a" in (repo / "kb" / "journal/assumptions/A-new-a.md").read_text(encoding="utf-8")

    rc, out = kb("whoami", user="Али")
    assert rc == 0, out
    assert "A-new-a" not in out

    # контроль: настоящая Алия своё допущение видит
    rc, out = kb("whoami", user="Алия")
    assert rc == 0, out
    assert "A-new-a" in out


# ---------- человек узнаётся по почте; состав команды с историей ----------
def test_person_resolved_by_email_in_git_column(repo, kb):
    rc, out = kb("team", "set", "Мадина", "git=madina@corp.kz", user="Гульнара")
    assert rc == 0, out
    git(repo, "config", "user.email", "madina@corp.kz")
    rc, out = kb("whoami", user="madina.s")
    assert rc == 0, out
    assert "Имя: Мадина; роли: разработчик" in out
    assert "Вас нет в team.md" not in out


def test_unknown_person_gets_line_for_analyst(repo, kb):
    rc, out = kb("whoami", user="Незнакомец")
    assert "Вас нет в team.md" in out and "team add" in out


def test_team_add_set_remove_writes_history_and_checks_rights(repo, kb):
    rc, out = kb("team", "add", "Ира", "git=ira@corp.kz", "роли=тестировщик", user="Мадина")
    assert rc == 1 and "правит аналитик" in out
    rc, out = kb("team", "add", "Ира", "git=ira@corp.kz", "роли=тестировщик", "стиль=краткий", user="Гульнара")
    assert rc == 0, out
    rc, out = kb("team", "set", "Ира", "стиль=грубый", user="Гульнара")
    assert rc == 2 and "ОШИБКА" in out
    rc, out = kb("team", "remove", "Ира", "ушла в другой продукт", user="Гульнара")
    assert rc == 0, out
    t = (repo / "kb" / "team.md").read_text(encoding="utf-8")
    assert "| 2026-10-16 | + Ира: тестировщик | Гульнара |" in t
    assert "| 2026-10-16 | − Ира: ушла в другой продукт | Гульнара |" in t
    assert "| Ира |" not in t
    rc, out = kb("check")
    assert rc == 0, out


def test_team_remove_refuses_owner_of_root(repo, kb):
    mk_step(repo, "PM-01", "reg")
    rc, out = kb("set", "PM-01", "владелец рута=Тимур", user="Гульнара")
    assert rc == 0, out
    rc, out = kb("team", "remove", "Тимур", "уходит", user="Гульнара")
    assert rc == 1 and "PM-01" in out


# ---------- ответ на мой вопрос виден автору ----------
def test_whoami_shows_answers_to_author(repo, kb):
    mk_question(repo, "versiya", раздел="ROOT", заголовок="Какая версия 1С")
    rc, out = kb("append", "Q-new-versiya", "Ответ",
                 "администратор, 2026-10-15, materials/answers/x.md: 8.3. Записан в: ждёт владельца")
    assert rc == 0, out
    rc, out = kb("set", "Q-new-versiya", "статус=отвечен")
    assert rc == 0, out
    rc, out = kb("whoami")          # автор — Мадина
    assert "НОВОЕ: пришли ответы на ваши вопросы" in out and "Q-new-versiya" in out
    rc, out = kb("whoami", user="Тимур")
    assert "НОВОЕ" not in out


# ---------- заметки сессии и восстановление ----------
def test_note_and_resume(repo, kb):
    rc, out = kb("note", "разобрал Правила; дальше: Исключения")
    assert rc == 0, out
    (repo / "kb" / "product.md").write_text("правка\n", encoding="utf-8")
    rc, out = kb("whoami")
    assert "НЕЗАВЕРШЁННОЕ: не закоммичено файлов — 1" in out
    rc, out = kb("resume")
    assert rc == 0, out
    assert "product.md" in out and "дальше: Исключения" in out
    assert ".kb/" not in git(repo, "status", "--porcelain")


# ---------- правила письма ----------
def test_check_writing_hints_do_not_count_as_errors(repo, kb):
    p = repo / "kb" / "product.md"
    t = p.read_text(encoding="utf-8")
    long = " ".join(["слово"] * 30) + "."
    t = t.replace("<3–5 предложений", long + "\nСистема осуществляет контроль.\n<3–5 предложений", 1)
    p.write_text(t, encoding="utf-8")
    rc, out = kb("check")
    assert rc == 0, out
    assert "30 слов в предложении" in out and "осуществляет" in out


def test_append_creates_technical_notes_in_old_step(repo, kb):
    path = mk_step(repo, "PM-01", "reg")
    t = path.read_text(encoding="utf-8")
    t = re.sub(r"## Технические заметки\n.*?(?=## Нарезка)", "", t, flags=re.S)
    path.write_text(t, encoding="utf-8")
    rc, out = kb("append", "PM-01", "Технические заметки", "ключ — номер надписи (разработчик, 2026-10-16, идея)")
    assert rc == 0, out
    t = path.read_text(encoding="utf-8")
    assert t.index("## Технические заметки") < t.index("## Нарезка на декомпозиты")
    assert "- ключ — номер надписи" in t


# ---------- вопрос показывается на экране, а не только пишется в файл ----------
def test_new_question_prints_card_to_show(repo, kb):
    rc, out = kb("new", "question", "porog", "тип=бизнес-правило", "раздел=PM-01",
                 "заголовок=Почему долги до 10 000 тг не попадают в претензии?", "Контекст=в выгрузке их нет")
    assert rc == 0, out
    assert "ПОКАЖИ ЧЕЛОВЕКУ И СПРОСИ — Q-new-porog Почему долги до 10 000 тг" in out
    assert "кто ответит: только бизнес" in out and "контекст: в выгрузке их нет" in out
    rc, out = kb("show", "Q-new-porog")
    assert rc == 0 and "ответ: — нет" in out


def test_whoami_lists_questions_addressed_to_owner(repo, kb):
    mk_step(repo, "PM-01", "reg")
    rc, out = kb("set", "PM-01", "владелец рута=Тимур", user="Гульнара")
    assert rc == 0, out
    mk_question(repo, "klyuch", тип="факт о системе", заголовок="По какому ключу связать оплату")
    rc, out = kb("whoami", user="Тимур")
    assert "ВОПРОСЫ К ВАМ" in out and "Q-new-klyuch" in out and "вы владелец раздела" in out
    rc, out = kb("whoami")          # автор, Мадина, свой вопрос себе не задаёт
    assert "ВОПРОСЫ К ВАМ" not in out


# ---------- convert: материалы в текст ----------
import zipfile as _zip


def _docx(path, paragraphs, table=None):
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = "".join(f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in paragraphs)
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r) + "</w:tr>"
                       for r in table)
        body += f"<w:tbl>{rows}</w:tbl>"
    with _zip.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')


def _text_pdf(path, pages):
    """Минимальный PDF с текстом (латиница, Helvetica) — без внешних библиотек."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def test_convert_docx_zip_meeting_and_keeps_originals(repo, kb):
    inbox = repo / "kb" / "inbox"
    inbox.mkdir(exist_ok=True)
    _docx(inbox / "koncept.docx", ["Zvonim posle 10 dney"], table=[["Stadiya", "Srok"], ["SMS", "1 den"]])
    with _zip.ZipFile(inbox / "skript.zip", "w") as z:
        z.writestr("sborka/run.py", "print('paket')\n")
    (inbox / "vstrecha-smezhniki.txt").write_text(
        "\n".join(f"[00:0{i}:1{i}] Спикер {i % 2 + 1}: реплика {i}" for i in range(8)), encoding="utf-8")
    (inbox / "reglament.txt").write_text("Пункт 1. Претензия при долге больше 10 000.\nПункт 2. SMS.\n", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in inbox.iterdir() if p.is_file()}
    rc, out = kb("convert")
    assert rc == 0, out
    md = inbox / "_md"
    d = (md / "koncept.docx.md").read_text(encoding="utf-8")
    assert "Оригинал: kb/inbox/koncept.docx" in d and "| SMS | 1 den |" in d
    assert (md / "skript.zip" / "sborka" / "run.py").read_text(encoding="utf-8") == "print('paket')\n"
    assert "Тип: похоже на расшифровку встречи" in (md / "vstrecha-smezhniki.txt.md").read_text(encoding="utf-8")
    assert "ПОХОЖЕ НА РАСШИФРОВКУ ВСТРЕЧИ" in out
    assert not (md / "reglament.txt.md").exists() and (md / "reglament.txt").exists()   # обычный текст — копия как есть
    assert {p.name: p.read_bytes() for p in inbox.iterdir() if p.is_file()} == before   # оригиналы не тронуты
    rc, out = kb("convert")
    assert "Нового нет" in out
    rc, out = kb("peek", "kb/inbox/koncept.docx")
    assert rc == 1 and "convert" in out


def test_convert_pdf_text_and_scan(repo, kb):
    pytest.importorskip("pypdf")
    inbox = repo / "kb" / "inbox"
    inbox.mkdir(exist_ok=True)
    _text_pdf(inbox / "otvet.pdf", ["Otvet BPI: delo 123 zakryto", "Stranica dva"])
    _text_pdf(inbox / "scan.pdf", [""])
    rc, out = kb("convert")
    assert rc == 0, out
    t = (inbox / "_md" / "otvet.pdf.md").read_text(encoding="utf-8")
    assert "## стр. 1" in t and "delo 123" in t and "Страниц: 2" in t
    s = (inbox / "_md" / "scan.pdf.md").read_text(encoding="utf-8")
    assert "СКАН: текста нет" in s and "НЕ ПРОЧИТАНО: 1" in out


# ---------- встречи ----------
def test_meeting_from_convert_summary_index_whoami(repo, kb):
    inbox = repo / "kb" / "inbox"
    (inbox / "zapis-vstrechi.txt").write_text(
        "Встреча 2026-10-14\n" + "\n".join(f"[00:0{i}:00] Спикер {i % 2 + 1}: про выгрузку {i}" for i in range(8)),
        encoding="utf-8")
    rc, out = kb("convert")
    assert "ПОХОЖЕ НА РАСШИФРОВКУ ВСТРЕЧИ" in out, out
    mk_step(repo, "PM-01", "reg")
    rc, out = kb("set", "PM-01", "владелец рута=Тимур", user="Гульнара")
    rc, out = kb("new", "meeting", "smezhniki-bki", "из=kb/inbox/_md/zapis-vstrechi.txt.md",
                 "с кем=смежники, команда БКИ", "тема=Обмен реестрами", "шаги=[PM-01]")
    assert rc == 0, out
    m = repo / "kb" / "materials" / "meetings"
    assert (m / "2026-10-14-smezhniki-bki.md").exists()
    sm = (m / "2026-10-14-smezhniki-bki.summary.md").read_text(encoding="utf-8")
    assert "встреча: 2026-10-14" in sm and "шаги: [PM-01]" in sm
    assert "2026-10-14-smezhniki-bki.md" in (inbox / "_razobrano.md").read_text(encoding="utf-8")
    rc, out = kb("check")
    assert "саммари не заполнено" in out
    rc, out = kb("index")
    assert "| 2026-10-14 | смежники, команда БКИ | Обмен реестрами | PM-01 |" in (m / "_index.md").read_text(encoding="utf-8")
    rc, out = kb("whoami", user="Тимур")
    assert "НОВОЕ: встреча 2026-10-14" in out and "PM-01" in out


def test_meeting_without_summary_warns_and_needs_date(repo, kb):
    m = repo / "kb" / "materials" / "meetings"
    (m / "2026-10-01-yurist.md").write_text("Спикер 1: привет\n", encoding="utf-8")
    rc, out = kb("check")
    assert "встреча без саммари" in out
    src = repo / "kb" / "inbox" / "bez-daty.md"
    src.write_text("Спикер 1: текст\n", encoding="utf-8")
    rc, out = kb("new", "meeting", "x", "из=kb/inbox/bez-daty.md", "с кем=юрист", "тема=т")
    assert rc == 2 and "дата=ГГГГ-ММ-ДД" in out


# ---------- режим персональных данных ----------
def test_pd_mode_contour_allows_raw_materials_but_not_steps(repo, kb):
    mat = repo / "kb" / "materials" / "files"
    mat.mkdir(parents=True, exist_ok=True)
    (mat / "reestr.csv").write_text("ФИО;ИИН\nКлиент;890101300123\n", encoding="utf-8")
    rc, out = kb("check")
    assert rc == 1 and "персональные данные" in out          # по умолчанию — запрещены
    team = repo / "kb" / "team.md"
    t = team.read_text(encoding="utf-8")
    team.write_text(t.replace("лимит рутов на человека:", "персональные данные: в контуре\nлимит рутов на человека:", 1),
                    encoding="utf-8")
    rc, out = kb("check")
    assert rc == 0, out
    path = mk_step(repo, "PM-01", "reg")
    path.write_text(path.read_text(encoding="utf-8").replace("## Правила\n", "## Правила\n- ИИН 890101300123 (x, подтверждено)\n", 1),
                    encoding="utf-8")
    rc, out = kb("check")
    assert rc == 1 and "PM-01" in out and "персональные данные" in out
