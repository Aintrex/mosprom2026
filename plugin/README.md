# Установка AI Assistant

1. Установите дистрибутив STE Slicer, выданный организаторами.
2. Откройте **Справка -> Показать папку конфигурации**.
3. Сохраните открытый проект, закройте слайсер и скопируйте каталог `AIAssistant` целиком в `plugins` этой папки. При обновлении сохраните резервную копию прежнего плагина и не удаляйте остальные файлы конфигурации. Локально найдена папка `%APPDATA%\steslicer\7.10\plugins`; путь на другой машине уточните через приложение.
4. Перезапустите слайсер. Откройте пункт **AI Assistant -> Open chat** в меню расширений.
5. Чат откроется в системном браузере. В текущем каркасе ответ демонстрационный, без вызова LLM.

Python-код должен оставаться совместимым с 3.5. PyQt5 и `UM` предоставляет сам слайсер. Не устанавливайте современный MCP SDK внутрь этого окружения.

## Реализовано в ТЗ 01

Через существующий `/mcp` доступны `assistant_status`, `get_setting` и `set_setting`. Настройки ограничены обязательными `key=layer_height`, `scope=global`; значение в мм. Остальные настройки, профили, режимы, модели, слайсинг и экспорт пока не реализованы. Вызовы настроек из HTTP выполняются через `app.callLater`, ожидание результата ограничено 5 секундами. Запись возвращает фактически прочитанные `before` и `after`.

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

## Настройка LLM: предстоит реализовать

Добавить окно настроек плагина: URL OpenAI-совместимого API, имя модели и ключ. Хранить значения через Preferences приложения. Не отправлять ключ в SPA и не записывать в логи. Текущий каркас не принимает и не сохраняет ключ.

## Проверка

- Загрузка плагина и get/set layer_height проверены на сборке 7.10.12; другие сборки и остальные операции требуют своих проверок. Shutdown приложения отдельно не проверялся.
- После правок Python перезапускайте слайсер.
- Ошибки смотрите в `stderr.log` и логах папки конфигурации.
- В финальную сдачу включить этот README, весь каталог `AIAssistant` и результаты в соседнем `results`.
