"""Общие средства тестов tools/kb.py: фикстура-репозиторий и запуск kb.py как в жизни (подпроцесс)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# KB_TEMPLATE позволяет прогнать те же тесты на другой версии шаблона (например, на старом коммите).
TEMPLATE = Path(os.environ.get("KB_TEMPLATE") or Path(__file__).resolve().parent.parent / "project-template")
# Даты коммитов фиксированы: activity считает период по ним, а не по настоящему «сейчас».
BASE_DATE = "2026-10-01T10:00:00+00:00"
TODAY = "2026-10-16"
COMMIT_DATE = "2026-10-16T12:00:00+00:00"

TEAM_MD = """---
режим: соло
архитектор недели: Тимур
рабочие часы: 09:00–18:00
лимит рутов на человека: 1
---
# Команда продукта

| Имя | Роли в пуле | Чем владеет |
|---|---|---|
| Мадина | разработчик | |
| Гульнара | аналитик, дежурный по знаниям | |
| Тимур | разработчик | |

## Кто кому задаёт вопросы в бизнесе
| Роль в бизнесе | Через кого из пула |
|---|---|
| бухгалтер | Гульнара |

## История архитекторов недели
| Неделя | Архитектор |
|---|---|
| 2026-10-12 | Тимур |
"""


def _git_env(user: str | None = None, date: str | None = None) -> dict:
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if user:
        # переопределяет user.name из репозитория: так kb.py видит нужного «пользователя»
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "user.name"
        env["GIT_CONFIG_VALUE_0"] = user
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    return env


def git(repo: Path, *args: str, user: str | None = None, date: str | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=repo, env=_git_env(user, date), capture_output=True, text=True,
                       encoding="utf-8")
    assert r.returncode == 0, f"git {' '.join(args)} упал: {r.stdout}{r.stderr}"
    return r.stdout


def commit(repo: Path, message: str = "тест", user: str | None = None, date: str = COMMIT_DATE) -> None:
    """Коммит всего, что изменилось, с фиксированной датой."""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, user=user, date=date)


def run_kb(repo: Path, *args: str, user: str | None = None, today: str = TODAY,
           env: dict | None = None) -> tuple[int, str]:
    """python3 tools/kb.py *args в корне репозитория → (код возврата, stdout + stderr)."""
    e = _git_env(user)
    e["KB_TODAY"] = today
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    e.update(env or {})
    r = subprocess.run([sys.executable, "tools/kb.py", *args], cwd=repo, env=e, capture_output=True, text=True,
                       encoding="utf-8")
    return r.returncode, r.stdout + r.stderr


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Копия project-template с git-репозиторием, командой из трёх человек и одним коммитом."""
    root = tmp_path / "repo"
    shutil.copytree(TEMPLATE, root, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    (root / "team.md").write_text(TEAM_MD, encoding="utf-8", newline="\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Мадина")
    git(root, "config", "user.email", "m@x")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "старт", date=BASE_DATE)
    return root


@pytest.fixture
def kb(repo: Path):
    """kb(*args, user=None, today="2026-10-16") → (rc, вывод) для репозитория текущего теста."""
    def _kb(*args: str, user: str | None = None, today: str = TODAY, env: dict | None = None):
        return run_kb(repo, *args, user=user, today=today, env=env)
    return _kb


def mk_step(repo: Path, step_id: str, slug: str, **fields) -> Path:
    """Создаёт шаг через `kb new step`. Ключи с пробелами — через **{"главный объект": …}.
    Заглавная буква ключа — раздел файла, строчная — поле шапки."""
    args = {"шаг": "Регистрация надписей", "порядок": "1 из 2", "главный объект": "надпись"}
    args.update(fields)
    rc, out = run_kb(repo, "new", "step", slug, *[f"{k}={v}" for k, v in args.items()])
    assert rc == 0, out
    path = repo / "context" / "process" / f"{step_id}-{slug}.md"
    assert path.exists(), f"ожидался файл {path.name}, вывод: {out}"
    return path


def mk_question(repo: Path, slug: str, **fields) -> Path:
    """Создаёт вопрос через `kb new question`; по умолчанию тип=понимание, раздел=PM-01."""
    args = {"тип": "понимание", "раздел": "PM-01", "заголовок": "Т", "Контекст": "к"}
    args.update(fields)
    rc, out = run_kb(repo, "new", "question", slug, *[f"{k}={v}" for k, v in args.items()])
    assert rc == 0, out
    path = repo / "journal" / "questions" / f"Q-new-{slug}.md"
    assert path.exists(), out
    return path


def snapshot(repo: Path) -> dict[str, bytes]:
    """Содержимое всех файлов (без .git) — чтобы проверять «ничего не изменилось»."""
    return {p.relative_to(repo).as_posix(): p.read_bytes()
            for p in sorted(repo.rglob("*")) if p.is_file() and ".git" not in p.relative_to(repo).parts
            and "__pycache__" not in p.parts}
