"""Декомпозиты: delivery/PM-XX-NN-<slug>.md. Статусы, спека, «нет кода без принятой спеки», ранний срез.

Статусы декомпозита: Нарезан → Спека → Сборка → Ревью → Приёмка → Готово.
В «Сборку» — только с принятой спекой или с ранним срезом. Ранний срез — с причиной и когда всё
неподтверждённое в шаге оформлено допущениями.
"""
from __future__ import annotations

import re
from pathlib import Path

import kbbase
import kbquestions
import kbsteps

STATUSES = ["Нарезан", "Спека", "Сборка", "Ревью", "Приёмка", "Готово"]
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
UNCONFIRMED = re.compile(r";\s*(оценка|противоречие)\)\s*$")


def decomposites(root: Path, step: str | None = None) -> list[Path]:
    folder = root / "delivery"
    if not folder.is_dir():
        return []
    pattern = f"{step}-[0-9][0-9]-*.md" if step else "PM-*-[0-9][0-9]-*.md"
    return sorted(folder.glob(pattern))


def load(root: Path, ident: str) -> tuple[Path, list[str], dict[str, str], int] | None:
    m = re.match(r"^(PM-\d+)-(\d{2})$", ident)
    found = [p for p in decomposites(root, m.group(1)) if p.stem.startswith(ident + "-")] if m else []
    if not found:
        print(f"ОШИБКА: декомпозита {ident} нет в `delivery/`")
        return None
    lines = kbbase.read_lines(found[0])
    head, body = kbbase.front_matter(lines)
    return found[0], lines, head, body


def new(root: Path, person: str, step: str, slug: str, args: list[str]) -> int:
    loaded = kbsteps.load(root, step)
    if loaded is None:
        return 1
    _, _, head, _ = loaded
    if person != head.get("владелец"):
        print(f"ОШИБКА: режет шаг владелец: {head.get('владелец')}")
        return 1
    if head.get("статус") not in ("Нарезка", "Приёмка", "Декомпозиты в работе"):
        print(f"ОШИБКА: декомпозиты создают в «Нарезке». {step} — «{head.get('статус')}»")
        return 1
    if not SLUG.match(slug):
        print(f"ОШИБКА: короткое имя «{slug}» — латиница, цифры и дефис")
        return 1
    fields = kbquestions.parse_fields(args)
    name = fields.get("название", "")
    pain = fields.get("боль", "")
    if not name or not pain:
        print('ОШИБКА: нужно название="…" и боль="…" — какую боль бизнеса убирает декомпозит')
        return 1
    number = len(decomposites(root, step)) + 1
    ident = f"{step}-{number:02d}"
    text = ["---", f"id: {ident}", f"шаг: {step}", f"название: {name}", "статус: Нарезан", f"владелец: {person}",
            f"зависит от: {fields.get('зависит', '—')}", "спека: —", "спека принята: —", "ранний срез: —", "---",
            f"# {ident} {name}", "", "## Какую боль убирает", pain, "", "## Допущения", "", "## История статуса", "",
            f"- {kbbase.today().isoformat()} создан — {person} (kb dec new)", ""]
    folder = root / "delivery"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ident}-{slug}.md"
    path.write_text("\n".join(text), encoding="utf-8")
    print(f"ДЕКОМПОЗИТ: {kbbase.rel(root, path)}")
    return 0


def unconfirmed(root: Path, step: str) -> int:
    loaded = kbsteps.load(root, step)
    if loaded is None:
        return 0
    return sum(1 for l in loaded[1] if UNCONFIRMED.search(l))


def set_spec(root: Path, person: str, ident: str, link: str) -> int:
    loaded = load(root, ident)
    if loaded is None:
        return 1
    path, lines, head, body = loaded
    if person != head.get("владелец"):
        print(f"ОШИБКА: спеку указывает владелец декомпозита: {head.get('владелец')}")
        return 1
    if not link.strip():
        print("ОШИБКА: нужна ссылка на спеку: путь в репозитории кода или MR")
        return 1
    kbquestions.set_field(lines, "спека", link.strip())
    kbsteps.add_history(lines, body, f"- {kbbase.today().isoformat()} спека: {link.strip()} — {person}")
    kbsteps.save(path, lines)
    print(f"СПЕКА: {ident} — {link.strip()}. Принимает архитектор недели: kb spec accept {ident}")
    return 0


def accept(root: Path, team: kbbase.Team, person: str, ident: str) -> int:
    loaded = load(root, ident)
    if loaded is None:
        return 1
    path, lines, head, body = loaded
    architect = team.head.get("архитектор недели", "")
    if person != architect:
        print(f"ОШИБКА: спеку принимает архитектор недели: {architect}")
        return 1
    if head.get("спека", "—") in ("", "—"):
        print(f"ОШИБКА: у {ident} нет спеки. Владелец указывает её: kb spec set {ident} \"<ссылка>\"")
        return 1
    today = kbbase.today().isoformat()
    kbquestions.set_field(lines, "спека принята", f"{person}, {today}")
    kbsteps.add_history(lines, body, f"- {today} спека принята — {person} (kb spec accept)")
    kbsteps.save(path, lines)
    print(f"СПЕКА ПРИНЯТА: {ident}")
    return 0


def early(root: Path, person: str, ident: str, reason: str) -> int:
    loaded = load(root, ident)
    if loaded is None:
        return 1
    path, lines, head, body = loaded
    if person != head.get("владелец"):
        print(f"ОШИБКА: ранний срез решает владелец декомпозита: {head.get('владелец')}")
        return 1
    if not reason:
        print('ОШИБКА: ранний срез — только с причиной: --reason "…"')
        return 1
    left = unconfirmed(root, head.get("шаг", ""))
    if left:
        print(f"ОШИБКА: в шаге {head.get('шаг')} неподтверждённых фактов: {left} (оценка, противоречие). "
              "Ранний срез — когда всё неподтверждённое оформлено допущениями")
        return 1
    today = kbbase.today().isoformat()
    kbquestions.set_field(lines, "ранний срез", reason)
    kbsteps.add_history(lines, body, f"- {today} ранний срез — {person}, причина: {reason}")
    kbsteps.save(path, lines)
    print(f"РАННИЙ СРЕЗ: {ident} — {reason}")
    return 0


def status(root: Path, team: kbbase.Team, person: str, ident: str, new_status: str, reason: str) -> int:
    loaded = load(root, ident)
    if loaded is None:
        return 1
    path, lines, head, body = loaded
    old = head.get("статус", "")
    if new_status not in STATUSES:
        print(f"ОШИБКА: неизвестный статус «{new_status}». Статусы: {', '.join(STATUSES)}")
        return 1
    if new_status == old:
        print(f"Статус уже «{new_status}»")
        return 0
    architect = team.head.get("архитектор недели", "")
    if new_status == "Готово" and person != architect:
        print(f"ОШИБКА: декомпозит принимает архитектор недели: {architect}")
        return 1
    if new_status != "Готово" and person not in (head.get("владелец"), architect):
        print(f"ОШИБКА: статус декомпозита меняет владелец: {head.get('владелец')}")
        return 1
    i_old, i_new = STATUSES.index(old), STATUSES.index(new_status)
    if i_new > i_old + 1:
        print(f"ОШИБКА: вперёд — только на один статус. Следующий: «{STATUSES[i_old + 1]}»")
        return 1
    if i_new < i_old and not reason:
        print('ОШИБКА: назад — только с причиной: --reason "…"')
        return 1
    if new_status == "Сборка":
        accepted = head.get("спека принята", "—") not in ("", "—")
        early_slice = head.get("ранний срез", "—") not in ("", "—")
        if not accepted and not early_slice:
            print(f"ОШИБКА: нет кода без принятой спеки. Архитектор недели: kb spec accept {ident}. "
                  f'Или ранний срез с причиной: kb spec early {ident} --reason "…"')
            return 1
    today = kbbase.today().isoformat()
    kbquestions.set_field(lines, "статус", new_status)
    kbsteps.add_history(lines, body, f"- {today} {old} → {new_status} — {person}" + (f", причина: {reason}" if reason else ""))
    kbsteps.save(path, lines)
    print(f"СТАТУС: {ident} {old} → {new_status}")
    return 0
