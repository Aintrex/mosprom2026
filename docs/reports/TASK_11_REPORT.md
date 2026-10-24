# ТЗ 11 — составной cylindrical_full

24.10.2026. Выполнено только `docs/tasks/TASK_11_FULL_5D_SLICING.md`, с сохранённого черновика после принятого10/2A/2B. Прочитаны AGENTS/план/задание, проверен git status. Чужие документы, Word locks и прежние изменения сохранены. Установка, commit/push/merge, реальныйLLM, активация/её обход и принтер не выполнялись. Следующий этап не начат; остановка для ревью.

## Изменения

- `full_slicing.py`: узкий FullStages для известного native CylindricalBackend/MultiBackend, без собственного запуска движков или нового pipeline framework. Внешний forceSlice запускает prep → основание → Cura → Glicer/ProcessCliJob → parser. Проверяются зарегистрированные child CuraEngineBackend/CLIParserBackend, их готовность/инициализация, identity jobs/процессов/платформ и реальные flags/result/error/cancel.
- `slicing.py`: третий разрешённый режим подключён к прежним slice_id/status/result, guard/QtBridge, контексту, PI/readback и экспортному буферу. Child Done и промежуточный Cura Processing0.5 не финал. Completed требует outer parser-final, отложенного чтения окончательных chunks, выхода стадий и последней подтверждённой outer→PrintInformation оценки. Время/расход не считаются плагином; null/warnings прежние. Поздняя подмена/изменение full chunks и контекста делает результат stale.
- Штатные разовые reset childsocket/process коррелируются с finished своего basement/ProcessCliJob и readback после native handler; поздние замены — ошибка. Живые idle child-процессы не мешают успеху. Своя активная Cura/parser-фаза удерживает guard и при outer._slicing=false; timeout не отменяет job. Late final после ошибки может доказать выход и освободить guard, но не меняет failed на completed.
- После close действует прежний30с readonly Qt-бюджет. Передаются только необходимые стадийные events/flags, включая штатные новые стадии после закрытия; scene/PI/новые engine-действия не выполняются. По неизвестному исходу наблюдение снимается, guard сохраняется и нужен перезапуск. Нет wait/kill/своих subprocess, monkeypatch native handlers или ещё одного фонового мониторинга чата.
- Classic/cylindrical и2A/2B сохранены. Общие описания tools/системный промпт/API/README/PLAN уточнены для трёх режимов. HTTP/SPA/downloads/custom-профили не менялись. Native export использует прежние writers/WriteFileJob, свежий slice_id и окончательные chunks; exports.py менять не потребовалось.

## Подтверждённые source-различия

Читалась выданная копия `C:/Users/sevay/Desktop/SourceSTE/STE-Slicer/STE-Slicer-master` и соседний Uranium-steslicer, только для справки, без runtime-зависимости/изменения дистрибутива:

- `plugins/CylindricalBackend/CylindricalBackend.py`, `steslicer/MultiBackend.py` и BackendManager: outer не имеет собственного parser _process/_socket; использует getBackends. Его StartSliceJob создаёт сообщения через child sockets **до** дальнейших reset: при None будет честный backend_state_unavailable до forceSlice, сокеты вручную не создаются.
- Full `StartSliceJob.py`: платформа _start_slice_job_build_plate и StartJobResult.Finished, а не поле CLI job. Внутри штатно ожидает свои nested prep jobs.
- Основание — `steslicer/Utils/GenerateBasementJob.py`, не plugin-local job. Оно не задаёт Job.result (None законен), outer не вызывает его setBuildPlate (None законен). Успех проверяется по finished/running/error/cancel и native getGCodeList/getLayersData. Нельзя требовать enumFinished у всех стадий.
- Full `ProcessCliJob` — `steslicer/Utils/ProcessCliJob.py`, не одноимённый plugin/CLI job. Проверяются _process/_build_plate_number и Finished плюс собственный Glicer exit0. Source cancel здесь не прерывает polling Glicer — guard не освобождается только по отмене.
- Outer `_onGenerateBasementJobFinished` штатно пересоздаёт Cura socket/process; `_onProcessCliFinished` пересоздаёт parser ещё до проверки error/cancel. Плагин лишь наблюдает этот порядок. Processing0.5 приходит до создания Glicer/ProcessCliJob; deferred ставится до их быстрого finished по прежнему UM.Signal/JobQueue FIFO-порядку.
- `_onSlicingFinishedMessage` после Cura промежуточный; `_onCliParserFinishedMessage` даёт Done до последних замен G-code/сброса флагов. Native outer агрегирует оценки и передаёт их в PI; плагин не суммирует их сам. Объявленный basement.timeMaterialEstimates outer в просмотренном source не подключает: полноту/точность native-оценок основания отдельно не обещаем и этот пробел своими формулами не подменяем.

## Проверки

- `python -m unittest discover -s tests -p test_full_slicing.py -q`: **18 тестов OK,0.082с**. Source-shaped fake chain: полный/быстрый цикл, разные job API, промежуточные/child Done, последние outer PI, error/cancel/result каждой job, Glicer nonzero, чужая платформа/job/process/PI, повторный finished, разрешённые reset/поздние подмены/старый socket/parser exit, timeout/live phase/late final, shutdown до/после30с и продолжение native стадий после close без scene/PI, stale chunks.
- Прежний writer harness записывает действительные временные G-code/ZIP-файлы с fake native-данными: в G-code сохранены basement и окончательный full-фрагмент, в workspace — captured cylindrical_full/layer_height. Это fake writers/подменные native API, **не** установленный STE/настоящие траектории/Preview/reopen.
- Итоговый `python -m unittest discover -s tests -q`: **275 тестов OK,15.291с** — прежние257 сохранены плюс18. Предыдущий полный274 также прошёл16.133с. Один первый новый тест выявил ошибку своей cleanup-фикстуры: при подмене child process исходный захваченный процесс оставался жив, guard правильно удерживался. Фикстура теперь явно завершает оба fake-процесса; runtime-проверка не ослаблена.
- `node --check plugin/AIAssistant/web/app.js`; `node tests/test_agent_web.js`: **8 offline DOM-сценариев OK**. SPA не менялась.
- Грамматика Python3.5 для38 Python-файлов, plugin.json api5 — OK на отдельном Python; `git diff --check` — чистый. Новых зависимостей нет. Это не запуск в bundled Python3.5/Qt/Arcus.

После достаточного зелёного набора runtime не расширялся. Все проверки с подменами явно отделены от native; реальный engine/провайдер не вызывались.

## Ручная приёмка и ограничения

Самостоятельные PowerShell-команды — `plugin/README.md`, раздел ТЗ11. После отдельно согласованного обновления установленной копии: сохранить проект, Hybrid530/cylindrical_full, одна подходящая модель, auto-slice off, без preview; проверить настройки/габариты, запустить один свой slice_id, дождаться фактического финала, сравнить время/материал с UI, выполнить прежний export/статус. Реальный G-code открыть в Preview,3MF — повторно как workspace с полным режимом/настройками/материалами/геометрией; затем регрессия classic/cylindrical. Команды подготовлены, **не запускались**; принтер не запускать. Нет engine/активации/подходящей модели — записать блокер без обхода.

Неинициализированные child sockets блокируют preflight; требуется штатная готовность приложения, плагин её не имитирует. Протокол не содержит plugin slice_id: одновременные неотличимые ручные операции/пакеты того же parser не поддерживаются, как в10. При исчезновении Qt event loop cleanup не гарантируется; неизвестный выход сохраняет guard. Source/layout других версий требует отдельной проверки. Реальные full-траектории, оценки/активация, Preview и смысл повторно открытого3MF пока не подтверждены. Ручная приёмка ТЗ03/04 и других native-этапов остаётся открыта.

Отчёт передан ведущему; custom-профили, downloads и следующие этапы не начинались.
