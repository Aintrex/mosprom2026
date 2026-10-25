# Наблюдение полного 5D и OpenSCAD

25.10.2026. Основа: **main160adcf**, текущий checkout пользователя. Локальное исправление передаётся ведущему на ревью. Установка/публикация/native-прогон не выполнялись.

## A — исправлено в наблюдателе, проверено с подменами

До runtime-изменений воспроизведены `cura_finished → running0.5 → backendQuit → failed0.5` и последующий рост progress при failed. Источник **реального** пользовательского quit этим не установлен. Новый тест также выявил: общий внешний/дочерний Done мог поставить record.done до финальной очистки внешнего backend. Сначала девять новых тестов на прежнем коде дали9 failures/1 error (дополнительный subTest продолжил работу после первого отказа); затем добавлены проверки доставки в агент/события, порядка функций/методов и неизвестного процесса.

Изменённые файлы:

- `plugin/AIAssistant/slicer/full_slicing.py`: EOF без PID вызывает отложенную Qt-проверку захваченных стадий/процессов вместо безусловного отказа. Сохраняются identity jobs, platform, socket и process, штатные два reset только после собственных finished. Первое появление `_process` после Listening принимается без замены уже захваченного процесса. Ненулевой exit Glicer/дочернего процесса остаётся ошибкой. Exit0 без финального сообщения проверяется отдельным callback после уже поставленных событий; exit0 не означает успех. Отложенные callbacks защищены от старого record/detach/close и ошибок API.
- Там же: внешний финальный Done подтверждается **после** native callback по завершённым стадиям и очистке `_slicing`/`_start_slice_job_build_plate` внешнего backend. Дочерний/общий Done и progress1 недостаточны. Поздний ожидаемый reset после принятого внешнего финала не портит результат; текущий ненулевой exit не скрывается этим правилом. Не наблюдавшийся собственный процесс приводит к отказу с удержанным guard, а не к угадыванию успеха/выхода.
- `slicer/slicing.py`: только полный5D делегирует quit/final стадийному наблюдателю; status получает stage/diagnostics. Classic/Spiral используют прежние ветви. Свежий G-code, последняя PI, контекст/актуальность/готовность стадий по-прежнему обязательны. Progress не заморожен косметически: при настоящем failed новые доли могут наблюдаться, но failed не становится completed.
- `agent/service.py`: дополнительные безопасные поля передаются в snapshot/events и сохраняются при получении финального результата. Новых LLM-запросов, engine-действий, retry или writer-цепочек нет.
- `tests/test_full_stage_lifecycle.py`:13 адресных проверок. `tests/test_full_slicing.py`: прежнее требование ошибки exit0 без финала сохранено; добавлены чтение running и прокачка Qt-callback перед проверкой этой асинхронной ошибки. Другие существующие проверки не ослаблялись.
- `docs/API_CONTRACT.md`, `plugin/README.md`, `README.md`: фактический контракт/ограничения, исправлены устаревшие упоминания бюджета180с на фактические600с текущей основы. Сам бюджет не менялся.

Status полного режима дополнен `stage`: preparing/basement/cura/glicer/parser/finalizing/completed. `diagnostics` содержит фиксированные event, process (`unknown/cura_engine/glicer/cli_parser`), exit_code integer/null и shared_signals boolean. PID/argv/личные пути/имена моделей/сырые логи/ключи не возвращаются. Первый установленный отказ не заменяется последующим неатрибутированным EOF. `quit_source_unknown` означает именно неизвестный источник, не доказанный штатный restart.

## Подтверждённый source-контракт и его пределы

Источники прочитаны только для исследования, не менялись и не копировались в Git:

- `UM/Backend/Backend.py`: `_storeOutputToLogThread(handle)` испускает backendQuit при EOF без PID/returncode. `_onSocketStateChanged(Listening)` вызывает startEngine; новый socket может существовать раньше нового process. `poll()` не блокирует, wait/terminate плагин не вызывает.
- `UM/Signal.py`: функции вызываются раньше bound methods, независимо от порядка connect; Auto с другого потока ставит событие в очередь приложения. Фактический модуль UM.Signal импортирован отдельно с инертными зависимостями приложения: порядок function→ранее подключённый bound method подтверждён. Это не запуск Qt/STE.
- `Backend` помечен `@signalemitter`; `CuraEngineBackend(QObject,Backend)`, `CliParserBackend(QObject,Backend)` и `CylindricalBackend(QObject,MultiBackend)` имеют QObject первым; MultiBackend не вызывает Backend.__init__. Decorator заменяет __new__ **Backend**, а не явно декорирует эти QObject-классы. Синтетическое наследование с первым __new__ подтвердило возможность обхода клонирования реального UM.Signal. Исполнение фактического QObject.__new__ этой сборки не проверялось: PyQt5 в тестовом Python отсутствует. Поэтому код не предполагает изоляцию и проверяет реальную identity трёх сигналов при старте; тест покрывает и общие, и отдельные экземпляры. Не утверждается, что именно общее наследование вызвало пользовательский quit.
- `CylindricalBackend.py`: GenerateBasement.finished → штатный Cura `_terminate/_createSocket`; Cura SlicingFinished → Processing/progress0.5 **перед** созданием Glicer/ProcessCliJob; ProcessCli.finished → штатный parser reset; parser progress →0.6+amount/2.5. Только `_onCliParserFinishedMessage` испускает внешний Done, заменяет финальные tokens и затем очищает plate/_slicing.
- `steslicer/Utils/ProcessCliJob.py`: Finished сам по себе не подтверждает exit0 Glicer; отдельная проверка poll сохранена. Принадлежность ProcessCliJob захваченному process/plate обязательна.

Всё чтение refs/poll/jobs/scene/PI выполняется в Qt через прежние мост/обработчики/таймер250мс; нет sleep/wait/нового фонового процесса в runtime. Существующий бюджет600с, отмена, ошибки сокета, смена контекста/платформы/буфера/оценок и блокировки записей сохранены. Failed/timeout не разрешают автоматический retry/экспорт. При неизвестном процессе guard сохраняется; при закрытии прежнее ограниченное readonly-наблюдение30с, затем нужен перезапуск. Общие сигналы не дают engine request ID: параллельные ручные операции с тем же контекстом не поддерживаются.

## B — установлен факт потери диагностики и опасного fallback; причина exit1 открыта

Прочитан встроенный `plugins/CuraEngineBackend/StartSliceJob.py:237–405` и вызывающий его `plugins/CylindricalBackend/StartSliceJob.py`. Для cylindrical_full создаётся цилиндр радиуса **(cylindrical_mode_base_diameter+cylindrical_mode_overlap)/2**, длины **2×node.getBoundingBox().height**, с поворотом pi/2 вокруг X: ось цилиндра — мировая Y, центр — начало координат. Вершины модели перед boolean переводятся её world transformation. Выполняются fill_holes/fix_normals, затем `mesh.intersection(cutting_mesh,engine="scad")`. Ошибка ловится общим except; логируется `Failed to intersect model!`, а **cutting_result заменяется всем cutting_mesh**, создаётся support_mesh. Следующая операция верхней поддержки — difference и может логировать тот же текст. Сообщение без точного stack frame не различает эти две операции. Внешний prep после этого способен вернуть Finished и довести цепочку до финального parser callback.

Поэтому результат такого fallback не тождественен правильному пересечению исходной детали. Исправление наблюдателя A **не исправляет geometry preparation внутри STE**. Никакой подмены native handlers, диаметра, материала, масштаба или режима в плагин не добавлено. Технический completed означает завершение наблюдаемого native-конвейера, не проверку корректности его fallback-геометрии.

Дополнительно только как данные прочитан установленный `python35.zip`, SHA256 `50fbff726be95c81a3b28e8a9c3d11b7763df2baf068e9f9a94d571e01d58096`. В нём нет исходников trimesh, прочитаны marshal/bytecode Python3.5 **без исполнения**:

- `trimesh/interfaces/scad.pyc`: interface_scad строит вызов OpenSCAD через MeshScript; boolean строит именно запрошенный intersection/difference, не доказанную «union-схему».
- `trimesh/interfaces/generic.pyc`, MeshScript.run: Template.substitute(...).split(), open(os.devnull,'w'), check_call(...,stdout=devnull,stderr=subprocess.STDOUT,startupinfo=...). То есть stdout **и stderr теряются в devnull**. CalledProcessError не содержит потерянного текста; включение обычного debug-логирования не восстановит его задним числом. `__exit__` удаляет временные script/input/output.
- Строковый split также небезопасен для executable/temp paths с пробелами. Синтетическая проверка подтверждает дробление argv, но **не доказывает** этот сценарий у пользователя. Для ненайденного исполняемого файла обычно был бы другой отказ; имеющийся exit1 не устанавливает ни проблему пути, ни проблему геометрии. Сборка/среда реального пользователя может отличаться от прочитанной копии.

| Гипотеза | Что установлено / чего не хватает |
| --- | --- |
| Пустое пересечение | Возможно, если мировая модель не пересекает цилиндр на осиY. Нужны актуальные world transform, габариты и D/overlap **конкретного запуска**; bbox и корректные рёбра STL этого не доказывают. Самопересечения и ориентация трёх моделей не исследованы. |
| Ось/диаметр/положение основания | Формула/центр/ось подтверждены исходником. В fdmprinter default D6мм/overlap0, но это не реальные настройки пользовательского профиля/проекта. Менять их наугад нельзя. |
| Windows paths/temp files | split и удаление temp подтверждены bytecode. Фактический argv/доступность/права/версия temp-файлов проблемного запуска неизвестны. |
| OpenSCAD/CSG/геометрия | По данным ТЗ процесс вернул1; реальный stderr утрачен. Не различены empty output, non-manifold/self-intersection, parse/import/dependency либо другие ошибки движка. Сам факт exception не устанавливает точную причину. |

Из ТЗ известны события24.10 23:56:29/23:57:36 (intersection/exit1), затем23:57:01/23:58:06 (финальный CLIParser callback). Это не наш новый runtime-прогон. Их привязка к конкретному присланному slice-1/детали не доказана. Исходные проверки2/5/7 ведущего (бинарная длина, конечность, ненулевые треугольники, границы/число смежных граней) не заменяют CSG/5D-приёмку. Архив/модели/приватные журналы не добавлялись в Git.

### Минимальное предложение разработчику STE — не применено

1. В trimesh MeshScript.run передавать argv списком, без split строки; stdout/stderr собирать в локальный временный diagnostic stream вместо devnull, а при ненулевом exit читать ограниченный хвост. Не журналировать ключи/rawкоманды/приватную геометрию; диагностический хвост перед передачей проверяет пользователь. Сохранять рабочие файлы для разбора только по явному выбору пользователя. Это место, где ещё доступен настоящий stderr.
2. В StartSliceJob вместо `cutting_result=cutting_mesh` после boolean exception завершать подготовку с `StartJobResult.Error`, а пустое/невалидное пересечение проверять отдельно. Такой fail закрывает дальнейшую подготовку, а не выдаёт резервный цилиндр за результат детали. Для различения intersection/difference нужны отдельные безопасные коды/контекст операции. Точный патч/совместимость/регрессия в дистрибутиве требуют отдельного согласования; плагин эти обработчики не заменяет.

## Проверки

| Проверка | Фактический результат |
| --- | --- |
| Новые lifecycle-тесты, окончательный адресный запуск | **13 OK,0.156с**: EOF0.5/поздний старый EOF, Listening/process=None→adoption, current exit0/7 без финала, exit9 не маскируется Glicer, Glicer exit4/guard/поздние события, shared Done, финал/exit race, parser exit3, неизвестный process/guard, timeout, UM-order, agent snapshot/events/final без новых LLM-запросов |
| Существующий полный5D, адресно | **19 OK,0.297с** после поправки асинхронного ожидания; окончательный общий прогон также не дал ошибок этого файла |
| Существующий Spiral, адресно | **17 OK,0.190с**; код CliStages не менялся |
| Первый общий набор (до двух последних новых тестов) | 409,29.184с, одна ошибка cleanup ниже |
| Окончательный `python -m unittest discover -s tests -q` | **411,29.164с:410 без ошибок,1 error cleanup**; весь набор зелёным не объявляется |
| Адресный `test_operation_events.py` |20,1.342с, та же ошибка cleanup |
| Контроль прежней основы | Тот же одиночный тест на **неизменённом HEAD160adcf**, в изолированной копии под ignored tmp:1 error,0.246с, тот же WinError32. Checkout/ветка не переключались. |
| `node tests/test_agent_web.js` | **34 сценария OK**, frontend не менялся |
| Грамматика Python3.5 / manifest |53 Python-файла plugin/tests через ast.parse(feature_version=(3,5)) OK; api5. Это не исполнение в Python3.5/Qt/STE. |
| UM/source/B проверки | Порядок фактического UM.Signal подтверждён с инертной app; прочитаны3 trimesh pyc как данные; синтетически проверены отсутствие отброшенного вывода в CalledProcessError, split пути с пробелами и возможность непересекающихся смещённых bounds. Это не геометрический прогон моделей пользователя. |
| `git diff --check` | OK |
| PowerShell-инструкция ниже |1 блок разобран System.Management.Automation.Language.Parser:0 ошибок; команды не исполнялись |

**Незелёная регрессия:** `test_operation_events.OperationEventsTests.test_busy_second_export_is_not_started_and_turn_continues`. При cleanup `tempfile.TemporaryDirectory.cleanup → shutil._rmtree_unsafe → os.unlink` возникает `PermissionError:[WinError32]`, файл `.../exports/.../writing.tmp` ещё открыт. Сбой повторён на базовом HEAD, текущие writers/этот тест не менялись. В helper clean_workers возможна ранняя проверка пустого списка WriteFileJob до его создания FSworker, затем фоновая запись остаётся открытой; это объяснение по коду, отдельное исправление не включено. Остальные негативные тесты выводят ожидаемые ошибки API в stdout. Общий набор не перезапускался многократно ради случайного зелёного результата.

## Ручной прогон и недостающий диагностический фрагмент

**Не выполнялось.** После отдельного согласования обновления установленной целой копии и прогона: сохранить проект, Hybrid530V5.2.4, cylindrical_full, auto-slice off, одна платформа, без Preview. Для2/5/7 по одной загруженной модели записать фактические world position/rotation/scale, габариты, D/overlap, исходные настройки; не менять их для обхода ошибки. Открыть новый адрес через меню. Прежние load/list/габариты и native-сценарии сохранены в plugin/README.

Ниже только подготовленные команды чтения/запуска **одного** ручного сценария; они не исполнялись. Модель предварительно загружена и проверена в UI. Адрес/порт берётся из текущего меню, без ключа/токена в URL:

```powershell
$fullUi = [Uri](Read-Host 'Новый адрес AI Assistant из меню STE')
$fullBase = $fullUi.GetLeftPart([System.UriPartial]::Authority)
if ($fullUi.Scheme -ne 'http' -or $fullUi.Host -notin @('127.0.0.1','localhost')) { throw 'Нужен локальный адрес STE' }
function Invoke-FullDiagnosticTool($name, $arguments) {
    $rpc = @{jsonrpc='2.0';id=1;method='tools/call';params=@{name=$name;arguments=$arguments}} | ConvertTo-Json -Depth 20 -Compress
    $reply = Invoke-RestMethod -Method Post -Uri "$fullBase/mcp" -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($rpc)) -TimeoutSec 15
    if ($reply.error) { throw ($reply.error | ConvertTo-Json -Depth 10 -Compress) }
    $value = $reply.result.content[0].text | ConvertFrom-Json
    if (-not $value.ok) { throw ($value.error | ConvertTo-Json -Depth 10 -Compress) }
    return $value.data
}
$fullRun = Invoke-FullDiagnosticTool 'start_slicing' @{}
$fullId = $fullRun.slice_id
$fullDeadline = (Get-Date).AddSeconds(650)
do {
    $fullStatus = Invoke-FullDiagnosticTool 'get_slicing_status' @{slice_id=$fullId}
    $fullStatus | Select-Object slice_id,state,progress,stage,valid,error,diagnostics | ConvertTo-Json -Depth 10 -Compress
    if ($fullStatus.state -in @('completed','failed')) { break }
    if ((Get-Date) -ge $fullDeadline) { throw 'Клиентское ожидание истекло; это не отмена engine. Не повторять автоматически.' }
    Start-Sleep -Seconds 1
} while ($true)
if ($fullStatus.state -eq 'completed' -and $fullStatus.valid) {
    Invoke-FullDiagnosticTool 'get_slicing_result' @{slice_id=$fullId} | ConvertTo-Json -Depth 10
}
```

Сохранить только времена запуска/событий и строки status со stage/diagnostics. Из журнала вручную выделить короткий фрагмент **этого временного окна**: название операции intersection либо difference/stack frame без приватного пути, факт exit code, безопасные сообщения вокруг старта стадий/финала. Удалить argv, пользовательские каталоги, имена приватных файлов, данные провайдера; полный журнал не передавать. Если текста OpenSCAD нет — отметить «stdout/stderr отброшен MeshScript», не домысливать причину. Для получения реального диагностического хвоста нужен согласованный пункт1 предложения STE выше, простое повторение с тем же devnull данных не добавит.

Если в этом окне есть Failed to intersect/exit1, даже технический completed не закрывает B: сначала разбор geometry preparation. Без подтверждённых completed/valid нельзя экспортировать/повторять автоматически. Для подтверждённого корректного результата вручную сравнить время/расход с UI, проверить реальные слои в Preview; G-code проверить в предпросмотре, 3MF сохранить штатно и повторно открыть. Физическую печать не запускать. Ручные приёмки ТЗ03/04 и качество5D этим отчётом не закрыты.

## Передача

A готово к ревью в исходниках/подменах; реальная атрибуция quit и прогон2/5/7 остаются открыты. B не закрыто: обнаружены потеря stderr и fallback, но точная причина exit1 неизвестна и безопасный native-патч вне полномочий этого задания не применён. Плагин не может достоверно обнаружить проглоченное native исключение через публичный результат Job; global Logger/native handlers не перехватывались.

Сохранены начальные чужие изменения `docs/COORDINATION.md` и файл текущего ТЗ. Один plugin/registry/QtBridge; classic/Spiral, writers, frozen/helper, настройки пользователя и исходные модели не менялись. Нет Git add/commit/push/merge/переключений, AppData-установки, реальной LLM/STE/MCP/engines/принтера. Исследовательские скрипты/копия HEAD находятся только под ignored tmp. После отчёта работа остановлена для ревью ведущего.

## Ревью 25.10 — поправка после интеграции

Принято: отложенная проверка неатрибутированного `backendQuit`, финал полного 5D только после внешнего Done и очистки `_slicing`, stage/diagnostics.

Изменено одно правило. Ненулевой код выхода процесса стадии, которая **уже отдала результат** (Cura после запуска Glicer; парсер после финального сообщения), больше не переводит запуск в failed, а пишется в diagnostics (`settled_process_exited`). Причины по исходникам:
- после `_onSlicingFinishedMessage` G-code Cura уже в буфере, Glicer — отдельный процесс;
- парсер после финала штатно завершается и пересоздаётся (`CliParserBackend._onSocketError → _terminate → _createSocket`), как в 5D Spiral, где это уже не считается ошибкой;
- `Popen.terminate()` на Windows задаёт код 1, поэтому штатное гашение процесса неотличимо от «ненулевого выхода».

Выход **до** результата стадии по-прежнему ошибка: тест с кодом 9 до доставки Cura сохранён. Тест `test_current_nonzero_parser_is_not_hidden_by_final_done` заменён на `test_parser_exit_code_after_final_message_does_not_void_delivered_gcode`; добавлен `test_settled_cura_killed_during_glicer_stage_does_not_fail_run`. На прежней версии оба падают.

`test_busy_second_export_is_not_started_and_turn_continues` теперь дожидается окончания принятой записи G-code: прежний WinError32 при очистке был артефактом теста (файл ещё открыт writer'ом), а не дефектом плагина. Полный набор: 412 Python OK, 34 JS OK.
