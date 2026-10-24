# ТЗ 10 — штатная нарезка cylindrical / 5D Spiral

Завершено для ревью по `docs/tasks/TASK_10_SPIRAL_SLICING.md` после возврата с принятых ролей2A/2B. Прочитаны AGENTS.md, план, исходники и TASK_10_PROGRESS; git status проверен. Сохранены изменения08/09/2A/2B, документы ведущего, Word locks и черновая история10. Второй плагин/сервер/QtBridge не создавались. Установка, Git-фиксации/push, реальный LLM, обход активации и физическая печать не выполнялись. Следующий этап не начат.

## Результат

Существующие start_slicing/get_slicing_status/get_slicing_result теперь поддерживают **classic → CuraEngineBackend** и **cylindrical → CLIParserBackend** по регистрации BackendManager и фактическому current backend. **cylindrical_full отклоняется**; его составной basement/Cura/Glicer-конвейер сюда не включён. printing_mode/backend_id захватываются в record, а не подставляются как classic. Схемы, единый реестр/MCP/LLM, общий guard и единицы прежние.

Сохранённый `cli_slicing.py` и hooks в slicing.py закончены: forceSlice запускает внешний штатный backend целиком, наблюдатель связывает собственный prep job, новый Glicer, ProcessCliJob и финальную фазу CLIParser. Для jobs проверяются identity/платформа/running/finished/result/error/cancel, для Glicer — неблокирующий poll и код0. Сменившиеся refs/платформа/принадлежность ProcessCliJob дают конфликт. Ошибка процесса/сокета/parser exit — честный отказ. Debug-сообщение сокета не считается ошибкой. Native handlers не переназначаются; subprocess/wait/kill/генератора траекторий в плагине нет.

ProcessCliJob.Finished, exit Glicer, progress1 и Done различаются. Parser-фаза подтверждается после собственного ProcessCliJob/native Processing; completed требует финального Done и отложенного Qt-readback после замен G-code/сброса native flags, выхода известных стадий/обработки слоёв, неизменного контекста, свежего буфера и подтверждённых оценок. Живой переиспользуемый parser не держит guard после успеха. Время/расход читаются из PrintInformation с прежней проверкой revisions всех трёх полей; промежуточные/старые/чужие оценки не принимаются. Последняя повторная оценка заменяет предыдущую только после подтверждённой передачи в PI. Недоступные показатели — null/warnings; своих формул нет.

Буфер/геометрия/настройки/машина/сцена/режим/платформа остаются частью контекста. Старый буфер не завершает новый run; замена и изменение окончательного CLI G-code in-place делают результат stale. Ошибка/timeout не превращаются поздним Done в completed и не запускают повтор автоматически.

Экспорт использует существующие export_gcode/save_project_3mf/get_export_status, native writers и WriteFileJob с актуальным slice_id и окончательным G-code; exports.py не потребовал изменения. Workspace writer сохраняет текущие контейнеры/Preferences/геометрию с cylindrical/выбранными настройками. Формирование и проверка файла остаются вне Qt по прежней границе; повторное открытие проекта остаётся ручной проверкой.

## Источники и уточнения протокола

Чтение выданной локальной копии `<SOURCE_ROOT>/STE-Slicer-master` (не runtime-зависимость), Uranium — соседний `Uranium-steslicer`:

- `plugins/CLIParserBackend/__init__.py`, `steslicer/BackendManager/BackendManager.py`: cylindrical регистрируется как CLIParserBackend, а cylindrical_full как CylindricalBackend. Manager пересылает оценки всех backend в общую PI; защита от чужого источника сохранена.
- `CliParserBackend.py`, slice около239–261, _onStartSliceCompleted около300–383, _onProcessCliFinished около395–519: Processing приходит до создания следующих refs; Glicer запускается новым Popen, parser _process может переиспользоваться. ProcessCliJob.py ждёт Glicer, но Finished не гарантирует exit0 — код проверяется отдельно.
- `UM/Signal.py`, `UM/JobQueue.py`, `UM/Job.py`: finished из worker ставится в Qt-очередь, flags устанавливаются до signal, functions-observer идёт перед native bound handler. Deferred из prep.finished ставится раньше законченного native handler, создающего следующий job: быстрый ProcessCliJob можно захватить по refs/flags без глобальных JobQueue hooks.
- _onSlicingFinishedMessage около699–738 испускает Done до последних замен placeholder и сброса _slicing. Успех непосредственно из signal handler запрещён; readback отложен.
- `steslicer/PrintInformation.py`, `plugins/GCodeWriter/GCodeWriter.py`, `plugins/3MFWriter/ThreeMFWorkspaceWriter.py`: оценки/данные/writers берутся штатные, workspace пишет активный global/extruder stack и контейнеры, не mesh-only3MF.

Ведущий указал startup-край: когда parser _process отсутствует, source forceSlice создаёт новый socket, даже если старый socket уже есть. Разрешена ровно одна такая замена **в синхронном окне своего forceSlice**, затем socket фиксируется; произвольный поздний replacement остаётся ошибкой. Старый socket до этого запуска не используется как источник parser-ошибок. Позднее появление первого parser-процесса после Listening допускается, дальнейшая подмена — конфликт. Проверены начальные обаNone и old-socket/None-process.

## Timeout и shutdown

Обычный бюджет нарезки180с сохранён. Guard не освобождается по одному _slicing=false, пока свой job/Glicer жив. Плагин не отменяет/не завершает native процесс.

После close CLI ещё до30с выполняется только readonly Qt-poll своих refs/flags/Glicer, без scene/PI/новых engine-действий. Поздний выход без нового signal до deadline освобождает guard и снимает наблюдение. Если deadline истёк, timer/подписки снимаются с неизвестным исходом и удержанным guard; сообщение требует перезапуска приложения. Даже при ошибке чтения полей бюджет не превращается в безграничное наблюдение. Без работающего Qt event loop очистка не обещается. Это не подтверждение отмены. Classic lifecycle сохранён прежним.

## Проверки

- `python -m unittest discover -s tests -p test_cli_slicing.py -v`: **16 тестов OK,0.068с**. Fork-shaped fake jobs/processes/Qt: полный и быстрый циклы, deferred G-code, Processing/Done/PI старого этапа, error/cancel/result обеих jobs, ненулевой Glicer даже при Finished, смена/чужой job, чужая/повторная/непереданная PI, parser/socket failure, timeout/late exit, bounded shutdown до/после deadline без scene/PI, stale mode/буфер, повторный start, неизвестный layout/регистрация и отказ cylindrical_full. Startup socket проверен отдельно.
- В export-сценарии прежний WriteFileJob harness сохранил настоящий временный файл с окончательными CLI chunks и ZIP workspace с captured cylindrical/layer_height; данные создаёт **fake writer**, не установленный STE. Это проверка нашей интеграции/файловой финализации, а не native-export/Preview/reopen.
- Итоговый `python -m unittest discover -s tests -q`: **257 тестов OK,15.593с** — сохранены241 прежний (включая classic/экспорт/2A/2B/lifetime ModelLoad) и добавлены16. До последнего startup-уточнения полный256 также прошёл15.640с. Один новый тест первоначально выявил отсутствие повторной проверки принадлежности уже захваченного ProcessCliJob; исправлено без ослабления проверок.
- `node --check plugin/AIAssistant/web/app.js`; `node tests/test_agent_web.js`: **8 сценариев OK**, SPA в10 не менялась.
- Грамматика Python3.5 для36 Python-файлов и plugin.json api5 — OK на отдельном Python; `git diff --check` чистый. Это не запуск на bundled Python3.5/Qt/Arcus.

После итогового полного набора runtime не расширялся; описания tools/system prompt приведены к двум поддержанным режимам, API/README/PLAN обновлены точечно. Тесты выполнены на отдельном Python с подменами и временными файлами, без вызова engine/активации/провайдера.

## Ручная приёмка и ограничения

PowerShell-последовательность для cylindrical добавлена в `plugin/README.md`, раздел ТЗ10: диагностика → импорт маленькой подходящей модели → чтение настроек/UI → один start/slice_id → bounded polling → фактический result → существующий export/статус → Preview G-code и повторное открытие workspace3MF. Отдельно повторить classic по сохранённым ТЗ05/06. Команды подготовлены, **не запускались** в установленном слайсере. Сначала требуется отдельно согласованное обновление установленной копии; при отсутствии лицензии/engine/подходящей модели зафиксировать блокер без обхода. Принтер не запускать.

Arcus не несёт plugin slice_id: защищены собственные стадии/refs/буферы/контекст и источник PI, но неотличимые посторонние пакеты того же parser при параллельной ручной работе не поддерживаются. Пропущенный/неподтверждённый stage/final не заменяется успехом — будет ошибка/timeout. Проверен известный source layout; другие форки/версии требуют проверки. Native 5D-траектории, оценки/лицензия, Preview и семантика повторно открытого3MF остаются неподтверждёнными.

Ручная приёмка ТЗ03/04 и остальных native-сценариев не объявляется выполненной. cylindrical_full, downloads, новые профили, новый UI/агент и следующие этапы не включены. После отчёта работа остановлена для ревью ведущего.
