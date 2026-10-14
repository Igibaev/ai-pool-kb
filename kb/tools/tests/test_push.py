"""kb.py push: все в context, гонка за рут, конфликты в журнале, показ свежих правок, текст для группы.

Сервер — голый репозиторий во временной папке; два человека — два клона: Мадина и Тимур.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from conftest import git, mk_question, mk_step, run_kb


@pytest.fixture
def pool(repo: Path, tmp_path: Path) -> dict[str, Path]:
    team = repo / "kb" / "team.md"
    team.write_text(team.read_text(encoding="utf-8").replace("режим: соло", "режим: команда\nосновная ветка: context"),
                    encoding="utf-8", newline="\n")
    mk_step(repo, "PM-01", "reg", приоритет="1", людей="1")
    step = repo / "kb" / "context" / "process" / "PM-01-reg.md"
    step.write_text(step.read_text(encoding="utf-8").replace("статус: Черновик", "статус: Готов к взятию", 1),
                    encoding="utf-8", newline="\n")
    mk_question(repo, "summa")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "подготовка")
    git(repo, "branch", "-M", "context")
    server = tmp_path / "server.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(server))
    people = {}
    for name in ("Мадина", "Тимур"):
        d = tmp_path / name
        git(tmp_path, "clone", "-q", "-b", "context", str(server), str(d))
        git(d, "config", "user.name", name)
        git(d, "config", "user.email", f"{name}@x")
        people[name] = d
    shutil.rmtree(repo)
    return people


def kb(d: Path, *args: str) -> tuple[int, str]:
    return run_kb(d, *args)


def head(d: Path, ref: str = "HEAD") -> str:
    return git(d, "rev-parse", ref).strip()


def test_take_race_second_gets_taken_by_other(pool):
    m, t = pool["Мадина"], pool["Тимур"]
    step = "kb/context/process/PM-01-reg.md"
    for d in (m, t):
        rc, out = kb(d, "take", "PM-01")
        assert rc == 0, out
        git(d, "commit", "-q", "-m", "PM-01: взял(а) рут", "--", step)
    rc, out = kb(m, "push")
    assert rc == 0, out
    rc, out = kb(t, "push")
    assert rc == 5, out
    assert "ВЗЯТО ДРУГИМ" in out and "Мадина" in out
    # взятие Тимура отменено, у него свежий context с Мадиной
    assert head(t) == head(t, "origin/context")
    assert "владелец рута: Мадина" in (t / step).read_text(encoding="utf-8")


def test_appends_to_same_section_merge_without_asking(pool):
    m, t = pool["Мадина"], pool["Тимур"]
    q = "kb/journal/questions/Q-new-summa.md"
    kb(m, "append", "Q-new-summa", "Подписчики", "Мадина — для PM-01")
    git(m, "commit", "-q", "-m", "Q: подписка", "--", q)
    kb(t, "append", "Q-new-summa", "Подписчики", "Тимур — для карты")
    git(t, "commit", "-q", "-m", "Q: подписка", "--", q)
    assert kb(m, "push")[0] == 0
    rc, out = kb(t, "push")
    assert rc == 3, out                      # сначала показывает свежие правки в его файле
    assert "свежие правки коллег" in out and "Мадина" in out
    before = head(t)
    assert head(t) == before                 # показ ничего не меняет
    rc, out = kb(t, "push", "--yes")
    assert rc == 0, out
    assert "оставлены обе" in out
    text = (t / q).read_text(encoding="utf-8")
    assert "- Мадина — для PM-01" in text and "- Тимур — для карты" in text
    assert "<<<<<<<" not in text and "<кто>" not in text.split("## Подписчики")[1].split("##")[0]


def test_same_line_changed_needs_human_then_continue(pool):
    m, t = pool["Мадина"], pool["Тимур"]
    q = "kb/journal/questions/Q-new-summa.md"
    for d, txt in ((m, "к: долг и пеня"), (t, "к: с НДС и без")):
        p = d / q
        p.write_text(re.sub(r"(?m)^к$", txt, p.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
        git(d, "commit", "-q", "-m", "Q: контекст", "--", q)
    assert kb(m, "push")[0] == 0
    rc, out = kb(t, "push", "--yes")
    assert rc == 4, out
    assert "НУЖЕН ЧЕЛОВЕК" in out and "в context:  к: долг и пеня" in out and "у вас:      к: с НДС и без" in out
    rc, out = kb(t, "push", "--continue")
    assert rc == 4 and "остались отметки" in out
    p = t / q
    p.write_text(re.sub(r"<<<<<<< .*?\n>>>>>>> [^\n]*\n", "к: долг и пеня, с НДС и без\n",
                        p.read_text(encoding="utf-8"), flags=re.S), encoding="utf-8", newline="\n")
    rc, out = kb(t, "push", "--continue")
    assert rc == 0, out
    assert head(t) == head(t, "origin/context")


def test_push_blocked_by_check_errors(pool):
    m = pool["Мадина"]
    q = m / "kb/journal/questions/Q-new-summa.md"
    q.write_text(q.read_text(encoding="utf-8") + "\nСм. PM-09.\n", encoding="utf-8")
    git(m, "commit", "-q", "-am", "ссылка в никуда")
    rc, out = kb(m, "push")
    assert rc == 1 and "kb.py check" in out
    assert head(m, "origin/context") != head(m)


def test_new_cards_announced_for_the_team(pool):
    m = pool["Мадина"]
    step = "kb/context/process/PM-01-reg.md"
    kb(m, "take", "PM-01")
    git(m, "commit", "-q", "-m", "PM-01: взял(а) рут", "--", step)
    rc, out = kb(m, "new", "card", "PM-01", "isklyucheniya", "заголовок=Исключения", "Что выяснить=какие строки отбрасывает")
    assert rc == 0, out
    git(m, "add", "kb/context/process/cards")
    git(m, "commit", "-q", "-m", "PM-01: карточки")
    rc, out = kb(m, "push")
    assert rc == 0, out
    assert "НОВЫЕ КАРТОЧКИ — можно брать" in out and "PM-01-K01 «Исключения»" in out
    rc, again = kb(m, "announce")
    assert rc == 0 and "PM-01-K01" in again


def test_take_refused_outside_context(pool):
    t = pool["Тимур"]
    git(t, "checkout", "-q", "-b", "своя")
    rc, out = kb(t, "take", "PM-01")
    assert rc == 1 and "берут только в context" in out


def test_separate_branch_only_on_request_and_into_context(pool):
    m, t = pool["Мадина"], pool["Тимур"]
    rc, out = kb(m, "branch")
    assert rc == 2 and "Без просьбы человека" in out
    rc, out = kb(m, "branch", "context-pm01-draft")
    assert rc == 0, out
    assert git(m, "branch", "--show-current").strip() == "context-pm01-draft"
    q = "kb/journal/questions/Q-new-summa.md"
    kb(m, "append", "Q-new-summa", "Подписчики", "Мадина — черновик")
    git(m, "commit", "-q", "-m", "Q: черновик", "--", q)
    # отправка из отдельной ветки уходит в неё же, context не трогается
    before = head(m, "origin/context")
    rc, out = kb(m, "push")
    assert rc == 0, out
    git(m, "fetch", "-q")
    assert head(m, "origin/context-pm01-draft") == head(m)
    assert head(m, "origin/context") == before
    # whoami предупреждает, что ветка отдельная
    rc, out = kb(m, "whoami")
    assert "отдельная, не context" in out
    # тем временем Тимур отправил в context
    kb(t, "append", "Q-new-summa", "Подписчики", "Тимур — для карты")
    git(t, "commit", "-q", "-m", "Q: подписка", "--", q)
    assert kb(t, "push")[0] == 0
    # по просьбе человека — влить в context: обновится, сольёт дописанные строки, отправит
    rc, out = kb(m, "push", "--into", "context", "--yes")
    assert rc == 0, out
    assert "влита в context" in out and "Вы остаётесь в context-pm01-draft" in out
    git(m, "fetch", "-q")
    text = git(m, "show", "origin/context:" + q)
    assert "Мадина — черновик" in text and "Тимур — для карты" in text
    assert git(m, "branch", "--show-current").strip() == "context-pm01-draft"
