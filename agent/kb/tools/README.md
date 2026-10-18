# Инструмент `kb`

Ворота флоу держит этот инструмент, а не промпт. Нужен только Python 3.9+, без библиотек.

```
python3 kb/tools/kb.py check                       проверка всей базы: факты, шапки, язык
python3 kb/tools/kb.py fact <ID> "<Раздел>" "<текст>" "<источник>" <статус>
python3 kb/tools/kb.py fact <ID> "<Раздел>" "- <текст> (ист: <источник>; <статус>)"
python3 kb/tools/kb.py whoami                      кто я по git config и team.md
python3 kb/tools/kb.py commit plan [файлы] [-m "…"]   что войдёт в коммит; запоминает отпечаток
python3 kb/tools/kb.py commit apply <id>           коммит, если с показа ничего не изменилось
python3 kb/tools/kb.py hooks                       подключить проверку перед коммитом (один раз на клон)
```

`fact` поправляет близкие формы ввода и пишет «ПОПРАВЛЕНО: …». Ошибка — «НЕ ЗАПИСАНО», файл не меняется.
`check` — код выхода 1, если есть ошибки. Предупреждения не мешают.

```
tools/
  kb.py              команды
  kbbase.py          где база, шапка, разделы, team.md
  kbfacts.py         факт: разбор, поправки, проверка источника и статуса
  kbcheck.py         kb check
  kbgit.py           kb commit plan / apply, pre-commit
  hooks/pre-commit   проверка перед любым коммитом: материалы, .kb/, сгенерированное, kb check
  tests/
    oshibki.md       что инструмент обязан отклонить — каждая строка = тест с тем же номером
    test_kb.py       факты и check
    test_commit.py   коммит, хук, права opencode.json
                     все тесты: python3 -m unittest discover -s kb/tools/tests -v
    fixture/         маленькая база (выдуманный продукт) — данные для тестов
  eval/              стенд проверки агента на живой модели (порция 8)
```
