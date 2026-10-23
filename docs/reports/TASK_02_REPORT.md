# Отчёт по ТЗ 02 — состояние машины и режимы

Дата: 23.10.2026. **Реализация готова к отдельной ручной приёмке; реальный прогон ТЗ 02 не выполнен.** Предыдущая приёмка ТЗ 01 сохранена в `TASK_01_RUNTIME.md`, она не подтверждает новый код. Установка/обновление установленной копии, staging, commit и push не выполнялись. Следующие этапы не начинались.

Это исходный отчёт исполнителя. **После него ТЗ 02 принято ведущим по результатам совместной приёмки в STE Slicer**; актуальный результат — [TASK_02_RUNTIME.md](TASK_02_RUNTIME.md). Описание невыполненного ручного прогона ниже относится к моменту сдачи реализации.

## Что изменено

- `slicer/modes.py`: короткие операции диагностики, проверки поддержки режима и записи `userChanges`; ID и метки берутся из API. Контекст переключения хранит исходный режим и backend, целевой runtime-объект, стек и менеджер; обращения к ним выполняются только на Qt-потоке. Результаты — JSON-совместимые данные, ссылки UM не возвращаются клиенту.
- `slicer/adapter.py`: `get_slicer_state`, `set_print_mode`, общий бюджет 5 секунд, подтверждение отдельными вызовами после записи. Только worker ожидает между проверками (до 50 мс); shutdown прерывает ожидание. Простая неблокирующая блокировка отклоняет второй вызов как `switch_in_progress`. Прямой вызов переключения на Qt-потоке возвращает явную ошибку до записи.
- `slicer/qt_bridge.py`: необязательный таймаут отдельного короткого вызова для передачи оставшегося общего бюджета. Дедлайн дополнительно проверяется перед исполнением callback; просроченный callback не начинает запись. Сохранены прямые короткие get/set на главном потоке, передача исходного исключения и отмена pending-вызовов при timeout/shutdown.
- `agent/tools.py`: две схемы и dispatch в прежнем общем реестре. `assistant_status` перечисляет четыре инструмента слайсера, сохраняет `llm_connected=false`, `runtime_verified=false`.
- `tests/test_slicer_modes.py`: проверки новой логики с раздельной постановкой/исполнением Qt-callback и отдельно отложенным событием backend. В `tests/test_slicer_settings.py` обновлён только ожидаемый список инструментов; старые схемы и сценарии ТЗ 01 сохранены.
- `plugin/README.md`, действующий раздел MCP в `docs/API_CONTRACT.md`: новые инструменты, семантика, ограничения и точные команды ручной проверки. Целевые REST-разделы не реализованы и не изменялись.

`AIAssistant.py`, HTTP-сервер, MCP-транспорт, SPA, LLM и `plugin.json` не менялись. Объём ограничен диагностикой/режимами; профили, материалы, модели, запуск слайсинга и экспорт не добавлялись. Runtime-зависимости не добавлены, внешние исходники не изменялись и не копировались в Git.

## Проверенные методы исходников

Читались справочные STE `C:/Users/sevay/Desktop/SourceSTE/STE-Slicer/STE-Slicer-master` и соседний `Uranium-steslicer`. Эти пути не используются runtime-кодом.

| Источник | Подтверждённое поведение |
| --- | --- |
| STE `steslicer/SteSlicerApplication.py:592,1690,1697` | `getGlobalContainerStack`, `getBackendManager`; `getBackend` получает current у менеджера и при отсутствии выбрасывает RuntimeError. Диагностика использует manager напрямую, чтобы представить отсутствующий backend как null. |
| Uranium `UM/Application.py:249,377,382` | `getVersion`, планирование `callLater`, ссылка на основной `threading.Thread` через `getMainThread`. |
| Uranium `UM/Qt/QtApplication.py:401` | `functionEvent` ставит Qt-событие через `postEvent`, callback выполняется event loop. |
| Uranium `UM/Settings/ContainerStack.py:95,103,208,267,279` | `getId`, `getName`, вычисление свойств через `getProperty`, `hasProperty`, queued `propertyChanged`. |
| STE `steslicer/Settings/SteSlicerContainerStack.py:73,147` | `userChanges` и `getDefinition`. Definition ID читается из фактического definition-контейнера. |
| Uranium `UM/Settings/InstanceContainer.py:316`, `SettingInstance.py:145` | Запись пользовательского значения и штатные сигналы; setter может не применить значение. Нет ручного вызова приватного обработчика или установки current-backend. |
| STE `steslicer/BackendManager/BackendManager.py:85,91,94` | `getBackendByType`, `getCurrentBackend`, смена backend по `printing_mode/value`. Сигнал называется `currendBackendChanged`, с опечаткой; в реализации выбран polling короткими Qt-вызовами, без подписки на этот сигнал. |
| Uranium `UM/PluginObject.py:16` | `getPluginId` — источник ID backend. Совпадение режима проверяется по идентичности объектов, а не только ID-строкам. |
| STE `resources/definitions/fdmprinter.def.json:210,799` | `machine_five_axis` — bool; `printing_mode` — enum с тремя режимами кейса и alpha-режимами, enabled зависит от `machine_five_axis`. |
| STE `plugins/CuraEngineBackend/__init__.py`, `CLIParserBackend/__init__.py`, `CylindricalBackend/__init__.py` | Регистрации classic, cylindrical и cylindrical_full соответственно. Таблица имён не зашита в плагин: используется runtime-lookup. |

Исследованы также общий `UM/Backend/Backend.py` и три реализации backend. Общий Backend имеет `backendStateChange`, но не публичный getter текущего состояния слайсинга; в трёх исследованных реализациях не найдены публичные `getState/isBusy/isSlicing`. Внутренние поля задач/состояния не использованы как универсальный API. Защита от активной нарезки в этом задании **не заявляется**, проверка ограничена пустой сценой.

Расхождения со сборкой в этом задании не проверялись: установленный плагин не обновлялся и runtime новых методов не вызывался.

## Схемы и результаты

Общий источник схем для MCP и будущего LLM — `ToolRegistry.list_tools()`:

```json
{"name":"get_slicer_state","inputSchema":{"type":"object","properties":{},"additionalProperties":false}}
```

```json
{"name":"set_print_mode","inputSchema":{"type":"object","properties":{"mode":{"type":"string","enum":["classic","cylindrical","cylindrical_full"]}},"required":["mode"],"additionalProperties":false}}
```

Аргументы проверяются до обращения к приложению. Неизвестный/alpha-режим, неправильный тип, недостающее или лишнее поле — JSON-RPC `-32602`. `get_setting`/`set_setting` по-прежнему принимают только глобальный `layer_height`, их схемы не расширены.

Диагностика: `{ok:true,data:{connected,application_version,machine,printing_mode,backend,available_modes}}`. Версия/машина/definition ID/строка режима — фактические значения API. Без активной машины — успешный ответ с `connected=true`, `machine=null`, `printing_mode=null`, `backend=null`, `available_modes=[]`. При отсутствии app — `slicer_not_connected`.

Backend — `{id,expected_id,matches_mode}`. Неизвестный текущий режим сохраняется, ожидаемый backend ищется в менеджере и для такого режима. Отсутствующие ID — null, сопоставление отсутствующих объектов — false. Доступные режимы фильтруются по enum текущего стека и, для 5D, `machine_five_axis=true`; метки берутся из enum. `backend_available` означает регистрацию, а не проверку работоспособности движка/лицензии.

Успех переключения: `{ok:true,data:{before,after,backend:{id,expected_id,matches_mode:true}}}`. Успех возвращается только при подтверждении настройки и backend. После реальной записи проверка выполняется отдельным событием, позволяя event loop обработать штатные queued-сигналы. Уже согласованный режим не записывается повторно; совпавшая строка при неверном backend ожидает подтверждения и при его отсутствии даёт ошибку. Смена машины/менеджера/регистрации во время ожидания не маскируется успехом.

Операционные ошибки: прежние `slicer_not_connected`, `no_active_machine`, `setting_unavailable`, `qt_timeout`, `slicer_error`; новые `mode_unavailable`, `backend_unavailable`, `mode_not_applied`, `backend_not_switched`, `switch_in_progress`. Формат `{ok:false,error:{code,message}}` остаётся в JSON-тексте MCP `content`, `isError=true`. Ошибки API не выдаются за Unknown tool. После потенциально начатой записи сообщение требует `get_slicer_state`; автоматического отката или повтора нет.

## Проверки и команды воспроизведения

Из корня репозитория:

```powershell
python --version
python -m unittest discover -s tests -v
git diff --check
git status --short --untracked-files=all
```

Финальный результат: **45 тестов, OK, 3.463 секунды**, Windows, Python **3.13.6**. Сохранены 21 проверка ТЗ 01 и добавлены 24 сценария ТЗ 02. Полный первый прогон тоже прошёл. При последующем прогоне в сетевой песочнице три HTTP-проверки были заблокированы: WinError 10013 при loopback-соединении и ожидание callback после заблокированного запроса. Точный повтор команды с разрешёнными локальными соединениями прошёл 45/45; тесты не отключались. `git diff --check` — без замечаний.

Проверенные отличимые сценарии:

- Runtime-данные версии/машины/definition/режима/backend, отсутствие машины/current/expected/менеджера, неизвестный текущий режим, доступность по enum/осям. Фиктивные backend ID намеренно отличаются от production-имён, чтобы выявить зашитую таблицу.
- Все чтения app/stack/manager/definition/backend — в главном тестовом потоке; worker отдельно ставит callback. Наружу выходят только сериализуемые результаты.
- Три режима: setter меняет строку, но не backend. Отдельная очередь моделирует отложенное штатное событие. Проверка до события не возвращает успех; после события — подтверждает. Реальный локальный HTTP → MCP → адаптер проверен с тем же разделением.
- Несменившийся backend, проигнорированная запись, одинаковые ID у разных объектов backend, idempotent no-op, уже совпадающая строка с неправильным backend.
- Валидация до API; отсутствие машины/setting/осей/enum/регистрации до записи; KeyError API после начала записи становится `slicer_error` с `isError=true`, приватный текст не попадает в журнал.
- Таймаут до callback отменяет позднюю запись; таймаут после начала сообщает неопределённость без повтора. Бюджет включает ожидание первого callback и подтверждение backend. Shutdown до старта и во время подтверждения, отказ нового вызова, параллельный запрос с другим target, освобождение блокировки после завершения.
- Общий tools/list, старые схемы, dev-подключение, запуск отдельного процесса dev-сервера и его ответ `/mcp` без Qt/UM. Все файлы Python плагина/scripts/tests проходят `ast.parse(..., feature_version=(3,5))`; `api:5` сохранён. Это синтаксическая проверка современным Python, не исполнение новых методов в Python 3.5/STE.

## Ручная приёмка и ограничения

**Реальный прогон ТЗ 02 не проводился.** По условию задания обновление установленной копии согласуется отдельно при приёмке. Исполнитель не копировал новый код в конфигурацию, не перезапускал STE Slicer и не менял реальный режим/машину/материал/качество. Фактические before/after/backend ID трёх режимов для нового кода пока не зафиксированы.

В [plugin/README.md](../../plugin/README.md), раздел «Команды ручной приёмки ТЗ 02», подготовлен полный PowerShell-блок с текущим портом, initialize/tools/list, чтением и сохранением исходного режима. Далее — отдельные команды `classic → cylindrical → cylindrical_full → исходный`, повторное чтение после каждой, сверка UI/ID/backend, неверные запросы. Каждую команду переключения нужно выполнять отдельно после проверки предыдущего ответа и UI. После ошибки — остановиться, прочитать состояние, передать ведущему фактическую диагностику; восстановление не считать выполненным без подтверждения API/UI.

Остаётся подтвердить на выданной сборке: API новых методов и свойства enum/осей, реальные ID и регистрацию backend, время штатного переключения, обновление UI, восстановление исходного режима. Сигналы/лицензия/движок могут отличаться от справочной копии. Даже успешное переключение не означает готовность 5D-слайсинга или G-code. Нет защиты от активной нарезки: приёмка только на пустой сцене без активных операций. Новых известных сбоев в пройденных изолированных сценариях нет.

## Итоговый git status

При старте рабочее дерево было чистым. Изменения задания:

```text
 M docs/API_CONTRACT.md
 M plugin/AIAssistant/agent/tools.py
 M plugin/AIAssistant/slicer/adapter.py
 M plugin/AIAssistant/slicer/qt_bridge.py
 M plugin/README.md
 M tests/test_slicer_settings.py
?? docs/reports/TASK_02_REPORT.md
?? plugin/AIAssistant/slicer/modes.py
?? tests/test_slicer_modes.py
```

Никакие файлы не добавлены в индекс. ТЗ 02 возвращается ведущему для приёмки; дальнейшие этапы не выполнялись.
