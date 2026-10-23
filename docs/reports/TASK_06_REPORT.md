# ТЗ 06 — штатные G-code и workspace 3MF

Дата: 23.10.2026. **Реализация подготовлена к ревью. Native-экспорт, Preview и повторное открытие проекта не выполнялись.** Ручная приёмка ТЗ 03/04/05/06 открыта; установленный плагин не обновлялся.

Переход к ТЗ 06 выполнен после сообщения ведущего о ревью 05 и проверки первоисточника разрешения пользователя через read_thread: чат «Лид» 01a16865-7080-7af2-a609-961a9f5c5115, human userMessage 01a16990-f264-7d01-b732-406a9978522c. Пользователь разрешил ведущему координировать «Разраб» и последовательно продолжать план. Это не разрешение на установку/commit/push.

## Объём изменения

| Файл | Изменение в ТЗ 06 |
| --- | --- |
| `plugin/AIAssistant/slicer/exports.py` | Новый модуль: preflight, штатный WriteFileJob, I/O worker, проверка файлов, статус/реестр своих артефактов, budget/cleanup |
| `plugin/AIAssistant/slicer/adapter.py` | export_gcode/save_project_3mf/get_export_status, общий lock и lifecycle нового модуля |
| `plugin/AIAssistant/agent/tools.py` | Три схемы в прежнем едином ToolRegistry; старые инструменты сохранены |
| `tests/test_slicer_exports.py` | 19 изолированных сценариев с настоящим filesystem и подменами native writers/jobs/Qt |
| `tests/test_slicer_settings.py`, `tests/test_slicer_modes.py` | Только дополнены ожидаемые tools/list, прежние проверки сохранены |
| `docs/API_CONTRACT.md`, `plugin/README.md` | Контракт, ограничения и последовательные PowerShell-команды приёмки |
| `README.md` | Только актуальные возможности/проверки; структура чужого упрощённого README сохранена |
| `docs/PLAN_SLICER.md`, `docs/COORDINATION.md` | Статус текущего этапа и ожидание ревью поверх чужих изменений |
| `docs/reports/TASK_06_REPORT.md` | Этот отчёт |

`slicing.py` и тесты нарезки в ТЗ 06 не переписывались: valid/slice_id проверяются прежним Slicing.result, используется его текущая запись и тот же lock. Второго источника актуальности нарезки нет. SPA/LLM/server/QtBridge и штатный Extension не менялись. Новых зависимостей нет, Python-код совместим с синтаксисом 3.5, plugin.json сохраняет api:5. artem_backend и его генераторы не переносились.

## API, подтверждённые по справочным исходникам

Читались STE `C:/Users/sevay/Desktop/SourceSTE/STE-Slicer/STE-Slicer-master` и соседний `Uranium-steslicer`. Справочные исходники и дистрибутив не менялись, пути не добавлялись в runtime.

| Источник | Подтверждённый факт |
| --- | --- |
| Uranium `UM/FileHandler/FileHandler.py:146`, STE `plugins/3MFWriter/__init__.py` | getWriter выбирается из конкретного handler. Под ID 3MFWriter зарегистрированы **разные** mesh/workspace writer-объекты; PluginRegistry.getPluginObject не используется |
| STE `plugins/GCodeWriter/GCodeWriter.py`, write | Текстовый mode; читает scene.gcode_dict активной платформы, добавляет thumbnail/version/mode/bounding и сериализованные настройки. Наш код G-code не генерирует |
| Тот же файл, _createSnapshot | Декоратор call_on_qt_thread переносит thumbnail/OpenGL на Qt; write должен входить с worker. Остальные чтения app внутри native GCodeWriter осуществляются встроенным механизмом writer |
| STE `plugins/3MFWriter/ThreeMFWorkspaceWriter.py`, write | Декорированное тело на Qt вызывает mesh writer с setStoreArchive(True), дописывает глобальный стек, контейнеры машины/экструдеров, Preferences и version, закрывает ZIP и сбрасывает storeArchive |
| STE `plugins/3MFWriter/ThreeMFWriter.py` | Записывает 3D/3dmodel.model, content types/relations; сохраняет group/child local transforms и object settings; фильтрует активную платформу. В mesh-only writer workspace-конфигурации нет |
| Uranium `UM/Scene/SceneNode.py:440` | getLocalTransformation возвращает deepcopy; штатный preMultiply при сериализации не меняет трансформацию исходного узла |
| STE `steslicer/Utils/Threading.py` | call_on_qt_thread всегда callLater + Event.wait, без проверки текущего потока. Вызов декорированного writer на Qt даёт deadlock; исключение в Qt handler может не вызвать Event.set |
| Uranium `UM/FileHandler/WriteFileJob.py`, `UM/Job.py`, `UM/JobQueue.py` | WriteFileJob(writer,stream,nodes,mode), progress/finished; getResult/getError. JobQueue выполняет run на worker, ловит исключения worker, устанавливает finished после выхода. Ошибка внутри отдельного декорированного Qt handler не обязательно вернётся в worker |
| Uranium `plugins/LocalFileOutputDevice/LocalFileOutputDevice.py` | Штатный образец запуска job, выбора TextMode/BinaryMode и закрытия stream. В нашем коде flush/close подтверждаются **до** completed, а не после сигнала успеха |
| Uranium `UM/OutputDevice/OutputDeviceManager.py`, LocalFileOutputDevice | getOutputDevice("local_file"), writeStarted. Публичного общего getter занятости нет; подтверждённое _writing читается readonly, не меняется |
| Uranium `UM/Resources.py:244` | Публичный Resources.getCacheStoragePath; используется как основа собственной экспортной папки |
| WorkspaceWriter._writeContainerToArchive, ContainerRegistry | Список ожидаемых файлов учитывает MIME suffix и base_file, исключает EmptyInstanceContainer; имена SteSlicer/<id>.<suffix> подтверждены по writer |

Ни декораторы, ни writer.write не извлекаются через inspect/monkeypatch. Собственной сериализации проекта/геометрии и ручного генератора G-code нет. Синхронного native write внутри bridge.run нет.

## Потоки, корреляция и публикация

| Где | Наши действия |
| --- | --- |
| HTTP worker | Валидация, короткий QtBridge-вызов. Вся запись не удерживает HTTP-запрос |
| Qt | Проверка актуального completed classic slice_id и занятости, получение writer/cache/узлов/ожидаемых контейнеров; создание и запуск WriteFileJob; подписки, native getResult/getError, setStoreArchive(False), повторная проверка контекста и регистрация артефакта |
| Native JobQueue worker | Вход в неизменённый штатный writer, как в LocalFileOutputDevice. WorkspaceWriter сам переносит write в Qt; GCodeWriter — только snapshot. Это граница ответственности встроенного API, не дополнительные app-вызовы нашего HTTP/I/O worker |
| Наш stdlib I/O worker | Создание уникальной папки/stream, ожидание finished через Python Event, закрытие archive/flush/stream, проверки ZIP/XML/G-code, эксклюзивная публикация, cleanup. Методы app/scene/stack/backend из него не вызываются |

Перед запуском под общим lock вызывается прежний Slicing.result, затем читается его текущая запись: scene/root/stack/backend/платформа/буфер. Проверяются idle engine/слои и штатная локальная запись. Старый/failed/stale/неизвестный slice_id не вызывает writer. Для G-code берётся mesh-handler GCodeWriter/TextMode UTF-8; для проекта — workspace-handler 3MFWriter/BinaryMode и фактический mesh writer для cleanup archive.

Передаются **живые верхние печатные root-узлы/группы** активной платформы без копирования/репарентинга. Child transforms и overrides сериализует штатный writer. Непечатный wrapper-родитель или служебный потомок внутри группы отклоняется: иначе native converter мог бы потерять трансформацию родителя/записать лишний mesh. Это явное ограничение, не тихое удаление узлов.

Файловый worker создаёт уникальную `Resources.getCacheStoragePath()/AIAssistant/exports/export-<random>/` и эксклюзивный writing.tmp; все пути генерируются внутри модуля. На Qt после проверки контекста создаётся WriteFileJob, подписки устанавливаются до start. State меняется queued → running; progress — native Job.progress/100 либо null. Чужой finished/job не принимается.

Completed требует собственного finished, положительного getResult без getError, успешных flush/close, непустого файла и проверок содержимого. Затем Qt повторно проверяет исходную нарезку, разрешает публикацию; worker эксклюзивно создаёт result.gcode/project.3mf, закрывает его, сравнивает размер и убирает временный файл. Перед регистрацией готового artifact Qt снова проверяет контекст. Пока native write/финализация не закончены, действует тот же lock нарезки/импорта/настроек. После полного успеха последовательный второй экспорт разрешён.

G-code сверяется с исходной последовательностью engine chunks своей платформы: она должна точно присутствовать в выходном файле наряду с штатными заголовками/настройками, и содержать команды движения. Это чтение/сравнение, не генерация. Для TextMode отключена Windows-трансляция LF при открытии нашего stream, чтобы сравнение соответствовало исходным строкам.

3MF проверяется на читаемый ZIP/CRC без дубликатов, 3D/3dmodel.model с mesh/resources/build, ссылки build на object и непустые ожидаемые SteSlicer/ файлы стека машины/экструдеров/контейнеров, preferences/version. Mesh-only ZIP не принимается. **Структурная проверка не доказывает восстановление всех параметров — повторное открытие в STE Slicer остаётся обязательным.**

Папки/файлы не перезаписываются. При коллизии существующий файл не удаляется; cleanup затрагивает только успешно созданные нами пути, rmdir — только свою пустую папку, без recursive delete. Ошибка открытия/диска/flush/close/проверки/копирования не создаёт успешного артефакта. В диагностическом completed возвращается generated local_directory; реестр содержит только metadata своих проверенных файлов и не предоставляет чтение произвольного пути.

## Таймаут, ошибки и shutdown

Бюджет — **120 секунд**, QTimer — 100 мс, клиентский polling — раз в секунду. Изменение печатного контекста, ручная writeStarted или timeout фиксируют failed. До фактического native finished stream не закрывается и guard остаётся; поздний finished только очищает ресурсы. Автоповтора/cancel/kill нет. Чтения и status доступны. После завершения исторический файл сохраняет старый slice_id, при изменении контекста его status.valid=false.

После подтверждённого выхода 3MF job читается собственный archive и вызывается setStoreArchive(False), archive закрывается файловым worker. Если обнаружен ещё работающий чужой UI writer, его archive не изменяется, операция не признаётся успешной. Ручная параллельная запись не поддерживается; такая неопределённость требует ручного контроля/перезапуска.

Shutdown останавливает таймер/публикацию, отключает ненужные сигналы, освобождает guard закрываемого адаптера. Если native job уже пишет, до его выхода сохраняется минимальный finished-callback; stream не закрывается преждевременно. Доставленный поздний finished закрывает/удаляет временные данные без публикации. Если Qt-loop уже не доставляет callback или writer завис в декораторе, гарантировать cleanup до выхода процесса невозможно — это явно не успех. Новые действия после close отклоняет прежний QtBridge.

## Контракт

| Метод | Единственное обязательное поле |
| --- | --- |
| export_gcode | slice_id |
| save_project_3mf | slice_id |
| get_export_status | export_id |

Для каждого поля — `type:string,minLength:1`; общая схема `type:object,required:[field],additionalProperties:false`. Произвольные пути/лишние поля запрещены. Валидация до API, неверные запросы — JSON-RPC -32602; без app — slicer_not_connected. MCP envelope `{ok,data}` / `{ok:false,error:{code,message}}` и isError сохранены. Failed-статус читается успешно, причина находится в data.error. ID непрозрачны и хранятся в памяти текущей сессии.

Status: export_id, slice_id, kind, state, progress, error, artifact, local_directory, valid. Artifact только после completed: artifact_id, file_name, kind=project/gcode, size_bytes, slice_id, source=ste_slicer, writer_id и writer_type. Готовые файлы остаются в кэше, который может очищаться приложением. HTTP-download/upload и кнопки UI в ТЗ 06 не добавлены.

Новые операционные коды: export_busy, export_not_found, export_writer_unavailable, export_workspace_unavailable, export_scene_unsupported, export_failed, export_io_error, export_invalid_file, export_timeout, export_result_stale, export_conflict. Preflight сохраняет slice_not_found/slice_not_ready/slice_result_stale; общие slicer_not_connected/qt_timeout/slicer_error и busy-коды прежних setters не меняются. Полные схемы данных/значения ошибок — [API_CONTRACT](../API_CONTRACT.md#штатный-экспорт-тз-06--реализован-runtime-приёмка-открыта).

## Проверки и результат

Windows, Python **3.13.6**, финальный полный прогон:

```text
python -m unittest discover -s tests
Ran 140 tests in 9.036s
OK
```

Все **121** прежних проверок сохранены, добавлены **19** сценариев экспорта. Проверены:

- schemas/JSON-RPC/dev, unknown/stale/failed slice_id, отсутствующий writer, занятые native layers/UI, отсутствие job до успешного preflight;
- правильные handlers и text/binary mode, worker-вход в writer и Qt-тело workspace, delayed job, настоящий filesystem и согласованная пара одного slice_id;
- native false/exception/getError, пустой/header-only G-code, corrupt/mesh-only/bad-XML 3MF без completed; сброс storeArchive после выхода;
- отказ открытия, flush/close, неполная публикация, отсутствие приватных значений исключения в ответах/нашем журнале;
- неизменность существующего чужого файла при коллизии; уникальные свои пути и реестр metadata, неизвестный export_id — операционная ошибка;
- общая защита записи/слайсинга при доступных чтениях, изменение геометрии во время write, UI-конфликт, timeout до/после native-start, поздний finished;
- shutdown до запуска и при пишущем job: stream жив до finished, затем закрыт, публикации нет; native callbacks/таймер очищаются;
- группы и child transforms передаются неизменными, служебный потомок явно отклоняется; исторический файл не переименовывается под новую сцену.

В полном наборе прошла грамматика **Python 3.5** всех файлов плагина и api:5; это не запуск на реальном Python 3.5/Qt. Прежние реальные loopback HTTP/MCP-тесты также прошли (полный набор запускался с разрешённым доступом вне sandbox). Новые fixtures остаются подменами, даже когда используют настоящие временные файлы.

Все **22 PowerShell-блока** plugin/README.md разобраны штатным Parser без выполнения, ошибок нет. `git diff --check` прошёл; предупреждение Git о CRLF → LF корневого README не является whitespace-ошибкой. Тестовые writer-файлы удалены TemporaryDirectory и не помещались в results/.

## Native-приёмка и ограничения

**Реальные result.gcode/project.3mf из установленного STE в этом задании не получены.** Установленный плагин не обновлялся по действующему ограничению, Preview/повторное открытие не выполнялись. Наличие кода и fake-архива за native-успех не выдаётся.

В [plugin/README.md — ТЗ 06](../../plugin/README.md#тз-06-штатный-экспорт-g-code-и-проекта-3mf) подготовлены команды: текущий порт и актуальный slice_id из прогона 05 → baseline машины/моделей/качества/материалов/высоты → export_gcode → конечный polling → save_project_3mf → polling → проверка размеров и пользовательские копии из generated local_directory. Клиентский предел 135 секунд плюс один HTTP timeout 15 секунд не отменяет native job.

После обоих экспортов пользователь сохраняет текущую работу, открывает G-code штатным Preview и **3MF как проект**, сравнивает машину/classic, материалы обоих экструдеров, качество/overrides, объекты/ориентацию/габариты. Затем повторно читает MCP и сохраняет JSON/скриншоты. Открытие G-code раньше второго экспорта изменило бы исходный slicing-контекст, поэтому порядок в инструкции явный. Этот сценарий не является mesh-импортом load_model и не запускает физическую печать.

Оставшиеся ограничения:

- Только актуальная classic-нарезка одной платформы; сохранение проекта без нарезки, 5D, новые инструменты настроек/моделей и публичные файловые HTTP-маршруты не реализованы этим ТЗ.
- Native writers работают с живой сценой по штатному механизму. Наш lock защищает инструменты адаптера, не произвольные ручные/сторонние job. Обнаруженная UI-запись даёт отказ; неотличимые сторонние записи вне OutputDeviceManager не обещается распознать. Во время native write пользователь не должен параллельно редактировать/писать проект.
- В fork call_on_qt_thread не освобождает Event при исключении в Qt handler. В этом случае нет native finished: через 120 секунд status.failed, stream/guard до выхода job не освобождаются. Требуется перезапуск, без исправления дистрибутива/обхода декоратора. Наш таймер также не может прервать длинное штатное workspace-тело, уже работающее на Qt.
- Структурные проверки файлов не доказывают физическую пригодность G-code или точность восстановления всех workspace-полей. Нужны native Preview/повторное открытие, включая object overrides; на выданных моделях это ещё не проверено.
- Непечатные wrapper/служебные потомки внутри группы отклоняются вместо рискованной сериализации. Большие сцены/файлы и отзывчивость native workspace writer не измерялись; G-code при проверке кодируется в worker, не на Qt.
- Реестр IDs только в памяти; файлы кэша нужно копировать для сдачи. Очистка при реальном закрытии приложения/исключениях декоратора остаётся предметом ручной проверки; публикации незавершённого результата нет.

## Сохранность и передача

Сначала прочитан git status; весь незакоммиченный результат 05, TASK_05_REVIEW.md, чужой HEAD b56e6e1 и пользовательские/ведущего untracked сохранены. Новые задания 07/08, scripts/demo_checkpoint.ps1, lock-файлы Word, справочная/установленная копии не менялись. В PLAN/COORDINATION сохранены параллельные дополнения ведущего, обновлён только текущий статус. Корневой README обновлён точечно, его чужая структура не заменялась.

git add/commit/push/merge, установка/публикация, новые чаты/агенты/worktree, реальные LLM-запросы и физическая печать не выполнялись. После этого отчёта исполнитель останавливается для ревью ведущего; ТЗ 07/08 самостоятельно не начинает.
