# Приёмка опубликованной версии AI Assistant

24 октября 2026. Проверяем canonical `plugin/AIAssistant/` из публикации **`84c1e021f3bfc94c3d8ddc72a041091f2e2dc3de`** (`main`, автор/коммитер владелец репозитория). В исходниках приняты каталог120 параметров/10 групп и сохранённые диалоги2G;363 Python/30 JS и offline browser QA не заменяют этот прогон в STE. Успехи ниже заранее не отмечены.

## 1. Версия и подготовка

Коммиты только с документацией могут иметь другой SHA при том же runtime-коде плагина.84c1e02 здесь обозначает проверенный исходный срез; запишите фактический SHA своей копии и отдельно факт установки. Новая инструкция сама не обновляет установленный плагин.

Запишите отдельно **SHA скачанной копии**, **какую целую папку установили**, **факт обычного закрытия/перезапуска STE**, версию STE и текущий порт. Скачивание/push не обновляют установленный плагин. API сообщает версию приложения, но **не Git SHA установленного плагина**; наличие120 ключей и saved_dialogs подтверждает возможности, не точное происхождение файлов.

Для Git-копии источник можно проверить чтением, без checkout/pull/reset:

```powershell
git rev-parse HEAD
git status --short
```

Ожидаемый SHA указан выше; при другой/изменённой копии сначала согласуйте источник проверки. Для ZIP запишите, из какой публикации он получен. Установку выполняет пользователь отдельно по [README](../plugin/README.md#установка-ai-assistant): сохранить проект, закрыть STE, backup вне plugins и заменить **целую** папку AIAssistant, затем перезапустить. Не смешивайте эту версию с frozen296 или заменой одного файла. Здесь нет команд установки; старые [TESTING_STEP_BY_STEP.md](TESTING_STEP_BY_STEP.md), helper и frozen296 описывают другой срез и остаются неизменными.

В STE выберите **Stereotech Hybrid 530 V5.2.4**, для первого теста **classic/3D**. Нужна сохранённая тестовая сцена без активного импорта/нарезки/экспорта; автоматическую нарезку выключите в UI. Во время проверки не меняйте машину/профиль/материал вручную. Физическую печать не запускайте. Откройте AI Assistant → Open chat; после каждого рестарта берите **новый текущий порт**. Для MCP ниже провайдер/ключ не нужны; для учебных чатов пользователь отдельно настраивает URL/модель/ключ через LLM Settings. Ключи/лицензии в ответы и скриншоты не включайте.

## 2. Соединение и чтение машины

В новом PowerShell выполните определения один раз. Функции сами ничего не запускают и не повторяют запросы; при ошибке прекращают текущий блок.

```powershell
$acceptPort = [int](Read-Host 'Текущий порт из адреса Open chat')
$acceptBase = 'http://127.0.0.1:{0}' -f $acceptPort
function Invoke-AcceptRpc($method, $params) {
    $body = @{jsonrpc='2.0'; id=1; method=$method; params=$params} | ConvertTo-Json -Depth 30 -Compress
    $rpc = Invoke-RestMethod -Uri ($acceptBase + '/mcp') -Method Post -ContentType 'application/json' -TimeoutSec 15 -ErrorAction Stop -Body ([Text.Encoding]::UTF8.GetBytes($body))
    if ($null -ne $rpc.error) { throw ($rpc.error | ConvertTo-Json -Depth 20 -Compress) }
    $rpc
}
function Invoke-AcceptTool($name, $arguments) {
    $rpc = Invoke-AcceptRpc 'tools/call' @{name=$name; arguments=$arguments}
    if ($null -eq $rpc.result.content) { throw 'Не получен MCP-результат' }
    $envelope = $rpc.result.content[0].text | ConvertFrom-Json
    if ($rpc.result.isError -or -not $envelope.ok) { throw ($envelope | ConvertTo-Json -Depth 25 -Compress) }
    $envelope.data
}
Invoke-AcceptRpc 'initialize' @{} | ConvertTo-Json -Depth 15
$acceptTools = (Invoke-AcceptRpc 'tools/list' @{}).result.tools
$acceptTools | Select-Object name
$acceptKeys = @($acceptTools | Where-Object name -eq 'get_setting')[0].inputSchema.properties.key.enum
'Ключей в схеме: {0}' -f @($acceptKeys).Count
$acceptStatus = Invoke-RestMethod -Uri ($acceptBase + '/api/status') -TimeoutSec 15 -ErrorAction Stop
$acceptStatus | ConvertTo-Json -Depth 20
$acceptState = Invoke-AcceptTool 'get_slicer_state' @{}
$acceptState | ConvertTo-Json -Depth 20
if ($null -eq $acceptState.machine -or $acceptState.printing_mode -ne 'classic' -or -not $acceptState.backend.matches_mode) { throw 'Сначала выберите нужную машину/classic и проверьте backend в UI' }
```

**Ожидаем:** в схеме120 ключей, status.mode=plugin, capabilities.saved_dialogs=true; get_slicer_state.connected=true, нужные machine.name/definition_id и application_version, printing_mode=classic, backend.id=CuraEngineBackend и matches_mode=true. Сверьте с UI. Это ещё не проверка engine/лицензии. Dev/slicer_not_connected, отсутствующая машина, другое число ключей/нет saved_dialogs или отказ соединения — повод остановиться и уточнить копию/порт. Backend API не перечисляет extruder_id; его нельзя «взять из get_slicer_state».

## 3. Адресные параметры из каталога120 — только чтение

```powershell
$acceptPage = Invoke-AcceptTool 'describe_settings' @{scope='global'}
$acceptPage | Select-Object scope,total,offset,limit,returned,next_offset,truncated
Invoke-AcceptTool 'describe_settings' @{scope='global'; keys=@('layer_height','speed_wall')} | ConvertTo-Json -Depth 25
$acceptSearch = Invoke-AcceptTool 'describe_settings' @{scope='global'; category='support'; query='pattern'; limit=1}
$acceptSearch | ConvertTo-Json -Depth 25
if ($null -ne $acceptSearch.next_offset) {
    Invoke-AcceptTool 'describe_settings' @{scope='global'; category='support'; query='pattern'; offset=$acceptSearch.next_offset; limit=1} | ConvertTo-Json -Depth 25
}
Invoke-AcceptTool 'describe_settings' @{scope='global'; query=''} | ConvertTo-Json -Depth 20
```

**Ожидаем:** первый запрос total120/offset0/limit20/returned20/next_offset20/truncated=true. Keys возвращает только два указанных ключа в порядке каталога. Support+pattern: total2, первая страница support_pattern, следующая support_interface_pattern; последняя next_offset=null/truncated=false. Пустая query даёт settings=[]/total0; отсутствие query не фильтрует. Все страницы автоматически не выгружаются.

Метаданные содержат label/category/description, text_source, actual value/type/unit/options/enabled/settable/read_only/bounds/limitations. Description до500 символов с отдельным флагом усечения; неизвестное значение может быть null. Наличие в каталоге **не обещает запись**: недоступный/disabled/readOnly параметр — корректное ограничение, не основание включать зависимость или подставлять default. Query ищет подстроку по key/исходному английскому label без регистра. Keys1–20 уникальных, query<=80, limit1–30, offset целое>=0; типы/extra/неизвестные ключи дают -32602 до API. Категории и полный список — [контракт](API_CONTRACT.md#настройки-тз-0713--реализованы-runtime-приёмка-открыта) и [каталог](tasks/TASK_13_SETTINGS_CATALOG.md).

Для отдельной проверки экструдера пользователь берёт строковую позицию из **проверенной конфигурации своей машины**. В исходном определении main Hybrid530 это `"0"`, second — `"1"`, но эти примеры не подтверждают текущий runtime-стек; это не номер UI-кнопки и не список из диагностики. Сначала подтвердите нужную известную позицию **чтением**, не перебирайте неизвестные ID для записи:

```powershell
$acceptExtruderId = Read-Host 'Известная строковая позиция экструдера из проверенной конфигурации машины'
$acceptExt = Invoke-AcceptTool 'describe_settings' @{scope='extruder'; extruder_id=$acceptExtruderId; keys=@('material_print_temperature_layer_0')}
$acceptExt | ConvertTo-Json -Depth 25
```

Успешный ответ с этим extruder_id подтверждает наличие позиции в текущем стеке; возможность записи всё равно проверяется отдельно. `extruder_not_found` — остановка этого пункта; другой ID не угадывать. UI-экструдер инструмент не переключает. Для global поле extruder_id **отсутствует**, даже null запрещён; для extruder обязательна строка. Само чтение не нагревает принтер.

## 4. Одна запись → readback/UI → явное восстановление

Основной тест — global layer_height. Если вместо него проверяете уже подтверждённый extruder, замените только строку target на закомментированный вариант. Выполните **один выбранный вариант**, не оба; тип в этом сценарии float. Сначала запишите исходное значение и метаданные:

```powershell
$acceptTarget = @{key='layer_height'; scope='global'}
# Альтернатива только после успешного пункта extruder выше:
# $acceptTarget = @{key='material_print_temperature_layer_0'; scope='extruder'; extruder_id=$acceptExtruderId}
$acceptDescribe = @{scope=$acceptTarget.scope; keys=@($acceptTarget.key)}
if ($acceptTarget.ContainsKey('extruder_id')) { $acceptDescribe.extruder_id = $acceptTarget.extruder_id }
$acceptMeta = @( (Invoke-AcceptTool 'describe_settings' $acceptDescribe).settings )[0]
$acceptMeta | ConvertTo-Json -Depth 25
if ($null -eq $acceptMeta -or $acceptMeta.type -ne 'float' -or -not $acceptMeta.available -or -not $acceptMeta.enabled -or -not $acceptMeta.settable -or $acceptMeta.read_only -ne $false) { throw 'Запись не подтверждена метаданными; остановитесь, зависимости не включаются автоматически' }
$acceptBefore = Invoke-AcceptTool 'get_setting' $acceptTarget
$acceptBefore | ConvertTo-Json -Depth 20
$acceptNewValue = [double]::Parse((Read-Host 'Другое допустимое число в показанной unit, с точкой; для global слоя можно0.12, если подходит профилю'), [Globalization.CultureInfo]::InvariantCulture)
if ([double]::IsNaN($acceptNewValue) -or [double]::IsInfinity($acceptNewValue) -or $acceptNewValue -eq $acceptBefore.value) { throw 'Нужно другое конечное допустимое число' }
$acceptWriteArgs = @{} + $acceptTarget
$acceptWriteArgs.value = $acceptNewValue
$acceptWriteConfirmed = $false
```

Сохраните показанное before вне окна/сделайте скриншот. Сверьте unit/hard/warning bounds и допустимость нового значения в UI. Для layer_height нужен >0;0.12 не является универсально допустимым значением. Затем **один вызов записи**:

```powershell
$acceptWritten = Invoke-AcceptTool 'set_setting' $acceptWriteArgs
$acceptWritten | ConvertTo-Json -Depth 25
$acceptReadback = Invoke-AcceptTool 'get_setting' $acceptTarget
$acceptReadback | ConvertTo-Json -Depth 20
if ($acceptWritten.after -ne $acceptNewValue -or $acceptReadback.value -ne $acceptNewValue) { throw 'Значение не подтверждено: остановитесь и сверьте UI без повторной записи' }
$acceptWriteConfirmed = $true
```

**Ожидаем:** before соответствует исходному, after/readback — запрошенному, нужное поле UI совпадает. При extruder проверьте именно выбранный стек и исходную UI-позицию; температура — настройка проекта, не команда нагрева. Успех только текста LLM не подходит. При любой ошибке/сетевом или Qt-timeout не выполняйте блок восстановления автоматически: исход мог быть неизвестным. Сначала **только чтение** `Invoke-AcceptTool 'get_setting' $acceptTarget` и UI, затем передайте ошибку ведущему.

После подтверждённого изменения и сверки UI явно верните сохранённое значение:

```powershell
if (-not $acceptWriteConfirmed) { throw 'Сначала выясните исход записи; автоматического отката нет' }
$acceptRestoreArgs = @{} + $acceptTarget
$acceptRestoreArgs.value = $acceptBefore.value
Invoke-AcceptTool 'set_setting' $acceptRestoreArgs | ConvertTo-Json -Depth 25
$acceptRestored = Invoke-AcceptTool 'get_setting' $acceptTarget
$acceptRestored | ConvertTo-Json -Depth 20
if ($acceptRestored.value -ne $acceptBefore.value) { throw 'Исходное значение не подтверждено; дальнейшие записи остановлены' }
```

Сверьте восстановление в UI. Возврат числа не удаляет созданный override и не возвращает старой нарезке valid; для сброса override нужен штатный UI, для актуального результата — новая нарезка. При исходной0.12 выберите другое допустимое число, иначе no-op не проверяет изменение.

## 5. Два учебных чата, сохранение и обычный рестарт

Для чата нужен уже отдельно настроенный пользователем провайдер. Наличие «Ключ сохранён» не подтверждает связь. Используйте два разных учебных факта; запросы ниже объяснительные и не требуют изменения проекта.

Для сверки с закрытыми техническими деталями: GET `/api/sessions` возвращает `{ready,persistent,error,sessions:[метаданные]}`; GET `/api/sessions/{session_id}` — messages/tool_events и title/revision/restored/history_truncated/process_id/persistence. `persistence:{state:"saved",saved_revision:N,error:null}` подтверждает записанную revision, saving/error/temporary — нет. POST создания принимает `{}`, POST сообщения — только `{text,client_message_id,attachment_ids?}`. DELETE принимает только путь с выбранным ID, без тела/query; `{session_id,deleted:true}` — подтверждённое удаление. UI запрашивает подтверждение удаления, нажатие кнопки само не доказывает ответ200. GET остаются доступны при busy; active-turn/своя наблюдаемая операция блокируют DELETE с409. Эти формы — справка, не команды повторного POST/DELETE.

| Шаг пользователя | Точный ожидаемый исход / отказ |
| --- | --- |
| Новый диалог A: «Учебный факт: деталь называется Альфа, материал PETG. Пока только объясни, что такое заполнение; ничего не меняй». | Ответ простыми словами; без изменяющих tools. Дождаться idle **и «Сохранено на этом компьютере»**. Название из первых слов, без отдельного LLM-запроса. Saving/error/temporary успехом сохранения не считаются. |
| Новый диалог B: «Учебный факт: деталь называется Бета, материал PLA. Только объясни высоту слоя; ничего не меняй». | Отдельная история; снова дождаться saved. При active/pending/наблюдаемой своей операции UI блокирует новые действия — не обходить блокировку. |
| Раскрыть «Диалоги», открыть A, затем B. Проверить Tab/Enter и узкое окно. | Текущий выделен, переписки не смешаны, выбор сам не вызывает LLM/инструменты, сцена не меняется. Длинный текст переносится, технические details закрыты. |
| Сохранить проект и обычным способом закрыть STE, затем открыть STE/Open chat на новом текущем порту. | В списке оба разговора; исторические сообщения помечены. Старый порт/вкладка сами не подтверждают работу новой копии. Не завершать процесс принудительно для основного теста. |
| В A: «Как называются деталь и материал, которые мы обсуждали именно здесь? Ничего не меняй». Затем такой же вопрос в B. | A вспоминает Альфа/PETG, B — Бета/PLA, из собственного ограниченного текста. Чужие факты не подмешиваются; история не равна восстановлению сцены/точного tool-протокола. Не угадывать факт при его отсутствии. |
| В отдельном тестовом диалоге приложить JPG/PNG через picker, отправить объяснительный запрос «Вложение только для проверки истории, не импортируй/не меняй проект», дождаться saved и повторить обычный рестарт. | Имя прежнего вложения видно как недоступное/expired, не обещается доступ к байтам; для нового анализа файл загружается заново. Старые model/slice/export/artifact/file/download IDs не становятся действующими. Временный upload не равен импорту модели. |
| Если в истории уже есть реально полученный экспорт, после рестарта проверить его карточку/итог. | Нет действующей старой download-ссылки/ownership; это исторический текст. Не запускайте новый экспорт ради этого пункта без отдельного native-сценария. Если такого факта нет — отметить «не проверено». |
| Выбрать B, нажать «Удалить», подтвердить. Ещё раз обычный restart/Open chat. | B отсутствует и не воскресает, A остаётся. Удаление истории не отменяет engine и не удаляет файлы проекта/G-code. При неподтверждённом DELETE сохранить ошибку/перечитать список, не повторять автоматически. |

Перед рестартом убедитесь, что запросы и свои native-операции закончены. Если встречается прежний pending/прерванный ход, ожидается заметка об истечении/прерывании и **никакого автоматического повторного POST/import/start/export**. Не провоцируйте аварийное закрытие рабочего проекта ради теста. Последняя неподтверждённая revision при аварии может потеряться; saved подтверждает запись, а не запуск engine. Лимиты архива:20 чатов,100 сообщений/кратких итогов,512КиБ на чат/10МиБ всего; history_truncated явно ограничивает доступный контекст, LLM получает максимум12 блоков выбранного разговора.

Полезные короткие промпты, выполнять отдельно:

- **Теория:** «Почему скорость стенок влияет на качество? Только объясни, ничего не меняй».
- **Чтение:** «Прочитай global layer_height через get_setting и сообщи фактическое значение. Не меняй его».
- **Адресный поиск:** «Покажи описания support_z_distance и support_xy_distance через keys. Только чтение, без включения поддержек».
- **Операция — только отдельный осознанный тест:** «Установи global layer_height0.12мм, только если метаданные разрешают; подтверди get_setting. При ошибке остановись, не повторяй». Сначала отдельно сохраните before, после подтверждения восстановите его отдельным запросом; при другой выбранной области этот промпт не используйте. После восстановления чата перед новым изменением нужны свежие get_slicer_state/list_models, история их не заменяет.

## 6. Оставшаяся native-приёмка и протокол

Далее — отдельная последовательность [NATIVE_ACCEPTANCE.md](NATIVE_ACCEPTANCE.md#роль1-очередность-проверок) и подробные разделы [plugin/README.md](../plugin/README.md): реальные профили/материалы, STL/OBJ/3MF и преобразования/Undo/arrange, custom-profile с рестартом, **classic/cylindrical/cylindrical_full**, настоящий engine/статус/время/материал, штатные writers, G-code Preview и повторное открытие workspace3MF. Исторические SHA/baseline в старых инструкциях не подменяют источник84c1e02, выбранный здесь. Возврат start/idle LLM/progress1 не означает completed; собственные формулы/G-code не используются. Не копируйте эту большую последовательность в учебный тест памяти и не запускайте физическую печать.

Для каждого пункта запишите версию/порт, исходные данные, реальные before/after/ID текущего процесса, ожидаемый и фактический исход, безопасный фрагмент ошибки и скриншот без ключа/активации. Отказ с явной причиной — корректно обработанный отказ, **не успешное выполнение функции**. Крупные логи/приватные вложения в Git не добавлять.

| Пункт | Не проверено | Результат пользователя | Безопасная ошибка / причина остановки |
| --- | --- | --- | --- |
| Источник84c1e02 / целая установленная копия / рестарт | Да | — | — |
| Текущий порт / машина / backend /120 ключей | Да | — | — |
| Keys/category/query/страница/пустая query | Да | — | — |
| Одна запись / readback / UI / восстановление | Да | — | — |
| Явная extruder-позиция и область | Да | — | — |
| Два чата / saved / выбор / restart / отдельный контекст | Да | — | — |
| Expired-вложение / старые ID/ссылки / отсутствие повторов | Да | — | — |
| Удаление B / повторный restart / сохранность A | Да | — | — |
| Профили/сцена/custom/три режима/engine/writers/Preview/reopen | Да | — | — |

Эта таблица — новый протокол именно текущей версии. Ранее подтверждённые01/02 и отдельные пользовательские чтения/импорт остаются в своих отчётах; они не заполняют новые результаты автоматически. ТЗ03/04 и сквозные native-файлы этим документом не закрываются.
