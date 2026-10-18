#!/bin/sh
# Условие 5 цели: инструкция первого запуска проходит с чистого клона.
# Берёт ветку new-agent с GitHub, выполняет блоки «<!-- smoke -->» из agent/kb/docs/zapusk.md как есть.
# Шаги «заполните файлы проекта» подменяет тестовыми файлами — их заполняют люди.
set -eu
REPO=${REPO:-https://github.com/Igibaev/ai-pool-kb}
BRANCH=${BRANCH:-new-agent}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git clone -q --depth 1 -b "$BRANCH" "$REPO" "$WORK/src"
export AGENT_SRC="$WORK/src/agent"
export BASE="$WORK/product"
mkdir -p "$BASE" && git -C "$BASE" init -q
git -C "$BASE" config user.name "Разработчик-1"
git -C "$BASE" config user.email "dev1@example.local"
python3 - "$AGENT_SRC/kb/docs/zapusk.md" > "$WORK/blocks.sh" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
blocks = re.findall(r"<!-- smoke -->\s*```bash\n(.*?)```", text, re.S)
print("set -eu")
for n, block in enumerate(blocks, 1):
    print(f'echo "=== блок {n} ==="')
    print(block)
    if n == 1:   # «заполните файлы проекта» — тестовые файлы вместо людей
        print('cp "$AGENT_SRC/kb/tools/tests/fixture/team.md" "$BASE/kb/team.md"')
PY
echo "Блоков из инструкции: $(grep -c '=== блок' "$WORK/blocks.sh")"
sh "$WORK/blocks.sh"
echo "SMOKE: ИНСТРУКЦИЯ ПРОШЛА С ЧИСТОГО КЛОНА ($BRANCH, $(git -C "$WORK/src" rev-parse --short HEAD))"
