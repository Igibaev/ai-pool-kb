# Установка и первый запуск

## Что нужно на компьютере

| Что | Зачем | Как проверить |
|---|---|---|
| Git | История, ветки, авторство правок | `git --version` |
| Python 3.9 или новее | `kb/tools/kb.py`: проверка, сводки, номера | `python3 --version` (Windows: `python --version` или `py -3 --version`) |
| Claude desktop, вкладка Code — или Claude Code в терминале | Агент, который выполняет скиллы | В Claude desktop есть вкладка Code |

OpenCode тоже работает с этим репозиторием, в том числе со своей моделью в контуре: агент `kb`, команды `/kb-…`, экзамен модели — в [opencode.md](opencode.md).

## 1. Создать репозиторий продукта

1. Новый продукт — новый репозиторий из шаблона `kb-template`. На GitHub: «Use this template». Иначе — клон без истории шаблона:
   ```
   git clone --depth 1 https://github.com/Igibaev/kb-template moy-produkt
   cd moy-produkt
   rm -rf .git kb/game
   git init -b main
   ```
   Папка `kb/game/` нужна только для игры — в продукте её удалите.
2. В этой папке:
   ```
   git config user.name "Адай"
   git config user.email "adai@company.kz"
   git add -A
   git commit -m "Шаблон проекта"
   ```
   Имя и почту настройте для этого репозитория. Аналитик впишет их в `kb/team.md`: агент узнаёт человека по почте, а если почты нет — по имени.
3. Проверка:
   ```
   python3 kb/tools/kb.py check
   ```
   Ожидается: `Итог: ошибок 0`.

**GitLab (для режима «команда»).** Создайте пустой проект, `git remote add origin …`, `git push -u origin main`. `.gitlab-ci.yml` уже в репозитории. Для соло-пилота GitLab не нужен. Настройки:

| Где | Что | Зачем |
|---|---|---|
| Settings → Repository → Protected branches | `context`: Allowed to push — Developers | Все работают в `context` и отправляют через `kb.py push`; иначе push отклонят. Подробно — [git.md](git.md) |
| Settings → CI/CD → Variables | `KB_PUSH_TOKEN` — токен с правом push в `context` (masked) | CI коммитит номера и сводки после каждого push в `context` |
| Settings → Merge requests → Approvals | Не обязательно: обязательное ревью только для первого рута новичка, чужого файла и ядра — назначают ревьюера вручную | — |

## 2. Открыть в Claude desktop

1. Вкладка **Code** → выбрать папку репозитория. Папка должна быть корнем репозитория: там, где лежат `CLAUDE.md` и `.claude/`.
2. Наберите `/` — в списке должны быть `kb-next`, `kb-init`, `kb-map` и остальные. Нет — проверьте, что открыта нужная папка и в ней есть `.claude/skills/`.
3. Claude читает `CLAUDE.md`, который подключает `AGENTS.md` с правилами проекта.

## 3. Разрешения

В `.claude/settings.json` заранее разрешены безопасные команды: `kb.py`, чтение git, `git add`, `git commit`, переключение веток. Claude не будет спрашивать о них каждый раз. Push и всё остальное — с вашим подтверждением.

## 4. Первый запуск

```
/kb-next
```

В новом репозитории агент скажет, что `kb/product.md` пуст и следующий шаг — `/kb-init`.

## Если что-то не так

| Симптом | Что сделать |
|---|---|
| `python3: command not found` | Windows: агент сам должен перейти на `python` или `py -3` (это в `AGENTS.md`). Если нет — скажите ему |
| Кириллица в выводе `kb.py` — знаки вопроса | Обновите Python до 3.9+; в `kb.py` вывод уже переключён на UTF-8 |
| Агент говорит «вы не владелец», хотя вы владелец | `python3 kb/tools/kb.py whoami`: каким именем агент вас узнал. Нет в `kb/team.md` — передайте аналитику строку из вывода, он добавит почту в колонку «git» |
| Скиллов нет в `/` | Открыта не та папка или нет `.claude/skills/` |
| Каждая строка файла в git показана как изменённая | Переводы строк. В `.gitattributes` уже `eol=lf`; выполните `git add --renormalize .` и закоммитьте |

## Перенос старого репозитория на папку `kb/`

Репозитории, созданные до того, как база знаний переехала в `kb/`, переносятся один раз. Работу перед этим закоммитьте и отправьте.

```
git pull --rebase
mkdir kb
git mv context core datasets docs journal materials specs tools product.md team.md FEEDBACK.md kb/
git mv inbox kb/ 2>/dev/null; git mv game kb/ 2>/dev/null
```

Затем возьмите из свежего шаблона и положите поверх: `AGENTS.md`, `CLAUDE.md`, `.claude/`, `.opencode/`, `opencode.json`, `.gitlab-ci.yml`, `kb/tools/`, `kb/docs/`. Проверка и коммит:

```
python3 kb/tools/kb.py check
git add -A && git commit -m "База знаний — в папку kb/"
```

Ссылки внутри файлов вида `materials/…` переписывать не нужно: `kb.py` понимает и старый, и новый вид. Новые факты агент пишет с `kb/`.

## Обновить инструмент из шаблона

Шаблон улучшают: новые скиллы, правила, команды `kb.py`. Репозиторий продукта сам не обновляется. Раз в неделю или после объявления руководителя:

```
python3 kb/tools/kb.py upgrade --from https://github.com/Igibaev/kb-template
python3 kb/tools/kb.py check
git diff
git commit -m "Обновление инструмента из шаблона"
```

Команда обновляет `AGENTS.md`, `CLAUDE.md`, `.claude/`, `.opencode/`, `opencode.json`, `.gitlab-ci.yml`, `kb/tools/`, `kb/docs/`, шаблоны `_template*` и `README.md` внутри `kb/`. Знания продукта не трогает: `kb/product.md`, `kb/team.md`, шаги, карточки, журнал, материалы, `kb/inbox/`, датасеты, спеки, ядро. Новые поля шапки `kb/team.md` не вписывает, а показывает — добавьте сами. Ничего не коммитит.
