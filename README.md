# AI Assistant for STE Slicer

Каркас плагина для аддитивного трека хакатона 2026. Цель: запрос пользователя и модель -> настройки STE Slicer -> проект 3MF, G-code и объяснение решений.

**Сейчас работает только основа:** пункт меню плагина, локальный HTTP-сервер, SPA с историей текущего диалога, демонстрационный ответ и минимальная JSON-RPC оболочка. LLM, вложения, настройки и операции слайсера пока не реализованы. Каркас не генерирует G-code и не имитирует успешный слайсинг.

Проверено: синтаксис всех 11 Python-файлов парсером с грамматикой 3.5, JavaScript через `node --check`, реальные HTTP-запросы к локальному серверу (SPA, чат, валидация и ошибки, Host/Origin, JSON-RPC initialize/list/call). Проверка выполнялась на доступном Python 3.13; запуск на Python 3.5 внутри дистрибутива STE Slicer ещё предстоит. Пункт меню и завершение Extension пока проверены только по коду.

## Структура

```text
plugin/
  README.md
  AIAssistant/
    plugin.json          # API 5
    __init__.py          # регистрация Extension
    AIAssistant.py       # меню, сервер, браузер
    server.py            # HTTP / SPA / JSON-RPC
    agent/
      service.py         # точка входа будущего цикла LLM
      llm.py             # контракт OpenAI Chat Completions
      mcp.py             # initialize, tools/list, tools/call
      tools.py           # общий реестр инструментов
    slicer/
      adapter.py         # граница работы с API STE Slicer
    web/                 # HTML + CSS + JS, без сборки
docs/
  BRIEF.md               # разбор материалов, ограничения, сроки
  TEAM.md                # задачи на четырех участников
  ARCHITECTURE.md        # контракты и очередь реализации
  CHECKLIST.md           # ручная проверка и сдача
scripts/dev_server.py    # запуск SPA без установленного слайсера
results/                 # будущие результаты по каждой модели
```

## Быстрый запуск без слайсера

Нужен Python 3.5+; сторонние пакеты для демонстрационного сервера не нужны:

```powershell
python scripts/dev_server.py
```

Откройте адрес, напечатанный в терминале. Остановить сервер: Ctrl+C. Порт выбирается автоматически; сервер слушает только `127.0.0.1`.

## Установка плагина

Смотрите [plugin/README.md](plugin/README.md). Основная проверка должна проходить в выданном организаторами дистрибутиве, с профилем **Stereotech Hybrid 530 V5.2.4**. Запуск внутри STE Slicer пока не проверен.

## С чего начать команде

1. Установить и активировать выданный STE Slicer, получить модели и параметры LLM на странице организаторов.
2. Проверить загрузку Extension и реальное чтение/изменение одной настройки через главный Qt-поток.
3. Собрать один полный сценарий: модель -> чат -> настройки -> слайсинг -> 3MF/G-code.
4. Расширить инструменты, затем прогнать все модели и три режима.

Распределение ролей: [docs/TEAM.md](docs/TEAM.md). Подробные ограничения и оставшаяся работа: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## GitHub: первый коммит и push вручную

В рабочей папке уже существует Git-репозиторий. Клонировать весь STE Slicer для разработки отдельного плагина не нужно. Его исходники и встроенные плагины нужны как справочник API; используйте именно версию организаторов, особенно для 5D.

Создайте на GitHub пустой репозиторий без автоматически добавленных README/.gitignore/license. Затем выполните, заменив `YOUR_LOGIN` и `YOUR_REPO`:

```powershell
Set-Location 'C:\Users\sevay\Documents\ChatGPT\hackaton_2026'
git status --short
git add .gitignore .gitattributes README.md plugin docs scripts results
git diff --cached --stat
git diff --cached
git commit -m "chore: scaffold STE Slicer AI assistant"
git branch -M main
git remote add origin https://github.com/YOUR_LOGIN/YOUR_REPO.git
git push -u origin main
```

Здесь коммиты, staging и push не выполнялись. Если `origin` уже добавлен, проверьте `git remote -v` и пропустите `git remote add` либо замените адрес через `git remote set-url origin ...`.

После push остальные участники клонируют **ваш** репозиторий. Для работы используйте отдельные ветки, например `codex/slicer`, `codex/agent`, `codex/web`. Ключ LLM вводится в настройки приложения при будущей реализации; ключи и файлы активации в Git не добавлять.
