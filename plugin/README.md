# Установка AI Assistant

1. Установите дистрибутив STE Slicer, выданный организаторами.
2. Откройте **Справка -> Показать папку конфигурации**.
3. Сохраните открытый проект, закройте слайсер и скопируйте каталог `AIAssistant` целиком в `plugins` этой папки. При обновлении сохраните резервную копию прежнего плагина и не удаляйте остальные файлы конфигурации. Локально найдена папка `%APPDATA%\steslicer\7.10\plugins`; путь на другой машине уточните через приложение.
4. Перезапустите слайсер. Откройте пункт **AI Assistant -> Open chat** в меню расширений.
5. Чат откроется в системном браузере. В текущем каркасе ответ демонстрационный, без вызова LLM.

Python-код должен оставаться совместимым с 3.5. PyQt5 и `UM` предоставляет сам слайсер. Не устанавливайте современный MCP SDK внутрь этого окружения.

## Реализовано в ТЗ 01

Через существующий `/mcp` доступны `assistant_status`, `get_setting` и `set_setting`. Настройки ограничены обязательными `key=layer_height`, `scope=global`; значение в мм. Остальные настройки, модели, слайсинг и экспорт пока не реализованы. Диагностика/режимы добавлены в ТЗ 02, списки и выбор встроенного качества/материалов — в ТЗ 03, см. ниже. Вызовы настроек из HTTP выполняются через `app.callLater`, ожидание результата ограничено 5 секундами. Запись возвращает фактически прочитанные `before` и `after`.

Аргументы проверяются до обращения к API. Жёсткие границы берутся из текущего стека; предупреждения профиля не запрещают запись. Если запись не подтверждена чтением, возвращается ошибка. MCP помещает результат `{ok,data}` или `{ok,error}` в JSON-текст `content` и выставляет `isError` при операционной ошибке. Неверные аргументы — JSON-RPC `-32602`.

`python scripts/dev_server.py` работает без Qt/UM. Список инструментов тот же, но настройки возвращают `slicer_not_connected`; успех не имитируется. `assistant_status` сообщает о частичной реализации и отсутствии LLM, не проверяет активную машину.

## Ручная проверка ТЗ 01

**Приёмка ТЗ 01 пройдена** в STE Slicer 7.10.12, Stereotech Hybrid 530 V5.2.4, 3D: чтение 0.1 мм, запись 0.12 мм и отображение в UI, восстановление 0.1 мм с проверкой API/UI, отклонение неверных запросов. 21 автоматическая проверка также прошла. Протокол в репозитории: `docs/reports/TASK_01_RUNTIME.md`. Ниже сохранены шаги воспроизведения на другой установке.

После установки выберите **Stereotech Hybrid 530 V5.2.4**, режим 3D, пустую сцену. Откройте **AI Assistant -> Open chat** и возьмите порт из адресной строки открывшегося браузера. Команды PowerShell выполняйте последовательно; версия приложения и значения для протокола приёмки записываются пользователем.

```powershell
$port = [int](Read-Host 'Порт из адреса открытого чата')
$mcpUrl = 'http://127.0.0.1:{0}/mcp' -f $port
function Invoke-Mcp($method, $params) {
    $request = @{jsonrpc='2.0'; id=1; method=$method; params=$params}
    Invoke-RestMethod -Uri $mcpUrl -Method Post -ContentType 'application/json' -TimeoutSec 15 -Body ($request | ConvertTo-Json -Depth 10 -Compress)
}
function Invoke-Setting($name, $arguments) {
    $response = Invoke-Mcp 'tools/call' @{name=$name; arguments=$arguments}
    if ($null -ne $response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $result = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $result.ok) { throw ($result | ConvertTo-Json -Depth 10 -Compress) }
    $result.data
}

Invoke-Mcp 'initialize' @{} | ConvertTo-Json -Depth 10
Invoke-Mcp 'tools/list' @{} | ConvertTo-Json -Depth 10
$before = Invoke-Setting 'get_setting' @{key='layer_height'; scope='global'}
$original = $before.value
$before | ConvertTo-Json
```

Сверьте исходное значение с UI. Выберите **другое** допустимое значение с учётом сопла/профиля (например, 0.24 мм, только если подходит текущему профилю). Ввод — с точкой. После успешной записи проверьте `before`, `after`, повторное чтение и UI:

```powershell
$newValue = [double]::Parse((Read-Host 'Новое допустимое значение в мм'), [Globalization.CultureInfo]::InvariantCulture)
Invoke-Setting 'set_setting' @{key='layer_height'; scope='global'; value=$newValue} | ConvertTo-Json
Invoke-Setting 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json
```

После сверки с UI восстановите сохранённое значение и подтвердите чтением и UI:

```powershell
Invoke-Setting 'set_setting' @{key='layer_height'; scope='global'; value=$original} | ConvertTo-Json
Invoke-Setting 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json
Invoke-Mcp 'tools/call' @{name='set_setting'; arguments=@{key='layer_height'; scope='global'; value=-1}} | ConvertTo-Json -Depth 10
Invoke-Mcp 'tools/call' @{name='set_setting'; arguments=@{key='layer_height'; scope='extruder'; value=0.24}} | ConvertTo-Json -Depth 10
Invoke-Setting 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json
```

Последние два вызова должны вернуть `error.code=-32602`, настройка должна оставаться исходной. При `qt_timeout` до старта callback отменён. Если операция уже началась, состояние неизвестно: сначала перечитайте настройку и проверьте UI; автоматически повторять запись нельзя. При ошибке API запись также могла начаться. Восстановление считается выполненным только после успешного ответа и сверки get/UI. Сохраните ответы и скриншоты изменённого и восстановленного значения для приёмки.

## ТЗ 02: состояние машины и режимы

Добавлены MCP-инструменты `get_slicer_state` с аргументами `{}` и `set_print_mode` с `{"mode":"classic"}`. Допустимы только `classic`, `cylindrical`, `cylindrical_full`; лишние поля отклоняются. Старый `set_setting` по-прежнему ограничен `layer_height`. В dev-режиме оба новых инструмента возвращают `slicer_not_connected`.

Диагностика возвращает версию приложения, ID/имя/definition ID машины, фактическую строку режима, текущий и ожидаемый ID backend. `matches_mode` проверяет совпадение объектов из runtime-менеджера. Неизвестный текущий режим сохраняется в диагностике, но не допускается для установки. Без машины диагностика успешна: `machine`, `printing_mode`, `backend` равны null, `available_modes=[]`. Доступные режимы фильтруются по enum и `machine_five_axis`; `backend_available` означает только наличие регистрации, не готовность движка/лицензии/G-code.

Переключение сначала проверяет поддержку и backend, затем пишет `userChanges` и отдельными Qt-вызовами ждёт подтверждения настройки и backend. Общий бюджет — 5 секунд, ожидание выполняется в HTTP worker. Второй параллельный вызов получает `switch_in_progress`; прямой вызов из Qt-потока запрещён до записи. Уже согласованный режим возвращается без записи и перезапуска. При совпадающей строке и неправильном backend ложного успеха нет. Автоматического отката/повтора записи нет; после ошибки требуется `get_slicer_state`.

**ТЗ 02 принято в STE Slicer 7.10.12, Hybrid 530 V5.2.4.** Диагностика, переходы 3D -> 5D Spiral -> 5D -> 3D, backend CuraEngineBackend/CLIParserBackend/CylindricalBackend, UI, восстановление и отрицательные запросы проверены; всего 45 автоматических проверок. Протокол: `docs/reports/TASK_02_RUNTIME.md`. Флаг `runtime_verified` в assistant_status остаётся false: он не является реестром приёмки конкретной сборки. Ниже — команды воспроизведения.

Ограничение: исследованный общий API Backend предоставляет сигнал состояния, но не публичный getter актуального состояния слайсинга. Защита от переключения во время нарезки не заявляется. Проверять ТЗ 02 только на **пустой сцене**, без запущенной нарезки; профиль качества и материал не менять.

### Команды ручной приёмки ТЗ 02

После согласованного обновления плагина сохраните проект, перезапустите STE Slicer, выберите Hybrid 530 V5.2.4 и пустую сцену. Откройте чат, возьмите текущий порт. Выполните этот блок в PowerShell:

```powershell
$port = [int](Read-Host 'Порт из адреса открытого чата')
$modeMcpUrl = 'http://127.0.0.1:{0}/mcp' -f $port
function Invoke-ModeMcp($method, $params) {
    $request = @{jsonrpc='2.0'; id=2; method=$method; params=$params}
    Invoke-RestMethod -Uri $modeMcpUrl -Method Post -ContentType 'application/json' -TimeoutSec 15 -Body ($request | ConvertTo-Json -Depth 10 -Compress)
}
function Invoke-SlicerTool($name, $arguments) {
    $response = Invoke-ModeMcp 'tools/call' @{name=$name; arguments=$arguments}
    if ($null -ne $response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $result = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $result.ok) { throw ($result | ConvertTo-Json -Depth 10 -Compress) }
    $result.data
}
Invoke-ModeMcp 'initialize' @{} | ConvertTo-Json -Depth 10
Invoke-ModeMcp 'tools/list' @{} | ConvertTo-Json -Depth 10
$initialState = Invoke-SlicerTool 'get_slicer_state' @{}
$initialState | ConvertTo-Json -Depth 10
$originalMode = $initialState.printing_mode
if ($null -eq $initialState.machine) { throw 'Сначала выберите активную машину' }
if ($originalMode -notin @('classic','cylindrical','cylindrical_full')) { throw 'Исходный режим вне ТЗ; согласуйте восстановление с ведущим' }
```

Сохраните диагностику и скриншот исходного режима. **Каждую следующую строку выполняйте отдельно**, сверяя ответ, повторное состояние и UI перед следующим переключением:

```powershell
Invoke-SlicerTool 'set_print_mode' @{mode='classic'} | ConvertTo-Json -Depth 10
Invoke-SlicerTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 10
Invoke-SlicerTool 'set_print_mode' @{mode='cylindrical'} | ConvertTo-Json -Depth 10
Invoke-SlicerTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 10
Invoke-SlicerTool 'set_print_mode' @{mode='cylindrical_full'} | ConvertTo-Json -Depth 10
Invoke-SlicerTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 10
Invoke-SlicerTool 'set_print_mode' @{mode=$originalMode} | ConvertTo-Json -Depth 10
Invoke-SlicerTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 10
```

Для каждого шага зафиксируйте реальный `before`, `after`, `backend.id`, `expected_id`, `matches_mode=true`, строку режима в повторной диагностике и UI (3D / 5D Spiral / 5D). Восстановление исходного режима подтвердите API и UI. Наличие backend и переключение не доказывают работоспособность движка. При ошибке остановите последовательность, вызовите `get_slicer_state`, сохраните ответ/сообщение приложения для ведущего; запись автоматически не повторять.

Неверный/alpha-режим и лишнее поле должны дать `-32602`, не изменив состояние:

```powershell
Invoke-ModeMcp 'tools/call' @{name='set_print_mode'; arguments=@{mode='spherical'}} | ConvertTo-Json -Depth 10
Invoke-ModeMcp 'tools/call' @{name='set_print_mode'; arguments=@{mode='classic'; extra=1}} | ConvertTo-Json -Depth 10
Invoke-SlicerTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 10
```

## ТЗ 03: встроенное качество и материалы

Добавлены `list_quality_profiles {}`, `list_materials {extruder_id}`, `select_profile {quality_type}` и `select_material {extruder_id,root_material_id}`. Все ID — непустые строки из runtime-списков, лишние поля запрещены. Позиция `"0"` означает первый экструдер, а не подпись кнопки `1` в UI. Активный UI-экструдер ради чтения/выбора не меняется. Custom-профили не создаются и не выбираются.

Качество перечисляется целыми QualityGroup с честным `available`, не ID отдельных контейнеров. `layer_height_mm` берётся из базового профиля/definition без пользовательского override; если это выражение или значение нельзя надёжно вычислить, возвращается null. Материалы фильтруются штатным MaterialManager по машине, позиции, соплу/платформе/диаметру; removed-пакеты исключены. Root ID и resolved material ID различаются и возвращаются отдельно. `compatible=null` означает отсутствие подтверждённых метаданных; явно несовместимый материал выбрать нельзя.

Выбор подтверждается чтением фактических контейнеров, с проверкой смены машины/экструдеров/lookup и сохранности overrides. Ожидание только в worker, общий бюджет 5 секунд. Режим/качество/материал используют одну неблокирующую блокировку. Подтверждённый no-op не вызывает setter/диалог. При ошибке после начала записи перечитайте списки и `get_slicer_state`; автоматического отката и повтора нет.

Выбор качества использует публичный `setQualityGroup(group,no_dialog=True)` и сохраняет userChanges. Custom-профиль блокирует выбор встроенного профиля (`custom_profile_active`), даже если underlying quality_type тот же. Реальное изменение материала также блокируется при custom: штатный API может заменить доступную группу качества и сбросить custom. No-op материала разрешён.

При overrides реальное изменение материала разрешено только при штатной политике `always_keep`; иначе возвращается `user_changes_present` **до setter**. Проверка консервативна и не опирается на UI active_mode. Плагин не меняет Preferences, не очищает userChanges и не отвечает на Keep/Discard. Пользователь сам решает судьбу изменений в приложении. Если overrides сохранены, отображаемый layer_height может остаться прежним после смены базового качества — это допустимо. Материал может изменить качество; после него нужно перечитать список профилей, сохранение всех зависимых температур/скоростей не обещается.

**ТЗ 03 проверено автоматически с подменой приложения и через локальный HTTP; ручной прогон ещё не проведён.** Установленная копия не обновлялась. Dev-сервер не имитирует успех: новые инструменты отвечают `slicer_not_connected`. Предыдущая приёмка ТЗ 01/02 не подтверждает новый код, `runtime_verified` остаётся false.

### Команды ручной приёмки ТЗ 03

После ревью и отдельно согласованного обновления плагина: сохраните проект, выберите Hybrid 530 V5.2.4 и пустую сцену без активных операций. Зафиксируйте UI-экструдер и пользовательские изменения (скриншоты изменённых настроек); приведённое чтение layer_height фиксирует только одну из них. Не сбрасывайте overrides и не меняйте настройки приложения автоматически ради теста.

Команды PowerShell из корня репозитория, порт — из адреса открытого чата:

```powershell
$port = [int](Read-Host 'Порт открытого чата')
$profilesMcpUrl = 'http://127.0.0.1:{0}/mcp' -f $port
function Invoke-ProfileRpc($method, $params) {
    $body = @{jsonrpc='2.0'; id=3; method=$method; params=$params} | ConvertTo-Json -Depth 15 -Compress
    Invoke-RestMethod -Uri $profilesMcpUrl -Method Post -ContentType 'application/json' -TimeoutSec 15 -Body $body
}
function Invoke-ProfileTool($name, $arguments) {
    $response = Invoke-ProfileRpc 'tools/call' @{name=$name; arguments=$arguments}
    if ($null -ne $response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $result = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $result.ok) { throw ($result | ConvertTo-Json -Depth 15 -Compress) }
    $result.data
}
Invoke-ProfileRpc 'initialize' @{} | ConvertTo-Json -Depth 15
Invoke-ProfileRpc 'tools/list' @{} | ConvertTo-Json -Depth 15
$stateBefore = Invoke-ProfileTool 'get_slicer_state' @{}
$qualityBefore = Invoke-ProfileTool 'list_quality_profiles' @{}
$material0Before = Invoke-ProfileTool 'list_materials' @{extruder_id='0'}
$material1Before = Invoke-ProfileTool 'list_materials' @{extruder_id='1'}
$heightBefore = Invoke-ProfileTool 'get_setting' @{key='layer_height'; scope='global'}
$baseline = @{state=$stateBefore; quality=$qualityBefore; material0=$material0Before; material1=$material1Before; height=$heightBefore}
$baseline | ConvertTo-Json -Depth 20
New-Item -ItemType Directory -Path 'tmp' -Force | Out-Null
$baseline | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath 'tmp/task03_before.json' -Encoding UTF8
```

Если исходный профиль custom, материал отсутствует или другой доступный вариант отсутствует, остановитесь и сообщите ведущему фактическое ограничение. Сначала нужны исходные значения, которые можно восстановить. Не устанавливайте пакеты или моки ради второго варианта.

Каждую строку ниже выполняйте отдельно, сверяя ответ и UI. Вводите **другой доступный ID из свежего списка**, а не название или пример из документа:

```powershell
$qualityType = Read-Host 'Другой доступный quality_type из list_quality_profiles'
Invoke-ProfileTool 'select_profile' @{quality_type=$qualityType} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'list_quality_profiles' @{} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'list_materials' @{extruder_id='0'} | ConvertTo-Json -Depth 15
$rootId = Read-Host 'Другой доступный root_material_id для позиции 0'
Invoke-ProfileTool 'select_material' @{extruder_id='0'; root_material_id=$rootId} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'list_materials' @{extruder_id='0'} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'list_quality_profiles' @{} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'list_materials' @{extruder_id='1'} | ConvertTo-Json -Depth 15
```

Зафиксируйте before/after, root/resolved ID, `quality_type_after`, `has_user_settings`, сохранность overrides, материала позиции 1 и активного UI-экструдера. При `user_changes_present` изменение не начато: объясните причину пользователю, дальнейшую политику он выбирает сам. После ошибки остановите последовательность и прочитайте списки/состояние; повторять setter автоматически нельзя.

Восстановление после успешного теста — **сначала материал затронутой позиции 0, затем исходная группа качества**; позицию 1 этот сценарий не меняет:

```powershell
Invoke-ProfileTool 'select_material' @{extruder_id='0'; root_material_id=$material0Before.current.root_material_id} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'list_materials' @{extruder_id='0'} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'select_profile' @{quality_type=$qualityBefore.current.quality_type} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'list_quality_profiles' @{} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'list_materials' @{extruder_id='1'} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json -Depth 15
Invoke-ProfileTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 15
```

Сверьте root **и** resolved ID материалов, группу качества, overrides, режим и UI с сохранённым baseline. Сохраните ответы и скриншоты. Успешный ответ без последующего подтверждения не считать восстановлением. Если меняли оба экструдера, сначала восстановите материалы обоих и только затем качество. Для отрицательной проверки можно отправить неверный тип (ожидается `-32602`) и отсутствующий ID (операционный отказ без записи), затем снова перечитать списки:

```powershell
Invoke-ProfileRpc 'tools/call' @{name='list_materials'; arguments=@{extruder_id=0}} | ConvertTo-Json -Depth 15
Invoke-ProfileRpc 'tools/call' @{name='select_profile'; arguments=@{quality_type='__absent_id_for_task03_check__'}} | ConvertTo-Json -Depth 15
```

## Настройка LLM: предстоит реализовать

Добавить окно настроек плагина: URL OpenAI-совместимого API, имя модели и ключ. Хранить значения через Preferences приложения. Не отправлять ключ в SPA и не записывать в логи. Текущий каркас не принимает и не сохраняет ключ.

## Проверка

- Загрузка плагина и get/set layer_height проверены на сборке 7.10.12; другие сборки и остальные операции требуют своих проверок. Shutdown приложения отдельно не проверялся.
- После правок Python перезапускайте слайсер.
- Ошибки смотрите в `stderr.log` и логах папки конфигурации.
- В финальную сдачу включить этот README, весь каталог `AIAssistant` и результаты в соседнем `results`.
