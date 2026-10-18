#!/usr/bin/env python3
"""Инструмент базы знаний. Ворота флоу держит он, а не промпт.

    python3 kb/tools/kb.py check
    python3 kb/tools/kb.py fact <ID> "<Раздел>" "<текст>" "<источник>" <статус>
    python3 kb/tools/kb.py fact <ID> "<Раздел>" "- <текст> (ист: <источник>; <статус>)"
    python3 kb/tools/kb.py whoami
    python3 kb/tools/kb.py commit plan [файлы] [-m "сообщение"]
    python3 kb/tools/kb.py commit apply <id>
    python3 kb/tools/kb.py hooks              подключить проверку перед коммитом
    python3 kb/tools/kb.py similar "<формулировка>"      похожие вопросы и факты — до того, как предлагать вопрос
    python3 kb/tools/kb.py ask send <slug> шаг=PM-02 кто=бизнес MVP=да блокирует=да откуда="…" вопрос="…" [контекст="…"]
    python3 kb/tools/kb.py q sort Q-… кто=<бизнес|смежник|команда|аналитик|сами>      (аналитик)
    python3 kb/tools/kb.py q answer Q-… "<ответ>" "<источник>" <статус> [записан=PM-02#Вход]
    python3 kb/tools/kb.py q close Q-…        (автор вопроса)
    python3 kb/tools/kb.py q out Q-… Q-… "<причина>"     (аналитик)
    python3 kb/tools/kb.py views              пересобрать списки вопросов
    python3 kb/tools/kb.py step gaps PM-XX    чего не хватает по критерию глубины
    python3 kb/tools/kb.py step enough PM-XX  «мне достаточно, чтобы писать спецификацию» — только владелец
    python3 kb/tools/kb.py step status PM-XX "<статус>" [--reason "…"]
    python3 kb/tools/kb.py take PM-XX         взять шаг
    python3 kb/tools/kb.py report morning [--since <ГГГГ-ММ-ДД|хеш>] [--save]   полный утренний отчёт
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kbbase  # noqa: E402
import kbcheck  # noqa: E402
import kbfacts  # noqa: E402
import kbgit  # noqa: E402
import kbquestions  # noqa: E402
import kbsteps  # noqa: E402
import kbreport  # noqa: E402

# «Ответ» вопроса пишет `kb q answer` (порция 4): у него служебные строки «Закрыл», «Записан в»
FACT_SECTIONS = {"PM": kbcheck.STEP_FACTS, "Q": {"Что уже знаем"}}


def cmd_check(args: list[str]) -> int:
    rep = kbcheck.run(kbbase.kb_root())
    rep.print()
    return 1 if rep.errors() else 0


def cmd_fact(args: list[str]) -> int:
    if len(args) not in (3, 5):
        print('ОШИБКА: нужно `kb fact <ID> "<Раздел>" "<текст>" "<источник>" <статус>` '
              'или `kb fact <ID> "<Раздел>" "- <текст> (ист: <источник>; <статус>)"`')
        return 1
    ident, section = args[0], args[1]
    root = kbbase.kb_root()
    team = kbbase.Team(root)
    path = kbbase.find_object(root, ident)
    if path is None:
        print(f"ОШИБКА: {ident} нет в базе")
        return 1
    line = args[2] if len(args) == 3 else f"- {args[2]} (ист: {args[3]}; {args[4]})"
    fact, fixes, problems = kbfacts.check(root, team, line)
    for f in fixes:
        print(f)
    errors = [p for p in problems if p.level == "ОШИБКА"]
    for p in problems:
        print(f"{p.level}: {p.message}")
    if errors or fact is None:
        print("НЕ ЗАПИСАНО")
        return 1

    lines = kbbase.read_lines(path)
    _, body = kbbase.front_matter(lines)
    secs = {name: (start, end) for name, start, end in kbbase.sections(lines, body)}
    allowed = FACT_SECTIONS.get(ident.split("-")[0])
    if section not in secs or (allowed and section not in allowed):
        names = [n for n in secs if not allowed or n in allowed]
        print(f"ОШИБКА: в {ident} нет раздела для фактов «{section}». Разделы: {', '.join(names)}")
        print("НЕ ЗАПИСАНО")
        return 1
    start, end = secs[section]
    last = start
    for i in range(start + 1, end):
        if lines[i].strip():
            last = i
    new_line = fact.line()
    numbered = [lines[i] for i in range(start + 1, end) if kbcheck.ITEM.match(lines[i]) and lines[i][0].isdigit()]
    if numbered:
        new_line = f"{len(numbered) + 1}. {new_line[2:]}"
    lines.insert(last + 1, new_line)
    if last == start:
        lines.insert(last + 1, "")
        if last + 3 < len(lines) and lines[last + 3].strip():
            lines.insert(last + 3, "")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"ЗАПИСАНО: {kbbase.rel(root, path)}, раздел «{section}»:")
    print(new_line)
    return 0


def git_config(key: str) -> str:
    r = subprocess.run(["git", "config", key], capture_output=True, text=True)
    return r.stdout.strip()


def cmd_whoami(args: list[str]) -> int:
    root = kbbase.kb_root()
    team = kbbase.Team(root)
    email, name = git_config("user.email"), git_config("user.name")
    person = team.by_git(email, name)
    if person is None:
        print(f"Вас нет в `team.md` (git: {name or '—'} <{email or '—'}>).")
        print(f"Передайте аналитику строку: | {name or '<Имя>'} | {email or '<почта>'} | <роль> |")
        return 1
    print(f"Имя: {person}")
    print(f"Роли: {team.roles.get(person) or '—'}")
    return 0


def current_person(team: kbbase.Team) -> str | None:
    person = team.by_git(git_config("user.email"), git_config("user.name"))
    if person is None:
        print("ОШИБКА: вас нет в `team.md`. Проверьте: python3 kb/tools/kb.py whoami")
    return person


def cmd_similar(args: list[str]) -> int:
    if not args:
        print('ОШИБКА: нужно `kb similar "<формулировка>"`')
        return 1
    found = kbquestions.similar(kbbase.kb_root(), " ".join(args))
    if not found:
        print("Похожего нет")
    for score, ident, what in found:
        print(f"{ident} ({score:.0%}): {what}")
    print("Совпадение слов — только кандидат. Дубль ли это, решает человек.")
    return 0


def cmd_ask(args: list[str]) -> int:
    if len(args) < 2 or args[0] != "send":
        print("ОШИБКА: нужно `kb ask send <slug> шаг=… кто=… откуда=… вопрос=\"…\"`")
        return 1
    root = kbbase.kb_root()
    team = kbbase.Team(root)
    person = current_person(team)
    if person is None:
        return 1
    return kbquestions.ask_send(root, team, person, args[1], args[2:])


def cmd_q(args: list[str]) -> int:
    root = kbbase.kb_root()
    team = kbbase.Team(root)
    sub = args[0] if args else ""
    if sub == "answer" and len(args) >= 3:
        return kbquestions.answer(root, team, args[1], args[2:])
    if sub in ("sort", "close", "out") and len(args) >= 2:
        person = current_person(team)
        if person is None:
            return 1
        if sub == "sort":
            return kbquestions.sort(root, team, person, args[1], args[2:])
        if sub == "close":
            return kbquestions.close(root, person, args[1])
        return kbquestions.out(root, team, person, args[1:])
    print("ОШИБКА: нужно `kb q sort|answer|close|out …` — подробно: python3 kb/tools/kb.py --help")
    return 1


def cmd_step(args: list[str]) -> int:
    root = kbbase.kb_root()
    team = kbbase.Team(root)
    sub = args[0] if args else ""
    if sub == "gaps" and len(args) == 2:
        return kbsteps.gaps(root, args[1])
    if sub in ("enough", "status") and len(args) >= 2:
        person = current_person(team)
        if person is None:
            return 1
        if sub == "enough" and len(args) == 2:
            return kbsteps.enough(root, person, args[1])
        if sub == "status" and len(args) >= 3:
            rest = args[2:]
            reason = ""
            if "--reason" in rest:
                i = rest.index("--reason")
                reason = " ".join(rest[i + 1:]).strip()
                rest = rest[:i]
                if not reason:
                    print('ОШИБКА: после --reason нужна причина: --reason "…"')
                    return 1
            return kbsteps.status(root, team, person, args[1], " ".join(rest).strip(), reason)
    print('ОШИБКА: нужно `kb step gaps|enough PM-XX` или `kb step status PM-XX "<статус>" [--reason "…"]`')
    return 1


def cmd_take(args: list[str]) -> int:
    if len(args) != 1:
        print("ОШИБКА: нужно `kb take PM-XX`")
        return 1
    root = kbbase.kb_root()
    team = kbbase.Team(root)
    person = current_person(team)
    return 1 if person is None else kbsteps.take(root, team, person, args[0])


def cmd_report(args: list[str]) -> int:
    if args[:1] != ["morning"]:
        print("ОШИБКА: нужно `kb report morning [--since <дата|хеш>] [--save]`")
        return 1
    since = None
    if "--since" in args:
        i = args.index("--since")
        if i + 1 >= len(args):
            print("ОШИБКА: после --since нужна дата ГГГГ-ММ-ДД или хеш коммита")
            return 1
        since = args[i + 1]
    return kbreport.morning(kbbase.kb_root(), since, "--save" in args)


def cmd_views(args: list[str]) -> int:
    changed = kbquestions.write_views(kbbase.kb_root())
    print(f"Списки пересобраны: {', '.join(changed)}" if changed else "Списки не изменились")
    return 0


def cmd_commit(args: list[str]) -> int:
    root = kbbase.kb_root()
    if args[:1] == ["plan"]:
        rest, message = args[1:], None
        if "-m" in rest:
            i = rest.index("-m")
            if i + 1 >= len(rest):
                print('ОШИБКА: после -m нужно сообщение: -m "текст"')
                return 1
            message, rest = rest[i + 1], rest[:i] + rest[i + 2:]
        person = current_person(kbbase.Team(root))
        if person is None:
            return 1
        return kbgit.plan(root, rest, message, person)
    if args[:1] == ["apply"] and len(args) == 2:
        return kbgit.apply(root, args[1])
    print("ОШИБКА: нужно `kb commit plan` или `kb commit apply <id>`")
    return 1


def cmd_precommit(args: list[str]) -> int:
    return kbgit.precommit(kbbase.kb_root())


def cmd_hooks(args: list[str]) -> int:
    return kbgit.install_hooks(kbbase.kb_root())


COMMANDS = {"check": cmd_check, "fact": cmd_fact, "whoami": cmd_whoami, "commit": cmd_commit,
            "precommit": cmd_precommit, "hooks": cmd_hooks, "similar": cmd_similar, "ask": cmd_ask,
            "q": cmd_q, "views": cmd_views, "step": cmd_step, "take": cmd_take,
            "report": cmd_report}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help") or argv[0] not in COMMANDS:
        print(__doc__.strip())
        return 0 if argv and argv[0] in ("-h", "--help", "help") else 1
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
