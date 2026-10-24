# ТЗ 09 — штатная авторасстановка classic

23.10.2026. Выполнено по `docs/tasks/TASK_09_NATIVE_ARRANGEMENT.md`, передаётся ведущему на ревью. **Native-приёмка не выполнена.** Git-фиксация/push/merge/установка, реальные LLM-запросы и принтер не выполнялись; ТЗ10 и следующие этапы не начаты.

## Результат и файлы

- Новый `plugin/AIAssistant/slicer/arrangement.py`: собственный native ArrangeObjectsJob, ID/статус, корреляция, Qt-таймер/завершение, проверка сцены, конечный бюджет и lifecycle.
- `slicer/adapter.py`: arrange_models/get_arrangement_status через прежний QtBridge, закрытие наблюдения перед остальными операциями, обычный Qt-deadline подготовки.
- `agent/tools.py`: две схемы/строгая валидация в едином реестре; arrange_models — мутация. Агентный цикл, HTTP и SPA не менялись. Два прежних exact-list теста расширены этими именами без удаления старых.
- `tests/test_arrangement.py`: 18 сценариев с подменными native job/Qt/геометрией и существующим реестром/адаптером.
- Точечно обновлены API_CONTRACT, plugin README с PowerShell, корневой README/PLAN/короткая frontend-инструкция. Старые команды сохранены.

Отдельное исправление замечания ведущего к 08: `slicer/slicing.py:backend_busy` различает **три подтверждённых layout**. CuraEngineBackend — прежние _slicing/start/basement/process_layers; CLIParserBackend — start/process_cli/process_layers/_slicing/Glicer без basement; CylindricalBackend — эти CLI-стадии плюс basement. Живой Popen проверяется неблокирующим poll, поля не записываются, process не запускается/не останавливается. Неизвестные ID/layout дают backend_state_unavailable, а не idle через getattr-fallback. Fixture classic получил реальный ID CuraEngineBackend с сохранённой проверкой результата. В test_model_transforms.py добавлен один сценарий CLI/full idle→move и занятых jobs/Glicer при _slicing=False, отказ неизвестному backend. Дополнение записано в TASK_08_REPORT; ведущий независимо повторил 17 transforms. Это guard преобразований, **не реализация 5D-нарезки**.

Рабочая папка содержит ещё не зафиксированное принятое ТЗ08 поверх main caac6f9; оно сохранено. Не тронуты чужие COORDINATION/ROLE02/задания08–10/FIVE_D_SOURCE_NOTES/Word locks и scripts/demo_checkpoint.ps1. Отчёт09, arrangement.py и тесты — новые untracked-файлы; смотреть вместе с diff, не только tracked-изменения. Установленная копия/внешние исходники не менялись.

## Основания в выданных исходниках

STE `SteSlicerApplication.py:arrangeAll/arrange` собирает верхние selectable sliceable/group активной платформы, запускает ArrangeObjectsJob с fixed_nodes=[] и min_offset=max(getEdgeDisallowedSize()+2,8), не возвращает job. Поэтому app.arrangeAll не используется для мнимой корреляции: плагин создаёт и удерживает тот же штатный класс с теми же входами/отступом.

Прочитаны `steslicer/Arranging/ArrangeObjectsJob.py`, Arrange.py, ShapeArray.py; Uranium UM/Job.py, JobQueue.py, Signal.py, OperationStack.py, GroupedOperation.py; BuildVolume/ConvexHullDecorator и методы ТЗ08. Внешние файлы только читались, не копировались целиком и не исправлялись; исследовательские пути не являются runtime-зависимостью.

Ключевые особенности форка:

- Native run работает в штатной **JobQueue**, сам собирает ShapeArray/расстановку, удаляет ZOffsetDecorator, делает GroupedOperation/TranslateOperation и опускание на стол. Плагин не реализует свой упаковщик и не запускает run на Qt. Внутренние native-чтения/записи worker — подтверждённая граница API, как у встроенного Arrange All; своих обращений к сцене в worker не добавлено.
- Native run испускает finished **раньше**, чем JobQueue выставит _running=false/_finished=true и испустит сигнал повторно. Own callback всегда передаёт проверку через app.callLater. Завершение принимается идемпотентно только для своего job и публичных isFinished=true/isRunning=false; hasError/getError проверяются отдельно. getResult остаётся None и не трактуется как ошибка.
- Job.progress не испускается: меняется штатный Message, поэтому progress всегда **null**, без расчётов по времени.
- ShapeArray пропускает отсутствующий/пустой hull. До запуска проверяются native hull/head с конечными непустыми точками; AABB не подставляется вместо hull. Модель шире/глубже стола отклоняет весь запуск до job, без тихого пропуска.

## Контракт, защита и ограничения результата

arrange_models `{}` быстро возвращает `{ok:true,data:{arrangement_id,state,...}}`, queued/running. Это принятие, не готовая расстановка. get_arrangement_status `{arrangement_id}` возвращает state queued/running/completed/failed, progress:null, error, models, all_fit, valid, observed_at и warnings. models — `{object_id,before:<state08>,after:<state08|null>}`, при недоступном after есть after_error. Схема state/bounds/Y-up/ID из 08 сохранена, живых API-объектов в HTTP нет. Неизвестный ID — arrangement_not_found; хранится до 16 последних принятых статусов в памяти процесса.

**completed означает выход native job и проверку снимка, не успех поиска всех мест.** found_solution_for_all — локальная переменная native run. При неудаче модель перемещается в `(200,center_y,-n*20)`, а X=200 может лежать внутри Hybrid530. Поэтому outside=false у всех не доказывает полного решения. В этой версии all_fit **не бывает true**: false при хотя бы одном подтверждённом outside=true после успешной проверки, иначе null. При failed/неподтверждённом контексте all_fit остаётся null. Факт false сохраняется и без движения; inside/неизвестные границы/отсутствие перемещения не превращаются в arranged:true. Per-object outside и предупреждения доступны для ручной сверки. valid описывает актуальность сохранённого after, не пригодность к печати.

До старта проверяются машина/scene/root/активная платформа, classic с соответствующим backend, auto_slice=false, automatic_drop_down=false и automatic_push_free=false, отсутствие preview/занятости общего guard/backend/local_file writer. Preferences пользователь меняет в UI. Все допустимые цели активной платформы должны быть enabled/selectable под единичным мировым родителем; все печатные части группы на этой платформе. Другие платформы/служебные объекты job не получает. Пустая платформа — empty_scene без фиктивного job.

После выхода сверяются прежние машина/настройки/платформа, состав моделей/ID/mesh/части/родители, rotation/scale и относительная геометрия группы; у других платформ — полные матрицы. Native перевод/опускание/ZOffset допустимы. Реальные after по живым ID сохраняются также после ошибок/timeout, без атомарного отката. До выхода геометрия результата не читается: только flags/context. Прежний arrangement-снимок консервативно снимает valid до следующего native job; старый status не читает меняющуюся геометрию новой расстановки.

Общий guard держится до фактического выхода job и проверки, в том числе после failed/timeout. Бюджет **120 секунд**: timeout не отменяет и не убивает native job, failed не меняется поздним сигналом на completed, автоповтор/откат отсутствуют. Если start выбросил ошибку после возможного принятия, ID и guard сохраняются до выхода/перезапуска. Обычный Qt-бюджет подготовки перепроверяется до native start, чтобы просроченный callback не запустил job позже. Принятый запуск инвалидирует прежнюю нарезку/будущий экспорт.

Shutdown прекращает новые действия/таймер и помечает незавершённый статус failed; job/nodes/finished/guard сохраняются до окончательного выхода. При уже недоступном Qt event loop безопасно наблюсти его выход невозможно — остаётся необходимость перезапуска, не заявление об отмене. Manual-параллельные операции запрещены сценарием: обнаруженный конфликт — failed; ручной перенос целевой модели нельзя достоверно отличить от native translate по одному конечному снимку. Штатный Undo выполняет пользователь с повторным чтением; native merge может объединять действия. Старый slice после Undo сам не становится актуальным.

Коды: empty_scene/no_active_machine/unsupported_arrangement_mode/auto_slicing_enabled, arrangement_busy/arrangement_unsupported/arrangement_hull_unavailable/arrangement_not_found; failed — arrangement_failed/arrangement_context_changed/arrangement_timeout/slicer_not_connected. Оболочка ok/data/error, MCP isError и -32602 сохранены. Неопределённый изменяющий вызов останавливает прежний агент без последующего delete/повтора.

## Проверки

Отдельные Python3.13.6 и Node v24.18.0, fake STE/UM/Qt/native-job/LLM; **реальная JobQueue/STE/GUI не запускались**.

| Команда | Результат |
| --- | --- |
| `python -m unittest discover -s tests` | **216 тестов OK, 13.421 сек** после итогового сохранения all_fit=false. Включены все 197 прежних, 1 уточнение busy и 18 сценариев расстановки. Ведущий сообщил независимые 216 OK/13.474 сек |
| Адресный test_model_transforms.py после уточнения CylindricalBackend CLI-стадии | **17 OK, 0.569 сек** |
| Адресный test_arrangement.py перед последним добавлением варианта no-movement/outside в тот же сценарий | **18 OK, 0.637 сек**; последний вариант затем прошёл в полном216 |
| `node --check plugin/AIAssistant/web/app.js`; `node tests/test_agent_web.js` | OK, **5 offline DOM-сценариев сохранены** |
| Python3.5 grammar ast.parse(feature_version=(3,5)), plugin.json | **31 файл plugin/scripts/tests OK; api:5 сохранён** |
| `git diff --check` | Без ошибок |
| PowerShell Parser.ParseInput всех powershell-блоков plugin/README.md | **32 блока OK**, только синтаксис, команды не исполнялись |

Сценарии09: строгие схемы до API/dev/unknown ID; queued→running→двойной finished/окончательная проверка; Qt-передача сигнала из worker; getResult=None не нужен для успеха; частичный outside/неизвестные границы; fallback X200 внутри широкого стола без false-positive all_fit=true; нет перемещения с inside→null и outside→false; preflight machine/mode/auto/physics/preview/hull/слишком большая модель/пустая активная платформа; группа/части/опускание/origin; чужой job/повторный callback; private error/частичный after; guard для старых записей и доступность чтений; timeout/early/late finish; контекст/rotation/другая платформа/состав сцены; stale после изменения; старый snapshot при новой работе без live-geometry reads; shutdown с live-контекстом; pending timeout/просроченная подготовка без позднего старта; неопределённый start с удержанием ID; stale slice и остановка агента после arrangement_busy.

Ведущий отдельно указал две границы, обе закреплены тестами: native fallback может оставаться внутри широкого стола; подтверждённый outside=true нельзя терять из-за отсутствия движения. Положительный all_fit не подменяет эти ограничения. Прежние timeout-тесты/runtime-бюджеты ради зелёного набора не ослаблялись; изменения точных списков отражают новые схемы.

## Что остаётся проверить вручную

Установленная копия не обновлялась, прежняя native-приёмка ТЗ03–08/агента и новая09 открыты. Не подтверждены реальная готовность hull, native JobQueue/сигналы/Undo, фактическое размещение/штатные сообщения/BuildVolume/физика на Hybrid530. Проверки с подменами/чтение исходников этого не заменяют.

PowerShell в plugin/README, раздел ТЗ09: текущий порт/2–3 небольшие модели classic → before по всем ID → arrange_models → короткий polling своего ID с клиентским пределом150сек → реальные after и UI/границы/штатное сообщение → Undo до before и чтение состояния → новая явная расстановка/проверка и отдельно новая classic-нарезка. Auto-slice/две physics-опции выключает и восстанавливает пользователь; не менять сцену параллельно, не трактовать all_fit:null как успех. Ничего не печатать/не вызывать провайдера автоматически. При зависшем native job перечитать доступное состояние и перезапустить, без скрытого повторного запуска.

После отчёта исполнитель остановлен для ревью ведущего. ТЗ10, custom-профили/вложения/downloads и прочие этапы не реализовывались.
