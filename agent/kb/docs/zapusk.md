# Первый запуск агента

Для команды. 15 минут. Нужны git, Python 3.9+, OpenCode 1.18+.

Две переменные в командах ниже:
- `AGENT_SRC` — папка `agent/` из ветки `new-agent` репозитория со сборкой агента;
- `BASE` — папка ветки базы знаний вашего продукта (ветка `context`).

## 1. Установить агента в базу — техлид, один раз

<!-- smoke -->
```bash
cp -R "$AGENT_SRC"/. "$BASE"/
cd "$BASE"
python3 kb/tools/kb.py check
```

Заполните файлы проекта — без них агент не знает, о чём продукт и кто есть кто:

| Файл | Что | Кто |
|---|---|---|
| `kb/team.md` | Люди пула, их git-почта и роли; роли бизнеса | Аналитик |
| `kb/product.md` | Что за продукт, для кого, главный объект, границы, MVP | Аналитик |
| `kb/project.md` | Ветки базы и кода, папка кода, закрытые для агента пути | Техлид |
| `kb/ai.json` | Какая модель на каком уровне | Техлид |

Потом закоммитьте и отправьте:

<!-- smoke -->
```bash
python3 kb/tools/kb.py check
git add -A && git commit -qm "Агент базы знаний"
```

## 2. Подготовить своё место — каждый, один раз

`git config user.email` должен совпадать с колонкой «git» в `kb/team.md`.

<!-- smoke -->
```bash
cd "$BASE"
python3 kb/tools/kb.py hooks
python3 kb/tools/kb.py whoami
python3 kb/tools/kb.py where
```

## 3. Модели

Какая модель на каком уровне, записано в `kb/ai.json` вашего проекта, показывает `python3 kb/tools/kb.py models`. Точные ID посмотрите в `opencode models` и впишите в переменные окружения — `opencode.json` берёт модели оттуда:

```bash
export KB_MODEL_LIGHT=<ID модели «light» из kb/ai.json>
export KB_MODEL_MEDIUM=<ID модели «medium» из kb/ai.json>
export KB_MODEL_DIALOG=<ID модели «dialog» из kb/ai.json>
export KB_MODEL_HEAVY=<ID модели «heavy» из kb/ai.json>
export KB_MODEL_INTERNAL=<ID модели «internal» из kb/ai.json>
export KB_MODEL_INTERNAL_THINK=<ID модели «internal_think» из kb/ai.json>
python3 kb/tools/kb.py models          # всё ли задано
```

В песочнице `sbx` переменные надо передать внутрь — так же, как ключи моделей. Файлы правил агента (`AGENTS.md`, `opencode.json`, `.opencode/`, `kb/rules/`, `kb/tools/`) монтируйте только на чтение.

## 4. Запуск

Откройте OpenCode (через kalude) в папке `$BASE`. Auto mode не включайте: окно разрешения — это ваше «да».

| Команда | Что делает |
|---|---|
| `/gde` | Где я, что дальше, почему |
| `/razbor PM-XX` | Разбор шага на сильной модели |
| `/kommit` | План коммита; коммит — после вашего «да» |
| `/utro` | Полный утренний отчёт |
| `/vstrecha <файл>` | Разбор расшифровки встречи в черновик |

Отправку на сервер (`git push`) делаете вы сами.

## 5. Если что-то не так

| Что видно | Что делать |
|---|---|
| `whoami`: «Вас нет в team.md» | Передайте аналитику строку, которую напечатала команда |
| `models`: «НЕ ЗАДАНО» | Раздел 3 |
| Коммит остановлен: «kb: коммит остановлен» | `python3 kb/tools/kb.py check` покажет, что исправить |
| Агент не может закоммитить сам | Так задумано: коммит — `kb commit plan`, потом ваше «да» |
