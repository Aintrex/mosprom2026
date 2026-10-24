# AI Assistant для STE Slicer

Плагин добавляет в STE Slicer (форк Cura 3.6, `api: 5`, Python 3.5) окно чата в системном браузере. Пользователь описывает задачу и прикладывает фото (JPG/PNG) или модель (STL/OBJ/3MF); агент через MCP-инструменты выбирает режим (classic / cylindrical / cylindrical_full), материал, профиль и настройки, импортирует и ориентирует модель, запускает нарезку и сохраняет настоящие G-code и 3MF штатными writer'ами STE.

## Установка

1. Установите и активируйте выданный дистрибутив STE Slicer.
2. Откройте **Справка → Показать папку конфигурации**, закройте слайсер.
3. Скопируйте каталог `AIAssistant` целиком в `plugins` этой папки (обычно `%APPDATA%\steslicer\7.x\plugins`). При обновлении сначала удалите старую копию `AIAssistant`, остальные файлы конфигурации не трогайте.
4. Запустите слайсер, выберите принтер **Stereotech Hybrid 530 V5.2.4**.

## Настройка LLM

**Расширения → AI Assistant → LLM Settings**: Base URL OpenAI-совместимого API (Chat Completions с tool calling), имя модели и ключ, которые выдали организаторы. Значения хранятся в Preferences приложения; ключ не выводится в браузер и логи, в исходниках его нет. Пустое поле ключа при повторном сохранении оставляет прежний ключ.

## Работа

**AI Assistant → Open chat** открывает `http://127.0.0.1:<порт>/`. Типовой запрос: прикрепить STL и написать «Кронштейн под нагрузку, PETG, без поддержек. Подготовь G-code и проект». В ленте видны вызовы инструментов, их результат и ошибки; слева — состояние нарезки/экспорта, ссылки на скачивание и панель **Модели на сцене** (поворот, перемещение, масштаб, удаление выбранной модели без LLM, с подтверждением фактического результата; отмена — Ctrl+Z в STE).

Рекомендуемые настройки STE: auto-slice выключен (по умолчанию), **«Автоматически раздвигать модели» (push free) выключено** (по умолчанию). «Автоматически опускать модели на стол» можно оставить включённым — после поворота STE сам поставит модель на стол, плагин сообщает об этом предупреждением.

## Изменения полировки 24.10.2026

- Поворот/перемещение/масштаб/удаление и расстановка больше не отклоняются при включённом по умолчанию drop down; запрещается только push free, который двигает соседние модели.
- Числовая строка для `set_setting` (`"0.12"`) разбирается на границе LLM строгим путём из коммита 2aea9b5 (`argument_admission` в результате и пометка в ответе). Дополнительно на той же границе `"true"/"false"` для bool-настроек и `extruder_id: null` при `scope=global` исправляются с пометкой `argument_coercions`. Числа преобразований (угол, координаты, масштаб) строкой не чинятся и отклоняются до API. Для LLM из схемы `set_setting` убирается верхнеуровневый `oneOf` (совместимость провайдеров); MCP отдаёт полную схему, проверка на сервере та же. Прямой MCP строгий.
- Новые инструменты только для чтения `wait_for_slicing` / `wait_for_export`: агент доводит сценарий «нарезка → G-code/3MF» в одном запросе без серии LLM-опросов.
- Бюджеты хода: 20 обращений к LLM, 40 инструментов, 480 с на ход, до 120 с на сетевой вызов; импорт модели ждёт до 60 с (раньше 10 с — мало для крупных STL).
- Размер запроса к LLM ограничен: старые результаты инструментов в истории укорачиваются до 600 символов, история прошлых ходов — до ~24 тыс. символов; числа в результатах округляются, список материалов сжимается, список 120 ключей передаётся один раз (в `set_setting`). Причина: на длинном диалоге провайдер не отвечал за 60 с. После таймаута можно написать «продолжай».
- Системный промпт описывает сценарий подготовки печати и выбор 3D/5D; фото из старых ходов не пересылаются base64 на каждом шаге.
- Прямые вызовы `/mcp` (и панель моделей) проходят через тот же учёт записей, что и агент: изменение сцены делает прежнюю нарезку и скачивания устаревшими, при работающем агенте ручная запись отклоняется.
- **5D-нарезка больше не помечается проваленной после успеха.** По исходникам `CliParserBackend` после финального `SlicingFinished` (Done, 1.0) парсер штатно завершается, а backend пересоздаёт сокет и процесс; G-code при этом сохраняется. Раньше наблюдатель трактовал это как `slicing_parser_failed` (на созвоне 24.10 — «Колесо» в 5D Spiral) либо снимал актуальность у уже завершённой нарезки. Теперь после подтверждённого Done выход/перезапуск парсера (5D Spiral) и дочерних движков (полный 5D) не считается сбоем; до Done по-прежнему считается. Лимит наблюдения нарезки — 600 с (было 180).
- Новый инструмент `center_model` и кнопка «Поставить по центру стола»: центр габаритов модели переносится в x=0, z=0 по фактическим границам (для 5D деталь печатается вокруг стержня-основания в центре стола). Режим печати переключается и при моделях на сцене — неверное правило «только на пустой сцене» убрано из промпта и описания инструмента.
- Служебная фраза о разборе числовой строки больше не вставляется в ответ агента; вместо неё понятная строка в карточке инструмента.
- UI: строки вложений с форматом, размером, миниатюрой фото, фазой загрузки и различием «прикреплено к сообщению» / «импортировано в сцену»; лимиты берутся из `/api/status`; Enter отправляет сообщение.

Автоматические проверки: `python -m unittest discover -s tests` и `node tests/test_agent_web.js`. Они используют подмены STE/провайдера; прогон в установленном STE Slicer с реальной LLM — отдельная приёмка (см. `docs/CURRENT_VERSION_ACCEPTANCE.md`).

Ниже — история разработки по заданиям; при расхождении действует раздел выше.

## Реализовано в ТЗ 01

ТЗ 01 добавило через `/mcp` инструменты `assistant_status`, `get_setting` и `set_setting` для обязательных `key=layer_height`, `scope=global`, значения в мм. Эти вызовы и форма ответов сохранены. ТЗ 07 расширяет настройки на allowlist и явно выбранный экструдер, см. ниже; остальные настройки вне allowlist пока не реализованы. Диагностика/режимы добавлены в ТЗ 02, списки и выбор встроенного качества/материалов — в ТЗ 03, импорт/список моделей — в ТЗ 04, classic-нарезка — в ТЗ 05, штатный экспорт — в ТЗ 06, преобразования/удаление с Undo — в ТЗ 08. Вызовы настроек из HTTP выполняются через `app.callLater`, ожидание результата ограничено 5 секундами. Запись возвращает фактически прочитанные `before` и `after`.

Аргументы проверяются до обращения к API. Жёсткие границы берутся из текущего стека; предупреждения профиля не запрещают запись. Если запись не подтверждена чтением, возвращается ошибка. MCP помещает результат `{ok,data}` или `{ok,error}` в JSON-текст `content` и выставляет `isError` при операционной ошибке. Неверные аргументы — JSON-RPC `-32602`.

`python scripts/dev_server.py` работает без Qt/UM. Список MCP-инструментов тот же, но операции возвращают `slicer_not_connected`; успех не имитируется. Настроек Preferences/ключа в dev нет, отправка чата даёт 503 llm_not_configured, запуск сервера не вызывает провайдера. Готовность чата показывает /api/status; /api/health — простой признак работы сервера.

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

Добавлены `load_model {path}`, `list_models {}` и `get_model_bounds {object_id}`. path/object_id — непустые строки, лишние поля запрещены. load_model принимает только абсолютный путь к читаемому обычному локальному STL/OBJ/3MF, **непустому и не больше 50 MiB** (ограничение прототипа). URL, UNC/device/сетевые диски и другие типы отклоняются. Это диагностический MCP-ввод; для чата роли2A/2B используют upload/file_id ниже, не передавайте LLM произвольный доступ к локальным путям. Файлы проверяются в worker; новый код не возвращает/не логирует полный путь модели. Штатные логи reader могут содержать его.

Импорт запускает штатный readLocalFile с `skip_project_file_check=True`: 3MF импортируется как mesh, без workspace-восстановления проекта. Объектные overrides 3MF могут сохраниться. Штатное размещение и автомасштабирование не отключаются, Preferences не меняются. G-code preview блокирует импорт до запуска, чтобы избежать штатного deleteAll.

Успех требует своего fileLoaded, затем fileCompleted и фактических новых верхних моделей в исходной сцене/машине. Полный путь сопоставляется только внутри контекста. Один ранний сигнал или посторонний узел недостаточны. При обнаруженной смене машины/сцены, исчезновении старых узлов или пустом результате возвращается ошибка. Ручной параллельный импорт/очистка не поддерживаются, в особенности тот же путь без уникального job ID.

Ожидание в worker ограничено общими 60 секундами (до 24.10 — 10 с). После запуска job не отменяется при timeout: модели могут появиться позже. Вызовите list_models, не повторяйте импорт автоматически. Новый импорт и изменения режима/профиля/материала блокируются до собственного завершения или shutdown. Если fileCompleted не пришёл, требуется перезапуск. Ошибка API с неизвестным результатом запуска также сохраняет блокировку. Списки/диагностика доступны. После подтверждённого завершения или shutdown подписки снимаются на Qt-потоке. Прямой синхронный load_model из Qt-потока запрещён.

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

Готовые файлы лежат в кэше STE, `AIAssistant/exports/export-<случайная папка>/result.gcode` или `project.3mf`. Точный **local_directory** возвращается только после completed; artifact сообщает ID, имя, kind, размер, slice_id и штатный источник. Каждый запуск создаёт новую папку без перезаписи. Нужные результаты скопируйте, поскольку это кэш. Регистрация ID действует в памяти до перезапуска; скачивание из чата добавлено ролью2C ниже. После изменения проекта исторический файл остаётся связан с прежним slice_id, статус valid=false.

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

ТЗ07 исходно открыло 19 ключей: layer_height, layer_height_0, line_width, wall_line_count, top_bottom_thickness, infill_sparse_density, infill_pattern, support_enable, support_type, support_angle, material_print_temperature, material_bed_temperature, speed_print, retraction_enable, retraction_amount, retraction_speed, cool_fan_enabled, cool_fan_speed, adhesion_type. ТЗ13 сохраняет их и расширяет каталог до120 (см. отдельный раздел ниже). Режим, машина/сопло, start/end G-code и произвольные ключи сюда не входят. Batch setter отсутствует.

Обязательны key/scope, для set ещё value. При `scope=extruder` обязателен строковый extruder_id из runtime-позиций, например `"0"`; при global extruder_id запрещён. UI-экструдер не меняется и не подставляется. `describe_settings {scope,extruder_id?,keys?,category?,query?,offset?,limit?}` возвращает страницу каталога с actual type/value/unit/options, enabled, разрешениями записи и hard/warning bounds. Без фильтров теперь выдаются первые20 параметров, не полный список; для известного ключа используйте keys. Отсутствующие/невычисленные поля — null/limitations, а не собственные defaults. Разрешение записи зависит от фактической машины/стека; наличие ключа в каталоге не обещает поддержку.

Числа конечные, bool не является числом; int не округляется, прямой MCP/адаптер числовые строки не принимают. Узкий явный LLM-only repair описан в разделе исправления аргументов ниже, строгую границу MCP он не меняет. Bool — только JSON boolean, enum — ключ текущих options, не подпись. Layer_height сохраняет >0, для остальных действуют runtime hard bounds (нулевое заполнение может допускаться). Выход за warning bounds разрешён с warnings. Неизвестные тип/permissions/enabled/readonly и невычисленные hard bounds запрещают запись.

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
$printGlobal = Invoke-PrintTool 'describe_settings' @{scope='global'; keys=@('speed_print')}
$printExt0 = Invoke-PrintTool 'describe_settings' @{scope='extruder'; extruder_id='0'; keys=@('material_print_temperature')}
$printExt1 = Invoke-PrintTool 'describe_settings' @{scope='extruder'; extruder_id='1'; keys=@('material_print_temperature')}
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

## ТЗ 13: расширенный каталог параметров

Каталог расширен с19 до **120 ключей в10 группах**, включая прежние19. Полный список/типы — `docs/tasks/TASK_13_SETTINGS_CATALOG.md`, контракт — `docs/API_CONTRACT.md`. Это перечень разрешённых запросов; доступность/тип/enabled/права записи подтверждаются API текущей машины/профиля/режима/экструдера. Машинные параметры, G-code/CLI, `printing_mode` и batch-запись не открыты; `cylindrical_round_segments` — float.

`describe_settings` принимает scope и необязательные `keys` (1–20 уникальных), `category`, `query` (до80 символов), `offset` (целое>=0), `limit` (1–30, default20). Категории: resolution/shell/infill/material/speed/travel/cooling/support/platform_adhesion/printing_mode_category. Фильтры пересекаются, выдача в порядке каталога; query ищет подстроку без регистра по key/исходному английскому краткому названию. Отсутствие query не фильтрует, пустая/пробельная query возвращает0. Все аргументы/машина/экструдер проверяются даже для пустой страницы.

Ответ сохраняет scope/settings/extruder_id и добавляет total/offset/limit/returned/next_offset/truncated. По умолчанию только20 метаданных, максимум30; API не читает все120 ради страницы. Next_offset=null в конце, truncated означает оставшиеся страницы. Label/description берутся из Definition API (до200/500 символов, отдельные флаги усечения), fallback label/category из каталога; text_source отличает статический текст от API. Нет описания — null, собственных defaults нет. Options берутся целиком из runtime, без отдельной квоты размера.

Прежние строгие get/set, before/after, Qt/userChanges, guards и stale slice/export сохранены. LLM получает инструкцию адресного поиска, UI — количество найденных/выданных и notice следующей страницы; JSONdetails остаются закрытыми. **Проверки с подменами не подтверждают доступность всех120 в STE.** Итоги/размеры ответов — `docs/reports/TASK_13_REPORT.md`; новая копия не установлена, native-приёмка открыта.

### PowerShell для приёмки ТЗ 13

Команды подготовлены, **не выполнялись**. После согласованного обновления целой копии сохраните проект, выберите Hybrid530V5.2.4/classic, дождитесь завершения native-операций и выключите auto-slice вручную. При любой ошибке setter остановитесь, перечитайте значение/UI: не повторяйте неизвестно исполненную запись и не делайте автоматический откат. Ниже восстановление — отдельный явный шаг после подтверждённого результата. Восстановление значения не удаляет override и не возвращает старой нарезке valid. Реальные ответы/скриншоты сохраните для ведущего; это не команды нагрева/печати.

```powershell
$extendedPort = [int](Read-Host 'Текущий порт из адреса чата')
$extendedUrl = 'http://127.0.0.1:{0}/mcp' -f $extendedPort
function Invoke-ExtendedTool($name, $arguments) {
    $body = @{jsonrpc='2.0'; id=13; method='tools/call'; params=@{name=$name; arguments=$arguments}} | ConvertTo-Json -Depth 25 -Compress
    $rpc = Invoke-RestMethod -Uri $extendedUrl -Method Post -ContentType 'application/json' -TimeoutSec 15 -Body $body
    if ($null -ne $rpc.error) { throw ($rpc.error | ConvertTo-Json -Compress) }
    $envelope = $rpc.result.content[0].text | ConvertFrom-Json
    if ($rpc.result.isError -or -not $envelope.ok) { throw ($envelope | ConvertTo-Json -Depth 25 -Compress) }
    $envelope.data
}
function Get-ExtendedWritable($key, $scope, $extruderId) {
    $arguments = @{scope=$scope; keys=@($key)}
    if ($scope -eq 'extruder') { $arguments.extruder_id = $extruderId }
    $page = Invoke-ExtendedTool 'describe_settings' $arguments
    $meta = @($page.settings)[0]
    if ($null -eq $meta -or -not $meta.available -or -not $meta.enabled -or -not $meta.settable -or $meta.read_only -ne $false) {
        throw 'API не подтвердило возможность записи: прочитайте limitations/UI, зависимости не включаются автоматически'
    }
    $meta
}
$extendedState = Invoke-ExtendedTool 'get_slicer_state' @{}
$extendedState | ConvertTo-Json -Depth 25
if ($extendedState.machine.name -ne 'Stereotech Hybrid 530 V5.2.4' -or $extendedState.printing_mode -ne 'classic' -or -not $extendedState.backend.matches_mode) { throw 'Сначала выберите Hybrid530/classic в UI' }
$extendedPage = Invoke-ExtendedTool 'describe_settings' @{scope='global'; category='support'; query='pattern'; limit=1}
$extendedPage | ConvertTo-Json -Depth 25
if ($null -ne $extendedPage.next_offset) {
    Invoke-ExtendedTool 'describe_settings' @{scope='global'; category='support'; query='pattern'; offset=$extendedPage.next_offset; limit=1} | ConvertTo-Json -Depth 25
}
Invoke-ExtendedTool 'describe_settings' @{scope='global'; keys=@('support_z_distance','support_xy_distance','brim_width','raft_surface_layers')} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'describe_settings' @{scope='global'; query=''} | ConvertTo-Json -Depth 25
```

Новый scalar speed_wall: сначала метаданные/текущее значение, затем другое допустимое число в runtime unit. Если runtime не подтвердил float, пункт остаётся непроверенным.

```powershell
$extendedScalarMeta = Get-ExtendedWritable 'speed_wall' 'global' $null
if ($extendedScalarMeta.type -ne 'float') { throw 'Не подтверждён float' }
$extendedScalarMeta | ConvertTo-Json -Depth 25
$extendedScalarBefore = Invoke-ExtendedTool 'get_setting' @{key='speed_wall'; scope='global'}
$extendedScalarNew = [double]::Parse((Read-Host 'Другая допустимая speed_wall, с точкой'), [Globalization.CultureInfo]::InvariantCulture)
if ($extendedScalarNew -eq $extendedScalarBefore.value) { throw 'Нужно другое допустимое значение' }
Invoke-ExtendedTool 'set_setting' @{key='speed_wall'; scope='global'; value=$extendedScalarNew} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_setting' @{key='speed_wall'; scope='global'} | ConvertTo-Json -Depth 25
```

После сверки readback/UI явно восстановите:

```powershell
Invoke-ExtendedTool 'set_setting' @{key='speed_wall'; scope='global'; value=$extendedScalarBefore.value} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_setting' @{key='speed_wall'; scope='global'} | ConvertTo-Json -Depth 25
```

Новый enum: используйте **ключ** текущих options, не подпись. После успешного изменения/readback сверьте UI, затем выполните блок восстановления.

```powershell
$extendedEnumMeta = Get-ExtendedWritable 'retraction_combing' 'global' $null
if ($extendedEnumMeta.type -ne 'enum') { throw 'Не подтверждён enum' }
$extendedEnumMeta.options | ConvertTo-Json -Depth 25
$extendedEnumBefore = Invoke-ExtendedTool 'get_setting' @{key='retraction_combing'; scope='global'}
$extendedEnumNew = Read-Host 'Другой ключ из runtime options retraction_combing'
if ($extendedEnumNew -eq $extendedEnumBefore.value -or $extendedEnumNew -notin $extendedEnumMeta.options.PSObject.Properties.Name) { throw 'Нужен другой разрешённый ключ options' }
Invoke-ExtendedTool 'set_setting' @{key='retraction_combing'; scope='global'; value=$extendedEnumNew} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_setting' @{key='retraction_combing'; scope='global'} | ConvertTo-Json -Depth 25
```

```powershell
Invoke-ExtendedTool 'set_setting' @{key='retraction_combing'; scope='global'; value=$extendedEnumBefore.value} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_setting' @{key='retraction_combing'; scope='global'} | ConvertTo-Json -Depth 25
```

Температура первого слоя: пользователь берёт строковую позицию из проверенной конфигурации своей машины; `"0"` — только пример. get_slicer_state не перечисляет extruders. Первое действие Get-ExtendedWritable ниже — readonly describe: он подтверждает позицию и метаданные либо отказывает до записи. При неизвестной позиции остановитесь, ID не угадывайте. UI-позиция инструментом не меняется. Сохраните также показания другого экструдера/UI, если он есть; после записи сверьте именно выбранный стек.

```powershell
$extendedExtruderId = Read-Host 'Известная строковая позиция экструдера из проверенной конфигурации машины'
$extendedExtMeta = Get-ExtendedWritable 'material_print_temperature_layer_0' 'extruder' $extendedExtruderId
if ($extendedExtMeta.type -ne 'float') { throw 'Не подтверждён float' }
$extendedExtMeta | ConvertTo-Json -Depth 25
$extendedExtBefore = Invoke-ExtendedTool 'get_setting' @{key='material_print_temperature_layer_0'; scope='extruder'; extruder_id=$extendedExtruderId}
$extendedExtNew = [double]::Parse((Read-Host 'Другая допустимая температура первого слоя, с точкой'), [Globalization.CultureInfo]::InvariantCulture)
if ($extendedExtNew -eq $extendedExtBefore.value) { throw 'Нужно другое допустимое значение' }
Invoke-ExtendedTool 'set_setting' @{key='material_print_temperature_layer_0'; scope='extruder'; extruder_id=$extendedExtruderId; value=$extendedExtNew} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_setting' @{key='material_print_temperature_layer_0'; scope='extruder'; extruder_id=$extendedExtruderId} | ConvertTo-Json -Depth 25
```

После подтверждения readback/UI восстановите:

```powershell
Invoke-ExtendedTool 'set_setting' @{key='material_print_temperature_layer_0'; scope='extruder'; extruder_id=$extendedExtruderId; value=$extendedExtBefore.value} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_setting' @{key='material_print_temperature_layer_0'; scope='extruder'; extruder_id=$extendedExtruderId} | ConvertTo-Json -Depth 25
```

Отдельный пункт5D: только после осознанного ручного перехода в cylindrical/5D Spiral с matching backend, без запущенной нарезки. В classic этот блок не выполняйте. Отключённый параметр не включается автоматически; отметьте ограничение.

```powershell
$extended5dState = Invoke-ExtendedTool 'get_slicer_state' @{}
if ($extended5dState.printing_mode -ne 'cylindrical' -or -not $extended5dState.backend.matches_mode) { throw 'Нужен вручную выбранный cylindrical с matching backend' }
$extended5dMeta = Get-ExtendedWritable 'cylindrical_layer_height' 'global' $null
if ($extended5dMeta.type -ne 'float') { throw 'Не подтверждён float' }
$extended5dMeta | ConvertTo-Json -Depth 25
$extended5dBefore = Invoke-ExtendedTool 'get_setting' @{key='cylindrical_layer_height'; scope='global'}
$extended5dNew = [double]::Parse((Read-Host 'Другая допустимая cylindrical_layer_height, с точкой'), [Globalization.CultureInfo]::InvariantCulture)
if ($extended5dNew -eq $extended5dBefore.value) { throw 'Нужно другое допустимое значение' }
Invoke-ExtendedTool 'set_setting' @{key='cylindrical_layer_height'; scope='global'; value=$extended5dNew} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_setting' @{key='cylindrical_layer_height'; scope='global'} | ConvertTo-Json -Depth 25
```

После сверки API/UI восстановите значение **в том же режиме**, затем вручную верните исходный classic и проверьте backend:

```powershell
Invoke-ExtendedTool 'set_setting' @{key='cylindrical_layer_height'; scope='global'; value=$extended5dBefore.value} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_setting' @{key='cylindrical_layer_height'; scope='global'} | ConvertTo-Json -Depth 25
Invoke-ExtendedTool 'get_slicer_state' @{} | ConvertTo-Json -Depth 25
```

## ТЗ 08: преобразования и удаление моделей с Undo

Добавлены get_model_state, move_model, rotate_model, scale_model и delete_model. Формы аргументов/результатов — [API_CONTRACT.md](../docs/API_CONTRACT.md). Используются штатные UM Operations и OperationStack, фактические before/after и мировые mesh-границы. Оси Uranium: Y вверх, X/Z стол. Позиция — абсолютный мировой origin в мм; поворот — относительный вокруг мировой оси через origin, градусы; scale — множители мировых осей (0.01–100), origin сохраняется. Координаты/углы и итоговая геометрия ограничены техническим ±1000000; это не размеры принтера. Непредставимые/вырожденные преобразования отклоняются. Родитель должен иметь единичную мировую матрицу; верхняя преобразованная группа поддерживается целиком, части группы отдельно не адресуются.

С 24.10 перед изменением/удалением нужно выключить только **push free/автоматическое раздвигание** (physics/automatic_push_free, по умолчанию выключено) — иначе явный отказ до push. Включённый Automatic drop down допускается: операция подтверждается сразу, затем физика может опустить модель на стол, о чём сообщает предупреждение. Preferences плагин не меняет. Preview, неактивная платформа, disabled-модель и занятость импорта/нарезки/экспорта/другой записи также запрещают мутацию; чтения доступны.

Успех подтверждается реальной матрицей/позицией и перечитанными габаритами, не одним push. outside_build_area — bool от BuildVolume либо null, если проверка недоступна; выход за пределы возвращает предупреждение, без автопереноса/масштабирования. No-op move/поворот 0 или полный оборот/scale=1 не добавляет историю и не портит native slice. Реальная или неподтверждённая запись инвалидирует старую нарезку/экспорт, Undo не делает старый slice снова актуальным. При таймауте начатая операция может закончиться позже: перечитайте состояние/UI, не повторяйте автоматически. Общий guard остаётся занят до выхода Qt callback. В LLM четыре мутации проходят прежнюю общую классификацию и остановку после ошибки; HTTP/SPA/цикл не менялись.

Ручная приёмка **не выполнена**. После отдельно согласованного обновления всей установленной копии сохраните проект, выберите Hybrid 530 V5.2.4/classic, дождитесь завершения всех job, закройте Preview и выключите обе физические опции в UI. Ниже используются Invoke-Setting и $mcpUrl из сценария ТЗ 01 выше (порт вводится из текущей вкладки). Загружайте отдельную маленькую тестовую деталь, не рабочий проект. Команды выполняйте последовательно со сверкой UI:

```powershell
$transformPath = Read-Host 'Абсолютный путь к маленькому тестовому STL'
$transformImport = Invoke-Setting 'load_model' @{path=$transformPath}
$transformImport | ConvertTo-Json -Depth 15
$transformObjects = Invoke-Setting 'list_models' @{}
$transformObjects | ConvertTo-Json -Depth 15
$transformId = Read-Host 'object_id новой тестовой модели из результата load_model'
if ($transformId -notin @($transformImport.objects.object_id)) { throw 'ID не принадлежит только что импортированной тестовой модели' }
$transformBaseline = Invoke-Setting 'get_model_state' @{object_id=$transformId}
$transformBaseline | ConvertTo-Json -Depth 15

$transformMove = @{x=($transformBaseline.position_mm.x + 10); y=($transformBaseline.position_mm.y + 2); z=$transformBaseline.position_mm.z}
Invoke-Setting 'move_model' @{object_id=$transformId; position_mm=$transformMove} | ConvertTo-Json -Depth 15
Invoke-Setting 'get_model_state' @{object_id=$transformId} | ConvertTo-Json -Depth 15
```

Сверьте origin/габариты/UI; Y API — высота. Выполните штатный Undo в UI и перечитайте state до подтверждения baseline-матрицы и положения. **Не обещается один Undo на один вызов**: OperationStack может объединять соседние операции, включая предыдущее действие пользователя. Если удалился импорт или состояние не восстанавливается ожидаемо — остановитесь и проверьте list_models. Не восстанавливайте обратными углами/Euler или собственной матрицей через API. Далее каждую операцию проверяйте отдельно, возвращаясь штатным Undo к baseline перед следующей:

```powershell
Invoke-Setting 'rotate_model' @{object_id=$transformId; axis='y'; angle_degrees=90} | ConvertTo-Json -Depth 15
Invoke-Setting 'get_model_state' @{object_id=$transformId} | ConvertTo-Json -Depth 15
# Пауза: сверить UI/матрицу/габариты, выполнить Undo в UI и подтвердить baseline.
```

После подтверждённого Undo:

```powershell
Invoke-Setting 'scale_model' @{object_id=$transformId; factors=@{x=1.1; y=1; z=1.1}} | ConvertTo-Json -Depth 15
Invoke-Setting 'get_model_state' @{object_id=$transformId} | ConvertTo-Json -Depth 15
# Пауза: сверить фактический scale/габариты и неизменный origin, Undo в UI, подтвердить baseline.
```

После восстановления удалите **только указанную тестовую модель**, проверьте отсутствие ID, сохранность соседей и исходного STL. Затем Undo в UI и новый list_models:

```powershell
Invoke-Setting 'delete_model' @{object_id=$transformId} | ConvertTo-Json -Depth 15
Invoke-Setting 'list_models' @{} | ConvertTo-Json -Depth 15
Invoke-Mcp 'tools/call' @{name='get_model_state'; arguments=@{object_id=$transformId}} | ConvertTo-Json -Depth 15
# Ожидается model_not_found. Пауза: выполнить штатный Undo в UI.
```

После Undo выполните list_models снова: объект получает новый ID, старый не переиспользуется. Группу проверяйте отдельным повтором по её верхнему ID; служебные узлы/дети не должны появиться отдельными целями. Дополнительно включите Automatic drop down, попробуйте поднять origin и подтвердите отказ без изменения, затем восстановите обе исходные Preferences вручную. Выход за build area проверяйте только на тестовой детали с допустимыми техническими координатами: ожидаются outside=true/предупреждение и ручной Undo, не обещание печати. При прежнем актуальном slice реальная мутация делает status.valid=false и result stale. Ничего не печатайте; сохраните ответы/скриншоты для ведущего.

Проверены подмены и отдельно классы Uranium с инертными сигналами, **не установленный STE/Qt/физика/UI**. Отчёт — [TASK_08_REPORT.md](../docs/reports/TASK_08_REPORT.md). ТЗ 03–08 и агент требуют своей native-приёмки. Расстановка добавлена отдельно в ТЗ 09 ниже; custom-профили/5D не реализованы.

## ТЗ 09: штатная расстановка classic

arrange_models {} создаёт собственный native ArrangeObjectsJob для всех верхних моделей активной платформы и возвращает arrangement_id/queued; get_arrangement_status {arrangement_id} опрашивает реальный job и after. Одно принятие не означает завершение. Два native finished обрабатываются идемпотентно после isFinished=true/isRunning=false; getResult=None допустим, progress остаётся null, потому что Job.progress этим job не публикуется. Свои чтения/создание/подтверждение — Qt, сам native run — штатная JobQueue, как Arrange All. Плагин не реализует свой упаковщик.

Общий guard держится до фактического выхода job; обычные чтения/status доступны. Бюджет 120 сек; timeout/неопределённый start не отменяет job, не снимает защиту до его выхода и не запускает повтор. Поздний выход не превращает failed в completed, частичные фактические after сохраняются. При зависании нужен перезапуск после сохранения возможного состояния. Shutdown не имитирует отмену: timer останавливается, необходимые job/nodes/finished сохраняются до выхода, если ещё работает Qt event loop. До native start проверяется также остаток обычного Qt-бюджета, чтобы просроченная подготовка не запустила job позже.

**completed — завершение job, не подтверждение полного размещения.** Native не возвращает found_solution_for_all. Непоместившаяся модель ставится в X=200, который может быть внутри большого стола. Поэтому all_fit=true не выдаётся: false при подтверждённом outside=true; иначе null, включая all outside=false. Без перемещения подтверждённый false не теряется. Перечитайте реальные after/границы и штатное сообщение/UI; плагин не доказывает отсутствие всех пересечений. Если матрицы вообще не изменились, есть предупреждение о неподтверждённом применении операции. valid — актуальность сохранённого снимка, а не пригодность печати; после изменения/следующей расстановки false.

Нужны Hybrid 530 V5.2.4/classic и соответствующий backend, auto_slice=false и обе physics-опции=false, свободные импорт/backend/export, закрытый Preview, enabled/selectable верхние модели/группы под единичными мировыми родителями. Все печатные части группы — на активной платформе. Нужны готовые native convex hull/head hull и BuildVolume. Модель шире/глубже стола отклоняет весь запуск, без тихого пропуска. Пустая платформа — empty_scene, job не создаётся. Другие платформы/служебные узлы не передаются native job. min_offset=max(getEdgeDisallowedSize()+2,8); штатное опускание на стол и удаление ZOffset допустимы, отдельная PlatformPhysics запрещена. Preferences не меняются.

Ручная приёмка **не выполнена**. После отдельно согласованного обновления всего плагина: сохраните проект, выключите auto-slice/Automatic drop down/push free в UI, загрузите 2–3 небольших STL вручную или MCP-командами ТЗ 04 на одной classic-платформе. Дождитесь готовности hull, не двигайте модели и не меняйте платформу параллельно. Используются текущий порт, Invoke-Setting и Invoke-Mcp из сценария ТЗ 01:

```powershell
$arrangeBeforeModels = Invoke-Setting 'list_models' @{}
$arrangeBeforeModels | ConvertTo-Json -Depth 15
$arrangeBefore = @($arrangeBeforeModels.models | ForEach-Object {
    Invoke-Setting 'get_model_state' @{object_id=$_.object_id}
})
$arrangeBefore | ConvertTo-Json -Depth 20
$arrangeStart = Invoke-Setting 'arrange_models' @{}
$arrangeId = $arrangeStart.arrangement_id
$arrangeStart | ConvertTo-Json -Depth 20
$arrangeClientDeadline = (Get-Date).AddSeconds(150)
do {
    $arrangeStatus = Invoke-Setting 'get_arrangement_status' @{arrangement_id=$arrangeId}
    $arrangeStatus | ConvertTo-Json -Depth 25
    if ($arrangeStatus.state -in @('completed','failed')) { break }
    if ((Get-Date) -ge $arrangeClientDeadline) { throw 'Клиентский timeout: native job не отменён. Сохраните ID, проверьте UI; не запускайте повтор' }
    Start-Sleep -Seconds 1
} while ($true)
if ($arrangeStatus.state -eq 'failed') { throw ($arrangeStatus | ConvertTo-Json -Depth 25 -Compress) }
Invoke-Setting 'list_models' @{} | ConvertTo-Json -Depth 15
```

Сверьте каждый object_id/before/after, реальные X/Y/Z/габариты, опускание на стол и сохранность моделей других платформ. progress:null — ожидаемо. all_fit:null не заменяйте успехом; проверьте штатное сообщение и вид сцены. outside=true/all_fit=false означает неподходящее размещение. Сохраните вывод/скриншоты. Выполните штатный Undo в UI до подтверждённого before, учитывая возможное объединение native operations; после Undo перечитайте get_model_state по тем же живым ID. valid прежнего снимка должен стать false. Для дальнейшей проверки снова явно расставьте/сверьте сцену, затем отдельно используйте сценарий ТЗ 05 для новой classic-нарезки — прежний slice остаётся stale. Ничего не печатайте и не запускайте реальные LLM-запросы автоматически. Восстановление physics/auto-slice — вручную после теста. Отчёт — [TASK_09_REPORT.md](../docs/reports/TASK_09_REPORT.md).

## Настройка LLM и асинхронный чат — интеграция b9c0190

В меню **AI Assistant -> LLM Settings** задайте Base URL OpenAI-совместимого Chat Completions API, имя модели и ключ; значений по умолчанию нет. Клиент добавляет /chat/completions к URL, если путь ещё не указан. TLS проверяется стандартной библиотекой, redirects с Authorization запрещены. Не помещайте ключ в URL. Настройки хранятся в Preferences ai_assistant/base_url, model, api_key через тот же QtBridge, сохранение подтверждается перечитыванием. Парольное поле при открытии пустое: пусто при Save — сохранить прежний ключ, отдельный флажок — удалить. При ошибке/таймауте кеш считается неподтверждённым; перечитайте через повторное открытие диалога, не повторяйте изменение автоматически. Используется Preferences по требованиям проекта. Браузеру доступны лишь configured/has_key, не значения настроек.

**Open chat** открывает временный UI коллеги b9c0190, адаптированный к [контракту](../docs/API_CONTRACT.md). Сообщение принимается HTTP 202, tools и ответ приходят polling через /api/events. История/карточки восстанавливаются из session snapshot при обновлении страницы; после перезапуска плагина память диалога сбрасывается. Один активный запрос для общей сцены; бюджеты 20 LLM / 40 tools / 480 сек, сеть до 120 сек с учётом остатка (изменено при полировке 24.10). При неопределённой мутации цикл останавливается и сообщает о необходимости проверки UI; автоповтора нет. При обрыве/5xx сохраняется исходный client_message_id, явный повтор отправляет то же тело. Вопрос «Что умеешь?» допускает только чтения. Диагностический load_model(path) и assistant_status исключены из LLM, остальные схемы — существующий единый ToolRegistry. Приложенную модель агент может импортировать через load_model_file по file_id; прямой MCP-вызов path сохранён для диагностики.

Статус «LLM настроена» не подтверждает связь с провайдером. Роль2D автоматически читает собственные принятые slice/export ID после idle/ошибки/лимита LLM; время/материал получает прежним result API из STE, готовую download-карточку — через2C. Это не сканирование ручных/MCP-запусков и не автоматический start/export. Завершение ответа агента не завершает engine. Вложения и HTTP-скачивание добавлены в2B/2C ниже; 5D-реализация ТЗ10/11 требует native-приёмки; преобразования доступны с ограничениями ТЗ 08 выше. Старый /api/message остаётся отдельным legacy-демо, SPA его не вызывает.

Проверки разработчика без провайдера/ключа:

```powershell
python -m unittest discover -s tests -v
node --check plugin/AIAssistant/web/app.js
node tests/test_agent_web.js
git diff --check
```

Отчёт — [AGENT_INTEGRATION_REPORT.md](../docs/reports/AGENT_INTEGRATION_REPORT.md). После согласованного обновления установленной копии нужна отдельная native-приёмка: register/shutdown, Qt-диалог и сохранение после перезапуска; вопрос без изменений; чтение слоя, явная допустимая запись/сверка UI/восстановление; затем подготовленная небольшая модель classic и актуальные slice/result/экспорт/Preview/повторное открытие 3MF. Команды ТЗ 01–07 выше сохранены. В этом заходе провайдер не вызывался, ключи не вводились, установленный плагин не менялся. ТЗ 03–07 и LLM-сценарий вручную не приняты.

## Роль 2A: загрузка файла

POST `/api/files` принимает один multipart file и возвращает 201 с file_id, именем, kind, размером и MIME. Модель — до 50 MiB, изображение — до 5 MiB, суммарно 250 MiB на процесс; расширения JPG/JPEG/PNG/STL/OBJ/3MF. Байты проверяются и сохраняются вне Qt в приватном временном каталоге, ID действуют до остановки сервиса. GET по upload-ID отсутствует; native download использует отдельный реестр2C. Сам upload не импортирует модель; передача агенту и минимальный picker подключены ролью2B ниже.

Пример ручного запроса к уже запущенному dev-серверу или текущему порту плагина (сам по себе слайсинг/LLM не запускает):

```powershell
$uploadPort = [int](Read-Host 'Текущий порт из адреса чата')
$uploadPath = Read-Host 'Абсолютный путь тестового STL/OBJ/3MF/JPG/PNG'
$uploadForm = 'file=@"{0}"' -f $uploadPath.Replace('\', '/')
curl.exe --include --max-time 30 --form $uploadForm "http://127.0.0.1:$uploadPort/api/files"
```

Ожидается 201 и JSON без локального пути; повторная загрузка получает новый ID. Файл для примера должен иметь обычное имя без кавычек. Для браузерного клиента используйте FormData из [контракта](../docs/API_CONTRACT.md), не задавая boundary вручную. Проверки и ограничения — [ROLE_02A_REPORT.md](../docs/reports/ROLE_02A_REPORT.md). Установленная копия не обновлялась; native-приёмка ТЗ 03/04 остаётся открыта.

## Роль 2B: вложения в чате

Выберите до5 JPG/JPEG/PNG/STL/OBJ/3MF, дождитесь загрузки каждого файла. Уберите ошибочные/лишние файлы кнопкой «Убрать», затем отправьте текст. Суммарно изображения сообщения — до10MiB. Фото передаются как фактический base64 image_url, модель — метаданными и инструментом `load_model_file {file_id}` через существующий adapter/QtBridge. Ключ задаётся только в Qt-настройках. Ошибка vision провайдера не подменяется анализом.

Имена/метаданные восстанавливаются из snapshot. При обрыве/5xx сохраняется полное pending-тело, включая порядок IDs и client_message_id; явный повтор после обновления страницы не перезагружает файлы и не повторяет уже принятое действие. «Убрать» исключает файл из сообщения, физический файл остаётся в ограниченной квоте до закрытия сервиса. После перезапуска процесса IDs неизвестны. Скачивание native-экспортов добавлено2C ниже, upload не раздаётся.

Для отдельной ручной приёмки после согласованного обновления установленной копии и настройки провайдера: приложить небольшой STL и PNG, отправить «Загрузи эту модель и объясни фото». Сверить tool load_model_file, подтверждённые objects/границы и появление объекта в STE; затем обновить страницу и сверить имена вложений/историю без повторного импорта. Upload201/принятие202/текст LLM не доказывают импорт. При ошибке/timeout не повторять мутацию; перечитать list_models/UI. В этом заходе этот сценарий не запускался.

Ограничение lifetime: launched reader удерживает свой файл до фактического fileCompleted даже после timeout/закрытия плагина; при отсутствии сигнала возможен временный остаток. Сцена может показывать серверное имя файла; переименование объектов не добавлено. Итоговые проверки/границы — [ROLE_02B_REPORT.md](../docs/reports/ROLE_02B_REPORT.md).

## ТЗ 10: цилиндрическая нарезка (5D Spiral)

`start_slicing` поддерживает classic/CuraEngineBackend и cylindrical/CLIParserBackend, проверяя регистрацию для режима; cylindrical_full пока отклоняется. Схемы/status/result/экспорт прежние, printing_mode захватывается из запуска. Весь штатный конвейер запускает forceSlice: prep → Glicer → ProcessCliJob → CLIParser. ProcessCliJob.Finished, выход Glicer, progress1 и Done различаются; completed требует собственного финального parser callback, отложенного readback окончательного G-code и подтверждённых оценок PrintInformation. Постоянный процесс парсера может оставаться живым после успеха. Ошибка сокета/процесса/стадии и отсутствие финала дают ошибку/timeout, без расчёта своих оценок.

Во время нарезки не меняйте проект/режим и не запускайте параллельный Slice в UI. Auto-slice заранее выключить вручную; одна непустая активная платформа, без G-code preview. Timeout180с не отменяет engine; guard удерживается до выхода стадий. При close CLI наблюдает свои jobs/Glicer на Qt ещё до30с без scene/PI/новых engine-действий; затем наблюдение прекращается, неизвестный исход/guard сохраняются, нужен перезапуск. Если event loop исчез, cleanup не гарантируется.

Ручные команды ниже **не выполнялись**. После отдельно согласованного обновления установленной копии сохраните проект; выберите Hybrid530 V5.2.4 и cylindrical на пустой тестовой сцене. Если нет активации/engine или модель не подходит, остановитесь и запишите отказ, не обходите его. Ниже самостоятельный MCP-вызов к текущему порту чата. При кодах ошибок/timeout не повторяйте мутацию автоматически.

```powershell
$spiralPort = [int](Read-Host 'Порт из адреса открытого чата')
$spiralMcpUrl = "http://127.0.0.1:$spiralPort/mcp"
function Invoke-SpiralTool($name, $arguments) {
    $rpc = @{jsonrpc='2.0'; id=10; method='tools/call'; params=@{name=$name; arguments=$arguments}} | ConvertTo-Json -Depth 30 -Compress
    $response = Invoke-RestMethod $spiralMcpUrl -Method Post -ContentType 'application/json; charset=utf-8' -TimeoutSec 15 -Body ([Text.Encoding]::UTF8.GetBytes($rpc))
    if ($null -ne $response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $envelope = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $envelope.ok) { throw ($envelope | ConvertTo-Json -Depth 30 -Compress) }
    $envelope.data
}
$spiralState = Invoke-SpiralTool 'get_slicer_state' @{}
if ($spiralState.printing_mode -ne 'cylindrical' -or -not $spiralState.backend.matches_mode) {
    throw 'Выберите cylindrical в UI и проверьте зарегистрированный CLIParserBackend'
}
$spiralPath = Read-Host 'Абсолютный путь небольшой подходящей модели STL/OBJ/3MF'
$spiralLoaded = Invoke-SpiralTool 'load_model' @{path=$spiralPath}
$spiralLoaded | ConvertTo-Json -Depth 25
Invoke-SpiralTool 'describe_settings' @{scope='global'} | ConvertTo-Json -Depth 25
# Пауза: сверить модель/границы/настройки с UI, без preview.
$spiralStart = Invoke-SpiralTool 'start_slicing' @{}
$spiralId = $spiralStart.slice_id
if ([string]::IsNullOrEmpty($spiralId)) { throw 'Нет slice_id; перечитайте UI без повторного start' }
$spiralWatch = [Diagnostics.Stopwatch]::StartNew()
do {
    $spiralStatus = Invoke-SpiralTool 'get_slicing_status' @{slice_id=$spiralId}
    $spiralStatus | ConvertTo-Json -Depth 25
    if ($spiralStatus.state -eq 'failed') { throw ($spiralStatus.error | ConvertTo-Json -Compress) }
    if ($spiralStatus.state -eq 'completed') { break }
    if ($spiralWatch.Elapsed.TotalSeconds -ge 195) { throw 'Клиентский timeout; job не отменён, не повторяйте start' }
    Start-Sleep -Seconds 1
} while ($true)
$spiralResult = Invoke-SpiralTool 'get_slicing_result' @{slice_id=$spiralId}
$spiralResult | ConvertTo-Json -Depth 25
if ($spiralResult.printing_mode -ne 'cylindrical' -or $spiralResult.backend_id -ne 'CLIParserBackend') { throw 'Неверный контекст результата' }
```

После сверки времени/расхода с UI выполните export_gcode/save_project_3mf и polling get_export_status по сценарию ТЗ06, подставив **этот** spiralId. Открывайте готовый G-code в Preview и 3MF именно как workspace-проект, сравнивайте cylindrical/машину/материалы/выбранные настройки/геометрию; открытие может сделать прежний slice stale. Сохраните реальные ответы/скриншоты. Затем отдельно повторите classic-сценарий ТЗ05/06 как регрессию на сохранённой тестовой сцене. Это не физическая печать. ТЗ03/04 и все native-результаты остаются открыты. Отчёт и границы — [TASK_10_REPORT.md](../docs/reports/TASK_10_REPORT.md).

## ТЗ 11: составной полный 5D

Добавлен cylindrical_full/CylindricalBackend, при сохранённых classic/cylindrical. Это штатный внешний forceSlice: prep → основание → Cura → Glicer/ProcessCliJob → parser. Только финальный outer parser Done и отложенный readback окончательного G-code/PI подтверждают completed; child Done, Cura Processing0.5, enumFinished и progress1 недостаточны. У полного backend нет собственного parser _process/_socket: требуются зарегистрированные/инициализированные свободные child Cura/CLI sockets; если их нет, будет отказ до запуска. Плагин их вручную не создаёт. Basement имеет result=None; его не проверяют по enum prep/ProcessCliJob. Оценки читает только подтверждённый outer→PrintInformation; своими формулами они не заменяются.

Законные reset childsocket/process наблюдаются после собственного finished соответствующей стадии; поздние подмены — ошибка. Guard сохраняет активную свою фазу и после outer._slicing=false. Timeout/shutdown не отменяют native job; после close только readonly стадийное наблюдение до30с без scene/PI/новых engine-действий, затем неизвестный исход/guard и перезапуск. Приёмка/ограничения — [TASK_11_REPORT.md](../docs/reports/TASK_11_REPORT.md).

Самостоятельные PowerShell-команды ниже подготовлены, **не запускались**. После согласованного обновления установленной копии сохраните проект, выберите Hybrid530 и cylindrical_full на пустой тестовой сцене, выключите auto-slice, без preview. Сначала загрузите небольшую подходящую модель через UI или прежний load_model, подтвердите list_models/габариты и настройки. Отсутствие активации/движка/подходящей модели записать как блокер, без обхода.

```powershell
$fullPort = [int](Read-Host 'Текущий порт чата')
$fullUrl = "http://127.0.0.1:$fullPort/mcp"
function Invoke-FullTool($name, $arguments) {
    $rpc = @{jsonrpc='2.0'; id=11; method='tools/call'; params=@{name=$name; arguments=$arguments}} | ConvertTo-Json -Depth 30 -Compress
    $response = Invoke-RestMethod $fullUrl -Method Post -ContentType 'application/json; charset=utf-8' -TimeoutSec 15 -Body ([Text.Encoding]::UTF8.GetBytes($rpc))
    if ($response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $result = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $result.ok) { throw ($result | ConvertTo-Json -Depth 30 -Compress) }
    $result.data
}
$fullState = Invoke-FullTool 'get_slicer_state' @{}
if ($fullState.printing_mode -ne 'cylindrical_full' -or -not $fullState.backend.matches_mode) { throw 'Сверьте полный режим/регистрацию в UI' }
Invoke-FullTool 'list_models' @{} | ConvertTo-Json -Depth 30
Invoke-FullTool 'describe_settings' @{scope='global'} | ConvertTo-Json -Depth 30
$fullStart = Invoke-FullTool 'start_slicing' @{}
$fullId = $fullStart.slice_id
if ([string]::IsNullOrEmpty($fullId)) { throw 'Нет slice_id; проверьте UI без повторного запуска' }
$fullWatch = [Diagnostics.Stopwatch]::StartNew()
do {
    $fullStatus = Invoke-FullTool 'get_slicing_status' @{slice_id=$fullId}
    $fullStatus | ConvertTo-Json -Depth 30
    if ($fullStatus.state -eq 'failed') { throw ($fullStatus.error | ConvertTo-Json -Compress) }
    if ($fullStatus.state -eq 'completed') { break }
    if ($fullWatch.Elapsed.TotalSeconds -ge 195) { throw 'Клиентский timeout; это не отмена, не повторяйте start' }
    Start-Sleep -Seconds 1
} while ($true)
$fullResult = Invoke-FullTool 'get_slicing_result' @{slice_id=$fullId}
$fullResult | ConvertTo-Json -Depth 30
if ($fullResult.printing_mode -ne 'cylindrical_full' -or $fullResult.backend_id -ne 'CylindricalBackend') { throw 'Неверный контекст результата' }
```

Сверьте оценки с UI, затем выполните существующий сценарий ТЗ06 export_gcode/save_project_3mf/get_export_status с **этим fullId**. Проверьте реальный G-code в Preview и повторно откройте3MF как workspace, сверив полный режим/машину/геометрию/настройки и материалы. После сохранения тестовой работы отдельно повторите classic и cylindrical как регрессию. JSON/файлы/скриншоты сохранить для ведущего. Принтер не запускать; ручная приёмка ТЗ03/04 и новых режимов остаётся открыта.

## ТЗ12: пользовательские профили

Добавлены `list_custom_profiles {}`, `create_custom_profile {name}`, `select_custom_profile {profile_id}`, `save_custom_profile {profile_id}`. Create переносит текущие old qualityChanges+userChanges глобального/экструдерных стеков в новую группу и отдельно выбирает её; очищенные overrides подтверждаются readback. Save — только в активную полную writable группу, без перезаписи встроенных профилей. Коллизия имени — явный отказ. Select сохраняет user overrides; прежний builtin select_profile не изменён и активный custom автоматически не сбрасывает.

memory_verified/disk_verified в persistence различаются: для create/save подтверждаются значения и соответствие QC-файлов native serialize, для select файлы не проверяются. restart_verified всегдаfalse до ручного прогона. Сохранение QC-файлов не является принудительным сохранением активного выбора/всех userChanges/Preferences. Ни void, ни True/dirty сами по себе не подтверждают disk. При ошибке partial-состояние остаётся, error.actual показывает последнюю подтверждённую фазу; не повторяйте создание автоматически.

Native LocalContainerProvider/SaveFile пишет синхронно на Qt и может ждать ОС. Worker ожидает ограниченно; при running-timeout guard сохраняется до выхода native callback, без дальнейших save после abandonment. Собственная ограниченная проверка файлов — вне Qt; raw значения/пути браузеру не выдаются. Отчёт — [TASK_12_REPORT.md](../docs/reports/TASK_12_REPORT.md).

PowerShell ниже подготовлен, **не запускался**. После согласованного обновления установленной копии сохраните работу, выберите тестовую машину/сцену без нарезки/импорта/экспорта и сохраните исходные настройки/UI. Профиль должен быть временным и уникальным. Все ошибки останавливают последовательность, без повторов и автоматического отката.

```powershell
$profilePort = [int](Read-Host 'Текущий порт чата')
$profileUrl = "http://127.0.0.1:$profilePort/mcp"
function Invoke-CustomTool($name, $arguments) {
    $rpc = @{jsonrpc='2.0'; id=12; method='tools/call'; params=@{name=$name; arguments=$arguments}} | ConvertTo-Json -Depth 30 -Compress
    $response = Invoke-RestMethod $profileUrl -Method Post -ContentType 'application/json; charset=utf-8' -TimeoutSec 15 -Body ([Text.Encoding]::UTF8.GetBytes($rpc))
    if ($response.error) { throw ($response.error | ConvertTo-Json -Compress) }
    $result = $response.result.content[0].text | ConvertFrom-Json
    if ($response.result.isError -or -not $result.ok) { throw ($result | ConvertTo-Json -Depth 30 -Compress) }
    $result.data
}
$profilesBefore = Invoke-CustomTool 'list_custom_profiles' @{}
$profilesBefore | ConvertTo-Json -Depth 30
$originalCustom = $profilesBefore.profiles | Where-Object active | Select-Object -First 1
$newProfile = Invoke-CustomTool 'create_custom_profile' @{name=('AI_TEST_' + [guid]::NewGuid().ToString('N'))}
$newProfile | ConvertTo-Json -Depth 30
Invoke-CustomTool 'set_setting' @{key='layer_height'; scope='global'; value=0.24} | ConvertTo-Json -Depth 30
$savedProfile = Invoke-CustomTool 'save_custom_profile' @{profile_id=$newProfile.profile_id}
$savedProfile | ConvertTo-Json -Depth 30
if (-not $savedProfile.persistence.disk_verified) { throw 'Файлы не подтверждены' }
if ($originalCustom) { Invoke-CustomTool 'select_custom_profile' @{profile_id=$originalCustom.profile_id} | ConvertTo-Json -Depth 30 }
# Если исходный профиль builtin, переключиться на него штатным UI (builtin tool custom не сбрасывает).
Invoke-CustomTool 'select_custom_profile' @{profile_id=$newProfile.profile_id} | ConvertTo-Json -Depth 30
Invoke-CustomTool 'get_setting' @{key='layer_height'; scope='global'} | ConvertTo-Json -Depth 30
```

Сверить выбранную группу/ID/слой и материалы экструдеров в UI. Сохранить profile_id/имя/ответы отдельно, перезапустить STE штатно, взять **новый порт** и снова определить функцию. Через list_custom_profiles найти тот же ID, выбрать его и прочитать layer_height=0.24, проверить прежние global/extruder перенесённые значения. Только этот прогон подтвердит восстановление после рестарта. Вернуть исходный профиль/настройки штатным UI либо его custom ID и сверить; временный профиль удаляется пользователем через UI после приёмки, инструмент удаления не добавлен. Рестарт/настоящая запись в установленном STE здесь не выполнялись.

## Роль2C: скачивание native-экспорта

После выполненного агентом либо observer его собственного экспорта get_export_status с completed/valid=true карточка native-артефакта получает стабильные file_id/download_url. GET `/api/files/{file_id}` перепроверяет актуальный slice через QtBridge, затем вне Qt открывает доверенный обычный файл и сверяет принятую идентичность/размер; передача блоками до64KiB без ServiceLock/engine guard. Upload-хранилище не используется, accepted exports не удаляются при его очистке. Dev сообщает downloads=false, plugin=true означает наличие механизма, а не готовность engine/лицензии. Наблюдение своих запусков добавлено ролью2D ниже; принятая копия296 его не имеет.

Неизвестный/upload/исчезнувший ID —404; stale/изменённый файл —409; закрытие/недоступный Qt —503 с JSON до начала тела. Во время передачи ошибка закрывает соединение без JSON. Close запрещает новые GET, уже открытый поток может закончиться. Fingerprint dev/ino/size/mtime_ns не является криптографической защитой от внешнего редактора с сохранением mtime; изменения после начала передачи её не отзывают. Download-ID не переживает рестарт, файлы остаются в кэше STE. Известный stale скрывает ссылку; ручное изменение обнаруживается при следующем чтении/GET.

Результат изолированных проверок:296 Python/10 JS OK (база286/8 сохранена). Проверены bytes/MIME обоих типов, headers/ошибки/close/streaming и snapshot/410/дедупликация. Fake native writer/ScriptedLLM не подтверждают STE/провайдер/Preview/reopen. Отчёт — [ROLE_02C_REPORT.md](../docs/reports/ROLE_02C_REPORT.md).

Самостоятельный ручной сценарий ниже **не выполнялся**. После отдельно согласованного обновления установленной копии и настройки провайдера сохранить проект и пройти прежний native-сценарий ТЗ05/06 (либо10/11): завершённая актуальная нарезка → штатный export → polling get_export_status. Для каждого вида (G-code и3MF) взять собственный completed export_id. Ошибка/timeout останавливает сценарий; автоматического повтора writer нет. Затем в этом же чате явно попросить агента: «Прочитай только get_export_status для export_id …». Дождаться tool_finished и карточки; прямой MCP tools/call сам download URL не регистрирует. Кнопка «Скачать» должна сохранить тот же файл. Обновить вкладку: карточка/ID сохраняются, повторное чтение не дублирует её.

Следующие команды проверяют уже полученную ссылку без нового LLM-запроса и без повторного экспорта. Session ID взять в DevTools → Application → Local Storage → ste_ai_session_id текущего origin, не старого порта.

```powershell
$downloadPort = [int](Read-Host 'Текущий порт открытого чата')
$downloadOrigin = "http://127.0.0.1:$downloadPort"
$downloadSession = Read-Host 'ste_ai_session_id текущего чата'
$downloadExportId = Read-Host 'Подтверждённый completed export_id'
$downloadRpc = @{jsonrpc='2.0'; id=13; method='tools/call'; params=@{name='get_export_status'; arguments=@{export_id=$downloadExportId}}} | ConvertTo-Json -Depth 20 -Compress
$downloadResponse = Invoke-RestMethod "$downloadOrigin/mcp" -Method Post -ContentType 'application/json; charset=utf-8' -TimeoutSec 15 -Body ([Text.Encoding]::UTF8.GetBytes($downloadRpc))
if ($downloadResponse.error) { throw ($downloadResponse.error | ConvertTo-Json -Compress) }
$downloadEnvelope = $downloadResponse.result.content[0].text | ConvertFrom-Json
if ($downloadResponse.result.isError -or -not $downloadEnvelope.ok) { throw ($downloadEnvelope | ConvertTo-Json -Depth 20 -Compress) }
$downloadNative = $downloadEnvelope.data
if ($downloadNative.state -ne 'completed' -or -not $downloadNative.valid) { throw 'Нет актуального completed native-экспорта' }
$downloadSnapshot = Invoke-RestMethod "$downloadOrigin/api/sessions/$downloadSession" -TimeoutSec 15
$downloadCard = @($downloadSnapshot.artifacts | Where-Object { $_.artifact_id -eq $downloadNative.artifact.artifact_id })
if ($downloadCard.Count -ne 1 -or -not $downloadCard[0].valid -or $downloadCard[0].download_url -notmatch '^/api/files/[A-Za-z0-9_-]{1,128}$') { throw 'Нет подтверждённой ссылки; агент должен прочитать статус этого экспорта' }
$downloadTarget = Read-Host 'Абсолютный путь НОВОГО файла для скачивания (.gcode либо .3mf)'
if (-not [IO.Path]::IsPathRooted($downloadTarget) -or (Test-Path -LiteralPath $downloadTarget)) { throw 'Укажите новый абсолютный путь без перезаписи' }
curl.exe --fail --show-error --max-time 120 --output $downloadTarget "$downloadOrigin$($downloadCard[0].download_url)"
if ($LASTEXITCODE -ne 0) { throw 'Скачивание не подтверждено; частичный файл не считать результатом' }
$downloadSource = Join-Path $downloadNative.local_directory $downloadNative.artifact.file_name
if ((Get-Item -LiteralPath $downloadTarget).Length -ne $downloadNative.artifact.size_bytes) { throw 'Размер отличается' }
if ((Get-FileHash -LiteralPath $downloadTarget -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $downloadSource -Algorithm SHA256).Hash) { throw 'Байты отличаются от native-файла' }
Get-Item -LiteralPath $downloadTarget | Select-Object Name,Length
```

Повторить для второго вида с его export_id и новым целевым путём. Скачанный G-code открыть штатным Preview, 3MF — как workspace-проект; сверить геометрию/режим/машину/настройки/материалы. Открытие проекта может сделать прежний slice stale: после сохранения доказательств проверить отказ409 старой ссылки и известный valid=false при следующем чтении статуса, без повторного writer. Сохранить ответы/размеры/хеши/скриншоты для ведущего. Установка, провайдер, Preview, повторное открытие3MF и ТЗ03/04 этим заходом не принимались; физическую печать не запускать.

## Роль2D: автоматическое чтение собственных операций

После успешного start_slicing/export_gcode/save_project_3mf агента один ленивый worker процесса продолжает чтение status/result примерно раз в1с, даже когда ответ LLM завершён/ошибочен/достиг лимита. Только подтверждённые ID и их session_id; максимум две active-записи (slice/export), без LLM-запросов, start/retry/export цепочек и сканирования исторических IDs. Прямой MCP/ручной UI автоматически не регистрируются. Готовый экспорт получает download-карточку через прежний2C resolver. Native pipeline/writers/guard/бюджеты и лимиты LLM8/16/120 прежние.

Две строки UI восстанавливаются из snapshot: slice с подтверждёнными оценками и один последний собственный export. export_progress — реальный снимок, не фальшивый tool_finished; pending не является артефактом. observation.watching/finished/stopped отделяет состояние наблюдения от состояния engine. При4 ошибках чтения после backoff1/2/4с — observation_interrupted и прекращение опроса, без выдуманного failed/cancel. Ранний slice_not_ready/qt_timeout от result не снимает живое наблюдение. Известная мутация/новый ID отсекают поздний ответ по поколению; prewrite busy (включая switch_in_progress) сохраняет observer. После terminal постоянного наблюдения внешних изменений нет, GET скачивания заново проверяет актуальность. Close не join-ит ожидающий Qt worker и не отменяет engine.

capabilities.background_slice_events=true только в plugin, в dev=false; это возможность своих наблюдений, не доказательство native-готовности. Принятая копия `tmp/acceptance/baseline_296/AIAssistant`/manifest сохранена (29 хешей совпадают), установленный плагин не обновлялся. Подробности/проверки — [ROLE_02D_REPORT.md](../docs/reports/ROLE_02D_REPORT.md).

Отдельный ручной сценарий новой версии **не выполнялся**: после согласованного обновления/настройки провайдера явно запустить небольшую нарезку агентом, дождаться idle и без нового сообщения проверить изменение прогресса→completed→оценки STE; затем явно запустить writer агентом и дождаться export_progress→artifact_ready/скачивания без следующего LLM-call. Сверить UI/Preview/reopen по прежним командам. Проверить reload/cursor recovery и ошибку наблюдения без автоповтора. Копия296 текущего ручного прогона имеет прежние снимки; её результаты не подтверждают2D. Никаких реальных LLM/engine/принтера этим заданием не запускалось, ТЗ03/04 и native-приёмка остаются открыты.

## Роль2G: сохранённые диалоги

В сворачиваемом блоке **«Диалоги»** показаны названия/даты и текущий разговор. Выбор строки открывает её, «Новый диалог» начинает отдельный контекст, «Удалить» требует подтверждения. Переключение меняет только переписку: сцена STE общая для всех чатов и историей не восстанавливается. Название берётся из первого сообщения, без дополнительного LLM. Длинные названия переносятся, кнопки доступны с клавиатуры; на узком экране блок изначально свёрнут. Markdown/закрытые технические детали, вложения, прогресс и скачивание текущего процесса сохранены.

Архив — локальные JSON в `Resources.getDataStoragePath()/ai_assistant/chats`, вне исходников плагина. Путь берётся через штатный API наQt; диск читает/пишет отдельный worker. До20 диалогов,100 архивных сообщений/кратких итогов,512КиБ на разговор/10МиБ суммарно (включая резерв16КиБ под индекс). При ограничении старой истории виден notice; переполнение/порча/недоступность дают ошибку, файлы не подменяются пустыми. Временно для атомарной замены нужно место ещё под один файл. Просмотр списка не загружает весь архив в контекст модели; посещённые разговоры сохраняют ограниченное прежними лимитами RAM-состояние до закрытия процесса, чтобы не терять его идемпотентность.

Статусы **«Сохранение…»**, **«Сохранено на этом компьютере»**, **«Сохранение не подтверждено»** различаются. Только saved означает подтверждённую запись revision, ни202, ни ответ LLM/idle её не заменяют. При закрытии Qt не ждёт диска: helper внеQt даёт до2с для финальной записи. Обычное закрытие тестового процесса проверено; принудительное завершение ОС/зависший диск могут потерять последнюю неподтверждённую версию. Это не гарантия fsync. Архив рассчитан на один экземпляр STE с этим каталогом; JSON не шифруется.

В архиве только выбранные тексты/безопасные краткие результаты, без Preferences/ключа/headers/rawproviderrequests, байтов/base64/путей и operationalIDs/живых ссылок. Известный ключ удаляется также из user/assistant/title. После рестарта возвращается **текстовая историческая справка**, максимум12 блоков только выбранного разговора; точные прежние tool_calls/results/arguments не сохраняются. Старые file_id/model_id/slice_id/export_id/download_url и ownership не действуют, вложение нужно загрузить снова. Начатый запрос отмечается прерванным, pending/старые tools не повторяются. Смена process_id обнаруживается status/snapshot/polling даже при совпадении курсора; старая вкладка заменяет карточки только GET-чтением. Перед первым новым изменением восстановленного чата агент должен перечитать состояние/модели; теория сама не запускает tools. Проект восстанавливается настоящим3MF отдельно.

Новый/выбор/удаление/отправка в UI блокируются при active/pending/своём наблюдаемом slice/export. Удаление сервером также защищено active ownership, не отменяет engine и не удаляет3MF/G-code. Старый API создания пустого дескриптора при busy сохранён. GETlist/snapshot/events остаются доступны; сервер не вводит глобальный selected-session. В dev по умолчанию только RAM и явный статус «Временный диалог». Само наличие capabilities.saved_dialogs не доказывает успешную запись.

### Ручная приёмка2G — подготовлена, не выполнялась

После отдельного согласованного обновления целой копии и настройки провайдера сохраните проект. В исходниках/подменах эта приёмка не считается пройденной:

1. Откройте «Диалоги», создайте разговор: «Обсудим кронштейн PETG. Пока только объясни влияние заполнения». Дождитесь ответа/idle и **«Сохранено»**. Никаких изменений проекта для этого запроса не требуется.
2. Создайте второй: «Обсудим декор PLA. Объясни высоту слоя». После сохранения вернитесь в первый, сверьте отдельную историю. Выбор сам не вызывает LLM/инструменты.
3. Обычным способом закройте и снова откройте STE, затем чат на его **новом текущем порту**. Найдите оба разговора, откройте первый. В нём видны исторические сообщения; старые операции/вложения/ссылки не ожили.
4. Спросите «Какой материал мы обсуждали в этом разговоре?» — контекст только первого. Для нового изменения потребуются реальные чтения STE; эта история не восстанавливает сцену.
5. При наличии завершённого upload/экспорта в тестовой истории после рестарта проверьте expired/no active download; новое вложение загружается заново. Не повторяйте старый pending/запуск. Ошибку/квоту проверяйте на отдельном тестовом архиве, не повреждайте рабочие файлы.
6. Явно удалите второй тестовый разговор с подтверждением. Перезапустите STE и убедитесь, что он не появился снова. Сохраните скриншоты списка/выбора/saved/истории, результаты передайте ведущему. Native/browser-приёмка остаётся открыта до этого сценария.

Чтение списка через PowerShell без LLM/native-команд (подготовлено, не запускалось):

```powershell
$historyPort = [int](Read-Host 'Текущий порт из адреса чата')
$historyBase = 'http://127.0.0.1:{0}' -f $historyPort
$historyList = Invoke-RestMethod -Uri ($historyBase + '/api/sessions') -TimeoutSec 15
$historyList | ConvertTo-Json -Depth 20
$historyId = Read-Host 'Непрозрачный session_id из списка для чтения'
Invoke-RestMethod -Uri ($historyBase + '/api/sessions/' + [Uri]::EscapeDataString($historyId)) -TimeoutSec 15 | ConvertTo-Json -Depth 25
```

Контракт/ошибки — `docs/API_CONTRACT.md`, проверки/ограничения — `docs/reports/ROLE_02G_REPORT.md`. Установка/реальный провайдер/STE/принтер агентом не выполнялись.

## Исправление окна LLM Settings

Окно открывается на следующем Qt-tick асинхронно, с transient owner к главному QQuickWindow, модальностью окна и фокусом URL. Повторный вызов поднимает тот же диалог, поздние focus/Save после закрытия игнорируются. Preferences/ключ/пустое значение/удаление ключа прежние. Гипотеза исходного отказа ввода — nested-loop/input grab из QML-меню; реальная причина требует ручного подтверждения в STE. Шесть адресных fake-Qt проверок и полный321 Python прошли; это не native-ввод.

Для проверки пользователь после сохранения проекта/закрытия STE и backup может заменить **только установленный AIAssistant.py** из canonical (совместим с baseline296, без включения2D) либо **всю canonical plugin/AIAssistant** (включая2D, отдельная приёмка). Для иной старой/альтернативной копии совместимость одного файла не подтверждена. Перезапустить STE и проверить ввод/Tab/вставку/Отмена/сохранение тестовых настроек без отправки чата. Frozen baseline_296/manifest не заменять и не считать содержащими fix. Точные шаги — [FIX_LLM_SETTINGS_REPORT.md](../docs/reports/FIX_LLM_SETTINGS_REPORT.md). Автоматической установки или реального LLM-запроса не было.

## Исправление числовых аргументов и диагностика поворота

Локальная доработка после0b67564, ещё не установленная и не проверенная реальным провайдером/STE. Единый set_setting теперь передаёт oneOf-связь key/value по каталогу: float→number, int→integer, bool→boolean, enum→string. При несовместимости провайдера со схемой показывается реальная ошибка, без скрытого fallback.

Для известного float/int set_setting на границе LLM разрешён только явный разбор строки1–64 символа по строгой JSON-number лексеме (int — только целая лексема без decimal/exponent). Пробелы/запятые/единицы/выражения, non-finite/overflow/underflow не исправляются. Scope/другой ключ/экструдер/boolean/enum/углы не угадываются. Метаданные argument_admission и видимая детерминированная заметка объясняют разбор; native-успех подтверждают только реальные before/after/readback. Прямой MCP по-прежнему отвергает `"0.12"` для layer_height. Причина того, что в пользовательском случае value оказался строкой, не установлена; заявление «транспорт превратил число» не доказано.

В отказе аргументов есть компактные field/expected_type/actual_type; повтор одинаковых неверных аргументов останавливает ход на втором отказе даже с новым call_id. После execution/Qt/network timeout/unknown другие изменения и автоматические повторы запрещены. Лимиты прежние.

Для преобразований различаются validation/executed=false, preflight/operation_started=false, execution/operation_started=true и unknown/null. Preflight мог читать Qt API, но не разрешил push операции. Execution означает начатый dispatch, не доказательство изменения. Unknown не становится безопасным отказом. Physics/Automatic drop down/push free, Preview, busy, активная платформа/родитель и живой ID проверяются как раньше, автоматически не отключаются. Математика штатного RotateOperation не заменена; жалоба пользователя без её payload/ошибки пока не имеет подтверждённой причины.

Повторная ручная приёмка **подготовлена, не выполнялась**: после отдельного согласованного обновления целой копии/настройки провайдера сохранить проект и original layer_height, затем запросить «Установи global layer_height0.12мм, если метаданные разрешают. Подтверди get_setting. При ошибке остановись». Сверить0.1→0.12/API/UI, при успешном результате отдельным явным запросом восстановить сохранённое число и перечитать. Если исходная высота другая/0.12 недопустима — использовать её фактический before и допустимый другой target, не подставлять исходное0.1 вслепую. При unknown сначала только чтение/UI.

Затем отдельная проверка: приложить несимметричный STL → «Импортируй приложенный STL, дождись завершения и покажи актуальные list_models/get_model_state, пока не поворачивай» → записать живой ID/матрицу → «Поверни объект с этим текущим ID на90градусов вокруг мировой осиZ. Угол числом, проверь before/after и отдельный get_model_state. При preflight/ошибке не обходи защиту, остановись» → сверить UI/матрицу/другие модели → штатный Undo и повторное чтение. На симметричной модели картинка может не измениться. При отказе нужны безопасные tool-name, типы аргументов, axis/angle type, stage/code/operation_started и актуальный get_model_state; приватный путь/байты/ключ не присылать. Старые ID после рестарта/Undo удаления не использовать. Сценарий и ограничения — [отчёт](../docs/reports/FIX_TOOL_ARGUMENTS_AND_MODEL_TRANSFORMS_REPORT.md).

## Проверка

- Загрузка плагина и get/set layer_height проверены на сборке 7.10.12; другие сборки и остальные операции требуют своих проверок. Shutdown приложения отдельно не проверялся.
- После правок Python перезапускайте слайсер.
- Ошибки смотрите в `stderr.log` и логах папки конфигурации.
- В финальную сдачу включить этот README, весь каталог `AIAssistant` и результаты в соседнем `results`.
