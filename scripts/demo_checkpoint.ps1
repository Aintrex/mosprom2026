param(
    [ValidateRange(1, 65535)][int]$Port = 58092,
    [string]$ModelPath = ''
)

$ErrorActionPreference = 'Stop'
$demoBase = 'http://127.0.0.1:{0}' -f $Port
$demoMcp = $demoBase + '/mcp'
$script:demoRpcId = 0

function Invoke-DemoRpc {
    param([string]$Method, [hashtable]$Params = @{})
    $script:demoRpcId += 1
    $json = @{jsonrpc='2.0'; id=$script:demoRpcId; method=$Method; params=$Params} |
        ConvertTo-Json -Depth 25 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $reply = Invoke-RestMethod -Uri $demoMcp -Method Post -TimeoutSec 35 -ContentType 'application/json; charset=utf-8' -Body $bytes
    if ($null -ne $reply.error) {
        throw ($reply.error | ConvertTo-Json -Depth 15 -Compress)
    }
    return $reply
}

function Invoke-DemoTool {
    param([string]$Name, [hashtable]$Arguments = @{})
    $reply = Invoke-DemoRpc 'tools/call' @{name=$Name; arguments=$Arguments}
    $value = $reply.result.content[0].text | ConvertFrom-Json
    if ($reply.result.isError -or -not $value.ok) {
        throw ($value | ConvertTo-Json -Depth 25 -Compress)
    }
    return $value.data
}

function Wait-Demo {
    param([string]$Message)
    Write-Host "`n$Message" -ForegroundColor Cyan
    [void](Read-Host 'Enter — выполнить следующий шаг; Ctrl+C — остановиться')
}

Write-Host "Проверяем плагин по адресу $demoBase"
Write-Host 'Перед показом сохраните проект. Нужны пустая сцена, режим 3D и отсутствие текущего слайсинга.'
Invoke-RestMethod -Uri ($demoBase + '/api/health') -TimeoutSec 5 | Format-List
$null = Invoke-DemoRpc 'initialize' @{}
Write-Host 'Доступные команды плагина:'
(Invoke-DemoRpc 'tools/list' @{}).result.tools | Select-Object name | Format-Table -AutoSize
$demoState = Invoke-DemoTool 'get_slicer_state' @{}
$demoState | ConvertTo-Json -Depth 15 | Write-Host
if ($null -eq $demoState.machine -or $demoState.machine.name -ne 'Stereotech Hybrid 530 V5.2.4') {
    throw 'Выберите Stereotech Hybrid 530 V5.2.4 и запустите скрипт заново.'
}
if ($demoState.printing_mode -ne 'classic' -or -not $demoState.backend.matches_mode) {
    throw 'Выберите 3D в STE Slicer, дождитесь переключения и запустите скрипт заново.'
}
$demoModels = Invoke-DemoTool 'list_models' @{}
if (@($demoModels.models).Count -gt 0) {
    throw 'Для переключения режимов нужна пустая сцена. Сохраните проект и подготовьте пустую сцену вручную.'
}

$demoLayerBefore = Invoke-DemoTool 'get_setting' @{key='layer_height'; scope='global'}
$demoOriginalLayer = $demoLayerBefore.value
Write-Host "Исходная высота слоя: $demoOriginalLayer мм. Запомните её для восстановления при остановке."
$demoLayerTarget = if ($demoOriginalLayer -eq 0.12) { 0.1 } else { 0.12 }

try {
    Wait-Demo "1. Раскройте Quality. Сейчас установим высоту слоя $demoLayerTarget мм."
    Invoke-DemoTool 'set_setting' @{key='layer_height'; scope='global'; value=$demoLayerTarget} | ConvertTo-Json | Write-Host

    Wait-Demo "Посмотрите новое значение в STE Slicer. Далее вернём $demoOriginalLayer мм."
    Invoke-DemoTool 'set_setting' @{key='layer_height'; scope='global'; value=$demoOriginalLayer} | ConvertTo-Json | Write-Host

    foreach ($demoMode in @(
        @{id='cylindrical'; label='5D Spiral'},
        @{id='cylindrical_full'; label='5D'},
        @{id='classic'; label='3D'}
    )) {
        Wait-Demo ('2. Раскройте Printing Mode. Переключаем на ' + $demoMode.label + '. После вызова проверьте UI и matches_mode=true.')
        Invoke-DemoTool 'set_print_mode' @{mode=$demoMode.id} | ConvertTo-Json -Depth 15 | Write-Host
    }

    Wait-Demo 'Проверьте, что восстановлены 3D и исходная высота слоя. Далее можно загрузить тестовую модель.'
    if ([string]::IsNullOrWhiteSpace($ModelPath)) {
        $ModelPath = Read-Host 'Абсолютный путь к небольшому STL/OBJ/3MF без кавычек. Пустая строка — пропустить загрузку'
    }
    if (-not [string]::IsNullOrWhiteSpace($ModelPath)) {
        $demoLoaded = Invoke-DemoTool 'load_model' @{path=$ModelPath.Trim()}
        $demoLoaded | ConvertTo-Json -Depth 20 | Write-Host
        Invoke-DemoTool 'list_models' @{} | ConvertTo-Json -Depth 20 | Write-Host
        foreach ($item in $demoLoaded.objects) {
            Invoke-DemoTool 'get_model_bounds' @{object_id=$item.object_id} | ConvertTo-Json -Depth 20 | Write-Host
        }
        Write-Host 'Покажите модель на столе и её размеры. Y — высота, X/Z — плоскость стола; размеры в мм.'
        Write-Host 'Тестовые модели удалите вручную через UI после показа. Скрипт не удаляет объекты.'
    }
    Write-Host 'Демонстрация закончена. Эти команды не подключают ИИ и не запускают нарезку.' -ForegroundColor Green
} catch {
    Write-Host 'Показ остановлен из-за ошибки. Автоматического повтора и отката нет.' -ForegroundColor Yellow
    Write-Host "Проверьте сцену и настройки. Исходная высота слоя: $demoOriginalLayer мм, исходный режим: 3D."
    Write-Host 'После таймаута импорта модель может появиться позже. Не повторяйте импорт автоматически.'
    throw
}
