# Установка AI Assistant

1. Установите дистрибутив STE Slicer, выданный организаторами.
2. Откройте **Справка -> Показать папку конфигурации**.
3. Сохраните открытый проект, закройте слайсер и скопируйте каталог `AIAssistant` целиком в `plugins` этой папки. При обновлении сохраните резервную копию прежнего плагина и не удаляйте остальные файлы конфигурации. Локально найдена папка `%APPDATA%\steslicer\7.10\plugins`; путь на другой машине уточните через приложение.
4. Перезапустите слайсер. Откройте пункт **AI Assistant -> Open chat** в меню расширений.
5. Задайте URL/модель/ключ в **AI Assistant -> LLM Settings**. Чат откроется в системном браузере и использует Chat Completions с реальными инструментами. Интеграция пока проверена только с подменами, установленная копия автоматически не обновляется.

Python-код должен оставаться совместимым с 3.5. PyQt5 и `UM` предоставляет сам слайсер. Не устанавливайте современный MCP SDK внутрь этого окружения.

## Реализовано в ТЗ 01

ТЗ 01 добавило через `/mcp` инструменты `assistant_status`, `get_setting` и `set_setting` для обязательных `key=layer_height`, `scope=global`, значения в мм. Эти вызовы и форма ответов сохранены. ТЗ 07 расширяет настройки на allowlist и явно выбранный экструдер, см. ниже; остальные настройки и преобразования моделей пока не реализованы. Диагностика/режимы добавлены в ТЗ 02, списки и выбор встроенного качества/материалов — в ТЗ 03, импорт/список моделей — в ТЗ 04, classic-нарезка — в ТЗ 05, штатный экспорт — в ТЗ 06. Вызовы настроек из HTTP выполняются через `app.callLater`, ожидание результата ограничено 5 секундами. Запись возвращает фактически прочитанные `before` и `after`.

Аргументы проверяются до обращения к API. Жёсткие границы берутся из текущего стека; предупреждения профиля не запрещают запись. Если запись не подтверждена чтением, возвращается ошибка. MCP помещает результат `{ok,data}` или `{ok,error}` в JSON-текст `content` и выставляет `isError` при операционной ошибке. Неверные аргументы — JSON-RPC `-32602`.

`python scripts/dev_server.py` работает без Qt/UM. Список MCP-инструментов тот же, но операции возвращают `slicer_not_connected`; успех не имитируется. Настроек Preferences/ключа в dev нет, отправка чата даёт 503 llm_not_configured, запуск сервера не вызывает провайдера. Legacy assistant_status и /api/health сохраняют прежний scaffold-ответ для совместимости; готовность нового чата показывает /api/status.

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

Добавлены MCP-инструменты `get_slicer_state` с аргументами `{}` и `set_print_mode` с `{"mode":"classic"}`. Допустимы только `classic`, `cylindrical`, `cylindrical_full`; лишние поля отклоняются. В ТЗ 02 setter настроек не расширялся; его новый allowlist описан в ТЗ 07 ниже. В dev-режиме оба новых инструмента возвращают `slicer_not_connected`.

Диагностика возвращает версию приложения, ID/имя/definition ID машины, фактическую строку режима, текущий и ожидаемый ID backend. `matches_mode` проверяет совпадение объектов из runtime-менеджера. Неизвестный текущий режим сохраняется в диагностике, но не допускается для установки. Без машины диагностика успешна: `machine`, `printing_mode`, `backend` равны null, `available_modes=[]`. Доступные режимы фильтруются по enum и `machine_five_axis`; `backend_available` означает только наличие регистрации, не готовность движка/лицензии/G-code.

Переключение сначала проверяет поддержку и backend, затем пишет `userChanges` и отдельными Qt-вызовами ждёт подтверждения настройки и backend. Общий бюджет — 5 секунд, ожидание выполняется в HTTP worker. Второй параллельный вызов получает `switch_in_progress`; прямой вызов из Qt-потока запрещён до записи. Уже согласованный режим возвращается без записи и перезапуска. При совпадающей строке и неправильном backend ложного успеха нет. Автоматического отката/повтора записи нет; после ошибки требуется `get_slicer_state`.

**ТЗ 02 принято в STE Slicer 7.10.12, Hybrid 530 V5.2.4.** Диагностика, переходы 3D -> 5D Spiral -> 5D -> 3D, backend CuraEngineBackend/CLIParserBackend/CylindricalBackend, UI, восстановление и отрицательные запросы проверены; всего 45 автоматических проверок. Протокол: `docs/reports/TASK_02_RUNTIME.md`. Флаг `runtime_verified` в assistant_status остаётся false: он не является реестром приёмки конкретной сборки. Ниже — команды воспроизведения.

Ограничение: исследованный общий API Backend предоставляет сигнал состояния, но не публичный getter актуального состояния слайсинга. ТЗ 05 добавляет общую защиту записей во время собственного classic-запуска; это не универсальная защита от ручных параллельных операций. Проверять ТЗ 02 только на **пустой сцене**, без запущенной нарезки; профиль качества и материал не менять.

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

## ТЗ 04: импорт моделей, ID и габариты

Добавлены `load_model {path}`, `list_models {}` и `get_model_bounds {object_id}`. path/object_id — непустые строки, лишние поля запрещены. load_model принимает только абсолютный путь к читаемому обычному локальному STL/OBJ/3MF, **непустому и не больше 50 MiB** (ограничение прототипа). URL, UNC/device/сетевые диски и другие типы отклоняются. Это диагностический MCP-ввод: HTTP-upload и file_id пока отсутствуют, не передавайте LLM произвольный доступ к локальным путям. Файлы проверяются в worker; новый код не возвращает/не логирует полный путь модели. Штатные логи reader могут содержать его.

Импорт запускает штатный readLocalFile с `skip_project_file_check=True`: 3MF импортируется как mesh, без workspace-восстановления проекта. Объектные overrides 3MF могут сохраниться. Штатное размещение и автомасштабирование не отключаются, Preferences не меняются. G-code preview блокирует импорт до запуска, чтобы избежать штатного deleteAll.

Успех требует своего fileLoaded, затем fileCompleted и фактических новых верхних моделей в исходной сцене/машине. Полный путь сопоставляется только внутри контекста. Один ранний сигнал или посторонний узел недостаточны. При обнаруженной смене машины/сцены, исчезновении старых узлов или пустом результате возвращается ошибка. Ручной параллельный импорт/очистка не поддерживаются, в особенности тот же путь без уникального job ID.

Ожидание в worker ограничено общими 10 секундами. После запуска job не отменяется при timeout: модели могут появиться позже. Вызовите list_models, не повторяйте импорт автоматически. Новый импорт и изменения режима/профиля/материала блокируются до собственного завершения или shutdown. Если fileCompleted не пришёл, требуется перезапуск. Ошибка API с неизвестным результатом запуска также сохраняет блокировку. Списки/диагностика доступны. После подтверждённого завершения или shutdown подписки снимаются на Qt-потоке. Прямой синхронный load_model из Qt-потока запрещён.

Список адресует верхние mesh-объекты и группы с печатной геометрией, не дублируя части групп. Служебные root/камера/платформа без печатных декораторов и preview исключаются. Непрозрачный object_id стабилен для живой адресуемой модели в сессии; одинаковые имена имеют разные ID. Удалённый ID недействителен и не переиспользуется. Геометрия берётся из мировых mesh-границ; для группы — объединение потомков без origin пустого родителя.

Ответы bounds используют **Uranium Y-up**: Y — высота, X/Z — плоскость стола, единица mm. Не переставляйте оси при сравнении с UI. Нулевая толщина по одной оси допустима; недоступные границы в списке — null, отдельный get_model_bounds возвращает ошибку. Полные результаты/коды описаны в `docs/API_CONTRACT.md`.

**Реализация проверена с fake reader/сценой и локальным HTTP; реальный импорт ТЗ 04 ещё не проверен.** Установленная копия не обновлялась, приложение не перезапускалось. Ручная приёмка ТЗ 03 также остаётся открыта. Наличие объектов/размеров не подтверждает слайсинг или G-code.

### Команды ручной приёмки ТЗ 04

Ведущий сначала координирует ревью и обновление установленной копии. Завершите приёмку ТЗ 03 либо явно зафиксируйте открытые пункты. Сохраните проект, выберите Hybrid 530 V5.2.4, classic, без нарезки и G-code preview. Для теста нужны небольшой известный STL, OBJ и 3MF; отсутствующий формат отметьте как непроверенный. Не добавляйте модели организаторов в Git.

В PowerShell из корня репозитория:

```powershell
$port = [int](Read-Host 'Порт из адреса открытого чата')
$modelsMcpUrl = 'http://127.0.0.1:{0}/mcp' -f $port
function Invoke-ModelRpc($method, $params) {
    $body = @{jsonrpc='2.0'; id=4; method=$method; params=$params} | ConvertTo-Json -Depth 20 -Compress
    Invoke-RestMethod -Uri $modelsMcpUrl -Method Post -ContentType 'application/json' -TimeoutSec 35 -Body $body
}
function Invoke-ModelTool($name, $arguments) {
    $response = Invoke-ModelRpc 'tools/call' @{name=$name; arguments=$arguments}
    if ($null -ne $response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $result = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $result.ok) { throw ($result | ConvertTo-Json -Depth 20 -Compress) }
    $result.data
}
Invoke-ModelRpc 'initialize' @{} | ConvertTo-Json -Depth 20
Invoke-ModelRpc 'tools/list' @{} | ConvertTo-Json -Depth 20
$modelsBefore = Invoke-ModelTool 'list_models' @{}
$stateBeforeModels = Invoke-ModelTool 'get_slicer_state' @{}
$qualityBeforeModels = Invoke-ModelTool 'list_quality_profiles' @{}
$material0BeforeModels = Invoke-ModelTool 'list_materials' @{extruder_id='0'}
$material1BeforeModels = Invoke-ModelTool 'list_materials' @{extruder_id='1'}
$modelBaseline = @{models=$modelsBefore; state=$stateBeforeModels; quality=$qualityBeforeModels; material0=$material0BeforeModels; material1=$material1BeforeModels}
$modelBaseline | ConvertTo-Json -Depth 25
New-Item -ItemType Directory -Path 'tmp' -Force | Out-Null
$modelBaseline | ConvertTo-Json -Depth 25 | Set-Content -LiteralPath 'tmp/task04_before.json' -Encoding UTF8
```

Следующие команды выполняйте последовательно, после каждого импорта проверяйте UI. Вводите абсолютный путь без окружающих кавычек. Сначала известный STL:

```powershell
$stlPath = Read-Host 'Абсолютный локальный путь к небольшому известному STL'
$stlResult = Invoke-ModelTool 'load_model' @{path=$stlPath}
$stlResult | ConvertTo-Json -Depth 20
Invoke-ModelTool 'list_models' @{} | ConvertTo-Json -Depth 20
foreach ($item in $stlResult.objects) { Invoke-ModelTool 'get_model_bounds' @{object_id=$item.object_id} | ConvertTo-Json -Depth 20 }
```

Сверьте мировые границы/размеры с известной геометрией и UI, учитывая Y-up и штатный autoscale. Убедитесь, что исходные модели сохранили ID и геометрию. Затем отдельно выполните импорты OBJ и 3MF, каждый раз повторяя list_models/get_model_bounds по ID из ответа:

```powershell
$objPath = Read-Host 'Абсолютный локальный путь к OBJ'
$objResult = Invoke-ModelTool 'load_model' @{path=$objPath}
$objResult | ConvertTo-Json -Depth 20
foreach ($item in $objResult.objects) { Invoke-ModelTool 'get_model_bounds' @{object_id=$item.object_id} | ConvertTo-Json -Depth 20 }
$threeMfPath = Read-Host 'Абсолютный локальный путь к 3MF для mesh-импорта'
$threeMfResult = Invoke-ModelTool 'load_model' @{path=$threeMfPath}
$threeMfResult | ConvertTo-Json -Depth 20
foreach ($item in $threeMfResult.objects) { Invoke-ModelTool 'get_model_bounds' @{object_id=$item.object_id} | ConvertTo-Json -Depth 20 }
Invoke-ModelTool 'list_models' @{} | ConvertTo-Json -Depth 20
Invoke-ModelTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 20
Invoke-ModelTool 'list_quality_profiles' @{} | ConvertTo-Json -Depth 20
Invoke-ModelTool 'list_materials' @{extruder_id='0'} | ConvertTo-Json -Depth 20
Invoke-ModelTool 'list_materials' @{extruder_id='1'} | ConvertTo-Json -Depth 20
```

Для 3MF проверьте отсутствие workspace-диалога/замены машины и глобальных профилей, отдельно отметьте object overrides. Зафиксируйте группы/несколько моделей: в списке должна быть верхняя группа без дубликатов частей. Сохраните ответы и UI; реально неизменившиеся глобальные параметры сравните с baseline.

Повторный импорт того же STL должен создать новые объекты с другими ID:

```powershell
Invoke-ModelTool 'load_model' @{path=$stlPath} | ConvertTo-Json -Depth 20
Invoke-ModelTool 'list_models' @{} | ConvertTo-Json -Depth 20
Invoke-ModelRpc 'tools/call' @{name='get_model_bounds'; arguments=@{object_id='__unknown_task04_id__'}} | ConvertTo-Json -Depth 20
Invoke-ModelRpc 'tools/call' @{name='load_model'; arguments=@{path='C:/__missing_task04__.stl'}} | ConvertTo-Json -Depth 20
Invoke-ModelRpc 'tools/call' @{name='load_model'; arguments=@{path=123}} | ConvertTo-Json -Depth 20
```

Неизвестный ID/неверный файл — операционный отказ, неверный тип — `-32602`; сцена после отказов не должна меняться. При любой ошибке/таймауте остановите сценарий и сначала вызовите list_models, не повторяйте импорт автоматически. После проверки пользователь удаляет **только тестовые объекты** штатным UI, затем list_models/get_model_bounds подтверждают исчезновение их ID и сохранность исходной сцены. Инструмент удаления не реализован. Слайсинг и физическую печать не запускать.

## ТЗ 05: штатная classic-нарезка

Добавлены `start_slicing {}`, `get_slicing_status {slice_id}` и `get_slicing_result {slice_id}`. Все поля ID — непустые строки, лишние аргументы запрещены. В dev без app инструменты возвращают `slicer_not_connected`. Старые инструменты сохранены. **Реализация проверена изолированно, runtime-приёмка ТЗ 03/04/05 открыта.** Установленный плагин в этом задании не обновлялся, engine не запускался.

До запуска проверяются машина, classic и соответствующий backend, одна непустая активная платформа, отсутствие preview/импорта/занятых native jobs и выключенная автоматическая нарезка. Выключать auto-slice нужно вручную в UI; Preferences не меняются. Ручной параллельный запуск и редактирование во время операции не поддерживаются. Все API-вызовы/подписки/таймер идут на Qt через прежний мост; HTTP не ждёт завершения engine.

Ответ start — принятие команды и ID, не завершение. Опрос статуса возвращает `queued/running/completed/failed`, реальный progress 0..1 либо null, mode/backend/платформу, valid и причину failed. Начальный NotStarted, progress=1 и старые G-code/оценки не подтверждают успех. Нужны начало, Done своего backend, отдельная Qt-проверка после финализации, свежий G-code и обновлённая PrintInformation своей платформы. Подтверждённые время, сумма длины и массы читаются из STE, без своих оценочных формул; длина переводится из метров в мм, масса в граммах. Исходное округление длины до 0.01 м сохраняется. Неизвестные данные — null и warnings.

Бюджет — 180 секунд, таймер наблюдения — 250 мс. Timeout/ошибка/отмена UI фиксируют failed; позднее завершение не превращается в completed. Защита записей остаётся до наблюдаемого выхода backend: дождитесь завершения/нажмите Stop в UI, при зависшем состоянии перезапустите приложение. Плагин не отменяет engine и не повторяет запуск. Во время защиты доступны чтения, а записи/импорт получают прежние busy-коды; для set_setting/start — slicing_busy.

После изменения печатного контекста `valid=false`, результат возвращает `slice_result_stale`; восстановление исходных настроек не делает старую нарезку актуальной. Камера/выделение и служебные узлы Preview сами по себе результат не портят. Поля и все коды ошибок: `docs/API_CONTRACT.md`; реализация и ограничения: `docs/reports/TASK_05_REPORT.md`. Экспорт 3MF/G-code, 5D-нарезка и события REST в этот объём не входят.

### Команды ручной приёмки ТЗ 05

Эти команды **подготовлены, но не выполнялись в установленном слайсере**. Ведущий после ревью координирует обновление всего плагина. Сначала сохраните проект, затем после согласованного обновления/перезапуска выберите **Stereotech Hybrid 530 V5.2.4**, **classic/3D**, вручную выключите автоматическую нарезку. Нужна одна активная платформа без G-code preview. Для однозначного теста очистите тестовую сцену штатным UI после сохранения проекта; не удаляйте рабочие модели без сохранения. Приёмка качества/материалов ТЗ 03 остаётся отдельной.

Откройте чат и в новом PowerShell выполните блок диагностики с текущим портом:

```powershell
$slicePort = [int](Read-Host 'Порт из адреса открытого чата')
$sliceMcpUrl = 'http://127.0.0.1:{0}/mcp' -f $slicePort
function Invoke-SliceRpc($method, $params) {
    $body = @{jsonrpc='2.0'; id=5; method=$method; params=$params} | ConvertTo-Json -Depth 25 -Compress
    Invoke-RestMethod -Uri $sliceMcpUrl -Method Post -ContentType 'application/json' -TimeoutSec 15 -Body $body
}
function Invoke-SliceTool($name, $arguments) {
    $response = Invoke-SliceRpc 'tools/call' @{name=$name; arguments=$arguments}
    if ($null -ne $response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $envelope = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $envelope.ok) { throw ($envelope | ConvertTo-Json -Depth 25 -Compress) }
    $envelope.data
}
Invoke-SliceRpc 'initialize' @{} | ConvertTo-Json -Depth 25
Invoke-SliceRpc 'tools/list' @{} | ConvertTo-Json -Depth 25
$sliceState = Invoke-SliceTool 'get_slicer_state' @{}
$sliceState | ConvertTo-Json -Depth 25
if ($null -eq $sliceState.machine -or $sliceState.printing_mode -ne 'classic' -or -not $sliceState.backend.matches_mode) {
    throw 'Выберите нужную машину и classic в UI; затем повторите диагностику'
}
$sliceOriginalHeight = (Invoke-SliceTool 'get_setting' @{key='layer_height'; scope='global'}).value
Invoke-SliceTool 'list_models' @{} | ConvertTo-Json -Depth 25
```

Подтвердите реальный импорт ТЗ 04: абсолютный локальный путь к небольшому известному STL без окружающих кавычек, затем list/bounds и сверка детали в UI. При ошибке/timeout сначала list_models и сообщения приложения, без автоматического повторного импорта. Этот прогон STL не закрывает оставшиеся проверки OBJ/3MF и ТЗ 03.

```powershell
$sliceStlPath = Read-Host 'Абсолютный локальный путь к небольшому известному STL'
$sliceLoaded = Invoke-SliceTool 'load_model' @{path=$sliceStlPath}
$sliceLoaded | ConvertTo-Json -Depth 25
Invoke-SliceTool 'list_models' @{} | ConvertTo-Json -Depth 25
foreach ($item in $sliceLoaded.objects) {
    Invoke-SliceTool 'get_model_bounds' @{object_id=$item.object_id} | ConvertTo-Json -Depth 25
}
```

После сверки модели/размеров и размещения внутри стола запустите нарезку. Polling раз в секунду, клиентский предел 195 секунд плюс не более одного HTTP timeout; выход по failed немедленный. Клиентский timeout не является отменой backend.

```powershell
$sliceStart = Invoke-SliceTool 'start_slicing' @{}
$sliceStart | ConvertTo-Json -Depth 25
$sliceId = $sliceStart.slice_id
if ([string]::IsNullOrEmpty($sliceId)) { throw 'Не получен slice_id; проверьте UI, не повторяйте start автоматически' }
$sliceWatch = [Diagnostics.Stopwatch]::StartNew()
do {
    $sliceStatus = Invoke-SliceTool 'get_slicing_status' @{slice_id=$sliceId}
    $sliceStatus | ConvertTo-Json -Depth 25 -Compress
    if ($sliceStatus.state -eq 'failed') { throw ($sliceStatus.error | ConvertTo-Json -Depth 25 -Compress) }
    if ($sliceStatus.state -eq 'completed') { break }
    if ($sliceWatch.Elapsed.TotalSeconds -ge 195) { throw 'Клиент прекратил polling; проверьте UI/Stop и статус этого ID перед новыми действиями' }
    Start-Sleep -Seconds 1
} while ($true)
if (-not $sliceStatus.valid) { throw 'Результат уже неактуален; проверьте изменения сцены' }
$sliceResult = Invoke-SliceTool 'get_slicing_result' @{slice_id=$sliceId}
$sliceResult | ConvertTo-Json -Depth 25
New-Item -ItemType Directory -Path 'tmp' -Force | Out-Null
@{state=$sliceState; loaded=$sliceLoaded; start=$sliceStart; status=$sliceStatus; result=$sliceResult} |
    ConvertTo-Json -Depth 30 | Set-Content -LiteralPath 'tmp/task05_runtime.json' -Encoding UTF8
```

Сверьте секунды/длину/массу с UI STE Slicer, учитывая округление и формат часов/минут. Откройте штатный **Preview**, убедитесь в наличии слоёв именно этой детали и сохраните скриншоты прогресса, результата и предпросмотра. После обычного просмотра статус должен оставаться valid, если печатный контекст не менялся:

```powershell
Invoke-SliceTool 'get_slicing_status' @{slice_id=$sliceId} | ConvertTo-Json -Depth 25
```

Затем проверьте устаревание: введите другое допустимое layer_height (с точкой), проверьте статус/ошибку результата, восстановите исходную высоту. Сначала setter должен успешно завершиться; при его ошибке остановитесь и перечитайте настройку/UI, без автоматического повтора.

```powershell
$sliceNewHeight = [double]::Parse((Read-Host 'Другая допустимая высота слоя в мм'), [Globalization.CultureInfo]::InvariantCulture)
if ($sliceNewHeight -eq $sliceOriginalHeight) { throw 'Нужно другое допустимое значение' }
Invoke-SliceTool 'set_setting' @{key='layer_height'; scope='global'; value=$sliceNewHeight} | ConvertTo-Json
try {
    Invoke-SliceTool 'get_slicing_status' @{slice_id=$sliceId} | ConvertTo-Json -Depth 25
    $sliceStaleRpc = Invoke-SliceRpc 'tools/call' @{name='get_slicing_result'; arguments=@{slice_id=$sliceId}}
    $sliceStaleRpc | ConvertTo-Json -Depth 25
    if ($null -ne $sliceStaleRpc.error) { throw 'Ожидалась операционная ошибка, получена JSON-RPC ошибка' }
    $sliceStale = $sliceStaleRpc.result.content[0].text | ConvertFrom-Json
    if (-not $sliceStaleRpc.result.isError -or $sliceStale.error.code -ne 'slice_result_stale') { throw 'Устаревание не подтверждено' }
} finally {
    Invoke-SliceTool 'set_setting' @{key='layer_height'; scope='global'; value=$sliceOriginalHeight} | ConvertTo-Json
    Invoke-SliceTool 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json
}
Invoke-SliceTool 'get_slicing_status' @{slice_id=$sliceId} | ConvertTo-Json -Depth 25
```

Подтвердите восстановленную высоту в UI. Старый результат должен остаться valid=false; новый актуальный результат требует нового явного запуска. В протоколе ведущего сохраните реальные ответы, модель/версию и скриншоты. Экспорт, повторное открытие 3MF и физическую печать этот сценарий не выполняет.

## ТЗ 06: штатный экспорт G-code и проекта 3MF

`export_gcode {slice_id}`, `save_project_3mf {slice_id}` запускают штатный WriteFileJob и возвращают export_id/state=queued. `get_export_status {export_id}` читает queued/running/completed/failed, реальный progress либо null и готовый артефакт. Нужен актуальный completed classic slice_id из ТЗ 05. У инструментов только непустые строковые ID, без произвольного output_path; неизвестный ID не заменяется последним. В dev — slicer_not_connected.

G-code записывает GCodeWriter из mesh-handler; проект — ThreeMFWorkspaceWriter из workspace-handler, с машиной/экструдерами/контейнерами/Preferences. Mesh-only 3MF недостаточен. Все наши app/scene вызовы идут на Qt, writer запускается штатным job с worker, файловая подготовка/проверка — вне Qt. Декораторы форка не обходятся. Успех требует finished/result без error, успешных flush/close, проверки непустых данных engine либо ZIP/XML/workspace и повторной проверки slice_id. Во время записи и финализации действует общая защита нарезки/настроек/импорта; чтения доступны. Ручная параллельная запись/редактирование не поддерживаются.

Бюджет — 120 секунд; при timeout writer не отменяется, используемый stream и защита сохраняются до его выхода. Поздний finished не публикует ошибочный экспорт. Если штатный декоратор завис после исключения, потребуется перезапуск без автоповтора. Ошибка 3MF после подтверждённого выхода сбрасывает storeArchive; чужой ещё работающий writer не трогается. Подробности: `docs/API_CONTRACT.md`, `docs/reports/TASK_06_REPORT.md`.

Готовые файлы лежат в кэше STE, `AIAssistant/exports/export-<случайная папка>/result.gcode` или `project.3mf`. Точный **local_directory** возвращается только после completed; artifact сообщает ID, имя, kind, размер, slice_id и штатный источник. Каждый запуск создаёт новую папку без перезаписи. Нужные результаты скопируйте, поскольку это кэш. Регистрация ID действует в памяти до перезапуска; HTTP-скачивание пока отсутствует. После изменения проекта исторический файл остаётся связан с прежним slice_id, статус valid=false.

**Native-экспорт, Preview и повторное открытие 3MF ещё не проверены. Установленный плагин не обновлялся.** Файлы fake-тестов не являются результатами STE Slicer, в results/ они не создаются. Ручная приёмка ТЗ 03/04/05/06 открыта.

### PowerShell для приёмки ТЗ 06

После ревью ведущий координирует обновление всего плагина. Пройдите ТЗ 05 выше на небольшом известном STL: Hybrid 530 V5.2.4, classic, auto-slice выключен, одна платформа. Нужен completed/valid=true slice_id **этой сессии плагина**. Если проверяли stale сменой высоты, сначала нужна новая явная нарезка. Не открывайте импортированный G-code до завершения **обоих** экспортов: он изменит исходную сцену.

```powershell
$exportPort = [int](Read-Host 'Текущий порт из адреса чата')
$exportMcpUrl = 'http://127.0.0.1:{0}/mcp' -f $exportPort
function Invoke-ExportRpc($method, $params) {
    $body = @{jsonrpc='2.0'; id=6; method=$method; params=$params} | ConvertTo-Json -Depth 25 -Compress
    Invoke-RestMethod -Uri $exportMcpUrl -Method Post -ContentType 'application/json' -TimeoutSec 15 -Body $body
}
function Invoke-ExportTool($name, $arguments) {
    $response = Invoke-ExportRpc 'tools/call' @{name=$name; arguments=$arguments}
    if ($null -ne $response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $envelope = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $envelope.ok) { throw ($envelope | ConvertTo-Json -Depth 25 -Compress) }
    $envelope.data
}
function Wait-LocalExport($exportId) {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    do {
        $status = Invoke-ExportTool 'get_export_status' @{export_id=$exportId}
        $status | ConvertTo-Json -Depth 25 -Compress | Write-Host
        if ($status.state -eq 'failed') { throw ($status.error | ConvertTo-Json -Compress) }
        if ($status.state -eq 'completed') {
            if (-not $status.valid -or $null -eq $status.artifact) { throw 'Нет актуального подтверждённого артефакта' }
            return $status
        }
        if ($watch.Elapsed.TotalSeconds -ge 135) { throw 'Клиентский timeout; writer не отменён. Проверьте этот ID/UI, без автоматического повтора' }
        Start-Sleep -Seconds 1
    } while ($true)
}
Invoke-ExportRpc 'tools/list' @{} | ConvertTo-Json -Depth 25
$exportSliceId = Read-Host 'slice_id завершённой актуальной нарезки ТЗ 05'
Invoke-ExportTool 'get_slicing_result' @{slice_id=$exportSliceId} | ConvertTo-Json -Depth 25
$exportBaseline = @{
    state = Invoke-ExportTool 'get_slicer_state' @{}
    models = Invoke-ExportTool 'list_models' @{}
    quality = Invoke-ExportTool 'list_quality_profiles' @{}
    material0 = Invoke-ExportTool 'list_materials' @{extruder_id='0'}
    material1 = Invoke-ExportTool 'list_materials' @{extruder_id='1'}
    layer = Invoke-ExportTool 'get_setting' @{key='layer_height'; scope='global'}
}
New-Item -ItemType Directory -Path 'tmp' -Force | Out-Null
$exportBaseline | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath 'tmp/task06_before.json' -Encoding UTF8
$exportBaseline | ConvertTo-Json -Depth 30
```

Сверьте baseline с UI; отдельно зафиксируйте overrides/ориентацию/размеры. Выполните записи последовательно, затем скопируйте в **новую несуществующую** пользовательскую папку. Никакого пути LLM-инструменту не передаётся; Copy-Item выполняет сам пользователь.

```powershell
$gcodeStart = Invoke-ExportTool 'export_gcode' @{slice_id=$exportSliceId}
$gcodeExport = Wait-LocalExport $gcodeStart.export_id
$projectStart = Invoke-ExportTool 'save_project_3mf' @{slice_id=$exportSliceId}
$projectExport = Wait-LocalExport $projectStart.export_id
@{gcode=$gcodeExport; project=$projectExport} | ConvertTo-Json -Depth 30 |
    Set-Content -LiteralPath 'tmp/task06_exports.json' -Encoding UTF8
$gcodeSource = Join-Path $gcodeExport.local_directory $gcodeExport.artifact.file_name
$projectSource = Join-Path $projectExport.local_directory $projectExport.artifact.file_name
if ((Get-Item -LiteralPath $gcodeSource).Length -ne $gcodeExport.artifact.size_bytes -or
    (Get-Item -LiteralPath $projectSource).Length -ne $projectExport.artifact.size_bytes) { throw 'Размеры файлов не совпали' }
$exportCopyDir = Read-Host 'Абсолютный путь к новой несуществующей папке для копий результатов'
New-Item -ItemType Directory -Path $exportCopyDir -ErrorAction Stop | Out-Null
$gcodeCopy = Join-Path $exportCopyDir 'result.gcode'
$projectCopy = Join-Path $exportCopyDir 'project.3mf'
Copy-Item -LiteralPath $gcodeSource -Destination $gcodeCopy -ErrorAction Stop
Copy-Item -LiteralPath $projectSource -Destination $projectCopy -ErrorAction Stop
Get-Item -LiteralPath $gcodeCopy, $projectCopy | Select-Object FullName, Length
```

Сохраните текущую работу перед открытием файлов. Штатным UI откройте G-code в Preview и проверьте слои/траектории именно этой детали, режим и оценки. Затем через UI откройте project.3mf **как проект**, не через load_model: сравните машину/classic, материалы обоих экструдеров, качество/overrides, объекты, ориентацию и габариты с baseline/UI. Повторное открытие может менять ID объектов — сравнивается содержимое, не прежние object_id. После полного открытия проекта перечитайте:

```powershell
Invoke-ExportTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 25
Invoke-ExportTool 'list_models' @{} | ConvertTo-Json -Depth 25
Invoke-ExportTool 'list_quality_profiles' @{} | ConvertTo-Json -Depth 25
Invoke-ExportTool 'list_materials' @{extruder_id='0'} | ConvertTo-Json -Depth 25
Invoke-ExportTool 'list_materials' @{extruder_id='1'} | ConvertTo-Json -Depth 25
Invoke-ExportTool 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json
```

Сохраните реальные JSON-ответы/файлы/скриншоты для ведущего. Физическую печать не запускайте. Открытие файлов меняет исходный slicing-контекст, поэтому исторический экспорт может стать valid=false — это не повторная нарезка и не потеря готового файла. Не выдавайте структурную проверку ZIP за этот native-прогон.

## ТЗ 07: настройки global и явно выбранного экструдера

get/set_setting теперь поддерживают 19 ключей: layer_height, layer_height_0, line_width, wall_line_count, top_bottom_thickness, infill_sparse_density, infill_pattern, support_enable, support_type, support_angle, material_print_temperature, material_bed_temperature, speed_print, retraction_enable, retraction_amount, retraction_speed, cool_fan_enabled, cool_fan_speed, adhesion_type. Режим, машина/сопло, start/end G-code и произвольные ключи сюда не входят. Batch setter отсутствует.

Обязательны key/scope, для set ещё value. При `scope=extruder` обязателен строковый extruder_id из runtime-позиций, например `"0"`; при global extruder_id запрещён. UI-экструдер не меняется и не подставляется. `describe_settings {scope,extruder_id?}` возвращает allowlist с actual type/value/unit/options, enabled, разрешениями записи и hard/warning bounds. Отсутствующие/невычисленные поля — null/limitations, а не собственные defaults. Разрешение записи зависит от фактической машины/стека; наличие ключа в allowlist не обещает поддержку.

Числа конечные, bool не является числом; int не округляется, числовые строки не принимаются. Bool — только JSON boolean, enum — ключ текущих options, не подпись. Layer_height сохраняет >0, для остальных действуют runtime hard bounds (нулевое заполнение может допускаться). Выход за warning bounds разрешён с warnings. Неизвестные тип/permissions/enabled/readonly и невычисленные hard bounds запрещают запись.

Запись только в userChanges целевого стека, before/after фактически читаются. No-op не пишет и не инвалидирует slice. Попытка изменения делает старую нарезку/будущий экспорт stale, в том числе при неизвестном результате setter; восстановление значения не возвращает valid. Общий guard 05/06 сохранён, чтения доступны. При timeout до старта callback отменяется, после начала — неопределённость: сначала чтение/UI, без автоматического повтора/отката. Зависимые значения могут пересчитаться штатно. Восстановление значения не удаляет созданный override — для сброса override используется штатный UI.

**Код проверен с подменами; ручная приёмка ТЗ 03–07 открыта.** Установленный плагин не обновлён. Изменение температуры в настройках проекта не является командой нагрева/физической печати. Контракт и ошибки: `docs/API_CONTRACT.md`; проверенные свойства и результаты: `docs/reports/TASK_07_REPORT.md`.

### PowerShell для приёмки ТЗ 07

После ревью и согласованного обновления всего плагина выберите **Hybrid 530 V5.2.4**, classic. Дождитесь завершения импорта/нарезки/экспорта; не прерывайте их ради теста. Сохраните текущий проект. Команды выполняйте последовательно из корня репозитория. При любой ошибке остановите сценарий, перечитайте настройку и UI; не повторяйте запись автоматически и не переходите к восстановлению до выяснения состояния.

```powershell
$printPort = [int](Read-Host 'Текущий порт из адреса чата')
$printMcpUrl = 'http://127.0.0.1:{0}/mcp' -f $printPort
function Invoke-PrintRpc($method, $params) {
    $body = @{jsonrpc='2.0'; id=7; method=$method; params=$params} | ConvertTo-Json -Depth 25 -Compress
    Invoke-RestMethod -Uri $printMcpUrl -Method Post -ContentType 'application/json' -TimeoutSec 15 -Body $body
}
function Invoke-PrintTool($name, $arguments) {
    $response = Invoke-PrintRpc 'tools/call' @{name=$name; arguments=$arguments}
    if ($null -ne $response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $envelope = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $envelope.ok) { throw ($envelope | ConvertTo-Json -Depth 25 -Compress) }
    $envelope.data
}
$printState = Invoke-PrintTool 'get_slicer_state' @{}
$printState | ConvertTo-Json -Depth 25
if ($null -eq $printState.machine -or $printState.machine.name -ne 'Stereotech Hybrid 530 V5.2.4' -or
    $printState.printing_mode -ne 'classic' -or -not $printState.backend.matches_mode) { throw 'Выберите Stereotech Hybrid 530 V5.2.4 и согласованный classic backend в UI' }
$printGlobal = Invoke-PrintTool 'describe_settings' @{scope='global'}
$printExt0 = Invoke-PrintTool 'describe_settings' @{scope='extruder'; extruder_id='0'}
$printExt1 = Invoke-PrintTool 'describe_settings' @{scope='extruder'; extruder_id='1'}
foreach ($description in @($printGlobal, $printExt0, $printExt1)) {
    $description.settings | Format-Table key, type, value, unit, available, enabled, settable
}
$printGlobalKey = 'speed_print'
$printExtKey = 'material_print_temperature'
$printGlobalMeta = @($printGlobal.settings | Where-Object key -eq $printGlobalKey)[0]
$printExtMeta = @($printExt0.settings | Where-Object key -eq $printExtKey)[0]
foreach ($item in @($printGlobalMeta, $printExtMeta)) {
    if ($null -eq $item -or -not $item.available -or -not $item.enabled -or -not $item.settable -or $item.type -ne 'float') {
        throw 'Выбранный параметр не подтверждён для записи. Изучите describe_settings; не включайте зависимости автоматически'
    }
    $item | ConvertTo-Json -Depth 25
}
$printOriginalGlobal = Invoke-PrintTool 'get_setting' @{key=$printGlobalKey; scope='global'}
$printOriginal0 = Invoke-PrintTool 'get_setting' @{key=$printExtKey; scope='extruder'; extruder_id='0'}
$printOriginal1 = Invoke-PrintTool 'get_setting' @{key=$printExtKey; scope='extruder'; extruder_id='1'}
New-Item -ItemType Directory -Path 'tmp' -Force | Out-Null
@{state=$printState; global=$printGlobal; ext0=$printExt0; ext1=$printExt1; originalGlobal=$printOriginalGlobal; original0=$printOriginal0; original1=$printOriginal1} |
    ConvertTo-Json -Depth 30 | Set-Content -LiteralPath 'tmp/task07_before.json' -Encoding UTF8
$printNewGlobal = [double]::Parse((Read-Host 'Другая допустимая speed_print в runtime unit, с точкой'), [Globalization.CultureInfo]::InvariantCulture)
$printNew0 = [double]::Parse((Read-Host 'Другая допустимая material_print_temperature экструдера 0 в runtime unit, с точкой'), [Globalization.CultureInfo]::InvariantCulture)
if ($printNewGlobal -eq $printOriginalGlobal.value -or $printNew0 -eq $printOriginal0.value) { throw 'Нужны другие допустимые значения' }
```

Если температура недоступна из-за зависимостей/профиля, отметьте этот пункт как непроверенный либо отдельно выберите подтверждённый per-extruder параметр и сохраните его baseline. Не меняйте профиль/зависимости автоматически. Сверьте исходные значения и активную UI-позицию. Сначала global:

```powershell
Invoke-PrintTool 'set_setting' @{key=$printGlobalKey; scope='global'; value=$printNewGlobal} | ConvertTo-Json -Depth 20
$printAfterGlobal = Invoke-PrintTool 'get_setting' @{key=$printGlobalKey; scope='global'}
$printAfterGlobal | ConvertTo-Json
```

Сверьте after с UI, затем отдельно измените экструдер 0. Снимок второго экструдера снимается непосредственно перед этим изменением: global-настройки могли штатно повлиять на derived values.

```powershell
$printOtherBefore = Invoke-PrintTool 'get_setting' @{key=$printExtKey; scope='extruder'; extruder_id='1'}
Invoke-PrintTool 'set_setting' @{key=$printExtKey; scope='extruder'; extruder_id='0'; value=$printNew0} | ConvertTo-Json -Depth 20
$printAfter0 = Invoke-PrintTool 'get_setting' @{key=$printExtKey; scope='extruder'; extruder_id='0'}
$printAfter1 = Invoke-PrintTool 'get_setting' @{key=$printExtKey; scope='extruder'; extruder_id='1'}
@{global=$printAfterGlobal; ext0=$printAfter0; ext1=$printAfter1} | ConvertTo-Json -Depth 25 |
    Set-Content -LiteralPath 'tmp/task07_after.json' -Encoding UTF8
$printAfter0, $printAfter1 | ConvertTo-Json -Depth 20
if ($printAfter1.value -ne $printOtherBefore.value) { throw 'Значение другого экструдера изменилось; сначала выясните причину в UI' }
```

Подтвердите в UI нужный стек/значение и отсутствие автоматического переключения UI-экструдера инструментом. Ручное переключение вкладок для сверки допустимо. После успешной проверки явно восстановите оба исходных значения и проверьте API/UI:

```powershell
Invoke-PrintTool 'set_setting' @{key=$printExtKey; scope='extruder'; extruder_id='0'; value=$printOriginal0.value} | ConvertTo-Json -Depth 20
Invoke-PrintTool 'set_setting' @{key=$printGlobalKey; scope='global'; value=$printOriginalGlobal.value} | ConvertTo-Json -Depth 20
$printRestoredGlobal = Invoke-PrintTool 'get_setting' @{key=$printGlobalKey; scope='global'}
$printRestored0 = Invoke-PrintTool 'get_setting' @{key=$printExtKey; scope='extruder'; extruder_id='0'}
$printRestored1 = Invoke-PrintTool 'get_setting' @{key=$printExtKey; scope='extruder'; extruder_id='1'}
@{global=$printRestoredGlobal; ext0=$printRestored0; ext1=$printRestored1} | ConvertTo-Json -Depth 25 |
    Set-Content -LiteralPath 'tmp/task07_restored.json' -Encoding UTF8
$printRestoredGlobal, $printRestored0, $printRestored1 | ConvertTo-Json -Depth 20
if ($printRestoredGlobal.value -ne $printOriginalGlobal.value -or $printRestored0.value -ne $printOriginal0.value -or
    $printRestored1.value -ne $printOriginal1.value) { throw 'Исходные значения не подтверждены; проверьте UI и сохранённый baseline' }
```

Сохраните реальные ответы и скриншоты до/после/восстановления для ведущего. Не объявляйте этот сценарий пройденным по тестам с подменами. Старую нарезку после изменения/восстановления повторно актуальной не считать; нужен новый явный start.

## Настройка LLM и асинхронный чат — интеграция b9c0190

В меню **AI Assistant -> LLM Settings** задайте Base URL OpenAI-совместимого Chat Completions API, имя модели и ключ; значений по умолчанию нет. Клиент добавляет /chat/completions к URL, если путь ещё не указан. TLS проверяется стандартной библиотекой, redirects с Authorization запрещены. Не помещайте ключ в URL. Настройки хранятся в Preferences ai_assistant/base_url, model, api_key через тот же QtBridge, сохранение подтверждается перечитыванием. Парольное поле при открытии пустое: пусто при Save — сохранить прежний ключ, отдельный флажок — удалить. При ошибке/таймауте кеш считается неподтверждённым; перечитайте через повторное открытие диалога, не повторяйте изменение автоматически. Используется Preferences по требованиям проекта. Браузеру доступны лишь configured/has_key, не значения настроек.

**Open chat** открывает временный UI коллеги b9c0190, адаптированный к [контракту](../docs/API_CONTRACT.md). Сообщение принимается HTTP 202, tools и ответ приходят polling через /api/events. История/карточки восстанавливаются из session snapshot при обновлении страницы; после перезапуска плагина память диалога сбрасывается. Один активный запрос для общей сцены; бюджеты 8 LLM / 16 tools / 120 сек, сеть до 30 сек с учётом остатка. При неопределённой мутации цикл останавливается и сообщает о необходимости проверки UI; автоповтора нет. При обрыве/5xx сохраняется исходный client_message_id, явный повтор отправляет то же тело. Вопрос «Что умеешь?» допускает только чтения. Диагностический load_model(path) и assistant_status исключены из LLM, остальные схемы — существующий единый ToolRegistry. Модель пока загрузите вручную или диагностическими MCP-командами ТЗ 04.

Статус «LLM настроена» не подтверждает связь с провайдером. Снимок нарезки — последний реально прочитанный tool-результат; фонового мониторинга engine между turn нет. Завершение ответа/лимит агента не завершает engine и не подтверждает экспорт. Запросите статус по slice_id, затем актуальный результат; время/материал берутся из STE. Вложения, HTTP-скачивание, преобразования и 5D-нарезка не добавлены. Старый /api/message остаётся отдельным legacy-демо, SPA его не вызывает.

Проверки разработчика без провайдера/ключа:

```powershell
python -m unittest discover -s tests -v
node --check plugin/AIAssistant/web/app.js
node tests/test_agent_web.js
git diff --check
```

Отчёт — [AGENT_INTEGRATION_REPORT.md](../docs/reports/AGENT_INTEGRATION_REPORT.md). После согласованного обновления установленной копии нужна отдельная native-приёмка: register/shutdown, Qt-диалог и сохранение после перезапуска; вопрос без изменений; чтение слоя, явная допустимая запись/сверка UI/восстановление; затем подготовленная небольшая модель classic и актуальные slice/result/экспорт/Preview/повторное открытие 3MF. Команды ТЗ 01–07 выше сохранены. В этом заходе провайдер не вызывался, ключи не вводились, установленный плагин не менялся. ТЗ 03–07 и LLM-сценарий вручную не приняты.

## Проверка

- Загрузка плагина и get/set layer_height проверены на сборке 7.10.12; другие сборки и остальные операции требуют своих проверок. Shutdown приложения отдельно не проверялся.
- После правок Python перезапускайте слайсер.
- Ошибки смотрите в `stderr.log` и логах папки конфигурации.
- В финальную сдачу включить этот README, весь каталог `AIAssistant` и результаты в соседнем `results`.
