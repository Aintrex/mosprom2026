# Dot-source this file, then call the helpers explicitly. No operation starts automatically.
$ErrorActionPreference = 'Stop'
$script:qaBase = $null
$script:qaRpcId = 0

function Connect-QASlicer {
    param([Parameter(Mandatory=$true)][ValidateRange(1,65535)][int]$Port)
    $script:qaBase = 'http://127.0.0.1:{0}' -f $Port
    Invoke-RestMethod -Uri ($script:qaBase + '/api/health') -TimeoutSec 10 -ErrorAction Stop
    Invoke-QARpc 'initialize' @{}
}

function Invoke-QARpc {
    param([Parameter(Mandatory=$true)][string]$Method, [hashtable]$Params = @{})
    if (-not $script:qaBase) { throw 'Сначала выполните Connect-QASlicer с текущим портом' }
    $script:qaRpcId += 1
    $body = @{jsonrpc='2.0'; id=$script:qaRpcId; method=$Method; params=$Params} |
        ConvertTo-Json -Depth 35 -Compress
    $reply = Invoke-RestMethod -Uri ($script:qaBase + '/mcp') -Method Post -TimeoutSec 35 `
        -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ErrorAction Stop
    if ($null -ne $reply.error) { throw ($reply.error | ConvertTo-Json -Depth 30 -Compress) }
    return $reply
}

function Invoke-QATool {
    param([Parameter(Mandatory=$true)][string]$Name, [hashtable]$Arguments = @{})
    $reply = Invoke-QARpc 'tools/call' @{name=$Name; arguments=$Arguments}
    if (-not $reply.result.content) { throw 'Нет результата инструмента; проверьте плагин/UI, не повторяйте запись' }
    $envelope = $reply.result.content[0].text | ConvertFrom-Json -ErrorAction Stop
    if ($reply.result.isError -or -not $envelope.ok) {
        throw ($envelope | ConvertTo-Json -Depth 35 -Compress)
    }
    return $envelope.data
}

function Wait-QAOperation {
    param([Parameter(Mandatory=$true)][ValidateSet('slice','export','arrangement')][string]$Kind,
          [Parameter(Mandatory=$true)][string]$Id)
    if ([string]::IsNullOrWhiteSpace($Id)) { throw 'Нет ID; новый запуск автоматически не выполняется' }
    $field = @{slice='slice_id'; export='export_id'; arrangement='arrangement_id'}[$Kind]
    $tool = @{slice='get_slicing_status'; export='get_export_status'; arrangement='get_arrangement_status'}[$Kind]
    $limit = if ($Kind -eq 'slice') { 195 } else { 135 }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        $arguments = @{}
        $arguments[$field] = $Id
        $value = Invoke-QATool $tool $arguments
        $value | ConvertTo-Json -Depth 35 -Compress | Write-Host
        if ($value.state -eq 'failed') { throw ($value | ConvertTo-Json -Depth 35 -Compress) }
        if ($value.state -eq 'completed') {
            if ($Kind -ne 'arrangement' -and -not $value.valid) { throw 'Результат уже неактуален; проверьте сцену/UI' }
            if ($Kind -eq 'export' -and $null -eq $value.artifact) { throw 'Нет подтверждённого файла' }
            return $value
        }
        if ($watch.Elapsed.TotalSeconds -ge $limit) {
            throw 'Ожидание остановлено. Это не отмена engine/writer. Перечитайте этот ID/UI; не повторяйте запуск автоматически'
        }
        Start-Sleep -Seconds 1
    }
}
