#!/bin/sh
# CI репозитория кода: база знаний живёт только в ветке context. Папка kb/ в ветке кода — ошибка.
if git ls-files --error-unmatch kb >/dev/null 2>&1 || [ -n "$(git ls-files kb)" ]; then
  echo "ОШИБКА: в ветке кода есть папка kb/. База знаний живёт только в ветке context — уберите kb/ из этой ветки."
  exit 1
fi
echo "kb/ в ветке кода нет — ок"
