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
python3 kb/tools/kb.py similar "<формулировка>"     похожие вопросы и факты
python3 kb/tools/kb.py ask send <slug> шаг=… кто=… MVP=… блокирует=… откуда="…" вопрос="…"
python3 kb/tools/kb.py q sort|answer|close|out …   жизнь вопроса — kb/rules/formats.md, раздел 3
python3 kb/tools/kb.py views                       пересобрать списки вопросов journal/views/
python3 kb/tools/kb.py step gaps PM-XX             чего не хватает по критерию глубины (подсказка)
python3 kb/tools/kb.py step enough PM-XX           «мне достаточно» — только владелец
python3 kb/tools/kb.py step status PM-XX "<статус>" [--reason "…"]
python3 kb/tools/kb.py take PM-XX                  взять шаг; лимит — в team.md
python3 kb/tools/kb.py report morning [--since <дата|хеш>] [--save]   полный утренний отчёт по git
python3 kb/tools/kb.py where                       штурман: ты здесь, дальше, почему, модель
python3 kb/tools/kb.py models                      какие модели заданы (переменные KB_MODEL_*)
python3 kb/tools/kb.py notes take PM-XX <логин> <номер> "<Раздел>"   перенести находку в шаг (владелец)
python3 kb/tools/kb.py deps                        связи шагов для роадмапа → context/process/_deps.md; «устарело»
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
  kbquestions.py     вопросы: ask send, similar, q sort/answer/close/out, списки views
  kbsteps.py         шаг: step gaps/enough/status, take
  kbreport.py        утренний отчёт
  kbnotes.py         находки не-владельца и их перенос
  kbdeps.py          связи шагов, похожие шаги, «устарело»
  kbglossary.py      глоссарий: синонимы, дубли терминов
  hooks/pre-commit   проверка перед любым коммитом: материалы, .kb/, сгенерированное, kb check
  tests/
    oshibki.md       что инструмент обязан отклонить — каждая строка = тест с тем же номером
    test_kb.py       факты и check
    test_commit.py   коммит, хук, права opencode.json
    test_questions.py вопросы и списки
    test_steps.py    разбор, «достаточно», статусы, взятие
    test_report.py   отчёт: каждый изменённый файл в отчёте
    test_agents.py   штурман и агенты opencode.json
    test_extra.py    ПД, находки, связи шагов, глоссарий
                     все тесты: python3 -m unittest discover -s kb/tools/tests -v
    fixture/         маленькая база (выдуманный продукт) — данные для тестов
  eval/              стенд проверки агента на живой модели (порция 8)
```
