# Тесты kb.py

```
pip install pytest        # один раз (на Windows: py -3 -m pip install pytest)
python3 -m pytest tools/tests -q
```

Каждый тест копирует шаблон (корень репозитория) во временную папку, делает `git init` и гоняет `tools/kb.py` как отдельный процесс — так же, как его запускают агенты. Другой шаблон: `KB_TEMPLATE=/путь python3 -m pytest tools/tests`.

Правите `kb.py` — запускайте тесты. Чините баг — сначала добавьте тест, который его ловит.
