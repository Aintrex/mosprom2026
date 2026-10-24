# ТЗ 05 — штатная classic-нарезка

Дата: 23.10.2026. **Реализация готова к ревью; ручная проверка в установленном STE Slicer не выполнена.** Изолированные тесты не подтверждают работу реального engine. Ручная приёмка ТЗ 03/04 остаётся открыта.

Выполнен только объём `docs/tasks/TASK_05_REAL_3D_SLICING.md`. Рабочая основа — существующий `plugin/AIAssistant/`; второй плагин, формулы и генераторы из artem_backend не переносились. Установленный плагин не обновлялся, приложение не перезапускалось, физическая печать не запускалась. Экспорт G-code/3MF и 5D-нарезка этим заданием не реализованы.

## Изменённые файлы

| Файл | Изменение |
| --- | --- |
| `plugin/AIAssistant/slicer/slicing.py` | Новый наблюдатель одного запуска и последнего completed: preflight, сигналы, QTimer, корреляция, статус/оценки, актуальность, shutdown |
| `plugin/AIAssistant/slicer/adapter.py` | Три публичных метода; прежний общий lock теперь защищает также set_setting; Qt-cleanup наблюдателя при close |
| `plugin/AIAssistant/agent/tools.py` | Три схемы/валидатора в существующем общем ToolRegistry, старые инструменты сохранены |
| `tests/test_slicer_slicing.py` | 27 значимых изолированных сценариев, включая worker/Qt queue и HTTP/MCP |
| `tests/test_slicer_settings.py`, `tests/test_slicer_modes.py` | Только расширены ожидаемые списки tools/list; старые проверки не удалялись |
| `docs/API_CONTRACT.md` | Формы данных, ошибки, бюджет, блокировки и границы ТЗ 05 |
| `plugin/README.md` | Описание ограничений и последовательные PowerShell-команды приёмки |
| `README.md` | Фактический объём реализации и открытая runtime-приёмка |
| `docs/PLAN_SLICER.md` | Точечно обновлён статус ТЗ 05 поверх исходных чужих изменений |
| `docs/reports/TASK_05_REPORT.md` | Этот отчёт |

HTTP/SPA, QtBridge, старые модули режимов/профилей/импорта и Extension не переписывались; Extension уже вызывает adapter.close на applicationShuttingDown. Новых зависимостей нет, plugin.json сохраняет api:5.

## API, подтверждённые по выданному форку

Читались только справочные копии STE `<SOURCE_ROOT>/STE-Slicer-master` и соседнего `Uranium-steslicer`. Исходники не менялись и целиком не копировались; эти пути не являются runtime-зависимостями.

| Источник | Подтверждение / применение |
| --- | --- |
| STE `plugins/CuraEngineBackend/CuraEngineBackend.py:227–296` | forceSlice → markSliceAll/slice; начальные progress=0/NotStarted; Processing до нового пустого gcode_dict[plate]; slicingStarted до создания StartSliceJob. Команда не возвращает результат engine |
| Тот же файл, `_onStartSliceCompleted`, `_onGenerateBasementJobFinished`, `_onSocketError`, stopSlicing | Ошибки подготовки, отмена, Error/NotStarted и занятость подготовки/основания. `_slicing` может остаться установленным после ошибки — guard нельзя освобождать только по ошибочному сигналу |
| Тот же файл, `_onGenerateBasementJobFinished:458`, `_onGCodeLayerMessage:737` | Оба обработчика могут заменить пустой список G-code. Наблюдатель допускает эту замену на собственных Processing/оценках, пока прежний буфер пуст. Непустой захваченный буфер не заменяется |
| Тот же файл, `_onSlicingFinishedMessage:694` | Done/progress=1 идут до подстановок G-code, очистки платформы и `_slicing`; окончательное подтверждение отложено через callLater. Если идёт ProcessSlicedLayersJob, ожидается и его освобождение |
| Тот же файл, `_onPrintTimeMaterialEstimates:790` | Оценка имеет платформу текущей подготовки, dict времени и массив объёмов. Raw-значения служат для свежести/валидации, время/вес/длина берутся из PrintInformation |
| STE `steslicer/BackendManager/BackendManager.py` | getCurrentBackend/getBackendByType("classic") сравниваются по идентичности. currendBackendChanged — фактическое имя сигнала. getBackends даёт источники для защиты общей PrintInformation от чужой оценки той же платформы |
| Uranium `UM/Backend/Backend.py` | BackendState из пяти значений; processingProgress/backendStateChange/backendQuit. Нет публичного getState/isBusy. backendQuit приходит при EOF stdout, Signal.Auto направляет его в Qt |
| Uranium `UM/Signal.py:299–319`, `UM/Application.py:377` | Auto переносит emit в основной поток; функции вызываются до bound methods. Наш callback-функция own printDurationMessage видит ревизии до BackendManager → PrintInformation; callLater допускает аргументы и отложенную проверку после forwarding. Callback хранится сильной ссылкой, поскольку UM.Signal использует weakref |
| STE `steslicer/PrintInformation.py:134–168,203–254`, Uranium `UM/Qt/Duration.py` | currentPrintTime и int(duration) в секундах, materialLengths в метрах с округлением до 0.01 м, materialWeights в граммах. Три сигнала изменения подтверждают обновление; порядок элементов не выдаётся за позиции экструдеров |
| STE `steslicer/Utils/ProcessSlicedLayersJob.py`, Scene/SteSlicerSceneNode.py | Узлы слоёв имеют no_setting_override, BuildPlate/LayerDataDecorator, не printable-декораторы. Исключены из снимка печатной сцены |
| Uranium `UM/Mesh/MeshData.py:45–103`, `UM/Scene/SceneNode.py`, `UM/Settings/ContainerStack.py`, `InstanceContainer.py` | MeshData хранит immutable-массивы; set создаёт новый mesh. Для снимка достаточно идентичности mesh/узла, числа вершин, мировой матрицы и платформы без копирования mesh. Стек даёт контейнеры и сырые значения без вычисления тысяч derived settings |
| STE `plugins/GCodeWriter/GCodeWriter.py` | scene.gcode_dict[active_plate] — данные движка. В ТЗ 05 они только проверяются, на диск не записываются |

Приватные чтения изолированы в slicing.py: `_slicing`, `_start_slice_job`, `_generate_basement_job`, `_process_layers_job`, `_start_slice_job_build_plate`. Их плагин не изменяет. Собственного kill/Stop, изменения Preferences и очистки engine-буферов нет.

## Корреляция, завершение и актуальность

До реализации выбранная корреляция была описана пользователю: захваченные backend/платформа/подготовка, новое начало и G-code, собственная свежая оценка и отдельный Qt-callback после Done. При дополнительной сверке учтены две штатные замены пустых буферов и порядок вызовов UM.Signal.

1. Неблокирующе захватывается **прежний общий lock** импорта/выбора режима/профилей. Проверяются активная машина, classic и соответствующий runtime-объект backend, выключенный auto-slice, отсутствие preview/native jobs и одна непустая активная платформа. Отказы не вызывают forceSlice.
2. Фиксируются backend, stack, scene/root, PrintInformation, платформа и снимок печатного контекста. Подписки подключаются на Qt до forceSlice. ID выдаётся в пределах обычного QtBridge-бюджета, без ожидания слайсинга. Начальное NotStarted до начала не считается ошибкой.
3. Начало подтверждают Processing/slicingStarted. Захватывается новый пустой список вместо прежнего G-code и конкретный StartSliceJob. Повторный slicingStarted, смена job/платформы или контекста — failed. Штатная замена пустого буфера в подготовке/первом GCodeLayer допускается только на собственном runtime-сигнале; старый или подменённый непустой буфер не подтверждает успех.
4. При собственном printDurationMessage совпадают backend/платформа подготовки; фиксируется ревизия трёх полей PrintInformation. После forwarding нужны новые currentPrintTimeChanged, materialLengthsChanged и materialWeightsChanged. Старые/reset-значения, пустой reset-message и чужая платформа не подтверждают свежесть. Чужой зарегистрированный backend с оценкой той же платформы даёт slicing_conflict, поскольку PrintInformation общая.
5. Done лишь разрешает последующую проверку. В отдельном Qt-callback после handler должны совпасть контекст и свежий непустой G-code, быть доступны свежие оценки и освободиться все четыре признака busy. Только тогда state=completed/valid=true. Один progress=1 не завершает запуск.
6. Секунды читаются через int(Duration), длина суммируется и переводится м → мм, вес суммируется в г. Собственных оценочных формул нет. Невалидные/недоступные числа становятся null с warnings. Подтверждённый ноль разрешён. Наружу возвращаются копии JSON-данных, без объектов UM/Qt и содержимого G-code.

QTimer имеет интервал 250 мс, бюджет запуска — **180 секунд с принятия**. Никаких sleep/wait и ожидания engine на Qt или в одном HTTP-запросе. При timeout фиксируется failed/slicing_timeout; поздний Done не восстанавливает completed. Lock остаётся до наблюдаемого освобождения backend, без автоповтора. При зависших private busy-полях требуется ручной Stop/ожидание либо перезапуск. Ошибка forceSlice после возможного начала также сохраняет guard; ID даётся в сообщении ошибки.

Снимок включает печатные узлы/mesh/мировые трансформации/платформы, идентичность машины/backend/сцены и сырые значения/ID контейнеров глобального, экструдерных и объектных стеков с релевантными metadata. События scene/stack/backend/платформы и таймер проверяют актуальность. Камера, выделение, имена и служебные слои в снимок не входят. Изменение во время запуска даёт failed; после completed — valid=false и slice_result_stale. Восстановление значения не возвращает valid.

set_setting теперь участвует в том же lock; режим/качество/материал/импорт сохраняют прежние busy-коды. Чтения доступны. Shutdown снимает подписки и QTimer на Qt, освобождает собственные ссылки/lock и делает отложенные callbacks неактивными; процесс engine не останавливается.

## Схемы и ошибки

В едином ToolRegistry добавлены:

```json
{"name":"start_slicing","inputSchema":{"type":"object","properties":{},"required":[],"additionalProperties":false}}
```

```json
{"name":"get_slicing_status","inputSchema":{"type":"object","properties":{"slice_id":{"type":"string","minLength":1}},"required":["slice_id"],"additionalProperties":false}}
```

```json
{"name":"get_slicing_result","inputSchema":{"type":"object","properties":{"slice_id":{"type":"string","minLength":1}},"required":["slice_id"],"additionalProperties":false}}
```

Неверная структура/тип — JSON-RPC -32602 до API. Прежняя операционная оболочка `{ok,data}` / `{ok:false,error:{code,message}}` и MCP isError сохранены. Чтение failed-статуса успешно, причина находится в data.error; get_slicing_result до completed возвращает slice_not_ready.

Новые коды: `empty_scene`, `unsupported_slicing_mode`, `slicing_busy`, `auto_slicing_enabled`, `multiple_build_plates`, `slicing_preview_active`, `slice_not_found`, `slice_not_ready`, `slice_result_stale`, `slicing_failed`, `slicing_timeout`, `slicing_cancelled`, `slicing_context_changed`, `slicing_conflict`. Сохранены no_active_machine/slicer_not_connected/qt_timeout/slicer_error и прежние busy-коды остальных setters. Формы статуса/результата и значения кодов зафиксированы в [API_CONTRACT](../API_CONTRACT.md#classic-нарезка-тз-05--реализована-runtime-приёмка-открыта).

## Результаты проверок

Среда автоматических проверок: Windows, Python **3.13.6**. Итоговый прогон:

```text
python -m unittest discover -s tests
Ran 121 tests in 8.630s
OK
```

Сохранены все **94** прежних сценария; добавлены **27** проверок ТЗ 05. Среди них:

- валидация/схемы, dev без Qt/UM, отсутствие машины/модели, режим/backend, preview/платформа/auto-slice и все четыре native busy-признака без forceSlice;
- короткий worker-start, queued/running, реальный finite progress/null, начальный NotStarted, Done до финализации, отсутствие начала/свежих оценок, две штатные замены пустых G-code-буферов;
- чужие job/платформа/backend, подмена непустого буфера, отмена/NotStarted/Error/Disabled/quit, finite timeout и поздний Done без повторного запуска/раннего снятия guard;
- подменённая PrintInformation: int(Duration), метры → мм, граммы, суммы, null/warnings и подтверждённый ноль, безопасные JSON-копии;
- блокировка всех перечисленных записей, доступность чтений; импорт штатным fake reader → slicing, отказ start до fileCompleted;
- изменение геометрии/mesh/машины/настроек/материала/качества/платформы, камера/выделение/служебные слои без ложного stale;
- Qt-поток API/подписок/таймера, перенос worker-сигнала, cleanup и поздние callbacks после shutdown, отмена просроченного pending-start, ошибка API после фактического начала;
- настоящий локальный HTTP/MCP поверх подменённого backend: start → status → not-ready → result; прежние проверки Host/Origin и других инструментов сохранены.

В тестовый набор входит `test_python35_syntax_and_plugin_api`: все Python-файлы плагина разобраны грамматикой **Python 3.5**, api остаётся 5. Это синтаксическая проверка, не запуск на Python 3.5 с настоящими Qt/UM.

Все **19 PowerShell-блоков** plugin/README.md разобраны `System.Management.Automation.Language.Parser` без выполнения; ошибок синтаксиса нет. `git diff --check` прошёл. Git сообщает только о нормализации CRLF → LF у корневого README, не об ошибке whitespace.

Первый sandbox-прогон HTTP-теста был ограничен WinError 10013 для loopback. Итоговый полный прогон выполнен с разрешённым запуском тестов вне sandbox и прошёл. Промежуточные ошибки тестовых fixtures/корреляции исправлены до итогового прогона; отказов автоматического approval-review не было.

## Runtime и команды приёмки

**Фактический runtime-прогон ТЗ 05 не выполнялся.** Установленная копия не обновлена по прямому запрету пользователя; её прежняя версия не доказывает работу новых методов. Реальное завершение engine, UI/Preview и совпадение оценок остаются неподтверждёнными.

Полный последовательный сценарий подготовлен в [plugin/README.md — ТЗ 05](../../plugin/README.md#тз-05-штатная-classic-нарезка):

1. После ревью и согласованного обновления ведущим: сохранить проект, Hybrid 530 V5.2.4, classic, auto-slice выключен вручную, одна платформа без preview.
2. Ввести **текущий порт** из адреса чата; initialize/tools-list/get_slicer_state/get_setting/list_models.
3. Ввести **абсолютный путь пользователя** к известному небольшому STL; load_model → list_models/get_model_bounds → сверка UI. Путь/порт не зашиты в команды.
4. start_slicing → сохранить slice_id → polling раз в секунду с конечным клиентским пределом 195 секунд и HTTP timeout 15 секунд → get_slicing_result. Failed прерывает polling с реальной причиной; клиентский timeout engine не отменяет.
5. Сравнить время/длину/массу с UI, открыть штатный Preview и проверить слои детали; сохранить ответы в tmp/task05_runtime.json и скриншоты пользователем.
6. Изменить layer_height на другое допустимое значение → valid=false/slice_result_stale → восстановить исходную высоту, проверить чтение/UI. Старый результат остаётся stale; для актуального нужен новый явный start.

Команды не выполняют установку, экспорт или печать. Прохождение STL-сценария не закрывает оставшиеся проверки OBJ/3MF и отдельную приёмку качества/материалов ТЗ 03.

## Открытые ограничения

- Прототип поддерживает только classic и одну непустую активную платформу. Несколько платформ/5D/экспорт файлов/отмена отдельным MCP-инструментом/LLM/REST-события не реализованы этим ТЗ.
- Нет engine request ID в исследованном API. Корреляция опирается на preflight idle, захваченные объекты/подготовку, окно сигналов, новый буфер и собственные оценки. Ручные параллельные операции тем же backend/платформой запрещены; неразличимый внешне конфликт нельзя обещать распознать по одному сигналу. Обнаруженные конфликты завершаются отказом.
- Порядок обновления PrintInformation опирается на подтверждённый functions-before-methods в этой версии UM.Signal. Совместимость с другой сборкой требует сверки API и runtime, отсутствующие методы/поля не подменяются успехом.
- Защита после failed/timeout консервативна: если backend не освобождает native признаки занятости, требуется ручное вмешательство/перезапуск. Закрытие плагина не отменяет engine.
- Оценки — прогноз STE, длина уже округлена источником; это не физический расход/длительность. Массивы суммируются без предположения об extruder_id. На настоящей сборке нужно проверить также доступность всех полей и QTimer/signal-порядок.
- Снимок ограничен печатными объектами и raw-контейнерами; тяжёлого хеширования mesh/универсального состояния приложения нет. Работа/отзывчивость на больших сценах не измерялась. Скрытая мутация чужим кодом без штатного API/сигналов вне подтверждённой модели MeshData не заявляется.
- Shutdown проверен с подменами; реальное закрытие приложения и ошибки engine/лицензии в установленной сборке требуют ручной проверки. Точная причина лицензии из сигнала не выдумывается: общая ошибка направляет к сообщениям UI/журналу.

## Сохранность рабочей папки и передача

Первым действием был git status. Изначально уже были изменения docs/PLAN_SLICER.md и неотслеживаемые ARTEM_BACKEND_REVIEW.md, ROLE_02_AGENT_INTEGRATION.md, TASK_05_REAL_3D_SLICING.md, временные lock-файлы Word и scripts/demo_checkpoint.ps1. Они сохранены; в PLAN изменена только строка статуса текущего ТЗ поверх исходных дополнений.

Появившиеся параллельно docs/COORDINATION.md и docs/tasks/TASK_06_NATIVE_EXPORT.md не менялись исполнителем. Чужой HEAD `b56e6e1` сохранён. Следующий этап исполнителем не начинался. Чужие файлы, web/, git index, установленная конфигурация и справочные исходники не изменялись. git add/commit/push/merge, новые чаты/агенты/worktree не выполнялись.

После этого отчёта работа по ТЗ 05 останавливается и передаётся ведущему на ревью; ручной прогон проводится отдельно после его решения.
