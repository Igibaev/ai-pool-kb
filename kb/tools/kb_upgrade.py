"""Обновить инструмент в репозитории продукта из шаблона: kb.py upgrade --from <адрес или путь> [--ref main].

Обновляет только инструмент: правила, скиллы, команды OpenCode, kb/tools, kb/docs, шаблоны и README внутри kb/.
Знания продукта (product.md, team.md, шаги, карточки, журнал, материалы, inbox, датасеты, спеки, ядро) не трогает.
Не коммитит: в конце — что изменилось и как проверить.
"""
from __future__ import annotations

import filecmp
import shutil
import subprocess
import tempfile
from pathlib import Path

TOOL_FILES = ["AGENTS.md", "CLAUDE.md", "opencode.json", ".gitlab-ci.yml"]
TOOL_DIRS = [".claude", ".opencode", ".github", "kb/tools", "kb/docs"]
SKIP = {"__pycache__", ".pytest_cache"}


def _files(base: Path, rel_dir: str) -> list[str]:
    d = base / rel_dir
    if not d.is_dir():
        return []
    return sorted(p.relative_to(base).as_posix() for p in d.rglob("*")
                  if p.is_file() and not (set(p.relative_to(base).parts) & SKIP))


def _template_bits(base: Path) -> list[str]:
    """Шаблоны и README внутри kb/: _template*, README.md — кроме знаний продукта."""
    out = []
    kb = base / "kb"
    if not kb.is_dir():
        return out
    for p in kb.rglob("*"):
        if not p.is_file() or set(p.relative_to(base).parts) & SKIP:
            continue
        r = p.relative_to(base).as_posix()
        if r.startswith(("kb/tools/", "kb/docs/", "kb/game/")):
            continue
        if p.name == "README.md" or p.name.startswith("_template") or "_template" in p.parts or p.name.endswith("-template.md"):
            out.append(r)
    return sorted(out)


def plan(src: Path) -> list[str]:
    files = [f for f in TOOL_FILES if (src / f).is_file()]
    for d in TOOL_DIRS:
        files += _files(src, d)
    files += _template_bits(src)
    return sorted(set(files))


def _team_fields(path: Path, kb) -> list[str]:
    if not path.is_file():
        return []
    return list(kb.parse_frontmatter(path.read_text(encoding="utf-8")).keys())


def run(args: list[str], kb) -> int:
    if "--from" not in args or args.index("--from") + 1 >= len(args):
        print("Как: python3 kb/tools/kb.py upgrade --from <git-адрес или путь к шаблону> [--ref main]\n"
              "Пример: upgrade --from https://github.com/Igibaev/kb-template")
        return 2
    src_arg = args[args.index("--from") + 1]
    ref = args[args.index("--ref") + 1] if "--ref" in args and args.index("--ref") + 1 < len(args) else "main"
    dst = kb.ROOT
    with tempfile.TemporaryDirectory() as td:
        src = Path(src_arg).expanduser()
        if not (src / "kb" / "tools" / "kb.py").is_file():
            src = Path(td) / "tpl"
            r = subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", ref, src_arg, str(src)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f"ОШИБКА  не получил шаблон {src_arg} ({ref}): {r.stderr.strip()[:300]}")
                return 1
        if src.resolve() == dst.resolve():
            print("ОШИБКА  шаблон и репозиторий — одна папка")
            return 1
        changed, added = [], []
        for f in plan(src):
            s, d = src / f, dst / f
            if d.is_file() and filecmp.cmp(s, d, shallow=False):
                continue
            (added if not d.exists() else changed).append(f)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(s, d)
        # .gitignore — дописать недостающие строки, свои не трогать
        gi_s, gi_d = src / ".gitignore", dst / ".gitignore"
        if gi_s.is_file():
            have = gi_d.read_text(encoding="utf-8").splitlines() if gi_d.exists() else []
            new = [l for l in gi_s.read_text(encoding="utf-8").splitlines() if l.strip() and l not in have]
            if new:
                with open(gi_d, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(("\n" if have and have[-1].strip() else "") + "\n".join(new) + "\n")
                changed.append(".gitignore")
        # скиллы, которых в шаблоне больше нет — не удаляем, показываем
        gone = sorted({p.parent.name for p in (dst / ".claude/skills").glob("*/SKILL.md")}
                      - {p.parent.name for p in (src / ".claude/skills").glob("*/SKILL.md")})
        miss_fields = [k for k in _team_fields(src / "kb/team.md", kb) if k not in _team_fields(dst / "kb/team.md", kb)]
    print(f"Обновлено файлов: {len(changed)}, новых: {len(added)}. Знания продукта не тронуты.")
    for f in added:
        print(f"  + {f}")
    for f in changed:
        print(f"  ~ {f}")
    if gone:
        print("В шаблоне нет скиллов: " + ", ".join(gone) + " — свои? оставьте; устарели? удалите руками.")
    if miss_fields:
        print("В шапке kb/team.md шаблона есть поля, которых нет у вас: " + ", ".join(miss_fields)
              + " — добавьте их в kb/team.md (значения — в шаблоне, комментарием после #).")
    print("Дальше: python3 kb/tools/kb.py check; git diff — посмотреть; "
          f"git commit -m \"Обновление инструмента из шаблона ({ref})\" — только эти файлы.")
    return 0
